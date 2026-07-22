# Normalizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `taxi_normalize` Python package that reads raw TLC parquet + a per-type curated YAML mapping and writes normalized parquet where every file conforms to the latest schema, halting with a consolidated error when human acknowledgment is missing for data loss.

**Architecture:** New `normalize/` component in the monorepo, mirroring the existing k6-loadtest / schema-drift / taxi-shared layout. `src/` package layout, single pyproject entry `normalize`. Bootstrap subcommand calls `schema_drift`'s Python API in-process (no subprocess) to seed the mapping YAML with rename suggestions. All schema checks run from parquet footer metadata; value scans only for precision-loss detection.

**Tech Stack:** Python 3.12+, uv, hatchling, DuckDB 1.4+, PyYAML, pytest. New internal dependency: `schema_drift` package (already in this repo, so a direct import — no new external deps).

**Reference spec:** `docs/superpowers/specs/2026-07-21-normalizer-design.md`

---

## Preconditions

- [ ] **Working tree is clean and on a branch dedicated to this feature.** If you're the subagent-driven-development orchestrator, invoke `superpowers:using-git-worktrees` before starting Task 1. Do not begin implementation on `main`.

- [ ] **Baseline pytest green.** Confirms nothing was already broken.
  ```bash
  uv sync --extra test
  uv run --extra test pytest -q
  ```
  Expected: 35 passed (all pre-existing tests).

- [ ] **Have a small raw dataset available for smoke tests.** At least one type in `raw/<type>/` with 2+ files spanning different eras (drift present) is ideal but not required — Task 8's end-to-end smoke test uses a synthesized fixture, so real raw data is a nice-to-have, not a blocker.

---

## Task 1: Package scaffold + CLI stub

**Motivation:** Get the directory structure, empty modules, pyproject registration, and a working `normalize --help` all landing in one atomic commit. Every subsequent task fills in one module.

**Files:**
- Create: `normalize/src/taxi_normalize/__init__.py` (empty)
- Create: `normalize/src/taxi_normalize/cli.py` (argparse stub with subcommands)
- Create: `normalize/src/taxi_normalize/mapping.py` (empty stub)
- Create: `normalize/src/taxi_normalize/data_check.py` (empty stub)
- Create: `normalize/src/taxi_normalize/planner.py` (empty stub)
- Create: `normalize/src/taxi_normalize/executor.py` (empty stub)
- Create: `normalize/src/taxi_normalize/bootstrap.py` (empty stub)
- Create: `normalize/mappings/.gitkeep`
- Modify: `pyproject.toml` (add package + script entry)

### Steps

- [ ] **1.1 Create the directory layout**

  ```bash
  mkdir -p normalize/src/taxi_normalize normalize/mappings
  touch normalize/src/taxi_normalize/__init__.py
  touch normalize/mappings/.gitkeep
  ```

- [ ] **1.2 Create the CLI stub**

  Create `normalize/src/taxi_normalize/cli.py`:

  ```python
  """Entry point for the `normalize` command."""
  import argparse
  import sys


  def main() -> int:
      parser = argparse.ArgumentParser(
          prog="normalize",
          description="Rewrite historical TLC parquet files to conform to the latest schema.",
      )
      subparsers = parser.add_subparsers(dest="command")

      bootstrap = subparsers.add_parser(
          "bootstrap",
          help="Generate normalize/mappings/<type>.yaml from raw/<type>/ + schema-drift analysis.",
      )
      bootstrap.add_argument("data_type", help="One of: yellow, green, fhv, fhvhv")
      bootstrap.add_argument(
          "--sample",
          default="100%",
          help="Rows to sample for rename verification: N (absolute) or N%% (percent). Default: 100%% (full scan).",
      )

      normalize_cmd = subparsers.add_parser(
          "normalize",
          help="Normalize one data type (or all if no argument).",
      )
      normalize_cmd.add_argument("data_type", nargs="?", help="Optional type; runs all if omitted.")

      # Bare `normalize` with no subcommand and no positional arg is also valid — runs all types.
      args = parser.parse_args()

      if args.command is None:
          # `normalize` with nothing → run all four types
          args.command = "normalize"
          args.data_type = None

      if args.command == "bootstrap":
          print(f"bootstrap {args.data_type} --sample {args.sample}: not implemented yet", file=sys.stderr)
          return 2
      elif args.command == "normalize":
          if args.data_type:
              print(f"normalize {args.data_type}: not implemented yet", file=sys.stderr)
          else:
              print("normalize (all types): not implemented yet", file=sys.stderr)
          return 2

      return 0


  if __name__ == "__main__":
      sys.exit(main())
  ```

- [ ] **1.3 Create empty module stubs**

  Each of the following files gets a single-line docstring so they show up cleanly in imports:

  `normalize/src/taxi_normalize/mapping.py`:
  ```python
  """YAML mapping loading and validation."""
  ```

  `normalize/src/taxi_normalize/data_check.py`:
  ```python
  """Parquet metadata queries and value-scan precision checks."""
  ```

  `normalize/src/taxi_normalize/planner.py`:
  ```python
  """Per-column action planning: compare raw schema vs target using the mapping."""
  ```

  `normalize/src/taxi_normalize/executor.py`:
  ```python
  """DuckDB SQL builder and atomic parquet writer."""
  ```

  `normalize/src/taxi_normalize/bootstrap.py`:
  ```python
  """Mapping YAML scaffold generator, backed by schema_drift's Python API."""
  ```

- [ ] **1.4 Update `pyproject.toml`**

  Two changes:

  Add to `[tool.hatch.build.targets.wheel]` packages list — final state:
  ```toml
  [tool.hatch.build.targets.wheel]
  packages = [
    "k6-loadtest/src/k6_loadtest",
    "normalize/src/taxi_normalize",
    "schema-drift/src/schema_drift",
    "shared/src/taxi_shared",
  ]
  ```

  Add to `[project.scripts]` — final state:
  ```toml
  [project.scripts]
  k6-preprocess = "k6_loadtest.preprocess:main"
  normalize = "taxi_normalize.cli:main"
  schema-drift = "schema_drift.cli:main"
  ```

- [ ] **1.5 Re-sync and smoke the CLI**

  ```bash
  uv sync --extra test
  uv run --extra test normalize --help
  uv run --extra test normalize bootstrap yellow --help
  uv run --extra test normalize normalize --help
  ```

  Expected: each `--help` prints usage text and exits 0. The commands themselves currently print "not implemented yet" and exit 2 — that's intentional at this scaffold stage.

- [ ] **1.6 Run the existing test suite to confirm nothing broke**

  ```bash
  uv run --extra test pytest -q
  ```

  Expected: 35 passed (same as baseline).

- [ ] **1.7 Commit**

  ```bash
  git add -A
  git commit -m "feat(normalize): scaffold taxi_normalize package + CLI stub"
  ```

---

## Task 2: Test fixtures — synthetic parquet families

**Motivation:** Every subsequent task tests against synthesized parquet files representing schema drift over three eras. Building the fixture infrastructure once, before any real logic, lets each downstream module have deterministic test data.

**Files:**
- Create: `tests/taxi_normalize/conftest.py` (fixture builders)
- Create: `tests/taxi_normalize/test_fixtures_smoke.py` (verifies the fixtures themselves work)

**Important:** Do NOT create `tests/taxi_normalize/__init__.py`. Matching the pattern of `tests/k6_loadtest/`, `tests/schema_drift/`, and `tests/taxi_shared/` (none of which have `__init__.py`) — pytest discovers tests via rootdir, and adding `__init__.py` here would create a Python package named `taxi_normalize` that shadows the real one under `normalize/src/`.

### Steps

- [ ] **2.1 Create the fixture module**

  Create `tests/taxi_normalize/conftest.py`:

  ```python
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
  ```

- [ ] **2.2 Create the fixture smoke test**

  Create `tests/taxi_normalize/test_fixtures_smoke.py`:

  ```python
  """Verify the fixture infrastructure produces valid parquet files with the expected schemas."""
  import duckdb


  def test_yellow_family_creates_three_files(yellow_family):
      files = sorted(yellow_family.rglob("*.parquet"))
      assert len(files) == 3
      assert files[0].name == "yellow_tripdata_2009-01.parquet"
      assert files[1].name == "yellow_tripdata_2015-06.parquet"
      assert files[2].name == "yellow_tripdata_2024-01.parquet"


  def test_yellow_family_eras_have_expected_columns(yellow_family, target_file):
      conn = duckdb.connect(":memory:")
      era1 = yellow_family / "2009" / "yellow_tripdata_2009-01.parquet"
      era1_cols = {r[0] for r in conn.execute(f"DESCRIBE SELECT * FROM '{era1}'").fetchall()}
      assert "pu_datetime" in era1_cols
      assert "pickup_latitude" in era1_cols
      assert "airport_fee" not in era1_cols

      target_cols = {r[0] for r in conn.execute(f"DESCRIBE SELECT * FROM '{target_file}'").fetchall()}
      assert "tpep_pickup_datetime" in target_cols
      assert "airport_fee" in target_cols
      assert "pu_datetime" not in target_cols


  def test_no_drift_family_has_uniform_schema(no_drift_family):
      files = sorted(no_drift_family.rglob("*.parquet"))
      assert len(files) == 2
      conn = duckdb.connect(":memory:")
      s0 = conn.execute(f"DESCRIBE SELECT * FROM '{files[0]}'").fetchall()
      s1 = conn.execute(f"DESCRIBE SELECT * FROM '{files[1]}'").fetchall()
      assert s0 == s1
  ```

- [ ] **2.3 Run the fixture smoke test**

  ```bash
  uv run --extra test pytest tests/taxi_normalize/test_fixtures_smoke.py -v
  ```

  Expected: 3 passed.

- [ ] **2.4 Run the full suite to confirm no regressions**

  ```bash
  uv run --extra test pytest -q
  ```

  Expected: 38 passed (35 previous + 3 fixture smoke).

