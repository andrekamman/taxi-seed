"""Read parquet files via DuckDB and export as chunked JSON."""

import json
from pathlib import Path

import duckdb


def get_schema(parquet_glob: str) -> dict[str, str]:
    """Get column name -> type mapping from parquet files.

    Returns dict like {"tpep_pickup_datetime": "TIMESTAMP", ...}.
    """
    db = duckdb.connect()
    rows = db.sql(f"DESCRIBE SELECT * FROM '{parquet_glob}'").fetchall()
    return {row[0]: row[1] for row in rows}


def export_chunks(
    parquet_glob: str,
    columns: dict[str, str],
    chunk_size: int,
    output_dir: Path,
) -> int:
    """Export parquet data as chunked JSON files.

    Args:
        parquet_glob: Glob pattern for parquet files (e.g. "raw/yellow/2026/*.parquet").
        columns: Mapping of {output_name: source_column_name}.
        chunk_size: Number of rows per chunk file.
        output_dir: Directory to write chunk_NNNN.json files.

    Returns:
        Number of chunk files created.

    Raises:
        FileNotFoundError: If glob matches no parquet files.
    """
    db = duckdb.connect()

    # Verify files exist
    try:
        total_rows = db.sql(f"SELECT COUNT(*) FROM '{parquet_glob}'").fetchone()[0]
    except (duckdb.IOException, duckdb.CatalogException, duckdb.BinderException):
        raise FileNotFoundError(f"No parquet files matched: {parquet_glob}")

    if total_rows == 0:
        raise FileNotFoundError(f"No parquet files matched: {parquet_glob}")

    # Build SELECT with column renaming
    select_parts = [
        f'"{source}" AS "{target}"' for target, source in columns.items()
    ]
    select_sql = ", ".join(select_parts)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    chunk_index = 0
    offset = 0

    while offset < total_rows:
        rows = db.sql(
            f"SELECT {select_sql} FROM '{parquet_glob}' LIMIT {chunk_size} OFFSET {offset}"
        ).fetchall()

        if not rows:
            break

        # Get column names from the mapping
        col_names = list(columns.keys())

        # Convert to list of dicts, handling timestamp serialization
        chunk_data = []
        for row in rows:
            row_dict = {}
            for i, name in enumerate(col_names):
                val = row[i]
                # Serialize timestamps as ISO 8601
                if hasattr(val, "isoformat"):
                    val = val.isoformat()
                row_dict[name] = val
            chunk_data.append(row_dict)

        chunk_path = output_dir / f"chunk_{chunk_index:04d}.json"
        with open(chunk_path, "w") as f:
            json.dump(chunk_data, f)

        chunk_index += 1
        offset += chunk_size

    return chunk_index
