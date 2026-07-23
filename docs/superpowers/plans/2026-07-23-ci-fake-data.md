# CI / fake-data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every push validate the taxi pipeline (normalize → load) against a real SQL Server using download-free synthetic data that exercises the actual committed mappings.

**Architecture:** A code-driven generator synthesizes, per type, the pinned `target:` reference parquet (from hardcoded canonical schemas) plus a tiny "drift-era" raw parquet shaped to fire the mapping's renames / lossy_casts / value_maps / acknowledged_data_loss. Two DB-free tests validate generation and real-`normalize` compatibility; one SQL-Server-gated e2e test drives `taxi-run --skip-download --load` against an isolated workroot (with the committed mappings copied in — no production-code change) and asserts row counts. A new CI job runs both the existing loader integration tests and the e2e test against a `mssql/server:2022-latest` service container.

**Tech Stack:** Python ≥3.12, DuckDB (incl. the `mssql` community extension), pytest, `uv`, GitHub Actions.

## Global Constraints

_Every task's requirements implicitly include this section._

- **No new production dependencies.** Core deps stay `duckdb>=1.4.4`, `pyyaml>=6.0`; `test` extra stays `pytest>=8.0`. Generator code lives under `tests/` and may import `duckdb`, `taxi_loader.*`, `taxi_normalize.*`.
- **No new production CLI flags.** The e2e test reconciles the mappings by copying `normalize/mappings/` into the workroot and driving `taxi-run --data-dir <workroot>`; do not add `normalize --mappings-dir` or similar.
- **Generated parquet must use only DuckDB types in `shared/src/taxi_shared/type_mapping.py`:** `BIGINT`, `INTEGER`, `DOUBLE`, `VARCHAR`, `TIMESTAMP` (and `DECIMAL(p,s)` if ever needed). Any other type breaks the loader with `TypeMappingError`.
- **Test-file basenames must be unique across components** (pytest default import mode): the new files `test_fakedata_gen.py`, `test_normalize_fakedata.py`, `test_pipeline_e2e.py` are already unique.
- **The SQL-Server e2e test is gated** `pytest.mark.skipif(not os.environ.get("MSSQL_PASSWORD"))` so `pytest` stays green locally without a database. The generator and normalize tests are **not** gated (DB-free).
- **Loader facts to honor:** year-table name is `f"{data_type}_{year}"`; raw filenames must contain `YYYY-MM` (`(\d{4})-(\d{2})`); the loader hard-asserts the `mssql` extension resolves to `EXPECTED_MSSQL_EXT_VERSION = "7e57d24"`.
- **Do not commit any `.parquet` fixtures.** All parquet is generated at test time into a tmp workroot. `raw/` and `raw-normalized/` are gitignored.

---

## Reference data (used across tasks)

### Canonical target schemas (col → DuckDB type), from `DESCRIBE` of the real pinned targets