- [ ] **2.5 Commit**

  ```bash
  git add -A
  git commit -m "test(normalize): add synthetic parquet family fixtures + smoke test"
  ```

---

## Task 3: `data_check.py` — metadata queries and precision scans

**Motivation:** Metadata-driven checks are the foundation of everything else — planner uses them to decide auto-drop / auto-cast safety, bootstrap uses them to build the scaffold. Implement + test in isolation.

**Files:**
- Modify: `normalize/src/taxi_normalize/data_check.py` (fill in the implementation)
- Create: `tests/taxi_normalize/test_data_check.py`

### Steps

- [ ] **3.1 Write the failing tests**

  Create `tests/taxi_normalize/test_data_check.py`:

  ```python
  """Tests for metadata queries and precision scans."""
  import duckdb
  import pytest

  from taxi_normalize.data_check import (
      get_file_metadata,
      aggregate_across_files,
      fits_in_target_type,
      has_precision_loss,
  )


  def test_get_file_metadata_returns_per_column_stats(target_file):
      conn = duckdb.connect(":memory:")
      md = get_file_metadata(conn, target_file)
      # Every column present, with type + null_count + row_count
      assert "vendorid" in md
      assert md["vendorid"]["type"] == "INTEGER"
      assert md["vendorid"]["null_count"] == 0
      assert md["vendorid"]["num_rows"] == 2
      assert "airport_fee" in md
      # min/max present for numeric columns
      assert md["vendorid"]["min"] == 1
      assert md["vendorid"]["max"] == 2


  def test_aggregate_across_files_unions_columns(yellow_family):
      conn = duckdb.connect(":memory:")
      files = sorted(yellow_family.rglob("*.parquet"))
      mds = [get_file_metadata(conn, f) for f in files]
      agg = aggregate_across_files(mds)
      # Old and new column names both appear in the union
      assert "pu_datetime" in agg
      assert "tpep_pickup_datetime" in agg
      # airport_fee only in era 3, so files_present == 1
      assert agg["airport_fee"]["files_present"] == 1
      assert agg["airport_fee"]["files_with_data"] == 1
      # pu_datetime only in era 1
      assert agg["pu_datetime"]["files_present"] == 1
      assert agg["pu_datetime"]["files_with_data"] == 1
      # vendorid in all three eras
      assert agg["vendorid"]["files_present"] == 3


  def test_fits_in_target_type_widening_is_safe():
      # INTEGER data with max=100 fits easily in BIGINT
      stats = {"type": "INTEGER", "min": 1, "max": 100, "num_rows": 100, "null_count": 0}
      fits, reason = fits_in_target_type(stats, "BIGINT")
      assert fits is True
      assert reason == ""


  def test_fits_in_target_type_range_overflow_flagged():
      # INT max 999999 does not fit SMALLINT (max 32767)
      stats = {"type": "INTEGER", "min": 0, "max": 999999, "num_rows": 100, "null_count": 0}
      fits, reason = fits_in_target_type(stats, "SMALLINT")
      assert fits is False
      assert "999999" in reason or "SMALLINT" in reason


  def test_fits_in_target_type_varchar_length_flagged():
      # String max length 50 does not fit VARCHAR(10)
      stats = {"type": "VARCHAR", "max": "a" * 50, "min": "a", "num_rows": 10, "null_count": 0}
      fits, reason = fits_in_target_type(stats, "VARCHAR(10)")
      assert fits is False


  def test_has_precision_loss_double_to_bigint_with_fractional(yellow_family):
      conn = duckdb.connect(":memory:")
      era2_file = yellow_family / "2015" / "yellow_tripdata_2015-06.parquet"
      # passenger_count in era 2 has 1.5 — that would truncate to 1
      loss, count = has_precision_loss(conn, era2_file, "passenger_count", "BIGINT")
      assert loss is True
      assert count == 1  # one row with 1.5


  def test_has_precision_loss_no_fractional_is_safe(yellow_family):
      conn = duckdb.connect(":memory:")
      era1_file = yellow_family / "2009" / "yellow_tripdata_2009-01.parquet"
      # Era 1 passenger_count is 1.0 and 2.0 — integer-valued
      loss, count = has_precision_loss(conn, era1_file, "passenger_count", "BIGINT")
      assert loss is False
      assert count == 0
  ```

- [ ] **3.2 Run the failing test to confirm the interface doesn't exist yet**

  ```bash
  uv run --extra test pytest tests/taxi_normalize/test_data_check.py -v
  ```

  Expected: `ImportError: cannot import name 'get_file_metadata' from 'taxi_normalize.data_check'`. All 7 tests fail with collection error.

- [ ] **3.3 Implement `data_check.py`**

  Replace `normalize/src/taxi_normalize/data_check.py` with:

  ```python
  """Parquet metadata queries and value-scan precision checks.

  Metadata queries (get_file_metadata, aggregate_across_files, fits_in_target_type)
  are footer-only — no data scan. Value scans (has_precision_loss) always read
  the full column, since sampling would risk false-negative data-loss decisions.
  """
  from __future__ import annotations

  from pathlib import Path
  from typing import Any

  import duckdb


  def get_file_metadata(conn: duckdb.DuckDBPyConnection, file_path: Path) -> dict[str, dict[str, Any]]:
      """Return {column_name: {type, null_count, num_rows, min, max}} from parquet footer."""
      # Column types from DESCRIBE (friendlier form than parquet_schema and matches
      # what DuckDB uses for casts).
      col_types: dict[str, str] = {}
      desc_rows = conn.execute(f"DESCRIBE SELECT * FROM '{file_path}'").fetchall()
      for row in desc_rows:
          col_types[row[0]] = row[1]

      # Row group stats: null_count, min, max per column per row group.
      # Aggregate across row groups.
      md_rows = conn.execute(
          f"SELECT path_in_schema, stats_null_count, stats_min_value, stats_max_value, num_values "
          f"FROM parquet_metadata('{file_path}')"
      ).fetchall()

      per_col: dict[str, dict[str, Any]] = {}
      for path_in_schema, null_count, min_val, max_val, num_values in md_rows:
          col = path_in_schema
          if col not in per_col:
              per_col[col] = {
                  "null_count": 0,
                  "num_rows": 0,
                  "min": None,
                  "max": None,
              }
          entry = per_col[col]
          entry["null_count"] += int(null_count or 0)
          entry["num_rows"] += int(num_values or 0)
          if min_val is not None:
              entry["min"] = min_val if entry["min"] is None else min(entry["min"], min_val)
          if max_val is not None:
              entry["max"] = max_val if entry["max"] is None else max(entry["max"], max_val)

      # Merge type in
      result: dict[str, dict[str, Any]] = {}
      for col, type_ in col_types.items():
          entry = per_col.get(col, {"null_count": 0, "num_rows": 0, "min": None, "max": None})
          entry["type"] = type_
          result[col] = entry
      return result


  def aggregate_across_files(files_metadata: list[dict[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
      """Aggregate per-file metadata into per-column presence + null/range summary.

      Returns {col_name: {files_present, files_with_data, total_nulls, total_rows,
                          min_range, max_range, types_seen}}.
      """
      agg: dict[str, dict[str, Any]] = {}
      for md in files_metadata:
          for col, stats in md.items():
              if col not in agg:
                  agg[col] = {
                      "files_present": 0,
                      "files_with_data": 0,
                      "total_nulls": 0,
                      "total_rows": 0,
                      "min_range": None,
                      "max_range": None,
                      "types_seen": set(),
                  }
              a = agg[col]
              a["files_present"] += 1
              a["types_seen"].add(stats["type"])
              a["total_nulls"] += stats["null_count"]
              a["total_rows"] += stats["num_rows"]
              non_null_count = stats["num_rows"] - stats["null_count"]
              if non_null_count > 0:
                  a["files_with_data"] += 1
              if stats["min"] is not None:
                  a["min_range"] = stats["min"] if a["min_range"] is None else min(a["min_range"], stats["min"])
              if stats["max"] is not None:
                  a["max_range"] = stats["max"] if a["max_range"] is None else max(a["max_range"], stats["max"])
      # Convert sets to sorted lists so callers can rely on stable output.
      for a in agg.values():
          a["types_seen"] = sorted(a["types_seen"])
      return agg


  # Signed integer ranges — the only ones DuckDB uses for its numeric types.
  _INT_RANGES = {
      "TINYINT": (-128, 127),
      "SMALLINT": (-32768, 32767),
      "INTEGER": (-2_147_483_648, 2_147_483_647),
      "BIGINT": (-9_223_372_036_854_775_808, 9_223_372_036_854_775_807),
  }


  def fits_in_target_type(col_stats: dict[str, Any], target_type: str) -> tuple[bool, str]:
      """Metadata-only range check. Returns (fits, reason_if_not).

      Handles integer widths, DOUBLE→integer range, VARCHAR(N) length.
      Precision (fractional-value) checks are separate (see has_precision_loss).
      """
      min_v = col_stats.get("min")
      max_v = col_stats.get("max")
      # Integer target
      target_upper = target_type.upper()
      if target_upper in _INT_RANGES:
          lo, hi = _INT_RANGES[target_upper]
          if min_v is not None and min_v < lo:
              return False, f"min value {min_v} is below {target_upper} range (min {lo})"
          if max_v is not None and max_v > hi:
              return False, f"max value {max_v} exceeds {target_upper} range (max {hi})"
          return True, ""
      # VARCHAR(N)
      if target_upper.startswith("VARCHAR(") and target_upper.endswith(")"):
          n = int(target_upper[len("VARCHAR("):-1])
          if max_v is not None and isinstance(max_v, str) and len(max_v) > n:
              return False, f"max string length {len(max_v)} exceeds VARCHAR({n})"
          return True, ""
      # Default: assume fit (unknown target type — caller handles as auto-safe).
      return True, ""


  def has_precision_loss(
      conn: duckdb.DuckDBPyConnection,
      file_path: Path,
      column: str,
      target_type: str,
  ) -> tuple[bool, int]:
      """Value scan for precision loss (DOUBLE→BIGINT truncation of fractional values).

      Always full-scans; never samples — a sampled false negative would silently
      discard user data.
      """
      target_upper = target_type.upper()
      if target_upper not in _INT_RANGES:
          return False, 0
      quoted = '"' + column.replace('"', '""') + '"'
      row = conn.execute(
          f"SELECT count(*) FILTER (WHERE {quoted} IS NOT NULL "
          f"AND {quoted} != CAST({quoted} AS {target_upper})) "
          f"FROM '{file_path}'"
      ).fetchone()
      count = int(row[0]) if row and row[0] is not None else 0
      return count > 0, count
  ```

