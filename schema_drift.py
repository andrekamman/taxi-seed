#!/usr/bin/env python3
"""
Schema Drift Analyzer for NYC TLC Taxi Data

Analyzes Parquet files across different time periods to detect and report
schema changes (drift) in the data structure over time.
"""

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import duckdb


@dataclass
class ColumnInfo:
    """Information about a column in a schema."""

    name: str
    dtype: str

    def __hash__(self):
        return hash((self.name, self.dtype))

    def __eq__(self, other):
        return self.name == other.name and self.dtype == other.dtype


@dataclass
class ColumnRename:
    """Represents a detected column rename."""

    old_col: ColumnInfo
    new_col: ColumnInfo
    confidence: float  # 0.0 to 1.0
    data_verified: bool | None = None  # None = not checked, True = verified, False = rejected
    verification_details: str = ""


@dataclass
class SchemaChange:
    """Represents a schema change between two time periods."""

    period_from: str
    period_to: str
    columns_added: list[ColumnInfo]
    columns_removed: list[ColumnInfo]
    columns_type_changed: list[tuple[ColumnInfo, ColumnInfo]]
    columns_renamed: list[ColumnRename]
    file_from: Path | None = None
    file_to: Path | None = None


# Common abbreviation expansions for taxi data
ABBREVIATIONS = {
    "amt": "amount",
    "lat": "latitude",
    "lon": "longitude",
    "long": "longitude",
    "fwd": "forward",
    "id": "id",
    "num": "number",
    "cnt": "count",
    "dist": "distance",
    "dt": "datetime",
    "ts": "timestamp",
    "pu": "pickup",
    "do": "dropoff",
    "loc": "location",
    "src": "source",
    "dst": "destination",
    "pax": "passenger",
    "surchg": "surcharge",
    # Taxi-specific synonyms
    "start": "pickup",
    "end": "dropoff",
    "origin": "pickup",
    "destination": "dropoff",
    "trip": "",  # Often a prefix that can be ignored
    "tpep": "",  # Taxi prefix
    "lpep": "",  # Taxi prefix
}

# Semantic opposites that should NOT be matched together
SEMANTIC_OPPOSITES = [
    {"pickup", "dropoff"},
    {"start", "end"},
    {"origin", "destination"},
    {"pulocationid", "dolocationid"},
    {"pu", "do"},
]


def get_semantic_categories(name: str) -> set[str]:
    """Get the semantic categories of a column name."""
    lower = name.lower()
    categories = set()

    # Pickup vs dropoff
    pickup_indicators = ["pickup", "start", "origin", "pu_", "_pu", "pulocation", "tpep_pickup", "lpep_pickup"]
    dropoff_indicators = ["dropoff", "drop_off", "end", "destination", "do_", "_do", "dolocation", "tpep_dropoff", "lpep_dropoff"]

    for indicator in pickup_indicators:
        if indicator in lower:
            categories.add("pickup")
            break

    for indicator in dropoff_indicators:
        if indicator in lower:
            categories.add("dropoff")
            break

    # Coordinate columns (lat/lon)
    coord_indicators = ["lat", "lon", "latitude", "longitude", "_lat", "_lon"]
    for indicator in coord_indicators:
        if indicator in lower:
            categories.add("coordinate")
            break

    # Location ID columns
    if "locationid" in lower or "location_id" in lower:
        categories.add("location_id")

    # Amount/money columns
    amount_indicators = ["amount", "amt", "fare", "fee", "surcharge", "tax", "tip", "toll"]
    for indicator in amount_indicators:
        if indicator in lower:
            categories.add("amount")
            break

    # Datetime columns
    datetime_indicators = ["datetime", "date", "time", "timestamp"]
    for indicator in datetime_indicators:
        if indicator in lower:
            categories.add("datetime")
            break

    return categories


def normalize_column_name(name: str) -> str:
    """Normalize a column name for comparison."""
    # Lowercase
    normalized = name.lower()

    # Replace underscores with spaces for token splitting
    tokens = normalized.replace("_", " ").split()

    # Expand abbreviations
    expanded_tokens = []
    for token in tokens:
        expanded_tokens.append(ABBREVIATIONS.get(token, token))

    # Join back and remove all separators for final comparison
    return "".join(expanded_tokens)