```
TARGET_COLUMNS = {
  "yellow": [
    ("VendorID","INTEGER"),("tpep_pickup_datetime","TIMESTAMP"),("tpep_dropoff_datetime","TIMESTAMP"),
    ("passenger_count","BIGINT"),("trip_distance","DOUBLE"),("RatecodeID","BIGINT"),
    ("store_and_fwd_flag","VARCHAR"),("PULocationID","INTEGER"),("DOLocationID","INTEGER"),
    ("payment_type","BIGINT"),("fare_amount","DOUBLE"),("extra","DOUBLE"),("mta_tax","DOUBLE"),
    ("tip_amount","DOUBLE"),("tolls_amount","DOUBLE"),("improvement_surcharge","DOUBLE"),
    ("total_amount","DOUBLE"),("congestion_surcharge","DOUBLE"),("Airport_fee","DOUBLE"),
    ("cbd_congestion_fee","DOUBLE"),
  ],
  "green": [
    ("VendorID","INTEGER"),("lpep_pickup_datetime","TIMESTAMP"),("lpep_dropoff_datetime","TIMESTAMP"),
    ("store_and_fwd_flag","VARCHAR"),("RatecodeID","BIGINT"),("PULocationID","INTEGER"),
    ("DOLocationID","INTEGER"),("passenger_count","BIGINT"),("trip_distance","DOUBLE"),
    ("fare_amount","DOUBLE"),("extra","DOUBLE"),("mta_tax","DOUBLE"),("tip_amount","DOUBLE"),
    ("tolls_amount","DOUBLE"),("ehail_fee","DOUBLE"),("improvement_surcharge","DOUBLE"),
    ("total_amount","DOUBLE"),("payment_type","BIGINT"),("trip_type","BIGINT"),
    ("congestion_surcharge","DOUBLE"),("cbd_congestion_fee","DOUBLE"),
  ],
  "fhv": [
    ("dispatching_base_num","VARCHAR"),("pickup_datetime","TIMESTAMP"),("dropOff_datetime","TIMESTAMP"),
    ("PUlocationID","BIGINT"),("DOlocationID","BIGINT"),("SR_Flag","BIGINT"),
    ("Affiliated_base_number","VARCHAR"),
  ],
  "fhvhv": [
    ("hvfhs_license_num","VARCHAR"),("dispatching_base_num","VARCHAR"),("originating_base_num","VARCHAR"),
    ("request_datetime","TIMESTAMP"),("on_scene_datetime","TIMESTAMP"),("pickup_datetime","TIMESTAMP"),
    ("dropoff_datetime","TIMESTAMP"),("PULocationID","INTEGER"),("DOLocationID","INTEGER"),
    ("trip_miles","DOUBLE"),("trip_time","BIGINT"),("base_passenger_fare","DOUBLE"),("tolls","DOUBLE"),
    ("bcf","DOUBLE"),("sales_tax","DOUBLE"),("congestion_surcharge","DOUBLE"),("airport_fee","DOUBLE"),
    ("tips","DOUBLE"),("driver_pay","DOUBLE"),("shared_request_flag","VARCHAR"),
    ("shared_match_flag","VARCHAR"),("access_a_ride_flag","VARCHAR"),("wav_request_flag","VARCHAR"),
    ("wav_match_flag","VARCHAR"),("cbd_congestion_fee","DOUBLE"),
  ],
}
TARGET_FILE = {
  "yellow": "yellow_tripdata_2026-05.parquet",
  "green":  "green_tripdata_2026-05.parquet",
  "fhv":    "fhv_tripdata_2026-04.parquet",
  "fhvhv":  "fhvhv_tripdata_2026-05.parquet",
}
```

### Drift raw files (col, DuckDB type, value SQL expr over `range(rows) t(i)`)

Each fires exactly the mechanisms its mapping supports (values chosen to be valid map keys):

