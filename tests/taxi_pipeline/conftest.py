"""Synthetic drift parquet for curate tests: a rename, a float->int lossy cast, a data-loss drop."""
from pathlib import Path

import duckdb
import pytest


@pytest.fixture
def drift_family(tmp_path: Path) -> Path:
    """raw/yellow with three eras. Returns raw/yellow (per-type dir).

    Drift exercised:
      - pu_datetime -> tpep_pickup_datetime (rename)
      - passenger_count DOUBLE (fractional) -> BIGINT target (float->int lossy cast)
      - pickup_latitude present early with data, absent from target (data-loss drop)
    """
    raw = tmp_path / "raw" / "yellow"
    (raw / "2009").mkdir(parents=True)
    (raw / "2015").mkdir(parents=True)
    (raw / "2024").mkdir(parents=True)
    conn = duckdb.connect(":memory:")
    conn.execute(f"""
        COPY (SELECT * FROM (VALUES
            (1, TIMESTAMP '2009-01-01 10:00', 40.71, CAST(1.0 AS DOUBLE)),
            (2, TIMESTAMP '2009-01-02 11:00', 40.72, CAST(2.0 AS DOUBLE)),
            (3, TIMESTAMP '2009-01-03 12:00', 40.73, CAST(1.0 AS DOUBLE))
        ) AS t(vendorid, pu_datetime, pickup_latitude, passenger_count))
        TO '{raw}/2009/yellow_tripdata_2009-01.parquet' (FORMAT PARQUET)
    """)
    conn.execute(f"""
        COPY (SELECT * FROM (VALUES
            (1, TIMESTAMP '2015-06-01 10:00', CAST(1.5 AS DOUBLE)),
            (2, TIMESTAMP '2015-06-02 11:00', CAST(2.0 AS DOUBLE)),
            (3, TIMESTAMP '2015-06-03 12:00', CAST(3.5 AS DOUBLE))
        ) AS t(vendorid, tpep_pickup_datetime, passenger_count))
        TO '{raw}/2015/yellow_tripdata_2015-06.parquet' (FORMAT PARQUET)
    """)
    conn.execute(f"""
        COPY (SELECT * FROM (VALUES
            (1, TIMESTAMP '2024-01-01 10:00', CAST(1 AS BIGINT)),
            (2, TIMESTAMP '2024-01-02 11:00', CAST(2 AS BIGINT)),
            (3, TIMESTAMP '2024-01-03 12:00', CAST(3 AS BIGINT))
        ) AS t(vendorid, tpep_pickup_datetime, passenger_count))
        TO '{raw}/2024/yellow_tripdata_2024-01.parquet' (FORMAT PARQUET)
    """)
    conn.close()
    return raw
