# taxi-mssql

`taxi-mssql` is the one command that produces a complete SQL Server copy of the TLC data
set. Give it one data directory on your host and it fills that directory with the whole **deliverable
set** — raw parquet, normalized parquet, a presized data file and log file, and a
compressed, verified backup. Run it again on the same directory and it continues from where
the last run stopped; every stage reads the directory to decide whether it has work to do, so
there is no state file to go stale.

Everything else in this repo is portable: [`taxi-pipeline`](pipeline.md) stops at "load
into a database that already exists". `taxi-mssql` is the SQL-Server-specific half —
provisioning, presizing, the memory cap, Ola Hallengren's maintenance solution, the backup
— and it is deliberately not portable. The boundary between the two is the point of having
two tools.

In practice you run it as a Docker Compose stack: `mssql/docker-compose.yml` brings up a
SQL Server next to it, which is the only way the presizing and the attach-on-warm-start
behaviour make sense.

## What a run produces

Everything lands under one host directory, bind-mounted at `/data`:

| Deliverable | Path in the data directory | Produced by |
|---|---|---|
| Raw parquet mirror | `raw/<trip type>/<year>/` | `taxi-download` |
| Normalized parquet | `raw-normalized/<trip type>/<year>/` | `taxi-normalize` |
| Curated mappings, writable | `normalize/mappings/<trip type>.yaml` | seeded from the image on first run |
| Presized data file | `mssql/data/taxi.mdf` | `taxi-mssql` |
| Presized log file | `mssql/log/taxi_log.ldf` | `taxi-mssql` |
| Loaded tables, page-compressed | one per `(trip type, year)` in the database | `taxi-load` |
| Compressed, verified backup | `mssql/backup/taxi-sql/taxi/FULL/*.bak` | `taxi-mssql` |

The mapping YAMLs live on the volume rather than only in the image because
`taxi-normalize` amends them when it meets schema drift, and those amendments have to
survive the container. Preflight copies the image's pristine copies in on first run and
**never overwrites an existing one** — overwriting would discard an acknowledgment and
re-halt every subsequent run.

## Prerequisites

- Docker with Compose v2 (`docker compose`, not `docker-compose`).
- A host directory for the data, which **must exist before the first run**.
- Disk for it. The full data set is roughly 40–100 GB of parquet plus a database sized at
  `TAXI_DATA_FACTOR` × the normalized parquet (3× by default) plus a log file; a full run
  wants several hundred GB. Scope it down with `TAXI_TYPES` and `TAXI_RECENT` first.
- An SA password that satisfies SQL Server's complexity policy.

Nothing else: the image carries taxi-seed's CLIs, `sqlcmd`, the curated mappings, Ola's
`MaintenanceSolution.sql`, and the vendored DuckDB `mssql` extension, so a run needs no
network beyond the TLC bucket.

## The `mssql/.env` contract

Two settings are required. Put them in `mssql/.env`, which is gitignored:

```bash
cat > mssql/.env <<'ENV'
TAXI_DATA_DIR=/srv/taxi
MSSQL_PASSWORD=change-me-Str0ng!
ENV
```

`TAXI_DATA_DIR` is the host directory to fill, and it must exist first. `MSSQL_PASSWORD` must
satisfy SQL Server's complexity policy, or the `mssql` container exits during startup.
Compose refuses to start at all if either is unset, rather than defaulting to something
unhelpful.

Every other variable has a working default. `taxi-mssql --help` prints the same list:

| Variable | Default | Meaning |
|---|---|---|
| `TAXI_DATA_DIR` | — (**required**) | Host directory to fill. Bind-mounted at `/data` in both services. |
| `MSSQL_PASSWORD` | — (**required**) | SA password. Reaches the containers as an environment variable only — never an argument, so it does not land in `docker top`, in `ps` on the host, or in `sqlcmd`'s argv. |
| `TAXI_TYPES` | `yellow,green,fhv,fhvhv` | Comma-separated trip types to mirror, normalize and load. Processed in that canonical order whatever order you list. |
| `TAXI_RECENT` | *(unset — full history)* | Mirror only the most recent N months. The fast way to get a small, real data set. |
| `TAXI_DATA_FACTOR` | `3.0` | mdf size = normalized parquet bytes × this factor. |
| `MSSQL_DATA_SIZE_GB` | *(derived)* | Skip the estimate and use this mdf size instead. Consulted **only at database creation**. |
| `MSSQL_LOG_SIZE_GB` | *(derived)* | ldf size. Default is 25% of the mdf size, floored at 1 GB and capped at 16 GB. |
| `MSSQL_FILEGROWTH_MB` | `1024` | Autogrowth step for both files. Presizing exists so this never fires. |
| `MSSQL_MAX_MEMORY_MB` | *(derived)* | SQL Server `max server memory`. Default: host RAM minus the larger of 4 GB and 25%. Applied with `sp_configure` before provisioning. |
| `MSSQL_HOST` | `mssql` | The Compose service name of the SQL Server. |
| `MSSQL_PORT` | `1433` | |
| `MSSQL_DATABASE` | `taxi` | Must be a plain identifier (`[A-Za-z_][A-Za-z0-9_]*`) — it is interpolated into T-SQL. Validated at config time, before the hours-long mirror stage, not at provision time. |
| `MSSQL_SCHEMA` | `dbo` | Same identifier rule. |
| `MSSQL_USER` | `sa` | Not identifier-validated: the login only ever reaches `sqlcmd -U` and `taxi-load --user`, never a SQL string. |
| `TAXI_PROGRESS_INTERVAL_S` | `60` | Heartbeat cadence during the mirror and load stages. `0` disables it. |

Compose forwards every one of these and passes them through **empty when unset**, so
`config.from_env` stays the single owner of each default. That is deliberate: a Compose-side
`:-16` on `MSSQL_LOG_SIZE_GB` once meant the container always saw the variable as explicitly
set, so a 128 MB test data set demanded a 16 GB log.

!!! warning "Never put `.env` in the image"
    `mssql/.env` holds an SA password and the build context is the whole repo, so
    `.dockerignore` keeps it out. A password baked into a layer survives any later
    deletion of the file.

## Running it

```bash
cd mssql
docker compose up --build --abort-on-container-exit --exit-code-from seeder
```

Keep both flags. They make Compose stop the whole stack as soon as the seeder exits, and
they make `$?` the seeder's own exit code. Without them the `mssql` service keeps running
after the work finishes, and you are left watching a SQL Server log with no signal that the
run ended and no exit code.

The default run covers the complete data set: all four trip types, full history. Expect
several hours and a large database on the first run, and a near no-op on later ones. For a
first look, scope it down:

```bash
# in mssql/.env
TAXI_TYPES=green
TAXI_RECENT=2
```

The Compose project is named `taxi-mssql`; the service that does the work is named
`seeder`, because the stack already has a service called `mssql` — the SQL Server itself —
and a second one called `taxi-mssql` would be actively confusing. `seeder` names the role
within the stack.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Clean. Everything is current, or the run advanced the data directory. |
| 1 | Needs review. In practice, schema drift you must acknowledge in a curated mapping. |
| 2 | Operational failure. SQL Server unreachable, bad configuration, not enough disk, a load that failed. |

