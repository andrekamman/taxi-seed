# Normalizer Design

**Date:** 2026-07-21
**Status:** Approved, ready for implementation planning
**Sub-project of:** the four-part expansion (normalizer, SQL Server loader, orchestrator, CI/CD)

## Motivation

TLC parquet files drift heavily over the years — especially the first decade. Column renames, type changes, entirely different location schemes (lat/lon replaced by LocationID). The `schema-drift` component identifies this drift. The normalizer turns the drift into a resolved dataset: every historical parquet file rewritten to conform to the latest schema, so downstream consumers (the SQL Server loader, ad-hoc analysts, DuckDB queries) get one uniform dataset.

## Non-goals

- Preserving multiple schema versions in the output. The output is single-schema.
- Handling schema drift automatically without human input on ambiguous cases. Data loss requires explicit acknowledgment.
- Alternative target schemas (e.g., "normalize to 2015 schema instead of latest"). Latest wins; no configuration for target era.
- Anything about the loader, orchestrator, or CI/CD sub-projects — those have their own specs.

## Core contract

The normalizer reads historical parquet + a curated YAML mapping per data type, and writes normalized parquet files that all conform to the latest schema.

**Data loss is a first-class error, not a warning.** If applying the mapping would drop a column that had non-null data in any historical file, or perform a lossy cast (e.g., `DOUBLE → BIGINT` where the source has fractional values, `VARCHAR(50) → VARCHAR(10)` where source strings exceed 10 chars), the tool halts with an error. The human must add an explicit entry to the YAML with an `ack_date` before the tool will proceed. `ack_by` and `reason` are optional but recommended for git-history documentation.

**Latest data is a no-op.** Once caught up, historical files already match the target schema; the tool only re-engages when TLC introduces a new drift.

## Directory layout

New component `normalize/`, matching the monorepo pattern:

```
normalize/
├── README.md
├── mappings/                         # checked-in per-type YAMLs
│   ├── yellow.yaml
│   ├── green.yaml
│   ├── fhv.yaml
│   └── fhvhv.yaml
└── src/taxi_normalize/
    ├── __init__.py
    ├── cli.py                        # entry point: `normalize`
    ├── bootstrap.py                  # generates starter YAML with schema-drift suggestions
    ├── mapping.py                    # loads + validates YAML, detects unresolved items
    ├── planner.py                    # compares raw schema vs target, decides per-column action
    ├── executor.py                   # runs DuckDB SQL, writes normalized parquet
    └── data_check.py                 # metadata-driven presence/range checks
```

**Output:**
```
raw-normalized/
  yellow/2009/yellow_tripdata_2009-01.parquet
  ...
  yellow/2024/yellow_tripdata_2024-01.parquet
  green/  fhv/  fhvhv/
```

**Tests:** `tests/taxi_normalize/` (mirrors existing `tests/{k6_loadtest,schema_drift,taxi_shared}/`).

**pyproject.toml additions:**
- Add `normalize/src/taxi_normalize` to `[tool.hatch.build.targets.wheel] packages`.
- Add `normalize = "taxi_normalize.cli:main"` to `[project.scripts]`.

## Mapping YAML

The mapping file at `normalize/mappings/<type>.yaml` has four top-level keys, all optional. The normalizer only requires entries for **ambiguous** cases; safe transformations are automatic.

