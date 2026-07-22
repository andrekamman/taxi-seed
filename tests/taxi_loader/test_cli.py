import duckdb
import pytest

from taxi_loader.cli import discover_month_files, main, parse_args


def test_parse_args_defaults():
    ns = parse_args([])
    assert ns.data_type is None
    assert ns.host == "localhost"
    assert ns.port == 1433
    assert ns.database == "taxi"
    assert ns.schema == "dbo"
    assert ns.user == "sa"
    assert ns.input_dir == "raw-normalized"
    assert ns.flush_rows == 100000
    assert ns.full_refresh is False
    assert ns.dry_run is False


def test_discover_month_files(normalized_family):
    conn = duckdb.connect(":memory:")
    months = discover_month_files(conn, normalized_family, "yellow")
    got = sorted((m.year, m.month, m.source_row_count) for m in months)
    assert got == [(2023, 1, 3), (2023, 2, 4), (2024, 1, 5)]


def test_missing_password_is_exit_2(monkeypatch, normalized_family):
    monkeypatch.delenv("MSSQL_PASSWORD", raising=False)
    rc = main(["yellow", "--input-dir", str(normalized_family)])
    assert rc == 2


def test_bad_schema_is_exit_2(monkeypatch, normalized_family):
    monkeypatch.setenv("MSSQL_PASSWORD", "pw")
    rc = main(["yellow", "--schema", "bad-schema", "--input-dir", str(normalized_family)])
    assert rc == 2
