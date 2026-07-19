import sys
from pathlib import Path

import duckdb

from schema_drift.models import ColumnInfo, SchemaChange
from schema_drift.renames import detect_renames, detect_renames_by_data, verify_renames_with_data


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
) -> tuple[list[ColumnInfo], list[ColumnInfo], list[tuple[ColumnInfo, ColumnInfo]], list]:
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
