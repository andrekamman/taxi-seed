"""DuckDB↔SQL Server load: DDL construction and (later) COPY execution."""
from __future__ import annotations

from pathlib import Path

import duckdb

from taxi_shared.sql_generator import generate_create_table_sql
from taxi_shared.type_mapping import map_duckdb_to_mssql


def describe_parquet_types(conn: duckdb.DuckDBPyConnection,
                           parquet_path: str | Path) -> dict[str, str]:
    """{column_name: duckdb_type} from one parquet file, in file column order."""
    rows = conn.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{parquet_path}')"
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def build_create_table_sql(conn: duckdb.DuckDBPyConnection, fq_table: str,
                           sample_parquet: str | Path) -> str:
    """Explicit CREATE TABLE for a (type, year) table, from one sample file.

    DESCRIBE -> taxi_shared type map -> generate_create_table_sql. Returns a
    single statement with no trailing ';' (mssql_exec wants one statement).
    Raises taxi_shared.type_mapping.TypeMappingError for unmapped columns.
    """
    duck_types = describe_parquet_types(conn, sample_parquet)
    mssql_cols = {name: map_duckdb_to_mssql(dt) for name, dt in duck_types.items()}
    return generate_create_table_sql(fq_table, mssql_cols).rstrip(";\n ")
