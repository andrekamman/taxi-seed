"""The _load_manifest bookkeeping table: create / read / write.

One row per loaded month; PK (data_type, year, month). All access goes through
the mssql extension functions (mssql_scan for reads, mssql_exec for writes) to
avoid the catalog metadata cache returning stale results within a session.
"""
from __future__ import annotations

import duckdb

from taxi_shared.sql_generator import generate_create_table_sql

from taxi_loader.connection import ATTACH_NAME, ConnConfig, _sql_str
from taxi_loader.reconcile import ManifestRow

MANIFEST_TABLE = "_load_manifest"

# Explicit, bounded, PK-compatible types (see plan design decision #2).
MANIFEST_COLUMNS: dict[str, str] = {
    "data_type": "NVARCHAR(16)",
    "year": "INT",
    "month": "INT",
    "source_file": "NVARCHAR(400)",
    "row_count": "BIGINT",
    "loaded_at": "DATETIME2",
}


def manifest_fq(schema: str) -> str:
    return f"{schema}.{MANIFEST_TABLE}"


def build_manifest_ddl(schema: str) -> list[str]:
    fq = manifest_fq(schema)
    create = generate_create_table_sql(fq, MANIFEST_COLUMNS).rstrip(";\n ")
    # `create` ends with the table-options clause, e.g.:
    #   CREATE TABLE dbo._load_manifest (\n    data_type NVARCHAR(16),\n    ...\n
    #   ) WITH (DATA_COMPRESSION = PAGE)
    # Peel that WITH(...) clause off first, then splice the PK constraint in
    # as a table constraint before the column list's closing paren, and
    # re-append WITH(...) at the very end -- so the whole table (including
    # its PK, and its compression) is created atomically in one statement --
    # see ensure_manifest_table's OBJECT_ID guard.
    options = " WITH (DATA_COMPRESSION = PAGE)"
    body = create.removesuffix(options)
    body = body.removesuffix(")").rstrip()
    pk_name = f"PK_{schema}_{MANIFEST_TABLE}"
    ddl = (
        f"{body},\n"
        f"    CONSTRAINT {pk_name} PRIMARY KEY (data_type, year, month)\n"
        f"){options}"
    )
    return [ddl]


def _exec(conn: duckdb.DuckDBPyConnection, sql: str) -> None:
    conn.execute(f"SELECT mssql_exec('{ATTACH_NAME}', ?)", [sql])


def manifest_table_exists(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig) -> bool:
    fq = manifest_fq(cfg.schema)
    row = conn.execute(
        f"SELECT o FROM mssql_scan('{ATTACH_NAME}', ?)",
        [f"SELECT OBJECT_ID('{_sql_str(fq)}','U') AS o"],
    ).fetchone()
    return bool(row and row[0] is not None)


def ensure_manifest_table(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig) -> None:
    if manifest_table_exists(conn, cfg):
        return
    try:
        for stmt in build_manifest_ddl(cfg.schema):
            _exec(conn, stmt)
    except duckdb.Error:
        # Every concurrent worker calls this on start-up and only one can win
        # the CREATE. The table is built in a single statement (see
        # build_manifest_ddl), so a loser has nothing half-made to clean up.
        if not manifest_table_exists(conn, cfg):
            raise


def read_manifest(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig,
                  data_type: str) -> list[ManifestRow]:
    fq = manifest_fq(cfg.schema)
    query = (
        f"SELECT year, month, row_count FROM {fq} "
        f"WHERE data_type = '{_sql_str(data_type)}'"
    )
    rows = conn.execute(
        f"SELECT year, month, row_count FROM mssql_scan('{ATTACH_NAME}', ?)",
        [query],
    ).fetchall()
    return [ManifestRow(int(y), int(m), int(rc)) for (y, m, rc) in rows]


def write_month_row(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig,
                    data_type: str, year: int, month: int,
                    source_file: str, row_count: int) -> None:
    fq = manifest_fq(cfg.schema)
    stmt = (
        f"INSERT INTO {fq} (data_type, year, month, source_file, row_count, loaded_at) "
        f"VALUES ('{_sql_str(data_type)}', {int(year)}, {int(month)}, "
        f"'{_sql_str(source_file)}', {int(row_count)}, SYSUTCDATETIME())"
    )
    _exec(conn, stmt)


def delete_year_rows(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig,
                     data_type: str, year: int) -> None:
    fq = manifest_fq(cfg.schema)
    stmt = (
        f"DELETE FROM {fq} "
        f"WHERE data_type = '{_sql_str(data_type)}' AND year = {int(year)}"
    )
    _exec(conn, stmt)