```
RAW_DRIFT = {
  # yellow: rename + lossy_cast + value_map (Case A) + value_map (Case B via rename) + ack-drop
  "yellow": [
    ("passenger_count", "DOUBLE",  "CAST((i % 6) + 1 AS DOUBLE)"),                 # lossy_cast -> BIGINT
    ("payment_type",    "VARCHAR", "(ARRAY['CRD','CASH','Dispute'])[(i % 3) + 1]"),# value_map A -> BIGINT (1,2,4)
    ("vendor_id",       "VARCHAR", "(ARRAY['CMT','VTS'])[(i % 2) + 1]"),           # rename->VendorID + value_map B -> INTEGER (1,2)
    ("Tip_Amt",         "DOUBLE",  "CAST(i * 0.5 AS DOUBLE)"),                     # plain rename -> tip_amount
    ("pickup_longitude","DOUBLE",  "CAST(-73.9 - i * 0.001 AS DOUBLE)"),          # non-null -> ack drop
  ],
  # green: lossy_casts only (+ one passthrough)
  "green": [
    ("passenger_count","DOUBLE","CAST((i % 6) + 1 AS DOUBLE)"),  # cast -> BIGINT
    ("trip_type",      "DOUBLE","CAST((i % 2) + 1 AS DOUBLE)"),  # cast -> BIGINT
    ("RatecodeID",     "DOUBLE","CAST((i % 6) + 1 AS DOUBLE)"),  # cast -> BIGINT
    ("payment_type",   "DOUBLE","CAST((i % 4) + 1 AS DOUBLE)"),  # cast -> BIGINT (green maps payment_type via lossy_cast, NOT value_map)
    ("trip_distance",  "DOUBLE","CAST(i * 1.1 AS DOUBLE)"),      # passthrough
  ],
  # fhv: lossy_casts only (+ one passthrough). NB target casing PUlocationID/DOlocationID (lowercase L).
  "fhv": [
    ("PUlocationID","DOUBLE","CAST((i % 200) + 1 AS DOUBLE)"),   # cast -> BIGINT
    ("DOlocationID","DOUBLE","CAST((i % 200) + 1 AS DOUBLE)"),   # cast -> BIGINT
    ("SR_Flag",     "DOUBLE","CAST(i % 2 AS DOUBLE)"),          # cast -> BIGINT
    ("dispatching_base_num","VARCHAR","'B' || CAST((i % 99) + 1 AS VARCHAR)"),  # passthrough
  ],
  # fhvhv: no mechanisms — passthrough columns (matching target types) + null-fill for the rest
  "fhvhv": [
    ("hvfhs_license_num","VARCHAR","'HV000' || CAST((i % 5) + 1 AS VARCHAR)"),  # passthrough
    ("PULocationID","INTEGER","CAST((i % 200) + 1 AS INTEGER)"),                # passthrough
    ("DOLocationID","INTEGER","CAST((i % 200) + 1 AS INTEGER)"),                # passthrough
    ("trip_miles","DOUBLE","CAST(i * 1.3 AS DOUBLE)"),                          # passthrough
    ("trip_time","BIGINT","CAST(i * 60 AS BIGINT)"),                            # passthrough
  ],
}
TARGET_YEAR = 2026   # the pinned target file's year (month per TARGET_FILE)
DRIFT_YEAR  = 2015   # the drift raw file's year -> table <type>_2015
```

**Layout produced per type** under `<workroot>/raw/<type>/`:
- `2026/<TARGET_FILE[type]>` — canonical reference (all passthrough; loads to `<type>_2026`).
- `2015/<type>_tripdata_2015-01.parquet` — drift file (loads to `<type>_2015`).

---

## Task 1: Fake-data generator module

Builds the generator and validates the produced parquet **without** running normalize or SQL Server.

**Files:**
- Create: `tests/e2e/fakedata.py`
- Test: `tests/e2e/test_fakedata_gen.py`

