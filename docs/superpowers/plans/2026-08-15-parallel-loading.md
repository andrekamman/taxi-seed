# Parallel Loading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load trip data with several concurrent bulk streams instead of one, turning a CPU-bound single core into 4–6 busy cores, and cap SQL Server's memory at a host-relative default that `.env` can override.

**Architecture:** The unit of parallelism is one **(trip type, year, month)** shard — one normalized parquet file. Shards write into the **existing year tables**, unchanged: `TABLOCK` on a bulk load takes a BU (bulk update) lock, and BU locks are compatible with each other, so several processes may load different months into `fhvhv_2019` at once. Each shard is its **own `taxi-load` OS process**, because the duckdb `mssql` extension keeps its ATTACH context process-global; a `ThreadPoolExecutor` in taxi-seeder only supervises those subprocesses. Before any fan-out, an integrity pre-pass resets any year whose committed rows disagree with its manifest, so every shard is a clean per-month append.

**Tech Stack:** Python 3.12 stdlib (`concurrent.futures`, `subprocess`), taxi-seed's `taxi-load` CLI, SQL Server 2022, pytest.

**Spec:** None — this plan carries its own evidence. See **Background** below, measured against the live run on 2026-08-15.

## Background: why one core, and why these shards

Measured on the live `fhvhv` load, 2026-08-15:

| Signal | Value | Reading |
|---|---|---|
| `sys.dm_exec_requests.dop` | **1** | single-threaded bulk stream |
| `cpu_time / total_elapsed_time` | 1417ms / 1620ms | **87% on-CPU**, not waiting |
| `sqlservr` CPU | **100.0%** of 8 cores | exactly one core |
| host CPU | 84.9% idle, 1.2% iowait | 7 cores unused |
| mdf write throughput | **3.0 MB/s** | vs **1.3 GB/s** measured capability |
| disk `%util` / `aqu-sz` | **0.30% / 0.01** | disk essentially idle |
| `WRITELOG` (120s delta) | **17 ms** | minimal logging already working |
| `PAGEIOLATCH_EX` (120s delta) | 48 ms | not IO-bound |
| top real wait | `ASYNC_NETWORK_IO` 7,957 ms | waiting on the duckdb client |
| row rate | 2.06M rows/min | 95.8 bytes/row compressed, 25 columns |

The bottleneck is per-row CPU on one core (page compression plus TDS row processing), not disk and not memory. `fhvhv` alone is **1,607,275,004 rows** — about 13 hours at this rate.

**Why not Ray:** Ray is a distributed-execution framework. The need is N concurrent bulk streams on one host, which the stdlib already expresses. Ray would add a large dependency, a cluster runtime, and an object store to a container whose job is to shell out to two CLIs.

**Why not threads in one process:** ruled out by taxi-seed, not by preference. `taxi_loader/cli.py`'s teardown says so directly:

> `# Release the process-global mssql attach context; the extension keeps it alive across DuckDB connections, so a leaked attach would collide with the next run in the same process.`

The ATTACH is **process-global**, so two concurrent loads in one process collide on it even with separate duckdb connections. Threads still appear here, but only as supervisors blocked in `subprocess.run`, releasing the GIL and doing no work.

**Why month shards, not year shards:** year-level fan-out leaves the largest year as an irreducible tail. On 6 workers, `fhvhv`'s eight years (243.6M, 239.5M, 234.6M, 232.5M, 212.4M, 174.6M, 143.3M, 126.8M rows) schedule to a ~165 min makespan against a ~130 min ideal. Month shards reach the ideal. The larger gain is observability: a month completing is an event, where a 234M-row year is two hours of silence.

**Why the existing year tables, not month tables:** month tables plus `UNION ALL` views would be the SQL 2000 partitioned-view pattern. Without a `CHECK` constraint per member table the optimizer cannot eliminate partitions, so every query against a total view would touch all **584** month tables, and nested views do not help — the optimizer flattens them. Adding those constraints needs a per-trip-type date column, and moving `year_table(data_type, year)` to a month grain is an upstream schema redesign touching `load.py`, `manifest.py`, `reconcile.py` and `count_year_table`. Concurrent BU locks give the same sharding win with no schema change, no views, and no change for consumers — `k6-loadtest` reads its table from config (`target["table"]`) and is unaffected either way.

**What month shards do *not* buy — state this plainly:** crash recovery stays **year-granular**. A bulk load commits per `FLUSH_ROWS` (100,000) and is not atomic, so a shard killed mid-`COPY` leaves rows with no manifest entry, and those orphans can only be identified at year grain by the integrity gate. The pre-pass therefore resets the whole year. The scheduler compensates by ordering shards **year-major**, so workers finish one year before starting the next and a kill exposes one or two years rather than six.

**Memory, separately:** `sqlservr` holds **23.0 GB of anonymous memory** (`RssAnon` 24,088,672 kB), uncapped, with `committed_kb == committed_target_kb == 22,959 MB` — SQL Server's default 80%-of-host behaviour (80% of 32,076 MB = 25,661 MB, exactly the `physical_memory_kb` it reports). It caused no failure — 6.9 GB stayed available, swap untouched — but it is unmanaged, and concurrent streams raise demand.

## Global Constraints

- **Exit codes are unchanged:** `0` clean, `1` needs-review, `2` operational failure; higher wins when aggregating. `load_exit_code` still maps taxi-load's `1` to `2`.
- **A shard failure must not abandon its siblings.** Every dispatched shard runs to completion or failure; results aggregate afterwards.
- **Shards write into existing year tables.** Concurrency is safe because `build_copy_sql` passes `TABLOCK true` and BU locks are mutually compatible. Nothing in this plan creates a table per month.
- **The integrity pre-pass runs before any fan-out**, and is the only thing allowed to truncate. A shard must never issue DDL that a sibling could be loading through — that is why upstream's year-level RELOAD branch must not fire when `--month` is given.
- **Shard ordering is year-major**, years by descending total rows, months ascending within a year. This bounds how many years a kill can expose.
- **Passwords never appear in argv.** `MSSQL_PASSWORD` goes through the environment, as `stages.run` already does.
- **Default worker count:** `min(6, max(1, cpu_count - 2))`. Two cores are left for the OS, dockerd, and the duckdb side of each stream.
- **`max server memory` default:** `host_total_mb - max(4096, host_total_mb // 4)`, floored at 1024 MB. Overridable with `MSSQL_MAX_MEMORY_MB`.
- **Prose says "trip type"**, the identifier stays `data_type` (see `CONTEXT.md`).
- **Prerequisite:** Task 2 lands in taxi-seed and is released before Tasks 5–7 can pass. This mirrors the existing precedent — the seeder-container plan's Task 1 was also a taxi-seed change.

## File Structure

```
seeder/
  docker-compose.yml                  + MSSQL_MAX_MEMORY_MB, TAXI_LOAD_WORKERS
  src/taxi_seeder/
    shards.py         NEW  (type, year, month) discovery + year-major ordering (pure)
    integrity.py      NEW  reset years whose rows disagree with the manifest
    sizing.py         +    max_server_memory_mb(host_total_mb)
    sql.py            +    set_max_server_memory, inconsistent_years, reset_year
    provision.py      +    apply the memory cap
    config.py         +    load_workers, mssql_max_memory_mb
    stages.py         +    --year/--month in load_cmd, load_shard, load_concurrent
    cli.py            ~    pre-pass then fan out
tests/taxi_seeder/
    test_shards.py    NEW
    test_integrity.py NEW
    test_sizing.py test_sql.py test_provision.py test_config.py
    test_stages.py test_cli.py                    all extended
```

`shards.py` is pure scheduling; `integrity.py` is the one place allowed to truncate; `stages.py` stays a subprocess wrapper. That is the same split the repo already draws between `sizing.py` and `provision.py`.

---

### Task 1: Cap SQL Server memory, host-relative, overridable

**Files:**
- Modify: `seeder/src/taxi_seeder/sizing.py`, `seeder/src/taxi_seeder/sql.py`, `seeder/src/taxi_seeder/config.py`, `seeder/src/taxi_seeder/provision.py`, `seeder/docker-compose.yml`, `seeder/src/taxi_seeder/cli.py` (help epilog)
- Test: `tests/taxi_seeder/test_sizing.py`, `tests/taxi_seeder/test_sql.py`, `tests/taxi_seeder/test_config.py`, `tests/taxi_seeder/test_provision.py`