That is the **run-level** contract, the same three codes [`taxi-pipeline`](pipeline.md#exit-codes) summarises a run with. The tools underneath emit more than three — normalize has a `3` for a first-run scaffold, `taxi-load` distinguishes a partial load from a config error — and `taxi-mssql` collapses them. [Reference → Exit codes](../reference/exit-codes.md#taxi-mssql) has every tool's own table.

One translation is worth knowing about. `taxi-load` returns `1` for "failed mid-load" (a
dropped connection, a DuckDB error on one type), and `taxi-mssql` reports that as **2**,
not 1. Passing it through as 1 would tell an operator to go inspect a drift acknowledgment
that does not exist, when a plain re-run is the remedy.

### Watching a long run

The command above runs in the foreground, which is what you want in CI: it is the form that
gives you the exit code. For a multi-hour interactive run, start the stack detached and
follow the logs instead. A foreground run is tied to your terminal — if the terminal dies
the containers keep going, but you lose the output, and running `docker compose up` a second
time **recreates the seeder and discards whatever it was loading**.

```bash
cd mssql
docker compose up -d --build     # start detached
docker compose logs -f seeder    # follow; Ctrl-C detaches, nothing is killed
```

Every stage announces itself, and during mirror and load it prints a progress line every 60
seconds:

```text
[07:12:56] mirror yellow: starting
[07:13:56] mirror yellow: 37 files, 12 files/min, elapsed 1m00s
[07:13:42] load fhvhv: starting (1.61B rows expected)
[07:14:42] load fhvhv: 2.1M/1.61B rows (0.1%), 2.06M rows/min, elapsed 1m00s, eta 13h01m
```

Load progress is sampled from the tables themselves, so it keeps moving even inside the
single multi-hour `COPY` a full year takes. The mirror stage has no row count to sample, so
it counts normalized parquet files instead; the arithmetic is otherwise identical. Set
`TAXI_PROGRESS_INTERVAL_S` to change the cadence, or `0` to switch the heartbeat off. The
sampler never raises into the run — a progress line that cannot be computed prints as
"unavailable" rather than killing a load that is going fine.

To check on a run from another shell without touching it:

```bash
docker compose ps
docker compose logs --tail 20 seeder
```

A run that ends prints what now exists, so its value is visible without digging — one line per selected trip type, then the database and the backup (a `TAXI_TYPES=yellow` run here):

```text
=== /data ===
  yellow   204 raw,   204 normalized month file(s), 1,804,264,756 row(s) loaded
  database  taxi (mdf /data/mssql/data/taxi.mdf, ldf /data/mssql/log/taxi_log.ldf)
  backup    taxi_FULL_20261101_041233.bak (766.0 MB)
            /data/mssql/backup/taxi-sql/taxi/FULL
```

## The eight stages

`taxi-mssql` takes no arguments. It is configured entirely by the environment, and it runs
one fixed sequence of eight stages:

1. **preflight** — create the directories under `/data`, seed any missing curated mapping,
   then wait until SQL Server accepts a login.
2. **mirror**, per trip type — `taxi-pipeline <type> --data-dir /data`, which is download →
   normalize. Deliberately **not** `--load`: the database must exist, presized, before
   anything loads into it, and sizing it needs the normalized parquet on disk first. So the
   pipeline is split around provisioning rather than run end to end.
3. **memory** — apply `max server memory` with `sp_configure`, before the database exists.
4. **provision** — create the presized database, or attach one whose files outlived the
   server's memory of it (see [Warm start](#warm-start)).
5. **ola** — install Ola Hallengren's `MaintenanceSolution.sql` into `master`, once. It is
   vendored in this repo and installed through `sqlcmd`, because the script is `GO`-batch
   separated and only a client that understands `GO` can run it.
6. **load**, per trip type — `taxi-load <type> --require-existing-database`.
7. **backup** — a compressed, verified `FULL` backup through Ola, but only when stale.
8. **report** — print what now exists in the data directory, so the run's value is visible
   without digging. This is the output shown [above](#watching-a-long-run).

A stage that returns 1 or 2 stops the run: no trip type is loaded whose normalize halted,
and no database is backed up whose load failed. Partial progress is always safe, because
every stage resumes from an observable on disk or in the database.

**The load stage is the one exception.** A trip type that fails to load does not abandon the
others — each is independent, and a partial data set is more useful than none. Every type is
attempted, every failure is reported, the worst code wins, and the backup is skipped unless
everything was clean.

`--require-existing-database` is what makes splitting the pipeline safe. By the load stage
provisioning has created the presized database; if that stage were skipped or failed soft,
`taxi-load` must refuse rather than quietly create a server-default-sized database that
nobody notices until it fills up.

## Warm start {#warm-start}

The stack deliberately puts **no volume on `/var/opt/mssql`**. Every container start gets a
fresh `master` with no record of the `taxi` database, while the mdf and ldf live on the bind
mount and survive `docker compose down` regardless. Provisioning sees a database that is not
registered but whose files already exist, and **attaches** them instead of trying to create
over them — `CREATE DATABASE` over an existing file fails with Msg 5170 and keeps failing on
every subsequent run.

Persisting `master` instead would only trade that for two worse failures: a moved
`TAXI_DATA_DIR` leaves a stale path recorded in the old `master`, and a rotated
`MSSQL_PASSWORD` never takes effect, because SQL Server consumes `MSSQL_SA_PASSWORD` only on
the first initialization of `/var/opt/mssql`. An ephemeral `master` makes every start a first
initialization, so both just work.

The `mssql` service's `hostname` is pinned to `taxi-sql` for a related reason: Ola writes
backups to `<Directory>/<@@SERVERNAME>/<db>/<type>/`, and `@@SERVERNAME` follows the
container hostname. Left unpinned it is random per run, so every run would write to a new
directory and cleanup would never find the previous backup.

## Sizing, memory and disk

Presizing is the point of the database-shaping stages: allocate everything the database will
ever need up front, so the file never autogrows mid-load and never fragments.

The mdf size is the normalized parquet bytes for the **selected** trip types × `TAXI_DATA_FACTOR`,
rounded up — to the next 1 GB below 10 GB, to the next 10 GB above it. A 128 MB smoke run
therefore gets a 1 GB file rather than a flat 10 GB. The ldf defaults to 25% of that, floored
at 1 GB and capped at 16 GB; under `SIMPLE` recovery with `TABLOCK` bulk loads the log
truncates on checkpoint and never tracks data volume, so that cap is a generous ceiling, not
a target.

Before allocating, the run checks free space on the volume against mdf + ldf plus a flat
1 GB of headroom, and exits 2 if it will not fit. Presizing asks for everything up front, so
the real risk is running the volume to zero rather than missing an estimate by a few percent.

Two things only happen at creation time:

- **Sizing.** A database that already exists is not resized. If this run's scope implies a
  file more than 25% larger than what is allocated, provisioning prints a warning and
  explains the remedy: `DROP DATABASE`, delete the mdf and ldf, re-run. Deleting the files
  alone is not enough within one server lifetime — `master` still has the database
  registered, so the next run skips provisioning and then fails reading a file that is gone.
- **`MSSQL_SA_PASSWORD`.** See [Warm start](#warm-start).

`max server memory` is capped before provisioning to host RAM minus the larger of 4 GB and
25%. Override it for a differently-sized box with `MSSQL_MAX_MEMORY_MB` (minimum 1024).

## Load performance

Loading is **one `taxi-load` process per trip type**, each using DuckDB's in-process
`mssql_copy_parallel_writers`. There are no shards, no worker pool, and no year-level
integrity gate; if you have read the older design notes, see
[Parallel loading](../superpowers/specs/2026-08-15-parallel-loading-design.md), which is kept
as a historical record of the architecture that was removed.

The measurement behind that, on green 2015 (19.2M rows): in-process writers beat the old
process-per-month fan-out **1.65×** (47.9s vs 79.0s), and still won **1.44×** at matched
parallelism — so the advantage was the mechanism, not the degree.

`TABLOCK` matters more than the writer count, and the loader sets it explicitly. On the
page-compressed heaps this stack creates, turning it off is **3.2× slower** and produces a
**36% larger** table: the writers contend on `PAGELATCH_EX` at the insert point and rows land
in less densely packed pages. That is page-latch contention, not lost compression. A
clustered index buys nothing here, and clustered + `TABLOCK` + writers **fails outright**
with a partial load. The [loader guide](load.md#the-writers-flag) has the rest.

`taxi-mssql` does not expose a writer count. `taxi-load --writers N` does, and the default —
DuckDB's thread count capped at 8 — is the fastest configuration measured on these tables.

## After re-pinning `target:`, reload once

When a curated mapping's `target:` moves to adopt a new TLC column, the normalized parquet
gains a column while the existing year tables do not. `taxi-load`'s reconcile compares months
on disk, the load manifest and committed row counts — **never column sets** — so it chooses
`APPEND`, and the `COPY` then fails on a column mismatch. A plain re-run of the stack will not
fix it.

This happened on 2026-10-31: yellow and green were re-pinned from `2026-05` to `2026-06` to
adopt `request_source`. See
[Re-pinning `target:`](normalize.md#re-pinning-target-changes-the-table-shape).

The remedy is a one-time full reload of the affected trip types. The stack has no flag for
it, so run the loader directly inside it:

```bash
cd mssql
docker compose up -d mssql                       # SQL Server only
docker compose run --rm --entrypoint taxi-load seeder \
    yellow --data-dir /data --host mssql --database taxi \
    --require-existing-database --full-refresh
```

Then run the stack normally again. Alternatively, drop the affected year tables by hand and
let the next run rebuild them.

## Troubleshooting

**Q: Compose exits immediately with `set MSSQL_PASSWORD in .env` or `set TAXI_DATA_DIR in .env`.**
A: Neither has a default, on purpose. Create `mssql/.env` as above. Compose resolves `.env`
relative to the Compose file, so run `docker compose` from the `mssql/` directory.

**Q: The `mssql` container exits during startup and the log mentions the password policy.**
A: `MSSQL_PASSWORD` must satisfy SQL Server's complexity policy — at least 8 characters from
three of the four classes (upper, lower, digit, symbol).

**Q: `error: MSSQL_DATABASE must be a plain identifier` (or `MSSQL_SCHEMA`).**
A: Both are interpolated into T-SQL, so they are validated against `[A-Za-z_][A-Za-z0-9_]*`
at config time — before the mirror stage, not after hours of work. A name like `taxi-db` is
legal in SQL Server but rejected here; pick a plain identifier.

**Q: `needs-review: mirror stage for yellow exited 1`.**
A: `taxi-normalize` met schema drift it cannot resolve on its own and amended
`<data dir>/normalize/mappings/yellow.yaml` with the items to review. Edit that file on the
host — it is on the volume precisely so you can — and re-run. See the
[normalize guide](normalize.md).

**Q: `provision: WARNING database 'taxi' already exists with N GB allocated…`.**
A: Sizing only runs at creation. The file will autogrow in `MSSQL_FILEGROWTH_MB` steps, which
is the fragmentation presizing exists to avoid. Either accept it, or re-presize: `DROP
DATABASE [taxi]`, delete the mdf and ldf, re-run.

**Q: `the data volume cannot fit the planned allocation`.**
A: Free space on `TAXI_DATA_DIR` is below mdf + ldf + 1 GB. Give it more disk, or scope the
run down with `TAXI_TYPES` / `TAXI_RECENT`, or set `MSSQL_DATA_SIZE_GB` explicitly on a fresh
database.

**Q: `'taxi-pipeline' is not on PATH`.**
A: You are running `taxi-mssql` outside its image. The image installs taxi-seed's CLIs; a run
from a clone needs `uv sync` (or an equivalent install of taxi-seed) first.

**Q: `backup none` in the final report, after a successful-looking run.**
A: The backup stage is skipped when any load returned non-zero, and it is also skipped when the
newest `.bak` is already newer than the newest `loaded_at` in the load manifest — a run that
changed nothing does not take a new backup. A run that loaded rows and then died before
backing up is caught on the next run, because the load is then newer than the backup.

**Q: The run died mid-load. Is the database corrupt?**
A: No, but it may be inconsistent, and `taxi-load` is built for exactly this: a bulk load
commits per 100,000 rows, so a killed load can leave a year whose committed rows disagree with
the load manifest. The next run's reconcile sees that disagreement and truncate-reloads that
year. Re-running is the remedy.

**Q: I ran `docker compose up` again while a detached run was going, and it restarted.**
A: `docker compose up` recreates the seeder. Use `docker compose logs -f seeder` to watch a
running one, and `docker compose ps` to check on it.
