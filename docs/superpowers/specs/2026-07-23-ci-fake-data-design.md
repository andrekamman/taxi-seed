# CI / fake-data — design spec (2026-07-23)

**Sub-project:** the four-part taxi expansion's last piece (normalizer ✅, docs ✅, loader ✅,
orchestrator ✅, **CI/fake-data ← this**).

**Goal:** make every push validate the loader + orchestrator + normalize integration against a
**real SQL Server**, without the ~100 GB TLC download — by (A) adding a CI job with a SQL Server
service container that runs the existing integration tests, and (B) generating tiny **fake data**
shaped like the real TLC schema and driving the whole pipeline (generate → normalize → load →
assert) as a CI smoke that exercises the **actual committed mappings**.

**Scope (v1): A + B.** k6-in-CI (C) and dev/test/prod promotion (D) are deferred to later
follow-ups and are out of scope here.

---

## Background / current state (facts)

- **CI** (`.github/workflows/ci.yml`): two jobs — `test` (pytest on py3.12/3.13 via `uv`;
  `uv sync --extra test` then `uv run --extra test pytest -q`) and `docs` (mkdocs `gh-deploy` on
  push to `main`, `needs: test`). **No SQL Server, no integration coverage today.**
- **Loader integration tests** already exist: `tests/taxi_loader/test_load_integration.py` (7 tests),
  gated by `pytestmark = pytest.mark.skipif(not os.environ.get("MSSQL_PASSWORD"), …)`. They
  provision/attach/load/verify against `mcr.microsoft.com/mssql/server:2022-latest`. **These run
  today only when `MSSQL_PASSWORD` is set — i.e. never in CI.**
- **Reusable helpers** in that test file (to be shared with the new e2e test): `cfg()` fixture
  (builds `ConnConfig` with a unique schema `"t"+uuid4().hex[:8]`, reads `MSSQL_HOST`/`MSSQL_PORT`/
  `MSSQL_USER` with defaults `localhost`/`1433`/`sa`, `database="taxi"`, `password=os.environ["MSSQL_PASSWORD"]`);
  `prepared(cfg)`; `attached(cfg)` (`@contextmanager`, short-lived read connection); `_count(cfg, table)`
  → `load.count_year_table`; `_read_manifest(cfg)` → `manifest.read_manifest`; `_detach_close(conn)`.
- **DuckDB `mssql` attach is process-global** — only one connection may hold the ATTACH at a time.
  Tests provision then detach; each verification opens a short-lived attached connection. The e2e
  test MUST respect this (drive the pipeline, then open short-lived reads to assert).
- **Loader** (`loader/src/taxi_loader/cli.py`, entry point `taxi-load`): argparse flags — positional
  `data_type` (four types; omit = all), `--host` (localhost), `--port` (1433), `--database` (taxi),
  `--schema` (dbo), `--user` (sa), `--input-dir` (default `raw-normalized`; reads
  `<input-dir>/<type>/<year>/*.parquet`, parsing `(\d{4})-(\d{2})` from the filename), `--flush-rows`,
  `--full-refresh`, `--dry-run`. Password only from env `MSSQL_PASSWORD`. Year-table name =
  `f"{data_type}_{year}"`. Connection provisioning (`connection.py`): `INSTALL mssql FROM community;
  LOAD mssql;` and **hard-asserts `EXPECTED_MSSQL_EXT_VERSION = "7e57d24"`**; `build_conn_string`
  uses `Encrypt=yes;TrustServerCertificate=yes`; `ensure_database` creates `taxi` if absent;
  `attach_target` attaches as `mssql` and creates the schema if `!= "dbo"`.
- **Orchestrator** (`orchestrator/src/taxi_orchestrate/cli.py`, entry point `taxi-run`): argparse
  flags — positional `data_type`, `--recent`, **`--skip-download`**, `--download-only`, **`--load`**,
  `--sample`, **`--data-dir`** ("working root holding raw/ + raw-normalized/; default: repo root"),
  `--dry-run`, plus load-forwarded `--host/--port/--database/--schema/--user/--flush-rows/--full-refresh`.
  `root = Path(args.data_dir).resolve() if args.data_dir else find_repo_root(cwd)`; **every stage runs
  with `cwd=root`**. Stage order (`_planned_stages`): download (unless `--skip-download`) → normalize →
  load (if `--load`). `--load` requires `MSSQL_PASSWORD` in env (forwarded to the load subprocess).
  Load stage reads `INPUT_DIR = "raw-normalized"`.
- **Normalize** (`normalize/src/taxi_normalize/cli.py`, entry point **`normalize`** — not
  `taxi-normalize`): argparse — positional `data_type` (omit = all four), `--sample` (default
  `"100%"`). **No `--input-dir`/`--mappings-dir`/`--data-dir` flags.** Paths are resolved relative to
  CWD: raw input `raw/<type>/`, mapping `normalize/mappings/<type>.yaml`, output `raw-normalized/<type>/`.
  Target resolved via `raw_dir.rglob(mapping.target)`; all inputs via `sorted(raw_dir.rglob("*.parquet"))`.
  Exit codes: 0 done, 1 needs-review (unresolved items), 2 error, 3 first-run scaffold generated.
