# taxi-seed

[![CI](https://github.com/andrekamman/taxi-seed/actions/workflows/ci.yml/badge.svg)](https://github.com/andrekamman/taxi-seed/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/taxi-seed)](https://pypi.org/project/taxi-seed/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docs](https://img.shields.io/badge/docs-online-brightgreen)](https://andrekamman.github.io/taxi-seed/)

The New York City Taxi and Limousine Commission (TLC) publishes a record of every taxi and for-hire vehicle trip in the city. The records start in 2009 and arrive as monthly parquet files.

`taxi-seed` mirrors that data set, normalizes it, and loads it into a database. It is one repo and one Python package holding five tools plus a shared library — see [Components](#components) for each one. The project is MIT-licensed. The downloader was loosely based on [`toddwschneider/nyc-taxi-data`](https://github.com/toddwschneider/nyc-taxi-data); the other tools are original to this repo. See [Acknowledgments](#acknowledgments) for details.

- **A downloader that survives AWS WAF.** WAF is the Web Application Firewall AWS uses to block traffic that looks like a scraper, and it is the failure you hit first when you mirror the TLC bucket at volume. The downloader tells a real `403 AccessDenied` from a WAF block page, waits 30s, then 90s, then 270s across four attempts, and stops the moment it meets a file it already has. A nightly cron therefore stays cheap once the mirror is warm.
- **A normalizer that treats data loss as an error.** Missing columns, lossy casts, and silent renames all halt the run. An operator must acknowledge the drift with an `ack_date` before it is written through.
- **A loader and an orchestrator for the whole pipeline.** The loader lands normalized parquet in a target database, page-compressed. The orchestrator drives download → normalize → load as one scheduled run.

**→ Full documentation: <https://andrekamman.github.io/taxi-seed/>**

The site has the deep-dive guides, a cookbook of cross-cutting recipes, an architecture overview, and the design specs; this README stays intentionally short.

## Components

One repo, five tools plus a shared library:

- [`downloader/`](downloader/) — Python CLI (`taxi-download`) that mirrors the TLC CloudFront bucket to local parquet.
- [`schema-drift/`](schema-drift/) — Python CLI that reports column-name and column-shape drift across a mirror.
- [`normalize/`](normalize/) — Python CLI that rewrites a mirror to a single target schema, refusing to lose data.
- [`loader/`](loader/) — Python CLI that loads normalized parquet into a target database.
- [`orchestrator/`](orchestrator/) — Python CLI that drives download → normalize → load as one pipeline.
- [`shared/`](shared/) — common library code (`sql_generator.py` for `CREATE TABLE` DDL generation, `type_mapping.py` for DuckDB→SQL Server type mapping) used across the tools.

Each component has a short `README.md` that points at the guide on the site; the guide is authoritative.

## Install

`taxi-seed` is published to PyPI as one distribution. It puts six commands on your PATH — `taxi-download`, `schema-drift`, `normalize`, `taxi-load`, `taxi-run`, and `taxi-curate-mappings`:

```bash
uv tool install taxi-seed     # isolated env, every CLI on PATH
pip install taxi-seed         # or into a venv of your own
```

Releases are cut by tagging `main` (`vX.Y.Z` → PyPI, anything else `v*` → TestPyPI) via `.github/workflows/release.yml`. See the [Installation page](https://andrekamman.github.io/taxi-seed/install/) for upgrading, prereleases from TestPyPI, and the one caveat for installed users — the curated normalize mappings live in this repo, not in the wheel — and the [Releasing runbook](https://andrekamman.github.io/taxi-seed/operations/releasing/) for the maintainer side.

## Quick start

```bash
git clone https://github.com/andrekamman/taxi-seed.git
cd taxi-seed
uv sync
uv run taxi-download yellow --recent 3
```

Downloads ~200 MB in 1–2 minutes on residential broadband. This Quick Start only exercises the downloader. All tools live in one `uv sync`-managed environment; the [Getting Started tutorial](https://andrekamman.github.io/taxi-seed/getting-started/) walks the full end-to-end path from clone to normalized parquet. Installed from PyPI instead? Same commands, without the `uv run` prefix.

A full-history mirror is roughly 40–100 GB (depending on how many of the four trip types and how much history you mirror) and takes 6–10 hours end-to-end. The downloader is therefore built to be resumable, incremental, and cheap to re-run on a schedule.

## Requirements

- Python 3.12 or 3.13. [uv](https://github.com/astral-sh/uv) for the clone workflow; a PyPI install works with plain `pip`.
- Disk sized to intent — see the [Downloader guide](https://andrekamman.github.io/taxi-seed/guides/downloader/#disk-sizing).
- Individual tools list per-guide prerequisites (a target database for the loader, etc.).

Everything runs on macOS, Linux, and Windows (via Git Bash). CI runs the test suite and the strict docs build on every PR.

## Documentation map

The [documentation site](https://andrekamman.github.io/taxi-seed/) is the source of truth. High-level sections:

- **[Installation](https://andrekamman.github.io/taxi-seed/install/)** — installing the released package from PyPI vs working from a clone.
- **[Getting Started](https://andrekamman.github.io/taxi-seed/getting-started/)** — 10-minute end-to-end walkthrough from clone to normalized parquet.
- **[Guides](https://andrekamman.github.io/taxi-seed/guides/downloader/)** — one deep-dive per tool: Downloader, Schema Drift, Normalize, Loader, Orchestrator.
- **[Cookbook](https://andrekamman.github.io/taxi-seed/cookbook/)** — cross-cutting recipes (nightly cron, DuckDB `httpfs` querying, corporate proxy).
- **[Architecture](https://andrekamman.github.io/taxi-seed/architecture/)** — how the tools fit together end-to-end.
- **[Reference](https://andrekamman.github.io/taxi-seed/reference/configuration/)** — configuration keys and exit codes.
- **[Design Specs](https://andrekamman.github.io/taxi-seed/superpowers/specs/2026-07-19-monorepo-restructure-design/)** — the "why is this shaped this way?" trail.

## Contributing

PRs welcome. See [`docs/contributing.md`](docs/contributing.md) — also published as the [Contributing page](https://andrekamman.github.io/taxi-seed/contributing/) on the site — for the dev-setup, test, and PR-checklist details.

## License

MIT — see [`LICENSE`](LICENSE).

## Acknowledgments

The **downloader** was loosely based on [`toddwschneider/nyc-taxi-data`](https://github.com/toddwschneider/nyc-taxi-data) (MIT) — specifically its convention for organizing TLC parquet by type and year. The rest of the repo (schema-drift analyzer, normalizer, loader, orchestrator) is original work and shares no code with Todd's project. See [`THIRD_PARTY_NOTICES`](THIRD_PARTY_NOTICES) for the full attribution.
