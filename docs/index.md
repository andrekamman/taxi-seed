# taxi

`taxi` is one repo, four tools for working with the NYC Taxi & Limousine
Commission (TLC) trip record data set: a WAF-aware CloudFront downloader that
recovers from block pages, a schema-drift analyzer that combines column-name
heuristics with data-verified rename detection, a K6-based SQL Server load
tester with real and synthetic modes, and a normalizer that halts on any data
loss unless the operator explicitly acknowledges the drift. A full-history
mirror is roughly 40 GB of parquet and takes 6–10 hours to download end-to-end
on residential broadband, so the pipeline is designed to be resumable,
incremental, and cheap to re-run on a schedule rather than something you kick
off once and hope survives. The project is MIT-licensed and grew out of — but
improves substantially on —
[`toddwschneider/nyc-taxi-data`](https://github.com/toddwschneider/nyc-taxi-data),
replacing its one-shot loader script with an auditable pipeline that survives
WAF rate limits, schema changes, and multi-year backfills.

## Why this repo

- **WAF-aware downloading** — the CloudFront classifier distinguishes real
  `403 AccessDenied` responses (a missing object, retry is pointless) from HTML
  block pages served by AWS WAF (a transient rate limit, retry is mandatory),
  which is the single most common failure mode when scraping the TLC bucket at
  any real volume. When a block is detected the downloader walks a 5 → 15 → 60
  minute exponential backoff ladder to ride out the WAF window without
  hammering the origin, and the incremental catch-up mode stops the moment a
  locally-existing file is encountered so scheduled runs stay cheap even once
  the mirror is warm. The net effect is that a nightly cron survives WAF
  incidents unattended instead of paging you at 03:00.
- **Data-loss-is-an-error normalization** — the normalizer treats any missing
  column, renamed column, or lossy type coercion as a hard failure rather than
  something to paper over. That means silent data loss on lossy `DOUBLE →
  BIGINT` casts, dropped payment-type columns, or a vendor quietly renaming
  `pickup_datetime` to `tpep_pickup_datetime` all halt the run instead of
  producing plausible-looking-but-wrong output. `ack_date` is the only
  required field in the mapping YAML, and bootstrap + amend semantics mean
  scheduled runs auto-detect newly-introduced drift and append review items
  to the mapping instead of dropping the affected columns; the drift analyzer
  uses DuckDB for parquet introspection so you can trust its verdict on
  columns that changed shape mid-file.
- **Realistic K6 load testing** — the SQL Server load tester runs against
  either real parquet trip data or a synthetic generator that starts
  instantly, so you can iterate on a `test.js` in seconds without waiting for
  a multi-gigabyte load. A Python preprocessor turns parquet files into a
  self-contained K6 test bundle — a DDL script, chunked JSON payloads, and a
  `test.js` entry point — so a benchmark is one `k6 run` away with no
  external state. The rig uses [K6](https://k6.io/) with the `xk6-sql`
  extension so you drive real SQL Server connections and get K6's built-in
  percentile reporting on top.

## How it compares

| Feature            | this repo                                          | `toddwschneider/nyc-taxi-data`   | `duckdb httpfs`                          |
| ------------------ | -------------------------------------------------- | -------------------------------- | ---------------------------------------- |
| Primary use case   | resumable mirror + normalize + benchmark           | one-shot Postgres load           | ad-hoc SQL over remote parquet           |
| Resumable download | yes; stop-on-local incremental catch-up            | one-shot `wget` loop             | no local mirror needed                   |
| WAF-aware retry    | classifier + 5/15/60 min exponential backoff       | none — fails on WAF block page   | N/A (single HTTP range request per scan) |
| Schema handling    | drift analyzer + rename-verified mapping YAML      | fixed columns; breaks on drift   | trusts remote schema per query           |
| Target database    | any (parquet-first) + SQL Server load-test bundles | Postgres                         | DuckDB (in-process)                      |
| Load testing       | K6 bundle: DDL + chunked JSON + `test.js`          | none                             | none                                     |
| Install effort     | `uv sync`; Go 1.22+ only if you build the K6 rig   | Postgres + shell + Ruby + client | single `duckdb` binary                   |

## Quick start

```bash
git clone https://github.com/andrekamman/taxi.git
cd taxi
./downloader/download_taxi_data.sh --recent 3 yellow
```

!!! tip
    Downloads ~200 MB in 1–2 minutes on residential broadband. See [Getting Started](getting-started.md) for the full end-to-end path from clone to normalized parquet.

## Requirements

- Python 3.12 or 3.13.
- [uv](https://github.com/astral-sh/uv) for Python environment management.
- `bash` 4+ and `curl` (macOS/Linux ship both; on Windows install [Git for Windows](https://gitforwindows.org/) and run in Git Bash).
- Disk sized to intent — see the [Downloader guide](guides/downloader.md#disk-sizing) for a sizing table.
- Individual tools list per-guide prerequisites (Go 1.22+ for the K6 build, SQL Server for load testing, etc.).

## Where to next

- **[Getting Started](getting-started.md)** — a 10-minute end-to-end tutorial
  that walks from a clean laptop to a normalized parquet directory, covering
  clone, `uv sync`, a small `--recent` download, and the first normalizer run
  so you have a working pipeline before you dive into the deep-dive guides.
- **[Guides](guides/downloader.md)** — one deep-dive per tool. Start with the
  Downloader guide for WAF classifier internals, the backoff ladder, and disk
  sizing; the Normalizer, Drift analyzer, and Load-tester guides cover each
  tool's flags, failure modes, and recommended production settings.
- **[Cookbook](cookbook.md)** — cross-cutting recipes that combine multiple
  tools: a nightly cron that mirrors + normalizes unattended, querying the
  mirror with DuckDB `httpfs` without a full load, load-testing normalized
  data at scale, and running the whole pipeline behind a corporate proxy.

## Acknowledgments

Originally inspired by
[`toddwschneider/nyc-taxi-data`](https://github.com/toddwschneider/nyc-taxi-data)
(MIT), whose one-shot Postgres loader was the reference point for what a
public TLC pipeline should look like end-to-end. This repo diverges from
Todd's in scope: instead of a single-target load script it is a parquet-first
mirror-plus-normalize-plus-benchmark pipeline, and the schema-drift heuristic
— column-name matching cross-validated against actual column data to catch
renames the analyzer would otherwise miss — was developed here rather than
inherited. See
[THIRD_PARTY_NOTICES](https://github.com/andrekamman/taxi/blob/main/THIRD_PARTY_NOTICES)
for the full attribution.