- **Mapping format** (`normalize/src/taxi_normalize/mapping.py`), allowed top-level keys:
  `target` (required — a target parquet **filename** whose schema every raw file is rewritten to
  conform to), `renames` (`{old: new}`), `lossy_casts` (`{col: {ack_date, from, to, ack_by?, reason?}}`),
  `acknowledged_data_loss` (`{col: {ack_date, ack_by?, reason?}}` — dropped columns), `value_maps`
  (`{col: {src: tgt}}` **or** `{col: {map: {…}, on_unmapped: 'error'|'null'}}`). Committed targets:
  `yellow→yellow_tripdata_2026-05.parquet`, `green→green_tripdata_2026-05.parquet`,
  `fhv→fhv_tripdata_2026-04.parquet`, `fhvhv→fhvhv_tripdata_2026-05.parquet`.
- **A raw input file processes cleanly** iff every non-conforming column is covered by `renames`,
  `lossy_casts`, `acknowledged_data_loss`, or `value_maps`; otherwise normalize exits 1. The pinned
  `target:` file itself must exist under `raw/<type>/` to supply the reference schema.
- **DuckDB→SQL Server type map** (`shared/src/taxi_shared/type_mapping.py`): `BIGINT→BIGINT`,
  `DOUBLE→FLOAT`, `VARCHAR/TEXT/STRING→NVARCHAR(MAX)`, `TIMESTAMP*→DATETIME2`, `DECIMAL(p,s)`
  passthrough; unmapped types raise `TypeMappingError`. Generated parquet must use only mapped types.
- **`pyproject.toml`**: `requires-python = ">=3.12"`; core deps `duckdb>=1.4.4`, `pyyaml>=6.0`;
  `[project.optional-dependencies].test = ["pytest>=8.0"]`; wheel packages include
  `loader/src/taxi_loader`, `normalize/src/taxi_normalize`, `orchestrator/src/taxi_orchestrate`,
  `shared/src/taxi_shared`.

---

## A — CI SQL Server integration job

Add **one** job to `.github/workflows/ci.yml`; leave `test` and `docs` unchanged.

- **`integration`**, `runs-on: ubuntu-latest` (x86_64 — supported by the DuckDB `mssql` extension),
  **single Python** (3.13). The py3.12/3.13 matrix stays on the unit `test` job; the integration job
  runs one version to save time.
- **Service container:**
  ```yaml
  services:
    mssql:
      image: mcr.microsoft.com/mssql/server:2022-latest
      env:
        ACCEPT_EULA: "Y"
        MSSQL_SA_PASSWORD: "Str0ng_Passw0rd!"
      ports:
        - "1433:1433"
  ```
- **Readiness = explicit wait step, not a container healthcheck.** A bounded poll loop (e.g. up to
  ~60s, ~2s interval) that succeeds when a `SELECT 1` connects — using the `sqlcmd` shipped in the
  `mssql-tools` image, or a Python/DuckDB `mssql` attempt, or a raw TCP check on 1433. Fail the job
  with a clear message on timeout. (SQL Server's own container healthcheck is the historically flaky
  part; an explicit step is more reliable and debuggable.)
- **Run step:**
  ```
  MSSQL_PASSWORD=Str0ng_Passw0rd! uv run --extra test pytest \
    tests/taxi_loader/test_load_integration.py \
    tests/e2e/test_pipeline_e2e.py
  ```
  With `MSSQL_HOST=localhost` / `MSSQL_PORT=1433` / `MSSQL_USER=sa` (defaults already match the mapped
  service port). This runs the 7 existing loader integration tests **and** the new e2e smoke.
- Steps otherwise mirror the `test` job: `actions/checkout@v4`, `astral-sh/setup-uv@v3`
  (`enable-cache: true`), `uv python install 3.13`, `uv sync --extra test`.

**Risk noted:** the loader pins `EXPECTED_MSSQL_EXT_VERSION = "7e57d24"`. CI installs the `mssql`
community extension against the same pinned `duckdb>=1.4.4`, so it should resolve identically to local;
if it drifts, the existing integration tests fail loudly (this is the same code path as a local run).

---

## B — fake-data generator + end-to-end smoke

### B.1 Schema-constants module (code-driven — no committed parquet)

A small committed module (e.g. `tests/e2e/schemas.py` or a `tests/e2e/fakedata/` package) declares,
**per type**, two things derived by reading each committed `normalize/mappings/<type>.yaml`:

1. **Target schema** — the canonical (post-rename, final-type) columns + DuckDB types, matching the
   mapping's pinned `target:` file. Used to write the pinned target parquet (a few rows) so normalize
   has its reference schema.
2. **Raw-historical schema(s)** — at least one **drift-era** raw file per type whose columns are the
   *pre-rename* historical names and types, deliberately shaped to make **every** mapping mechanism
   fire:
   - a **rename** (a column under its historical name, e.g. `vendor_id`, `pickup_datetime`),
   - a **lossy_cast** (a column with the `from` type, e.g. `passenger_count`/`RatecodeID` as DOUBLE),
   - a **value_map** (a column holding a source code string, e.g. `payment_type`, `store_and_fwd_flag`,
     covering both the flat and the `map:`/`on_unmapped:` forms),
   - an **acknowledged_data_loss** drop (an extra column the mapping drops, e.g. lat/long,
     `__index_level_0__`).