```yaml
target: yellow_tripdata_2024-01.parquet     # pinned; bootstrap picks newest, human bumps

# Data-preserving renames: old column name -> latest column name
renames:
  pu_datetime: tpep_pickup_datetime
  do_datetime: tpep_dropoff_datetime

# Lossy type casts requiring human ack. Safe widening casts (INT->BIGINT,
# DOUBLE->DECIMAL, VARCHAR(10)->VARCHAR(50)) are auto-applied; only list
# casts that could lose data. `ack_date` is required; `ack_by` and `reason`
# are optional but recommended.
lossy_casts:
  passenger_count:
    from: DOUBLE
    to: BIGINT
    ack_date: 2026-07-21
    ack_by: andrekamman                            # optional
    reason: "Fractional values are data-entry noise; passenger_count is logically integer."  # optional

# Columns present in historical data with non-null values, absent from the target
# schema, and not renamed to anything. `ack_date` alone is sufficient acknowledgment;
# `ack_by` and `reason` are optional but recommended for future readers.
acknowledged_data_loss:
  pickup_latitude:
    ack_date: 2026-07-21
    ack_by: andrekamman                            # optional
    reason: "Replaced by PULocationID in 2016; lat/lon has no equivalent in new schema."  # optional
```

### Automatic behavior (no YAML entry needed)

- Column always-null across all historical files, missing from target → auto-drop.
- Column missing from historical files but present in target → filled with NULL.
- Type widening that cannot lose data (`INT → BIGINT`, `FLOAT → DOUBLE`, `VARCHAR(N) → VARCHAR(M>N)`) → auto-cast.
- Column present unchanged in historical and target → passthrough.

### Error triggers (require YAML entry)

- Historical column has non-null data, is missing from target, and has no entry in `renames:` or `acknowledged_data_loss:`.
- Type cast between historical and target could lose data (either range overflow or precision loss), and no entry in `lossy_casts:`.
- Entry in `lossy_casts:` or `acknowledged_data_loss:` is missing the required `ack_date`.

## CLI

Two subcommands, one no-arg mode:

```
normalize bootstrap <type> [--sample <N|N%>]   # generate normalize/mappings/<type>.yaml
normalize <type>                               # normalize raw/<type>/ -> raw-normalized/<type>/
normalize                                      # (no arg) run all four types
```

