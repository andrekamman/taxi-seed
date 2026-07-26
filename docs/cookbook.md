# Cookbook

Scenario-oriented recipes that combine the four tools — downloader, schema-drift, normalize, and K6 load test — into end-to-end workflows. Each recipe is self-contained and copy-pasteable, aimed at data engineers running the stack in a real environment. Where a recipe overlaps with a per-tool guide, this page focuses on the glue between tools and the operational details (cron, proxies, dev bootstraps).

## Nightly refresh via cron

**Goal:** keep the local TLC mirror caught up on new months, refresh normalized parquet, and log any schema drift.

**Recipe** — a shell script plus a scheduler entry.

1. Create `/srv/taxi/bin/nightly-refresh.sh`:

    ```bash
    #!/bin/bash
    set -euo pipefail
    cd /srv/taxi

    # 1. Fetch any newly-published months (walker stops at first local file)
    ./downloader/download_taxi_data.sh --recent 3 yellow
    ./downloader/download_taxi_data.sh --recent 3 green
    ./downloader/download_taxi_data.sh --recent 3 fhv
    ./downloader/download_taxi_data.sh --recent 3 fhvhv

    # 2. Normalize; auto-amends the mapping if new drift showed up
    uv run normalize

    # 3. Snapshot schema-drift report for later diffing
    uv run schema-drift --output /var/log/taxi/drift-$(date +%Y%m%d).txt
    ```

2. Wire it into cron (`crontab -e`):

    ```
    0 4 * * * /srv/taxi/bin/nightly-refresh.sh >> /var/log/taxi/refresh.log 2>&1
    ```

3. Or use a systemd timer + service instead. `/etc/systemd/system/taxi-refresh.service`:

    ```ini
    [Unit]
    Description=Nightly TLC data refresh

    [Service]
    Type=oneshot
    ExecStart=/srv/taxi/bin/nightly-refresh.sh
    User=taxi
    StandardOutput=append:/var/log/taxi/refresh.log
    StandardError=append:/var/log/taxi/refresh.log
    ```

    `/etc/systemd/system/taxi-refresh.timer`:

    ```ini
    [Unit]
    Description=Nightly TLC data refresh

    [Timer]
    OnCalendar=*-*-* 04:00:00
    Persistent=true

    [Install]
    WantedBy=timers.target
    ```

    Enable:

    ```bash
    systemctl enable --now taxi-refresh.timer
    ```

**Notes:**

