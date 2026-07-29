# Cookbook

Scenario-oriented recipes that combine the tools — downloader, schema-drift, normalize, and loader, orchestrated end-to-end by `taxi-run` — into real workflows. Each recipe is self-contained and copy-pasteable, aimed at data engineers running the stack in a real environment. Where a recipe overlaps with a per-tool guide, this page focuses on the glue between tools and the operational details (cron, proxies, dev bootstraps).

## Nightly refresh via cron

**Goal:** keep the local TLC mirror caught up on new months, refresh normalized parquet, and log any schema drift.

**Recipe** — a shell script plus a scheduler entry.

1. Create `/srv/taxi/bin/nightly-refresh.sh`. The orchestrator (`taxi-run`) already chains download → normalize per type and honors each stage's exit code, so the whole refresh is one command:

    ```bash
    #!/bin/bash
    set -euo pipefail
    cd /srv/taxi

    # 1+2. Fetch any newly-published months (recent-mode stops at first local
    # file) then normalize; auto-amends the mapping if new drift showed up.
    # Exit 1 means at least one type needs a human to review a mapping amend.
    uv run taxi-run --recent 3

    # 3. Snapshot schema-drift report for later diffing
    uv run schema-drift --output /var/log/taxi/drift-$(date +%Y%m%d).txt
    ```

    If you'd rather run each stage yourself instead of going through `taxi-run` (for finer-grained logging, say), the equivalent per-type chain is:

    ```bash
    for t in yellow green fhv fhvhv; do
        uv run taxi-download --recent 3 "$t"
        uv run normalize "$t"
    done
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
- `taxi-run` exits 0 for a clean run, 1 if any type's normalize amended its mapping and needs a human to review the new drift, or 2 for an operational failure (download failure, a real normalize config error). Consider paging on exit=2 vs. logging-only on exit=1 — see the [orchestrator guide's exit codes](guides/orchestrator.md#exit-codes). Note `set -euo pipefail` means the script stops at the `taxi-run` line on any non-zero exit, so the schema-drift snapshot won't run that day either; drop `-e` (or run schema-drift unconditionally with `|| true` before it) if you want the snapshot regardless.
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

## Loading the normalizer's output into SQL Server

**Goal:** take `raw-normalized/` and get it into SQL Server as one table per type per year, re-runnable without re-loading data that's already there.

**Recipe:**

1. Point the loader at a reachable SQL Server and set the password (there is no `--password` flag — it only ever comes from the environment):

    ```bash
    export MSSQL_PASSWORD='YourStrong@Passw0rd'
    ```

2. See the plan before writing anything:

    ```bash
    uv run taxi-load yellow --dry-run
    ```

    This prints, per year, one of `skip`, `append month(s) NN, NN`, or `truncate + reload (N month file(s))` — see the [loader guide](guides/loader.md#idempotent-reconcile-skip-append-truncate-reload) for how that decision is made.

3. Run it for real:

    ```bash
    uv run taxi-load yellow
    ```

    Omit the type to load all four (`yellow`, `green`, `fhv`, `fhvhv`) in turn:

    ```bash
    uv run taxi-load
    ```

**Notes:**

- The loader reads `raw-normalized/<type>/<year>/*.parquet` by default; point it elsewhere with `--data-dir DIR` (reads `DIR/raw-normalized`) or `--input-dir DIR` (reads `DIR/<type>/<year>/*.parquet` directly, bypassing the `raw-normalized` assumption).
- Re-running `taxi-load` after a fresh `normalize` pass is the common case, not a special one: new months on disk get **appended**; years where nothing changed are **skipped**; anything that looks inconsistent (a prior interrupted run, a row-count mismatch against the manifest) gets **truncated and reloaded** rather than silently left half-updated. Pass `--full-refresh` to force every year onto the reload path regardless of the manifest.
- Table DDL is generated straight from the parquet schema via `taxi_shared`'s type mapper — there's no separate DDL file to hand-maintain or apply.
- One command does download → normalize → load end to end: `uv run taxi-run yellow --load` (see the [orchestrator guide](guides/orchestrator.md)).

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
    # Downloader uses httpx.Client, which honors HTTPS_PROXY/HTTP_PROXY via trust_env
    uv run taxi-download --recent 3 yellow

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
- Some corporate proxies MITM HTTPS with a self-signed CA. Point Python (and therefore `taxi-download`'s `httpx` client) at the CA bundle via `SSL_CERT_FILE=/path/to/corp-ca.pem`; uv reads `UV_NATIVE_TLS=true` for OS trust store on Windows/macOS.
- `NO_PROXY=localhost,127.0.0.1` is still worth setting even though `taxi-load`'s connection to SQL Server goes through DuckDB's `mssql` extension over the native TDS protocol, not HTTP, so it isn't itself proxy-aware — the exclusion matters for every *other* localhost-bound tool in the same shell (e.g. `sqlcmd`, a local dev server) that would otherwise try to route through the proxy and fail.
- Docker's daemon has its own proxy configuration (`/etc/systemd/system/docker.service.d/http-proxy.conf`) — env vars in your shell don't affect image pulls.

## Populating a fresh dev SQL Server

**Goal:** go from an empty Docker-hosted SQL Server to a populated `taxi` database in well under 30 minutes.

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
    uv run taxi-download --recent 3 yellow
    ```

3. Normalize (first-run bootstrap + human ack pass omitted for brevity — on a stable-schema `--recent 3` window it's typically a no-op):

    ```bash
    uv run normalize yellow
    uv run normalize yellow    # 2nd run once mapping is fine
    ```

4. Load into SQL Server. The loader creates the `taxi` database and `dbo` schema automatically, derives table DDL from the parquet, and writes one table per year (e.g. `yellow_2026`):

    ```bash
    export MSSQL_PASSWORD='YourStrong@Passw0rd'
    uv run taxi-load yellow
    ```

    Steps 2–4 collapse into a single command if you'd rather not run each stage by hand:

    ```bash
    export MSSQL_PASSWORD='YourStrong@Passw0rd'
    uv run taxi-run --recent 3 --load yellow
    ```

5. Verify the load:

    ```bash
    # Confirm the loader considers this year fully loaded (reports "skip")
    uv run taxi-load yellow --dry-run

    # Row count for a given year's table, e.g. 2026
    sqlcmd -S localhost -U sa -P 'YourStrong@Passw0rd' \
      -Q "SELECT count(*) FROM taxi.dbo.yellow_2026;"
    ```

**Notes:**

- The loader's defaults (`--host localhost`, `--port 1433`, `--database taxi`, `--schema dbo`, `--user sa`) match this docker-compose setup, so no flags are needed beyond `MSSQL_PASSWORD` for a local dev target.
- Total time is dominated by network to CloudFront (parquet download); the SQL Server load itself is a bulk `COPY` via DuckDB's `mssql` extension and is fast even for a full year. On a residential gigabit link, `--recent 3 yellow` end to end is a few minutes, not 30.
- Re-running `taxi-load yellow` after loading is a no-op (`skip`) unless new months landed on disk, in which case it appends just those months — see the [loader guide](guides/loader.md) for the full reconcile model.
- If you're iterating on schema and want to reset a year without losing the container: `uv run taxi-load yellow --full-refresh` forces a drop-and-reload of every year processed, or drop the table by hand (`sqlcmd ... -Q "DROP TABLE yellow_2026;"`) and re-run `taxi-load`.
