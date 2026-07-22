# taxi

[![CI](https://github.com/andrekamman/taxi/actions/workflows/ci.yml/badge.svg)](https://github.com/andrekamman/taxi/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docs](https://img.shields.io/badge/docs-online-brightgreen)](https://andrekamman.github.io/taxi/)

`taxi` is one repo, four tools for working with the NYC Taxi & Limousine Commission (TLC) trip record data set: a WAF-aware CloudFront **downloader**, a **schema-drift** analyzer, a **normalize** step that halts on any data loss unless the operator explicitly acknowledges the drift, and a **k6-loadtest** rig that drives SQL Server from real parquet or a synthetic generator. The project is MIT-licensed. The downloader script was loosely based on [`toddwschneider/nyc-taxi-data`](https://github.com/toddwschneider/nyc-taxi-data); the other three tools are original to this repo. See [Acknowledgments](#acknowledgments) for details.

- **WAF-aware CloudFront downloader** with a 5 / 15 / 60 minute exponential backoff ladder and stop-on-local incremental catch-up so nightly crons stay cheap once the mirror is warm.
- **Normalizer that treats data loss as a first-class error** — missing columns, lossy casts, and silent renames halt the run; explicit `ack_date` acknowledgment is required before drift is written through.
- **K6-based SQL Server load tester** with both real-parquet and synthetic-generator modes so you can iterate on a `test.js` in seconds without waiting for a multi-gigabyte load.

**→ Full documentation: <https://andrekamman.github.io/taxi/>**

The site has the deep-dive guides, a cookbook of cross-cutting recipes, an architecture overview, and the design specs; this README stays intentionally short.

## Components

Four tools, one repo:

- [`downloader/`](downloader/) — bash CLI that mirrors the TLC CloudFront bucket to local parquet.
- [`schema-drift/`](schema-drift/) — Python CLI that reports column-name and column-shape drift across a mirror.
- [`normalize/`](normalize/) — Python CLI that rewrites a mirror to a single target schema, refusing to lose data.
- [`k6-loadtest/`](k6-loadtest/) — K6 rig plus a Python preprocessor that turns parquet (or synthetic data) into a self-contained load-test bundle.

Each component has a short `README.md` that points at the guide on the site; the guide is authoritative.

## Quick start

```bash
git clone https://github.com/andrekamman/taxi.git
cd taxi
./downloader/download_taxi_data.sh --recent 3 yellow
```

Downloads ~200 MB in 1–2 minutes on residential broadband. This Quick Start only exercises the downloader (bash + curl — no Python needed). To use the other three tools (schema-drift, normalize, k6-preprocess) you'll additionally need `uv sync` for the Python side; the [Getting Started tutorial](https://andrekamman.github.io/taxi/getting-started/) walks the full end-to-end path from clone to normalized parquet.

A full-history mirror is roughly 40 GB and takes 6–10 hours end-to-end; the downloader is designed to be resumable, incremental, and cheap to re-run on a schedule rather than something you kick off once and hope survives.

## Requirements

- `bash` 4+, `curl` (Git for Windows on Windows). Required by the downloader; that's all the downloader needs.
- Python 3.12 or 3.13, [uv](https://github.com/astral-sh/uv). Required by the other three tools (schema-drift, normalize, k6-preprocess), which are all Python.
- Disk sized to intent — see the [Downloader guide](https://andrekamman.github.io/taxi/guides/downloader/#disk-sizing).
- Individual tools list per-guide prerequisites (Go 1.22+ for the K6 binary build, SQL Server for load testing, etc.).

Everything runs on macOS, Linux, and Windows (via Git Bash). CI runs the test suite and the strict docs build on every PR.

## Documentation map

The [documentation site](https://andrekamman.github.io/taxi/) is the source of truth. High-level sections:

- **[Getting Started](https://andrekamman.github.io/taxi/getting-started/)** — 10-minute end-to-end walkthrough from clone to normalized parquet.
- **[Guides](https://andrekamman.github.io/taxi/guides/downloader/)** — one deep-dive per tool (Downloader, Schema Drift, Normalize, K6 Load Test).
- **[Cookbook](https://andrekamman.github.io/taxi/cookbook/)** — cross-cutting recipes (nightly cron, DuckDB `httpfs` querying, corporate proxy, load-testing at scale).
- **[Architecture](https://andrekamman.github.io/taxi/architecture/)** — how the four tools fit together end-to-end.
- **[Reference](https://andrekamman.github.io/taxi/reference/configuration/)** — configuration keys and exit codes.
- **[Design Specs](https://andrekamman.github.io/taxi/superpowers/specs/2026-07-19-monorepo-restructure-design/)** — the "why is this shaped this way?" trail.

## Contributing

PRs welcome. See [`docs/contributing.md`](docs/contributing.md) — also published as the [Contributing page](https://andrekamman.github.io/taxi/contributing/) on the site — for the dev-setup, test, and PR-checklist details.

## License

MIT — see [`LICENSE`](LICENSE).

## Acknowledgments

The **downloader** shell script was loosely based on [`toddwschneider/nyc-taxi-data`](https://github.com/toddwschneider/nyc-taxi-data) (MIT) — specifically its convention for organizing TLC parquet by type and year. The rest of the repo (schema-drift analyzer, normalizer, k6-loadtest rig) is original work and shares no code with Todd's project. See [`THIRD_PARTY_NOTICES`](THIRD_PARTY_NOTICES) for the full attribution.
