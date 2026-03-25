import json
import pytest
from pathlib import Path

import duckdb

from loadtest.data_export import export_chunks, get_schema


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_parquet(tmp_path):
    """Create a small parquet file for testing."""
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
            FROM range(25)
        ) TO '{tmp_path}/test.parquet' (FORMAT PARQUET)
    """)
    return tmp_path


def test_export_chunks_creates_files(sample_parquet, tmp_path):
    output_dir = tmp_path / "output"
    columns = {
        "pickup_time": "tpep_pickup_datetime",
        "dropoff_time": "tpep_dropoff_datetime",
        "passenger_count": "passenger_count",
        "trip_distance": "trip_distance",
        "fare_amount": "fare_amount",
        "tip_amount": "tip_amount",
    }
    num_chunks = export_chunks(
        parquet_glob=str(sample_parquet / "*.parquet"),
        columns=columns,
        chunk_size=10,
        output_dir=output_dir / "yellow_trips",
    )
    assert num_chunks == 3  # 25 rows / 10 per chunk = 3 chunks
    assert (output_dir / "yellow_trips" / "chunk_0000.json").exists()
    assert (output_dir / "yellow_trips" / "chunk_0001.json").exists()
    assert (output_dir / "yellow_trips" / "chunk_0002.json").exists()


def test_export_chunks_json_format(sample_parquet, tmp_path):
    output_dir = tmp_path / "output"
    columns = {
        "pickup_time": "tpep_pickup_datetime",
        "fare_amount": "fare_amount",
    }
    export_chunks(
        parquet_glob=str(sample_parquet / "*.parquet"),
        columns=columns,
        chunk_size=100,
        output_dir=output_dir / "data",
    )
    with open(output_dir / "data" / "chunk_0000.json") as f:
        rows = json.load(f)
    assert len(rows) == 25
    assert "pickup_time" in rows[0]
    assert "fare_amount" in rows[0]
    # Should only have mapped columns, not originals
    assert "tpep_pickup_datetime" not in rows[0]


def test_export_chunks_max_rows(sample_parquet, tmp_path):
    output_dir = tmp_path / "output"
    columns = {
        "pickup_time": "tpep_pickup_datetime",
        "fare_amount": "fare_amount",
    }
    num_chunks = export_chunks(
        parquet_glob=str(sample_parquet / "*.parquet"),
        columns=columns,
        chunk_size=10,
        output_dir=output_dir / "data",
        max_rows=15,
    )
    assert num_chunks == 2  # 15 rows / 10 per chunk = 2 chunks
    with open(output_dir / "data" / "chunk_0000.json") as f:
        rows0 = json.load(f)
    with open(output_dir / "data" / "chunk_0001.json") as f:
        rows1 = json.load(f)
    assert len(rows0) == 10
    assert len(rows1) == 5


def test_export_chunks_no_matching_files(tmp_path):
    with pytest.raises(FileNotFoundError, match="No parquet files"):
        export_chunks(
            parquet_glob=str(tmp_path / "nonexistent" / "*.parquet"),
            columns={"a": "b"},
            chunk_size=10,
            output_dir=tmp_path / "output",
        )


def test_get_schema(sample_parquet):
    schema = get_schema(str(sample_parquet / "*.parquet"))
    assert "tpep_pickup_datetime" in schema
    assert "TIMESTAMP" in schema["tpep_pickup_datetime"].upper()
