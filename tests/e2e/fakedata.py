"""Code-driven synthetic taxi data for the CI end-to-end smoke.

Generates, per type, the pinned ``target:`` reference parquet (from the canonical
schemas below) plus a tiny "drift-era" raw parquet shaped to exercise the real
committed mapping (normalize/mappings/<type>.yaml): renames, lossy_casts,
value_maps and acknowledged_data_loss. No parquet is committed; everything is
written into a tmp workroot at test time.
"""
from __future__ import annotations

from pathlib import Path

import duckdb

DATA_TYPES = ("yellow", "green", "fhv", "fhvhv")
TARGET_YEAR = 2026
DRIFT_YEAR = 2015

TARGET_FILE = {
    "yellow": "yellow_tripdata_2026-05.parquet",
    "green": "green_tripdata_2026-05.parquet",
    "fhv": "fhv_tripdata_2026-04.parquet",
    "fhvhv": "fhvhv_tripdata_2026-05.parquet",
}

TARGET_COLUMNS = {
    "yellow": [
        ("VendorID", "INTEGER"), ("tpep_pickup_datetime", "TIMESTAMP"),
        ("tpep_dropoff_datetime", "TIMESTAMP"), ("passenger_count", "BIGINT"),
        ("trip_distance", "DOUBLE"), ("RatecodeID", "BIGINT"),
        ("store_and_fwd_flag", "VARCHAR"), ("PULocationID", "INTEGER"),
        ("DOLocationID", "INTEGER"), ("payment_type", "BIGINT"),
        ("fare_amount", "DOUBLE"), ("extra", "DOUBLE"), ("mta_tax", "DOUBLE"),
        ("tip_amount", "DOUBLE"), ("tolls_amount", "DOUBLE"),
        ("improvement_surcharge", "DOUBLE"), ("total_amount", "DOUBLE"),
        ("congestion_surcharge", "DOUBLE"), ("Airport_fee", "DOUBLE"),
        ("cbd_congestion_fee", "DOUBLE"),
    ],
    "green": [
        ("VendorID", "INTEGER"), ("lpep_pickup_datetime", "TIMESTAMP"),
        ("lpep_dropoff_datetime", "TIMESTAMP"), ("store_and_fwd_flag", "VARCHAR"),
        ("RatecodeID", "BIGINT"), ("PULocationID", "INTEGER"),
        ("DOLocationID", "INTEGER"), ("passenger_count", "BIGINT"),
        ("trip_distance", "DOUBLE"), ("fare_amount", "DOUBLE"), ("extra", "DOUBLE"),
        ("mta_tax", "DOUBLE"), ("tip_amount", "DOUBLE"), ("tolls_amount", "DOUBLE"),
        ("ehail_fee", "DOUBLE"), ("improvement_surcharge", "DOUBLE"),
        ("total_amount", "DOUBLE"), ("payment_type", "BIGINT"),
        ("trip_type", "BIGINT"), ("congestion_surcharge", "DOUBLE"),
        ("cbd_congestion_fee", "DOUBLE"),
    ],
    "fhv": [
        ("dispatching_base_num", "VARCHAR"), ("pickup_datetime", "TIMESTAMP"),
        ("dropOff_datetime", "TIMESTAMP"), ("PUlocationID", "BIGINT"),
        ("DOlocationID", "BIGINT"), ("SR_Flag", "BIGINT"),
        ("Affiliated_base_number", "VARCHAR"),
    ],
    "fhvhv": [
        ("hvfhs_license_num", "VARCHAR"), ("dispatching_base_num", "VARCHAR"),
        ("originating_base_num", "VARCHAR"), ("request_datetime", "TIMESTAMP"),
        ("on_scene_datetime", "TIMESTAMP"), ("pickup_datetime", "TIMESTAMP"),
        ("dropoff_datetime", "TIMESTAMP"), ("PULocationID", "INTEGER"),
        ("DOLocationID", "INTEGER"), ("trip_miles", "DOUBLE"),
        ("trip_time", "BIGINT"), ("base_passenger_fare", "DOUBLE"),
        ("tolls", "DOUBLE"), ("bcf", "DOUBLE"), ("sales_tax", "DOUBLE"),
        ("congestion_surcharge", "DOUBLE"), ("airport_fee", "DOUBLE"),
        ("tips", "DOUBLE"), ("driver_pay", "DOUBLE"),
        ("shared_request_flag", "VARCHAR"), ("shared_match_flag", "VARCHAR"),
        ("access_a_ride_flag", "VARCHAR"), ("wav_request_flag", "VARCHAR"),
        ("wav_match_flag", "VARCHAR"), ("cbd_congestion_fee", "DOUBLE"),
    ],
}

