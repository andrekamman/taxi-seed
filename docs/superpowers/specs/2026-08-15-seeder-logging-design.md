# Seeder logging

**Date:** 2026-08-15
**Status:** Approved

!!! note "Written in taxi-lab; adopted here 2026-11-01"
    Designed while the seeder lived in `taxi-lab`, and moved here with the code (#24,
    #35). The progress and heartbeat design it describes is current; only the tool's name
    changed, from `taxi-seeder` to `taxi-mssql`. Note that the load stage is now announced
    per trip type rather than once per fan-out, which this spec predates.
**Repo:** `taxi-lab`
**Plan:** `docs/plans/2026-08-15-seeder-logging.md`

## Problem

A seeder run goes silent for hours at a time, and a silent run is
indistinguishable from a hung one.

Measured against a healthy run on 2026-08-15:

- `taxi_loader/cli.py::_process_type` prints exactly one line per trip type,
  after every year of that type has loaded. `yellow` ran **6h50m** and printed
  nothing until it finished.
- `taxi_seeder/cli.py` never announces what it is about to do. Nothing in the
  output says which stage or which trip type is live.
- `seeder/Dockerfile` sets no `PYTHONUNBUFFERED`. Under `docker compose up` the
  child's stdout is a pipe, so Python block-buffers it and even the lines that
  do exist can sit unflushed for minutes.

The operator's only recourse was to attach a SQL client and count rows by hand.

## Goal

Every stage announces itself, and a stage that spends hours inside a single
subprocess reports progress on a fixed heartbeat: how much is done, how fast it
is going, and how long is left.

## Non-goals

- Changing what any stage does, or how long it takes.
- Changing any exit code. Logging is observation only.
- A structured log format, log levels, or a logging framework. The consumer is
  `docker compose logs -f`, and plain stamped lines are what it renders well.
- Progress inside `provision`, `ola`, `preflight`, or `backup`. Each is minutes
  at most; a start/done pair is enough.

## Architecture

One new module, `seeder/src/taxi_seeder/progress.py`, plus wiring in `cli.py`.

```
progress.py
  utc_hms, emit                  stamped output
  humanize_count/_duration       pure formatters
  rows_per_min, eta_seconds      pure arithmetic
  format_progress                pure line assembly
  expected_rows                  parquet footers  -> load total
  normalized_files               filesystem count -> mirror sample
  Heartbeat                      daemon thread, every collaborator injected

sql.py      + loaded_table_rows(schema, data_type) -> T-SQL string
report.py   + table_rows(settings, data_type)      -> int | None
config.py   + Settings.progress_interval_s
cli.py      ~ stage announcements, Heartbeat around mirror and load
Dockerfile  + ENV PYTHONUNBUFFERED=1
```

### Why a new module rather than an addition to `report.py`

`report.py` renders a **final** summary once, from a finished state.
`progress.py` renders a **running** one repeatedly, from a moving state. They
share no logic and have opposite lifetimes.

### The pure/impure split

Everything above `Heartbeat` is pure: no clock, no filesystem, no database, no
IO. This is the repo's existing convention (`sql.py` builds SQL and never
executes it; `config.py` maps environment to `Settings` and touches nothing),
and it is what makes the interesting part — the arithmetic behind a percentage,
a rate, and an ETA — testable without a thread, a subprocess, or a wait.

`Heartbeat` takes `sample`, `clock`, `stamp`, `sleeper`, and `out` as injected
collaborators, so its tests drive it with a fake clock and a fake sleeper and
never wait on wall time.

`cli.py` owns all wiring. `stages.py` stays exactly what its docstring says it
is: a thin subprocess wrapper coupled to taxi-seed's documented CLI.

## Progress sources

Two long stages, two samplers. Both return `int | None`, and both are called
from the heartbeat thread, so both answer `None` rather than raise.

### load — committed table rows

`sql.loaded_table_rows` sums `sys.partitions.rows` over `index_id IN (0, 1)`
for every table named `<data_type>_%` in the configured schema.

Deliberately **not** `_load_manifest`, which is what `report.loaded_rows`
already reads: `execute_year_plan`'s RELOAD branch issues one `COPY` for a whole
year and writes its manifest rows only afterwards, so a year in RELOAD reports
zero manifest progress for its entire duration — about two hours for
`fhvhv_2019`. `sys.partitions` moves continuously, which is the whole point of a
heartbeat.

The `_` in `fhvhv_2019` is a `LIKE` wildcard and is escaped
(`LIKE 'fhvhv\_%' ESCAPE '\'`); unescaped, a hypothetical `fhvhvX2019` would
match too. Schema and trip type both go through `sql.validate_identifier`.

### load — total, from parquet footers

