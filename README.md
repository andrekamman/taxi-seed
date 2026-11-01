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

One repo, six tools plus a shared library:

- [`download/`](download/) — Python CLI (`taxi-download`) that mirrors the TLC CloudFront bucket to local parquet.
- [`schema-drift/`](schema-drift/) — Python CLI (`taxi-schema-drift`) that reports column-name and column-shape drift across a mirror.
- [`normalize/`](normalize/) — Python CLI (`taxi-normalize`) that rewrites a mirror to a single target schema, refusing to lose data.
- [`load/`](load/) — Python CLI (`taxi-load`) that loads normalized parquet into a target database.
- [`pipeline/`](pipeline/) — Python CLI (`taxi-pipeline`) that drives download → normalize → load as one pipeline.
- [`mssql/`](mssql/) — Docker Compose stack (`taxi-mssql`) that turns one host folder into a complete, backed-up SQL Server data set.
- [`shared/`](shared/) — common library code (`sql_generator.py` for `CREATE TABLE` DDL generation, `type_mapping.py` for DuckDB→SQL Server type mapping) used across the tools.

Each component has a short `README.md` that points at the guide on the site; the guide is authoritative.

## Install

`taxi-seed` is published to PyPI as one distribution. It puts seven commands on your PATH, all `taxi-` prefixed — `taxi-download`, `taxi-schema-drift`, `taxi-normalize`, `taxi-load`, `taxi-pipeline`, `taxi-curate-mappings`, and `taxi-vendor-extension`:

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

## A complete SQL Server data set

[`mssql/`](mssql/) is a Compose stack. Give it one folder on your host and it fills that
folder with the whole **deliverable set** — raw parquet, normalized parquet, a presized
data file and log file, and a compressed, verified backup. Run it again on the same folder
and it continues from where the last run stopped; every step reads the folder to decide
whether it has work to do, so there is no state file to go stale.

Two settings are required, in `mssql/.env`, which is gitignored:

```bash
cat > mssql/.env <<'ENV'
TAXI_DATA_DIR=/srv/taxi
MSSQL_PASSWORD=change-me-Str0ng!
ENV
```

`TAXI_DATA_DIR` is the host folder to fill, and it must exist first. `MSSQL_PASSWORD` must
satisfy SQL Server's complexity policy or the `mssql` container exits during startup.

```bash
cd mssql
docker compose up --build --abort-on-container-exit --exit-code-from seeder
```

Keep both flags. They stop the whole stack as soon as the seeder exits, and they make `$?`
the seeder's own exit code — `0` clean, `1` needs review (usually schema drift to
acknowledge), `2` operational failure. Without them the `mssql` service keeps running
after the work finishes, and you are left watching a SQL Server log with no signal that
the run ended and no exit code.

The default run covers all four trip types over the full history: expect several hours and
a large database on the first run, and a near no-op on later ones.

## Requirements

- Python 3.12 or 3.13. [uv](https://github.com/astral-sh/uv) for the clone workflow; a PyPI install works with plain `pip`.
- Disk sized to intent — see the [Downloader guide](https://andrekamman.github.io/taxi-seed/guides/download/#disk-sizing).
- Individual tools list per-guide prerequisites (a target database for the loader, etc.).

Everything runs on macOS, Linux, and Windows (via Git Bash). CI runs the test suite on Python 3.12 and 3.13, the loader and pipeline integration tests against a real SQL Server, and a strict docs build, on every PR.

## Documentation map

The [documentation site](https://andrekamman.github.io/taxi-seed/) is the source of truth. High-level sections:

- **[Installation](https://andrekamman.github.io/taxi-seed/install/)** — installing the released package from PyPI vs working from a clone.
- **[Getting Started](https://andrekamman.github.io/taxi-seed/getting-started/)** — 10-minute end-to-end walkthrough from clone to normalized parquet.
- **[Guides](https://andrekamman.github.io/taxi-seed/guides/download/)** — one deep-dive per tool: Downloader, Schema Drift, Normalize, Loader, Orchestrator.
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