**Interfaces:**
- Produces:
  - `TARGET_COLUMNS: dict[str, list[tuple[str,str]]]`, `TARGET_FILE: dict[str,str]`, `RAW_DRIFT: dict[str, list[tuple[str,str,str]]]`, `TARGET_YEAR: int`, `DRIFT_YEAR: int`, `DATA_TYPES: tuple[str,...]` (all four).
  - `generate(workroot: pathlib.Path, data_type: str, rows_target: int = 2, rows_drift: int = 3) -> dict` — writes the two parquet files and returns
    `{"target_year": 2026, "target_month": <mm>, "target_rows": rows_target, "drift_year": 2015, "drift_month": 1, "drift_rows": rows_drift, "target_path": Path, "drift_path": Path}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/e2e/test_fakedata_gen.py
import duckdb
import pytest
from fakedata import DATA_TYPES, TARGET_COLUMNS, TARGET_FILE, generate


def _describe(path):
    con = duckdb.connect()
    rows = con.execute(
        "SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM read_parquet(?))",
        [str(path)],
    ).fetchall()
    con.close()
    return rows


@pytest.mark.parametrize("data_type", DATA_TYPES)
def test_target_file_matches_canonical_schema(tmp_path, data_type):
    info = generate(tmp_path, data_type, rows_target=2, rows_drift=3)
    assert info["target_path"].name == TARGET_FILE[data_type]
    assert info["target_path"].exists()
    described = _describe(info["target_path"])
    assert described == TARGET_COLUMNS[data_type]  # names, types, and order


@pytest.mark.parametrize("data_type", DATA_TYPES)
def test_drift_file_written_with_expected_row_count(tmp_path, data_type):
    info = generate(tmp_path, data_type, rows_target=2, rows_drift=3)
    assert info["drift_path"].exists()
    assert "2015-01" in info["drift_path"].name
    con = duckdb.connect()
    n = con.execute(
        "SELECT count(*) FROM read_parquet(?)", [str(info["drift_path"])]
    ).fetchone()[0]
    con.close()
    assert n == 3


def test_yellow_payment_type_values_are_valid_map_keys(tmp_path):
    info = generate(tmp_path, "yellow", rows_drift=6)
    con = duckdb.connect()
    vals = {r[0] for r in con.execute(
        "SELECT DISTINCT payment_type FROM read_parquet(?)", [str(info["drift_path"])]
    ).fetchall()}
    con.close()
    assert vals <= {"CRD", "CASH", "Dispute"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/e2e/test_fakedata_gen.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'fakedata'`.

- [ ] **Step 3: Write the generator**

```python
# tests/e2e/fakedata.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/e2e/test_fakedata_gen.py -q`
Expected: PASS (all parametrized cases). If `DESCRIBE` order/type mismatches, fix `TARGET_COLUMNS` to match the assertion exactly.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/fakedata.py tests/e2e/test_fakedata_gen.py
git commit -m "test(e2e): code-driven synthetic taxi raw/target parquet generator"
```

---

## Task 2: Normalize-compatibility test (DB-free)

Proves the generated data drives the **real committed mappings** to a clean `exit 0` with canonical output — the highest-value check, and it needs no SQL Server, so it runs in the ordinary `test` job too.

**Files:**
- Create: `tests/e2e/test_normalize_fakedata.py`

**Interfaces:**
- Consumes: `fakedata.generate`, `fakedata.TARGET_COLUMNS`, `fakedata.DATA_TYPES`; `taxi_normalize.cli.main`.
- Produces: nothing (leaf test).

**Key facts this test encodes:**
- `normalize` resolves paths relative to CWD: reads `raw/<type>/`, mapping `normalize/mappings/<type>.yaml`, writes `raw-normalized/<type>/`. So the test copies the repo mappings into the workroot and `chdir`s there.
- `taxi_normalize.cli.main([data_type])` returns an int (0 done, 1 needs-review, 2 error, 3 first-run scaffold); it does not raise on exit codes.

- [ ] **Step 1: Write the failing test**

```python
# tests/e2e/test_normalize_fakedata.py
import shutil
from pathlib import Path

import duckdb
import pytest
from fakedata import DATA_TYPES, TARGET_COLUMNS, generate
from taxi_normalize.cli import main as normalize_main

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_MAPPINGS = REPO_ROOT / "normalize" / "mappings"


def _describe(path):
    con = duckdb.connect()
    rows = con.execute(
        "SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM read_parquet(?))",
        [str(path)],
    ).fetchall()
    con.close()
    return rows


@pytest.mark.parametrize("data_type", DATA_TYPES)
def test_generated_data_normalizes_cleanly(tmp_path, monkeypatch, data_type):
    shutil.copytree(REPO_MAPPINGS, tmp_path / "normalize" / "mappings")
    generate(tmp_path, data_type, rows_target=2, rows_drift=3)

    monkeypatch.chdir(tmp_path)
    rc = normalize_main([data_type])
    assert rc == 0, f"normalize exited {rc} for {data_type} (expected 0 clean)"

    out_dir = tmp_path / "raw-normalized" / data_type
    produced = sorted(out_dir.rglob("*.parquet"))
    assert len(produced) == 2, f"expected 2 normalized files, got {produced}"

    # Every normalized file conforms to the canonical target schema.
    for f in produced:
        assert _describe(f) == TARGET_COLUMNS[data_type]