`progress.expected_rows` sums `num_rows` from the parquet footers under
`raw-normalized/<type>/`. Metadata only, no row scan — the whole `fhvhv` tree
answered in under a second on the 2026-08-15 dataset. This is what makes the
percentage and the ETA possible. When the tree is missing or duckdb fails, it
returns `None` and the line simply omits both.

### mirror — normalized file count

`progress.normalized_files` counts `raw-normalized/<type>/**/*.parquet`.

Mirror is `taxi-run`'s download plus normalize, and it is also a multi-hour
stage, so it gets a heartbeat rather than only a start/done pair. No total is
cheaply knowable while a mirror is in flight — the set of months to download is
not enumerated on disk before it is fetched — so the mirror line carries count,
rate, and elapsed, but no percentage and no ETA.

`format_progress` therefore takes a `unit` keyword, defaulting to `rows`, so the
mirror line reads `files` and `files/min` where the load line reads `rows` and
`rows/min`. Same formatter, same tests, one word different.

## Output format

One line per event, stamped `[HH:MM:SS]` in UTC to match the container's clock
and `docker logs --timestamps`.

```
[07:12:56] mirror yellow: starting
[07:13:56] mirror yellow: 37 files, 12 files/min, elapsed 1m00s
[07:18:23] mirror yellow: done in 5m27s (exit 0)
[07:13:42] load fhvhv: starting (1.61B rows expected)
[07:14:42] load fhvhv: 2.1M/1.61B rows (0.1%), 2.06M rows/min, elapsed 1m00s, eta 13h01m
```

Counts are abbreviated (`34.6M`, `1.61B`) because the exact digit never matters
in a progress line and the final report prints it exactly. Durations are
`5s` / `1m30s` / `12h44m` — the largest two units, never more.

A sample that comes back unknown degrades to
`load fhvhv: rows unavailable, elapsed 30s` rather than dropping the line.

## Configuration

`TAXI_PROGRESS_INTERVAL_S`, seconds between progress lines. Default 60. `0`
disables the heartbeat and starts no thread at all. Negative is a `ConfigError`.
Empty is unset, following the rule the rest of `from_env` already uses.

It is passed through in `docker-compose.yml` as `${TAXI_PROGRESS_INTERVAL_S:-}`
— empty when unset, never repeating a default `config.from_env` already owns.

`PYTHONUNBUFFERED=1` is set in the image rather than in the compose file so it
also reaches taxi-seed's own CLIs, which inherit the environment through
`stages.run`.

## Invariants

- **Exit codes are unchanged.** `_timed` announces and times a stage but does
  not catch: `cli.main`'s existing `except` block owns the error-to-exit-code
  mapping, and swallowing anything here would change a return code.
- **A heartbeat failure is never fatal.** Sampling errors are swallowed and
  reported as an unknown value. No progress line may kill a 13-hour load. This
  is the one place the repo permits a bare `except Exception`, in
  `expected_rows` and in `Heartbeat._loop`.
- **The password never enters argv.** `mssql.py` already owns that rule via
  `SQLCMDPASSWORD`; the new sampler goes through `mssql.scalar` and inherits it.
- **The heartbeat thread is a daemon and is joined on exit**, so a stage that
  ends between ticks does not leave a thread behind.
- **Prose says "trip type"; the identifier stays `data_type`** (`CONTEXT.md`).

## Testing

TDD, task by task, per the implementation plan. New coverage:

- `test_progress.py` — formatters, `expected_rows`, `normalized_files`, and a
  `Heartbeat` driven by a fake clock, a fake sleeper, and a tick cap, including
  the case where `sample` raises and the case where the thread is joined.
- `test_sql.py` — `loaded_table_rows` targets `sys.partitions`, escapes the
  `LIKE` wildcard, and rejects a malformed schema or trip type.
- `test_report.py` — `table_rows` parses the scalar and returns `None` when
  sqlcmd fails.
- `test_config.py` — default, disable, negative, and empty-is-unset.
- `test_cli.py` — every stage announces itself, and a failing stage still
  returns its own exit code.
- `test_compose.py` — its two env-var lists gain `TAXI_PROGRESS_INTERVAL_S`.

Adding a field to the frozen `Settings` dataclass breaks every test that
constructs it exhaustively; those constructions gain `progress_interval_s=60`.

Verification is the full pytest suite plus `docker compose config` and a build
of the seeder image. No live seed run — a real one is measured in hours, and
nothing in this change can only be proven by one.

## Documentation

`README.md` gains a "Watching a run" section. The 2026-08-15 incident was not a
code failure: the tmux server hosting `docker compose up` died, the client went
with it, the containers kept running, and the next `docker compose up`
**recreated the seeder and destroyed roughly 50 minutes of in-flight work**. The
docs make the detached form (`up -d`, then `logs -f`) the default advice.