- [ ] **3.4 Run tests to verify all pass**

  ```bash
  uv run --extra test pytest tests/taxi_normalize/test_data_check.py -v
  ```

  Expected: 7 passed.

- [ ] **3.5 Run the full suite**

  ```bash
  uv run --extra test pytest -q
  ```

  Expected: 45 passed (38 previous + 7 new).

- [ ] **3.6 Commit**

  ```bash
  git add -A
  git commit -m "feat(normalize): data_check module — parquet metadata + precision scans"
  ```

---

## Task 4: `mapping.py` — YAML loading and validation

**Motivation:** The mapping YAML defines the human's decisions. This module owns loading + validation. `ack_date` is the only required field on lossy_casts and acknowledged_data_loss entries; everything else is optional.

**Files:**
- Modify: `normalize/src/taxi_normalize/mapping.py`
- Create: `tests/taxi_normalize/test_mapping.py`

### Steps

- [ ] **4.1 Write the failing tests**

  Create `tests/taxi_normalize/test_mapping.py`:

  ```python
  """Tests for YAML mapping loading and validation."""
  import pytest

  from taxi_normalize.mapping import (
      Mapping,
      LossyCastEntry,
      DataLossEntry,
      MappingError,
      load_mapping,
  )


  def _write(path, content):
      path.write_text(content)
      return path


  def test_load_minimal_valid_mapping(tmp_path):
      f = _write(tmp_path / "yellow.yaml", "target: yellow_tripdata_2024-01.parquet\n")
      m = load_mapping(f)
      assert m.target == "yellow_tripdata_2024-01.parquet"
      assert m.renames == {}
      assert m.lossy_casts == {}
      assert m.acknowledged_data_loss == {}


  def test_load_full_mapping(tmp_path):
      f = _write(tmp_path / "yellow.yaml", """
  target: yellow_tripdata_2024-01.parquet
  renames:
    pu_datetime: tpep_pickup_datetime
    do_datetime: tpep_dropoff_datetime
  lossy_casts:
    passenger_count:
      from: DOUBLE
      to: BIGINT
      ack_date: 2026-07-21
      ack_by: andrekamman
      reason: "Integer semantically"
  acknowledged_data_loss:
    pickup_latitude:
      ack_date: 2026-07-21
  """)
      m = load_mapping(f)
      assert m.renames == {"pu_datetime": "tpep_pickup_datetime", "do_datetime": "tpep_dropoff_datetime"}
      assert m.lossy_casts["passenger_count"].ack_date == "2026-07-21"
      assert m.lossy_casts["passenger_count"].ack_by == "andrekamman"
      assert m.lossy_casts["passenger_count"].reason == "Integer semantically"
      assert m.lossy_casts["passenger_count"].from_type == "DOUBLE"
      assert m.lossy_casts["passenger_count"].to_type == "BIGINT"
      assert m.acknowledged_data_loss["pickup_latitude"].ack_date == "2026-07-21"
      assert m.acknowledged_data_loss["pickup_latitude"].ack_by is None
      assert m.acknowledged_data_loss["pickup_latitude"].reason is None


  def test_missing_ack_date_on_lossy_cast_raises(tmp_path):
      f = _write(tmp_path / "yellow.yaml", """
  target: yellow_tripdata_2024-01.parquet
  lossy_casts:
    passenger_count:
      from: DOUBLE
      to: BIGINT
      ack_by: andrekamman
  """)
      with pytest.raises(MappingError, match="ack_date"):
          load_mapping(f)


  def test_missing_ack_date_on_data_loss_raises(tmp_path):
      f = _write(tmp_path / "yellow.yaml", """
  target: yellow_tripdata_2024-01.parquet
  acknowledged_data_loss:
    pickup_latitude:
      reason: "removed"
  """)
      with pytest.raises(MappingError, match="ack_date"):
          load_mapping(f)


  def test_missing_target_raises(tmp_path):
      f = _write(tmp_path / "yellow.yaml", "renames: {}\n")
      with pytest.raises(MappingError, match="target"):
          load_mapping(f)


  def test_unknown_top_level_key_raises(tmp_path):
      f = _write(tmp_path / "yellow.yaml", """
  target: yellow_tripdata_2024-01.parquet
  something_weird: {}
  """)
      with pytest.raises(MappingError, match="something_weird"):
          load_mapping(f)


  def test_missing_file_raises(tmp_path):
      with pytest.raises(MappingError, match="not found"):
          load_mapping(tmp_path / "doesnotexist.yaml")


  def test_malformed_yaml_raises(tmp_path):
      f = _write(tmp_path / "yellow.yaml", "target: yellow\n  invalid: yaml:\n")
      with pytest.raises(MappingError):
          load_mapping(f)
  ```

- [ ] **4.2 Run the failing tests**

  ```bash
  uv run --extra test pytest tests/taxi_normalize/test_mapping.py -v
  ```

  Expected: ImportError; all 8 tests fail collection.

- [ ] **4.3 Implement `mapping.py`**

  Replace `normalize/src/taxi_normalize/mapping.py` with:

  ```python
  """YAML mapping loading and validation."""
  from __future__ import annotations

  from dataclasses import dataclass, field
  from pathlib import Path
  from typing import Optional

  import yaml


  class MappingError(Exception):
      """Raised for any mapping YAML validation failure."""


  @dataclass
  class LossyCastEntry:
      column: str
      from_type: str
      to_type: str
      ack_date: str            # required — anything truthy counts as acknowledgment
      ack_by: Optional[str] = None
      reason: Optional[str] = None


  @dataclass
  class DataLossEntry:
      column: str
      ack_date: str            # required
      ack_by: Optional[str] = None
      reason: Optional[str] = None


  @dataclass
  class Mapping:
      target: str
      renames: dict[str, str] = field(default_factory=dict)
      lossy_casts: dict[str, LossyCastEntry] = field(default_factory=dict)
      acknowledged_data_loss: dict[str, DataLossEntry] = field(default_factory=dict)


  _ALLOWED_KEYS = {"target", "renames", "lossy_casts", "acknowledged_data_loss"}


  def load_mapping(path: Path) -> Mapping:
      """Load and validate a mapping YAML file. Raises MappingError on any problem."""
      if not path.exists():
          raise MappingError(f"Mapping file not found: {path}")

      try:
          raw = yaml.safe_load(path.read_text())
      except yaml.YAMLError as e:
          raise MappingError(f"Invalid YAML in {path}: {e}") from e

      if raw is None:
          raise MappingError(f"Empty mapping file: {path}")
      if not isinstance(raw, dict):
          raise MappingError(f"Mapping root must be a dict, got {type(raw).__name__}")

      unknown = set(raw.keys()) - _ALLOWED_KEYS
      if unknown:
          raise MappingError(f"Unknown top-level key(s): {sorted(unknown)}. "
                             f"Allowed: {sorted(_ALLOWED_KEYS)}")

      target = raw.get("target")
      if not target or not isinstance(target, str):
          raise MappingError("Mapping must have a 'target:' key with a filename value.")

      renames = raw.get("renames") or {}
      if not isinstance(renames, dict):
          raise MappingError("'renames:' must be a dict of {old_name: new_name}.")
      for k, v in renames.items():
          if not isinstance(k, str) or not isinstance(v, str):
              raise MappingError(f"Rename entry must be string→string, got {k!r}: {v!r}")

      lossy_casts: dict[str, LossyCastEntry] = {}
      for col, entry in (raw.get("lossy_casts") or {}).items():
          if not isinstance(entry, dict):
              raise MappingError(f"lossy_casts.{col} must be a dict")
          if not entry.get("ack_date"):
              raise MappingError(f"lossy_casts.{col} requires 'ack_date'")
          from_type = entry.get("from")
          to_type = entry.get("to")
          if not from_type or not to_type:
              raise MappingError(f"lossy_casts.{col} requires 'from' and 'to' types")
          lossy_casts[col] = LossyCastEntry(
              column=col,
              from_type=str(from_type),
              to_type=str(to_type),
              ack_date=str(entry["ack_date"]),
              ack_by=entry.get("ack_by"),
              reason=entry.get("reason"),
          )

      data_loss: dict[str, DataLossEntry] = {}
      for col, entry in (raw.get("acknowledged_data_loss") or {}).items():
          if not isinstance(entry, dict):
              raise MappingError(f"acknowledged_data_loss.{col} must be a dict")
          if not entry.get("ack_date"):
              raise MappingError(f"acknowledged_data_loss.{col} requires 'ack_date'")
          data_loss[col] = DataLossEntry(
              column=col,
              ack_date=str(entry["ack_date"]),
              ack_by=entry.get("ack_by"),
              reason=entry.get("reason"),
          )

      return Mapping(
          target=target,
          renames=renames,
          lossy_casts=lossy_casts,
          acknowledged_data_loss=data_loss,
      )
  ```

- [ ] **4.4 Run tests to verify all pass**

  ```bash
  uv run --extra test pytest tests/taxi_normalize/test_mapping.py -v
  ```

  Expected: 8 passed.

