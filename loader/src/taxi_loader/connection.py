"""Connect DuckDB to SQL Server via the mssql community extension.

INSTALL/LOAD the extension, assert its version, provision the database and
schema, and ATTACH the target. All errors here are exit-2 (nothing loaded).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import duckdb

ATTACH_NAME = "mssql"
BOOT_ATTACH_NAME = "mssql_boot"
# Spike-confirmed installed version (Task 1, non-Docker spike against DuckDB
# 1.4.4): `INSTALL mssql FROM community` resolves to a build whose
# extension_version is the commit-hash string below, not a semver tag — the
# community registry versions this extension by commit. A bump here must be
# a deliberate, tested change (re-run the spike, confirm the new hash).
EXPECTED_MSSQL_EXT_VERSION = "7e57d24"

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class LoaderError(Exception):
    """Base for loader errors that map to exit code 2."""


class LoaderConnectionError(LoaderError):
    """Install/load/attach/provision failure."""


class LoaderConfigError(LoaderError):
    """Bad configuration (identifier, missing password, unmapped type)."""


@dataclass
class ConnConfig:
    host: str
    port: int
    database: str
    schema: str
    user: str
    password: str


def validate_identifier(name: str, what: str) -> str:
    if not _IDENT_RE.match(name or ""):
        raise LoaderConfigError(
            f"invalid {what} {name!r}: must match [A-Za-z_][A-Za-z0-9_]*"
        )
    return name


def _sql_str(s: str) -> str:
    """Escape a value for embedding as a T-SQL single-quoted string literal."""
    return s.replace("'", "''")


def build_conn_string(cfg: ConnConfig, database: str) -> str:
    return (
        f"Server={cfg.host},{cfg.port};"
        f"Database={database};"
        f"User Id={cfg.user};"
        f"Password={cfg.password};"
        f"Encrypt=yes;TrustServerCertificate=yes"
    )


def connect_duckdb() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("INSTALL mssql FROM community;")
        conn.execute("LOAD mssql;")
    except duckdb.Error as e:
        raise LoaderConnectionError(f"failed to install/load mssql extension: {e}") from e
    row = conn.execute(
        "SELECT extension_version FROM duckdb_extensions() WHERE extension_name = 'mssql'"
    ).fetchone()
    version = row[0] if row else None
    if version != EXPECTED_MSSQL_EXT_VERSION:
        raise LoaderConnectionError(
            f"mssql extension version {version!r} != expected "
            f"{EXPECTED_MSSQL_EXT_VERSION!r}; a version bump must be a deliberate, "
            f"tested change (update EXPECTED_MSSQL_EXT_VERSION)."
        )
    return conn


def ensure_database(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig) -> None:
    """Create cfg.database if absent, by attaching master and running CREATE DATABASE."""
    try:
        conn.execute(
            f"ATTACH ? AS {BOOT_ATTACH_NAME} (TYPE mssql)",
            [build_conn_string(cfg, "master")],
        )
        stmt = (
            f"IF DB_ID('{_sql_str(cfg.database)}') IS NULL "
            f"EXEC('CREATE DATABASE [{cfg.database}]')"
        )
        conn.execute(f"SELECT mssql_exec('{BOOT_ATTACH_NAME}', ?)", [stmt])
    except duckdb.Error as e:
        raise LoaderConnectionError(f"failed to provision database {cfg.database!r}: {e}") from e
    finally:
        try:
            conn.execute(f"DETACH {BOOT_ATTACH_NAME}")
        except duckdb.Error:
            pass


def attach_target(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig, *,
                  create_schema: bool = True) -> None:
    """ATTACH the target database as `mssql` and create the schema if non-default.

    create_schema=False skips the CREATE SCHEMA branch entirely, leaving the
    ATTACH as the only side effect (still raises LoaderConnectionError if the
    target database itself does not exist).
    """
    try:
        conn.execute(
            f"ATTACH ? AS {ATTACH_NAME} (TYPE mssql)",
            [build_conn_string(cfg, cfg.database)],
        )
        if create_schema and cfg.schema != "dbo":
            stmt = (
                f"IF SCHEMA_ID('{_sql_str(cfg.schema)}') IS NULL "
                f"EXEC('CREATE SCHEMA [{cfg.schema}]')"
            )
            conn.execute(f"SELECT mssql_exec('{ATTACH_NAME}', ?)", [stmt])
    except duckdb.Error as e:
        raise LoaderConnectionError(
            f"failed to attach database {cfg.database!r} / schema {cfg.schema!r}: {e}"
        ) from e
