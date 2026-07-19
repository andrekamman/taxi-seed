from pathlib import Path

import duckdb


def get_column_stats(
    conn: duckdb.DuckDBPyConnection, file_path: Path, column_name: str, sample_size: int = 10000
) -> dict | None:
    """Get statistics for a column from a parquet file."""
    try:
        # Quote column name to handle special characters
        col_quoted = f'"{column_name}"'

        # Get basic stats using a sample for performance
        stats_query = f"""
            WITH sample AS (
                SELECT {col_quoted} as val
                FROM '{file_path}'
                USING SAMPLE {sample_size}
            )
            SELECT
                COUNT(*) as total_count,
                COUNT(val) as non_null_count,
                COUNT(DISTINCT val) as unique_count
            FROM sample
        """
        result = conn.execute(stats_query).fetchone()
        stats = {
            "total_count": result[0],
            "non_null_count": result[1],
            "unique_count": result[2],
        }

        # For numeric columns, get min/max/avg
        numeric_query = f"""
            WITH sample AS (
                SELECT {col_quoted} as val
                FROM '{file_path}'
                USING SAMPLE {sample_size}
            )
            SELECT
                MIN(TRY_CAST(val AS DOUBLE)) as min_val,
                MAX(TRY_CAST(val AS DOUBLE)) as max_val,
                AVG(TRY_CAST(val AS DOUBLE)) as avg_val
            FROM sample
            WHERE TRY_CAST(val AS DOUBLE) IS NOT NULL
        """
        num_result = conn.execute(numeric_query).fetchone()
        if num_result[0] is not None:
            stats["min"] = num_result[0]
            stats["max"] = num_result[1]
            stats["avg"] = num_result[2]

        # Get top values for categorical comparison
        top_query = f"""
            WITH sample AS (
                SELECT {col_quoted} as val
                FROM '{file_path}'
                USING SAMPLE {sample_size}
            )
            SELECT val, COUNT(*) as cnt
            FROM sample
            WHERE val IS NOT NULL
            GROUP BY val
            ORDER BY cnt DESC
            LIMIT 5
        """
        top_result = conn.execute(top_query).fetchall()
        stats["top_values"] = [str(row[0]) for row in top_result]

        return stats
    except Exception as e:
        return None


def compare_column_stats(stats1: dict | None, stats2: dict | None) -> tuple[bool, str]:
    """Compare statistics between two columns to verify if they're likely the same data."""
    if stats1 is None or stats2 is None:
        return None, "Could not retrieve statistics"

    reasons = []
    score = 0.0
    checks = 0

    # Compare null ratios
    if stats1["total_count"] > 0 and stats2["total_count"] > 0:
        null_ratio1 = 1 - (stats1["non_null_count"] / stats1["total_count"])
        null_ratio2 = 1 - (stats2["non_null_count"] / stats2["total_count"])
        null_diff = abs(null_ratio1 - null_ratio2)
        if null_diff < 0.1:
            score += 1
            reasons.append(f"null ratio similar ({null_ratio1:.1%} vs {null_ratio2:.1%})")
        elif null_diff > 0.3:
            reasons.append(f"null ratio differs ({null_ratio1:.1%} vs {null_ratio2:.1%})")
        checks += 1

    # Compare cardinality ratio (unique values / total)
    if stats1["non_null_count"] > 0 and stats2["non_null_count"] > 0:
        card_ratio1 = stats1["unique_count"] / stats1["non_null_count"]
        card_ratio2 = stats2["unique_count"] / stats2["non_null_count"]
        # Compare on log scale since cardinality can vary widely
        if card_ratio1 > 0 and card_ratio2 > 0:
            import math
            log_diff = abs(math.log10(card_ratio1 + 0.001) - math.log10(card_ratio2 + 0.001))
            if log_diff < 0.5:
                score += 1
                reasons.append(f"cardinality similar ({stats1['unique_count']} vs {stats2['unique_count']})")
            elif log_diff > 1.5:
                reasons.append(f"cardinality differs significantly ({stats1['unique_count']} vs {stats2['unique_count']})")
            checks += 1

    # Compare numeric ranges if available
    if "min" in stats1 and "max" in stats1 and "min" in stats2 and "max" in stats2:
        range1 = stats1["max"] - stats1["min"] if stats1["max"] != stats1["min"] else 1
        range2 = stats2["max"] - stats2["min"] if stats2["max"] != stats2["min"] else 1

        # Check if ranges overlap significantly
        overlap_start = max(stats1["min"], stats2["min"])
        overlap_end = min(stats1["max"], stats2["max"])

        if overlap_start <= overlap_end:
            overlap = overlap_end - overlap_start
            overlap_ratio = overlap / max(range1, range2)
            if overlap_ratio > 0.5:
                score += 1.5  # Strong signal
                reasons.append(f"value ranges overlap ({stats1['min']:.2f}-{stats1['max']:.2f} vs {stats2['min']:.2f}-{stats2['max']:.2f})")
            checks += 1.5
        else:
            reasons.append(f"value ranges don't overlap ({stats1['min']:.2f}-{stats1['max']:.2f} vs {stats2['min']:.2f}-{stats2['max']:.2f})")
            checks += 1.5

        # Compare averages
        if stats1["avg"] is not None and stats2["avg"] is not None:
            avg_diff = abs(stats1["avg"] - stats2["avg"])
            avg_scale = max(abs(stats1["avg"]), abs(stats2["avg"]), 1)
            if avg_diff / avg_scale < 0.2:
                score += 1
                reasons.append(f"averages similar ({stats1['avg']:.2f} vs {stats2['avg']:.2f})")
            elif avg_diff / avg_scale > 0.5:
                reasons.append(f"averages differ ({stats1['avg']:.2f} vs {stats2['avg']:.2f})")
            checks += 1

    # Compare top values for categorical data
    if stats1["top_values"] and stats2["top_values"]:
        top_set1 = set(stats1["top_values"])
        top_set2 = set(stats2["top_values"])
        if top_set1 & top_set2:
            overlap_count = len(top_set1 & top_set2)
            score += overlap_count / 5
            reasons.append(f"top values overlap: {top_set1 & top_set2}")
            checks += 1
        elif stats1["unique_count"] < 20 and stats2["unique_count"] < 20:
            # For low-cardinality columns, no overlap is a bad sign
            reasons.append(f"top values don't overlap: {top_set1} vs {top_set2}")
            checks += 1

    if checks == 0:
        return None, "Insufficient data for comparison"

    confidence = score / checks
    verified = confidence >= 0.5

    detail = "; ".join(reasons) if reasons else "No specific patterns detected"
    return verified, detail


