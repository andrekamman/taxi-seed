"""End-to-end against SQL Server in Docker. Skips when MSSQL_PASSWORD is unset,
so `pytest` stays green on a laptop with no SQL Server.

Bring a server up first, e.g.:
  docker run -d --name mssql-it -e ACCEPT_EULA=Y \
    -e MSSQL_SA_PASSWORD='Str0ng_Passw0rd!' -p 1433:1433 \
    mcr.microsoft.com/mssql/server:2022-latest
Then: MSSQL_PASSWORD='Str0ng_Passw0rd!' uv run --extra test pytest tests/taxi_loader/test_load_integration.py

Or run everything (container bring-up + these tests + the pipeline e2e) with:
    ./scripts/e2e-smoke.sh

Note: the `mssql` extension keeps its ATTACH "context" alive process-wide across
DuckDB connections, so only ONE connection may hold the `mssql` attach at a time.
These tests therefore provision, then release the attach, and each verification
opens a short-lived attached connection that detaches before the next `main()`.
"""
from __future__ import annotations

import os
import uuid
from contextlib import contextmanager

import duckdb
import pytest

from conftest import write_month  # helper from tests/taxi_loader/conftest.py

from taxi_loader import load, manifest
from taxi_loader.cli import main
from taxi_loader.connection import (
    ATTACH_NAME, ConnConfig, _sql_str, attach_target, build_conn_string,
    connect_duckdb, ensure_database,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("MSSQL_PASSWORD"),
    reason="MSSQL_PASSWORD unset; skipping SQL Server integration tests",
)


@pytest.fixture
def cfg():
    # Unique schema per test run for isolation within the shared 'taxi' DB.
    schema = "t" + uuid.uuid4().hex[:8]
    return ConnConfig(
        host=os.environ.get("MSSQL_HOST", "localhost"),
        port=int(os.environ.get("MSSQL_PORT", "1433")),
        database="taxi", schema=schema, user=os.environ.get("MSSQL_USER", "sa"),
        password=os.environ["MSSQL_PASSWORD"],
    )


def _detach_close(conn) -> None:
    try:
        conn.execute(f"DETACH {ATTACH_NAME}")
    except duckdb.Error:
        pass
    conn.close()


@pytest.fixture
def prepared(cfg):
    """Provision the database/schema/manifest, then RELEASE the mssql attach so
    main() (which attaches the same process-global context) can run."""
    conn = connect_duckdb()
    ensure_database(conn, cfg)
    attach_target(conn, cfg)          # creates the unique schema
    manifest.ensure_manifest_table(conn, cfg)
    _detach_close(conn)
    return cfg


@contextmanager
def attached(cfg):
    """A short-lived DuckDB connection with the target DB attached read-side;
    detaches on exit so it never collides with a concurrent main()."""
    conn = connect_duckdb()
    attach_target(conn, cfg, create_schema=False)
    try:
        yield conn
    finally:
        _detach_close(conn)


def _count(cfg, table):
    with attached(cfg) as conn:
        return load.count_year_table(conn, cfg, table)


def _read_manifest(cfg):
    with attached(cfg) as conn:
        return manifest.read_manifest(conn, cfg, "yellow")


def _run(cfg, root, extra=None):
    argv = ["yellow", "--host", cfg.host, "--port", str(cfg.port),
            "--database", cfg.database, "--schema", cfg.schema,
            "--user", cfg.user, "--input-dir", str(root)]
    return main(argv + (extra or []))


def test_end_to_end_load_counts_and_manifest(prepared, normalized_family):
    cfg = prepared
    assert _run(cfg, normalized_family) == 0
    assert _count(cfg, "yellow_2023") == 7      # 3 + 4
    assert _count(cfg, "yellow_2024") == 5
    rows = _read_manifest(cfg)
    assert sorted((r.year, r.month, r.row_count) for r in rows) == \
        [(2023, 1, 3), (2023, 2, 4), (2024, 1, 5)]


def test_immediate_rerun_is_full_noop(prepared, normalized_family):
    cfg = prepared
    assert _run(cfg, normalized_family) == 0
    assert _run(cfg, normalized_family) == 0
    assert _count(cfg, "yellow_2023") == 7      # unchanged, no duplicates
    assert _count(cfg, "yellow_2024") == 5


