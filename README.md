# taxi

Tools for downloading, analyzing, and load-testing with NYC TLC taxi trip data.

## Components

- **[downloader/](downloader/)** — WAF-aware bulk downloader for TLC parquet files, with exponential backoff and boundary auto-termination per data type.
- **[schema-drift/](schema-drift/)** — Analyzer that detects and reports schema changes across TLC parquet files over time, with name-based and data-driven rename detection.
- **[k6-loadtest/](k6-loadtest/)** — K6-based SQL Server load tester. Preprocesses parquet (or generates synthetic data) into K6-compatible input.

See each component's README for install and usage.

## Install

```bash
uv sync
```

This installs `k6-preprocess` and `schema-drift` as commands. The downloader is a standalone bash script — no install step required.

## Acknowledgments

Originally inspired by [toddwschneider/nyc-taxi-data](https://github.com/toddwschneider/nyc-taxi-data) (MIT). See [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES).