def test_yellow_value_map_and_cast_applied(tmp_path, monkeypatch):
    """payment_type CRD/CASH/Dispute -> 1/2/4 (BIGINT); passenger_count DOUBLE -> BIGINT."""
    shutil.copytree(REPO_MAPPINGS, tmp_path / "normalize" / "mappings")
    info = generate(tmp_path, "yellow", rows_target=2, rows_drift=6)
    monkeypatch.chdir(tmp_path)
    assert normalize_main(["yellow"]) == 0

    normalized_drift = (
        tmp_path / "raw-normalized" / "yellow" / "2015" / info["drift_path"].name
    )
    con = duckdb.connect()
    pay = {r[0] for r in con.execute(
        "SELECT DISTINCT payment_type FROM read_parquet(?)", [str(normalized_drift)]
    ).fetchall()}
    coltypes = dict(con.execute(
        "SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM read_parquet(?))",
        [str(normalized_drift)],
    ).fetchall())
    con.close()
    assert pay <= {1, 2, 4}
    assert coltypes["passenger_count"] == "BIGINT"
    assert coltypes["payment_type"] == "BIGINT"
    assert coltypes["VendorID"] == "INTEGER"  # renamed from vendor_id + value-mapped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/e2e/test_normalize_fakedata.py -q`
Expected: FAIL — the test exists but has never run against real mappings; if the generator is correct it should actually PASS here. If any case exits non-zero, read the printed normalize report and fix the corresponding `RAW_DRIFT` entry (e.g. a value outside a value_map's keys, or a column that isn't covered by the mapping). Treat a non-zero exit as a generator bug, not a test bug.

- [ ] **Step 3: (Fix generator if needed)**

No new code if Step 2 passes. If a type exits 1 "unresolved", adjust that type's `RAW_DRIFT` in `tests/e2e/fakedata.py` so every drift column is a target passthrough, a rename source, an acked-drop (non-null), or a covered cast/value_map — per the mapping. Re-run Step 2.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/e2e/test_normalize_fakedata.py -q`
Expected: PASS for all four types + the yellow value-map assertion.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_normalize_fakedata.py tests/e2e/fakedata.py
git commit -m "test(e2e): generated data normalizes cleanly against committed mappings"
```

---

## Task 3: End-to-end pipeline test against SQL Server

Drives the whole pipeline (normalize → load) via `taxi-run` into a real SQL Server and asserts row counts. Gated on `MSSQL_PASSWORD`; validated locally against Docker SQL Server.

**Files:**
- Create: `tests/e2e/conftest.py`
- Create: `tests/e2e/test_pipeline_e2e.py`
- Create: `scripts/wait_for_mssql.py`

**Interfaces:**
- Consumes: `fakedata.generate`, `fakedata.DATA_TYPES`; `taxi_loader.connection` (`ConnConfig`, `connect_duckdb`, `attach_target`, `ATTACH_NAME`); `taxi_loader.load.count_year_table`; `taxi_loader.manifest.read_manifest`; the `taxi-run` console script.
- Produces: nothing (leaf test) + `wait_for_mssql.py` consumed by Tasks 4 & 5.

**Why subprocess `taxi-run`:** the DuckDB `mssql` ATTACH is process-global; running the load in a child process means its attach is released before the test opens its own short-lived attach to assert. Do **not** run the load in-process.

- [ ] **Step 1: Write the SQL-Server wait helper**

```python
# scripts/wait_for_mssql.py
"""Block until the SQL Server used by the integration/e2e tests accepts a
connection, then ensure the target database exists. Reuses the loader's real
connection path (so it also validates the pinned mssql extension version).

Usage: MSSQL_PASSWORD=... uv run python scripts/wait_for_mssql.py
Env: MSSQL_HOST (default 127.0.0.1), MSSQL_PORT (1433), MSSQL_USER (sa).
"""
import os
import sys
import time

