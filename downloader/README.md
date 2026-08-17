# downloader

`taxi_download` — a Python package that mirrors NYC TLC parquet trip data from
CloudFront to a local `raw/` directory. Rate-limit-aware backoff, PAR1
corrupt-file validation, end-of-history detection, incremental catch-up.

Run it as a console script or a module:

```bash
taxi-download                       # all four types, full history
taxi-download yellow --recent 3     # 3 most recent yellow months
python -m taxi_download.cli --data-dir /data yellow
```

→ **[Full guide](https://andrekamman.github.io/taxi-seed/guides/downloader/)**
