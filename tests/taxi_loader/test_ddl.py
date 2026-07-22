import duckdb

from taxi_loader.load import build_create_table_sql, describe_parquet_types


def test_describe_returns_column_types(normalized_family):
    conn = duckdb.connect(":memory:")
    f = normalized_family / "yellow" / "2024" / "yellow_tripdata_2024-01.parquet"
    types = describe_parquet_types(conn, f)
    assert types["vendorid"] == "BIGINT"
    assert types["trip_distance"] == "DOUBLE"
    assert types["store_and_fwd_flag"] == "VARCHAR"


def test_create_table_sql_maps_types_and_keeps_column_order(normalized_family):
    conn = duckdb.connect(":memory:")
    f = normalized_family / "yellow" / "2024" / "yellow_tripdata_2024-01.parquet"
    sql = build_create_table_sql(conn, "dbo.yellow_2024", f)
    assert sql.startswith("CREATE TABLE dbo.yellow_2024 (")
    assert "vendorid BIGINT" in sql
    assert "trip_distance FLOAT" in sql            # DOUBLE -> FLOAT via taxi_shared
    assert "store_and_fwd_flag NVARCHAR(MAX)" in sql
    assert "tpep_pickup_datetime DATETIME2" in sql
    assert not sql.rstrip().endswith(";")          # single statement for mssql_exec
    # column order matches parquet order
    assert sql.index("vendorid") < sql.index("tpep_pickup_datetime") < sql.index("trip_distance")