RAW_DRIFT = {
    "yellow": [
        ("passenger_count", "DOUBLE", "CAST((i % 6) + 1 AS DOUBLE)"),
        ("payment_type", "VARCHAR", "(ARRAY['CRD','CASH','Dispute'])[(i % 3) + 1]"),
        ("vendor_id", "VARCHAR", "(ARRAY['CMT','VTS'])[(i % 2) + 1]"),
        ("Tip_Amt", "DOUBLE", "CAST(i * 0.5 AS DOUBLE)"),
        ("pickup_longitude", "DOUBLE", "CAST(-73.9 - i * 0.001 AS DOUBLE)"),
    ],
    "green": [
        ("passenger_count", "DOUBLE", "CAST((i % 6) + 1 AS DOUBLE)"),
        ("trip_type", "DOUBLE", "CAST((i % 2) + 1 AS DOUBLE)"),
        ("RatecodeID", "DOUBLE", "CAST((i % 6) + 1 AS DOUBLE)"),
        ("payment_type", "DOUBLE", "CAST((i % 4) + 1 AS DOUBLE)"),
        ("trip_distance", "DOUBLE", "CAST(i * 1.1 AS DOUBLE)"),
    ],
    "fhv": [
        ("PUlocationID", "DOUBLE", "CAST((i % 200) + 1 AS DOUBLE)"),
        ("DOlocationID", "DOUBLE", "CAST((i % 200) + 1 AS DOUBLE)"),
        ("SR_Flag", "DOUBLE", "CAST(i % 2 AS DOUBLE)"),
        ("dispatching_base_num", "VARCHAR", "'B' || CAST((i % 99) + 1 AS VARCHAR)"),
    ],
    "fhvhv": [
        ("hvfhs_license_num", "VARCHAR", "'HV000' || CAST((i % 5) + 1 AS VARCHAR)"),
        ("PULocationID", "INTEGER", "CAST((i % 200) + 1 AS INTEGER)"),
        ("DOLocationID", "INTEGER", "CAST((i % 200) + 1 AS INTEGER)"),
        ("trip_miles", "DOUBLE", "CAST(i * 1.3 AS DOUBLE)"),
        ("trip_time", "BIGINT", "CAST(i * 60 AS BIGINT)"),
    ],
}


def _dummy_expr(duckdb_type: str) -> str:
    t = duckdb_type.upper()
    if t in ("BIGINT", "INTEGER", "SMALLINT", "TINYINT", "HUGEINT"):
        return f"CAST(i AS {t})"
    if t in ("DOUBLE", "FLOAT", "REAL"):
        return f"CAST(i * 1.0 AS {t})"
    if t.startswith("TIMESTAMP"):
        return "TIMESTAMP '2026-05-01 00:00:00' + to_hours(CAST(i AS BIGINT))"
    if t in ("VARCHAR", "TEXT", "STRING", "CHAR", "BPCHAR"):
        return "'v' || CAST(i AS VARCHAR)"
    raise ValueError(f"no dummy expression for DuckDB type {duckdb_type!r}")


def _write_parquet(con, path: Path, col_exprs, rows: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    selects = ", ".join(f"{expr} AS {name}" for name, expr in col_exprs)
    con.execute(
        f"COPY (SELECT {selects} FROM range({rows}) t(i)) "
        f"TO '{path}' (FORMAT PARQUET)"
    )


def generate(workroot: Path, data_type: str, rows_target: int = 2, rows_drift: int = 3) -> dict:
    if data_type not in DATA_TYPES:
        raise ValueError(f"unknown data_type {data_type!r}")
    raw_dir = Path(workroot) / "raw" / data_type
    target_name = TARGET_FILE[data_type]
    target_month = int(target_name.split("-")[-1].split(".")[0])
    target_path = raw_dir / str(TARGET_YEAR) / target_name
    drift_path = raw_dir / str(DRIFT_YEAR) / f"{data_type}_tripdata_{DRIFT_YEAR}-01.parquet"

    con = duckdb.connect()
    try:
        target_exprs = [(c, _dummy_expr(t)) for c, t in TARGET_COLUMNS[data_type]]
        _write_parquet(con, target_path, target_exprs, rows_target)
        drift_exprs = [(c, expr) for c, _t, expr in RAW_DRIFT[data_type]]
        _write_parquet(con, drift_path, drift_exprs, rows_drift)
    finally:
        con.close()

    return {
        "target_year": TARGET_YEAR, "target_month": target_month, "target_rows": rows_target,
        "drift_year": DRIFT_YEAR, "drift_month": 1, "drift_rows": rows_drift,
        "target_path": target_path, "drift_path": drift_path,
    }
