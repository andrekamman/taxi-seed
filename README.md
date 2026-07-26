# taxi-seed

[![CI](https://github.com/andrekamman/taxi-seed/actions/workflows/ci.yml/badge.svg)](https://github.com/andrekamman/taxi-seed/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docs](https://img.shields.io/badge/docs-online-brightgreen)](https://andrekamman.github.io/taxi-seed/)

`taxi-seed` is one repo, one Python package with five tools (plus a shared library) for working with the NYC Taxi & Limousine Commission (TLC) trip record data set: a WAF-aware CloudFront **downloader** (WAF = Web Application Firewall — the layer AWS uses to block traffic that looks like a scraper), a **schema-drift** analyzer, a **normalize** step that halts on any data loss unless the operator explicitly acknowledges the drift, a **loader** that lands normalized parquet into a target database, and an **orchestrator** that drives the whole download → analyze → normalize → load pipeline end-to-end. The project is MIT-licensed. The downloader was loosely based on [`toddwschneider/nyc-taxi-data`](https://github.com/toddwschneider/nyc-taxi-data); the other tools are original to this repo. See [Acknowledgments](#acknowledgments) for details.

- **WAF-aware CloudFront downloader** with a 5 / 15 / 60 minute exponential backoff ladder and stop-on-local incremental catch-up so nightly crons stay cheap once the mirror is warm.
- **Normalizer that treats data loss as a first-class error** — missing columns, lossy casts, and silent renames halt the run; explicit `ack_date` acknowledgment is required before drift is written through.
- **Loader + orchestrator** that land normalized parquet into a target database and drive the full pipeline end-to-end on a schedule.

**→ Full documentation: <https://andrekamman.github.io/taxi-seed/>**

The site has the deep-dive guides, a cookbook of cross-cutting recipes, an architecture overview, and the design specs; this README stays intentionally short.

## Components

One repo, five tools plus a shared library:

- [`downloader/`](downloader/) — Python CLI (`taxi-download`) that mirrors the TLC CloudFront bucket to local parquet.
- [`schema-drift/`](schema-drift/) — Python CLI that reports column-name and column-shape drift across a mirror.
- [`normalize/`](normalize/) — Python CLI that rewrites a mirror to a single target schema, refusing to lose data.
- [`loader/`](loader/) — Python CLI that loads normalized parquet into a target database.
- [`orchestrator/`](orchestrator/) — Python CLI that drives download → analyze → normalize → load as one pipeline.
- [`shared/`](shared/) — common library code (parquet conventions, DuckDB helpers) used across the tools.

Each component has a short `README.md` that points at the guide on the site; the guide is authoritative.

## Quick start

```bash
git clone https://github.com/andrekamman/taxi-seed.git
cd taxi-seed
uv sync
uv run taxi-download yellow --recent 3
```

Downloads ~200 MB in 1–2 minutes on residential broadband. This Quick Start only exercises the downloader. All tools live in one `uv sync`-managed environment; the [Getting Started tutorial](https://andrekamman.github.io/taxi-seed/getting-started/) walks the full end-to-end path from clone to normalized parquet.

A full-history mirror is roughly 40 GB and takes 6–10 hours end-to-end; the downloader is designed to be resumable, incremental, and cheap to re-run on a schedule rather than something you kick off once and hope survives.

## Requirements

- Python 3.12 or 3.13, [uv](https://github.com/astral-sh/uv). Required by every tool in the repo, including the downloader.
- Disk sized to intent — see the [Downloader guide](https://andrekamman.github.io/taxi-seed/guides/downloader/#disk-sizing).
- Individual tools list per-guide prerequisites (a target database for the loader, etc.).

Everything runs on macOS, Linux, and Windows (via Git Bash). CI runs the test suite and the strict docs build on every PR.

## Documentation map

The [documentation site](https://andrekamman.github.io/taxi-seed/) is the source of truth. High-level sections:

- **[Getting Started](https://andrekamman.github.io/taxi-seed/getting-started/)** — 10-minute end-to-end walkthrough from clone to normalized parquet.
- **[Guides](https://andrekamman.github.io/taxi-seed/guides/downloader/)** — one deep-dive per tool (Downloader, Schema Drift, Normalize).
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