- [ ] **4.5 Full suite check**

  ```bash
  uv run --extra test pytest -q
  ```

  Expected: 53 passed (45 previous + 8 new).

- [ ] **4.6 Commit**

  ```bash
  git add -A
  git commit -m "feat(normalize): mapping module — YAML load + validation, ack_date required"
  ```

---

## Task 5: `planner.py` — per-column action planning

**Motivation:** The planner is the tool's brain. Given a raw file's schema + metadata, the target schema, and the mapping, it decides what to do with each column: passthrough, rename, cast, drop, or flag unresolved.

**Files:**
- Modify: `normalize/src/taxi_normalize/planner.py`
- Create: `tests/taxi_normalize/test_planner.py`

### Steps

- [ ] **5.1 Write the failing tests**

  Create `tests/taxi_normalize/test_planner.py`:

  ```python
  """Tests for the planner logic."""
  import pytest

  from taxi_normalize.mapping import LossyCastEntry, DataLossEntry, Mapping
  from taxi_normalize.planner import ColumnAction, Plan, Unresolved, plan_file


  def _stats(type_, min_v=1, max_v=10, nulls=0, rows=10):
      return {"type": type_, "min": min_v, "max": max_v, "null_count": nulls, "num_rows": rows}


  def _make_metadata(type_map):
      return {col: _stats(t) for col, t in type_map.items()}


  def test_pure_passthrough_identical_schemas():
      raw = _make_metadata({"a": "INTEGER", "b": "VARCHAR"})
      target = _make_metadata({"a": "INTEGER", "b": "VARCHAR"})
      mapping = Mapping(target="t.parquet")
      plan = plan_file(raw, target, mapping)
      assert plan.unresolved == []
      assert [a.action for a in plan.actions] == ["passthrough", "passthrough"]


  def test_rename_applied():
      raw = _make_metadata({"pu_datetime": "TIMESTAMP", "vendorid": "INTEGER"})
      target = _make_metadata({"tpep_pickup_datetime": "TIMESTAMP", "vendorid": "INTEGER"})
      mapping = Mapping(target="t.parquet", renames={"pu_datetime": "tpep_pickup_datetime"})
      plan = plan_file(raw, target, mapping)
      assert plan.unresolved == []
      # Find the rename action
      rename = next(a for a in plan.actions if a.action == "rename")
      assert rename.source_column == "pu_datetime"
      assert rename.target_column == "tpep_pickup_datetime"


  def test_safe_widening_auto_cast():
      raw = _make_metadata({"a": "INTEGER"})
      target = _make_metadata({"a": "BIGINT"})
      mapping = Mapping(target="t.parquet")
      plan = plan_file(raw, target, mapping)
      assert plan.unresolved == []
      cast = next(a for a in plan.actions if a.action == "cast")
      assert cast.cast_to == "BIGINT"


  def test_safe_auto_drop_of_all_null_column():
      raw = {"a": _stats("INTEGER"), "b": _stats("INTEGER", min_v=None, max_v=None, nulls=10, rows=10)}
      target = _make_metadata({"a": "INTEGER"})
      mapping = Mapping(target="t.parquet")
      plan = plan_file(raw, target, mapping)
      assert plan.unresolved == []
      # 'b' is all null and not in target → auto-drop; not represented in actions
      col_names = [a.source_column or a.target_column for a in plan.actions]
      assert "b" not in col_names


  def test_unmapped_drop_with_data_is_unresolved():
      raw = _make_metadata({"a": "INTEGER", "gone_col": "INTEGER"})  # gone_col has data (nulls=0)
      target = _make_metadata({"a": "INTEGER"})
      mapping = Mapping(target="t.parquet")
      plan = plan_file(raw, target, mapping)
      assert any(u.column == "gone_col" and u.kind == "unmapped_drop" for u in plan.unresolved)


  def test_acknowledged_data_loss_removes_from_unresolved():
      raw = _make_metadata({"a": "INTEGER", "gone_col": "INTEGER"})
      target = _make_metadata({"a": "INTEGER"})
      mapping = Mapping(
          target="t.parquet",
          acknowledged_data_loss={"gone_col": DataLossEntry(column="gone_col", ack_date="2026-07-21")},
      )
      plan = plan_file(raw, target, mapping)
      assert plan.unresolved == []


  def test_column_added_since_gets_null_fill():
      raw = _make_metadata({"a": "INTEGER"})
      target = _make_metadata({"a": "INTEGER", "new_col": "VARCHAR"})
      mapping = Mapping(target="t.parquet")
      plan = plan_file(raw, target, mapping)
      assert plan.unresolved == []
      nullfill = next(a for a in plan.actions if a.action == "null_fill")
      assert nullfill.target_column == "new_col"
      assert nullfill.target_type == "VARCHAR"


  def test_lossy_cast_without_ack_is_unresolved():
      # DOUBLE with fractional values → BIGINT is lossy
      raw = {"passenger_count": _stats("DOUBLE", min_v=0.5, max_v=6.5)}
      target = _make_metadata({"passenger_count": "BIGINT"})
      mapping = Mapping(target="t.parquet")
      plan = plan_file(raw, target, mapping)
      assert any(u.column == "passenger_count" and u.kind == "unacked_lossy_cast" for u in plan.unresolved)


  def test_lossy_cast_with_ack_date_only_is_applied():
      raw = {"passenger_count": _stats("DOUBLE", min_v=0.5, max_v=6.5)}
      target = _make_metadata({"passenger_count": "BIGINT"})
      mapping = Mapping(
          target="t.parquet",
          lossy_casts={
              "passenger_count": LossyCastEntry(
                  column="passenger_count",
                  from_type="DOUBLE",
                  to_type="BIGINT",
                  ack_date="2026-07-21",
              )
          },
      )
      plan = plan_file(raw, target, mapping)
      assert plan.unresolved == []
      cast = next(a for a in plan.actions if a.action == "cast")
      assert cast.cast_to == "BIGINT"
  ```

- [ ] **5.2 Run failing tests**

  ```bash
  uv run --extra test pytest tests/taxi_normalize/test_planner.py -v
  ```

  Expected: ImportError; all fail collection.

- [ ] **5.3 Implement `planner.py`**

  Replace `normalize/src/taxi_normalize/planner.py` with:

  ```python
  """Per-column action planning.

  Given a raw file's metadata, a target schema, and a mapping, decides one of:
    - passthrough  (column present unchanged in target)
    - rename       (raw column name differs from target, per mapping)
    - cast         (raw type differs from target — auto if safe, per mapping if lossy)
    - null_fill    (column in target but not in raw)
    - (drop, implicit) — column in raw but not in target, either all-null or acked_data_loss
    - unresolved   — anything the planner can't decide without human input
  """
  from __future__ import annotations

  from dataclasses import dataclass
  from typing import Optional

  from taxi_normalize.data_check import fits_in_target_type
  from taxi_normalize.mapping import Mapping


  @dataclass
  class ColumnAction:
      action: str  # "passthrough" | "rename" | "cast" | "null_fill"
      source_column: Optional[str] = None
      target_column: Optional[str] = None
      cast_to: Optional[str] = None
      target_type: Optional[str] = None


  @dataclass
  class Unresolved:
      column: str
      kind: str  # "unmapped_drop" | "unacked_lossy_cast"
      details: str = ""


  @dataclass
  class Plan:
      actions: list[ColumnAction]
      unresolved: list[Unresolved]


  def _column_has_data(stats: dict) -> bool:
      """A column has data if any non-null value exists across all row groups."""
      total = stats.get("num_rows", 0)
      nulls = stats.get("null_count", 0)
      return (total - nulls) > 0


  def _cast_is_safe(raw_stats: dict, target_type: str) -> bool:
      """Metadata-only safety check for the raw→target type transition."""
      fits, _reason = fits_in_target_type(raw_stats, target_type)
      return fits


  def plan_file(
      raw_metadata: dict[str, dict],
      target_metadata: dict[str, dict],
      mapping: Mapping,
  ) -> Plan:
      """Produce a Plan for one raw file.

      raw_metadata:    {col: stats} from data_check.get_file_metadata(raw_path)
      target_metadata: {col: stats} from data_check.get_file_metadata(target_path)
      """
      actions: list[ColumnAction] = []
      unresolved: list[Unresolved] = []

      # Invert the rename map so we can look up "target column ← raw column" too.
      rename_of = mapping.renames                       # raw -> target
      inv_renames = {v: k for k, v in rename_of.items()}  # target -> raw

      raw_cols = set(raw_metadata.keys())
      target_cols = set(target_metadata.keys())

      # Emit actions in target-schema order so the resulting parquet has a
      # canonical column layout.
      for tgt_col in target_metadata.keys():
          tgt_type = target_metadata[tgt_col]["type"]
          # Case A: target column exists in raw as the same name → passthrough or cast
          if tgt_col in raw_cols:
              raw_stats = raw_metadata[tgt_col]
              raw_type = raw_stats["type"]
              if raw_type == tgt_type:
                  actions.append(ColumnAction(action="passthrough", source_column=tgt_col, target_column=tgt_col))
              elif _cast_is_safe(raw_stats, tgt_type):
                  actions.append(ColumnAction(action="cast", source_column=tgt_col, target_column=tgt_col, cast_to=tgt_type))
              else:
                  # Range doesn't fit → require lossy_cast ack
                  if tgt_col in mapping.lossy_casts:
                      actions.append(ColumnAction(action="cast", source_column=tgt_col, target_column=tgt_col, cast_to=tgt_type))
                  else:
                      unresolved.append(Unresolved(
                          column=tgt_col,
                          kind="unacked_lossy_cast",
                          details=f"{raw_type} -> {tgt_type} would lose data (range/precision)",
                      ))
              continue
          # Case B: target column absent from raw, but mapping renames some raw col INTO it
          if tgt_col in inv_renames:
              src = inv_renames[tgt_col]
              if src in raw_cols:
                  raw_stats = raw_metadata[src]
                  raw_type = raw_stats["type"]
                  if raw_type == tgt_type:
                      actions.append(ColumnAction(action="rename", source_column=src, target_column=tgt_col))
                  elif _cast_is_safe(raw_stats, tgt_type):
                      actions.append(ColumnAction(action="rename", source_column=src, target_column=tgt_col, cast_to=tgt_type))
                  else:
                      if tgt_col in mapping.lossy_casts or src in mapping.lossy_casts:
                          actions.append(ColumnAction(action="rename", source_column=src, target_column=tgt_col, cast_to=tgt_type))
                      else:
                          unresolved.append(Unresolved(
                              column=src,
                              kind="unacked_lossy_cast",
                              details=f"rename {src}->{tgt_col} with type {raw_type} -> {tgt_type} would lose data",
                          ))
                  continue
          # Case C: target column has no source in raw at all → null_fill
          actions.append(ColumnAction(action="null_fill", target_column=tgt_col, target_type=tgt_type))

      # Now check for raw columns that don't map to any target column: potential drops
      for raw_col in raw_cols:
          if raw_col in target_cols:
              continue  # handled as passthrough above
          if raw_col in rename_of:
              continue  # handled as rename above
          # Not in target, not renamed. Either all-null (safe auto-drop) or unresolved.
          raw_stats = raw_metadata[raw_col]
          if not _column_has_data(raw_stats):
              continue  # safe auto-drop, no action emitted
          if raw_col in mapping.acknowledged_data_loss:
              continue  # acknowledged, no action emitted
          unresolved.append(Unresolved(
              column=raw_col,
              kind="unmapped_drop",
              details="column has data; add to renames: or acknowledged_data_loss:",
          ))

      return Plan(actions=actions, unresolved=unresolved)
  ```