def column_name_similarity(name1: str, name2: str) -> float:
    """Calculate similarity between two column names (0.0 to 1.0)."""
    cats1 = get_semantic_categories(name1)
    cats2 = get_semantic_categories(name2)

    # Check for semantic category conflicts
    # Pickup vs dropoff should never match
    if "pickup" in cats1 and "dropoff" in cats2:
        return 0.0
    if "dropoff" in cats1 and "pickup" in cats2:
        return 0.0

    # Coordinates should only match coordinates, location IDs should match location IDs
    if "coordinate" in cats1 and "coordinate" not in cats2 and cats2:
        return 0.0
    if "coordinate" in cats2 and "coordinate" not in cats1 and cats1:
        return 0.0
    if "location_id" in cats1 and "location_id" not in cats2 and cats2:
        return 0.0
    if "location_id" in cats2 and "location_id" not in cats1 and cats1:
        return 0.0

    # Amount columns should match amount columns
    if "amount" in cats1 and "amount" not in cats2 and cats2:
        return 0.0
    if "amount" in cats2 and "amount" not in cats1 and cats1:
        return 0.0

    # Datetime columns should match datetime columns
    if "datetime" in cats1 and "datetime" not in cats2 and cats2:
        return 0.0
    if "datetime" in cats2 and "datetime" not in cats1 and cats1:
        return 0.0

    norm1 = normalize_column_name(name1)
    norm2 = normalize_column_name(name2)

    # Exact match after normalization
    if norm1 == norm2:
        return 1.0

    # Check if one contains the other
    if norm1 in norm2 or norm2 in norm1:
        shorter = min(len(norm1), len(norm2))
        longer = max(len(norm1), len(norm2))
        return shorter / longer

    # Token-based similarity with expanded abbreviations
    tokens1 = set(ABBREVIATIONS.get(t, t) for t in name1.lower().replace("_", " ").split())
    tokens2 = set(ABBREVIATIONS.get(t, t) for t in name2.lower().replace("_", " ").split())

    # Remove empty tokens (from prefix expansions)
    tokens1 = {t for t in tokens1 if t}
    tokens2 = {t for t in tokens2 if t}

    if not tokens1 or not tokens2:
        return 0.0

    intersection = tokens1 & tokens2
    union = tokens1 | tokens2

    if not union:
        return 0.0

    jaccard = len(intersection) / len(union)

    # Calculate string similarity using longest common subsequence ratio
    lcs_len = longest_common_subsequence_length(norm1, norm2)
    lcs_similarity = (2 * lcs_len) / (len(norm1) + len(norm2)) if (norm1 and norm2) else 0

    return max(jaccard, lcs_similarity)


