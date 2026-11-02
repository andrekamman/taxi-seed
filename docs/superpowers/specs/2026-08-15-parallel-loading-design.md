# Parallel loading

**Date:** 2026-08-15
**Status:** Approved — **the architecture described here was removed**

!!! warning "Historical record. Do not implement from this."
    This spec designed the month-shard fan-out: a supervisor dispatching one `taxi-load`
    process per month, with a year-level integrity gate to clean up after a killed worker.
    **That design was deleted in issue #19** and never moved into this repo.

    It was replaced by DuckDB's in-process `mssql_copy_parallel_writers`, which measured
    **1.65x faster** on 19.2M rows — and 1.44x faster at matched parallelism, so the gain
    was the mechanism rather than the degree. `shards.py`, `integrity.py`,
    `load_concurrent` and `TAXI_LOAD_WORKERS` no longer exist.

    What remains valuable here is the measurement that motivated parallelism at all: a
    single bulk stream was CPU-bound on one core, 3 MB/s against a disk capable of 1.3
    GB/s. That finding stands; only the remedy changed. See issue #16 for the numbers that
    replaced it.
**Repo:** `taxi-lab` (with a prerequisite change in `taxi-seed`)
**Plan:** `docs/plans/2026-08-15-parallel-loading.md`

## Problem

A load uses one core of eight, and the box is idle around it.

Measured on the live `fhvhv` load, 2026-08-15:

| Signal | Value | Reading |
|---|---|---|
| `sys.dm_exec_requests.dop` | 1 | single-threaded bulk stream |
| `cpu_time / total_elapsed_time` | 1417ms / 1620ms | 87% on-CPU, not waiting |
| `sqlservr` CPU | 100.0% of 8 cores | exactly one core |
| host CPU | 84.9% idle, 1.2% iowait | seven cores unused |
| mdf write throughput | 3.0 MB/s | against 1.3 GB/s measured capability |
| disk `%util` / `aqu-sz` | 0.30% / 0.01 | disk essentially idle |
| `WRITELOG` (120s delta) | 17 ms | minimal logging already working |
| top real wait | `ASYNC_NETWORK_IO` 7,957 ms | waiting on the duckdb client |
| row rate | 2.06M rows/min | 95.8 bytes/row compressed, 25 columns |

The bottleneck is per-row CPU on one core — page compression plus TDS row
processing — not disk and not memory. `fhvhv` alone is 1,607,275,004 rows, about
13 hours at this rate.

Separately, `sqlservr` holds 23.0 GB of anonymous memory, uncapped
(`committed_kb == committed_target_kb == 22,959 MB`), which is SQL Server's
default 80%-of-host behaviour. It caused no failure — 6.9 GB stayed available,
swap untouched — but it is unmanaged, and concurrent streams raise demand.

## Goal

Run several concurrent bulk streams instead of one, and cap SQL Server's memory
at a host-relative default that `.env` can override.

## The unit of work

One **shard** is one `(trip type, year, month)` — one normalized parquet file.

Shards write into the **existing per-year tables, unchanged**. `build_copy_sql`
already passes `TABLOCK true`, which takes a BU (bulk update) lock, and BU locks
are compatible with one another, so several processes may bulk-load different
months into `fhvhv_2019` at once. This is what SQL Server's parallel bulk import
is built on.

Each shard is its **own `taxi-load` OS process**. This is forced, not preferred:
the duckdb `mssql` extension keeps its ATTACH context process-global, as
`taxi_loader/cli.py`'s own teardown comment states, so two concurrent loads in
one process collide on it. A `ThreadPoolExecutor` in taxi-seeder only supervises
those subprocesses — its threads block in `subprocess.run` and do no work.

### Rejected alternatives

- **Ray.** A distributed-execution framework for a need that is N concurrent
  processes on one host. It would add a cluster runtime and an object store to a
  container whose job is to shell out to two CLIs.
- **Threads in one process.** Ruled out by the process-global ATTACH above.
- **Year shards.** On 6 workers, `fhvhv`'s eight years schedule to a ~165 min
  makespan against a ~130 min ideal; month shards reach the ideal. The larger
  gain is observability — a month completing is an event, where a 234M-row year
  is two hours of silence.