**`--sample <N|N%>`** controls how many rows are read when comparing candidate rename pairs (schema-drift's data-driven rename verification). Format is either an absolute row count (`--sample 5000`) or a percentage (`--sample 10%`). Default is `100%` (full scan) — err on the safe side, only reduce for massive datasets where bootstrap runtime becomes a problem. The flag does NOT affect metadata-only checks (schema, null counts, min/max ranges) or the precision-loss check for lossy casts — both always use the complete file, since sampling those risks false-negative data-loss decisions.

### `normalize bootstrap <type>`

Scans `raw/<type>/**/*.parquet`, calls `schema_drift.analyze.analyze_data_type()` and `schema_drift.renames.detect_renames_by_data()` internally to identify rename candidates and data-driven verification, runs the presence/range checks (metadata-only), and writes `normalize/mappings/<type>.yaml`:

- **YAML doesn't exist yet:** write it fresh with the scaffold below.
- **YAML exists:** refuse to overwrite. Print: `<type>.yaml already exists. Delete it or edit manually. Re-run bootstrap after deletion to regenerate scaffolding.`

**Scaffold shape** (with schema-drift suggestions pre-filled as commented entries):

```yaml
# Generated by `normalize bootstrap yellow`. Review each SUGGESTED entry:
# uncomment to accept, delete to reject. Fill in each TODO before running.
target: yellow_tripdata_2024-01.parquet

renames:
  # SUGGESTED (confidence 92%, data-verified) — uncomment to accept:
  # pu_datetime: tpep_pickup_datetime
  # SUGGESTED (confidence 92%, data-verified) — uncomment to accept:
  # do_datetime: tpep_dropoff_datetime
  # SUGGESTED (confidence 68%, NOT data-verified — review carefully):
  # pu_location: PULocationID

lossy_casts:
  # DETECTED: passenger_count changed DOUBLE -> BIGINT.
  # 12,384 non-integer values across 84 files would truncate.
  # Set ack_date to accept (ack_by and reason are optional):
  # passenger_count:
  #   from: DOUBLE
  #   to: BIGINT
  #   ack_date: TODO

acknowledged_data_loss:
  # DETECTED: pickup_latitude has non-null data in 84 files (2009-01 to 2016-06),
  # no rename candidate above the confidence threshold.
  # Set ack_date to accept the loss (ack_by and reason are optional):
  # pickup_latitude:
  #   ack_date: TODO
  # DETECTED: pickup_longitude has non-null data in 84 files (2009-01 to 2016-06):
  # pickup_longitude:
  #   ack_date: TODO
```

Rationale for the SUGGESTED-with-comment pattern: schema-drift's rename detection is heuristic + data-verification, not perfect. Emitting commented suggestions preserves the human-in-the-loop philosophy while doing the tedious work of listing candidates. High-confidence data-verified suggestions get flagged as safe; low-confidence ones get flagged as needing careful review.

Confidence threshold for emitting a SUGGESTED rename: 60% (same as schema-drift's default). Below that, the column goes into `acknowledged_data_loss:` as a TODO instead.

### `normalize <type>` (or `normalize` for all)

1. Load `normalize/mappings/<type>.yaml`. Error if missing — direct user to `normalize bootstrap <type>`.
2. Load target parquet schema via `parquet_schema(target)`.
3. For each `raw/<type>/<year>/*.parquet` in chronological order:
   a. Skip if `raw-normalized/<type>/<year>/<same-name>` already exists (idempotent, matches downloader pattern).
   b. Read raw schema + metadata (`parquet_schema`, `parquet_metadata`) — footer only, no data scan.
   c. Planner compares raw schema vs target using the mapping, produces a plan per column: passthrough / rename / cast / drop / null-fill.
   d. Collect unresolved items (unmapped drops with data, unacked lossy casts). Don't halt on the first file — accumulate across all files in this data type for a single consolidated error.
4. If any unresolved items exist: skip this data type entirely, print consolidated error report, exit code 1. Nothing is written.
5. Otherwise, for each raw file: generate the transform SQL, execute it writing to `<output>.tmp.parquet`, then atomic rename to final path.
6. Print per-type summary.

### Error report format

Single consolidated report per data type. Sample:

```
ERROR: yellow — 4 unresolved items in normalize/mappings/yellow.yaml
  Cannot normalize this data type until these are handled.

  Missing renames or acknowledged_data_loss entries:
    - pu_datetime           (present in 2009-01 through 2016-06, has data)
    - do_datetime           (present in 2009-01 through 2016-06, has data)
    - pickup_latitude       (present in 2009-01 through 2016-06, has data)
    - pickup_longitude      (present in 2009-01 through 2016-06, has data)

  Unacknowledged lossy casts:
    - passenger_count       DOUBLE -> BIGINT
                            (12,384 non-integer values across 84 files would truncate)

  Options for each unresolved item:
    * add to `renames:` if the data moved to another column
    * add to `lossy_casts:` with ack_date (ack_by / reason optional) if the cast is acceptable
    * add to `acknowledged_data_loss:` with ack_date (ack_by / reason optional) if the data has no destination

  Nothing was written. Re-run after updating yellow.yaml.

green:  ok, nothing to normalize (all outputs present)
fhv:    ok, 3 files normalized
fhvhv:  ok, 12 files normalized

1 data type had errors. Exit code 1.
```

### Exit codes

- `0` — all types processed cleanly (may include no-ops due to idempotency skips).
- `1` — one or more data types had unresolved mapping items. Nothing written for those types.
- `2` — transient/system errors (YAML syntax invalid, DuckDB failure, disk full).

### Incremental re-runs

- Existing output files are skipped (idempotent).
- To force re-normalize (e.g., after editing a rename): `rm -rf raw-normalized/<type>/` and re-run. No auto-invalidation based on mapping mtime — magic like that is wrong more often than right.

## Implementation approach (DuckDB)

### Metadata-only checks (fast — no data scan)

All use `parquet_metadata('file.parquet')` and `parquet_schema('file.parquet')`, reading only the footer:

| Check | Metadata used | Outcome |
|---|---|---|
| Column always null → safe auto-drop | `null_count` summed across row groups vs `num_rows` | All-null → auto-drop |
| Numeric range fits smaller type | per-row-group `min` / `max` | Range fits → auto-cast; range exceeds → lossy_cast ack required |
| VARCHAR max length fits smaller VARCHAR | per-row-group `max` (string) | Fits → auto-cast; exceeds → lossy_cast ack required |
| Presence periods for the error report | filename-derived + `null_count` per file | Purely metadata |

### Value scans (only when metadata cannot answer — precision)

- `DOUBLE → BIGINT` truncation:
  ```sql
  SELECT count(*) FILTER (WHERE val IS NOT NULL AND val != CAST(val AS BIGINT))
  FROM read_parquet('...')
  ```
  DuckDB scans the parquet column vectorized. Only runs after the range check has passed — otherwise range failure alone is sufficient reason to demand an ack.
- `DECIMAL` scale reduction: analogous.

**These precision-check scans always read the full column, regardless of the `--sample` flag.** Sampling could return a false negative (no fractional values seen in the sample even though the file contains some), which would silently discard user data. Correctness beats speed here.

For TLC data, most columns settle from metadata alone; the precision-scan path fires only when a column crosses a range boundary between historical and target types.

### Transform SQL (per file, generated by planner)

```sql
COPY (
  SELECT
    tpep_pickup_datetime,                                    -- passthrough
    pu_datetime AS tpep_pickup_datetime,                     -- rename
    CAST(passenger_count AS BIGINT) AS passenger_count,      -- cast
    NULL::VARCHAR AS airport_fee                             -- added since; null in old data
    -- pickup_latitude, pickup_longitude: dropped (auto or acknowledged)
  FROM read_parquet('raw/yellow/2013/yellow_tripdata_2013-01.parquet')
) TO 'raw-normalized/yellow/2013/yellow_tripdata_2013-01.tmp.parquet'
  (FORMAT PARQUET, COMPRESSION ZSTD);
```

Then `os.rename(tmp, final)` — atomic on POSIX. An interrupted run leaves no half-written file.

Column order in output matches the target file's schema exactly.

### Bootstrap analysis

`bootstrap.py` imports schema-drift's Python API directly (no shelling out, no parsing text):

```python
from schema_drift.analyze import analyze_data_type
from schema_drift.renames import detect_renames_by_data
```

Flow:
1. Discover raw files under `raw/<type>/`.
2. Call `analyze_data_type()` with `generic_mode=True` to get transitions + rename candidates.
3. Union all column names across all historical files (via `parquet_schema()` per file).
4. Per-column: null-count aggregation across files (via `parquet_metadata()`).
5. Compare each historical column vs target file's schema; classify as passthrough / auto-safe-cast / lossy-cast / drop-candidate / rename-candidate.
6. Emit YAML scaffold with SUGGESTED lines from rename candidates (annotated with confidence + data-verified boolean) and TODO placeholders for lossy_casts + acknowledged_data_loss items.

Bootstrap is a one-shot per data type.

**Runtime scaling with `--sample`:**

- Metadata checks (schema, null counts, min/max) run per file in constant time regardless of file size — footer only, no data scan. Same cost at any `--sample` value.
- Rename-detection data verification (schema-drift's `detect_renames_by_data()`) samples rows per candidate pair. At the default `--sample 100%`, this becomes a full column scan per pair — DuckDB-vectorized, but scales with row count. Yellow bootstrap (~200 files, ~50M rows per recent file, roughly 10–20 candidate pairs at transition points) at 100% takes several minutes. At `--sample 10%` it drops to under a minute. At `--sample 5000` it's near-instant, at the cost of noisier rename suggestions.

The `--sample` flag is passed through to `detect_renames_by_data(sample_size=...)`. Values ending in `%` become DuckDB's `USING SAMPLE X PERCENT`; bare integers become `USING SAMPLE N ROWS`.

## Testing strategy

Test tree matches the monorepo pattern:

```
tests/taxi_normalize/
  conftest.py            # fixture: builds synthetic parquet families via DuckDB
  test_mapping.py        # YAML loading, validation, missing-field errors
  test_planner.py        # (raw schema, target schema, mapping) -> correct plan
  test_data_check.py     # metadata-only checks (all-null detection, range fit)
  test_executor.py       # runs the transform SQL, verifies output parquet
  test_bootstrap.py      # synthetic family -> verifies emitted YAML shape
  test_cli_smoke.py      # `normalize --help`, missing-type error paths, end-to-end
```

**No dependency on real `raw/` data.** `conftest.py` builds tiny synthetic 3-era parquet families programmatically via DuckDB (era 1 = 2009-ish old schema, era 2 = 2015-ish mid-drift, era 3 = 2024-ish target).

**Planner test cases to cover explicitly:**

- Pure passthrough (raw == target).
- Simple rename applied.
- Rename + type cast combined.
- Safe auto-drop (all-null column, not in target).
- Unsafe drop with data → planner returns "unresolved".
- Safe widening auto-cast (INT → BIGINT).
- Lossy cast without any ack → planner returns "unresolved".
- Lossy cast with only `ack_date` (no `ack_by`, no `reason`) → applied (minimum viable ack).
- Lossy cast entry with `ack_by` but no `ack_date` → planner returns "unresolved" (ack_date is required).
- `acknowledged_data_loss` entry with only `ack_date` → drop applied.

**Data-check tests** verify metadata-only paths actually take the metadata-only path (no data scan issued for range checks); only precision cases (DOUBLE → BIGINT truncation with fractional values) trigger a value scan.

**Bootstrap tests**:

- Empty mapping input + synthetic family → emitted YAML has correct `target:`, correct SUGGESTED renames from schema-drift, correct set of TODO items, no extra keys.
- Refuses to overwrite an existing YAML.
- `--sample 5000` passes sample_size=5000 through to `detect_renames_by_data()`.
- `--sample 10%` translates to DuckDB `USING SAMPLE 10 PERCENT` in the underlying query.
- No `--sample` flag → full scan (default behavior, no sampling clause).

**CLI smoke tests** (subprocess-based, minimal):

- `normalize --help` exits 0 with usage text.
- `normalize nonexistent-type` exits non-zero with helpful message.
- End-to-end: bootstrap → hand-edit YAML fixture → normalize → verify output parquet schema matches target.

**Target test count for launch:** roughly 20–25 tests. Proportional to expected ~600–800 LOC.

## Success criteria

- `normalize bootstrap yellow` runs against the checked-in `raw/yellow/` and produces a `normalize/mappings/yellow.yaml` scaffold with SUGGESTED renames from schema-drift's detectors and TODO placeholders for data-loss items.
- After a human completes the YAML, `normalize yellow` writes `raw-normalized/yellow/**/*.parquet` where every file's schema matches the target file.
- `normalize` with no argument runs all four data types.
- Re-running `normalize yellow` is a no-op if `raw-normalized/yellow/` is already populated.
- Attempting to run `normalize <type>` with an incomplete YAML fails with a consolidated error report; nothing is written.
- `uv run --extra test pytest tests/taxi_normalize/` passes with ≥20 tests.

## Out of scope

- The SQL Server loader (separate sub-project).
- The orchestrator (separate sub-project).
- CI/CD pipelines (separate sub-project).
- Auto-invalidation of normalized outputs when the mapping YAML changes. Users delete `raw-normalized/<type>/` to force re-run.
- Alternative target schemas (era pinning is fine, but no "normalize to 2015 schema").
- Producing a report of what changed per file (beyond the per-file progress line and the final summary).