- `--recent`'s stop-on-local semantic makes this fully idempotent — running it twice in the same day just skips everything.
- If normalize amends the mapping (new drift detected), the script exits non-zero on that day. Consider paging on exit=1 vs. logging-only on exit=0.
- `raw/` grows monotonically — plan capacity via the [Downloader guide's sizing table](guides/downloader.md#disk-sizing).
- Prefer systemd timers over cron on modern hosts: `Persistent=true` catches up missed runs after downtime, and logs land in the journal automatically.

## DuckDB `httpfs` — no local mirror

**Goal:** run one-off analytics queries against TLC parquet without downloading anything.

**Recipe:**

1. Start DuckDB:

    ```bash
    duckdb
    ```

2. Load the `httpfs` extension and query directly:

    ```sql
    INSTALL httpfs;
    LOAD httpfs;

    -- Simple count
    SELECT count(*) AS trips
    FROM read_parquet('https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-01.parquet');

    -- Average trip distance by day of week
    SELECT strftime(tpep_pickup_datetime, '%A') AS day_of_week,
           avg(trip_distance) AS avg_distance,
           count(*) AS trips
    FROM read_parquet('https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-01.parquet')
    GROUP BY day_of_week
    ORDER BY avg_distance DESC;

    -- Query multiple months in one shot with a glob
    SELECT count(*) AS trips_2024
    FROM read_parquet('https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-*.parquet');
    ```

**Notes:**

- First query downloads the file; subsequent queries in the same session hit DuckDB's in-memory cache. Across sessions, use the on-disk cache: `SET enable_external_file_cache=true; PRAGMA cache_directory='/tmp/duckdb-cache';`
- Costs: TLC's CloudFront is unmetered from the requester side, but repeated full-scans of a 50 MB file are wasteful. Use the downloader for anything you'll query more than a couple of times.
- Schema drift is on you here — DuckDB will happily union files with different schemas via `union_by_name=true`, but drops columns silently. Cross-reference with `schema-drift` output.
- The glob pattern hits CloudFront's directory listing, which requires HTTP range requests — some corporate proxies mangle these. If globs fail, list the URLs explicitly.

## Side-by-side comparison of multiple TLC years

**Goal:** compute trips-per-year and average fare across a decade of yellow taxi data using the normalized mirror.

**Recipe:** with a full history mirror at `raw-normalized/yellow/`:

1. Start DuckDB in the taxi directory:

    ```bash
    duckdb
    ```

2. Aggregate across every year:

    ```sql
    -- Every year with total trips and average fare
    SELECT extract(year FROM tpep_pickup_datetime) AS year,
           count(*) AS trips,
           avg(fare_amount) AS avg_fare
    FROM read_parquet('raw-normalized/yellow/**/*.parquet')
    GROUP BY year
    ORDER BY year;

    -- Top 10 pickup locations across the whole history (post-2015)
    SELECT PULocationID,
           count(*) AS trips,
           avg(trip_distance) AS avg_distance,
           avg(fare_amount) AS avg_fare
    FROM read_parquet('raw-normalized/yellow/**/*.parquet')
    WHERE PULocationID IS NOT NULL
      AND extract(year FROM tpep_pickup_datetime) >= 2015
    GROUP BY PULocationID
    ORDER BY trips DESC
    LIMIT 10;
    ```

**Notes:**

- Uses `raw-normalized/` (post-normalize output), not `raw/` — schemas are uniform, no need to reason about pre-2015 `pickup_latitude` vs post-2015 `PULocationID`.
- Pre-2015 rows have NULL for `PULocationID` (they were represented as lat/lon in the raw and dropped during normalize per the mapping's `acknowledged_data_loss:` entry). The `WHERE PULocationID IS NOT NULL` handles that.
- ~30 GB of parquet queried in one DuckDB command takes seconds — parquet + DuckDB scale surprisingly well on a laptop with plenty of RAM.
- If you're memory-bound, add `SET memory_limit='8GB'; SET threads=4;` before running — DuckDB will spill to disk rather than OOM.

## Load-testing the normalizer's output

**Goal:** stress-test SQL Server with normalized parquet instead of raw — closer to your production data path.

**Recipe:**

1. Edit `k6-loadtest/config.yaml` to point at `raw-normalized/`:

    ```yaml
    data_sources:
      yellow_trips:
        mode: parquet
        path: raw-normalized/yellow/**/*.parquet
        key_columns: [tpep_pickup_datetime]
        max_rows: 500000
    ```

2. Preprocess parquet into K6 fixtures:

    ```bash
    uv run k6-preprocess --config k6-loadtest/config.yaml --output k6-loadtest/output/
    ```

3. Apply DDL and run K6:

    ```bash
    sqlcmd -S localhost -U sa -P "$MSSQL_PASSWORD" -i k6-loadtest/output/schema/yellow_trips.sql
    MSSQL_PASSWORD='YourStrong@Passw0rd' ./k6-loadtest/k6 run k6-loadtest/output/test.js
    ```

**Notes:**

- Load-testing normalized data is more representative if your production pipeline normalizes before loading. If you load raw parquet directly and never normalize, load-test with `raw/` paths instead.
- `max_rows: 500000` caps the fixture size; K6 will iterate through it. Set based on how long you want the test to run and your available VU-hours.
- SQL Server bulk-insert throughput is often bounded by the client-side JSON parsing + network before the server itself. Watch `sql_query_duration` vs total `iteration_duration` to see where time goes.
- If throughput plateaus, try lowering `vus` — SQL Server's lock contention on a single table can make more concurrency slower, not faster.

## Running behind a corporate proxy

**Goal:** get the downloader, uv, and gh to work behind a corporate HTTP proxy at `http://proxy.corp.internal:3128`.

**Recipe:**

1. Export the standard proxy env vars once per shell:

    ```bash
    export HTTPS_PROXY=http://proxy.corp.internal:3128
    export HTTP_PROXY=http://proxy.corp.internal:3128
    export NO_PROXY=localhost,127.0.0.1,.corp.internal
    ```

2. Then everything just works:

    ```bash
    # Downloader uses curl, honors HTTPS_PROXY
    ./downloader/download_taxi_data.sh --recent 3 yellow

    # uv also honors it
    uv sync --extra test

    # gh honors it too (for API + PR operations)
    gh auth login
    ```

3. Persist across sessions by adding to `~/.bashrc` / `~/.zshrc`:

    ```bash
    if [ -z "$HTTPS_PROXY" ]; then
        export HTTPS_PROXY=http://proxy.corp.internal:3128
        export HTTP_PROXY=http://proxy.corp.internal:3128
        export NO_PROXY=localhost,127.0.0.1,.corp.internal
    fi
    ```

**Notes:**

- If your proxy requires authentication: `http://user:pass@proxy.corp.internal:3128`. Prefer a keychain-backed helper over hardcoding.
- Some corporate proxies MITM HTTPS with a self-signed CA. Point curl at the CA bundle via `CURL_CA_BUNDLE=/path/to/corp-ca.pem`; python via `SSL_CERT_FILE`; uv reads `UV_NATIVE_TLS=true` for OS trust store on Windows/macOS.
- `NO_PROXY=localhost` matters if you're running SQL Server locally — you don't want K6 hitting the proxy for `localhost:1433`.
- Docker's daemon has its own proxy configuration (`/etc/systemd/system/docker.service.d/http-proxy.conf`) — env vars in your shell don't affect image pulls.

## Populating a fresh dev SQL Server

**Goal:** go from an empty Docker-hosted SQL Server to a populated `yellow_trips` table in ~30 minutes.

**Recipe:**

1. Start SQL Server:

    ```bash
    cat > docker-compose.yml <<'EOF'
    services:
      sqlserver:
        image: mcr.microsoft.com/mssql/server:2022-latest
        environment:
          MSSQL_SA_PASSWORD: "YourStrong@Passw0rd"
          ACCEPT_EULA: "Y"
        ports: [ "1433:1433" ]
        volumes: [ "sqlserver-data:/var/opt/mssql" ]
    volumes:
      sqlserver-data:
    EOF
    docker compose up -d
    ```

2. Fetch three months of yellow (small enough to complete in a couple of minutes):

    ```bash
    ./downloader/download_taxi_data.sh --recent 3 yellow
    ```

3. Normalize (first-run bootstrap + human ack pass omitted for brevity — on a stable-schema `--recent 3` window it's typically a no-op):

    ```bash
    uv run normalize yellow
    uv run normalize yellow    # 2nd run once mapping is fine
    ```

4. Configure `k6-preprocess` to load normalized parquet as a one-shot bulk load. Edit `k6-loadtest/config.yaml`:

    ```yaml
    data_sources:
      yellow_trips:
        mode: parquet
        path: raw-normalized/yellow/**/*.parquet
        key_columns: [tpep_pickup_datetime]

    targets:
      - name: local
        host: localhost
        port: 1433
        database: master
        user: sa
        password_env: MSSQL_PASSWORD

    scenarios:
      - name: bulk_load
        data_source: yellow_trips
        target: local
        vus: 4
        iterations: -1
    ```

5. Preprocess, apply DDL, run:

    ```bash
    uv run k6-preprocess --config k6-loadtest/config.yaml --output k6-loadtest/output/
    sqlcmd -S localhost -U sa -P 'YourStrong@Passw0rd' -i k6-loadtest/output/schema/yellow_trips.sql
    MSSQL_PASSWORD='YourStrong@Passw0rd' ./k6-loadtest/k6 run k6-loadtest/output/test.js
    ```

6. Verify the load:

    ```bash
    sqlcmd -S localhost -U sa -P 'YourStrong@Passw0rd' \
      -Q "SELECT count(*) FROM master.dbo.yellow_trips;"
    ```

**Notes:**

- `iterations: -1` means "keep iterating until the fixture is exhausted"; with `vus: 4`, four connections load in parallel.
- SQL Server's default `master` database is fine for a dev target. For anything real, `CREATE DATABASE taxi` and put the table there.
- Total time is dominated by network to CloudFront (parquet download) then network to SQL Server (bulk insert). On a residential gigabit link, ~30 minutes for `--recent 3 yellow`.
- If you're iterating on schema and want to reset without losing the container: `sqlcmd ... -Q "DROP TABLE yellow_trips;"` then re-apply the DDL — much faster than tearing down the volume.