Only DuckDB types in the `type_mapping` table (BIGINT/DOUBLE/VARCHAR/TIMESTAMP/DECIMAL) are used, so
the loaded parquet maps cleanly to SQL Server. Row counts are tiny (single digits per file) and can
span ≥2 months/years so `<type>_<year>` table assertions and the manifest are meaningful.

### B.2 Generator

A helper (mirroring `tests/taxi_loader/conftest.py::write_month`, which uses DuckDB
`COPY (SELECT … FROM range(n)) TO '<path>' (FORMAT PARQUET)`) writes, per type, into an isolated
temp `workroot`:
- the pinned **target** file `workroot/raw/<type>/…/<mapping.target>` (canonical schema), and
- the **raw-historical** file(s) `workroot/raw/<type>/<year>/<type>_tripdata_<year>-<mm>.parquet`
  (filename **must** contain `YYYY-MM`).

### B.3 End-to-end test — `tests/e2e/test_pipeline_e2e.py`

Gated by the **same** `skipif(not os.environ.get("MSSQL_PASSWORD"))` as the loader integration tests,
so local `pytest` stays green without SQL Server. For **all four** types (parametrized):

1. Create isolated temp `workroot`.
2. **Copy** the repo's `normalize/mappings/` → `workroot/normalize/mappings/` (so normalize, which
   resolves mappings relative to CWD, uses the **real committed** mappings without polluting the repo's
   gitignored `raw/`). **No production-code change is required** — this is the reconciliation mechanism.
3. Generate raw + target files into `workroot/raw/<type>/` (B.2).
4. Drive the whole pipeline: `taxi-run <type> --skip-download --load --data-dir workroot
   --schema <unique-schema> --host … --port … --user …` (invoke `taxi_orchestrate.cli.main([...])`
   in-process, or subprocess the console script). This chains normalize → load with `cwd=workroot`.
5. Assert via the shared helpers: `load.count_year_table` (table `<type>_<year>`) equals the generated
   row counts, and `manifest.read_manifest` reflects the loaded (year, month, rows) tuples — using
   **short-lived attached reads**, respecting the single-process `mssql`-attach constraint. Use a
   **unique schema per test** (as the existing `cfg()` fixture does) so parametrized types don't
   collide.

**Shared helpers:** factor the reusable SQL-Server-side helpers so both
`tests/taxi_loader/test_load_integration.py` and `tests/e2e/test_pipeline_e2e.py` use them. Prefer a
shared `conftest.py`/module over duplication; keep test-file basenames unique across components
(pytest default import mode) — the e2e file is `test_pipeline_e2e.py`.

### B.4 Local parity

A small runnable entry (a `uv` script target or a `make`/shell wrapper, e.g. `scripts/e2e-smoke.sh`)
that sets `MSSQL_PASSWORD` and runs the same `pytest tests/e2e/test_pipeline_e2e.py` against a local
Docker SQL Server — documented alongside the existing manual bring-up in the integration test's
docstring, so CI and local runs are identical.

---

## Testing strategy

- The e2e smoke **is** the new integration coverage; it doubles as regression coverage for the whole
  `taxi_shared` → loader DDL path on a real server and for the committed mappings staying loadable.
- Local `pytest -q` (no `MSSQL_PASSWORD`) stays green: today's 189 passed / 7 skipped becomes 189
  passed / (7 + N) skipped, where N is the new e2e cases.
- With `MSSQL_PASSWORD` set (local Docker or CI), the 7 loader integration tests + the new e2e cases
  execute and assert real row counts.

## Error handling

- CI readiness wait fails fast with a clear message on timeout (A).
- Normalize non-zero exits (1 needs-review, 2 error, 3 scaffold) must fail the e2e test loudly — a
  drift file that doesn't fire the intended mapping mechanism should surface as a test failure, not a
  silent pass.
- The e2e test cleans up its schema/attach on exit (mirroring the existing `prepared`/`_detach_close`
  teardown) so parametrized runs and reruns are isolated.

## Out of scope (deferred)

- **C** — building/running xk6 load tests in CI.
- **D** — dev/test/prod promotion + schema-diff gates.
- Any new production CLI flags (e.g. a `normalize --mappings-dir`): the mapping-copy mechanism avoids
  needing them; revisit only if a future piece requires it.

## Open items to resolve during implementation (not blocking)

- Exact readiness mechanism (sqlcmd vs Python/DuckDB attempt vs TCP) — pick the simplest reliable one.
- Whether to invoke `taxi-run` in-process (`cli.main`) or via subprocess — in-process is faster and
  gives direct exceptions; subprocess is closer to real usage. Prefer in-process unless the `cwd=root`
  handling forces subprocess.
- Precise per-type drift-file column sets — authored from each committed mapping during implementation.
