"""Parquet metadata queries and value-scan precision checks.

Metadata queries (get_file_metadata, aggregate_across_files, fits_in_target_type)
are footer-only — no data scan. Value scans (has_precision_loss) always read
the full column, since sampling would risk false-negative data-loss decisions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb


_INT_TYPE_PREFIXES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT",
                       "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT")
_FLOAT_TYPE_PREFIXES = ("FLOAT", "DOUBLE", "REAL", "DECIMAL")


def _coerce_stat(value: Any, col_type: str) -> Any:
    """Coerce a parquet_metadata min/max string into the natural Python type
    for its declared column type. Leaves non-numeric types as-is."""
    if value is None:
        return None
    upper = col_type.upper()
    try:
        if any(upper.startswith(p) for p in _INT_TYPE_PREFIXES):
            return int(value)
        if any(upper.startswith(p) for p in _FLOAT_TYPE_PREFIXES):
            return float(value)
    except (TypeError, ValueError):
        return value
    return value


def get_file_metadata(conn: duckdb.DuckDBPyConnection, file_path: Path) -> dict[str, dict[str, Any]]:
    """Return {column_name: {type, null_count, num_rows, min, max}} from parquet footer."""
    # Column types from DESCRIBE (friendlier form than parquet_schema and matches
    # what DuckDB uses for casts).
    col_types: dict[str, str] = {}
    desc_rows = conn.execute(f"DESCRIBE SELECT * FROM '{file_path}'").fetchall()
    for row in desc_rows:
        col_types[row[0]] = row[1]

    # Row group stats: null_count, min, max per column per row group.
    # Aggregate across row groups. parquet_metadata returns min/max as strings.
    md_rows = conn.execute(
        f"SELECT path_in_schema, stats_null_count, stats_min_value, stats_max_value, num_values "
        f"FROM parquet_metadata('{file_path}')"
    ).fetchall()

    per_col: dict[str, dict[str, Any]] = {}
    for path_in_schema, null_count, min_val, max_val, num_values in md_rows:
        col = path_in_schema
        col_type = col_types.get(col, "")
        min_coerced = _coerce_stat(min_val, col_type)
        max_coerced = _coerce_stat(max_val, col_type)
        if col not in per_col:
            per_col[col] = {
                "null_count": 0,
                "num_rows": 0,
                "min": None,
                "max": None,
            }
        entry = per_col[col]
        entry["null_count"] += int(null_count or 0)
        entry["num_rows"] += int(num_values or 0)
        if min_coerced is not None:
            entry["min"] = min_coerced if entry["min"] is None else min(entry["min"], min_coerced)
        if max_coerced is not None:
            entry["max"] = max_coerced if entry["max"] is None else max(entry["max"], max_coerced)

    # Merge type in
    result: dict[str, dict[str, Any]] = {}
    for col, type_ in col_types.items():
        entry = per_col.get(col, {"null_count": 0, "num_rows": 0, "min": None, "max": None})
        entry["type"] = type_
        result[col] = entry
    return result


def aggregate_across_files(files_metadata: list[dict[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Aggregate per-file metadata into per-column presence + null/range summary.

    Returns {col_name: {files_present, files_with_data, total_nulls, total_rows,
                        min_range, max_range, types_seen}}.
    """
    agg: dict[str, dict[str, Any]] = {}
    for md in files_metadata:
        for col, stats in md.items():
            if col not in agg:
                agg[col] = {
                    "files_present": 0,
                    "files_with_data": 0,
                    "total_nulls": 0,
                    "total_rows": 0,
                    "min_range": None,
                    "max_range": None,
                    "types_seen": set(),
                }
            a = agg[col]
            a["files_present"] += 1
            a["types_seen"].add(stats["type"])
            a["total_nulls"] += stats["null_count"]
            a["total_rows"] += stats["num_rows"]
            non_null_count = stats["num_rows"] - stats["null_count"]
            if non_null_count > 0:
                a["files_with_data"] += 1
            if stats["min"] is not None:
                a["min_range"] = stats["min"] if a["min_range"] is None else min(a["min_range"], stats["min"])
            if stats["max"] is not None:
                a["max_range"] = stats["max"] if a["max_range"] is None else max(a["max_range"], stats["max"])
    # Convert sets to sorted lists so callers can rely on stable output.
    for a in agg.values():
        a["types_seen"] = sorted(a["types_seen"])
    return agg


# Signed integer ranges — the only ones DuckDB uses for its numeric types.
_INT_RANGES = {
    "TINYINT": (-128, 127),
    "SMALLINT": (-32768, 32767),
    "INTEGER": (-2_147_483_648, 2_147_483_647),
    "BIGINT": (-9_223_372_036_854_775_808, 9_223_372_036_854_775_807),
}


def fits_in_target_type(col_stats: dict[str, Any], target_type: str) -> tuple[bool, str]:
    """Metadata-only range check. Returns (fits, reason_if_not).

    Handles integer widths, DOUBLE→integer range, VARCHAR(N) length.
    Precision (fractional-value) checks are separate (see has_precision_loss).
    """
    min_v = col_stats.get("min")
    max_v = col_stats.get("max")
    # Integer target
    target_upper = target_type.upper()
    if target_upper in _INT_RANGES:
        lo, hi = _INT_RANGES[target_upper]
        if min_v is not None and min_v < lo:
            return False, f"min value {min_v} is below {target_upper} range (min {lo})"
        if max_v is not None and max_v > hi:
            return False, f"max value {max_v} exceeds {target_upper} range (max {hi})"
        return True, ""
    # VARCHAR(N)
    if target_upper.startswith("VARCHAR(") and target_upper.endswith(")"):
        n = int(target_upper[len("VARCHAR("):-1])
        if max_v is not None and isinstance(max_v, str) and len(max_v) > n:
            return False, f"max string length {len(max_v)} exceeds VARCHAR({n})"
        return True, ""
    # Default: assume fit (unknown target type — caller handles as auto-safe).
    return True, ""


def has_precision_loss(
    conn: duckdb.DuckDBPyConnection,
    file_path: Path,
    column: str,
    target_type: str,
) -> tuple[bool, int]:
    """Value scan for precision loss (DOUBLE→BIGINT truncation of fractional values).

    Always full-scans; never samples — a sampled false negative would silently
    discard user data.
    """
    target_upper = target_type.upper()
    if target_upper not in _INT_RANGES:
        return False, 0
    quoted = '"' + column.replace('"', '""') + '"'
    row = conn.execute(
        f"SELECT count(*) FILTER (WHERE {quoted} IS NOT NULL "
        f"AND {quoted} != CAST({quoted} AS {target_upper})) "
        f"FROM '{file_path}'"
    ).fetchone()
    count = int(row[0]) if row and row[0] is not None else 0
    return count > 0, count