def test_new_month_appends_only_it(prepared, normalized_family):
    cfg = prepared
    assert _run(cfg, normalized_family) == 0
    # Drop a new month into 2024.
    write_month(duckdb.connect(":memory:"), normalized_family, "yellow", 2024, 2, rows=8)
    assert _run(cfg, normalized_family) == 0
    assert _count(cfg, "yellow_2024") == 13      # 5 + 8
    assert _count(cfg, "yellow_2023") == 7       # untouched


def test_changed_month_reloads_whole_year(prepared, normalized_family):
    cfg = prepared
    assert _run(cfg, normalized_family) == 0
    # Rewrite 2023-01 with a different row count -> whole 2023 rebuilds.
    write_month(duckdb.connect(":memory:"), normalized_family, "yellow", 2023, 1, rows=10)
    assert _run(cfg, normalized_family) == 0
    assert _count(cfg, "yellow_2023") == 14      # 10 + 4
    rows = {r.month: r.row_count for r in _read_manifest(cfg) if r.year == 2023}
    assert rows == {1: 10, 2: 4}


def test_partial_load_recovery_via_integrity_check(prepared, normalized_family):
    cfg = prepared
    assert _run(cfg, normalized_family) == 0
    # Simulate a partial prior load: extra committed rows with no manifest row.
    table = load.year_table("yellow", 2024)
    with attached(cfg) as conn:
        conn.execute(
            f"SELECT mssql_exec('{ATTACH_NAME}', ?)",
            [f"INSERT INTO {cfg.schema}.{table} "
             f"(vendorid, tpep_pickup_datetime, trip_distance, store_and_fwd_flag) "
             f"VALUES (99, SYSUTCDATETIME(), 1.0, 'N')"],
        )
    assert _count(cfg, table) == 6               # 5 + 1 injected
    # Next run detects table(6) != manifest(5) -> reload year cleanly.
    assert _run(cfg, normalized_family) == 0
    assert _count(cfg, table) == 5               # rebuilt, no duplicate/injected row


def test_dry_run_touches_nothing(normalized_family, capsys):
    """Dry-run against a database that does not exist yet must be fully
    read-only: no CREATE DATABASE, no CREATE SCHEMA, no CREATE TABLE — yet it
    still prints an accurate reconciliation plan (everything looks "fresh")."""
    dbname = "drytest_" + uuid.uuid4().hex[:8]
    cfg = ConnConfig(
        host=os.environ.get("MSSQL_HOST", "localhost"),
        port=int(os.environ.get("MSSQL_PORT", "1433")),
        database=dbname, schema="t" + uuid.uuid4().hex[:8],
        user=os.environ.get("MSSQL_USER", "sa"),
        password=os.environ["MSSQL_PASSWORD"],
    )
    # Deliberately do NOT provision: no ensure_database, no attach_target.
    rc = _run(cfg, normalized_family, extra=["--dry-run"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "append" in out or "reload" in out   # plan was printed
    assert cfg.password not in out              # never logged

    # The database must NOT have been created by the dry-run.
    check_conn = connect_duckdb()
    check_conn.execute(
        f"ATTACH '{_sql_str(build_conn_string(cfg, 'master'))}' "
        "AS mssql_check (TYPE mssql)"
    )
    row = check_conn.execute(
        "SELECT id FROM mssql_scan('mssql_check', ?)",
        [f"SELECT DB_ID('{dbname}') AS id"],
    ).fetchone()
    assert row is not None and row[0] is None
    try:
        check_conn.execute("DETACH mssql_check")
    except duckdb.Error:
        pass
    check_conn.close()


def test_dry_run_on_prepared_schema_creates_no_tables(prepared, normalized_family, capsys):
    """Dry-run against an already-provisioned schema prints a plan but creates
    no year tables."""
    cfg = prepared
    assert _run(cfg, normalized_family, extra=["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "append" in out or "reload" in out
    with attached(cfg) as conn:
        assert not load.table_exists(conn, cfg, "yellow_2024")
