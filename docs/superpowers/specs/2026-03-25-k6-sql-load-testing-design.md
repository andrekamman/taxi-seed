# K6 SQL Server Load Testing Tool

**Date:** 2026-03-25
**Status:** Approved
**Purpose:** Stress-test ETL/CDC pipelines by generating realistic SQL Server workloads from NYC taxi parquet data.

## Problem

We need to generate realistic transactional load on multiple SQL Server databases that feed a data warehouse through CDC and merge replication. The load must be configurable (think times, workload mix, VU profiles) and use complete datasets so downstream quality controls and BI reports produce deterministic results.

## Architecture

Three components:

1. **Preprocessor** (`preprocess.py`) — Python + DuckDB. Reads parquet files and a YAML config, outputs chunked JSON data files, scenario manifests, SQL templates, CREATE TABLE scripts, and a generated K6 test script.
2. **Custom K6 binary** — vanilla K6 + xk6-sql with MS SQL driver. Built once.
3. **Generated K6 test script** (`test.js`) — uses K6's built-in scenarios feature to run multiple workloads concurrently against different SQL Server targets from a single process.

### Data flow

```
parquet files + config.yaml
        |
        v
   preprocessor (Python + DuckDB)
        |
        v
   k6_output/
     schema/     -- CREATE TABLE scripts
     data/       -- chunked JSON files
     scenarios/  -- manifest JSON per scenario
     test.js     -- generated K6 script
        |
        v
   custom K6 binary (xk6-sql)
        |
        v
   SQL Server A (db1, db2)
   SQL Server B (db3)
   SQL Server C (db1)
```

## Config format

```yaml
data_sources:
  yellow_trips:
    path: raw/yellow/2026/*.parquet
    chunk_size: 5000                    # rows per chunk file
    key_columns: [pickup_time, dropoff_time]  # WHERE clause for UPDATE/DELETE
    columns:                            # select + rename columns
      pickup_time: tpep_pickup_datetime
      dropoff_time: tpep_dropoff_datetime
      passenger_count: passenger_count
      trip_distance: trip_distance
      fare_amount: fare_amount
      tip_amount: tip_amount

targets:
  server_a_sales:
    host: sqlserver-a.local
    port: 1433
    database: sales_db
    username: sa
    password: ${MSSQL_PASSWORD}         # env var substitution
    table: taxi_trips

  server_b_analytics:
    host: sqlserver-b.local
    port: 1433
    database: analytics_db
    username: sa
    password: ${MSSQL_PASSWORD}
    table: taxi_trips

scenarios:
  heavy_inserts_server_a:
    target: server_a_sales
    data_source: yellow_trips
    ordering: parallel                  # or 'sequential'
    workload:
      insert: 80                        # percentages, must sum to 100
      update: 15
      delete: 5
    think_time:
      min: 200ms
      max: 1s
    k6:                                 # passthrough K6 executor config
      executor: ramping-vus
      startVUs: 2
      stages:
        - duration: 1m
          target: 20
        - duration: 5m
          target: 20
        - duration: 1m
          target: 0

  steady_load_server_b:
    target: server_b_analytics
    data_source: yellow_trips
    ordering: sequential
    workload:
      insert: 95
      update: 5
      delete: 0
    think_time:
      min: 500ms
      max: 2s
    k6:
      executor: constant-vus
      vus: 10
      duration: 7m
```

### Config sections

- **`data_sources`** — which parquet files to read and how to map columns. Decoupled from scenarios so the same data can be reused across multiple targets. `chunk_size` controls memory usage during K6 execution.
- **`targets`** — SQL Server connection details. One entry per server/database combination. `${VAR}` references are resolved at K6 runtime via `__ENV.VAR`, not by the preprocessor — credentials are never written to disk in the output directory.
- **`scenarios`** — each becomes a K6 scenario. Links a target to a data source with a specific workload mix, think time range, and K6 executor configuration. The `k6` section is passthrough — any valid K6 executor config is accepted.

## Preprocessor (`preprocess.py`)

**Invocation:** `python preprocess.py --config config.yaml --output k6_output/`

**Responsibilities:**

1. Parse and validate config YAML (check workload percentages sum to 100, required fields present, referenced targets/data_sources exist, key_columns exist in columns mapping, glob patterns match at least one file).
2. Read parquet files via DuckDB, apply column selection and renaming from the `columns` mapping.
3. Map DuckDB/parquet types to SQL Server types (BIGINT, VARCHAR, DATETIME2, FLOAT, DECIMAL, etc.).
4. Split rows into numbered chunk files (`chunk_0000.json`, `chunk_0001.json`, ...) per data source.
5. Generate parameterized SQL templates (INSERT, UPDATE, DELETE) per target/table based on the column mapping.
6. Generate CREATE TABLE scripts per target/table.
7. Generate scenario manifest JSON files containing: table name, workload percentages, think time config, SQL templates, data source reference, ordering mode, and connection string template (with `${VAR}` placeholders preserved for K6 runtime resolution).
8. Generate the K6 `test.js` script with one scenario block and executor function per config scenario. The generated script reads manifests at runtime via `open()` — manifests are runtime artifacts, not just intermediate build artifacts.

### Output structure

```
k6_output/
  schema/
    server_a_sales_taxi_trips.sql
    server_b_analytics_taxi_trips.sql
  data/
    yellow_trips/
      chunk_0000.json
      chunk_0001.json
      chunk_0002.json
      ...
  scenarios/
    heavy_inserts_server_a.json
    steady_load_server_b.json
  test.js
```