- **A table per month plus `UNION ALL` views.** The SQL 2000 partitioned-view
  pattern. Without a `CHECK` constraint per member table the optimizer cannot
  eliminate partitions, so every query against a total view would touch all 584
  month tables, and nested views do not help — the optimizer flattens them.
  Those constraints need a per-trip-type date column, and moving
  `year_table(data_type, year)` to a month grain is an upstream schema redesign
  across `load.py`, `manifest.py`, `reconcile.py` and `count_year_table`.
  Concurrent BU locks give the same sharding win with no schema change and no
  change for consumers.

## What month shards do not buy

Crash recovery stays **year-granular**. A bulk load commits per `FLUSH_ROWS`
(100,000) and is not atomic, so a shard killed mid-`COPY` leaves rows with no
manifest entry, and those orphans can only be identified at year grain by the
integrity gate. The pre-pass therefore resets the whole year.

The scheduler compensates: shards are ordered **year-major** — years by
descending total rows, months ascending within a year — so workers finish one
year before starting the next and a kill exposes one or two years rather than
six.

## The taxi-seed prerequisite

`taxi-load` has no month filter, so three changes land in **taxi-seed** first,
in `loader/src/taxi_loader/`:

1. **`--year` / `--month` filters**, applied inside `discover_month_files` —
   before `reconcile`, so reconcile's year set and its per-year
   `count_year_table` calls are scoped too. Filtering afterwards would leave
   every worker counting every year of its type on every invocation. `--month`
   requires `--year`.
2. **`--month` forces the APPEND path.** The RELOAD branch drops the year table;
   a worker doing that while five siblings load into it would destroy their
   work. Under `--month`, a year needing RELOAD is a `LoaderError`, not
   something to fix — taxi-lab's integrity pre-pass owns that.
3. **Race-safe DDL.** Six concurrent workers all call `ensure_manifest_table`,
   and the first workers for a given year all call `_ensure_table`.
   `IF NOT EXISTS` then `CREATE` is not atomic across sessions; one loses.
   Tolerate the duplicate-object error and re-check.

### Release sequencing

taxi-lab installs taxi-seed from PyPI, and taxi-seed's version comes from git
tags. The upstream change is therefore committed but **not released** as part of
this work, and taxi-lab's `taxi-seed>=0.2.0` floor is **left alone**: raising it
to a version PyPI does not serve would break dependency resolution for everyone,
including this repo's own test run.

Instead, `preflight` asks `taxi-load --help` whether it advertises `--month`, and
fails fast with exit 2 and an actionable message if it does not. Until taxi-seed
is released and the seeder image rebuilt, a live run stops there — deliberately
and legibly, rather than by having every shard rejected with an argparse error.

At release time the floor becomes `taxi-seed>=<new version>` and the capability
check remains as a guard against a stale image.

## Components

```
seeder/src/taxi_seeder/
  shards.py      NEW  (type, year, month) discovery + year-major ordering (pure)
  integrity.py   NEW  reset years whose committed rows disagree with the manifest
  sizing.py      +    max_server_memory_mb(host_total_mb)
  sql.py         +    set_max_server_memory, inconsistent_years, reset_year
  provision.py   +    apply_memory_cap
  preflight.py   +    the taxi-load --month capability check
  config.py      +    load_workers, mssql_max_memory_mb
  stages.py      +    --year/--month in load_cmd, load_shard, load_concurrent
  report.py      +    table_rows_total
  progress.py    +    expected_rows_total
  cli.py         ~    pre-pass, then fan out
```

`shards.py` is pure scheduling; `integrity.py` is the one place allowed to
truncate; `stages.py` stays a subprocess wrapper. That is the same split the repo
already draws between `sizing.py` and `provision.py`.

## The integrity pre-pass

Runs to completion **before any shard is dispatched**, serially, while nothing
else is running. It is the only component permitted to truncate.

Its job: guarantee that when month shards start, every year table is either
consistent with its manifest or empty — so upstream's `guard_month_mode` never
fires and no worker ever needs the RELOAD path.

A year is inconsistent when committed rows ≠ manifest rows. That is exactly
taxi-seed's own integrity gate, evaluated here because the *scheduling* decision
belongs to whoever dispatches the shards. The query is a `FULL OUTER JOIN`
because a year can have table rows and no manifest rows (a crashed load) or
manifest rows and no table (a dropped table).

## Memory cap

`max server memory (MB)` via `sp_configure`, applied at provision time, set to
`host_total_mb - max(4096, host_total_mb // 4)`, floored at 1024 MB, overridable
with `MSSQL_MAX_MEMORY_MB`.

