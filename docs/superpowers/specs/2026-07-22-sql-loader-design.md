# SQL Server Loader Design

**Date:** 2026-07-22
**Status:** Implemented and merged to `main` (2026-07-23). Reconciled with the shipped
implementation — passages that changed during the build are marked **[as-built]**.
**Sub-project of:** the four-part expansion (normalizer ✅, docs ✅, **loader ✅**, orchestrator, CI/fake-data)

## Motivation

The normalizer produces `raw-normalized/<type>/<year>/*.parquet` where every file of a
data type conforms to one uniform schema. The loader is the last hop of the pipeline: it
bulk-loads those normalized parquet files into a SQL Server database so the data is
queryable with ordinary T-SQL and usable as a load-test target for the K6 component.

The loader is deliberately the narrowest useful thing: **one database, one table per year
per data type, DuckDB does the reading, the DuckDB `mssql` community extension does the
writing.** No pyodbc, no ODBC driver install, no `bcp` CLI, no CSV round-trip, no Arrow
batching in application code.

## The `mssql` DuckDB extension

Loading is done entirely inside a DuckDB session via the
[`mssql` community extension](https://duckdb.org/community_extensions/extensions/mssql)
([hugr-lab/mssql-extension](https://github.com/hugr-lab/mssql-extension), native TDS protocol).
**[as-built]** The community registry versions this extension by **commit hash**, not semver:
`INSTALL mssql FROM community` on DuckDB 1.4.4 installs `extension_version = "7e57d24"` (the spec
originally nominated `v0.2.1`, which the registry does not serve). Relevant capabilities, all
verified against the extension README and confirmed end-to-end against SQL Server 2022:

- **Install/load at runtime:** `INSTALL mssql FROM community; LOAD mssql;` — no external
  system dependency. This is what keeps the "clone and run" story intact.
- **Attach:** `ATTACH 'Server=host,1433;Database=taxi;User Id=sa;Password=…;Encrypt=yes;TrustServerCertificate=yes' AS mssql (TYPE mssql)`.
  **[as-built]** DuckDB does **not** accept bind parameters in `ATTACH`, so the connection
  string is interpolated as an escaped single-quoted literal (it is never logged). The extension's
  ATTACH "context" is **process-global** — it survives across DuckDB connections, so the CLI
  `DETACH`es and closes on exit to avoid a leaked context colliding with the next run in the
  same process. Credentials can also come from a DuckDB `SECRET` (unused here).
- **Bulk load via BCP:** `COPY (<query>) TO 'mssql://mssql/<schema>/<table>' (FORMAT 'bcp', …)`,
  using the native TDS `BulkLoadBCP` path (~1.2M rows/s benchmarked). Options:
  - `CREATE_TABLE` (bool, default true) — auto-create target if absent. **[as-built]** the loader
    always passes `CREATE_TABLE false` and builds the DDL explicitly (see *Table DDL* below).
  - `REPLACE` (bool, default false) — drop and recreate the table before load. **[as-built]**
    **not used** — the loader does its own explicit `DROP TABLE IF EXISTS` + `CREATE TABLE` for the
    truncate-and-reload path, so the year table keeps its deterministic `taxi_shared` column types
    instead of the extension's auto-DDL (which defaults strings to `NVARCHAR(MAX)`).
  - `FLUSH_ROWS` (bigint, default 100000) — rows per commit batch.
  - `TABLOCK` (bool, default false) — bulk-load table lock; the loader passes `TABLOCK true`.
- **Arbitrary SQL** for provisioning and reads. **[as-built]** confirmed function names:
  - `mssql_exec(catalog, sql)` — scalar; runs DDL/DML (CREATE DATABASE / CREATE SCHEMA /
    CREATE TABLE / INSERT / DELETE), returns affected-row count. `CREATE DATABASE` is wrapped as
    `IF DB_ID('taxi') IS NULL EXEC('CREATE DATABASE [taxi]')` against a `master` attach.
  - `mssql_scan(catalog, query)` — table function; runs a read query and returns rows. Used for
    all reads (manifest, per-year `COUNT(*)`, `OBJECT_ID` existence checks) because it runs live
    SQL and sidesteps the extension's metadata cache returning stale results within a session.
- **Platform matrix:** macOS ARM64 (primary dev; SQL Server 2022 image runs under amd64
  emulation), Linux x86_64 (matches the `ubuntu-latest` CI runner), Linux ARM64, Windows x64.
  Requires DuckDB ≥ 1.4.1; the repo pins `duckdb>=1.4.4`. ✅

**Risk acknowledgment:** the extension is marked *experimental* and its API may shift between
releases. Mitigation: the constant `EXPECTED_MSSQL_EXT_VERSION` (`connection.py`) is asserted at
startup against the installed `extension_version`; a mismatch is a hard error (exit 2). **[as-built]**
because the registry pins by commit hash (`7e57d24`), a rebuild changes the hash and trips the
assertion until the constant is deliberately bumped — noisier than a semver pin, but faithful to
"a version bump is a deliberate, tested change." Relaxing to a warning is a future option.

## Non-goals

- Any target other than SQL Server. One backend, no abstraction layer for "maybe Postgres later".
- Any table layout other than one-table-per-year-per-type. No single wide table, no partitioning scheme.
- Upserts / MERGE / CDC / incremental row-level updates. The unit of change is a **month file**
  (append) or a **year table** (truncate + reload). Never a single-row or single-month `DELETE`.
- Reading anything but `raw-normalized/`. The loader does not normalize; it consumes the
  normalizer's output. Loading un-normalized `raw/` is out of scope (schemas drift; tables wouldn't line up).
- Orchestration (download → normalize → load in one command) — that is the orchestrator sub-project.
- Committing the per-type mapping YAMLs that make the pipeline run unattended — that belongs to
  the orchestrator/mappings sub-project.

## Component layout

New component `loader/`, matching the monorepo pattern:

```
loader/
├── README.md                      # one-paragraph pointer to the docs guide
└── src/taxi_loader/
    ├── __init__.py
    ├── cli.py          # entry point `taxi-load`
    ├── connection.py   # INSTALL/LOAD mssql, ensure `taxi` DB + schema, ATTACH
    ├── manifest.py     # the _load_manifest bookkeeping table: create / read / write
    ├── reconcile.py    # pure logic: per (type, year) -> skip | append | truncate+reload
    └── load.py         # emits + runs the COPY(bcp) and CREATE TABLE statements
```

**Tests:** `tests/taxi_loader/` (mirrors `tests/{taxi_normalize,schema_drift,taxi_shared,…}/`).

**`pyproject.toml` additions:**
- Add `loader/src/taxi_loader` to `[tool.hatch.build.targets.wheel] packages`.
- Add `taxi-load = "taxi_loader.cli:main"` to `[project.scripts]`.
- **No new runtime dependency.** `duckdb` is already a dependency; the `mssql` extension is
  installed at runtime via `INSTALL … FROM community`.

## CLI

```
taxi-load [TYPE]                      # TYPE optional; omit = all four types
  --host HOST        --port 1433
  --database taxi    --schema dbo     # both configurable; these are the defaults
  --user sa
  --input-dir raw-normalized          # reads <input-dir>/<type>/<year>/*.parquet
  --flush-rows 100000                 # BCP commit batch size
  --full-refresh                      # force truncate+reload of every year
  --dry-run                           # print the reconciliation plan, touch nothing
```

- `TYPE` is one of `yellow`, `green`, `fhv`, `fhvhv`; omitting it processes all four.
  **[as-built]** the positional arg is restricted with `choices=DATA_TYPES`, so a typo
  (`taxi-load yello`) is a usage error (exit 2), not a silent no-op — and `data_type` never
  reaches a SQL identifier position unvalidated.
- **Password comes from the `MSSQL_PASSWORD` environment variable only** (matches the K6
  component's convention). It is never accepted on the command line and never logged.
- `--dry-run` prints, per `(type, year)`, the decided action (skip / append which months /
  truncate+reload) and exits 0. **[as-built]** it is **fully read-only**: it performs no
  provisioning (no CREATE DATABASE/SCHEMA/TABLE) and no COPY/manifest writes. It attaches
  read-only and tolerates an absent database or manifest table by treating that year as fresh, so
  the plan is still accurate against a virgin server.

## Database, schema, and table naming

- **Database:** `taxi` by default (`--database`). The loader **creates it if absent**
  (attach `master`, `CREATE DATABASE taxi` if `DB_ID('taxi') IS NULL`, detach, attach `taxi`).
- **Schema:** `dbo` by default (`--schema`). If a non-`dbo` schema is given and missing, the
  loader creates it.
- **Tables:** one per `(type, year)`, named `<schema>.<type>_<year>` — e.g. `dbo.yellow_2026`,
  `dbo.green_2015`. Years are derived from the `raw-normalized/<type>/<year>/` directory layout.

## Table DDL — deterministic, reusing `taxi_shared`

Normalization guarantees every file of a type shares one schema, so **all year-tables for a
type have identical columns**. The loader therefore does not rely on the extension's
auto-`CREATE TABLE` (which currently defaults strings to `NVARCHAR(MAX)`, which is not
indexable and wastes space). Instead:

1. `DESCRIBE SELECT * FROM read_parquet('<one file of the type>')` to get column names + DuckDB types.
2. Map each type with `taxi_shared.type_mapping.map_duckdb_to_mssql`.
3. Build `CREATE TABLE <schema>.<type>_<year> (…)` with
   `taxi_shared.sql_generator.generate_create_table_sql`.
4. Execute the DDL through the extension, then bulk-load with `COPY … (FORMAT 'bcp', CREATE_TABLE false)`.

This honors the intent to reuse `taxi_shared`, gives deterministic and bounded column types,
and keeps column order aligned with the normalized parquet. (`TypeMappingError` from
`taxi_shared` surfaces as a config-level failure — exit code 2 — naming the offending column.)

## Idempotency — per-month reconciliation via a manifest

### The manifest table

A single bookkeeping table `<schema>._load_manifest`, one row per loaded month:

| Column        | Type          | Notes                                  |
|---------------|---------------|----------------------------------------|
| `data_type`   | `NVARCHAR(16)` | `yellow` / `green` / `fhv` / `fhvhv`. **[as-built]** bounded (not `NVARCHAR(MAX)`) so it can sit in the PK. |
| `year`        | `INT`         |                                        |
| `month`       | `INT`         | 1–12                                   |
| `source_file` | `NVARCHAR(400)` | path of the loaded parquet file      |
| `row_count`   | `BIGINT`      | rows actually loaded for this month    |
| `loaded_at`   | `DATETIME2`   | write time (`SYSUTCDATETIME()`)        |

Primary key `(data_type, year, month)`. **[as-built]** the column types are a hand-chosen ordered
dict (not derived from `map_duckdb_to_mssql`, which maps `VARCHAR → NVARCHAR(MAX)` and so cannot
sit in a PK) fed to `taxi_shared.generate_create_table_sql`; the PK is spliced in as an inline
`CONSTRAINT … PRIMARY KEY (data_type, year, month)` so the whole table is created by a **single
atomic statement** (a CREATE-then-ALTER pair could leave a permanently PK-less table if the ALTER
failed). Created on first run if absent. **[as-built]** it is read into DuckDB via
`mssql_scan('mssql', 'SELECT year, month, row_count FROM <schema>._load_manifest WHERE data_type = …')`
(live SQL, cache-safe), not a catalog `SELECT * FROM mssql.<schema>._load_manifest`.

**Why a manifest table and not a lineage column in the data:** the data tables stay a pure
mirror of the normalized schema — nothing extra injected into `yellow_2026` — which matches
the normalizer's "output matches the target schema exactly" principle. All bookkeeping lives
in one out-of-band table.

### Durability model — batched commits, drop-the-year on partial failure

Bulk load uses BCP with **batched commits** (`FLUSH_ROWS`, default 100k, `--flush-rows`) and
`TABLOCK`. There is **no whole-file transaction**. If a load dies mid-file, the committed
batches remain in the table; recovery is a year rebuild, not a rollback. This is a deliberate
choice: whole-file transactions over multi-GB loads are costly, and the truncate+reload path
already exists for exactly this case.

Two invariants make batched commits safe:

1. **A month's manifest row is written only after that month's `COPY` fully succeeds**, and
   records the actual loaded row count.
2. **Reconciliation runs an integrity check first, per `(type, year)`:**

   > `actual COUNT(*) of <schema>.<type>_<year>`  vs  `SUM(manifest.row_count)` for that year.
   > **Mismatch → truncate the whole year and reload.**

   Because this check *gates* the per-month decision, a partially-loaded month (rows committed,
   no manifest row) makes the actual count exceed the manifest sum → the year is rebuilt →
   the partial month is **never** mistaken for "missing" and re-appended. No duplicates, no
   `DELETE`s.

### Per-`(type, year)` decision table

Evaluated after the integrity check passes (counts agree). Month row counts come from the
parquet **footer metadata** (`parquet_metadata` / `parquet_file_metadata`), not a data scan.

| Situation                                                        | Action                                          |
|------------------------------------------------------------------|-------------------------------------------------|
| Integrity check failed (table count ≠ manifest sum)              | **truncate + reload year**                      |
| Table missing but manifest has rows for the year (inconsistency) | **truncate + reload year**                      |
| Month file on disk with no manifest row                          | **append** that month (`COPY … CREATE_TABLE false`) |
| Month present, manifest `row_count` == source metadata count     | **skip**                                        |
| Month present, manifest `row_count` ≠ source count (file changed)| **truncate + reload year**                      |
| Manifest month no longer present on disk                         | **truncate + reload year**                      |
| `--full-refresh` set                                             | **truncate + reload year** (unconditional)      |

- **Append** = ensure the year table exists (fresh year → explicit `CREATE TABLE` first), then per
  month `COPY (SELECT * FROM read_parquet(['<month file>'])) TO 'mssql://mssql/<schema>/<type>_<year>' (FORMAT 'bcp', CREATE_TABLE false, FLUSH_ROWS …, TABLOCK true)`,
  and insert that month's manifest row **only after its COPY succeeds**.
- **Truncate + reload** **[as-built, no `REPLACE`]** = `DROP TABLE IF EXISTS <schema>.<type>_<year>`
  → explicit `CREATE TABLE` (from `taxi_shared`) → delete the year's manifest rows →
  `COPY (SELECT * FROM read_parquet(['<all month files of the year>'])) TO '…' (FORMAT 'bcp', CREATE_TABLE false, FLUSH_ROWS …, TABLOCK true)`
  → rewrite the manifest rows for that year. This ordering (drop → create → clear-manifest → copy →
  write-manifest) makes any mid-load crash self-heal on the next run via the integrity check.

This is exactly the requested behavior: *a month not there → add it; there and complete →
skip; there and incomplete → truncate the whole year and reload* — and the always-partial
current year picks up each new month via the append path as it lands.

## `reconcile.py` as a pure function

The decision logic is a pure function of two inputs:

```
reconcile(disk_months: list[MonthFile],        # (year, month, path, source_row_count)
          manifest_rows: list[ManifestRow],     # (year, month, row_count)
          table_row_counts: dict[int, int],     # year -> actual COUNT(*) (0 if table absent)
          full_refresh: bool) -> list[YearPlan] # per year: SKIP | APPEND[months] | RELOAD
```

No database access inside `reconcile`; the CLI gathers the three inputs (disk metadata +
manifest select + per-year `COUNT(*)`) and hands them in. This makes the core logic
exhaustively unit-testable with zero infrastructure, and keeps `load.py` a thin executor of
the returned plan.

## Exit codes (mirrors `normalize`)

- `0` — success, including all-skip no-ops and successful `--dry-run`.
- `1` — one or more data types failed mid-load; the others were still processed. The failing
  type's year is left in whatever state the batched commits reached (next run truncates+reloads it).
- `2` — connection / auth / provisioning / config error (bad host, missing `MSSQL_PASSWORD`,
  unmapped DuckDB type, extension load failure). Nothing loaded.

## Testing strategy

```
tests/taxi_loader/
  conftest.py            # builds tiny synthetic normalized parquet families via DuckDB
  test_reconcile.py      # pure-function decision table — NO database
  test_ddl.py            # DESCRIBE -> taxi_shared mapping -> CREATE TABLE string shape
  test_manifest.py       # manifest read/write round-trips (against the container)
  test_load_integration.py  # end-to-end against SQL-Server-in-Docker
```

- **`test_reconcile.py` (no DB, the bulk of the coverage):** every row of the decision table —
  fresh load, all-skip, append-one-new-month, source-count-changed → reload, manifest-month-
  vanished → reload, integrity-mismatch → reload, `--full-refresh` → reload. Pure and fast.
- **Integration (`mcr.microsoft.com/mssql/server:2022-latest` in Docker):** load synthetic
  parquet, assert data-table row counts and manifest contents; then assert **idempotency**
  (immediate re-run skips everything), **append** (drop in a new month file → only that month
  loads, others skip), and **truncate-reload** (mutate a month's row count on disk → the whole
  year rebuilds and the manifest is rewritten). These same tests are the loader's slice of the
  CI SQL-Server-in-Docker job (CI sub-project). Locally they **skip** when `MSSQL_PASSWORD` /
  host env is unset, so `pytest` stays green on a laptop with no SQL Server.

**Target test count:** ~20–25, weighted toward the pure `reconcile` cases.

## Implementation sequence (for the plan)

> **[as-built]** Historical planning order. The build followed
> `docs/superpowers/plans/2026-07-22-sql-loader.md` (9 tasks, subagent-driven). The spike ran in
> two parts (extension install/version without Docker, then full behavior against SQL Server 2022);
> its findings are in `docs/superpowers/notes/2026-07-22-mssql-extension-spike.md`. The `REPLACE true`
> path below was investigated but **not** adopted — see the *[as-built]* notes above.

0. **Extension spike:** confirm `INSTALL mssql FROM community` v0.2.1 loads, the ATTACH +
   `CREATE DATABASE` provisioning path, the exact scan/exec function name, and that
   `COPY … (FORMAT 'bcp')` append + `REPLACE true` behave as documented against a throwaway
   Docker SQL Server. This de-risks every later step.
1. `taxi_shared`-backed DDL builder (`load.py` DDL half) + `test_ddl.py`.
2. `reconcile.py` pure function + exhaustive `test_reconcile.py`.
3. `connection.py` (install/load/attach/provision) + `manifest.py`.
4. `load.py` executor (append + truncate-reload) wiring `reconcile` plans to `COPY` statements.
5. `cli.py` (arg parsing, `--dry-run`, exit codes) + integration tests.
6. `pyproject.toml` wiring + `loader/README.md` pointer.

## Success criteria

- `taxi-load yellow` against a populated `raw-normalized/yellow/` creates database `taxi`
  (if absent) and `dbo.yellow_<year>` tables, bulk-loaded via the `mssql` extension's BCP path.
- Re-running `taxi-load yellow` immediately is a full no-op (every year skips).
- Dropping a new month file into `raw-normalized/yellow/<year>/` and re-running loads **only**
  that month (append), leaving other years untouched.
- Changing a previously-loaded month's row count triggers a **whole-year** truncate + reload,
  with no `DELETE` statements issued.
- A load interrupted mid-file leaves the table with committed batches; the next run detects the
  table/manifest count mismatch and rebuilds that year cleanly (no duplicate rows).
- `taxi-load` (no arg) processes all four types.
- `--database` / `--schema` are honored; a non-`dbo` schema is created if missing.
- `uv run --extra test pytest tests/taxi_loader/` passes; the pure-`reconcile` tests run with
  no SQL Server, the integration tests skip without `MSSQL_PASSWORD`.

## Out of scope

- The orchestrator (`download → normalize → load` in one command) — separate sub-project.
- Committing the per-type normalize mapping YAMLs for unattended runs — orchestrator sub-project.
- The broader CI fake-data end-to-end pipeline and K6 run — CI sub-project (this spec only owns
  the loader's own Docker integration tests).
- Any non-SQL-Server backend, alternative table layouts, or row-level upsert/MERGE.
- Bounded/tuned string lengths beyond `taxi_shared`'s current mapping, indexes, or post-load
  statistics — a later optimization pass, not v1.
