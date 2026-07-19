# downloader

Bash script that mirrors NYC TLC parquet trip data from CloudFront to a local `raw/` directory.

## What makes it different

- **WAF-aware retry classifier** — distinguishes CloudFront WAF blocks (HTML error page, or 429/503) from "file not published yet" (403 with S3-style AccessDenied XML). Naive downloaders treat both as generic failure and either false-positive on rate limits or false-negative on missing files.
- **Exponential backoff** on real rate-limit hits: 5 min → 15 min → 60 min, resets on any successful download.
- **Boundary auto-termination** — walks each series chronologically forward, stops cleanly when it reaches the end of published data for that type, then moves on to the next type.
- **Parquet validation** — verifies PAR1 magic bytes at both head and tail of every downloaded file; truncated / intercepted downloads are automatically retried.

## Requirements

- `bash` 4+
- `curl`
- `find`, `grep`, `head`, `tail`, `date`, `printf`, `mktemp`, `sleep` (all standard on Linux/macOS)

**Windows:** install [Git for Windows](https://gitforwindows.org/) and run the script in Git Bash. No additional setup needed.

## Usage

From the repo root:

```bash
# Catch up on all history for every data type (yellow, green, fhv, fhvhv)
./downloader/download_taxi_data.sh

# Just the newest N months per type (useful for incremental updates)
./downloader/download_taxi_data.sh --recent 3
```

Files land in `raw/<type>/<year>/<type>_tripdata_YYYY-MM.parquet`. Already-downloaded files are skipped. Corrupt files (missing PAR1 magic bytes) are cleaned up automatically at the start of each run.

## Alternative: query in place

If you don't need a local mirror, DuckDB's `httpfs` extension can query TLC parquet directly from CloudFront:

```sql
INSTALL httpfs; LOAD httpfs;
SELECT count(*) FROM read_parquet('https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-01.parquet');
```

This downloader is for cases where you want a resumable local mirror — bulk analytics, offline work, or feeding a database.
