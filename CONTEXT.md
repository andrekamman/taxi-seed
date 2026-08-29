# taxi-seed

taxi-seed mirrors the NYC TLC trip record data set, normalizes it, and loads it into a
database. This file is the glossary — use these words in code, comments, commit messages,
and docs. The consumer repo
[`taxi-lab`](https://github.com/andrekamman/taxi-lab) keeps a matching `CONTEXT.md`; terms
defined in both must stay in step.

## The data set

**Trip record**:
One row of the NYC Taxi and Limousine Commission's public data — a single taxi or
for-hire vehicle journey. The records start in 2009 and are published as monthly parquet
files.
_Avoid_: ride, journey, trip event

**Trip type**:
One of the four services the TLC publishes separately: `yellow`, `green`, `fhv`, `fhvhv`.
_Avoid_: data type, series, taxi type, category
_Note_: the code identifier is `data_type` and stays that way. Three strings the tools
print still say "data type" (`normalize/src/taxi_normalize/cli.py`,
`schema-drift/src/schema_drift/report.py` twice). Docs quote that output verbatim, so
those quotes keep the old word until the strings themselves change.

**Mirror**:
The local copy of the published parquet, under `raw/`. Also the verb for making it.
_Avoid_: download folder, cache, sync, scrape

**Data directory**:
The root folder holding `raw/`, `raw-normalized/`, and `normalize/mappings/`. Every tool
takes it as `--data-dir`.
_Avoid_: data dir, workspace, output folder, root

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
`(data_type, year, month)`. The loader reconciles against it to decide what to do.
_Avoid_: manifest (unqualified), tracking table, state table

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

## Orchestrator

**Pipeline**:
download → normalize → load, run per trip type. `taxi-run` drives it.
_Avoid_: workflow, job, chain, ETL

**Needs review**:
A run that stopped because a human must decide something — in practice, normalize meeting
unacknowledged drift. Distinct from a run that broke. Per-tool exit codes are in
[`docs/reference/exit-codes.md`](docs/reference/exit-codes.md).
_Avoid_: warning, soft failure, partial
