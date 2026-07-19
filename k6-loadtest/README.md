# k6-loadtest

K6-based SQL Server load tester with a Python preprocessor that turns parquet (or synthetic data specs) into a K6 test bundle: CREATE TABLE DDL, chunked JSON payloads, a K6 `test.js`, and a manifest.

## Prerequisites

- **Go 1.22+** (used by `build_k6.sh` to compile a K6 binary with the SQL Server extension)
- **A SQL Server instance** to test against (local Docker works; adjust `config.sample.yaml` accordingly)

## Setup

```bash
# 1. Build the custom K6 binary (produces `./k6-loadtest/k6`)
./k6-loadtest/build_k6.sh

# 2. Copy the sample config and adjust for your environment
cp k6-loadtest/config.sample.yaml k6-loadtest/config.yaml
$EDITOR k6-loadtest/config.yaml

# 3. Preprocess data into K6 inputs
uv run k6-preprocess --config k6-loadtest/config.yaml --output k6-loadtest/output/

# 4. Create tables (apply files under k6-loadtest/output/schema/ to your SQL Servers)

# 5. Run the load test
MSSQL_PASSWORD=yourpass ./k6-loadtest/k6 run k6-loadtest/output/test.js
```

## Data source modes

Set per-source in `config.yaml`:

- **`mode: parquet`** — reads real data from parquet files (typically the `raw/` mirror from the downloader).
- **`mode: synthetic`** — K6 generates random rows at runtime from column value ranges. Instant startup, unlimited scale, no parquet files needed.

See `config.sample.yaml` for full option documentation.
