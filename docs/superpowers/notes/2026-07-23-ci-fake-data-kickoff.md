# CI / fake-data sub-project — KICKOFF (resume-from-cold primer)

**Purpose:** the four-part expansion's last piece (normalizer ✅, docs ✅, loader ✅, orchestrator ✅,
**CI/fake-data ← this**). This note lets a fresh session continue without re-discovery.

## How to resume
1. Read this file, then skim `docs/architecture.md` §"What's not built yet" and the two most
   relevant existing files: `.github/workflows/ci.yml` and `tests/taxi_loader/test_load_integration.py`.
2. We are mid-**brainstorming** (superpowers). Next steps: finish the design → get user approval →
   write spec (`docs/superpowers/specs/2026-07-23-ci-fake-data-design.md`) → writing-plans →
   subagent-driven-development. (Same flow used for loader / orchestrator / yellow-fidelity.)
3. Decisions already made are below under **Decisions locked**. Open questions under **To resolve**.

## What "CI/fake-data" means
Run the whole pipeline end-to-end in CI **without the ~100 GB TLC download**, by generating small
**fake data** and loading it into a **real SQL Server service container**, so every push validates
loader + orchestrator + normalize integration (today those integration tests SKIP in CI).

## Current state (facts)
- **CI** (`.github/workflows/ci.yml`): two jobs — `test` (pytest on py3.12/3.13 via `uv`) and `docs`
  (mkdocs gh-deploy on push to main). **No SQL Server; no integration coverage.**
- **Integration tests already exist and gate on env**: `tests/taxi_loader/test_load_integration.py`
  (7 tests) `skipif(not os.environ.get("MSSQL_PASSWORD"))`. They provision/attach/load/verify against
  `mcr.microsoft.com/mssql/server:2022-latest`. Locally: `MSSQL_PASSWORD=… uv run --extra test pytest …`.
  Note the `mssql` extension ATTACH context is **process-global** — the suite provisions then detaches;
  `taxi-run --load` cleans up its attach on exit.
- **Loader** CLI `taxi-load` (env `MSSQL_PASSWORD`, `--host/--database taxi/--schema dbo/--user sa/--input-dir raw-normalized`). Conn string already sets `Encrypt=yes;TrustServerCertificate=yes`.
- **Orchestrator** CLI `taxi-run [TYPE] --skip-download --load …` chains normalize→load; exit `2>1>0`.
- **Curation** CLI `taxi-curate-mappings [TYPE]` auto-builds `normalize/mappings/<type>.yaml`.
- **Committed mappings** exist for all four types (`normalize/mappings/*.yaml`) + `CURATION-REPORT.md`.
  They pin a `target:` filename (e.g. `yellow_tripdata_2026-05.parquet`) and expect raw files with the
  historical TLC schema. `raw/` and `raw-normalized/` are gitignored.
- **k6-loadtest** has a "synthetic mode" (`k6-loadtest/src/k6_loadtest/{config,preprocess}.py`) — but
  that generates **k6 test traffic** (INSERT/UPDATE/DELETE), NOT pipeline parquet. Not the fake-data
  generator we need for B; relevant only to piece C.
- **xk6 binary**: `k6-loadtest/build_k6.sh` builds a custom k6 (Go/xk6-sql). Needed only for piece C.
- Full suite today: 189 passed / 7 skipped (the 7 = loader integration, skipped w/o SQL Server).

## Decomposition
- **A. CI SQL Server job** — add a CI job with a `mssql/server:2022-latest` **service container** +
  `MSSQL_PASSWORD`, running the existing integration tests so they actually execute on push. Small.
- **B. Fake-data end-to-end** — generate tiny synthetic `raw/` parquet (download-free) and run
  generate→normalize→load→assert as a whole-pipeline CI smoke. Medium.
- **C. k6 run in CI** — build xk6 + run a load test against the loaded DB. Larger/flakier. **Deferred.**
- **D. dev/test/prod promotion + schema-diff gates** — separate, large; own project. **Deferred.**

## Decisions locked
- **v1 = A + B** (recommended; C and D deferred to later follow-ups). *(User picked the fake-data shape
  and asked to clear context before final scope sign-off — reconfirm A+B vs A+B+C at resume, but A+B is
  the working assumption.)*
