from pathlib import Path

import duckdb

from schema_drift.models import ColumnInfo, ColumnRename
from schema_drift.similarity import column_name_similarity, types_compatible
from schema_drift.stats import compare_column_stats, compute_data_similarity_score, get_column_stats


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
