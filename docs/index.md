# taxi-seed

`taxi-seed` is one repo, one Python package with five tools (plus a shared
library) for working with the NYC Taxi & Limousine Commission (TLC) trip
record data set: a WAF-aware CloudFront downloader that recovers from block
pages (WAF = Web Application Firewall — the layer AWS uses to block traffic
that looks like a scraper), a schema-drift analyzer that combines column-name
heuristics with data-verified rename detection, a normalizer that halts on any
data loss unless the operator explicitly acknowledges the drift, a loader that
lands normalized parquet into a target database, and an orchestrator that
drives the download → normalize → load pipeline end-to-end. A
full-history mirror is roughly 40–100 GB of parquet (depending on how many of
the four series and how much history you mirror) and takes 6–10 hours to
download end-to-end on residential broadband, so the pipeline is designed to
be resumable, incremental, and cheap to re-run on a schedule rather than
something you kick off once and hope survives. The project is MIT-licensed.
The downloader was loosely based on
[`toddwschneider/nyc-taxi-data`](https://github.com/toddwschneider/nyc-taxi-data);
the other tools are original to this repo. See
[Acknowledgments](#acknowledgments) for details.

## Why this repo

- **WAF-aware downloading** — the CloudFront classifier distinguishes real
  `403 AccessDenied` responses (a missing object, retry is pointless) from HTML
  block pages served by AWS WAF (a transient rate limit, retry is mandatory),
  which is the single most common failure mode when scraping the TLC bucket at
  any real volume. When a block is detected the downloader walks a 30s → 90s → 270s
  exponential backoff ladder (capped at 3600s) to ride out the WAF window without
  hammering the origin, and the incremental catch-up mode stops the moment a
  locally-existing file is encountered so scheduled runs stay cheap even once
  the mirror is warm. The net effect is that a nightly cron survives WAF
  incidents unattended instead of paging you at 03:00.
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

## How it compares

| Feature            | this repo                                          | `toddwschneider/nyc-taxi-data`   | `duckdb httpfs`                          |
| ------------------ | -------------------------------------------------- | -------------------------------- | ---------------------------------------- |
| Primary use case   | resumable mirror + normalize + load                | one-shot Postgres load           | ad-hoc SQL over remote parquet           |
| Resumable download | yes; stop-on-local incremental catch-up            | one-shot `wget` loop             | no local mirror needed                   |
| WAF-aware retry    | classifier + 30s/90s/270s exponential backoff (capped 3600s) | none — fails on WAF block page   | N/A (single HTTP range request per scan) |
| Schema handling    | drift analyzer + rename-verified mapping YAML      | fixed columns; breaks on drift   | trusts remote schema per query           |
| Target database    | SQL Server (via DuckDB `mssql` extension)          | Postgres                         | DuckDB (in-process)                      |
| Install effort     | `uv sync`                                          | Postgres + shell + Ruby + client | single `duckdb` binary                   |

## Quick start

```bash
git clone https://github.com/andrekamman/taxi-seed.git
cd taxi-seed
uv sync
uv run taxi-download yellow --recent 3
```

!!! tip
    Downloads ~200 MB in 1–2 minutes on residential broadband. This Quick Start only exercises the downloader, but every tool in the repo lives in the same `uv sync`-managed environment; [Getting Started](getting-started.md) walks the full end-to-end path from clone to normalized parquet.

## Requirements

- Python 3.12 or 3.13, and [uv](https://github.com/astral-sh/uv) for Python environment management. Required by every tool in the repo, including the downloader.
- Disk sized to intent — see the [Downloader guide](guides/downloader.md#disk-sizing) for a sizing table.
- Individual tools list per-guide prerequisites (a target database for the loader, etc.).

## Where to next

- **[Getting Started](getting-started.md)** — a 10-minute end-to-end tutorial
  that walks from a clean laptop to a normalized parquet directory, covering
  clone, `uv sync`, a small `--recent` download, and the first normalizer run
  so you have a working pipeline before you dive into the deep-dive guides.
- **[Guides](guides/downloader.md)** — one deep-dive per tool. Start with the
  Downloader guide for WAF classifier internals, the backoff ladder, and disk
  sizing; the Normalizer and Drift analyzer guides cover each tool's flags,
  failure modes, and recommended production settings.
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
