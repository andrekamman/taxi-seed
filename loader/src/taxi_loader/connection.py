"""Connect DuckDB to SQL Server via the vendored mssql extension.

LOAD the extension from a vendored extension directory, assert its identity, apply the
session settings the loader depends on, provision the database and schema, and ATTACH
the target. All errors here are exit-2 (nothing loaded).

The extension is NOT installed from the community registry at runtime. The registry
rebuilds -- DuckDB 1.5.5 has served three different builds of this extension -- so what
`INSTALL` returns today is not what was tested. See taxi_loader.extension.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb

from taxi_loader import extension

ATTACH_NAME = "mssql"
BOOT_ATTACH_NAME = "mssql_boot"
SECRET_NAME = "taxi_mssql"

# Session settings the loader depends on, applied to every connection.
#
# mssql_copy_tablock: defaults to 'auto', which resolves to OFF for any clustered
#   target. Measured on this repo's page-compressed heaps (issue #16), TABLOCK=false is
#   3.2x slower and produces a 36% larger table -- page-latch contention at the insert
#   point, not lost compression. Set it rather than let table shape decide.
# mssql_exec_invalidate_cache: the catalog metadata cache returns stale results within a
#   session, so a table created through mssql_exec can be invisible to the catalog in the
#   same session. This loader issues DDL constantly, so it must be on.
# mssql_query_timeout: defaults to 30 SECONDS. A COUNT_BIG over a large year table and
#   other mssql_scan reads can exceed that, and the timeout surfaces as what looks like
#   a connection failure. 0 disables it.
#
# mssql_copy_parallel_writers is NOT set here: its default (0) derives from DuckDB's
# thread count capped at 8, which is the configuration issue #16 measured as fastest.
# --writers overrides it when a caller needs to.
SESSION_SETTINGS: dict[str, object] = {
    "mssql_copy_tablock": "true",
    "mssql_exec_invalidate_cache": True,
    "mssql_query_timeout": 0,
}

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


def secret_name(attach_name: str) -> str:
    return f"{SECRET_NAME}_{attach_name}"


def create_secret(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig, database: str,
                  attach_name: str) -> str:
    """Create (or replace) an mssql secret for one database, and return its name.

    Every field is a bind parameter, so the password never appears in a SQL string, in
    a log line, or in an error message -- `duckdb_secrets()` renders it as redacted.
    This replaces the connection-string form, which ATTACH could only accept as an
    interpolated literal because ATTACH takes no bind parameters.

    Note there is no trust-server-certificate option on an mssql secret. USE_ENCRYPT
    encrypts without validating the certificate, which is the same effective posture as
    the `Encrypt=yes;TrustServerCertificate=yes` it replaces -- required for the
    self-signed certificate a SQL Server container presents.
    """
    name = secret_name(attach_name)
    try:
        conn.execute(
            f"CREATE OR REPLACE SECRET {name} (TYPE mssql, HOST ?, PORT ?, "
            f"DATABASE ?, USER ?, PASSWORD ?, USE_ENCRYPT true)",
            [cfg.host, cfg.port, database, cfg.user, cfg.password],
        )
    except duckdb.Error as e:
        raise LoaderConnectionError(
            f"failed to create the mssql secret for database {database!r}: {e}"
        ) from e
    return name


def connect_duckdb(*, extension_root: Path | None = None,
                   writers: int | None = None) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection with the vendored mssql extension loaded and asserted.

    Vendors the extension on first use if the directory is empty -- idempotent, and the
    download is verified against a recorded SHA-256 before it is accepted.
    """
    root = Path(extension_root) if extension_root else extension.default_root()
    try:
        path = extension.extension_path(root, extension.platform_key())
        if not path.exists():
            # Say so: a silent 40 MB download on first run is worse than a noisy one.
            print(f"vendoring the mssql extension into {root} (first run)...",
                  file=sys.stderr)
            extension.fetch(root)
    except extension.ExtensionError as e:
        raise LoaderConnectionError(
            f"could not vendor the mssql extension: {e}\n"
            f"Run `taxi-vendor-extension` (optionally with --root) and retry."
        ) from e

    conn = duckdb.connect(":memory:", config={"extension_directory": str(root)})
    try:
        # LOAD by name out of the vendored directory. No INSTALL, no network. DuckDB
        # still enforces the extension signature on this path, under the default
        # config -- allow_unsigned_extensions is deliberately NOT set.
        conn.execute(f"LOAD {extension.EXTENSION_NAME};")
    except duckdb.Error as e:
        raise LoaderConnectionError(
            f"failed to load the vendored mssql extension from {root}: {e}"
        ) from e
    try:
        extension.assert_loaded(conn)
    except extension.ExtensionError as e:
        raise LoaderConnectionError(str(e)) from e

    settings = dict(SESSION_SETTINGS)
    if writers is not None:
        settings["mssql_copy_parallel_writers"] = int(writers)
    for name, value in settings.items():
        literal = f"'{value}'" if isinstance(value, str) else str(value).lower() \
            if isinstance(value, bool) else str(value)
        try:
            conn.execute(f"SET {name} = {literal}")
        except duckdb.Error as e:
            raise LoaderConnectionError(f"failed to set {name}: {e}") from e
    return conn


def ensure_database(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig, *,
                    require_existing: bool = False) -> None:
    """Create cfg.database if absent, by attaching master and running CREATE DATABASE.

    require_existing=True verifies instead of creating, and fails if the database is
    absent. The seeder uses it: it provisions a presized database several steps earlier,
    and a CREATE here would quietly produce a server-default-sized one if that step was
    skipped or failed soft -- turning a provisioning bug into a wrong-shaped database
    nobody notices until it is full.
    """
    try:
        name = create_secret(conn, cfg, "master", BOOT_ATTACH_NAME)
        conn.execute(f"ATTACH '' AS {BOOT_ATTACH_NAME} (TYPE mssql, SECRET {name})")
        if require_existing:
            row = conn.execute(
                f"SELECT d FROM mssql_scan('{BOOT_ATTACH_NAME}', ?)",
                [f"SELECT DB_ID('{_sql_str(cfg.database)}') AS d"],
            ).fetchone()
            if not row or row[0] is None:
                raise LoaderConnectionError(
                    f"database {cfg.database!r} does not exist, and --require-existing-"
                    f"database was given. Provision it first (the seeder does this in "
                    f"its provision step); refusing to create a server-default-sized one."
                )
        else:
            stmt = (
                f"IF DB_ID('{_sql_str(cfg.database)}') IS NULL "
                f"EXEC('CREATE DATABASE [{cfg.database}]')"
            )
            conn.execute(f"SELECT mssql_exec('{BOOT_ATTACH_NAME}', ?)", [stmt])
    except LoaderConnectionError:
        raise
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
        name = create_secret(conn, cfg, cfg.database, ATTACH_NAME)
        conn.execute(f"ATTACH '' AS {ATTACH_NAME} (TYPE mssql, SECRET {name})")
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
