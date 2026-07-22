# K6 Load Test

`k6-loadtest` is a K6-based SQL Server load tester with a Python preprocessor. It turns parquet files (or synthetic data specs) into a K6 test bundle: CREATE TABLE DDL, chunked JSON payloads, a K6 `test.js`, and a manifest. Point it at a SQL Server (local Docker, on-prem VM, Azure SQL) and it exercises insert, update, and delete workloads under a configurable virtual-user (VU) profile.

Why the extra preprocessing step and a custom K6 binary? SQL Server support in K6 does not ship out of the box — it comes via the [xk6-sql](https://github.com/grafana/xk6-sql) extension plus Grafana's [`xk6-sql-driver-sqlserver`](https://github.com/grafana/xk6-sql-driver-sqlserver) driver, both of which have to be compiled into a custom K6 binary using [`xk6`](https://github.com/grafana/xk6). The preprocessor's job is to bridge parquet or synthetic-mode configs into the JSON-and-JavaScript shape K6 actually expects at runtime.

## Prerequisites

- **Go 1.22+** so `xk6` can build the custom K6 binary. Install via the [official Go installer](https://go.dev/doc/install) or Homebrew (`brew install go`).
- **A SQL Server instance** to test against. Options include:
    - Local Docker container (recommended for a first test — sample `docker-compose.yml` below).
    - SQL Server 2019+ VM on-prem.
    - Azure SQL Database or Azure SQL Managed Instance.
    - Any other cloud SQL Server. SQL Edge and SQL Server 2022 both work.
- **`uv sync`** in the repo root so the `k6-preprocess` entry point is on your PATH.

## 5-step setup

### 1. Build the custom K6 binary

```bash
./k6-loadtest/build_k6.sh
```

This script runs `xk6 build --with github.com/grafana/xk6-sql --with github.com/grafana/xk6-sql-driver-sqlserver --output ./k6`. It installs `xk6` itself (via `go install`) if it is not already on your PATH, then produces `./k6-loadtest/k6` — the binary lands next to the build script regardless of where you invoke it from. Build takes 30 seconds to 2 minutes depending on the state of your Go module cache.

Verify with:

```bash
./k6-loadtest/k6 version
```

### 2. Copy the sample config

```bash
cp k6-loadtest/config.sample.yaml k6-loadtest/config.yaml
$EDITOR k6-loadtest/config.yaml
```

See the [Reference → Configuration](../reference/configuration.md) page for the field-by-field schema. `config.sample.yaml` itself is also a canonical reference — every option is commented inline.

### 3. Edit `k6-loadtest/config.yaml`

At minimum you need to set three things:

- **A data source.** Pick your source mode (parquet or synthetic — see [Source modes](#source-modes) below). For a first test, keep the `yellow_trips` synthetic block from `config.sample.yaml` — it needs no external files.
- **A target.** Set `host`, `port`, `database`, `username`, and the `password` reference (leave it as `${MSSQL_PASSWORD}` so the value stays out of git).
- **A scenario.** Wire a `target` and `data_source` together and pick your VU profile. A VU is a virtual user — one concurrent SQL connection driving one iteration loop. Ramp up gradually if you're testing a fresh server; ten VUs on a laptop-Docker SQL Server is already a real load.

### 4. Preprocess data into K6 inputs

```bash
uv run k6-preprocess --config k6-loadtest/config.yaml --output k6-loadtest/output/
```

The preprocessor writes:

- `k6-loadtest/output/schema/<datasource>.sql` — one CREATE TABLE DDL per data source, with columns mapped from parquet (or synthetic) types to SQL Server types.
- `k6-loadtest/output/data/<datasource>/chunk_*.json` — chunked JSON payloads (parquet mode only). Each chunk becomes one bulk-insert batch at runtime. Chunk size is controlled by `chunk_size:` in the data source config.
- `k6-loadtest/output/test.js` — the K6 test script. It loads chunks via K6's `SharedArray` (so they are read once and shared across VUs), wires up scenarios, and drives the SQL Server target.
- `k6-loadtest/output/manifest.json` — a machine-readable summary of every scenario, its target connection string (with `${MSSQL_PASSWORD}` preserved for runtime substitution), and its data source metadata.

### 5. Apply the CREATE TABLE DDL

The generated schema file for a yellow-taxi source looks roughly like this:

```sql
IF OBJECT_ID('yellow_trips', 'U') IS NULL
CREATE TABLE yellow_trips (
    VendorID INT,
    tpep_pickup_datetime DATETIME2,
    tpep_dropoff_datetime DATETIME2,
    passenger_count BIGINT,
    trip_distance FLOAT,
    fare_amount FLOAT,
    tip_amount FLOAT,
    PULocationID INT,
    DOLocationID INT
);
```

Apply it via `sqlcmd`:

```bash
sqlcmd -S localhost -U sa -P "$MSSQL_PASSWORD" -i k6-loadtest/output/schema/yellow_trips.sql
```

Or paste the file's contents into Azure Data Studio, DBeaver, or SSMS.

### 6. Run the K6 test

```bash
MSSQL_PASSWORD='YourStrong@Passw0rd' ./k6-loadtest/k6 run k6-loadtest/output/test.js
```

The K6 binary spins up VUs, each opens a SQL Server connection, and every VU iterates through its scenario — inserting, updating, or deleting rows according to the workload mix you configured. K6 prints a live progress banner and, on completion, a summary of every metric (see [Interpreting K6 output](#interpreting-k6-output)).

## Source modes

`k6-loadtest` supports two data source modes. Pick per data source in `config.yaml` under `data_sources: <name>: mode:`.

### `mode: parquet` — real data from parquet

- Reads real data from parquet files — typically the `raw/` mirror produced by the [downloader](downloader.md), or `raw-normalized/` from [normalize](normalize.md).
- Preserves real ratios, real cardinalities, real value distributions.
- Slower startup: preprocess has to read parquet and chunk into JSON before K6 can start.
- `chunk_size:` shapes the memory/network trade-off — larger chunks mean fewer round-trips but more RAM held by each VU's `SharedArray` slice.
- Best for: bulk-load benchmarks that need to mimic the production data path, or reproducing a specific real-world write pattern.

### `mode: synthetic` — K6 generates rows at runtime

- K6 generates rows at runtime from column value ranges declared in `config.yaml` (`type: int/float/datetime/string/bool/date` plus `min:`/`max:`).
- Instant startup — no parquet reading, no preprocessing bottleneck, no chunk files on disk.
- Unlimited scale — every VU generates its own rows independently, so you can never exhaust a fixture.
- No parquet mirror needed, so you can smoke-test a fresh SQL Server without downloading anything first.
- Best for: first-time smoke tests, unbounded-scale runs, and measuring pure SQL Server insertion throughput without being bound by dataset size.

### When to use each

- First-time smoke test → **synthetic** (fast iteration, no downloader dependency).
- Realistic bulk-load benchmark → **parquet** (real ratios, real cardinalities).
- Testing insertion ceiling under unbounded load → **synthetic** (VUs never run out of rows).
- Testing insertion plus realistic distribution → **parquet**.

## Sample local SQL Server (Docker)

A minimal `docker-compose.yml` that gets you a SQL Server 2022 instance on `localhost:1433`:

```yaml
services:
  sqlserver:
    image: mcr.microsoft.com/mssql/server:2022-latest
    environment:
      MSSQL_SA_PASSWORD: "YourStrong@Passw0rd"
      ACCEPT_EULA: "Y"
    ports:
      - "1433:1433"
    volumes:
      - sqlserver-data:/var/opt/mssql
volumes:
  sqlserver-data:
```

Bring it up:

```bash
docker compose up -d
```

Verify:

```bash
sqlcmd -S localhost -U sa -P 'YourStrong@Passw0rd' -Q "SELECT @@VERSION"
```

Matching `config.yaml` target stub:

```yaml
targets:
  local_server:
    host: localhost
    port: 1433
    database: loadtest_db
    username: sa
    password: ${MSSQL_PASSWORD}
    table: yellow_trips
```

`MSSQL_PASSWORD` must be exported in the environment when you run K6 (`MSSQL_PASSWORD='YourStrong@Passw0rd' ./k6-loadtest/k6 run …`). The preprocessor preserves the `${VAR}` placeholder in the manifest so K6 does the substitution at runtime, not at preprocess time. This keeps passwords out of the generated `test.js` and out of your git history.

## Config summary

Top-level keys in `config.yaml`:

- `data_sources:` — dict keyed by source name. Each has a `mode:` (`parquet` or `synthetic`) plus mode-specific keys (`path:` and `chunk_size:` for parquet, `columns:` with type/min/max blocks for synthetic).
- `targets:` — dict of connection targets. Each entry declares `host`, `port`, `database`, `username`, `password` (use `${MSSQL_PASSWORD}` for runtime substitution), and `table`.
- `scenarios:` — dict of K6 scenarios. Each ties a `data_source` to a `target`, sets a workload mix (`insert`/`update`/`delete` percentages), think-time bounds, and a K6 executor block (`executor:`, `startVUs:`, `stages:` or `duration:`).

See the [Reference → Configuration](../reference/configuration.md) page for the exhaustive schema. `config.sample.yaml` in the repo is also a canonical reference.

## Interpreting K6 output

At the end of a run, K6 prints a summary block that looks like this:

```text
     data_received..............: 4.2 kB  70 B/s
     data_sent..................: 8.1 MB  135 kB/s
     iteration_duration.........: avg=203.4ms  min=45ms  med=195ms  max=1.2s
     iterations.................: 296    4.93/s
     sql_query_duration.........: avg=198.1ms  min=42ms  med=190ms  max=1.19s
     sql_rows_affected..........: 296000  4930/s
     vus........................: 5      min=5      max=5
     vus_max....................: 5      min=5      max=5
```

Line-by-line:

- `data_received` / `data_sent` — network throughput for the run. SQL Server bulk-insert workloads are send-heavy, so `data_sent` will dwarf `data_received`.
- `iteration_duration` — how long one scenario iteration took end-to-end (open connection, execute SQL, tear down). Includes think time.
- `iterations` — total scenario runs, plus per-second rate. Multiply by rows-per-iteration to reason about total row throughput.
- `sql_query_duration` — server-side query latency reported by `xk6-sql`. This is the number to compare against SQL Server DMV latency to isolate client-side overhead.
- `sql_rows_affected` — total rows inserted, updated, or deleted across all iterations.
- `vus` / `vus_max` — active virtual users during the test. For ramping scenarios, `vus_max` is your peak concurrency.

**Bulk-insert scenarios.** `iteration_duration` should be roughly equal to `sql_query_duration` — if the gap is large, client-side setup (connection open, JSON parsing) is dominating and you may want larger chunks or fewer VUs. Watch `sql_rows_affected / duration` for the throughput number to publish.

**Query-latency scenarios.** For read or update workloads, `sql_query_duration` p95 and p99 matter more than the mean. Add K6 [thresholds](https://grafana.com/docs/k6/latest/using-k6/thresholds/) to your scenario config to fail the run automatically if p99 regresses beyond your SLO.

For long-running tests, export metrics to InfluxDB (or Prometheus) so you can chart them in Grafana:

```bash
./k6-loadtest/k6 run --out influxdb=http://localhost:8086/k6 k6-loadtest/output/test.js
```

Grafana's [official K6 dashboards](https://grafana.com/grafana/dashboards/?search=k6) render `sql_query_duration` percentiles, VU ramps, and iteration rates out of the box.

## Troubleshooting

- **`build_k6.sh` fails with `go: command not found`.** `xk6` needs Go on the PATH. Install Go 1.22+ and re-open your shell so `go env GOPATH` resolves, then re-run the build script.
- **`build_k6.sh` fails on `xk6 build`.** Usually a stale Go module cache. Clear it with `go clean -modcache` and retry. The build script always fetches the latest `xk6-sql` and `xk6-sql-driver-sqlserver` from Grafana's GitHub org.
- **K6 exits with `unable to connect to SQL Server`.** Confirm the target is reachable (`sqlcmd -S <host> -U <user> -P "$MSSQL_PASSWORD" -Q "SELECT 1"`). For Docker on macOS/Windows, use `localhost` from the host and the container name from other containers. For Azure SQL, whitelist your client IP in the firewall.
- **`sql_query_duration` is orders of magnitude higher than expected.** You are probably measuring cold-cache first-iteration effects. Run a short warmup scenario before the measured one, or discard the first N iterations when computing your throughput number.
- **`iterations` is much lower than expected.** Either your think-time is dominating (drop `think_time.max`), or the scenario is bottlenecked on SQL Server (check DMVs for waits). Compare `iteration_duration` against `sql_query_duration` to tell which side is slow.
