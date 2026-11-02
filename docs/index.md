# taxi-seed

The New York City Taxi and Limousine Commission (TLC) publishes a record of
every taxi and for-hire vehicle trip in the city. The records start in 2009 and
arrive as monthly parquet files.

**taxi-seed exists to produce data to ingest.** Its primary purpose is to give a
lakehouse — or any other system you need to test at a realistic scale — a large,
real, public data set to load, repeatedly and reproducibly. The data set is
useful more widely, but that is the goal the tools are shaped around.

So `taxi-seed` mirrors that data set, normalizes it, and loads it into a
database. It is one repo and one Python package holding six tools plus a shared
library:

| Tool | Command | What it does |
|---|---|---|
| **downloader** | `taxi-download` | Mirrors the TLC CloudFront bucket to local parquet, and recovers when AWS WAF blocks it. |
| **schema-drift** | `taxi-schema-drift` | Reports column-name and column-shape drift across a mirror. |
| **normalize** | `taxi-normalize` | Rewrites a mirror to one target schema. Halts if that would lose data. |
| **loader** | `taxi-load` | Lands normalized parquet in a target database, page-compressed. |
| **pipeline** | `taxi-pipeline` | Drives download → normalize → load as one portable run. |
| **seeder** | `taxi-mssql` | Turns one data directory into a complete, presized, backed-up SQL Server data set. |

The seeder is arguably the headline: it is the one command that produces a
*complete* SQL Server copy of the data set rather than a stage of one. Start at
the [taxi-mssql guide](guides/mssql.md) if that is what you came for.

A full-history mirror is roughly 40–100 GB of parquet, depending on how many of
the four trip types you take and how much history. It takes 6–10 hours to download
on residential broadband, so every tool here is built to be resumable,
incremental, and cheap to re-run on a schedule.

The project is MIT-licensed. The downloader was loosely based on
[`toddwschneider/nyc-taxi-data`](https://github.com/toddwschneider/nyc-taxi-data);
the other tools are original to this repo. See
[Acknowledgments](#acknowledgments) for details.

## Why this repo

- **WAF-aware downloading** — the CloudFront classifier distinguishes real
  `403 AccessDenied` responses (a missing object, retry is pointless) from HTML
  block pages served by AWS WAF (a transient rate limit, retry is mandatory),
  which is the failure you hit first when you scrape the TLC bucket at any real
  volume. On a block, the downloader makes four attempts and waits 30s, then
  90s, then 270s between them, which rides out the WAF window without hammering
  the origin. Incremental catch-up stops the moment it meets a file that already
  exists locally, so scheduled runs stay cheap once the mirror is warm. A nightly
  cron therefore survives WAF incidents unattended.
- **Data-loss-is-an-error normalization** — the normalizer treats any missing
  column, renamed column, or lossy type coercion as a hard failure rather than
  something to paper over. That means silent data loss on lossy `DOUBLE →
  BIGINT` casts, dropped payment-type columns, or a vendor quietly renaming
  `pickup_datetime` to `tpep_pickup_datetime` all halt the run instead of
  producing plausible-looking-but-wrong output. `target` is the only other
  required top-level key in the mapping YAML besides `ack_date` (required
  within each lossy-cast/data-loss entry), and bootstrap + amend semantics mean
  scheduled runs auto-detect newly-introduced drift and append review items
  to the mapping instead of dropping the affected columns; the drift analyzer
  uses DuckDB for parquet introspection so you can trust its verdict on
  columns that changed shape mid-file.
- **Loader + pipeline for the full chain** — the loader lands normalized parquet
  into a target database, and `taxi-pipeline` drives download → normalize → load
  as a single scheduled run, so a nightly cron can go from "check for new
  months" to "target database is up to date" without hand-wiring the individual
  tools together. DuckDB is pinned exactly and its `mssql` extension is
  vendored and signature-verified, because the community registry rebuilds
  underneath an unchanged version — see
  [the vendored extension](guides/load.md#the-vendored-extension).
- **A seeder that finishes the job** — `taxi-mssql` adds what a portable
  pipeline cannot: a presized data file and log file on a known path, a memory
  cap, `SIMPLE` recovery, Ola Hallengren's maintenance solution, and a
  compressed, verified backup. One data directory in, a complete deliverable set
  out, resumable across runs.

## Quick start

`taxi-seed` is published to [PyPI](https://pypi.org/project/taxi-seed/) as one distribution, putting all eight commands on your PATH:

=== "Install the release"

    ```bash
    uv tool install taxi-seed        # or: pip install taxi-seed
    taxi-download yellow --recent 3
    ```

=== "From a clone"

    ```bash
    git clone https://github.com/andrekamman/taxi-seed.git
    cd taxi-seed
    uv sync
    uv run taxi-download yellow --recent 3
    ```

!!! tip
    Downloads ~200 MB in 1–2 minutes on residential broadband. This Quick Start only exercises the downloader, but every tool ships in the same distribution; [Getting Started](getting-started.md) walks the full end-to-end path to normalized parquet.

Examples throughout these docs are written for the clone workflow and prefix commands with `uv run` — drop the prefix if you installed from PyPI. See [Installation](install.md) for both paths, upgrading, prereleases from TestPyPI, and the one caveat that matters for installed users: the curated normalize mappings live in the repo, not in the wheel.

## Requirements

- Python 3.12 or 3.13. [uv](https://github.com/astral-sh/uv) is used for the clone workflow and throughout these docs; a PyPI install works with plain `pip` and needs no `uv`.
- Disk sized to intent — see the [Downloader guide](guides/download.md#disk-sizing) for a sizing table.
- Individual tools list per-guide prerequisites (a target database for the loader, etc.).

## Where to next

- **[Installation](install.md)** — installing the released package from PyPI
  (`uv tool install`, `pip`, `uvx`), upgrading, installing a prerelease from
  TestPyPI, and how an installed workflow differs from a clone.
- **[Getting Started](getting-started.md)** — a 10-minute end-to-end tutorial
  that walks from a clean laptop to a normalized parquet directory, covering
  clone, `uv sync`, a small `--recent` download, and the first normalizer run
  so you have a working pipeline before you dive into the deep-dive guides.
- **[Guides](guides/download.md)** — one deep-dive per tool: `taxi-download`,
  `taxi-schema-drift`, `taxi-normalize`, `taxi-load`, `taxi-pipeline`, and
  [`taxi-mssql`](guides/mssql.md). Start with the downloader guide for the WAF
  classifier, the backoff waits, and disk sizing. Each of the others covers that
  tool's flags, failure modes, and production settings.
- **[Cookbook](cookbook.md)** — cross-cutting recipes that combine multiple
  tools: a nightly cron that mirrors + normalizes unattended, querying the
  mirror with DuckDB `httpfs` without a full load, and running the whole
  pipeline behind a corporate proxy.

## Acknowledgments

The **downloader** was loosely based on
[`toddwschneider/nyc-taxi-data`](https://github.com/toddwschneider/nyc-taxi-data)
(MIT) — specifically its convention for organizing TLC parquet by type and
year. The rest of the repo (schema-drift analyzer, normalizer, loader, pipeline,
seeder) is original work and shares no code with Todd's project.

`mssql/sql/MaintenanceSolution.sql` is **Ola Hallengren's SQL Server Maintenance
Solution** (MIT), vendored unmodified and used for the seeder's backup stage. See
[THIRD_PARTY_NOTICES](https://github.com/andrekamman/taxi-seed/blob/main/THIRD_PARTY_NOTICES)
for the full attribution.
