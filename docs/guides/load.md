# taxi-load

`taxi-load` bulk-loads normalized TLC parquet (`raw-normalized/<type>/<year>/*.parquet`) into SQL Server, one table per year per type, entirely through DuckDB and the `mssql` extension — no ODBC driver, no separate ETL framework. DuckDB is pinned at exactly `1.5.5` and the extension is **vendored**, not installed at runtime (see [The vendored extension](#the-vendored-extension)). It reconciles what's on disk against what's already loaded and picks, per year, one of three actions: skip, append the new months, or truncate and reload the whole year. That reconciliation is what makes re-running the tool after a fresh `taxi-normalize` pass (or after TLC ships a new month) safe and cheap.

If you want a complete, presized, backed-up SQL Server data set rather than just rows in a database you already have, [`taxi-mssql`](mssql.md) drives this tool as one of its stages and does the database shaping around it.

## Prerequisites

- Normalized parquet under `raw-normalized/<type>/<year>/` — produced by the [normalize](normalize.md) guide's tool.
- A reachable SQL Server instance (local, container, or remote). Nothing has to be installed server-side beyond SQL Server itself.
- The vendored `mssql` extension on disk. `taxi-load` fetches it on first use, or run [`taxi-vendor-extension`](#the-vendored-extension) as a separate, retryable step first.
- The `MSSQL_PASSWORD` environment variable set. There is no `--password` flag — the password only ever comes from the environment, so it never shows up in shell history or process listings.
- `uv sync` in the repo root.

## Install

```bash
uv sync
```

This exposes the `taxi-load` console script (defined in the root `pyproject.toml`, `taxi_load.cli:main`). You can also invoke it as a module:

```bash
python -m taxi_load.cli yellow
```

## Basic usage

```bash
export MSSQL_PASSWORD=your-password

# Load one type
uv run taxi-load yellow

# Load all four types (yellow, green, fhv, fhvhv)
uv run taxi-load

# See the plan without writing anything
uv run taxi-load yellow --dry-run
```

`data_type` is a positional argument, one of `yellow`, `green`, `fhv`, `fhvhv`; omit it to load all four in turn.

## The vendored extension

`INSTALL mssql FROM community` resolves against a registry that **rebuilds**. DuckDB 1.5.5 alone has served three different builds of the `mssql` extension — `37596c2` (`mssql_version()` `0.2.3`), `e2b8c01` (`0.2.4`) and `d35b617` — with no DuckDB release in between. Pinning either the build hash or the semver therefore pins nothing on its own: the bytes move underneath an unchanged pin. Worse, a plain `INSTALL` **never re-fetches when the extension is cached**, so a machine that has run the loader before cannot even observe the drift without `FORCE INSTALL`.

So taxi-seed owns the bytes. The extension is mirrored as a release asset on this repo and loaded from a vendored extension directory, which has three consequences worth knowing:

- **Integrity is cryptographic, not a checksum we keep.** DuckDB enforces the extension signature on that path under its *default* configuration. `allow_unsigned_extensions` is not needed and is not used; a tampered file is refused outright. The recorded SHA-256s are an identity pin — "this is the build we tested" — not the integrity mechanism.
- **`LOAD mssql` resolves by name** out of `extension_directory`, with no `INSTALL`, no network and no `.info` sidecar.
- **Both the build hash and the semver are asserted at load**, and the error names whichever disagreed. The hash catches a version re-pinned without re-spiking; the semver catches a stale vendored directory.

One pinned fact drives all of it — DuckDB version, build hash, semver and download URL all derive from constants in `load/src/taxi_load/extension.py`. That is also why `duckdb==1.5.5` is an **exact** pin in `pyproject.toml`: the vendored directory is keyed by exact DuckDB version, so a floating pin silently orphans it.

Vendoring happens on first use, but you can do it as a separate, retryable step:

```bash
uv run taxi-vendor-extension                 # into ~/.taxi-seed/extensions
uv run taxi-vendor-extension --root /opt/ext  # somewhere else
export TAXI_EXTENSION_DIR=/opt/ext            # point the loader at it
```

`TAXI_EXTENSION_DIR` is what the [`taxi-mssql`](mssql.md) image sets, having baked the extension at build time so a multi-hour run never stops to download it. The fetch prefers taxi-seed's own release mirror and falls back to the community CDN; both are tried before it gives up, and the message says what each returned. (The community CDN serves over plain HTTP and 403s `urllib` while allowing `curl` — the signature, not the transport, is what protects the fetch.)

## Session settings

Three DuckDB session settings have defaults that are wrong for this loader, so it sets all three explicitly on every connection:

| Setting | Extension default | What the loader sets | Why |
|---|---|---|---|
| `mssql_copy_tablock` | `auto` | `true` | `auto` resolves to **off** for any clustered target, so table shape would silently decide it. See [`--writers`](#the-writers-flag) for what off costs. |
| `mssql_exec_invalidate_cache` | `false` | `true` | The catalog metadata cache returns stale results within a session, so a table created through `mssql_exec` can be invisible to the catalog in that same session. This loader issues DDL constantly. |
| `mssql_query_timeout` | `30s` | `0` (disabled) | A `COUNT_BIG` over a large year table can exceed 30s, and the timeout surfaces as what looks like a connection failure. |

`mssql_copy_parallel_writers` is deliberately *not* in that list: its default (0) derives from DuckDB's thread count capped at 8, which is the fastest configuration measured here. [`--writers`](#the-writers-flag) overrides it.

Credentials go through DuckDB's `mssql` **secret type** rather than a connection string:

```sql
CREATE OR REPLACE SECRET taxi_mssql (TYPE mssql, HOST ?, PORT ?, DATABASE ?, USER ?, PASSWORD ?, USE_ENCRYPT true);
ATTACH '' AS mssql (TYPE mssql, SECRET taxi_mssql);
```

Every field binds as a parameter, so the password never reaches a SQL string — `ATTACH` takes no bind parameters, which is why the connection-string form could only interpolate it as a literal. `duckdb_secrets()` renders it redacted. Note there is no trust-server-certificate option on an `mssql` secret; `USE_ENCRYPT` encrypts without validating the certificate, which is the posture a SQL Server container's self-signed certificate requires.

## One table per year per type

Every `(type, year)` pair gets its own table, named `<type>_<year>` (e.g. `yellow_2024`, `fhvhv_2019`) in the configured schema (`dbo` by default). The table's DDL is derived directly from the parquet: the loader runs `DESCRIBE` on a sample month, maps each DuckDB column type to a SQL Server type (`taxi_shared.type_mapping.map_duckdb_to_mssql` — e.g. `DOUBLE`→`FLOAT`, `VARCHAR`→`NVARCHAR(MAX)`, `TIMESTAMP`→`DATETIME2`, `DECIMAL(p,s)` preserved), and generates a `CREATE TABLE` statement. There is no manual DDL to maintain — the schema tracks whatever `taxi-normalize` last wrote.

If a column's DuckDB type has no SQL Server equivalent, `map_duckdb_to_mssql` raises `TypeMappingError` and that type's load fails (see [Exit codes](#exit-codes)).

Data actually moves via DuckDB's `COPY ... TO 'mssql://mssql/<schema>/<table>' (FORMAT 'bcp', ...)`, which streams through the `mssql` extension's bulk-copy path rather than row-by-row inserts.

## Page compression

Every table the loader creates is page-compressed. The generated DDL ends with a `WITH` clause:

```sql
CREATE TABLE dbo.yellow_2024 (
    VendorID INT,
    tpep_pickup_datetime DATETIME2,
    ...
) WITH (DATA_COMPRESSION = PAGE);
```

This is unconditional — there is no flag and no configuration key. It applies to the `<type>_<year>` data tables and to the `_load_manifest` bookkeeping table, because both are built by the same `taxi_shared.sql_generator.generate_create_table_sql`.

Taxi data compresses well: the columns repeat heavily within a page (vendor IDs, location IDs, rate codes, payment types), which is the pattern page compression's dictionary and prefix stages are built for. The result is a smaller database and fewer pages read per query.

Two things to know:

- **Every edition supports it.** Data compression stopped being an Enterprise-only feature in SQL Server 2016 SP1, so Express and Developer both take this DDL. (Backup compression is a separate feature and is still edition-limited.)
- **Compressing the manifest is a consequence, not a goal.** `_load_manifest` holds one row per loaded month, so compressing it saves nothing worth measuring. It is compressed because it shares the generator, and that was accepted as a side effect.

To undo it for a table you have already loaded:

```sql
ALTER TABLE dbo.yellow_2024 REBUILD WITH (DATA_COMPRESSION = NONE);
```

A later `--full-refresh` (or any truncate-reload) recreates the table from the generator, so it comes back compressed.

## Idempotent reconcile: skip / append / truncate-reload

The loader tracks what it has already loaded in a bookkeeping table, `<schema>._load_manifest`, with one row per loaded month (primary key `(data_type, year, month)`, plus `source_file`, `row_count`, `loaded_at`). Every run, for every `(type, year)` present either on disk or in the manifest, it decides one of three actions by comparing disk parquet against the manifest and the live table's row count:

- **skip** — the manifest's months exactly match disk (same months, same row counts) and the table's actual row count matches the manifest's recorded total. Nothing to do.
- **append** — every manifest month still matches its disk file, and disk has one or more new months not yet in the manifest. Only the new months are copied; the existing table and its rows are untouched.
- **truncate + reload** — anything smells inconsistent: `--full-refresh` was passed, the live table's row count doesn't match the manifest's recorded sum (someone loaded outside this tool, or a prior run was interrupted after the `COPY` but before the manifest write), a month recorded in the manifest is no longer on disk, or a month's row count changed on disk (re-normalized data). The table is dropped, recreated from the current parquet schema, and every month for that year is copied in one `COPY` statement.

This is computed by the pure function `reconcile()` (`taxi_load/reconcile.py`) from three inputs gathered up front: the disk parquet files (with per-file row counts from parquet metadata), the manifest rows for that type, and the live table row counts — no database writes happen during planning.

**Durability model**, applied while executing the plan (`load.py:execute_year_plan`):

- On **append**, months are loaded one at a time; a month's manifest row is written only *after* that month's `COPY` succeeds. If the process dies mid-append, the manifest accurately reflects what actually landed — a re-run appends the remaining months rather than silently believing they're already loaded.
- On **reload**, the table is dropped and recreated, the year's manifest rows are deleted up front, all months for the year are copied in a single `COPY` statement, and manifest rows for every month are written afterward.
- A year that had manifest rows but now has **zero** files on disk (the whole year vanished) is treated as a reload target with no months: the table is dropped and the manifest rows for that year are deleted, but nothing is recreated.

### What reconcile does not look at, and the guard that covers it

Reconcile decides from three observables: the months on disk, the rows in the load
manifest, and the committed row count of the live table. **Column sets are not among
them**, and they deliberately stay that way — reconcile is a pure function with no
database access, which is where nearly all of the loader's correctness is tested.

That matters when a curated mapping's `target:` moves (see
[Re-pinning `target:`](normalize.md#re-pinning-target-changes-the-table-shape)). The
normalized parquet gains a column while the existing year table does not, the row counts
still agree, so reconcile picks `APPEND`.

A separate guard runs after reconcile and **refuses** such a plan:

```
error: green refused: the normalized parquet and the existing table disagree on columns:
  2026: parquet has ['request_source'] which the table lacks

Appending would fail in the bulk-load path. This usually means a curated mapping's
`target:` was re-pinned to adopt a new column ... Re-run with --full-refresh for this
trip type to rebuild the affected year(s), or drop them by hand.
```

Exit code **2**, and the message names the remedy. Three choices in that behaviour are
deliberate:

- **It refuses rather than rebuilding automatically.** A `RELOAD` drops and recreates the
  table, so a spurious mismatch — a column someone added by hand on purpose, say — would
  silently discard a loaded year. Typing `--full-refresh` keeps that decision yours.
- **It compares names, not types.** Comparing types would mean trusting that
  `map_duckdb_to_mssql` round-trips identically against `sys.columns`; a rendering
  difference there would make the guard misfire on a table that is actually fine.
- **It only checks `APPEND`.** `RELOAD` rebuilds the table from the parquet anyway, and
  checking it would make the guard refuse the very command its own message tells you to
  run.

So the remedy after re-pinning is still `--full-refresh`, once, for the affected types —
but you now get told, before anything is attempted, instead of a column mismatch surfacing
from inside the bulk-load path.

## The `--dry-run` flag

`--dry-run` prints the reconciliation plan per type/year (`skip`, `append month(s) NN, NN`, or `truncate + reload (N month file(s))`) and exits without touching SQL Server data. It still connects and attaches — read-only — so the plan reflects the real manifest and table state, *except* when the target database doesn't exist yet: in that case the attach is allowed to fail silently, and every year is reported as fresh (no manifest, no table-count lookups), since there's nothing on the server to compare against.

```bash
uv run taxi-load yellow --dry-run
```

## The `--full-refresh` flag

`--full-refresh` forces every `(type, year)` being processed onto the **truncate + reload** path, regardless of what the manifest says. Use it to rebuild a year after fixing a mapping bug in `taxi-normalize`, or to recover from a manifest you no longer trust.

```bash
uv run taxi-load yellow --full-refresh
```

## The `--data-dir` and `--input-dir` flags

**`--data-dir DIR`** — base directory; the loader reads `DIR/raw-normalized/<type>/<year>/*.parquet` (default: unset — when `--input-dir` is also not given, falls back to bare `raw-normalized` in the current directory).

**`--input-dir DIR`** — overrides `--data-dir` entirely: the loader reads `DIR/<type>/<year>/*.parquet` directly. Use this when your normalized output doesn't live under a `raw-normalized/` subdirectory.

```bash
uv run taxi-load yellow --data-dir /mnt/nas/tlc-mirror
uv run taxi-load yellow --input-dir /mnt/nas/tlc-mirror/normalized-output
```

If a type has no parquet at all under the resolved input directory, that type is skipped with a message (`<type>: no parquet under <dir>/<type>, skipping`) and contributes success (exit 0) for that type — this is the same "nothing to do yet" posture as the other tools in this repo.

## The `--writers` flag

**`--writers N`** — how many concurrent bulk-load connections a single `COPY` may open (`mssql_copy_parallel_writers`). Omit it and DuckDB derives the count from its thread count, capped at 8, which is the fastest configuration measured on this repo's tables. `--writers 1` loads serially, which is the tool to reach for when bisecting a load problem.

This is in-process parallelism: one `COPY`, several bulk-load connections. It replaced an earlier design that ran one `taxi-load` process per month, supervised by a thread pool, with a year-level integrity gate to clean up after a killed worker. On green 2015 (19.2M rows) the in-process writers measured **1.65x faster** — 47.9s against 79.0s — and still **1.44x faster at matched parallelism**, so the gain is the mechanism rather than simply the count. The fan-out, its shard discovery and the integrity gate are all gone; the loader's own reconcile covers the hazard the gate existed for.

### `TABLOCK` and table shape

`TABLOCK` is always on, and it matters more than the writer count. Three results from the same measurement, on this repo's actual table shape:

| Arm | Against page-compressed heap + `TABLOCK` |
|---|---|
| `TABLOCK=false` | **3.2x slower**, and a **36% larger** table |
| Clustered index, no writers | no gain |
| Clustered index + `TABLOCK` + writers | **fails outright**, with a partial load |

The `TABLOCK=false` penalty is **page-latch contention, not lost compression**: without `TABLOCK` the writers contend on `PAGELATCH_EX` at the insert point and rows land in less densely packed pages. That is why the extension's `mssql_copy_tablock = auto` default is a trap here — `auto` resolves to *off* for any clustered target, so adding an index would silently turn off the setting the load depends on. The loader sets it explicitly rather than letting table shape decide.

!!! note "Published advice to the contrary is scoped elsewhere"
    Microsoft's and the community's `TABLOCK=false` guidance is written for Azure SQL Database and for clustered tables. Neither holds for a boxed SQL Server loading page-compressed heaps, which is what this loader creates. The numbers above were taken on this repo's tables; prefer them.

## The `--year` and `--month` flags

**`--year YYYY`** restricts the run to one year; **`--month 1..12`** restricts it further to one month of that year, and requires `--year` (a bare `--month` would mean "this month of every year", which is not a useful request).

These narrow what reconcile considers. They are a debugging and backfill tool — reload a single bad year without touching the rest, or bisect a load failure down to one file — not a parallelism mechanism. Running several of them at once is no longer how this loader goes fast; `--writers` is.

## The `--require-existing-database` flag

**`--require-existing-database`** — fail if the target database does not exist, instead of creating it.

By default the loader creates the database if it is absent, with server-default file paths and sizes. That is wrong for any caller that provisions a presized database itself: if its provisioning step is skipped or fails soft, the loader would quietly produce a correctly-named database of entirely the wrong shape, and nothing notices until it fills up. With this flag that becomes a clean exit 2.

## Configuration

| Flag | Default | Meaning |
|---|---|---|
| `data_type` (positional) | all four | `yellow`, `green`, `fhv`, or `fhvhv`. Omit to load all four in sequence. |
| `--host` | `localhost` | SQL Server hostname. |
| `--port` | `1433` | SQL Server port. |
| `--database` | `taxi` | Target database name. Created automatically if it doesn't exist (non-dry-run only). Must match `[A-Za-z_][A-Za-z0-9_]*`. |
| `--schema` | `dbo` | Target schema. Created automatically if non-`dbo` and absent (non-dry-run only). Must match `[A-Za-z_][A-Za-z0-9_]*`. |
| `--user` | `sa` | SQL Server login. |
| `--data-dir` | *(unset)* | Base dir; reads `<data-dir>/raw-normalized` unless `--input-dir` is given; falls back to bare `raw-normalized` when neither flag is given. |
| `--input-dir` | *(unset)* | Reads `<input-dir>/<type>/<year>/*.parquet` directly; overrides `--data-dir`. |
| `--flush-rows` | `100000` | BCP commit batch size passed to the `mssql` extension's `COPY`. |
| `--full-refresh` | off | Force truncate + reload of every year processed, ignoring the manifest. |
| `--dry-run` | off | Print the reconciliation plan; write nothing. |
| `--writers` | *(DuckDB's thread count, capped at 8)* | Concurrent bulk-load connections one `COPY` may open (`mssql_copy_parallel_writers`). `1` loads serially. |
| `--require-existing-database` | off | Fail if the target database is absent instead of creating it. |
| `--year` | *(every year on disk)* | Restrict the run to one year. |
| `--month` | *(every month of `--year`)* | Restrict the run to one month; requires `--year`. |

`MSSQL_PASSWORD` is the only accepted source for the password — there is no `--password` flag. `TAXI_EXTENSION_DIR` is the only other environment variable the loader reads; see [The vendored extension](#the-vendored-extension).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every requested type either loaded successfully, was a no-op (`--dry-run`), or had no parquet under the resolved input dir (skipped as "nothing to load yet"). |
| 1 | At least one type failed mid-load (a `duckdb.Error` or a loader-internal error during `COPY`/DDL execution) — a partial load; other types may have still succeeded. |
| 2 | An identifier/config error (invalid `--schema` or `--database`), a missing `MSSQL_PASSWORD`, a connection/provisioning failure (vendoring or loading the `mssql` extension, an extension build or version that does not match the pin, attaching the database, creating the database/schema, or `--require-existing-database` with no such database), or a per-type `TypeMappingError` (a DuckDB column type with no SQL Server equivalent). |

When multiple types are processed in one invocation (no positional `data_type`), the overall exit code is the **maximum** across all per-type outcomes — so a `TypeMappingError` on one type (2) outranks a mid-load failure on another (1), which outranks types that loaded cleanly (0). Note that the DuckDB extension install/attach/provisioning check happens once, up front, before any type is processed — a failure there returns 2 immediately without attempting any type.

## Troubleshooting

**Q: `error: MSSQL_PASSWORD environment variable is required`.**
A: Set it before invoking: `export MSSQL_PASSWORD=...` (or `MSSQL_PASSWORD=... uv run taxi-load yellow`). There is no flag alternative — this is deliberate so the password never appears in `ps` output or shell history via an argument.

**Q: `error: invalid schema '...' : must match [A-Za-z_][A-Za-z0-9_]*` (or `database`).**
A: `--schema` and `--database` are validated against a strict identifier pattern before anything touches SQL Server, since they're interpolated into DDL (`CREATE DATABASE`, `CREATE SCHEMA`) rather than passed as bind parameters. Rename to a plain identifier, or quote/alias it on the SQL Server side and point `--database`/`--schema` at that name instead.

**Q: `error: vendored mssql extension is not the pinned build: ...`.**
A: The loader does not `INSTALL` the extension from the community registry — it loads a **vendored** copy, because that registry rebuilds. DuckDB 1.5.5 alone has served three different builds (`37596c2`, `e2b8c01`, `d35b617`), so what `INSTALL` returns today is not what was tested. The assertion checks both the build hash and `mssql_version()`, and the message names whichever disagreed: the hash catches a version re-pinned without re-spiking, the semver catches a stale vendored directory. Re-run `taxi-vendor-extension` against a clean directory. Changing the pinned values in `load/src/taxi_load/extension.py` is a decision to make after re-testing, not a way to silence this.

**Q: `could not vendor the mssql extension`.**
A: The loader fetches the extension on first use, preferring taxi-seed's own release mirror and falling back to the community CDN. The message lists what each source returned. Run `taxi-vendor-extension` (optionally `--root DIR`) to do it as a separate, retryable step; set `TAXI_EXTENSION_DIR` to point the loader at a directory you populated yourself, which is what the container does at image build time.

**Q: `error: yellow: No SQL Server mapping for DuckDB type: '...'`.**
A: A `TypeMappingError` — a normalized parquet column has a DuckDB type with no entry in `taxi_shared.type_mapping`. Add the mapping there, or check whether `taxi-normalize` should have cast the column to something already mapped.

**Q: `error: yellow failed mid-load: ...`.**
A: The type got partway through a `COPY` or DDL statement and DuckDB (or the `mssql` extension) raised. Common causes: SQL Server connectivity dropped mid-run, a constraint violation, or disk/log space exhausted on the SQL Server side. Re-running is safe — the reconcile logic will pick up from the manifest's actual state (the affected year likely reloads rather than silently continuing a corrupt append).

**Q: The plan says `truncate + reload` for a year I didn't expect to change.**
A: This fires whenever the live table's row count doesn't match the manifest's recorded sum for that year — which can mean rows were inserted/deleted outside `taxi-load`, a prior run was interrupted between the `COPY` and the manifest write, or `taxi-normalize` re-wrote that year's parquet with different row counts. Run with `--dry-run` first to see exactly which years are affected before committing to a reload.

**Q: `<type>: no parquet under <dir>/<type>, skipping`.**
A: Nothing has been normalized for that type yet at the resolved input directory. Run [normalize](normalize.md) for that type first, or check `--data-dir`/`--input-dir` if your normalized output lives elsewhere. This is not an error — the type contributes exit 0.
