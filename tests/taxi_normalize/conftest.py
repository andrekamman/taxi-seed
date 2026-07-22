"""Synthetic parquet family fixtures for taxi_normalize tests.

Each fixture builds tiny parquet files representing schema drift over three
eras — early (2009-ish, old columns), mid (2015-ish, mid-drift), and target
(2024-ish, latest schema). All fixtures live under `tmp_path` per test.
"""
from pathlib import Path

import duckdb
import pytest


@pytest.fixture
def yellow_family(tmp_path: Path) -> Path:
    """Build a synthetic 3-era yellow-taxi drift dataset.

    Returns the raw/ directory containing three files under yellow/<year>/.
    Schema drift illustrated:
      - Era 1 (2009-01): pu_datetime, do_datetime, pickup_latitude, pickup_longitude,
                          passenger_count as DOUBLE
      - Era 2 (2015-06): tpep_pickup_datetime, tpep_dropoff_datetime, PULocationID,
                          DOLocationID, passenger_count as DOUBLE (fractional values present)
      - Era 3 (2024-01): same as Era 2 but passenger_count as BIGINT, adds airport_fee
    """
    raw = tmp_path / "raw" / "yellow"
    (raw / "2009").mkdir(parents=True)
    (raw / "2015").mkdir(parents=True)
    (raw / "2024").mkdir(parents=True)

    conn = duckdb.connect(":memory:")

    # Era 1
    conn.execute(f"""
        COPY (
            SELECT * FROM (VALUES
                (1, TIMESTAMP '2009-01-01 10:00', TIMESTAMP '2009-01-01 10:15',
                    40.7, -74.0, 1.0),
                (2, TIMESTAMP '2009-01-02 11:00', TIMESTAMP '2009-01-02 11:20',
                    40.8, -73.9, 2.0)
            ) AS t(vendorid, pu_datetime, do_datetime, pickup_latitude, pickup_longitude, passenger_count)
        )
        TO '{raw}/2009/yellow_tripdata_2009-01.parquet' (FORMAT PARQUET)
    """)

    # Era 2 — introduces fractional passenger_count so the lossy-cast path fires
    conn.execute(f"""
        COPY (
            SELECT * FROM (VALUES
                (1, TIMESTAMP '2015-06-01 10:00', TIMESTAMP '2015-06-01 10:15', 161, 236, 1.5),
                (2, TIMESTAMP '2015-06-02 11:00', TIMESTAMP '2015-06-02 11:20', 236, 161, 2.0)
            ) AS t(vendorid, tpep_pickup_datetime, tpep_dropoff_datetime,
                   "PULocationID", "DOLocationID", passenger_count)
        )
        TO '{raw}/2015/yellow_tripdata_2015-06.parquet' (FORMAT PARQUET)
    """)

    # Era 3 — target schema; passenger_count is BIGINT, adds airport_fee
    conn.execute(f"""
        COPY (
            SELECT * FROM (VALUES
                (1, TIMESTAMP '2024-01-01 10:00', TIMESTAMP '2024-01-01 10:15', 161, 236, 1, 1.75),
                (2, TIMESTAMP '2024-01-02 11:00', TIMESTAMP '2024-01-02 11:20', 236, 161, 2, 0.00)
            ) AS t(vendorid, tpep_pickup_datetime, tpep_dropoff_datetime,
                   "PULocationID", "DOLocationID", passenger_count, airport_fee)
        )
        TO '{raw}/2024/yellow_tripdata_2024-01.parquet' (FORMAT PARQUET)
    """)

    conn.close()
    return raw


@pytest.fixture
def no_drift_family(tmp_path: Path) -> Path:
    """Family where all files have identical schema — normalizer should be a pure passthrough."""
    raw = tmp_path / "raw" / "green"
    (raw / "2024").mkdir(parents=True)
    (raw / "2025").mkdir(parents=True)

    conn = duckdb.connect(":memory:")
    for year, month in [("2024", "01"), ("2025", "01")]:
        conn.execute(f"""
            COPY (SELECT 1 AS vendorid, TIMESTAMP '{year}-{month}-01' AS pickup_datetime, 5.0 AS trip_distance)
            TO '{raw}/{year}/green_tripdata_{year}-{month}.parquet' (FORMAT PARQUET)
        """)
    conn.close()
    return raw


@pytest.fixture
def target_file(yellow_family: Path) -> Path:
    """Convenience: the Era 3 file in yellow_family."""
    return yellow_family / "2024" / "yellow_tripdata_2024-01.parquet"
