# taxi-seed

The New York City Taxi and Limousine Commission (TLC) publishes a record of
every taxi and for-hire vehicle trip in the city. The records start in 2009 and
arrive as monthly parquet files.

`taxi-seed` mirrors that data set, normalizes it, and loads it into a database.
It is one repo and one Python package holding five tools plus a shared library:

| Tool | What it does |
|---|---|
| **downloader** | Mirrors the TLC CloudFront bucket to local parquet, and recovers when AWS WAF blocks it. |
| **schema-drift** | Reports column-name and column-shape drift across a mirror. |
| **normalize** | Rewrites a mirror to one target schema. Halts if that would lose data. |
| **loader** | Lands normalized parquet in a target database, page-compressed. |
| **orchestrator** | Drives download → normalize → load as one pipeline. |

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
- **Loader + orchestrator for the full pipeline** — the loader lands
  normalized parquet into a target database, and the orchestrator drives
  download → normalize → load as a single scheduled run, so a
  nightly cron can go from "check for new months" to "target database is
  up to date" without hand-wiring the individual tools together.

## Quick start

`taxi-seed` is published to [PyPI](https://pypi.org/project/taxi-seed/) as one distribution, putting all six commands on your PATH:

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
- Disk sized to intent — see the [Downloader guide](guides/downloader.md#disk-sizing) for a sizing table.
- Individual tools list per-guide prerequisites (a target database for the loader, etc.).

## Where to next

- **[Installation](install.md)** — installing the released package from PyPI
  (`uv tool install`, `pip`, `uvx`), upgrading, installing a prerelease from
  TestPyPI, and how an installed workflow differs from a clone.
- **[Getting Started](getting-started.md)** — a 10-minute end-to-end tutorial
  that walks from a clean laptop to a normalized parquet directory, covering
  clone, `uv sync`, a small `--recent` download, and the first normalizer run
  so you have a working pipeline before you dive into the deep-dive guides.
- **[Guides](guides/downloader.md)** — one deep-dive per tool: Downloader,
  Schema Drift, Normalize, Loader, Orchestrator. Start with the Downloader guide
  for the WAF classifier, the backoff waits, and disk sizing. Each of the others
  covers that tool's flags, failure modes, and production settings.
- **[Cookbook](cookbook.md)** — cross-cutting recipes that combine multiple
  tools: a nightly cron that mirrors + normalizes unattended, querying the
  mirror with DuckDB `httpfs` without a full load, and running the whole
  pipeline behind a corporate proxy.

## Acknowledgments

The **downloader** was loosely based on
[`toddwschneider/nyc-taxi-data`](https://github.com/toddwschneider/nyc-taxi-data)
(MIT) — specifically its convention for organizing TLC parquet by type and
year. The rest of the repo (schema-drift analyzer, normalizer, loader,
orchestrator) is original work and shares no code with Todd's project. See
[THIRD_PARTY_NOTICES](https://github.com/andrekamman/taxi-seed/blob/main/THIRD_PARTY_NOTICES)
for the full attribution.