**Interfaces:**
- Consumes: `mssql.run`, `config._int` (existing).
- Produces: `sizing.max_server_memory_mb(host_total_mb: int) -> int`, `sql.set_max_server_memory(mb: int) -> str`, `Settings.mssql_max_memory_mb: int | None`, `provision.apply_memory_cap(settings, *, runner=None, meminfo=None) -> int | None`.

Independent of the rest of the plan and shippable on its own. First because concurrent streams raise memory demand, and an uncapped server is a worse neighbour once six are running.

`sp_configure 'max server memory (MB)'` rather than the container's `MSSQL_MEMORY_LIMIT_MB`: it is **dynamic** (no restart), and it lets the default be computed from the actual host instead of hardcoded per machine.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/taxi_seeder/test_sizing.py
import pytest
from taxi_seeder import sizing


@pytest.mark.parametrize("host_mb,expected", [
    (32076, 24057),   # the 32 GB devbox -> ~24 GB, reserve 25%
    (16384, 12288),   # 16 GB -> reserve the 4 GB floor
    (8192, 4096),     # 8 GB  -> reserve the 4 GB floor
    (65536, 49152),   # 64 GB -> reserve 25%
    (2048, 1024),     # tiny  -> clamped to the 1 GB floor
])
def test_max_server_memory_mb(host_mb, expected):
    assert sizing.max_server_memory_mb(host_mb) == expected


def test_max_server_memory_mb_rejects_nonsense():
    with pytest.raises(ValueError):
        sizing.max_server_memory_mb(0)
```

```python
# append to tests/taxi_seeder/test_sql.py
def test_set_max_server_memory_reconfigures():
    out = sql.set_max_server_memory(24057)
    assert "sp_configure 'show advanced options', 1" in out
    assert "sp_configure 'max server memory (MB)', 24057" in out
    assert out.count("RECONFIGURE") == 2


def test_set_max_server_memory_rejects_a_non_integer():
    with pytest.raises(ValueError):
        sql.set_max_server_memory("24057; DROP DATABASE taxi")
```

```python
# append to tests/taxi_seeder/test_config.py
def test_mssql_max_memory_defaults_to_none():
    assert from_env({"MSSQL_PASSWORD": "pw"}).mssql_max_memory_mb is None


def test_mssql_max_memory_is_read_when_set():
    s = from_env({"MSSQL_PASSWORD": "pw", "MSSQL_MAX_MEMORY_MB": "24576"})
    assert s.mssql_max_memory_mb == 24576


def test_mssql_max_memory_rejects_below_the_floor():
    with pytest.raises(ConfigError, match="MSSQL_MAX_MEMORY_MB"):
        from_env({"MSSQL_PASSWORD": "pw", "MSSQL_MAX_MEMORY_MB": "512"})
```

```python
# append to tests/taxi_seeder/test_provision.py
from taxi_seeder import provision


def test_apply_memory_cap_uses_the_override(settings_with):
    sent = []

    def runner(cmd, env, stdin_text):
        sent.append(cmd)
        return _Proc(0, "")

    s = settings_with(mssql_max_memory_mb=20000)
    assert provision.apply_memory_cap(s, runner=runner, meminfo=lambda: 32076) == 20000
    assert any("20000" in " ".join(c) for c in sent)


def test_apply_memory_cap_derives_from_host_ram_when_unset(settings_with):
    s = settings_with(mssql_max_memory_mb=None)
    assert provision.apply_memory_cap(s, runner=lambda *a: _Proc(0, ""),
                                     meminfo=lambda: 32076) == 24057


