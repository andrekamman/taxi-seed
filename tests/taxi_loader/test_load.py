import duckdb
import pytest

from taxi_loader import load
from taxi_loader.connection import ConnConfig
from taxi_loader.load import (
    build_copy_sql, dest_url, parquet_row_count, year_table,
)


def test_year_table_and_dest_url():
    assert year_table("yellow", 2024) == "yellow_2024"
    assert dest_url("dbo", "yellow_2024") == "mssql://mssql/dbo/yellow_2024"


def test_build_copy_sql_append_options():
    sql = build_copy_sql(
        ["/a/2024-01.parquet"], "mssql://mssql/dbo/yellow_2024",
        create_table=False, replace=False, flush_rows=100000, tablock=True,
    )
    assert "read_parquet(['/a/2024-01.parquet'])" in sql
    assert "TO 'mssql://mssql/dbo/yellow_2024'" in sql
    assert "FORMAT 'bcp'" in sql
    assert "CREATE_TABLE false" in sql
    assert "REPLACE false" in sql
    assert "FLUSH_ROWS 100000" in sql
    assert "TABLOCK true" in sql


def test_build_copy_sql_multi_file_list():
    sql = build_copy_sql(
        ["/a/2024-01.parquet", "/a/2024-02.parquet"], "mssql://mssql/dbo/yellow_2024",
        create_table=False, replace=False, flush_rows=50000, tablock=True,
    )
    assert "'/a/2024-01.parquet', '/a/2024-02.parquet'" in sql


def test_parquet_row_count(tmp_path):
    conn = duckdb.connect(":memory:")
    p = tmp_path / "x.parquet"
    conn.execute(f"COPY (SELECT i FROM range(7) t(i)) TO '{p}' (FORMAT PARQUET)")
    assert parquet_row_count(conn, p) == 7


# --- race-safe DDL: several workers load different months of one year -------

class _FakeConn:
    def __init__(self, on_execute):
        self._on_execute = on_execute

    def execute(self, sql, params=None):
        return self._on_execute(sql, params)


def _cfg():
    return ConnConfig(host="h", port=1433, database="taxi", schema="dbo",
                      user="sa", password="pw")


def _duplicate_object(sql, params=None):
    raise duckdb.Error("There is already an object named 'fhvhv_2019'")


def test_ensure_table_tolerates_a_concurrent_creator(monkeypatch):
    """IF NOT EXISTS + CREATE is not atomic across sessions; losing the race to
    a sibling month of the same year is expected, not an error."""
    calls = {"exists": 0}

    def table_exists(conn, cfg, table):
        calls["exists"] += 1
        return calls["exists"] > 1          # absent first, present on re-check

    monkeypatch.setattr(load, "table_exists", table_exists)
    monkeypatch.setattr(load, "build_create_table_sql",
                        lambda *a, **kw: "CREATE TABLE dbo.fhvhv_2019 (a INT)")

    load._ensure_table(_FakeConn(_duplicate_object), _cfg(), "fhvhv_2019",
                       "x.parquet")
    assert calls["exists"] == 2


def test_ensure_table_reraises_when_the_table_is_still_absent(monkeypatch):
    """A real DDL failure must not be swallowed by the race tolerance."""
    monkeypatch.setattr(load, "table_exists", lambda conn, cfg, table: False)
    monkeypatch.setattr(load, "build_create_table_sql",
                        lambda *a, **kw: "CREATE TABLE dbo.fhvhv_2019 (a INT)")

    with pytest.raises(duckdb.Error):
        load._ensure_table(_FakeConn(_duplicate_object), _cfg(), "fhvhv_2019",
                           "x.parquet")


def test_ensure_table_does_nothing_when_it_already_exists(monkeypatch):
    monkeypatch.setattr(load, "table_exists", lambda conn, cfg, table: True)

    def explode(sql, params=None):
        raise AssertionError("should not have issued DDL")

    load._ensure_table(_FakeConn(explode), _cfg(), "fhvhv_2019", "x.parquet")