- [ ] **5.4 Run tests to verify all pass**

  ```bash
  uv run --extra test pytest tests/taxi_normalize/test_planner.py -v
  ```

  Expected: 9 passed.

- [ ] **5.5 Full suite**

  ```bash
  uv run --extra test pytest -q
  ```

  Expected: 62 passed (53 previous + 9 new).

- [ ] **5.6 Commit**

  ```bash
  git add -A
  git commit -m "feat(normalize): planner module — per-column action planning"
  ```

---

## Task 6: `executor.py` — DuckDB SQL builder + parquet writer

**Motivation:** Convert a Plan into executable SQL, write via DuckDB, ensure atomic replacement of the target file.

**Files:**
- Modify: `normalize/src/taxi_normalize/executor.py`
- Create: `tests/taxi_normalize/test_executor.py`

### Steps

- [ ] **6.1 Write the failing tests**

  Create `tests/taxi_normalize/test_executor.py`:

  ```python
  """Tests for SQL building and parquet writing."""
  import duckdb

  from taxi_normalize.executor import build_transform_sql, execute_transform
  from taxi_normalize.planner import ColumnAction, Plan


  def test_build_transform_sql_covers_all_action_types(tmp_path):
      plan = Plan(
          actions=[
              ColumnAction(action="passthrough", source_column="vendorid", target_column="vendorid"),
              ColumnAction(action="rename", source_column="pu_datetime", target_column="tpep_pickup_datetime"),
              ColumnAction(action="cast", source_column="passenger_count", target_column="passenger_count", cast_to="BIGINT"),
              ColumnAction(action="null_fill", target_column="airport_fee", target_type="DOUBLE"),
          ],
          unresolved=[],
      )
      sql = build_transform_sql(plan, tmp_path / "in.parquet", tmp_path / "out.parquet")
      assert '"vendorid"' in sql
      assert '"pu_datetime" AS "tpep_pickup_datetime"' in sql
      assert 'CAST("passenger_count" AS BIGINT)' in sql
      assert 'NULL::DOUBLE AS "airport_fee"' in sql
      assert "COPY (" in sql
      assert "FORMAT PARQUET" in sql


  def test_build_transform_sql_uses_tmp_path_not_final(tmp_path):
      plan = Plan(actions=[ColumnAction(action="passthrough", source_column="a", target_column="a")], unresolved=[])
      sql = build_transform_sql(plan, tmp_path / "in.parquet", tmp_path / "out.parquet")
      # SQL writes to .tmp.parquet; the caller does the rename.
      assert ".tmp.parquet" in sql


  def test_execute_transform_produces_valid_parquet(yellow_family, tmp_path):
      # Take the era-1 file and passthrough it to a new location
      conn = duckdb.connect(":memory:")
      era1 = yellow_family / "2009" / "yellow_tripdata_2009-01.parquet"
      # Build a passthrough plan by reading the raw schema
      desc = conn.execute(f"DESCRIBE SELECT * FROM '{era1}'").fetchall()
      plan = Plan(
          actions=[ColumnAction(action="passthrough", source_column=r[0], target_column=r[0]) for r in desc],
          unresolved=[],
      )
      out = tmp_path / "passthrough.parquet"
      execute_transform(conn, plan, era1, out)

      assert out.exists()
      assert not out.with_suffix(".tmp.parquet").exists()  # tmp cleaned up
      # Verify identical row count
      orig_count = conn.execute(f"SELECT count(*) FROM '{era1}'").fetchone()[0]
      new_count = conn.execute(f"SELECT count(*) FROM '{out}'").fetchone()[0]
      assert orig_count == new_count


  def test_execute_transform_applies_rename_and_cast(yellow_family, tmp_path):
      conn = duckdb.connect(":memory:")
      era1 = yellow_family / "2009" / "yellow_tripdata_2009-01.parquet"
      out = tmp_path / "normalized.parquet"

      # Rename pu_datetime → tpep_pickup_datetime; cast passenger_count DOUBLE→BIGINT (era1 is safe: 1.0, 2.0)
      plan = Plan(
          actions=[
              ColumnAction(action="passthrough", source_column="vendorid", target_column="vendorid"),
              ColumnAction(action="rename", source_column="pu_datetime", target_column="tpep_pickup_datetime"),
              ColumnAction(action="cast", source_column="passenger_count", target_column="passenger_count", cast_to="BIGINT"),
          ],
          unresolved=[],
      )
      execute_transform(conn, plan, era1, out)

      cols = [r[0] for r in conn.execute(f"DESCRIBE SELECT * FROM '{out}'").fetchall()]
      assert "tpep_pickup_datetime" in cols
      assert "pu_datetime" not in cols
      pc_type = next(r[1] for r in conn.execute(f"DESCRIBE SELECT * FROM '{out}'").fetchall() if r[0] == "passenger_count")
      assert pc_type == "BIGINT"
  ```

- [ ] **6.2 Run failing tests**

  ```bash
  uv run --extra test pytest tests/taxi_normalize/test_executor.py -v
  ```

  Expected: ImportError; all fail collection.

- [ ] **6.3 Implement `executor.py`**

  Replace `normalize/src/taxi_normalize/executor.py` with:

  ```python
  """DuckDB SQL builder and atomic parquet writer."""
  from __future__ import annotations

  import os
  from pathlib import Path

  import duckdb

  from taxi_normalize.planner import ColumnAction, Plan


  def _quote(name: str) -> str:
      """Double-quote an identifier, escaping embedded quotes."""
      return '"' + name.replace('"', '""') + '"'


  def _action_sql(action: ColumnAction) -> str:
      """Produce a single SELECT-list expression for one column action."""
      if action.action == "passthrough":
          return _quote(action.source_column)
      if action.action == "rename":
          src = _quote(action.source_column)
          tgt = _quote(action.target_column)
          if action.cast_to:
              return f"CAST({src} AS {action.cast_to}) AS {tgt}"
          return f"{src} AS {tgt}"
      if action.action == "cast":
          src = _quote(action.source_column)
          tgt = _quote(action.target_column)
          if action.source_column == action.target_column:
              return f"CAST({src} AS {action.cast_to}) AS {tgt}"
          return f"CAST({src} AS {action.cast_to}) AS {tgt}"
      if action.action == "null_fill":
          tgt = _quote(action.target_column)
          return f"NULL::{action.target_type} AS {tgt}"
      raise ValueError(f"Unknown action type: {action.action!r}")


  def _tmp_path_for(final_path: Path) -> Path:
      return final_path.with_suffix(".tmp.parquet")


  def build_transform_sql(plan: Plan, input_path: Path, output_path: Path) -> str:
      """Return the full COPY (...) TO ... SQL statement for one file's transform."""
      select_list = ",\n    ".join(_action_sql(a) for a in plan.actions)
      tmp_path = _tmp_path_for(output_path)
      return (
          "COPY (\n"
          f"  SELECT\n    {select_list}\n"
          f"  FROM read_parquet('{input_path}')\n"
          f") TO '{tmp_path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
      )


  def execute_transform(
      conn: duckdb.DuckDBPyConnection,
      plan: Plan,
      input_path: Path,
      output_path: Path,
  ) -> None:
      """Run the plan and atomically place the result at output_path."""
      output_path.parent.mkdir(parents=True, exist_ok=True)
      tmp_path = _tmp_path_for(output_path)
      sql = build_transform_sql(plan, input_path, output_path)
      conn.execute(sql)
      os.replace(tmp_path, output_path)  # atomic on POSIX
  ```

- [ ] **6.4 Run tests**

  ```bash
  uv run --extra test pytest tests/taxi_normalize/test_executor.py -v
  ```

  Expected: 4 passed.

- [ ] **6.5 Full suite**

  ```bash
  uv run --extra test pytest -q
  ```

  Expected: 66 passed (62 previous + 4 new).

- [ ] **6.6 Commit**

  ```bash
  git add -A
  git commit -m "feat(normalize): executor module — SQL builder + atomic parquet writer"
  ```

---

## Task 7: `bootstrap.py` — YAML scaffold generator with schema-drift integration

