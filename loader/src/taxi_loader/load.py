"""DuckDB↔SQL Server load: DDL construction and (later) COPY execution."""
from __future__ import annotations

from pathlib import Path

import duckdb

from taxi_shared.sql_generator import generate_create_table_sql
from taxi_shared.type_mapping import map_duckdb_to_mssql

from taxi_loader.connection import ATTACH_NAME, ConnConfig, _sql_str
from taxi_loader import manifest
from taxi_loader.reconcile import APPEND, RELOAD, SKIP, YearPlan


def describe_parquet_types(conn: duckdb.DuckDBPyConnection,
                           parquet_path: str | Path) -> dict[str, str]:
    """{column_name: duckdb_type} from one parquet file, in file column order."""
    rows = conn.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{_sql_str(str(parquet_path))}')"
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


def year_table(data_type: str, year: int) -> str:
    return f"{data_type}_{year}"


def dest_url(schema: str, table: str) -> str:
    return f"mssql://{ATTACH_NAME}/{schema}/{table}"


def build_copy_sql(parquet_paths, dest, *, create_table: bool, replace: bool,
                   flush_rows: int, tablock: bool) -> str:
    files = ", ".join(f"'{_sql_str(str(p))}'" for p in parquet_paths)
    return (
        f"COPY (SELECT * FROM read_parquet([{files}])) "
        f"TO '{_sql_str(dest)}' "
        f"(FORMAT 'bcp', CREATE_TABLE {str(create_table).lower()}, "
        f"REPLACE {str(replace).lower()}, FLUSH_ROWS {int(flush_rows)}, "
        f"TABLOCK {str(tablock).lower()})"
    )


def parquet_row_count(conn: duckdb.DuckDBPyConnection, path) -> int:
    row = conn.execute(
        f"SELECT num_rows FROM parquet_file_metadata('{_sql_str(str(path))}')"
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _fq(cfg: ConnConfig, table: str) -> str:
    return f"{cfg.schema}.{table}"


def table_exists(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig, table: str) -> bool:
    fq = _fq(cfg, table)
    row = conn.execute(
        f"SELECT o FROM mssql_scan('{ATTACH_NAME}', ?)",
        [f"SELECT OBJECT_ID('{_sql_str(fq)}','U') AS o"],
    ).fetchone()
    return bool(row and row[0] is not None)


def count_year_table(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig, table: str) -> int:
    if not table_exists(conn, cfg, table):
        return 0
    fq = _fq(cfg, table)
    row = conn.execute(
        f"SELECT c FROM mssql_scan('{ATTACH_NAME}', ?)",
        [f"SELECT COUNT_BIG(*) AS c FROM {fq}"],
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _ensure_table(conn, cfg, table: str, sample_parquet) -> None:
    if table_exists(conn, cfg, table):
        return
    ddl = build_create_table_sql(conn, _fq(cfg, table), sample_parquet)
    try:
        conn.execute(f"SELECT mssql_exec('{ATTACH_NAME}', ?)", [ddl])
    except duckdb.Error:
        # Another worker loading a different month of this same year raced us to
        # the CREATE. IF NOT EXISTS + CREATE is not atomic across sessions, so
        # losing the race is expected, not an error -- re-check and continue.
        if not table_exists(conn, cfg, table):
            raise


def execute_year_plan(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig,
                      data_type: str, plan: YearPlan, *, flush_rows: int) -> int:
    """Execute one YearPlan. Returns rows loaded. Writes manifest rows per the
    durability model (append: manifest row only after that month's COPY; reload:
    drop -> create -> delete manifest year -> COPY all -> write manifest all)."""
    if plan.action == SKIP or not plan.months:
        if plan.action == RELOAD:
            # Whole year vanished from disk: rebuild to empty + clear manifest.
            table = year_table(data_type, plan.year)
            conn.execute(f"SELECT mssql_exec('{ATTACH_NAME}', ?)",
                         [f"DROP TABLE IF EXISTS {_fq(cfg, table)}"])
            manifest.delete_year_rows(conn, cfg, data_type, plan.year)
        return 0

    table = year_table(data_type, plan.year)
    fq = _fq(cfg, table)
    url = dest_url(cfg.schema, table)
    loaded = 0

    if plan.action == RELOAD:
        conn.execute(f"SELECT mssql_exec('{ATTACH_NAME}', ?)",
                     [f"DROP TABLE IF EXISTS {fq}"])
        ddl = build_create_table_sql(conn, fq, plan.months[0].path)
        conn.execute(f"SELECT mssql_exec('{ATTACH_NAME}', ?)", [ddl])
        manifest.delete_year_rows(conn, cfg, data_type, plan.year)
        copy_sql = build_copy_sql(
            [m.path for m in plan.months], url,
            create_table=False, replace=False, flush_rows=flush_rows, tablock=True,
        )
        conn.execute(copy_sql)
        for m in plan.months:
            manifest.write_month_row(conn, cfg, data_type, m.year, m.month,
                                     m.path, m.source_row_count)
            loaded += m.source_row_count
        return loaded

    # APPEND: ensure the table exists (fresh year), then load month-by-month so a
    # month's manifest row is written only after its own COPY succeeds.
    _ensure_table(conn, cfg, table, plan.months[0].path)
    for m in plan.months:
        copy_sql = build_copy_sql(
            [m.path], url,
            create_table=False, replace=False, flush_rows=flush_rows, tablock=True,
        )
        conn.execute(copy_sql)
        manifest.write_month_row(conn, cfg, data_type, m.year, m.month,
                                 m.path, m.source_row_count)
        loaded += m.source_row_count
    return loaded