def longest_common_subsequence_length(s1: str, s2: str) -> int:
    """Calculate the length of the longest common subsequence."""
    m, n = len(s1), len(s2)
    # Use space-optimized DP
    prev = [0] * (n + 1)
    curr = [0] * (n + 1)

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i - 1] == s2[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev, curr = curr, prev

    return prev[n]


def types_compatible(dtype1: str, dtype2: str) -> bool:
    """Check if two types are compatible (same or commonly interchangeable)."""
    if dtype1 == dtype2:
        return True

    # Normalize types
    t1, t2 = dtype1.upper(), dtype2.upper()

    # Numeric types are often interchangeable
    numeric_types = {"INTEGER", "BIGINT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "INT"}
    if t1 in numeric_types and t2 in numeric_types:
        return True

    # String types
    string_types = {"VARCHAR", "TEXT", "STRING", "CHAR"}
    if t1 in string_types and t2 in string_types:
        return True

    # Timestamp/datetime
    time_types = {"TIMESTAMP", "DATETIME", "DATE", "TIME"}
    if t1 in time_types and t2 in time_types:
        return True

    return False


def detect_renames(
    removed: list[ColumnInfo], added: list[ColumnInfo], threshold: float = 0.7
) -> tuple[list[ColumnRename], list[ColumnInfo], list[ColumnInfo]]:
    """Detect likely column renames from removed/added columns."""
    renames = []
    unmatched_removed = list(removed)
    unmatched_added = list(added)

    # Calculate similarity scores for all pairs
    scores = []
    for old_col in removed:
        for new_col in added:
            similarity = column_name_similarity(old_col.name, new_col.name)
            type_match = types_compatible(old_col.dtype, new_col.dtype)

            # Boost score if types match, penalize if they don't
            if type_match:
                adjusted_score = similarity
            else:
                adjusted_score = similarity * 0.7  # Penalize type mismatch

            if adjusted_score >= threshold:
                scores.append((adjusted_score, old_col, new_col))

    # Sort by score descending and greedily match
    scores.sort(key=lambda x: x[0], reverse=True)

    matched_old = set()
    matched_new = set()

    for score, old_col, new_col in scores:
        if old_col.name in matched_old or new_col.name in matched_new:
            continue

        renames.append(ColumnRename(old_col=old_col, new_col=new_col, confidence=score))
        matched_old.add(old_col.name)
        matched_new.add(new_col.name)

    # Filter out matched columns from unmatched lists
    unmatched_removed = [c for c in removed if c.name not in matched_old]
    unmatched_added = [c for c in added if c.name not in matched_new]

    return renames, unmatched_removed, unmatched_added


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


def detect_renames_by_data(
    conn: duckdb.DuckDBPyConnection,
    removed: list[ColumnInfo],
    added: list[ColumnInfo],
    file_from: Path,
    file_to: Path,
    threshold: float = 0.6,
) -> tuple[list[ColumnRename], list[ColumnInfo], list[ColumnInfo]]:
    """Detect likely column renames using data similarity (generic, no domain knowledge)."""
    renames = []

    # Pre-fetch stats for all columns (more efficient than fetching per-pair)
    stats_removed = {}
    stats_added = {}

    for col in removed:
        stats_removed[col.name] = get_column_stats(conn, file_from, col.name, sample_size=5000)

    for col in added:
        stats_added[col.name] = get_column_stats(conn, file_to, col.name, sample_size=5000)

    # Calculate data similarity scores for all pairs with compatible types
    scores = []
    for old_col in removed:
        for new_col in added:
            # Only compare columns with compatible types
            if not types_compatible(old_col.dtype, new_col.dtype):
                continue

            stats_old = stats_removed.get(old_col.name)
            stats_new = stats_added.get(new_col.name)

            similarity, details = compute_data_similarity_score(stats_old, stats_new)

            if similarity >= threshold:
                scores.append((similarity, old_col, new_col, details))

    # Sort by score descending and greedily match
    scores.sort(key=lambda x: x[0], reverse=True)

    matched_old = set()
    matched_new = set()

    for score, old_col, new_col, details in scores:
        if old_col.name in matched_old or new_col.name in matched_new:
            continue

        rename = ColumnRename(
            old_col=old_col,
            new_col=new_col,
            confidence=score,
            data_verified=True,
            verification_details=details,
        )
        renames.append(rename)
        matched_old.add(old_col.name)
        matched_new.add(new_col.name)

    # Filter out matched columns from unmatched lists
    unmatched_removed = [c for c in removed if c.name not in matched_old]
    unmatched_added = [c for c in added if c.name not in matched_new]

    return renames, unmatched_removed, unmatched_added


def verify_renames_with_data(
    conn: duckdb.DuckDBPyConnection,
    renames: list[ColumnRename],
    file_from: Path,
    file_to: Path,
    verify_threshold: float = 0.95,
) -> list[ColumnRename]:
    """Verify rename candidates by comparing actual column data."""
    verified_renames = []

    for rename in renames:
        # Only verify uncertain renames (high confidence ones we trust)
        if rename.confidence >= verify_threshold:
            rename.data_verified = None  # Skip verification for high confidence
            rename.verification_details = "High confidence - skipped data verification"
            verified_renames.append(rename)
            continue

        # Get stats for both columns
        stats_old = get_column_stats(conn, file_from, rename.old_col.name)
        stats_new = get_column_stats(conn, file_to, rename.new_col.name)

        verified, details = compare_column_stats(stats_old, stats_new)
        rename.data_verified = verified
        rename.verification_details = details

        if verified is None:
            # Couldn't verify, keep the rename but mark as unverified
            verified_renames.append(rename)
        elif verified:
            # Data supports the rename
            verified_renames.append(rename)
        else:
            # Data doesn't support the rename - still include but mark as rejected
            verified_renames.append(rename)

    return verified_renames


def get_parquet_schema(conn: duckdb.DuckDBPyConnection, file_path: Path) -> list[ColumnInfo]:
    """Extract schema from a Parquet file using DuckDB."""
    try:
        result = conn.execute(f"DESCRIBE SELECT * FROM '{file_path}'").fetchall()
        return [ColumnInfo(name=row[0], dtype=row[1]) for row in result]
    except Exception as e:
        print(f"  Warning: Could not read schema from {file_path.name}: {e}", file=sys.stderr)
        return []


def extract_period(file_path: Path) -> str:
    """Extract the YYYY-MM period from a filename."""
    # Filename format: type_tripdata_YYYY-MM.parquet
    stem = file_path.stem
    parts = stem.split("_")
    return parts[-1]  # Returns YYYY-MM


def find_parquet_files(data_dir: Path, data_type: str) -> list[Path]:
    """Find all parquet files for a given data type, sorted by period."""
    type_dir = data_dir / data_type
    if not type_dir.exists():
        return []

    files = list(type_dir.rglob("*.parquet"))
    # Sort by period (YYYY-MM)
    files.sort(key=lambda f: extract_period(f))
    return files


def compare_schemas(
    old_schema: list[ColumnInfo], new_schema: list[ColumnInfo], skip_rename_detection: bool = False
) -> tuple[list[ColumnInfo], list[ColumnInfo], list[tuple[ColumnInfo, ColumnInfo]], list[ColumnRename]]:
    """Compare two schemas and return differences including detected renames."""
    old_by_name = {col.name: col for col in old_schema}
    new_by_name = {col.name: col for col in new_schema}

    old_names = set(old_by_name.keys())
    new_names = set(new_by_name.keys())

    added = [new_by_name[name] for name in (new_names - old_names)]
    removed = [old_by_name[name] for name in (old_names - new_names)]

    # Check for type changes in columns that exist in both
    type_changed = []
    for name in old_names & new_names:
        old_col = old_by_name[name]
        new_col = new_by_name[name]
        if old_col.dtype != new_col.dtype:
            type_changed.append((old_col, new_col))

    # Detect renames among added/removed columns (using name similarity)
    if skip_rename_detection:
        # In generic mode, rename detection happens later using data similarity
        return added, removed, type_changed, []

    renames, remaining_removed, remaining_added = detect_renames(removed, added)

    return remaining_added, remaining_removed, type_changed, renames


def schema_signature(schema: list[ColumnInfo]) -> tuple:
    """Create a hashable signature for a schema."""
    return tuple((c.name, c.dtype) for c in schema)


def analyze_data_type(
    conn: duckdb.DuckDBPyConnection,
    data_dir: Path,
    data_type: str,
    verify_data: bool = False,
    generic_mode: bool = False,
) -> dict:
    """Analyze schema drift for a specific data type.

    Args:
        conn: DuckDB connection
        data_dir: Directory containing the data
        data_type: Type of data (e.g., 'yellow', 'green')
        verify_data: If True, verify name-based renames with data (taxi mode only)
        generic_mode: If True, detect renames using data similarity only (no domain knowledge)
    """
    files = find_parquet_files(data_dir, data_type)

    if not files:
        return {"data_type": data_type, "files_analyzed": 0, "schemas": {}, "changes": [], "generic_mode": generic_mode}

    print(f"  Analyzing {len(files)} files...")
    if generic_mode:
        print("  Using generic mode (data-driven rename detection)")

    # Phase 1: Read schemas and find transition points (where schema changes)
    # This is fast - just DESCRIBE queries, no data sampling
    transitions = []  # List of (period, file_path, schema) at schema change points
    current_sig = None

    for file_path in files:
        period = extract_period(file_path)
        schema = get_parquet_schema(conn, file_path)

        if not schema:
            continue

        sig = schema_signature(schema)

        # Only record when schema actually changes
        if sig != current_sig:
            transitions.append((period, file_path, schema))
            current_sig = sig

    print(f"  Found {len(transitions)} unique schema versions")

    # Build schemas dict from transitions only (first occurrence of each schema)
    schemas_by_period = {period: schema for period, _, schema in transitions}

    # Phase 2: Compare only at transition points (expensive data analysis only here)
    changes = []

    for i in range(1, len(transitions)):
        prev_period, prev_file, prev_schema = transitions[i - 1]
        curr_period, curr_file, curr_schema = transitions[i]

        # In generic mode, skip name-based rename detection
        added, removed, type_changed, renames = compare_schemas(
            prev_schema, curr_schema, skip_rename_detection=generic_mode
        )

        if generic_mode and (removed or added):
            # Use data-driven rename detection
            print(f"    Comparing {len(removed)} removed vs {len(added)} added columns by data similarity...")
            renames, removed, added = detect_renames_by_data(
                conn, removed, added, prev_file, curr_file
            )
            if renames:
                print(f"    Found {len(renames)} potential renames (requires human review)")

        elif verify_data and renames:
            # Taxi mode: verify name-based renames with data
            print(f"    Verifying {len(renames)} rename candidates with data...")
            renames = verify_renames_with_data(conn, renames, prev_file, curr_file)

            # Separate verified renames from rejected ones
            verified_renames = []
            rejected_to_add = []
            rejected_to_remove = []

            for rename in renames:
                if rename.data_verified is False:
                    rejected_to_remove.append(rename.old_col)
                    rejected_to_add.append(rename.new_col)
                else:
                    verified_renames.append(rename)

            renames = verified_renames
            removed = removed + rejected_to_remove
            added = added + rejected_to_add

        if added or removed or type_changed or renames:
            changes.append(
                SchemaChange(
                    period_from=prev_period,
                    period_to=curr_period,
                    columns_added=added,
                    columns_removed=removed,
                    columns_type_changed=type_changed,
                    columns_renamed=renames,
                    file_from=prev_file,
                    file_to=curr_file,
                )
            )

    return {
        "data_type": data_type,
        "files_analyzed": len(files),
        "schemas": schemas_by_period,
        "changes": changes,
        "generic_mode": generic_mode,
    }


def format_schema_table(schema: list[ColumnInfo]) -> str:
    """Format a schema as a table."""
    if not schema:
        return "  (empty schema)"

    max_name_len = max(len(col.name) for col in schema)
    lines = []
    for col in schema:
        lines.append(f"    {col.name:<{max_name_len}}  {col.dtype}")
    return "\n".join(lines)


def generate_report(results: list[dict], output_format: str = "text") -> str:
    """Generate a comprehensive schema drift report."""
    lines = []

    # Check if any result used generic mode
    generic_mode = any(result.get("generic_mode", False) for result in results)

    lines.append("=" * 80)
    if generic_mode:
        lines.append("SCHEMA DRIFT REPORT (GENERIC MODE)")
        lines.append("=" * 80)
        lines.append("NOTE: Renames detected by data similarity - REQUIRES HUMAN REVIEW")
    else:
        lines.append("SCHEMA DRIFT REPORT")
        lines.append("=" * 80)
    lines.append("")

    for result in results:
        data_type = result["data_type"]
        files_analyzed = result["files_analyzed"]
        schemas = result["schemas"]
        changes = result["changes"]
        is_generic = result.get("generic_mode", False)

        lines.append(f"{'─' * 80}")
        lines.append(f"DATA TYPE: {data_type.upper()}")
        lines.append(f"{'─' * 80}")
        lines.append(f"Files analyzed: {files_analyzed}")
        if is_generic:
            lines.append("Mode: Generic (data-driven rename detection)")

        if not schemas:
            lines.append("No data files found.")
            lines.append("")
            continue

        # Show period range
        periods = sorted(schemas.keys())
        lines.append(f"Period range: {periods[0]} to {periods[-1]}")
        lines.append(f"Total schema changes detected: {len(changes)}")
        lines.append("")

        # Show initial schema
        first_period = periods[0]
        lines.append(f"INITIAL SCHEMA ({first_period}):")
        lines.append(format_schema_table(schemas[first_period]))
        lines.append("")

        # Show each change
        if changes:
            lines.append("SCHEMA CHANGES:")
            lines.append("")

            for change in changes:
                lines.append(f"  [{change.period_from}] → [{change.period_to}]")

                if change.columns_renamed:
                    if is_generic:
                        lines.append("    ? SUGGESTED RENAMES (requires human review):")
                    else:
                        lines.append("    ↔ Columns RENAMED:")
                    for rename in sorted(change.columns_renamed, key=lambda r: r.confidence, reverse=True):
                        type_note = ""
                        if rename.old_col.dtype != rename.new_col.dtype:
                            type_note = f" [type: {rename.old_col.dtype} → {rename.new_col.dtype}]"
                        confidence_pct = int(rename.confidence * 100)

                        # Add verification status if data was verified
                        verify_note = ""
                        if is_generic:
                            verify_note = " ← REVIEW"
                        elif rename.data_verified is True:
                            verify_note = " ✓ data verified"
                        elif rename.data_verified is False:
                            verify_note = " ✗ data mismatch"

                        lines.append(f"        ↔ {rename.old_col.name} → {rename.new_col.name} ({confidence_pct}% confidence){type_note}{verify_note}")

                        # Show verification details if available
                        if rename.verification_details and rename.data_verified is not None:
                            lines.append(f"            └─ {rename.verification_details}")

                if change.columns_added:
                    lines.append("    + Columns ADDED:")
                    for col in change.columns_added:
                        lines.append(f"        + {col.name} ({col.dtype})")

                if change.columns_removed:
                    lines.append("    - Columns REMOVED:")
                    for col in change.columns_removed:
                        lines.append(f"        - {col.name} ({col.dtype})")

                if change.columns_type_changed:
                    lines.append("    ~ Type CHANGED:")
                    for old_col, new_col in change.columns_type_changed:
                        lines.append(f"        ~ {old_col.name}: {old_col.dtype} → {new_col.dtype}")

                lines.append("")
        else:
            lines.append("No schema changes detected across all periods.")
            lines.append("")

        # Show final schema
        last_period = periods[-1]
        lines.append(f"FINAL SCHEMA ({last_period}):")
        lines.append(format_schema_table(schemas[last_period]))
        lines.append("")

        # Summary of unique schemas
        unique_schemas = []
        seen = set()
        for period in periods:
            schema_tuple = tuple((c.name, c.dtype) for c in schemas[period])
            if schema_tuple not in seen:
                seen.add(schema_tuple)
                unique_schemas.append(period)

        lines.append(f"Unique schema versions: {len(unique_schemas)}")
        if len(unique_schemas) > 1:
            lines.append(f"Schema version periods: {', '.join(unique_schemas)}")
        lines.append("")

    # Cross-type comparison
    lines.append("=" * 80)
    lines.append("CROSS-TYPE SUMMARY")
    lines.append("=" * 80)
    lines.append("")

    # Collect all unique column names across all data types
    all_columns = defaultdict(set)
    for result in results:
        for period, schema in result["schemas"].items():
            for col in schema:
                all_columns[col.name].add(result["data_type"])

    lines.append("Columns by data type coverage:")
    for col_name in sorted(all_columns.keys()):
        types = sorted(all_columns[col_name])
        lines.append(f"  {col_name}: {', '.join(types)}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Analyze schema drift in NYC TLC taxi Parquet files"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("raw"),
        help="Directory containing the taxi data (default: raw)",
    )
    parser.add_argument(
        "--types",
        nargs="+",
        default=["yellow", "green", "fhv", "fhvhv"],
        help="Data types to analyze (default: all)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Output file for the report (default: stdout)",
    )
    parser.add_argument(
        "--verify-data",
        action="store_true",
        help="Verify rename candidates by sampling actual data (slower but more accurate)",
    )
    parser.add_argument(
        "--generic",
        action="store_true",
        help="Use generic mode: detect renames by data similarity only, without domain knowledge. "
             "Suggestions require human review.",
    )

    args = parser.parse_args()

    if args.generic and args.verify_data:
        print("Note: --generic mode already uses data verification, --verify-data is ignored.", file=sys.stderr)

    if not args.data_dir.exists():
        print(f"Error: Data directory '{args.data_dir}' does not exist.", file=sys.stderr)
        sys.exit(1)

    # Create a DuckDB connection (in-memory)
    conn = duckdb.connect(":memory:")

    results = []
    for data_type in args.types:
        print(f"Analyzing {data_type} data...")
        result = analyze_data_type(
            conn,
            args.data_dir,
            data_type,
            verify_data=args.verify_data,
            generic_mode=args.generic,
        )
        results.append(result)

    print("")

    # Generate report
    report = generate_report(results)

    if args.output:
        args.output.write_text(report)
        print(f"Report written to: {args.output}")
    else:
        print(report)

    conn.close()


if __name__ == "__main__":
    main()