**Motivation:** Bootstrap is the biggest module. It analyzes the raw dataset (metadata + schema-drift rename detection), then emits a scaffold YAML with commented SUGGESTED renames and TODO items.

This task also modifies `schema_drift` to accept a configurable sample size (currently hardcoded to 5000 in `detect_renames_by_data`).

**Files:**
- Modify: `schema-drift/src/schema_drift/renames.py` (add optional `sample_size` parameter)
- Modify: `schema-drift/src/schema_drift/stats.py` (thread sample_size into `get_column_stats`)
- Modify: `normalize/src/taxi_normalize/bootstrap.py`
- Create: `tests/taxi_normalize/test_bootstrap.py`

### Steps

- [ ] **7.1 Thread `sample_size` through schema-drift's stats → renames → analyze chain**

  Three files in `schema-drift/` need to be modified so `sample_size` flows from `analyze_data_type` all the way down to the `USING SAMPLE` clause in `get_column_stats`. Do all three edits in one commit at the end of this step.

  **7.1a — `schema-drift/src/schema_drift/stats.py`: accept int-rows or percent-string**

  Modify `get_column_stats` to accept `sample_size: int | str = 5000`. At the top of the function body, build a sample clause:

  ```python
  # Build the SAMPLE clause. int → rows; str ending in "%" → percent;
  # 0 or "0%" or "100%" → no sampling (full scan).
  if isinstance(sample_size, str) and sample_size.endswith("%"):
      pct = int(sample_size.rstrip("%"))
      if pct <= 0 or pct >= 100:
          sample_clause = ""
      else:
          sample_clause = f"USING SAMPLE {pct} PERCENT"
  elif isinstance(sample_size, int) and sample_size > 0:
      sample_clause = f"USING SAMPLE {sample_size}"
  else:
      sample_clause = ""
  ```

  Find the three SQL queries inside `get_column_stats` — each contains `USING SAMPLE {sample_size}`. Replace each with `{sample_clause}` referring to the new local variable.

  **7.1b — `schema-drift/src/schema_drift/renames.py`: add sample_size parameter to detect_renames_by_data and thread it**

  Change the signature of `detect_renames_by_data` from:
  ```python
  def detect_renames_by_data(
      conn, removed, added, file_from, file_to, threshold=0.6,
  ) -> tuple[list[ColumnRename], list[ColumnInfo], list[ColumnInfo]]:
  ```
  to:
  ```python
  def detect_renames_by_data(
      conn, removed, added, file_from, file_to, threshold=0.6,
      sample_size: "int | str" = 5000,
  ) -> tuple[list[ColumnRename], list[ColumnInfo], list[ColumnInfo]]:
  ```

  Inside the function, the two calls to `get_column_stats` currently pass `sample_size=5000` explicitly. Change both to `sample_size=sample_size`.

  **7.1c — `schema-drift/src/schema_drift/analyze.py`: add sample_size parameter to analyze_data_type and thread it**

  Change the signature of `analyze_data_type` from:
  ```python
  def analyze_data_type(
      conn, data_dir, data_type, verify_data=False, generic_mode=False,
  ) -> dict:
  ```
  to:
  ```python
  def analyze_data_type(
      conn, data_dir, data_type, verify_data=False, generic_mode=False,
      sample_size: "int | str" = 5000,
  ) -> dict:
  ```

  Inside the function, find the call to `detect_renames_by_data(conn, removed, added, prev_file, curr_file)` (used in generic mode) and add `sample_size=sample_size`.

  **7.1d — verify schema-drift tests still pass:**

  ```bash
  uv run --extra test pytest tests/schema_drift/ -v
  ```
  Expected: 3 passed (existing smoke tests only care about `--help` and CLI, not the signature changes).

- [ ] **7.2 Write the failing bootstrap tests**

  Create `tests/taxi_normalize/test_bootstrap.py`:

  ```python
  """Tests for bootstrap YAML scaffold generation."""
  from pathlib import Path

  import pytest
  import yaml

  from taxi_normalize.bootstrap import bootstrap_type
  from taxi_normalize.mapping import MappingError, load_mapping


  def test_bootstrap_writes_yaml_with_target_pinned(yellow_family, tmp_path):
      out_yaml = tmp_path / "yellow.yaml"
      bootstrap_type("yellow", yellow_family, out_yaml)
      assert out_yaml.exists()
      raw = yaml.safe_load(out_yaml.read_text())
      # Target is pinned to the newest file
      assert raw["target"] == "yellow_tripdata_2024-01.parquet"


  def test_bootstrap_emits_suggested_rename_for_pu_datetime(yellow_family, tmp_path):
      out_yaml = tmp_path / "yellow.yaml"
      bootstrap_type("yellow", yellow_family, out_yaml)
      text = out_yaml.read_text()
      # Should suggest pu_datetime -> tpep_pickup_datetime as a commented SUGGESTED entry
      assert "SUGGESTED" in text
      assert "pu_datetime" in text
      assert "tpep_pickup_datetime" in text


  def test_bootstrap_flags_pickup_latitude_as_potential_data_loss(yellow_family, tmp_path):
      out_yaml = tmp_path / "yellow.yaml"
      bootstrap_type("yellow", yellow_family, out_yaml)
      text = out_yaml.read_text()
      # pickup_latitude/longitude have data but no target column and no rename candidate
      # (they should appear in the acknowledged_data_loss TODO block)
      assert "pickup_latitude" in text
      assert "acknowledged_data_loss" in text
      assert "TODO" in text


  def test_bootstrap_refuses_to_overwrite(yellow_family, tmp_path):
      out_yaml = tmp_path / "yellow.yaml"
      out_yaml.write_text("target: existing.parquet\n")
      with pytest.raises(FileExistsError):
          bootstrap_type("yellow", yellow_family, out_yaml)


  def test_bootstrap_sample_flag_accepts_percent(yellow_family, tmp_path):
      # Sanity: the call succeeds when sample is a percent string.
      out_yaml = tmp_path / "yellow.yaml"
      bootstrap_type("yellow", yellow_family, out_yaml, sample="10%")
      assert out_yaml.exists()


  def test_bootstrap_sample_flag_accepts_absolute_rows(yellow_family, tmp_path):
      out_yaml = tmp_path / "yellow.yaml"
      bootstrap_type("yellow", yellow_family, out_yaml, sample="100")
      assert out_yaml.exists()


  def test_bootstrap_no_drift_family_produces_minimal_yaml(no_drift_family, tmp_path):
      out_yaml = tmp_path / "green.yaml"
      bootstrap_type("green", no_drift_family, out_yaml)
      raw = yaml.safe_load(out_yaml.read_text())
      # Nothing should need suggestion or ack
      assert raw.get("renames", {}) in ({}, None)
      # Loading should succeed with just target set
      m = load_mapping(out_yaml)
      assert m.target.startswith("green_tripdata_")
  ```

- [ ] **7.3 Run failing tests**

  ```bash
  uv run --extra test pytest tests/taxi_normalize/test_bootstrap.py -v
  ```

  Expected: ImportError; all 7 tests fail collection.

