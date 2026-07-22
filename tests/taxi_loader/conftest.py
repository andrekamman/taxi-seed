"""Synthetic *normalized* parquet families for taxi_loader tests.

A normalized family has ONE uniform schema across every file of a type, laid out
as <root>/<type>/<year>/<type>_tripdata_<year>-<mm>.parquet — matching the
normalizer's raw-normalized/ output.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

# One uniform schema per type. Chosen to exercise the type map: BIGINT, DOUBLE,
# VARCHAR, TIMESTAMP.
TYPE_COLUMNS = {
    "yellow": ["vendorid BIGINT", "tpep_pickup_datetime TIMESTAMP",
               "trip_distance DOUBLE", "store_and_fwd_flag VARCHAR"],
    "green": ["vendorid BIGINT", "lpep_pickup_datetime TIMESTAMP",
              "trip_distance DOUBLE", "store_and_fwd_flag VARCHAR"],
}


def write_month(conn: duckdb.DuckDBPyConnection, root: Path, data_type: str,
                year: int, month: int, rows: int) -> Path:
    """Write one synthetic normalized month file; return its path."""
    d = root / data_type / str(year)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{data_type}_tripdata_{year}-{month:02d}.parquet"
    pick = "tpep_pickup_datetime" if data_type == "yellow" else "lpep_pickup_datetime"
    conn.execute(f"""
        COPY (
            SELECT i AS vendorid,
                   TIMESTAMP '{year}-{month:02d}-01' + (i * INTERVAL 1 HOUR) AS {pick},
                   CAST(i * 1.5 AS DOUBLE) AS trip_distance,
                   CASE WHEN i % 2 = 0 THEN 'N' ELSE 'Y' END AS store_and_fwd_flag
            FROM range({rows}) t(i)
        ) TO '{path}' (FORMAT PARQUET)
    """)
    return path


@pytest.fixture
def normalized_family(tmp_path: Path) -> Path:
    """Build yellow/2023 (2 months) and yellow/2024 (1 month). Return the root dir."""
    root = tmp_path / "raw-normalized"
    conn = duckdb.connect(":memory:")
    write_month(conn, root, "yellow", 2023, 1, rows=3)
    write_month(conn, root, "yellow", 2023, 2, rows=4)
    write_month(conn, root, "yellow", 2024, 1, rows=5)
    conn.close()
    return root
