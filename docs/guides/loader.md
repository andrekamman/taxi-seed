# Loader

`taxi-load` bulk-loads normalized TLC parquet (`raw-normalized/<type>/<year>/*.parquet`) into SQL Server, one table per year per type, entirely through DuckDB and the `mssql` community extension — no ODBC driver, no separate ETL framework. It reconciles what's on disk against what's already loaded and picks, per year, one of three actions: skip, append the new months, or truncate and reload the whole year. That reconciliation is what makes re-running the tool after a fresh `normalize` pass (or after TLC ships a new month) safe and cheap.

## Prerequisites

- Normalized parquet under `raw-normalized/<type>/<year>/` — produced by the [normalize](normalize.md) guide's tool.
- A reachable SQL Server instance (local, container, or remote). The `mssql` DuckDB extension is installed automatically on first run; you don't need to install anything server-side beyond SQL Server itself.
- The `MSSQL_PASSWORD` environment variable set. There is no `--password` flag — the password only ever comes from the environment, so it never shows up in shell history or process listings.
- `uv sync` in the repo root.

## Install

```bash
uv sync
```

This exposes the `taxi-load` console script (defined in the root `pyproject.toml`, `taxi_loader.cli:main`). You can also invoke it as a module:

```bash
python -m taxi_loader.cli yellow
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

## One table per year per type

Every `(type, year)` pair gets its own table, named `<type>_<year>` (e.g. `yellow_2024`, `fhvhv_2019`) in the configured schema (`dbo` by default). The table's DDL is derived directly from the parquet: the loader runs `DESCRIBE` on a sample month, maps each DuckDB column type to a SQL Server type (`taxi_shared.type_mapping.map_duckdb_to_mssql` — e.g. `DOUBLE`→`FLOAT`, `VARCHAR`→`NVARCHAR(MAX)`, `TIMESTAMP`→`DATETIME2`, `DECIMAL(p,s)` preserved), and generates a `CREATE TABLE` statement. There is no manual DDL to maintain — the schema tracks whatever `normalize` last wrote.

If a column's DuckDB type has no SQL Server equivalent, `map_duckdb_to_mssql` raises `TypeMappingError` and that type's load fails (see [Exit codes](#exit-codes)).

Data actually moves via DuckDB's `COPY ... TO 'mssql://mssql/<schema>/<table>' (FORMAT 'bcp', ...)`, which streams through the `mssql` extension's bulk-copy path rather than row-by-row inserts.

## Idempotent reconcile: skip / append / truncate-reload

The loader tracks what it has already loaded in a bookkeeping table, `<schema>._load_manifest`, with one row per loaded month (primary key `(data_type, year, month)`, plus `source_file`, `row_count`, `loaded_at`). Every run, for every `(type, year)` present either on disk or in the manifest, it decides one of three actions by comparing disk parquet against the manifest and the live table's row count:

- **skip** — the manifest's months exactly match disk (same months, same row counts) and the table's actual row count matches the manifest's recorded total. Nothing to do.
- **append** — every manifest month still matches its disk file, and disk has one or more new months not yet in the manifest. Only the new months are copied; the existing table and its rows are untouched.
- **truncate + reload** — anything smells inconsistent: `--full-refresh` was passed, the live table's row count doesn't match the manifest's recorded sum (someone loaded outside this tool, or a prior run was interrupted after the `COPY` but before the manifest write), a month recorded in the manifest is no longer on disk, or a month's row count changed on disk (re-normalized data). The table is dropped, recreated from the current parquet schema, and every month for that year is copied in one `COPY` statement.

This is computed by the pure function `reconcile()` (`taxi_loader/reconcile.py`) from three inputs gathered up front: the disk parquet files (with per-file row counts from parquet metadata), the manifest rows for that type, and the live table row counts — no database writes happen during planning.

**Durability model**, applied while executing the plan (`load.py:execute_year_plan`):

- On **append**, months are loaded one at a time; a month's manifest row is written only *after* that month's `COPY` succeeds. If the process dies mid-append, the manifest accurately reflects what actually landed — a re-run appends the remaining months rather than silently believing they're already loaded.
- On **reload**, the table is dropped and recreated, the year's manifest rows are deleted up front, all months for the year are copied in a single `COPY` statement, and manifest rows for every month are written afterward.
- A year that had manifest rows but now has **zero** files on disk (the whole year vanished) is treated as a reload target with no months: the table is dropped and the manifest rows for that year are deleted, but nothing is recreated.

## The `--dry-run` flag

`--dry-run` prints the reconciliation plan per type/year (`skip`, `append month(s) NN, NN`, or `truncate + reload (N month file(s))`) and exits without touching SQL Server data. It still connects and attaches — read-only — so the plan reflects the real manifest and table state, *except* when the target database doesn't exist yet: in that case the attach is allowed to fail silently, and every year is reported as fresh (no manifest, no table-count lookups), since there's nothing on the server to compare against.

```bash
uv run taxi-load yellow --dry-run
```

## The `--full-refresh` flag

`--full-refresh` forces every `(type, year)` being processed onto the **truncate + reload** path, regardless of what the manifest says. Use it to rebuild a year after fixing a mapping bug in `normalize`, or to recover from a manifest you no longer trust.

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

`MSSQL_PASSWORD` is the only accepted source for the password — there is no `--password` flag.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every requested type either loaded successfully, was a no-op (`--dry-run`), or had no parquet under the resolved input dir (skipped as "nothing to load yet"). |
| 1 | At least one type failed mid-load (a `duckdb.Error` or a loader-internal error during `COPY`/DDL execution) — a partial load; other types may have still succeeded. |
| 2 | An identifier/config error (invalid `--schema` or `--database`), a missing `MSSQL_PASSWORD`, a connection/provisioning failure (installing the `mssql` extension, an unexpected extension version, attaching the database, creating the database/schema), or a per-type `TypeMappingError` (a DuckDB column type with no SQL Server equivalent). |

When multiple types are processed in one invocation (no positional `data_type`), the overall exit code is the **maximum** across all per-type outcomes — so a `TypeMappingError` on one type (2) outranks a mid-load failure on another (1), which outranks types that loaded cleanly (0). Note that the DuckDB extension install/attach/provisioning check happens once, up front, before any type is processed — a failure there returns 2 immediately without attempting any type.

## Troubleshooting

**Q: `error: MSSQL_PASSWORD environment variable is required`.**
A: Set it before invoking: `export MSSQL_PASSWORD=...` (or `MSSQL_PASSWORD=... uv run taxi-load yellow`). There is no flag alternative — this is deliberate so the password never appears in `ps` output or shell history via an argument.

**Q: `error: invalid schema '...' : must match [A-Za-z_][A-Za-z0-9_]*` (or `database`).**
A: `--schema` and `--database` are validated against a strict identifier pattern before anything touches SQL Server, since they're interpolated into DDL (`CREATE DATABASE`, `CREATE SCHEMA`) rather than passed as bind parameters. Rename to a plain identifier, or quote/alias it on the SQL Server side and point `--database`/`--schema` at that name instead.

**Q: `error: mssql extension version '...' != expected '7e57d24'`.**
A: The loader pins the exact community-registry build of the `mssql` DuckDB extension it was tested against. A version mismatch means `INSTALL mssql FROM community` resolved to a different build than expected (the community registry updated). This is a deliberate hard-stop, not a warning — bumping `EXPECTED_MSSQL_EXT_VERSION` in `taxi_loader/connection.py` is a decision that should be made after re-testing, not silently.

**Q: `error: yellow: No SQL Server mapping for DuckDB type: '...'`.**
A: A `TypeMappingError` — a normalized parquet column has a DuckDB type with no entry in `taxi_shared.type_mapping`. Add the mapping there, or check whether `normalize` should have cast the column to something already mapped.

**Q: `error: yellow failed mid-load: ...`.**
A: The type got partway through a `COPY` or DDL statement and DuckDB (or the `mssql` extension) raised. Common causes: SQL Server connectivity dropped mid-run, a constraint violation, or disk/log space exhausted on the SQL Server side. Re-running is safe — the reconcile logic will pick up from the manifest's actual state (the affected year likely reloads rather than silently continuing a corrupt append).

**Q: The plan says `truncate + reload` for a year I didn't expect to change.**
A: This fires whenever the live table's row count doesn't match the manifest's recorded sum for that year — which can mean rows were inserted/deleted outside `taxi-load`, a prior run was interrupted between the `COPY` and the manifest write, or `normalize` re-wrote that year's parquet with different row counts. Run with `--dry-run` first to see exactly which years are affected before committing to a reload.

**Q: `<type>: no parquet under <dir>/<type>, skipping`.**
A: Nothing has been normalized for that type yet at the resolved input directory. Run [normalize](normalize.md) for that type first, or check `--data-dir`/`--input-dir` if your normalized output lives elsewhere. This is not an error — the type contributes exit 0.
