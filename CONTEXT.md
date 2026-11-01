# taxi-seed

taxi-seed exists to provide data to ingest for lakehouse testing. It mirrors the NYC TLC
trip record data set, normalizes it, and loads it into a database; the data set is useful
more widely, but that is the purpose the repo is built around.

This file is the glossary — use these words in code, comments, commit messages, and docs.
It owns the vocabulary of *producing* the data set: provisioning, presizing, the
deliverable set, the load manifest, and the three-state exit contract. The consumer repo
[`taxi-lab`](https://github.com/andrekamman/taxi-lab) keeps its own `CONTEXT.md` for the
k6 recipes that *read* the data set. The few terms defined in both are marked below and
must stay in step.

## The data set

**Trip record**:
One row of the NYC Taxi and Limousine Commission's public data — a single taxi or
for-hire vehicle journey. The records start in 2009 and are published as monthly parquet
files.
_Avoid_: ride, journey, trip event
_Note_: defined in both repos; keep in step.

**Trip type**:
One of the four services the TLC publishes separately: `yellow`, `green`, `fhv`, `fhvhv`.
_Avoid_: data type, series, taxi type, category
_Note_: defined in both repos; keep in step. The code identifier is `data_type` and stays
that way. Three strings the tools print still say "data type"
(`normalize/src/taxi_normalize/cli.py`, `schema-drift/src/taxi_schema_drift/report.py`
twice). Docs quote that output verbatim, so those quotes keep the old word until the
strings themselves change.

**Mirror**:
The local copy of the published parquet, under `raw/`. Also the verb for making it.
_Avoid_: download folder, cache, sync, scrape
_Note_: defined in both repos; keep in step. `taxi-mssql`'s `mirror` stage is wider than
the noun — it runs `taxi-pipeline` without the load, so it normalizes too.

**Data directory**:
The one root folder holding everything a run reads and writes: `raw/`,
`raw-normalized/`, `normalize/mappings/`, and the database files and backups under
`mssql/`. Every tool takes it as `--data-dir`; `taxi-mssql` takes it as `TAXI_DATA_DIR`
and bind-mounts it at `/data`.
_Avoid_: data folder, data dir, workspace, mount, volume, output folder, root

## Downloader

**Block page**:
The HTML page AWS WAF serves instead of a file when it decides the caller looks like a
scraper. It arrives as a `403`, so it must be told apart from a real `403 AccessDenied`.
_Avoid_: rate limit page, captcha, WAF error

**Give-up**:
One month the downloader stopped retrying — it exhausted its four attempts against a
block, or hit a persistent network error. Counted per run as `gaveup`.
_Avoid_: failure, error, miss, skip (a skip is a file already held)

**Incremental catch-up**:
The `--recent N` walk, which stops the moment it meets a month already on disk. This is
what makes a scheduled re-run cheap once the mirror is warm.
_Avoid_: sync, delta, update, resume

## Schema drift and normalize

**Normalize**:
Reconcile a trip type's parquet across years into one stable set of columns and types,
writing `raw-normalized/`.
_Avoid_: clean, transform, conform
_Note_: defined in both repos; keep in step.

**Schema drift**:
A published month whose columns disagree with the curated mapping for that trip type — a
new column, a rename, a changed type.
_Avoid_: schema change, mismatch, breaking change

**Curated mapping**:
The checked-in YAML in `normalize/mappings/<trip type>.yaml` recording every drift
decision a human has reviewed. It is the reason a fresh clone can normalize the full
history without redoing that review.
_Avoid_: config, schema file, mapping file, rules

**Acknowledgment**:
The `ack_date` a human writes into a curated mapping to accept a lossy cast or a dropped
column. Without it, normalize halts.
_Avoid_: approval, override, ignore, allowlist

**Target schema**:
The single column set a trip type normalizes to, declared under `target` in its curated
mapping.
_Avoid_: canonical schema, output schema, unified schema
_Note_: unrelated to a SQL Server schema such as `dbo` — say "database schema" for that.

## Loader

**Load manifest**:
The `<schema>._load_manifest` table, one row per loaded month, keyed
`(data_type, year, month)`. The loader reconciles against it to decide what to do, and it
is what makes resumability and backup staleness answerable.
_Avoid_: manifest (unqualified), tracking table, state table
_Note_: write it schema-qualified. `<schema>` is `--schema` / `MSSQL_SCHEMA`, which
defaults to `dbo`, so `dbo._load_manifest` is one instance of it rather than the name.

**Reconcile**:
The per-`(trip type, year)` decision the loader makes every run by comparing disk, the
load manifest, and the live table's row count. It picks one of three actions.
_Avoid_: sync, diff, plan

**Skip / append / truncate-reload**:
The three reconcile actions. **Skip** — disk and manifest agree and the row counts match.
**Append** — disk has new months and the old ones are unchanged. **Truncate-reload** —
anything disagrees, so the table is dropped, recreated, and refilled.
_Avoid_: no-op, incremental, full reload, rebuild

**Page compression**:
`WITH (DATA_COMPRESSION = PAGE)` on every table the loader creates. Unconditional — no
flag, no configuration key. It reaches the load manifest too, because both come from
`taxi_shared.sql_generator.generate_create_table_sql`.
_Avoid_: compression (unqualified — backup compression is a different feature)

## Pipeline

**Pipeline**:
download → normalize → load, run per trip type. `taxi-pipeline` drives it. It stops at
loading into a database that already exists; everything about making that database belongs
to `taxi-mssql`.
_Avoid_: workflow, job, chain, ETL

**Stage**:
One named unit of work inside a run. A stage decides for itself whether it has work to do,
and its exit code rolls up into the run's. `taxi-pipeline`'s stages are download,
normalize and load; `taxi-mssql`'s are preflight, mirror, memory, provision, ola, load and
backup, closing with a report.
_Avoid_: step, phase, task
_Note_: `taxi_mssql`'s module docstrings still number them "Step 1" … "Step 7"; those are
the outliers, and new text says stage.

## taxi-mssql

The SQL-Server-specific producer: it takes a data directory from empty to a loaded,
backed-up boxed SQL Server, calling the pipeline's tools for the portable work.

**Deliverable set**:
The five things a complete `taxi-mssql` run leaves in the data directory: the raw parquet
mirror, the normalized parquet, the database's data file, its log file, and the backup.
_Avoid_: output, artifacts, results, bundle

**Deliverable**:
One member of that set. Each is wanted in its own right — the data file and log file are
deliverables, not by-products of the backup.
_Avoid_: artifact, output file

**Provision**:
Create the database with its files at a chosen path and a chosen size, or attach the files
if they already exist.
_Avoid_: setup, init, prepare, bootstrap

**Presize**:
Create a database file at its intended final size instead of letting it autogrow there.
_Avoid_: preallocate, size up, pre-provision

**Warm start**:
A run that finds existing database files and attaches them, rather than creating and
reloading the database. This is the normal path for every run after the first.
_Avoid_: reattach, resume, restart

**Skip condition**:
The observable a stage checks to decide it has no work to do — a file on disk, a database
object, a row in the load manifest. There is no state file of its own.
_Avoid_: guard, idempotency check, short-circuit
_Note_: unrelated to the loader's **Skip**, which is one of the three reconcile actions.

## Exit codes

The three-state contract, which this repo owns: `taxi-pipeline` and `taxi-mssql` both
summarise a run as `0`, `1` or `2`, and these are the words for them. taxi-lab has no such
contract of its own and defers to these definitions. The individual tools underneath emit
more codes than three; those are in
[`docs/reference/exit-codes.md`](docs/reference/exit-codes.md).

**Clean** (`0`):
Everything was already current, or the run advanced the data directory without incident.

**Needs review** (`1`):
A human must decide something before the run can continue — in practice, normalize meeting
unacknowledged schema drift. Distinct from a run that broke.
_Avoid_: warning, soft failure, partial

**Operational failure** (`2`):
Something broke that nobody needs to adjudicate — an unreachable server, a full disk, a
backup that failed verification. Re-running is often the whole remedy.
_Avoid_: error, hard failure, fatal