- [ ] **7.4 Implement `bootstrap.py`**

  Replace `normalize/src/taxi_normalize/bootstrap.py` with:

  ```python
  """Mapping YAML scaffold generator.

  Analyzes raw/<type>/ files (schema + metadata + schema-drift rename detection)
  and writes a YAML scaffold with SUGGESTED entries for likely renames and TODO
  entries for lossy casts and potential data loss.
  """
  from __future__ import annotations

  from pathlib import Path
  from typing import Union

  import duckdb

  from schema_drift.analyze import (
      analyze_data_type,
      find_parquet_files,
      get_parquet_schema,
  )
  from schema_drift.renames import detect_renames_by_data
  from taxi_normalize.data_check import (
      aggregate_across_files,
      fits_in_target_type,
      get_file_metadata,
  )


  RENAME_CONFIDENCE_THRESHOLD = 0.6  # matches schema-drift's default


  def _parse_sample(sample: str) -> Union[int, str]:
      """Convert CLI --sample value into what schema-drift's get_column_stats expects."""
      s = sample.strip()
      if s.endswith("%"):
          pct = int(s.rstrip("%"))
          if pct <= 0:
              return 0     # 0 → no sampling
          if pct >= 100:
              return 0     # 100% → no sampling
          return f"{pct}%"
      if s.isdigit():
          return int(s)
      raise ValueError(f"Invalid --sample value: {sample!r}. Use N or N%.")


  def bootstrap_type(
      data_type: str,
      raw_dir: Path,
      output_yaml: Path,
      sample: str = "100%",
  ) -> None:
      """Analyze raw_dir and write output_yaml with scaffolding.

      raw_dir: the type-scoped raw directory (e.g., raw/yellow/). May contain
               year subdirectories with parquet files (matches downloader layout).
      output_yaml: destination for the mapping file. Refuses to overwrite.
      sample: passed through to schema-drift's rename detector; default 100%.
      """
      if output_yaml.exists():
          raise FileExistsError(
              f"{output_yaml} already exists. Delete it or edit manually. "
              f"Re-run bootstrap after deletion to regenerate scaffolding."
          )

      # Collect files. raw_dir may be the top-level "raw/" or a per-type dir.
      # Support both by peeking one level down.
      files = sorted(raw_dir.rglob(f"{data_type}_tripdata_*.parquet"))
      if not files:
          files = sorted(raw_dir.rglob("*.parquet"))
      if not files:
          raise FileNotFoundError(f"No parquet files found under {raw_dir} for {data_type}")

      target_file = files[-1]  # newest by filename sort

      conn = duckdb.connect(":memory:")
      files_md = [get_file_metadata(conn, f) for f in files]
      agg = aggregate_across_files(files_md)
      target_md = get_file_metadata(conn, target_file)
      target_cols = set(target_md.keys())

      # Ask schema-drift for rename candidates via its Python API.
      # Use the "parent of raw_dir" as data_dir if raw_dir is per-type, otherwise use raw_dir.
      if raw_dir.name == data_type:
          data_dir = raw_dir.parent
      else:
          data_dir = raw_dir
      sample_arg = _parse_sample(sample)
      analysis = analyze_data_type(
          conn, data_dir, data_type, verify_data=False, generic_mode=True,
          sample_size=sample_arg,
      )
      # analysis['changes'] is a list of SchemaChange objects at transition points.
      # Collect all rename candidates across transitions, keyed by (old_col, new_col).
      rename_candidates: dict[tuple[str, str], float] = {}
      for change in analysis["changes"]:
          for rename in change.columns_renamed:
              old = rename.old_col.name
              new = rename.new_col.name
              conf = rename.confidence
              existing = rename_candidates.get((old, new), 0)
              if conf > existing:
                  rename_candidates[(old, new)] = conf

      # Determine per-column disposition
      lossy_cast_todos: list[dict] = []
      data_loss_todos: list[dict] = []

      for col, stats in agg.items():
          if col in target_cols:
              # Check whether the type change is lossy
              raw_type_seen = stats["types_seen"][0] if stats["types_seen"] else None
              tgt_type = target_md[col]["type"]
              if raw_type_seen and raw_type_seen != tgt_type:
                  fits, reason = fits_in_target_type({"type": raw_type_seen, "min": stats["min_range"], "max": stats["max_range"]}, tgt_type)
                  if not fits:
                      lossy_cast_todos.append({
                          "column": col, "from": raw_type_seen, "to": tgt_type, "reason": reason,
                      })
              continue
          # Column not in target
          if stats["files_with_data"] == 0:
              continue  # safe auto-drop
          # Is there a rename suggestion targeting an existing target column for this old col?
          renamed_to = [new for (old, new), conf in rename_candidates.items() if old == col and new in target_cols and conf >= RENAME_CONFIDENCE_THRESHOLD]
          if renamed_to:
              continue  # will be emitted as SUGGESTED rename below
          data_loss_todos.append({"column": col, "files_present": stats["files_present"]})

      # Emit YAML text with commented SUGGESTED lines and TODO placeholders.
      # We write text directly (not via yaml.dump) so we can include comments.
      lines: list[str] = []
      lines.append(f"# Generated by `normalize bootstrap {data_type}`. Review each SUGGESTED entry:")
      lines.append("# uncomment to accept, delete to reject. Fill in each TODO before running.")
      lines.append(f"target: {target_file.name}")
      lines.append("")
      lines.append("renames:")
      any_rename = False
      for (old, new), conf in sorted(rename_candidates.items(), key=lambda x: -x[1]):
          if new not in target_cols:
              continue
          verified = "data-verified" if conf >= 0.8 else "NOT data-verified — review carefully"
          lines.append(f"  # SUGGESTED (confidence {int(conf*100)}%, {verified}) — uncomment to accept:")
          lines.append(f"  # {old}: {new}")
          any_rename = True
      if not any_rename:
          lines.append("  {}")
      lines.append("")
      lines.append("lossy_casts:")
      if not lossy_cast_todos:
          lines.append("  {}")
      else:
          for entry in lossy_cast_todos:
              lines.append(f"  # DETECTED: {entry['column']} changed {entry['from']} -> {entry['to']}. {entry['reason']}")
              lines.append("  # Set ack_date to accept (ack_by and reason are optional):")
              lines.append(f"  # {entry['column']}:")
              lines.append(f"  #   from: {entry['from']}")
              lines.append(f"  #   to: {entry['to']}")
              lines.append("  #   ack_date: TODO")
      lines.append("")
      lines.append("acknowledged_data_loss:")
      if not data_loss_todos:
          lines.append("  {}")
      else:
          for entry in data_loss_todos:
              lines.append(f"  # DETECTED: {entry['column']} has non-null data in {entry['files_present']} file(s),")
              lines.append("  # no rename candidate above the confidence threshold.")
              lines.append("  # Set ack_date to accept the loss (ack_by and reason are optional):")
              lines.append(f"  # {entry['column']}:")
              lines.append("  #   ack_date: TODO")

      output_yaml.parent.mkdir(parents=True, exist_ok=True)
      output_yaml.write_text("\n".join(lines) + "\n")
  ```

- [ ] **7.5 Run bootstrap tests**

  ```bash
  uv run --extra test pytest tests/taxi_normalize/test_bootstrap.py -v
  ```

  Expected: 7 passed.

- [ ] **7.6 Full suite**

  ```bash
  uv run --extra test pytest -q
  ```

  Expected: 73 passed (66 previous + 7 new).

- [ ] **7.7 Commit**

  ```bash
  git add -A
  git commit -m "feat(normalize): bootstrap module + configurable sample size in schema-drift"
  ```

---

## Task 8: CLI wiring + end-to-end smoke test

**Motivation:** Wire the CLI stub from Task 1 to actually dispatch to bootstrap and normalize implementations. Add an end-to-end test that goes bootstrap → manual YAML → normalize → verify output.

**Files:**
- Modify: `normalize/src/taxi_normalize/cli.py` (replace stub with real dispatch)
- Create: `tests/taxi_normalize/test_cli_smoke.py`

### Steps

- [ ] **8.1 Write the failing CLI tests**

  Create `tests/taxi_normalize/test_cli_smoke.py`:

  ```python
  """Smoke tests for the CLI. Uses subprocess for genuine end-to-end coverage."""
  import subprocess
  import sys
  from pathlib import Path

  import yaml


  def _run(*args, cwd=None):
      return subprocess.run(
          [sys.executable, "-m", "taxi_normalize.cli", *args],
          capture_output=True, text=True, cwd=cwd,
      )


  def test_help_exits_zero():
      r = _run("--help")
      assert r.returncode == 0
      assert "normalize" in r.stdout.lower()


  def test_bootstrap_help_exits_zero():
      r = _run("bootstrap", "--help")
      assert r.returncode == 0
      assert "--sample" in r.stdout


  def test_normalize_missing_mapping_errors(tmp_path):
      # `normalize yellow` in a dir with no mappings/ → error
      (tmp_path / "raw" / "yellow" / "2024").mkdir(parents=True)
      r = _run("normalize", "yellow", cwd=tmp_path)
      assert r.returncode != 0
      assert "mapping" in (r.stdout + r.stderr).lower()


  def test_end_to_end_bootstrap_then_normalize(yellow_family, tmp_path, monkeypatch):
      # 1. bootstrap emits the scaffold
      mappings_dir = tmp_path / "normalize" / "mappings"
      raw_dir = yellow_family.parent  # tmp_path/raw
      # Change to a working dir so CLI can find raw/ and normalize/mappings/ relative
      workdir = tmp_path
      (workdir / "normalize" / "mappings").mkdir(parents=True)
      # Symlink or copy the raw/ dir into workdir (yellow_family is already tmp_path/raw/yellow)
      # Bootstrap wants raw/<type>/ under workdir/raw/
      # yellow_family already lives at tmp_path/raw/yellow, so we're good.

      r_boot = _run("bootstrap", "yellow", cwd=workdir)
      assert r_boot.returncode == 0, r_boot.stderr
      mapping_file = mappings_dir / "yellow.yaml"
      assert mapping_file.exists()

      # 2. Hand-edit: uncomment the rename suggestions + ack the lossy cast + ack data loss.
      # We write a complete valid mapping directly, matching what the human would produce.
      mapping_file.write_text("""
  target: yellow_tripdata_2024-01.parquet
  renames:
    pu_datetime: tpep_pickup_datetime
    do_datetime: tpep_dropoff_datetime
  lossy_casts:
    passenger_count:
      from: DOUBLE
      to: BIGINT
      ack_date: 2026-07-21
  acknowledged_data_loss:
    pickup_latitude:
      ack_date: 2026-07-21
    pickup_longitude:
      ack_date: 2026-07-21
  """)

      # 3. Run normalize
      r_norm = _run("normalize", "yellow", cwd=workdir)
      assert r_norm.returncode == 0, r_norm.stderr

      # 4. Verify outputs exist and have target schema
      out_dir = workdir / "raw-normalized" / "yellow"
      out_files = sorted(out_dir.rglob("*.parquet"))
      assert len(out_files) == 3
      # Check the era-1 output has the renamed columns
      import duckdb
      conn = duckdb.connect(":memory:")
      era1_out = next(f for f in out_files if "2009-01" in f.name)
      cols = {r[0] for r in conn.execute(f"DESCRIBE SELECT * FROM '{era1_out}'").fetchall()}
      assert "tpep_pickup_datetime" in cols
      assert "pu_datetime" not in cols
      assert "pickup_latitude" not in cols


  def test_normalize_with_unresolved_mapping_errors_consolidated(yellow_family, tmp_path):
      workdir = tmp_path
      (workdir / "normalize" / "mappings").mkdir(parents=True)
      # Empty mapping — will produce unresolved items
      (workdir / "normalize" / "mappings" / "yellow.yaml").write_text(
          "target: yellow_tripdata_2024-01.parquet\n"
      )
      r = _run("normalize", "yellow", cwd=workdir)
      assert r.returncode == 1
      out = r.stdout + r.stderr
      assert "unresolved" in out.lower()
      assert "pu_datetime" in out
  ```

- [ ] **8.2 Run failing tests**

  ```bash
  uv run --extra test pytest tests/taxi_normalize/test_cli_smoke.py -v
  ```

  Expected: 5 tests fail (CLI still returns exit 2 with "not implemented").