def compute_data_similarity_score(stats1: dict | None, stats2: dict | None) -> tuple[float, str]:
    """Compute a similarity score (0-1) between two columns based on their data statistics."""
    if stats1 is None or stats2 is None:
        return 0.0, "Could not retrieve statistics"

    scores = []
    reasons = []

    # Compare null ratios (weight: 1.0)
    if stats1["total_count"] > 0 and stats2["total_count"] > 0:
        null_ratio1 = 1 - (stats1["non_null_count"] / stats1["total_count"])
        null_ratio2 = 1 - (stats2["non_null_count"] / stats2["total_count"])
        null_diff = abs(null_ratio1 - null_ratio2)
        null_score = max(0, 1 - null_diff * 2)  # 0.5 diff = 0 score
        scores.append(null_score)
        if null_score > 0.8:
            reasons.append(f"null ratio ~{null_ratio1:.0%}")

    # Compare cardinality ratio (weight: 1.0)
    if stats1["non_null_count"] > 0 and stats2["non_null_count"] > 0:
        card_ratio1 = stats1["unique_count"] / stats1["non_null_count"]
        card_ratio2 = stats2["unique_count"] / stats2["non_null_count"]
        if card_ratio1 > 0 and card_ratio2 > 0:
            import math
            log_diff = abs(math.log10(card_ratio1 + 0.001) - math.log10(card_ratio2 + 0.001))
            card_score = max(0, 1 - log_diff)
            scores.append(card_score)
            if card_score > 0.7:
                reasons.append(f"cardinality ~{stats1['unique_count']}")

    # Compare numeric ranges (weight: 1.5)
    if "min" in stats1 and "max" in stats1 and "min" in stats2 and "max" in stats2:
        range1 = stats1["max"] - stats1["min"] if stats1["max"] != stats1["min"] else 1
        range2 = stats2["max"] - stats2["min"] if stats2["max"] != stats2["min"] else 1

        overlap_start = max(stats1["min"], stats2["min"])
        overlap_end = min(stats1["max"], stats2["max"])

        if overlap_start <= overlap_end:
            overlap = overlap_end - overlap_start
            overlap_ratio = overlap / max(range1, range2)
            scores.append(overlap_ratio)
            scores.append(overlap_ratio)  # Double weight for range overlap
            if overlap_ratio > 0.7:
                reasons.append(f"range {stats1['min']:.1f}-{stats1['max']:.1f}")
        else:
            scores.append(0)
            scores.append(0)

        # Compare averages
        if stats1["avg"] is not None and stats2["avg"] is not None:
            avg_diff = abs(stats1["avg"] - stats2["avg"])
            avg_scale = max(abs(stats1["avg"]), abs(stats2["avg"]), 1)
            avg_score = max(0, 1 - avg_diff / avg_scale)
            scores.append(avg_score)
            if avg_score > 0.8:
                reasons.append(f"avg ~{stats1['avg']:.1f}")

    # Compare top values (weight: 1.0)
    if stats1["top_values"] and stats2["top_values"]:
        top_set1 = set(stats1["top_values"])
        top_set2 = set(stats2["top_values"])
        if top_set1 or top_set2:
            overlap_count = len(top_set1 & top_set2)
            top_score = overlap_count / max(len(top_set1), len(top_set2))
            scores.append(top_score)
            if overlap_count > 0:
                reasons.append(f"values overlap: {top_set1 & top_set2}")

    if not scores:
        return 0.0, "Insufficient data"

    final_score = sum(scores) / len(scores)
    detail = "; ".join(reasons) if reasons else "Low similarity"
    return final_score, detail
