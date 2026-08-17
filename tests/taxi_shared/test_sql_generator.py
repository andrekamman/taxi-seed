import pytest

from taxi_shared.sql_generator import (
    generate_insert_sql,
    generate_update_sql,
    generate_delete_sql,
    generate_create_table_sql,
)


COLUMNS = {
    "pickup_time": "DATETIME2",
    "dropoff_time": "DATETIME2",
    "passenger_count": "INT",
    "trip_distance": "FLOAT",
    "fare_amount": "FLOAT",
    "tip_amount": "FLOAT",
}
KEY_COLUMNS = ["pickup_time", "dropoff_time"]
TABLE = "taxi_trips"


def test_generate_insert_sql():
    sql = generate_insert_sql(TABLE, COLUMNS)
    assert "INSERT INTO taxi_trips" in sql
    assert "pickup_time" in sql
    assert "tip_amount" in sql
    assert "@p1" in sql
    assert "@p6" in sql


def test_generate_update_sql():
    sql = generate_update_sql(TABLE, COLUMNS, KEY_COLUMNS)
    assert "UPDATE taxi_trips SET" in sql
    assert "passenger_count" in sql
    assert "fare_amount" in sql
    assert "WHERE" in sql
    assert "pickup_time = @p" in sql
    assert "dropoff_time = @p" in sql


def test_generate_update_sql_key_not_in_set():
    sql = generate_update_sql(TABLE, COLUMNS, KEY_COLUMNS)
    set_clause = sql.split("WHERE")[0]
    set_part = set_clause.split("SET")[1]
    assert "pickup_time" not in set_part
    assert "dropoff_time" not in set_part


def test_generate_delete_sql():
    sql = generate_delete_sql(TABLE, KEY_COLUMNS)
    assert "DELETE FROM taxi_trips" in sql
    assert "WHERE" in sql
    assert "pickup_time = @p1" in sql
    assert "dropoff_time = @p2" in sql


def test_generate_create_table_sql():
    sql = generate_create_table_sql(TABLE, COLUMNS)
    assert "CREATE TABLE taxi_trips" in sql
    assert "pickup_time DATETIME2" in sql
    assert "passenger_count INT" in sql
    assert "fare_amount FLOAT" in sql


def test_create_table_is_page_compressed():
    sql = generate_create_table_sql("dbo.yellow_2015", {"a": "BIGINT"})
    assert sql.endswith(") WITH (DATA_COMPRESSION = PAGE);")


def test_create_table_still_lists_columns():
    sql = generate_create_table_sql("dbo.t", {"a": "BIGINT", "b": "DATETIME2"})
    assert "a BIGINT" in sql
    assert "b DATETIME2" in sql