- [ ] **8.3 Rewrite `cli.py` with real dispatch**

  Replace `normalize/src/taxi_normalize/cli.py` with:

  ```python
  """Entry point for the `normalize` command."""
  from __future__ import annotations

  import argparse
  import sys
  from pathlib import Path

  import duckdb

  from taxi_normalize.bootstrap import bootstrap_type
  from taxi_normalize.data_check import get_file_metadata
  from taxi_normalize.executor import execute_transform
  from taxi_normalize.mapping import MappingError, load_mapping
  from taxi_normalize.planner import plan_file


  DATA_TYPES = ("yellow", "green", "fhv", "fhvhv")


  def main() -> int:
      parser = argparse.ArgumentParser(
          prog="normalize",
          description="Rewrite historical TLC parquet files to conform to the latest schema.",
      )
      subparsers = parser.add_subparsers(dest="command")

      bootstrap = subparsers.add_parser(
          "bootstrap",
          help="Generate normalize/mappings/<type>.yaml from raw/<type>/ + schema-drift analysis.",
      )
      bootstrap.add_argument("data_type", help=f"One of: {', '.join(DATA_TYPES)}")
      bootstrap.add_argument(
          "--sample", default="100%",
          help="Rows to sample for rename verification: N (absolute) or N%% (percent). Default: 100%%.",
      )

      normalize_cmd = subparsers.add_parser(
          "normalize", help="Normalize one data type (or all if no argument).",
      )
      normalize_cmd.add_argument("data_type", nargs="?")

      args = parser.parse_args()

      if args.command is None:
          args.command = "normalize"
          args.data_type = None

      if args.command == "bootstrap":
          return _cmd_bootstrap(args.data_type, args.sample)
      if args.command == "normalize":
          types = [args.data_type] if args.data_type else list(DATA_TYPES)
          return _cmd_normalize(types)
      return 0


  def _cmd_bootstrap(data_type: str, sample: str) -> int:
      raw_dir = Path("raw") / data_type
      if not raw_dir.exists():
          print(f"error: {raw_dir} does not exist. Run the downloader first.", file=sys.stderr)
          return 2
      output_yaml = Path("normalize") / "mappings" / f"{data_type}.yaml"
      try:
          bootstrap_type(data_type, raw_dir, output_yaml, sample=sample)
      except FileExistsError as e:
          print(f"error: {e}", file=sys.stderr)
          return 2
      except (FileNotFoundError, ValueError) as e:
          print(f"error: {e}", file=sys.stderr)
          return 2
      print(f"Wrote {output_yaml}. Review the SUGGESTED entries and fill in TODOs before running normalize.")
      return 0


  def _cmd_normalize(types: list[str]) -> int:
      overall_rc = 0
      for data_type in types:
          rc = _normalize_one(data_type)
          if rc != 0:
              overall_rc = rc
      return overall_rc


  def _normalize_one(data_type: str) -> int:
      raw_dir = Path("raw") / data_type
      mapping_path = Path("normalize") / "mappings" / f"{data_type}.yaml"
      out_dir = Path("raw-normalized") / data_type

      if not mapping_path.exists():
          print(
              f"error: mapping file {mapping_path} not found. "
              f"Run `normalize bootstrap {data_type}` first.",
              file=sys.stderr,
          )
          return 2

      try:
          mapping = load_mapping(mapping_path)
      except MappingError as e:
          print(f"error: mapping {mapping_path}: {e}", file=sys.stderr)
          return 2

      if not raw_dir.exists():
          print(f"{data_type}: no raw files at {raw_dir}, skipping", file=sys.stdout)
          return 0

      raw_files = sorted(raw_dir.rglob("*.parquet"))
      target_file = raw_dir.rglob(mapping.target)
      target_path = next(iter(target_file), None)
      if target_path is None:
          print(
              f"error: target file {mapping.target} not found under {raw_dir}",
              file=sys.stderr,
          )
          return 2

      conn = duckdb.connect(":memory:")
      target_md = get_file_metadata(conn, target_path)

      # Plan all files to collect unresolved items in one report.
      plans: list[tuple[Path, "plan_file"]] = []
      unresolved_by_col: dict[str, str] = {}
      for f in raw_files:
          raw_md = get_file_metadata(conn, f)
          plan = plan_file(raw_md, target_md, mapping)
          plans.append((f, plan))
          for u in plan.unresolved:
              unresolved_by_col.setdefault(u.column, u.kind + ": " + u.details)

      if unresolved_by_col:
          print(f"\nERROR: {data_type} — {len(unresolved_by_col)} unresolved item(s) in {mapping_path}", file=sys.stderr)
          print(f"  Cannot normalize this data type until these are handled.\n", file=sys.stderr)
          for col, details in sorted(unresolved_by_col.items()):
              print(f"  - {col}: {details}", file=sys.stderr)
          print("\n  Options: add to `renames:`, `lossy_casts:` (with ack_date), or `acknowledged_data_loss:` (with ack_date).", file=sys.stderr)
          print(f"  Nothing was written for {data_type}.\n", file=sys.stderr)
          return 1

      written = 0
      skipped = 0
      for f, plan in plans:
          # Compute output path preserving year subdirectory.
          rel = f.relative_to(raw_dir)
          out_path = out_dir / rel
          if out_path.exists():
              skipped += 1
              continue
          execute_transform(conn, plan, f, out_path)
          written += 1

      print(f"{data_type}: {written} file(s) normalized, {skipped} skipped (already present).")
      return 0


  if __name__ == "__main__":
      sys.exit(main())
  ```

- [ ] **8.4 Run tests to verify all pass**

  ```bash
  uv run --extra test pytest tests/taxi_normalize/test_cli_smoke.py -v
  ```

  Expected: 5 passed.

- [ ] **8.5 Full suite**

  ```bash
  uv run --extra test pytest -q
  ```

  Expected: 78 passed (73 previous + 5 new).

- [ ] **8.6 Manual smoke test**

  From the repo root:
  ```bash
  uv run --extra test normalize --help
  uv run --extra test normalize bootstrap yellow --sample 5000
  # If successful, the file at normalize/mappings/yellow.yaml can be inspected.
  # DO NOT commit that file yet — it's your local scaffold to edit for real data.
  # After editing, run:
  # uv run --extra test normalize yellow
  ```
  Expected: bootstrap produces a scaffold; if you then delete `raw-normalized/yellow/` and run `normalize yellow` with the empty scaffold, you get the consolidated error report.

  Reset any test artifacts:
  ```bash
  rm -rf normalize/mappings/yellow.yaml raw-normalized/
  ```

- [ ] **8.7 Commit**

  ```bash
  git add -A
  git commit -m "feat(normalize): CLI dispatch — bootstrap + normalize + consolidated error report"
  ```

---

## Task 9: README

**Motivation:** Document the tool for users. Same structure as the other per-component READMEs (downloader/README.md, schema-drift/README.md, k6-loadtest/README.md).

**Files:**
- Create: `normalize/README.md`

### Steps

- [ ] **9.1 Create the README**

  Create `normalize/README.md`:

  ```markdown
  # normalize

  Rewrites historical TLC parquet files to conform to the latest schema, driven by a per-type curated YAML mapping. Data loss is a first-class error — the tool halts unless every discarded column or lossy cast has an explicit acknowledgment.

  ## Usage

  ```bash
  # First time per data type: generate the mapping scaffold
  uv run normalize bootstrap yellow

  # Edit normalize/mappings/yellow.yaml — uncomment SUGGESTED renames, fill TODO ack_date fields

  # Then run the normalizer
  uv run normalize yellow

  # Or run all four types
  uv run normalize
  ```

  Requires the parquet mirror produced by the [downloader](../downloader/README.md) at `raw/<type>/<year>/*.parquet`. Writes to `raw-normalized/<type>/<year>/*.parquet`.

  ## Mapping file

  Every data type has a mapping at `normalize/mappings/<type>.yaml`. `ack_date` is the only required field for `lossy_casts:` and `acknowledged_data_loss:` entries; `ack_by` and `reason` are optional but recommended for git-history documentation.

  ```yaml
  target: yellow_tripdata_2024-01.parquet
  renames:
    pu_datetime: tpep_pickup_datetime
  lossy_casts:
    passenger_count:
      from: DOUBLE
      to: BIGINT
      ack_date: 2026-07-21
  acknowledged_data_loss:
    pickup_latitude:
      ack_date: 2026-07-21
  ```

  ## What runs automatically (no mapping needed)

  - Columns always-null in historical data, missing from target → dropped
  - Columns missing from historical data, present in target → filled with NULL
  - Type widenings that cannot lose data (INT → BIGINT, VARCHAR(10) → VARCHAR(50), etc.)

  ## What triggers an error

  - Historical column has non-null data, is missing from target, and has no `renames:` or `acknowledged_data_loss:` entry
  - Type cast could lose data (range or precision) and there's no `lossy_casts:` entry with an `ack_date`

  The error is a single consolidated report per data type — nothing is written if any item is unresolved.

  ## Options

  - `--sample <N|N%>` on `bootstrap`: rows or percent sampled during schema-drift rename verification. Default `100%` (full scan). Reduce only for very large datasets; metadata-only checks and precision scans always full-scan regardless.
  ```

- [ ] **9.2 Verify markdown renders and full suite still passes**

  ```bash
  uv run --extra test pytest -q
  ```

  Expected: 78 passed.

- [ ] **9.3 Commit**

  ```bash
  git add -A
  git commit -m "docs(normalize): per-component README"
  ```

---

## Post-implementation verification

- [ ] **Full test suite passes:**
  ```bash
  uv run --extra test pytest -q
  ```
  Expected: 78 passed.

- [ ] **Entry point works:**
  ```bash
  uv run --extra test normalize --help
  ```

- [ ] **No leftover artifacts:**
  ```bash
  git status --short
  ```
  Expected: clean (any `raw-normalized/` from smoke testing is not tracked because we didn't add it to git).

- [ ] **All commits present in order:**
  ```bash
  git log --oneline main..HEAD
  ```
  Expected: nine commits, ending with the README.

- [ ] **Wheel builds and packages the new component:**
  ```bash
  uv build 2>&1 | tail -5
  ```
  Expected: build succeeds; new wheel contains `taxi_normalize/` at root.