- **Fake-data shape = mimic the real TLC schema + committed mappings** (user's explicit choice). The
  generator produces synthetic raw parquet shaped like real yellow/green/fhv/fhvhv (matching each
  committed mapping's target schema, plus at least one drift-era file to exercise renames/casts/
  value_maps/drops), so CI validates the **actual committed mappings** end-to-end — not a throwaway type.

## Design sketch (A + B), to refine at resume
### A — CI SQL Server job
Add a job to `ci.yml` (e.g. `integration`), `runs-on: ubuntu-latest`, with:
```yaml
    services:
      mssql:
        image: mcr.microsoft.com/mssql/server:2022-latest
        env: { ACCEPT_EULA: "Y", MSSQL_SA_PASSWORD: "Str0ng_Passw0rd!" }
        ports: [ "1433:1433" ]
        options: >-
          --health-cmd "..." --health-interval 10s --health-retries 10
```
(SQL Server's readiness/health check is fiddly — likely a `sqlcmd`/TCP wait step rather than a
container healthcheck; resolve at implementation.) Then `MSSQL_PASSWORD=Str0ng_Passw0rd! uv run
--extra test pytest tests/taxi_loader/test_load_integration.py` (+ the B e2e test). Runs on
ubuntu-latest x86_64, which the `mssql` DuckDB extension supports.

### B — fake-data end-to-end (mimic real schema)
Key challenge: the committed mappings pin a `target:` **filename** and expect the full historical
schema, but the real target parquet is gitignored — so CI has nothing to normalize. Options to pick
from at resume:
- **B1 (recommended to evaluate first):** commit a **tiny representative target parquet per type** as a
  CI fixture (a handful of rows, the real modern target columns/types) under e.g. `tests/e2e/fixtures/`
  (NOT gitignored — small). A generator perturbs it into 2-3 drift-era raw files (rename some columns to
  historical names, flip a type, drop/add a column, inject a `payment_type`/`vendor` string code) so
  normalize's renames/casts/value_maps/drops all fire. Reconcile the mapping's pinned `target:` name
  (either name the fixture to match, or have the e2e test use a CI-local copy of the mappings / pass an
  override).
- **B2:** bake the target schemas into a small committed module (`schemas.py`: `{type: {col: ddltype}}`)
  and generate everything (incl. the target) from it. Fully code-driven, no committed parquet.
- Either way, keep it **small** and put it behind the same `MSSQL_PASSWORD` skip gate so `pytest`
  stays green locally without SQL Server. Likely lives as `tests/e2e/test_pipeline_e2e.py` (generate →
  `taxi-run --skip-download --load` via subprocess, or call the stage `main()`s → assert SQL Server
  row counts via a short-lived `mssql` attach, mirroring `test_load_integration.py`'s helpers).

The real target schemas (columns) can be recovered by DESCRIBE-ing the local real data at
`/Users/andre/git/taxi/raw` or `raw/` during implementation to author the fixtures accurately. Modern
yellow target columns (from this session) e.g.: `VendorID, tpep_pickup_datetime, tpep_dropoff_datetime,
passenger_count, trip_distance, RatecodeID, store_and_fwd_flag, PULocationID, DOLocationID, payment_type,
fare_amount, extra, mta_tax, tip_amount, tolls_amount, improvement_surcharge, total_amount,
congestion_surcharge, Airport_fee, cbd_congestion_fee`.

## To resolve at resume (open questions)
1. **Confirm v1 scope**: A+B (recommended) vs A+B+C (add k6-in-CI). D (promotion) stays out.
2. **Fake-data representation**: B1 (committed tiny target parquet fixtures) vs B2 (schema constants).
   Reconcile the mappings' pinned `target:` filename with the CI fixtures (rename fixture to match, or
   CI-local mapping copies, or a `--target`/`--mappings-dir` override — note: normalize has no
   `--mappings-dir` today; loader has `--input-dir`, orchestrator has `--data-dir`).
3. **Which types in the smoke**: all four, or just yellow (richest: multi-source renames + value_maps)
   plus one simple type? Fewer = faster CI.
4. **SQL Server readiness in CI**: healthcheck vs an explicit wait-for-1433 step (the flaky part).
5. Whether to also run the smoke **locally** via a make/uv script for parity.

## Also worth a line in the eventual spec
- The e2e smoke doubles as regression coverage for the whole `taxi_shared`→loader DDL path on a real
  server, and for the committed mappings staying loadable.
- CI matrix already tests py3.12/3.13; the integration job can be a single Python version to save time.

## Status of the four-part expansion at this point
normalizer ✅ · docs ✅ · loader ✅ (Docker-validated locally) · orchestrator ✅ (+ value_maps,
multi-source renames, curated mappings) · **CI/fake-data — brainstorming, this note.**
`main` is at the yellow-fidelity merge; everything pushed to origin.