`sp_configure` rather than the container's `MSSQL_MEMORY_LIMIT_MB` because it is
**dynamic** — no restart — and it lets the default be computed from the actual
host instead of hardcoded per machine. Host RAM comes from `/proc/meminfo`; when
that is unreadable the cap is skipped and SQL Server's own default is left alone
rather than guessed.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `TAXI_LOAD_WORKERS` | `min(6, max(1, cpu_count - 2))` | concurrent load streams; `1` restores serial-equivalent loading |
| `MSSQL_MAX_MEMORY_MB` | derived from host RAM | `max server memory`, floor 1024 |

Two cores are left for the OS, dockerd, and the duckdb side of every stream. The
cap of 6 is because beyond that the streams contend for one buffer pool without
adding throughput. Both are forwarded by Compose as `${VAR:-}` — empty when
unset — so `config.from_env` stays the single source of each default.

## Interaction with the progress heartbeat

The seeder-logging work (`docs/specs/2026-08-15-seeder-logging-design.md`) wraps
each stage in an announcement and, for mirror and load, a heartbeat. Fan-out
removes the per-trip-type load stage that heartbeat was attached to, so the load
phase is re-wired rather than left to rot:

- **Mirror is unchanged** — still one announced, heartbeated stage per trip type,
  sampling normalized file counts.
- **Load becomes one announced stage** covering the whole fan-out, with a
  heartbeat sampling committed rows summed across every selected trip type, and
  a total summed from the parquet footers of all of them. So the percentage,
  rate, and ETA now describe the run, not one type.
- **Each completed shard emits its own stamped line** through
  `load_concurrent`'s `on_done` callback. This is the observability win month
  shards were chosen for: an event every few minutes instead of hours of silence.

That needs `report.table_rows_total` and `progress.expected_rows_total`, each of
which sums the existing per-type function and answers `None` if any part is
unknown — the heartbeat's established degradation rule.

## Invariants

- **Exit codes keep their meanings:** `0` clean, `1` needs-review, `2`
  operational failure, worst wins. `load_exit_code` still maps taxi-load's `1`
  to `2`.
- **A shard failure must not abandon its siblings.** Every dispatched shard runs
  to completion or failure; results aggregate afterwards. A shard that raises is
  recorded as exit 2 rather than propagated. `StageCommandNotFoundError` is the
  one exception, re-raised because a missing `taxi-load` is a configuration
  failure every shard would hit.
- **Nothing creates a table per month.**
- **Only the pre-pass truncates**, and only before any fan-out.
- **Passwords never appear in argv** — `MSSQL_PASSWORD` goes through the
  environment, as `stages.run` already does.
- **Prose says "trip type"; the identifier stays `data_type`** (`CONTEXT.md`).

## Deliberate behaviour changes

1. **Serial loading halted at the first failing trip type; fan-out cannot**,
   because siblings are already in flight. The new contract: every dispatched
   shard finishes, the worst code wins, and the backup is skipped unless
   everything was clean.
2. **`taxi-load --month` refuses a year needing RELOAD** rather than performing
   it. That is the safety property the whole design rests on, and the integrity
   pre-pass is what keeps it from ever triggering in practice.

## Testing

TDD, task by task. New unit coverage for `shards` (discovery, year-major
ordering, contiguity), `integrity` (resets only inconsistent years, queries each
trip type once), `sizing`/`sql`/`provision` (the memory cap and its SQL),
`config` (both new variables), `stages` (`--year`/`--month` argv,
`load_concurrent` aggregation and sibling survival), `preflight` (the capability
check both ways), and `cli` (pre-pass before fan-out, year-major dispatch, worst
code wins, backup skipped on failure). Upstream: `--year`/`--month` filtering,
`--month` requires `--year`, the RELOAD guard, and both race-safe DDL paths.

Integration assertions are added for concurrency (`> 1` concurrent
`BULK INSERT`) and correctness (zero year tables disagreeing with their
manifest), marked `integration` and deselected by default.

**Verification for this change is unit tests plus `docker compose config` and a
seeder image build.** The live before/after measurement the plan asks for
requires a multi-hour run against a released taxi-seed, so the README records the
measured *baseline* only; no projected figure is presented as measured.

## Out of scope

Page compression is the other half of the per-row CPU cost. Loading into an
uncompressed heap and then
`ALTER INDEX ... REBUILD WITH (DATA_COMPRESSION = PAGE, MAXDOP = 8)` would move
that cost onto every core, at the price of scratch space and a second pass. It
belongs upstream in `execute_year_plan` and should be spiked and measured before
anyone plans it.
