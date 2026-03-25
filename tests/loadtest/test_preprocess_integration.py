import json
import pytest
from pathlib import Path

import duckdb

from loadtest.preprocess import run_preprocess


@pytest.fixture
def integration_setup(tmp_path):
    """Create parquet files and config for full integration test."""
    parquet_dir = tmp_path / "raw"
    parquet_dir.mkdir()
    db = duckdb.connect()
    db.sql(f"""
        COPY (
            SELECT
                '2026-01-15 08:30:00'::TIMESTAMP AS tpep_pickup_datetime,
                '2026-01-15 09:15:00'::TIMESTAMP AS tpep_dropoff_datetime,
                2::INTEGER AS passenger_count,
                3.4::DOUBLE AS trip_distance,
                15.50::DOUBLE AS fare_amount,
                3.00::DOUBLE AS tip_amount
            FROM range(50)
        ) TO '{parquet_dir}/test.parquet' (FORMAT PARQUET)
    """)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"""
data_sources:
  yellow_trips:
    path: "{parquet_dir}/*.parquet"
    chunk_size: 20
    key_columns: [pickup_time, dropoff_time]
    columns:
      pickup_time: tpep_pickup_datetime
      dropoff_time: tpep_dropoff_datetime
      passenger_count: passenger_count
      trip_distance: trip_distance
      fare_amount: fare_amount
      tip_amount: tip_amount

targets:
  test_server:
    host: localhost
    port: 1433
    database: test_db
    username: sa
    password: ${{MSSQL_PASSWORD}}
    table: taxi_trips

scenarios:
  basic_load:
    target: test_server
    data_source: yellow_trips
    ordering: parallel
    workload:
      insert: 80
      update: 15
      delete: 5
    think_time:
      min: 200ms
      max: 1s
    k6:
      executor: constant-vus
      vus: 5
      duration: 1m
""")

    output_dir = tmp_path / "k6_output"
    return config_path, output_dir


def test_full_preprocess(integration_setup):
    config_path, output_dir = integration_setup

    run_preprocess(config_path, output_dir)

    # Check output structure
    assert (output_dir / "test.js").exists()
    assert (output_dir / "schema" / "test_server_taxi_trips.sql").exists()
    assert (output_dir / "scenarios" / "basic_load.json").exists()
    assert (output_dir / "data" / "yellow_trips" / "chunk_0000.json").exists()
    assert (output_dir / "data" / "yellow_trips" / "chunks.json").exists()

    # Check chunk index
    with open(output_dir / "data" / "yellow_trips" / "chunks.json") as f:
        chunk_list = json.load(f)
    assert len(chunk_list) == 3  # 50 rows / 20 per chunk

    # Check manifest
    with open(output_dir / "scenarios" / "basic_load.json") as f:
        manifest = json.load(f)
    assert manifest["table"] == "taxi_trips"
    assert manifest["num_chunks"] == 3
    assert "${MSSQL_PASSWORD}" in manifest["connection_string"]

    # Check CREATE TABLE has correct types
    schema_sql = (output_dir / "schema" / "test_server_taxi_trips.sql").read_text()
    assert "pickup_time DATETIME2" in schema_sql
    assert "passenger_count INT" in schema_sql
    assert "trip_distance FLOAT" in schema_sql

    # Check test.js is valid-looking
    test_js = (output_dir / "test.js").read_text()
    assert "basic_load" in test_js
    assert "import sql from" in test_js