def test_apply_memory_cap_skips_when_host_ram_is_unreadable(settings_with):
    s = settings_with(mssql_max_memory_mb=None)
    assert provision.apply_memory_cap(s, runner=lambda *a: _Proc(0, ""),
                                     meminfo=lambda: None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/taxi_seeder/test_sizing.py tests/taxi_seeder/test_sql.py tests/taxi_seeder/test_config.py tests/taxi_seeder/test_provision.py -v`
Expected: FAIL — `AttributeError: module 'taxi_seeder.sizing' has no attribute 'max_server_memory_mb'`

- [ ] **Step 3: Write minimal implementation**

Append to `seeder/src/taxi_seeder/sizing.py`:

```python
MIN_SERVER_MEMORY_MB = 1024


def max_server_memory_mb(host_total_mb: int) -> int:
    """How much RAM SQL Server may have, given the whole host.

    Aggressive but not greedy: reserve a quarter of the box, never less than
    4 GB, for the OS, dockerd, and the duckdb side of every load stream. Left
    alone, SQL Server on Linux takes 80% of the host and never gives it back --
    measured at 22,959 MB committed on a 32 GB box on 2026-08-15.
    """
    if host_total_mb < 1:
        raise ValueError(f"host_total_mb must be >= 1, got {host_total_mb}")
    reserve = max(4096, host_total_mb // 4)
    return max(MIN_SERVER_MEMORY_MB, host_total_mb - reserve)
```

Append to `seeder/src/taxi_seeder/sql.py`:

```python
def set_max_server_memory(mb: int) -> str:
    """Cap the buffer pool. Dynamic -- no restart, unlike memory.memorylimitmb."""
    if isinstance(mb, bool) or not isinstance(mb, int):
        raise ValueError(f"max server memory must be an int, got {mb!r}")
    if mb < 1:
        raise ValueError(f"max server memory must be >= 1, got {mb}")
    return (
        "EXEC sp_configure 'show advanced options', 1;\n"
        "RECONFIGURE;\n"
        f"EXEC sp_configure 'max server memory (MB)', {mb};\n"
        "RECONFIGURE;"
    )
```

In `seeder/src/taxi_seeder/config.py`, add `mssql_max_memory_mb: int | None` to `Settings`, and in `from_env` before the `return`:

```python
    max_mem_raw = (env.get("MSSQL_MAX_MEMORY_MB") or "").strip()
    mssql_max_memory_mb = _int(env, "MSSQL_MAX_MEMORY_MB", 0) if max_mem_raw else None
    if mssql_max_memory_mb is not None and mssql_max_memory_mb < 1024:
        raise ConfigError(
            f"MSSQL_MAX_MEMORY_MB must be >= 1024, got {mssql_max_memory_mb}"
        )
```

plus `mssql_max_memory_mb=mssql_max_memory_mb,` in the constructor call.

Append to `seeder/src/taxi_seeder/provision.py` (adding `from taxi_seeder import sizing` to its imports):

```python
def _host_total_mb() -> int | None:
    """Host RAM in MB from /proc/meminfo.

    The mssql service has no container memory limit, so the host total is the
    number that matters. None when unreadable -- on a platform where this file
    is absent we leave SQL Server's own default alone rather than guess.
    """
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def apply_memory_cap(settings: Settings, *, runner=None, meminfo=None) -> int | None:
    """Set `max server memory`. Returns the cap applied, or None if skipped."""
    if settings.mssql_max_memory_mb is not None:
        cap = settings.mssql_max_memory_mb
        source = "MSSQL_MAX_MEMORY_MB"
    else:
        host_mb = (meminfo or _host_total_mb)()
        if host_mb is None:
            print("memory: host RAM unreadable, leaving max server memory at "
                  "SQL Server's default (80% of host)")
            return None
        cap = sizing.max_server_memory_mb(host_mb)
        source = f"derived from {host_mb} MB host RAM"
    mssql.run(settings, sql.set_max_server_memory(cap),
              database="master", runner=runner)
    print(f"memory: max server memory set to {cap} MB ({source})")
    return cap
```

Call `apply_memory_cap(settings)` at the top of `provision.run`.

In `seeder/docker-compose.yml`, under the `seeder` service `environment:`:

```yaml
      # Cap for SQL Server's buffer pool. Unset -> derived from host RAM as
      # total - max(4 GB, 25%), applied with sp_configure at provision time.
      MSSQL_MAX_MEMORY_MB: ${MSSQL_MAX_MEMORY_MB:-}
```

In `cli.py`'s `_ENV_VARS_HELP`, after `MSSQL_FILEGROWTH_MB`:

```
  MSSQL_MAX_MEMORY_MB   SQL Server 'max server memory' in MB (default:
                        host RAM minus the larger of 4 GB and 25%)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/taxi_seeder/ -v`
Expected: PASS. Add `mssql_max_memory_mb=None` to every exhaustive `Settings(...)` construction in the test suite.

- [ ] **Step 5: Commit**

```bash
git add seeder/src/taxi_seeder/ seeder/docker-compose.yml tests/taxi_seeder/
git commit -m "feat(seeder): cap max server memory from host RAM, overridable via .env"
```

---

### Task 2: taxi-seed — `--year`, `--month`, and race-safe DDL

**Files (in the `taxi-seed` repo, not this one):**
- Modify: `src/taxi_loader/cli.py`, `src/taxi_loader/load.py`, `src/taxi_loader/manifest.py`
- Test: `tests/taxi_loader/test_cli.py`, `tests/taxi_loader/test_load.py`

**Interfaces:**
- Produces: `taxi-load <type> --year <YYYY> --month <M>` loads exactly one month; `_ensure_table` and `ensure_manifest_table` tolerate a concurrent creator.

Three changes, all small, all required before taxi-lab can fan out:

1. **`--year` / `--month` filters**, applied inside `discover_month_files` — *before* `reconcile`, so reconcile's year set and its per-year `count_year_table` calls are scoped too. Filtering afterwards would leave every worker counting every year of its type on every invocation.
2. **`--month` forces the APPEND path.** The RELOAD branch drops the year table; a worker doing that while five siblings load into it would destroy their work. With `--month` given, a year needing RELOAD is an error, not something to fix — taxi-lab's integrity pre-pass owns that (Task 5).
3. **Race-safe DDL.** Six concurrent workers all call `ensure_manifest_table`, and the first six for a given year all call `_ensure_table`. `IF NOT EXISTS` then `CREATE` is not atomic across sessions; one loses. Tolerate the duplicate-object error and re-check.

- [ ] **Step 1: Write the failing test**

```python
# tests/taxi_loader/test_cli.py
import pytest


def _tree(root, spec):
    for year, months in spec.items():
        d = root / "fhvhv" / str(year)
        d.mkdir(parents=True, exist_ok=True)
        for m in months:
            (d / f"fhvhv_tripdata_{year}-{m:02d}.parquet").touch()


def test_year_and_month_filter_discovery(tmp_path, monkeypatch):
    _tree(tmp_path, {2019: [2, 3], 2020: [1]})
    monkeypatch.setattr(cli.load, "parquet_row_count", lambda conn, f: 1)

    months = cli.discover_month_files(None, str(tmp_path), "fhvhv",
                                      year=2019, month=3)
    assert [(m.year, m.month) for m in months] == [(2019, 3)]


def test_year_filter_alone_keeps_every_month(tmp_path, monkeypatch):
    _tree(tmp_path, {2019: [2, 3], 2020: [1]})
    monkeypatch.setattr(cli.load, "parquet_row_count", lambda conn, f: 1)

    months = cli.discover_month_files(None, str(tmp_path), "fhvhv", year=2019)
    assert sorted(m.month for m in months) == [2, 3]


def test_no_filter_keeps_everything(tmp_path, monkeypatch):
    _tree(tmp_path, {2019: [2, 3], 2020: [1]})
    monkeypatch.setattr(cli.load, "parquet_row_count", lambda conn, f: 1)

    months = cli.discover_month_files(None, str(tmp_path), "fhvhv")
    assert len(months) == 3


def test_parse_args_accepts_year_and_month():
    args = cli.parse_args(["fhvhv", "--year", "2019", "--month", "3"])
    assert (args.year, args.month) == (2019, 3)


def test_month_requires_year():
    with pytest.raises(SystemExit):
        cli.parse_args(["fhvhv", "--month", "3"])


def test_month_mode_refuses_a_year_needing_reload(monkeypatch):
    """RELOAD drops the year table -- fatal while siblings are loading into it."""
    plan = cli.YearPlan(2019, cli.RELOAD, [])
    with pytest.raises(cli.LoaderError, match="integrity"):
        cli.guard_month_mode([plan], month=3)


def test_month_mode_allows_append_and_skip():
    plans = [cli.YearPlan(2019, cli.APPEND, []), cli.YearPlan(2020, cli.SKIP, [])]
    assert cli.guard_month_mode(plans, month=3) is None
```

```python
# tests/taxi_loader/test_load.py
def test_ensure_table_tolerates_a_concurrent_creator(monkeypatch):
    """Two workers loading two months of one year both try to CREATE it."""
    calls = {"exists": 0}

    def table_exists(conn, cfg, table):
        calls["exists"] += 1
        return calls["exists"] > 1        # absent first, present on re-check

    def execute(sql_text):
        raise load.duckdb.Error("There is already an object named 'fhvhv_2019'")

    monkeypatch.setattr(load, "table_exists", table_exists)
    # Should return quietly rather than raise.
    load._ensure_table(_FakeConn(execute), _cfg(), "fhvhv_2019", "x.parquet")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/taxi_loader/ -v -k "month or ensure_table"`
Expected: FAIL — `TypeError: discover_month_files() got an unexpected keyword argument 'month'`

- [ ] **Step 3: Write minimal implementation**

In `src/taxi_loader/cli.py`, add the arguments and cross-validation:

```python
    p.add_argument("--year", type=int, default=None,
                   help="load only this year; omit for every year on disk")
    p.add_argument("--month", type=int, default=None, choices=range(1, 13),
                   metavar="{1..12}",
                   help="load only this month of --year. Each month is a "
                        "separate parquet file, so separate processes may load "
                        "different months of one year concurrently -- TABLOCK "
                        "takes a BU lock, and BU locks are compatible.")
```

and immediately after `args = p.parse_args(argv)` in `parse_args`:

```python
    if args.month is not None and args.year is None:
        p.error("--month requires --year")
    return args
```

Filter during the walk:

```python
def discover_month_files(conn: duckdb.DuckDBPyConnection, input_dir,
                         data_type: str, year: int | None = None,
                         month: int | None = None) -> list[MonthFile]:
    base = Path(input_dir) / data_type
    months: list[MonthFile] = []
    if not base.exists():
        return months
    for f in sorted(base.rglob("*.parquet")):
        m = _MONTH_RE.search(f.name)
        if not m:
            continue
        file_year, file_month = int(m.group(1)), int(m.group(2))
        # Filter here, not after reconcile: reconcile derives its year set and
        # its per-year count_year_table calls from this list, and a worker that
        # owns one month must not count or plan the other 583.
        if year is not None and file_year != year:
            continue
        if month is not None and file_month != month:
            continue
        months.append(MonthFile(file_year, file_month, str(f),
                                load.parquet_row_count(conn, f)))
    return months
```

Add the month-mode guard:

```python
def guard_month_mode(plans, month: int | None) -> None:
    """In --month mode a RELOAD plan is fatal, not something to execute.

    RELOAD drops and recreates the year table. With several workers loading
    different months of that same year, one of them doing that would destroy
    the others' work mid-flight. A year that needs rebuilding must be reset by
    the caller before any month of it is dispatched.
    """
    if month is None:
        return None
    bad = [p.year for p in plans if p.action == RELOAD]
    if bad:
        raise LoaderError(
            f"year(s) {bad} fail the integrity check (committed rows disagree "
            f"with the manifest) and need a truncate+reload, which --month mode "
            f"will not do while sibling months may be loading. Reset the year "
            f"first, then re-dispatch its months."
        )
    return None
```

Thread both through `_process_type`, calling the guard between `reconcile` and execution:

```python
def _process_type(conn, cfg, data_type: str, input_dir: str,
                  flush_rows: int, full_refresh: bool, dry_run: bool,
                  attached: bool, year: int | None = None,
                  month: int | None = None) -> int:
    disk = discover_month_files(conn, input_dir, data_type, year=year, month=month)
    if not disk:
        scope = "".join([f" {year}" if year else "", f"-{month:02d}" if month else ""])
        print(f"{data_type}{scope}: no parquet under {input_dir}/{data_type}, skipping")
        return 0
```

Scope every `manifest_rows` assignment the same way so reconcile compares like with like:

```python
        if year is not None:
            manifest_rows = [r for r in manifest_rows if r.year == year]
        if month is not None:
            manifest_rows = [r for r in manifest_rows if r.month == month]
```

and after `plans = reconcile(...)`:

```python
    guard_month_mode(plans, month)
```

Pass them at the call site in `main`: `year=args.year, month=args.month`.

In `src/taxi_loader/load.py`, make `_ensure_table` race-safe:

```python
def _ensure_table(conn, cfg, table, sample_parquet) -> None:
    if table_exists(conn, cfg, table):
        return
    ddl = build_create_table_sql(conn, _fq(cfg, table), sample_parquet)
    try:
        conn.execute(f"SELECT mssql_exec('{ATTACH_NAME}', ?)", [ddl])
    except duckdb.Error:
        # Another worker loading a different month of this same year raced us to
        # the CREATE. IF NOT EXISTS + CREATE is not atomic across sessions, so
        # losing the race is expected, not an error -- re-check and continue.
        if not table_exists(conn, cfg, table):
            raise
```

Apply the identical pattern to `ensure_manifest_table` in `src/taxi_loader/manifest.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/taxi_loader/ -v`
Expected: PASS

- [ ] **Step 5: Commit, release, and bump the floor here**

```bash
git commit -am "feat(loader): add --year/--month and race-safe DDL for parallel loading"
```

Release taxi-seed, then raise taxi-lab's floor in `pyproject.toml` to the new version, e.g. `"taxi-seed>=0.3.0"`, with a comment naming `--month` and the race-safe DDL as the reason.

---

### Task 3: Configure the worker count

**Files:**
- Modify: `seeder/src/taxi_seeder/config.py`, `seeder/docker-compose.yml`, `seeder/src/taxi_seeder/cli.py` (help epilog)
- Test: `tests/taxi_seeder/test_config.py`

**Interfaces:**
- Produces: `Settings.load_workers: int`, `config.default_load_workers(cpu_count: int | None) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/taxi_seeder/test_config.py
from taxi_seeder import config


@pytest.mark.parametrize("cpus,expected", [
    (8, 6), (4, 2), (3, 1), (2, 1), (1, 1), (32, 6), (None, 1),
])
def test_default_load_workers(cpus, expected):
    assert config.default_load_workers(cpus) == expected


def test_load_workers_defaults_from_cpu_count(monkeypatch):
    monkeypatch.setattr(config.os, "cpu_count", lambda: 8)
    assert from_env({"MSSQL_PASSWORD": "pw"}).load_workers == 6


def test_load_workers_can_be_pinned():
    assert from_env({"MSSQL_PASSWORD": "pw",
                     "TAXI_LOAD_WORKERS": "3"}).load_workers == 3


def test_load_workers_rejects_zero():
    with pytest.raises(ConfigError, match="TAXI_LOAD_WORKERS"):
        from_env({"MSSQL_PASSWORD": "pw", "TAXI_LOAD_WORKERS": "0"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/taxi_seeder/test_config.py -v -k workers`
Expected: FAIL — `AttributeError: module 'taxi_seeder.config' has no attribute 'default_load_workers'`

- [ ] **Step 3: Write minimal implementation**

At the top of `config.py` add `import os`, then:

```python
MAX_LOAD_WORKERS = 6


def default_load_workers(cpu_count: int | None) -> int:
    """Concurrent load streams to run when TAXI_LOAD_WORKERS is unset.

    Each stream saturates about one SQL Server core (measured dop=1, 87%
    on-CPU on 2026-08-15), so leave two cores for the OS, dockerd, and the
    duckdb side of every stream. Capped at 6: beyond that the streams contend
    for one buffer pool without adding throughput.
    """
    if not cpu_count or cpu_count < 3:
        return 1
    return min(MAX_LOAD_WORKERS, cpu_count - 2)
```

Add `load_workers: int` to `Settings`, and in `from_env`:

```python
    load_workers = _int(env, "TAXI_LOAD_WORKERS", default_load_workers(os.cpu_count()))
    if load_workers < 1:
        raise ConfigError(f"TAXI_LOAD_WORKERS must be >= 1, got {load_workers}")
```

plus `load_workers=load_workers,` in the constructor call.

In `docker-compose.yml`, under the `seeder` service `environment:`:

```yaml
      # Concurrent (trip type, year, month) load streams.
      # Unset -> cpu_count - 2, capped at 6. 1 restores serial loading.
      TAXI_LOAD_WORKERS: ${TAXI_LOAD_WORKERS:-}
```

In `_ENV_VARS_HELP`:

```
  TAXI_LOAD_WORKERS     concurrent load streams (default: CPU count minus 2,
                        capped at 6; 1 restores serial loading)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/taxi_seeder/ -v`
Expected: PASS (add `load_workers=1` to exhaustive `Settings(...)` constructions)

- [ ] **Step 5: Commit**

```bash
git add seeder/src/taxi_seeder/config.py seeder/docker-compose.yml \
        seeder/src/taxi_seeder/cli.py tests/taxi_seeder/test_config.py
git commit -m "feat(seeder): add TAXI_LOAD_WORKERS with a cpu-relative default"
```

---

### Task 4: Discover and order the month shards

**Files:**
- Create: `seeder/src/taxi_seeder/shards.py`
- Test: `tests/taxi_seeder/test_shards.py`

**Interfaces:**
- Consumes: `Settings.raw_normalized_dir`, `Settings.types`.
- Produces: `shards.Shard(data_type: str, year: int, month: int, rows: int)` with `.label`, `shards.discover(settings, *, footer_reader=None) -> list[Shard]`, `shards.order(items) -> list[Shard]`, `shards.years(items) -> list[tuple[str, int]]`.

`order` is **year-major**: years sorted by descending total rows, months ascending inside a year. Not per-shard longest-first — that would scatter six workers across six different years, and because crash recovery is year-granular (see Background), a kill would then expose six partially-loaded years instead of one or two.

- [ ] **Step 1: Write the failing test**

```python
# tests/taxi_seeder/test_shards.py
from pathlib import Path

import pytest

from taxi_seeder import shards
from taxi_seeder.config import Settings


@pytest.fixture
def settings_for():
    def make(data_dir, types=("green", "fhvhv")):
        return Settings(
            data_dir=Path(data_dir), types=types, recent=None, password="pw",
            host="mssql", port=1433, database="taxi", schema="dbo", user="sa",
            data_factor=3.0, data_size_gb=None, log_size_gb=None,
            filegrowth_mb=1024, mssql_max_memory_mb=None, load_workers=6,
        )
    return make


def _tree(root, spec):
    for data_type, years in spec.items():
        for year, months in years.items():
            d = root / "raw-normalized" / data_type / str(year)
            d.mkdir(parents=True, exist_ok=True)
            for m in months:
                (d / f"{data_type}_tripdata_{year}-{m:02d}.parquet").touch()


def test_discover_finds_one_shard_per_month(tmp_path, settings_for):
    _tree(tmp_path, {"green": {2025: [1, 2]}, "fhvhv": {2019: [2]}})
    found = shards.discover(settings_for(tmp_path), footer_reader=lambda p: 100)
    assert {(s.data_type, s.year, s.month) for s in found} == {
        ("green", 2025, 1), ("green", 2025, 2), ("fhvhv", 2019, 2)
    }
    assert all(s.rows == 100 for s in found)


def test_discover_honours_the_selected_types(tmp_path, settings_for):
    _tree(tmp_path, {"green": {2025: [1]}, "fhvhv": {2019: [2]}})
    found = shards.discover(settings_for(tmp_path, types=("green",)),
                            footer_reader=lambda p: 1)
    assert [s.data_type for s in found] == ["green"]


def test_discover_ignores_directories_that_are_not_years(tmp_path, settings_for):
    _tree(tmp_path, {"green": {2025: [1]}})
    stray = tmp_path / "raw-normalized" / "green" / "scratch"
    stray.mkdir()
    (stray / "green_tripdata_2025-01.parquet").touch()
    found = shards.discover(settings_for(tmp_path), footer_reader=lambda p: 1)
    assert [(s.year, s.month) for s in found] == [(2025, 1)]


def test_discover_ignores_files_without_a_month_stamp(tmp_path, settings_for):
    _tree(tmp_path, {"green": {2025: [1]}})
    (tmp_path / "raw-normalized" / "green" / "2025" / "notes.parquet").touch()
    found = shards.discover(settings_for(tmp_path), footer_reader=lambda p: 1)
    assert len(found) == 1


def test_discover_is_empty_when_nothing_is_normalized(tmp_path, settings_for):
    assert shards.discover(settings_for(tmp_path), footer_reader=lambda p: 1) == []


def test_order_is_year_major_biggest_year_first():
    items = [
        shards.Shard("green", 2025, 1, 10),
        shards.Shard("fhvhv", 2019, 2, 500),
        shards.Shard("fhvhv", 2019, 1, 400),
        shards.Shard("green", 2025, 2, 20),
    ]
    ordered = shards.order(items)
    # fhvhv 2019 totals 900 vs green 2025's 30, so it goes first, months ascending.
    assert [(s.data_type, s.year, s.month) for s in ordered] == [
        ("fhvhv", 2019, 1), ("fhvhv", 2019, 2),
        ("green", 2025, 1), ("green", 2025, 2),
    ]


def test_order_keeps_a_year_contiguous():
    items = [
        shards.Shard("fhvhv", 2020, 1, 100),
        shards.Shard("fhvhv", 2019, 1, 90),
        shards.Shard("fhvhv", 2020, 2, 100),
        shards.Shard("fhvhv", 2019, 2, 90),
    ]
    ordered = shards.order(items)
    assert [s.year for s in ordered] == [2020, 2020, 2019, 2019]


def test_years_lists_each_type_year_once_in_order():
    items = [
        shards.Shard("green", 2025, 1, 10),
        shards.Shard("fhvhv", 2019, 2, 500),
        shards.Shard("fhvhv", 2019, 1, 400),
    ]
    assert shards.years(items) == [("fhvhv", 2019), ("green", 2025)]


def test_label_is_readable():
    assert shards.Shard("fhvhv", 2019, 3, 1).label == "fhvhv 2019-03"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/taxi_seeder/test_shards.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_seeder.shards'`

- [ ] **Step 3: Write minimal implementation**

```python
# seeder/src/taxi_seeder/shards.py
"""What to load, in what order. Pure: no database, no subprocess.

A shard is one (trip type, year, month) -- one normalized parquet file. Shards
write into the existing per-year tables, not a table each: build_copy_sql passes
TABLOCK, which takes a BU lock, and BU locks are compatible with one another, so
several processes may bulk-load different months of one year concurrently.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from taxi_seeder.config import Settings

_YEAR_DIR_RE = re.compile(r"\A\d{4}\Z")
_MONTH_RE = re.compile(r"(\d{4})-(\d{2})")


@dataclass(frozen=True)
class Shard:
    data_type: str
    year: int
    month: int
    rows: int          # expected rows, from the parquet footer; drives ordering

    @property
    def label(self) -> str:
        return f"{self.data_type} {self.year}-{self.month:02d}"


def _footer_rows(path: str) -> int:
    import duckdb

    con = duckdb.connect()
    try:
        row = con.execute(
            "SELECT num_rows FROM parquet_file_metadata(?)", [path]
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        con.close()


def discover(settings: Settings, *, footer_reader=None) -> list[Shard]:
    """Every (trip type, year, month) with a normalized parquet file on disk."""
    footer_reader = footer_reader or _footer_rows
    found: list[Shard] = []
    for data_type in settings.types:
        base = settings.raw_normalized_dir / data_type
        if not base.is_dir():
            continue
        for year_dir in sorted(base.iterdir()):
            if not year_dir.is_dir() or not _YEAR_DIR_RE.match(year_dir.name):
                continue
            for path in sorted(year_dir.glob("*.parquet")):
                m = _MONTH_RE.search(path.name)
                if not m:
                    continue
                found.append(Shard(data_type, int(m.group(1)), int(m.group(2)),
                                   footer_reader(str(path))))
    return found


def order(items: list[Shard]) -> list[Shard]:
    """Year-major: biggest year first, months ascending inside it.

    Deliberately not per-shard longest-first. Crash recovery is year-granular --
    a bulk load commits per FLUSH_ROWS and is not atomic, so a killed shard
    leaves orphan rows that only the year-level integrity gate can spot. Keeping
    a year contiguous means the workers finish one before starting the next, so
    a kill exposes one or two years rather than six.
    """
    totals: dict[tuple[str, int], int] = {}
    for s in items:
        totals[(s.data_type, s.year)] = totals.get((s.data_type, s.year), 0) + s.rows
    return sorted(
        items,
        key=lambda s: (-totals[(s.data_type, s.year)], s.data_type, s.year, s.month),
    )


def years(items: list[Shard]) -> list[tuple[str, int]]:
    """Each (trip type, year) once, in the order `order` would load them."""
    seen: list[tuple[str, int]] = []
    for s in order(items):
        key = (s.data_type, s.year)
        if key not in seen:
            seen.append(key)
    return seen
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/taxi_seeder/test_shards.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add seeder/src/taxi_seeder/shards.py tests/taxi_seeder/test_shards.py
git commit -m "feat(seeder): add pure month-shard discovery and year-major ordering"
```

---

### Task 5: The integrity pre-pass

**Files:**
- Create: `seeder/src/taxi_seeder/integrity.py`
- Modify: `seeder/src/taxi_seeder/sql.py`
- Test: `tests/taxi_seeder/test_integrity.py`, `tests/taxi_seeder/test_sql.py`

**Interfaces:**
- Consumes: `mssql.run`, `sql.validate_identifier`, `shards.years` (Task 4).
- Produces: `sql.inconsistent_years(schema, data_type) -> str`, `sql.reset_year(schema, data_type, year) -> str`, `integrity.reset_inconsistent_years(settings, items, *, runner=None) -> list[tuple[str, int]]`.

This is the only component permitted to truncate, and it runs to completion **before** any shard is dispatched. Its job: guarantee that when month shards start, every year table is either consistent with its manifest or empty — so upstream's `guard_month_mode` never fires and no worker ever needs the RELOAD path.

A year is inconsistent when committed rows ≠ manifest rows. That is exactly taxi-seed's integrity gate, evaluated here because the *scheduling* decision belongs to whoever dispatches the shards.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/taxi_seeder/test_sql.py
def test_inconsistent_years_guards_a_missing_manifest():
    out = sql.inconsistent_years("dbo", "fhvhv")
    assert "OBJECT_ID('dbo._load_manifest', 'U') IS NULL" in out
    assert r"tb.name LIKE 'fhvhv\_%' ESCAPE '\'" in out
    assert "FULL OUTER JOIN" in out


def test_inconsistent_years_rejects_bad_identifiers():
    with pytest.raises(sql.SqlIdentifierError):
        sql.inconsistent_years("dbo", "fhvhv'; DROP TABLE x --")


def test_reset_year_truncates_and_clears_the_manifest():
    out = sql.reset_year("dbo", "fhvhv", 2019)
    assert "TRUNCATE TABLE dbo.fhvhv_2019" in out
    assert "OBJECT_ID('dbo.fhvhv_2019', 'U') IS NOT NULL" in out
    assert "DELETE FROM dbo._load_manifest" in out
    assert "data_type = 'fhvhv'" in out
    assert "year = 2019" in out


def test_reset_year_rejects_a_non_integer_year():
    with pytest.raises(ValueError):
        sql.reset_year("dbo", "fhvhv", "2019; DROP DATABASE taxi")
```

```python
# tests/taxi_seeder/test_integrity.py
from taxi_seeder import integrity, shards


def test_resets_only_the_years_the_server_reports(settings, proc):
    sent = []

    def runner(cmd, env, stdin_text):
        sent.append(cmd)
        joined = " ".join(cmd)
        if "FULL OUTER JOIN" in joined:
            return proc(0, "2019\n2021\n")
        return proc(0, "")

    items = [shards.Shard("fhvhv", y, 1, 1) for y in (2019, 2020, 2021)]
    reset = integrity.reset_inconsistent_years(settings, items, runner=runner)
    assert reset == [("fhvhv", 2019), ("fhvhv", 2021)]
    assert any("TRUNCATE TABLE dbo.fhvhv_2019" in " ".join(c) for c in sent)
    assert not any("TRUNCATE TABLE dbo.fhvhv_2020" in " ".join(c) for c in sent)


def test_resets_nothing_when_everything_agrees(settings, proc):
    def runner(cmd, env, stdin_text):
        return proc(0, "")

    items = [shards.Shard("fhvhv", 2019, 1, 1)]
    assert integrity.reset_inconsistent_years(settings, items, runner=runner) == []


def test_queries_each_trip_type_once(settings, proc):
    queried = []

    def runner(cmd, env, stdin_text):
        joined = " ".join(cmd)
        if "FULL OUTER JOIN" in joined:
            queried.append("fhvhv" if "fhvhv" in joined else "green")
        return proc(0, "")

    items = [shards.Shard("fhvhv", 2019, 1, 1), shards.Shard("fhvhv", 2020, 1, 1),
             shards.Shard("green", 2025, 1, 1)]
    integrity.reset_inconsistent_years(settings, items, runner=runner)
    assert sorted(queried) == ["fhvhv", "green"]


def test_an_empty_shard_list_is_a_no_op(settings):
    assert integrity.reset_inconsistent_years(settings, [], runner=None) == []
```

Add to `tests/taxi_seeder/test_integrity.py` the same `settings` fixture used in `test_shards.py`, plus:

```python
import types

import pytest


@pytest.fixture
def proc():
    def make(returncode, stdout="", stderr=""):
        return types.SimpleNamespace(returncode=returncode, stdout=stdout,
                                     stderr=stderr)
    return make
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/taxi_seeder/test_integrity.py tests/taxi_seeder/test_sql.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_seeder.integrity'`

- [ ] **Step 3: Write minimal implementation**

Append to `seeder/src/taxi_seeder/sql.py`:

```python
def inconsistent_years(schema: str, data_type: str) -> str:
    """Years whose committed rows disagree with their manifest rows.

    taxi-seed's own integrity gate, asked here because the caller schedules the
    shards and must reset such a year before dispatching any month of it. The
    FULL OUTER JOIN matters: a year can have table rows and no manifest rows
    (a crashed load) or manifest rows and no table (a dropped table).
    """
    sch = validate_identifier(schema, "schema")
    dt = validate_identifier(data_type, "trip type")
    return (
        f"IF OBJECT_ID('{_lit(sch)}._load_manifest', 'U') IS NULL\n"
        f"    SELECT TOP 0 CAST(NULL AS INT);\n"
        f"ELSE\n"
        f"WITH t AS (\n"
        f"  SELECT TRY_CAST(RIGHT(tb.name, 4) AS INT) AS year,\n"
        f"         SUM(p.rows) AS table_rows\n"
        f"  FROM sys.tables tb\n"
        f"  JOIN sys.schemas s ON s.schema_id = tb.schema_id\n"
        f"  JOIN sys.partitions p ON p.object_id = tb.object_id\n"
        f"   AND p.index_id IN (0, 1)\n"
        f"  WHERE s.name = '{_lit(sch)}'\n"
        f"    AND tb.name LIKE '{_lit(dt)}\\_%' ESCAPE '\\'\n"
        f"  GROUP BY tb.name\n"
        f"), m AS (\n"
        f"  SELECT year, SUM(row_count) AS manifest_rows\n"
        f"  FROM {sch}._load_manifest WHERE data_type = '{_lit(dt)}'\n"
        f"  GROUP BY year\n"
        f")\n"
        f"SELECT COALESCE(t.year, m.year) FROM t\n"
        f"FULL OUTER JOIN m ON m.year = t.year\n"
        f"WHERE ISNULL(t.table_rows, 0) <> ISNULL(m.manifest_rows, 0)\n"
        f"  AND COALESCE(t.year, m.year) IS NOT NULL\n"
        f"ORDER BY 1"
    )


def reset_year(schema: str, data_type: str, year: int) -> str:
    """Empty one year table and forget its manifest rows, so every month of it
    reloads as a clean append."""
    sch = validate_identifier(schema, "schema")
    dt = validate_identifier(data_type, "trip type")
    if isinstance(year, bool) or not isinstance(year, int):
        raise ValueError(f"year must be an int, got {year!r}")
    table = f"{sch}.{dt}_{year}"
    return (
        f"IF OBJECT_ID('{_lit(table)}', 'U') IS NOT NULL\n"
        f"    TRUNCATE TABLE {table};\n"
        f"IF OBJECT_ID('{_lit(sch)}._load_manifest', 'U') IS NOT NULL\n"
        f"    DELETE FROM {sch}._load_manifest\n"
        f"    WHERE data_type = '{_lit(dt)}' AND year = {year};"
    )
```

```python
# seeder/src/taxi_seeder/integrity.py
"""Make every year table consistent with its manifest, before any fan-out.

Month shards load into shared per-year tables, so no worker may issue DDL that a
sibling could be loading through. taxi-seed's guard_month_mode refuses to run a
RELOAD plan under --month for exactly that reason. This module is what makes
that refusal never fire: it resets any year that fails the integrity gate first,
serially, while nothing else is running.
"""
from __future__ import annotations

from taxi_seeder import mssql, shards, sql
from taxi_seeder.config import Settings


def reset_inconsistent_years(settings: Settings, items: list[shards.Shard],
                             *, runner=None) -> list[tuple[str, int]]:
    """Truncate + clear the manifest for every inconsistent year. Returns them."""
    if not items:
        return []

    wanted = shards.years(items)
    reset: list[tuple[str, int]] = []
    for data_type in sorted({dt for dt, _ in wanted}):
        out = mssql.run(settings, sql.inconsistent_years(settings.schema, data_type),
                        database=settings.database, runner=runner)
        for line in out.splitlines():
            line = line.strip()
            if not line or not line.isdigit():
                continue
            year = int(line)
            if (data_type, year) not in wanted:
                continue
            mssql.run(settings, sql.reset_year(settings.schema, data_type, year),
                      database=settings.database, runner=runner)
            print(f"integrity: {data_type} {year} disagreed with the manifest; "
                  f"truncated for a clean reload")
            reset.append((data_type, year))
    return reset
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/taxi_seeder/test_integrity.py tests/taxi_seeder/test_sql.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add seeder/src/taxi_seeder/integrity.py seeder/src/taxi_seeder/sql.py \
        tests/taxi_seeder/test_integrity.py tests/taxi_seeder/test_sql.py
git commit -m "feat(seeder): reset years failing the integrity gate before fan-out"
```

---

### Task 6: Run one shard, and run many

**Files:**
- Modify: `seeder/src/taxi_seeder/stages.py`
- Test: `tests/taxi_seeder/test_stages.py`

**Interfaces:**
- Consumes: `shards.Shard` (Task 4), `stages.run` (existing), `--year`/`--month` from Task 2.
- Produces: `stages.load_cmd(settings, data_type, *, year=None, month=None)`, `stages.load_shard(settings, shard, *, runner=None) -> int`, `stages.load_concurrent(settings, items, *, workers, runner=None, on_done=None) -> list[tuple[Shard, int]]`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/taxi_seeder/test_stages.py
from taxi_seeder import shards, stages


def test_load_cmd_omits_year_and_month_by_default(settings):
    cmd = stages.load_cmd(settings, "fhvhv")
    assert "--year" not in cmd and "--month" not in cmd


def test_load_cmd_includes_year_and_month(settings):
    cmd = stages.load_cmd(settings, "fhvhv", year=2019, month=3)
    assert cmd[cmd.index("--year") + 1] == "2019"
    assert cmd[cmd.index("--month") + 1] == "3"


def test_load_shard_passes_both(settings):
    seen = {}

    def runner(cmd, env, cwd):
        seen["cmd"] = cmd
        return _Proc(0)

    rc = stages.load_shard(settings, shards.Shard("fhvhv", 2019, 3, 1), runner=runner)
    assert rc == 0
    assert seen["cmd"][seen["cmd"].index("--month") + 1] == "3"


def test_load_concurrent_runs_every_shard_and_returns_each_code(settings):
    items = [shards.Shard("fhvhv", 2019, m, 1) for m in (1, 2, 3)]
    codes = {1: 0, 2: 2, 3: 0}

    def runner(cmd, env, cwd):
        return _Proc(codes[int(cmd[cmd.index("--month") + 1])])

    got = stages.load_concurrent(settings, items, workers=3, runner=runner)
    assert {s.month: rc for s, rc in got} == codes


def test_load_concurrent_does_not_abandon_siblings_when_one_fails(settings):
    items = [shards.Shard("fhvhv", 2019, m, 1) for m in (1, 2, 3)]

    def runner(cmd, env, cwd):
        if cmd[cmd.index("--month") + 1] == "1":
            raise RuntimeError("worker exploded")
        return _Proc(0)

    got = stages.load_concurrent(settings, items, workers=3, runner=runner)
    assert len(got) == 3
    by_month = {s.month: rc for s, rc in got}
    assert by_month[1] == 2      # a crash maps to FAILURE
    assert by_month[2] == 0


def test_load_concurrent_reports_each_completion(settings):
    done = []
    items = [shards.Shard("fhvhv", 2019, m, 1) for m in (1, 2)]
    stages.load_concurrent(settings, items, workers=2,
                           runner=lambda *a: _Proc(0),
                           on_done=lambda s, rc: done.append(s.label))
    assert sorted(done) == ["fhvhv 2019-01", "fhvhv 2019-02"]


def test_load_concurrent_of_nothing(settings):
    assert stages.load_concurrent(settings, [], workers=4, runner=None) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/taxi_seeder/test_stages.py -v -k "shard or concurrent or month"`
Expected: FAIL — `TypeError: load_cmd() got an unexpected keyword argument 'year'`

- [ ] **Step 3: Write minimal implementation**

In `stages.py` add `import sys`, `from concurrent.futures import ThreadPoolExecutor`, and `from taxi_seeder.shards import Shard`. Extend `load_cmd`:

```python
def load_cmd(settings: Settings, data_type: str, *, year: int | None = None,
             month: int | None = None) -> list[str]:
    cmd = [
        "taxi-load", data_type,
        "--data-dir", str(settings.data_dir),
        "--host", settings.host,
        "--port", str(settings.port),
        "--database", settings.database,
        "--schema", settings.schema,
        "--user", settings.user,
    ]
    if year is not None:
        cmd += ["--year", str(year)]
    if month is not None:
        cmd += ["--month", str(month)]
    return cmd
```

and append:

```python
def load_shard(settings: Settings, shard: Shard, *, runner=None) -> int:
    return run(load_cmd(settings, shard.data_type, year=shard.year,
                        month=shard.month), settings, runner=runner)


def load_concurrent(settings: Settings, items, *, workers: int,
                    runner=None, on_done=None) -> list[tuple[Shard, int]]:
    """Load every shard with at most `workers` concurrent taxi-load processes.

    Threads here only supervise subprocesses -- they block in subprocess.run and
    release the GIL. The parallelism is process-level, which is forced: the
    duckdb mssql extension keeps its ATTACH context process-global, so two loads
    in one process would collide on it.

    A shard that raises is recorded as exit 2 rather than propagated, so one bad
    month cannot abandon its siblings. The next run's integrity pre-pass resets
    whatever year was left half-loaded.
    """
    if not items:
        return []

    def one(shard: Shard) -> tuple[Shard, int]:
        try:
            rc = load_shard(settings, shard, runner=runner)
        except StageCommandNotFoundError:
            raise
        except Exception as e:                       # noqa: BLE001 -- see docstring
            print(f"error: load stage for {shard.label} crashed: {e}",
                  file=sys.stderr)
            rc = 2
        if on_done is not None:
            on_done(shard, rc)
        return shard, rc

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(one, items))
```

`StageCommandNotFoundError` is deliberately re-raised: a missing `taxi-load` is a configuration failure every shard would hit, and `cli.main` already maps it to exit 2.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/taxi_seeder/test_stages.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add seeder/src/taxi_seeder/stages.py tests/taxi_seeder/test_stages.py
git commit -m "feat(seeder): run taxi-load per month shard, concurrently"
```

---

### Task 7: Fan out from `cli.py`

**Files:**
- Modify: `seeder/src/taxi_seeder/cli.py`
- Test: `tests/taxi_seeder/test_cli.py`

**Interfaces:**
- Consumes: `shards.discover`, `shards.order`, `integrity.reset_inconsistent_years`, `stages.load_concurrent`, `load_exit_code`, `worst` (existing).
- Produces: no new public API; `main()` keeps its signature and exit-code contract.

Sequence: `provision` (creates the database) → `ola` → **integrity pre-pass** → **fan out every month shard**. No warm-up shard is needed — Task 2 made `ensure_manifest_table` and `_ensure_table` race-safe, so concurrent cold starts are fine.

State the behaviour change plainly: serial loading halted at the first failing trip type. Fan-out cannot, because siblings are already in flight. The new contract is **every dispatched shard finishes, then the worst code wins**, and the backup is skipped unless everything was clean.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/taxi_seeder/test_cli.py
from taxi_seeder import cli, shards


def _stub_pipeline(monkeypatch):
    monkeypatch.setattr(cli.preflight, "run", lambda s: None)
    monkeypatch.setattr(cli.provision, "run", lambda s: None)
    monkeypatch.setattr(cli.ola, "run", lambda s: None)
    monkeypatch.setattr(cli.stages, "mirror", lambda s, t, **kw: 0)
    monkeypatch.setattr(cli.backup, "run", lambda s, **kw: False)
    monkeypatch.setattr(cli.backup, "newest_backup", lambda s: None)
    monkeypatch.setattr(cli.report, "loaded_rows", lambda s, **kw: {})
    monkeypatch.setattr(cli.integrity, "reset_inconsistent_years",
                        lambda s, it, **kw: [])


def test_main_runs_the_pre_pass_before_fanning_out(monkeypatch, base_env):
    _stub_pipeline(monkeypatch)
    calls = []
    items = [shards.Shard("green", 2025, m, 10) for m in (1, 2)]
    monkeypatch.setattr(cli.shards, "discover", lambda s, **kw: items)
    monkeypatch.setattr(cli.integrity, "reset_inconsistent_years",
                        lambda s, it, **kw: calls.append("pre-pass") or [])
    monkeypatch.setattr(cli.stages, "load_concurrent",
                        lambda s, it, **kw: calls.append("fanout") or
                        [(x, 0) for x in it])
    monkeypatch.setattr(cli.os, "environ", {**base_env, "TAXI_TYPES": "green"})

    assert cli.main([]) == 0
    assert calls == ["pre-pass", "fanout"]


def test_main_fans_out_in_year_major_order(monkeypatch, base_env):
    _stub_pipeline(monkeypatch)
    seen = {}
    items = [shards.Shard("green", 2025, 1, 10), shards.Shard("fhvhv", 2019, 1, 900)]
    monkeypatch.setattr(cli.shards, "discover", lambda s, **kw: items)
    monkeypatch.setattr(cli.stages, "load_concurrent",
                        lambda s, it, **kw: seen.setdefault("order",
                                                            [x.label for x in it])
                        or [(x, 0) for x in it])
    monkeypatch.setattr(cli.os, "environ",
                        {**base_env, "TAXI_TYPES": "green,fhvhv"})

    assert cli.main([]) == 0
    assert seen["order"] == ["fhvhv 2019-01", "green 2025-01"]


def test_main_returns_the_worst_shard_code(monkeypatch, base_env):
    _stub_pipeline(monkeypatch)
    items = [shards.Shard("green", 2025, 1, 10)]
    monkeypatch.setattr(cli.shards, "discover", lambda s, **kw: items)
    monkeypatch.setattr(cli.stages, "load_concurrent",
                        lambda s, it, **kw: [(x, 1) for x in it])
    monkeypatch.setattr(cli.os, "environ", {**base_env, "TAXI_TYPES": "green"})

    # taxi-load's 1 means "failed mid-load", which load_exit_code maps to 2.
    assert cli.main([]) == 2


def test_main_skips_the_backup_when_a_shard_failed(monkeypatch, base_env):
    _stub_pipeline(monkeypatch)
    backed_up = []
    monkeypatch.setattr(cli.backup, "run", lambda s, **kw: backed_up.append(1))
    items = [shards.Shard("green", 2025, 1, 10)]
    monkeypatch.setattr(cli.shards, "discover", lambda s, **kw: items)
    monkeypatch.setattr(cli.stages, "load_concurrent",
                        lambda s, it, **kw: [(x, 2) for x in it])
    monkeypatch.setattr(cli.os, "environ", {**base_env, "TAXI_TYPES": "green"})

    assert cli.main([]) == 2
    assert backed_up == []


def test_main_backs_up_when_there_is_nothing_to_load(monkeypatch, base_env):
    _stub_pipeline(monkeypatch)
    backed_up = []
    monkeypatch.setattr(cli.backup, "run", lambda s, **kw: backed_up.append(1))
    monkeypatch.setattr(cli.shards, "discover", lambda s, **kw: [])
    monkeypatch.setattr(cli.os, "environ", {**base_env, "TAXI_TYPES": "green"})

    assert cli.main([]) == 0
    assert backed_up == [1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/taxi_seeder/test_cli.py -v -k "pre_pass or year_major or worst_shard"`
Expected: FAIL — `AttributeError: module 'taxi_seeder.cli' has no attribute 'shards'`

- [ ] **Step 3: Write minimal implementation**

Add `integrity` and `shards` to the package import in `cli.py`, then replace the serial load loop with:

```python
        found = shards.order(shards.discover(settings))
        if found:
            print(f"load: {len(found)} month shard(s) across "
                  f"{len(shards.years(found))} year table(s), "
                  f"{settings.load_workers} worker(s)")

            # Serial, and first: this is the only component allowed to truncate,
            # and it must finish before any worker opens a bulk stream into a
            # table it might reset. After it, every shard is a clean append.
            integrity.reset_inconsistent_years(settings, found)

            for shard, child_rc in stages.load_concurrent(
                    settings, found, workers=settings.load_workers):
                rc = load_exit_code(child_rc)
                codes.append(rc)
                if rc != CLEAN:
                    print(f"{_halt_label(rc)}: load stage for {shard.label} "
                          f"exited {child_rc} (re-running resumes from the "
                          f"manifest)", file=sys.stderr)

        if worst(codes) != CLEAN:
            print("error: one or more load shards failed; skipping backup",
                  file=sys.stderr)
            return worst(codes)

        backup.run(settings)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/taxi_seeder/ -v`
Expected: PASS (whole suite)

- [ ] **Step 5: Commit**

```bash
git add seeder/src/taxi_seeder/cli.py tests/taxi_seeder/test_cli.py
git commit -m "feat(seeder): fan out loads across month shards after an integrity pass"
```

---

### Task 8: Measure it, then document what it bought

**Files:**
- Modify: `README.md`, `tests/taxi_seeder/test_integration_compose.py`

- [ ] **Step 1: Add integration assertions for concurrency and correctness**

```python
# append to tests/taxi_seeder/test_integration_compose.py
import pytest


@pytest.mark.integration
def test_parallel_load_uses_more_than_one_bulk_stream(compose_stack, sqlcmd):
    """Sampled while the load runs; a single-stream regression makes this 1."""
    out = sqlcmd(
        "SELECT COUNT(*) FROM sys.dm_exec_requests WHERE command = 'BULK INSERT'"
    )
    assert int(out.strip()) > 1


@pytest.mark.integration
def test_concurrent_months_do_not_duplicate_rows(compose_stack, sqlcmd):
    """Every year table must end up agreeing with its manifest exactly.

    This is the assertion that would catch a BU-lock assumption being wrong, or
    two workers both appending the same month.
    """
    out = sqlcmd("""
        WITH t AS (
          SELECT TRY_CAST(RIGHT(tb.name, 4) AS INT) AS year,
                 LEFT(tb.name, LEN(tb.name) - 5) AS data_type,
                 SUM(p.rows) AS table_rows
          FROM sys.tables tb
          JOIN sys.partitions p ON p.object_id = tb.object_id
           AND p.index_id IN (0, 1)
          WHERE tb.name LIKE '%[_]20[0-9][0-9]'
          GROUP BY tb.name
        ), m AS (
          SELECT data_type, year, SUM(row_count) AS manifest_rows
          FROM dbo._load_manifest GROUP BY data_type, year
        )
        SELECT COUNT(*) FROM t JOIN m
          ON m.year = t.year AND m.data_type = t.data_type
        WHERE t.table_rows <> m.manifest_rows
    """)
    assert int(out.strip()) == 0
```

Reuse the `compose_stack` and `sqlcmd` fixtures the file already defines; if it has no `sqlcmd` helper, add one that shells out through `docker exec` the way the existing integration tests do.

- [ ] **Step 2: Run the integration tests against a real stack**

```bash
TAXI_TYPES=green TAXI_LOAD_WORKERS=4 docker compose -f seeder/docker-compose.yml up -d
pytest tests/taxi_seeder/test_integration_compose.py -v -m integration
```

Expected: PASS — more than one concurrent `BULK INSERT`, and zero year tables disagreeing with the manifest.

- [ ] **Step 3: Record the real before/after numbers**

Re-measure the rate the way the Background table did, and write the **measured** figures into the README below — do not copy a projection.

```bash
docker exec -e SQLCMDPASSWORD="$MSSQL_PASSWORD" taxi-seeder-mssql-1 \
  /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -C -b -h -1 -W -d taxi \
  -Q "SET NOCOUNT ON; SELECT SUM(p.rows) FROM sys.tables t
      JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0,1)
      WHERE t.name LIKE 'fhvhv\_%' ESCAPE '\'"
```

- [ ] **Step 4: Add a "Load performance" section to `README.md`**

````markdown
## Load performance

Loading runs one `taxi-load` process per (trip type, year, month) — one process
per normalized parquet file. Several processes load different months into the
same year table at once: `TABLOCK` takes a BU (bulk update) lock, and BU locks
are compatible with each other, which is what SQL Server's parallel bulk import
is built on. The table layout is unchanged — no month tables, no views.

A single stream is CPU-bound, not disk-bound: measured `dop=1`, 87% on-CPU,
3 MB/s against a disk that does 1.3 GB/s, with the host 85% idle. Parallelism is
process-level because the duckdb `mssql` extension keeps its ATTACH context
process-global — two loads in one process would collide on it.

```bash
TAXI_LOAD_WORKERS=6 docker compose up -d   # default: CPU count - 2, capped at 6
TAXI_LOAD_WORKERS=1 docker compose up -d   # serial, the pre-parallel behaviour
```

Before any shard runs, an integrity pass resets any year whose committed rows
disagree with its manifest. That is also the recovery granularity: a bulk load
commits per 100,000 rows and is not atomic, so a run killed mid-shard leaves
rows the year-level check has to clear. Shards are ordered year-major so the
workers finish one year before starting the next, which keeps a kill to one or
two years rather than six.

SQL Server's memory is capped at provision time to host RAM minus the larger of
4 GB and 25%. Override it for a differently-sized box:

```bash
MSSQL_MAX_MEMORY_MB=24576   # in .env
```
````

- [ ] **Step 5: Commit**

```bash
git add README.md tests/taxi_seeder/test_integration_compose.py
git commit -m "docs: record parallel load configuration and measured rates"
```

---

## Self-Review

**Coverage against the Background findings:**

| Finding | Task |
|---|---|
| `dop=1`, one core of eight, 87% on-CPU | 4, 6, 7 (month-shard fan-out) |
| Ray / threads-in-one-process ruled out by the process-global ATTACH | 6 (subprocess per shard) |
| Year shards leave a ~165 min tail against a ~130 min ideal | 4 (month grain) |
| Month tables + UNION ALL views would cost partition elimination | — rejected; year tables kept, see Architecture |
| Concurrent writers to one year table need BU-lock compatibility | 6, and asserted in 8 |
| Concurrent `ensure_manifest_table` / `_ensure_table` races | 2 (race-safe DDL upstream) |
| Year-level RELOAD would drop a table siblings are loading | 2 (`guard_month_mode`), 5 (pre-pass) |
| Crash recovery is year-granular | 4 (`order` year-major), documented in 8 |
| `taxi-load` has no month filter | 2 (upstream) |
| 23 GB anonymous, uncapped `max server memory` | 1 |

**Type consistency:** `Shard(data_type, year, month, rows)` is constructed identically in Tasks 4, 5, 6 and 7, and `Shard.label` is used in `stages.load_concurrent`'s error text (Task 6), `cli.py`'s output (Task 7) and the tests in both. `stages.load_concurrent` returns `list[tuple[Shard, int]]` in Task 6 and is unpacked as `for shard, child_rc in ...` in Task 7. `shards.years` returns `list[tuple[str, int]]` in Task 4 and is consumed as `(data_type, year)` membership in Task 5 and as a count in Task 7. `integrity.reset_inconsistent_years` returns the same type.

**Known follow-on:** Tasks 1 and 3 each add a field to the frozen `Settings` dataclass (`mssql_max_memory_mb`, `load_workers`). Every exhaustive `Settings(...)` construction in the test suite must gain both. If the **logging** plan lands first, `progress_interval_s` is there too — the `settings_for` fixtures in Tasks 4 and 5 must then include it.

**Deliberate behaviour changes:**
1. Serial loading stopped at the first failing trip type; fan-out cannot, because siblings are already running. Every dispatched shard finishes, the worst code wins, and the backup is skipped if any shard failed.
2. `taxi-load --month` refuses a year needing RELOAD rather than performing it. That is the safety property the whole design rests on, and Task 5 is what keeps it from ever triggering in practice.

**Not in scope:** page compression is the other half of the per-row CPU cost. Loading into an uncompressed heap and then `ALTER INDEX ... REBUILD WITH (DATA_COMPRESSION = PAGE, MAXDOP = 8)` would move that cost onto every core, at the price of scratch space and a second pass. It belongs upstream in `execute_year_plan` and should be spiked and measured before anyone plans it.