### Chunk file format

Each chunk file is a JSON array of row objects using mapped column names. Timestamps are ISO 8601 strings. Example:

```json
[
  {"pickup_time": "2026-01-15T08:30:00", "dropoff_time": "2026-01-15T09:15:00", "passenger_count": 2, "trip_distance": 3.4, "fare_amount": 15.50, "tip_amount": 3.00},
  ...
]
```

### Data loading in K6

Chunk metadata (list of file paths) is loaded into a `SharedArray` in the init context. During execution, each VU uses `open()` to read its assigned chunk file and `JSON.parse()` to deserialize it. Only one chunk is in memory per VU at a time. This avoids loading the full dataset into `SharedArray` while keeping memory bounded.

### SQL generation

The preprocessor generates parameterized SQL from the column mapping:

```sql
-- INSERT (all columns)
INSERT INTO taxi_trips (pickup_time, dropoff_time, passenger_count,
  trip_distance, fare_amount, tip_amount)
VALUES (@p1, @p2, @p3, @p4, @p5, @p6)

-- UPDATE (sets all non-key columns, WHERE uses key_columns)
UPDATE taxi_trips SET passenger_count = @p1, trip_distance = @p2,
  fare_amount = @p3, tip_amount = @p4
WHERE pickup_time = @p5 AND dropoff_time = @p6

-- DELETE (WHERE uses key_columns)
DELETE FROM taxi_trips WHERE pickup_time = @p1 AND dropoff_time = @p2
```

Key columns for UPDATE/DELETE WHERE clauses are defined by `key_columns` in the data source config. The UPDATE template always sets all non-key columns — the variability comes from using different source row values, not from randomizing which columns appear.

### Type mapping

| Parquet/DuckDB type | SQL Server type |
|---------------------|-----------------|
| BIGINT              | BIGINT          |
| INTEGER             | INT             |
| DOUBLE              | FLOAT           |
| FLOAT               | REAL            |
| VARCHAR             | NVARCHAR(MAX)   |
| TIMESTAMP           | DATETIME2       |
| BOOLEAN             | BIT             |
| DECIMAL(p,s)        | DECIMAL(p,s)    |
| DATE                | DATE            |
| SMALLINT / INT16    | SMALLINT        |
| TINYINT / INT8      | TINYINT         |
| HUGEINT             | DECIMAL(38,0)   |

Unmapped types cause a validation error with a message listing the column name, source type, and file path.

## K6 test script behavior

### Scenario execution

Each config scenario maps to a K6 scenario with its own executor function. The K6 `options.scenarios` object is generated directly from the config's `k6` section, with `exec` pointing to the generated function name.

### Chunk processing

- **Parallel mode (default):** Each VU uses `execution.scenario.iterationInTest` as a chunk index — K6 assigns each iteration a unique sequential number, so chunks are naturally distributed across VUs without shared mutable state. When chunk index exceeds available chunks, the VU wraps around or completes.
- **Sequential mode:** The preprocessor overrides the K6 executor config to force `per-vu-iterations` with 1 VU. If the user's config specifies more VUs, the preprocessor emits a warning. The single VU processes chunks 0, 1, 2, ... in order. Think time and pacing still apply.

### Operation selection

For each row in a chunk, the script picks an operation (insert, update, delete) using weighted random selection based on the workload percentages.

- **INSERT:** Executes the insert SQL template with values from the current row.
- **UPDATE:** Picks a previously-processed row from the current chunk and updates some of its non-key fields with values from the current row.
- **DELETE:** Picks a previously-processed row from the current chunk and deletes it by key columns.

UPDATE and DELETE target rows from the current VU's current chunk only. This is a deliberate design choice — it avoids cross-VU conflicts in parallel mode and ensures operations target rows that are known to exist.

If no previously-processed rows exist yet (start of a chunk), update/delete operations fall back to insert.

### Think time

After each operation, the script sleeps for a random duration between `think_time.min` and `think_time.max` (uniform distribution).

### Connection management

Each VU opens its own database connection via xk6-sql on first use within the executor function, then caches it for the VU's lifetime. Connections cannot be shared through K6's `setup()` because setup return values are serialized. The connection string is read from the scenario manifest (loaded via `open()` in the init context). Connections are closed in `teardown()` or when the VU completes.

## Custom K6 binary

Built with xk6:

```bash
xk6 build --with github.com/grafana/xk6-sql \
  --with github.com/grafana/xk6-sql-driver-mssql
```

Exact module paths and versions should be verified against current xk6-sql documentation at build time.

This is a one-time build step. The resulting binary replaces the standard `k6` command.

## Technology choices

- **DuckDB** for parquet reading — already in the project, excellent at reading parquet files and applying transformations via SQL.
- **K6 with xk6-sql** over HTTP proxy — eliminates middleware, enables future scale-out by distributing the K6 binary.
- **K6 scenarios** over separate processes — built-in multiplexing of different workloads in a single process, simpler orchestration.
- **Chunked JSON** over full-dataset loading — bounded memory usage while preserving complete data for downstream quality validation.
- **YAML config** — readable, supports complex nested structures, familiar.

## Phase 2 (future, out of scope)

- Schema change injection mid-test (ALTER TABLE scenarios) for testing data warehouse schema drift handling.
- Reporting/SELECT query workloads from template files with parameterized queries.
- K6 metrics integration for measuring ETL lag and CDC throughput.