from taxi_loader.connection import ConnConfig, connect_duckdb, ensure_database

ATTEMPTS = 40
DELAY_S = 3


def main() -> int:
    password = os.environ.get("MSSQL_PASSWORD")
    if not password:
        print("MSSQL_PASSWORD not set", file=sys.stderr)
        return 2
    cfg = ConnConfig(
        host=os.environ.get("MSSQL_HOST", "127.0.0.1"),
        port=int(os.environ.get("MSSQL_PORT", "1433")),
        database="taxi",
        schema="dbo",
        user=os.environ.get("MSSQL_USER", "sa"),
        password=password,
    )
    for attempt in range(1, ATTEMPTS + 1):
        try:
            conn = connect_duckdb()
            ensure_database(conn, cfg)
            conn.close()
            print(f"SQL Server ready (attempt {attempt}); database 'taxi' ensured")
            return 0
        except Exception as exc:  # noqa: BLE001 - report and retry
            print(f"waiting for SQL Server ({attempt}/{ATTEMPTS}): {exc}")
            time.sleep(DELAY_S)
    print("SQL Server did not become ready", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

Verify the `ConnConfig(...)` keyword arguments match `loader/src/taxi_loader/connection.py` (fields: host, port, database, schema, user, password). Adjust if the dataclass differs.

- [ ] **Step 2: Write the e2e conftest (shared SQL helpers) + the failing test**

```python
# tests/e2e/conftest.py
import os
from contextlib import contextmanager
from uuid import uuid4

import pytest
from taxi_loader.connection import ATTACH_NAME, ConnConfig, attach_target, connect_duckdb


@pytest.fixture
def cfg():
    return ConnConfig(
        host=os.environ.get("MSSQL_HOST", "localhost"),
        port=int(os.environ.get("MSSQL_PORT", "1433")),
        database="taxi",
        schema="t" + uuid4().hex[:8],
        user=os.environ.get("MSSQL_USER", "sa"),
        password=os.environ["MSSQL_PASSWORD"],
    )


@contextmanager
def attached(conn_cfg):
    """Short-lived read connection; respects the process-global mssql attach."""
    conn = connect_duckdb()
    try:
        attach_target(conn, conn_cfg, create_schema=False)
        yield conn
    finally:
        try:
            conn.execute(f"DETACH {ATTACH_NAME}")
        except Exception:
            pass
        conn.close()
```

```python
# tests/e2e/test_pipeline_e2e.py
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import attached
from fakedata import DATA_TYPES, generate
from taxi_loader import load, manifest

pytestmark = pytest.mark.skipif(
    not os.environ.get("MSSQL_PASSWORD"),
    reason="requires SQL Server (set MSSQL_PASSWORD)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_MAPPINGS = REPO_ROOT / "normalize" / "mappings"


def _run_pipeline(workroot: Path, data_type: str, cfg) -> None:
    argv = [
        "taxi-run", data_type, "--skip-download", "--load",
        "--data-dir", str(workroot),
        "--host", cfg.host, "--port", str(cfg.port),
        "--database", cfg.database, "--schema", cfg.schema, "--user", cfg.user,
    ]
    env = {**os.environ, "MSSQL_PASSWORD": cfg.password}
    result = subprocess.run(argv, env=env, capture_output=True, text=True)
    assert result.returncode == 0, (
        f"taxi-run exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def _count(cfg, table: str) -> int:
    with attached(cfg) as conn:
        return load.count_year_table(conn, cfg, table)


@pytest.mark.parametrize("data_type", DATA_TYPES)
def test_pipeline_loads_expected_row_counts(tmp_path, cfg, data_type):
    shutil.copytree(REPO_MAPPINGS, tmp_path / "normalize" / "mappings")
    info = generate(tmp_path, data_type, rows_target=2, rows_drift=3)

    _run_pipeline(tmp_path, data_type, cfg)

    assert _count(cfg, f"{data_type}_{info['target_year']}") == info["target_rows"]
    assert _count(cfg, f"{data_type}_{info['drift_year']}") == info["drift_rows"]

    with attached(cfg) as conn:
        rows = manifest.read_manifest(conn, cfg, data_type)
    triples = {(y, m, n) for (y, m, n) in rows}
    assert (info["target_year"], info["target_month"], info["target_rows"]) in triples
    assert (info["drift_year"], info["drift_month"], info["drift_rows"]) in triples
```

- [ ] **Step 3: Bring up SQL Server locally and run the test**

```bash
docker run -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=Str0ng_Passw0rd!" \
  -p 1433:1433 -d --name taxi-mssql mcr.microsoft.com/mssql/server:2022-latest
MSSQL_PASSWORD=Str0ng_Passw0rd! uv run python scripts/wait_for_mssql.py
```

Run: `MSSQL_PASSWORD=Str0ng_Passw0rd! uv run --extra test pytest tests/e2e/test_pipeline_e2e.py -v`
Expected: PASS for all four types. If `manifest.read_manifest` returns a different tuple shape than `(year, month, rows)`, adjust the manifest assertion to match its real return type (keep the row-count asserts, which are the core check). If `taxi-run` exits non-zero, the captured STDOUT/STDERR names the failing stage.

- [ ] **Step 4: Confirm the gate keeps local `pytest` green**

Run (no password): `uv run --extra test pytest tests/e2e -q`
Expected: `test_fakedata_gen.py` and `test_normalize_fakedata.py` PASS; `test_pipeline_e2e.py` SKIPPED.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/conftest.py tests/e2e/test_pipeline_e2e.py scripts/wait_for_mssql.py
git commit -m "test(e2e): whole-pipeline smoke into SQL Server via taxi-run"
```

---

## Task 4: CI integration job

Adds a job that starts SQL Server and runs the existing loader integration tests **and** the new e2e test on every push/PR.

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `scripts/wait_for_mssql.py`; `tests/taxi_loader/test_load_integration.py`; `tests/e2e/test_pipeline_e2e.py`.

- [ ] **Step 1: Add the `integration` job**

Append this job to `.github/workflows/ci.yml` (sibling of `test` and `docs`; leave those unchanged):

```yaml
  integration:
    runs-on: ubuntu-latest
    services:
      mssql:
        image: mcr.microsoft.com/mssql/server:2022-latest
        env:
          ACCEPT_EULA: "Y"
          MSSQL_SA_PASSWORD: "Str0ng_Passw0rd!"
        ports:
          - 1433:1433
    env:
      MSSQL_PASSWORD: "Str0ng_Passw0rd!"
      MSSQL_HOST: "127.0.0.1"
      MSSQL_PORT: "1433"
      MSSQL_USER: "sa"
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - run: uv python install 3.13
      - run: uv sync --extra test
      - name: Wait for SQL Server
        run: uv run python scripts/wait_for_mssql.py
      - name: Run loader integration + pipeline e2e tests
        run: |
          uv run --extra test pytest \
            tests/taxi_loader/test_load_integration.py \
            tests/e2e/test_pipeline_e2e.py -v
```

- [ ] **Step 2: Validate the workflow YAML locally**

Run: `uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml ok')"`
Expected: `yaml ok` (no parse error; single top-level `jobs` with `test`, `docs`, `integration`).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: run loader integration + pipeline e2e against SQL Server service container"
```

- [ ] **Step 4: Verify on a pushed branch**

After the branch is pushed (per the repo's merge/push habit, this happens at integration time), confirm the `integration` job goes green in GitHub Actions — this is the only place the service container actually runs. Expected: the previously-skipped 7 loader integration tests plus the 4 e2e cases all execute and pass.

---

## Task 5: Local parity script

A single command that mirrors CI locally, documented next to the existing manual bring-up.

**Files:**
- Create: `scripts/e2e-smoke.sh`
- Modify: `tests/taxi_loader/test_load_integration.py` (docstring — add a pointer to the script)

**Interfaces:**
- Consumes: `scripts/wait_for_mssql.py`; the same tests as CI.

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# Run the SQL Server integration + pipeline e2e tests locally, mirroring CI.
# Brings up a disposable SQL Server container, waits for readiness, runs the
# tests, and tears the container down.
set -euo pipefail

PASSWORD="${MSSQL_PASSWORD:-Str0ng_Passw0rd!}"
CONTAINER="${MSSQL_CONTAINER:-taxi-mssql-e2e}"

cleanup() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=${PASSWORD}" \
  -p 1433:1433 -d --name "$CONTAINER" \
  mcr.microsoft.com/mssql/server:2022-latest >/dev/null

MSSQL_PASSWORD="$PASSWORD" MSSQL_HOST=127.0.0.1 MSSQL_PORT=1433 MSSQL_USER=sa \
  uv run python scripts/wait_for_mssql.py

MSSQL_PASSWORD="$PASSWORD" MSSQL_HOST=127.0.0.1 MSSQL_PORT=1433 MSSQL_USER=sa \
  uv run --extra test pytest \
    tests/taxi_loader/test_load_integration.py \
    tests/e2e/test_pipeline_e2e.py -v
```

- [ ] **Step 2: Make it executable and run it**

```bash
chmod +x scripts/e2e-smoke.sh
./scripts/e2e-smoke.sh
```

Expected: container starts, readiness succeeds, all loader integration + e2e tests PASS, container removed on exit.

- [ ] **Step 3: Add a pointer in the integration test docstring**

In `tests/taxi_loader/test_load_integration.py`, add one line to the module docstring's bring-up notes:

```
    Or run everything (container bring-up + these tests + the pipeline e2e) with:
        ./scripts/e2e-smoke.sh
```

- [ ] **Step 4: Commit**

```bash
git add scripts/e2e-smoke.sh tests/taxi_loader/test_load_integration.py
git commit -m "chore(e2e): local parity script mirroring the CI integration job"
```

---

## Self-Review

**1. Spec coverage:**
- Spec A (CI SQL Server job, single Python, service container, explicit readiness wait, runs existing integration tests) → **Task 4** (+ `wait_for_mssql.py` from Task 3).
- Spec B.1 (code-driven schema constants, no committed parquet) → **Task 1** (`TARGET_COLUMNS`/`RAW_DRIFT` in `fakedata.py`).
- Spec B.2 (generator producing raw + pinned target) → **Task 1** (`generate`).
- Spec B.3 (e2e test, all four types, `MSSQL_PASSWORD` gate, mapping-copy reconciliation, count assertions via loader helpers) → **Task 3**; the DB-free mapping validation is **Task 2**.
- Spec B.4 (local parity) → **Task 5**.
- Spec "each mapping mechanism fires" → encoded per type in `RAW_DRIFT` and asserted in **Task 2**.
- Spec "local pytest stays green" → **Task 3 Step 4**.
No gaps.

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code. The only "adjust if…" notes (ConnConfig fields, manifest tuple shape) point at named real symbols to verify, not unwritten logic.

**3. Type consistency:** `generate()` return keys (`target_year/target_month/target_rows/drift_year/drift_month/drift_rows/target_path/drift_path`) are produced in Task 1 and consumed identically in Tasks 1–3. `TARGET_COLUMNS`/`TARGET_FILE`/`DATA_TYPES`/`RAW_DRIFT` names are consistent across tasks. Helper names (`attached`, `_count`, `_run_pipeline`, `_describe`) are defined before use in each file. Table names use `f"{data_type}_{year}"` throughout, matching `load.year_table`.

**Known verification points for the implementer** (flagged inline, not blocking): exact `ConnConfig` field names; `manifest.read_manifest` return-tuple shape; that `DESCRIBE` reports types verbatim as in `TARGET_COLUMNS` (fix the constant to match if DuckDB renders e.g. `TIMESTAMP` differently).
