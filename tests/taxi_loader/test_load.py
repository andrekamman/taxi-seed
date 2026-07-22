import duckdb

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
