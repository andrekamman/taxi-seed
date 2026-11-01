# Seeder Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a multi-hour seeder run legible while it is running — every stage announces itself, and a load in progress reports rows, rate, and ETA on a fixed heartbeat instead of going silent for hours.

**Architecture:** A new pure module `progress.py` holds all formatting (counts, durations, rates, ETA) so the interesting logic is unit-testable without a clock, a database, or a subprocess. A small `Heartbeat` class wraps a daemon thread that calls an injected zero-argument `sample()` callable on an interval and writes one line per tick. `cli.py` owns the wiring: it announces each stage and opens a `Heartbeat` around the two long subprocess calls. `stages.py` stays exactly what its docstring says it is — a thin subprocess wrapper coupled to taxi-seed's documented CLI.

**Tech Stack:** Python 3.12 stdlib only (`threading`, `time`), existing `mssql.py`/`sql.py` for sampling, duckdb (already a taxi-lab dependency) for parquet footer totals, pytest.

**Spec:** None — this plan carries its own evidence. See **Background** below, measured against the live run on 2026-08-15.

## Background: what the measurements showed

Taken from a stalled-looking but perfectly healthy run on 2026-08-15:

- `taxi_loader/cli.py::_process_type` prints **exactly one line per trip type**, after every year of that type has loaded. `yellow` ran **6h50m** and printed nothing until it finished.
- `taxi_seeder/cli.py` never announces what it is about to do. There is no marker for which stage or which trip type is live.
- `seeder/Dockerfile` sets no `PYTHONUNBUFFERED`. Child stdout is a pipe, so it is block-buffered and can sit unflushed.
- `_load_manifest` is **not** a usable progress source on its own. `execute_year_plan`'s RELOAD branch issues one `COPY` for the whole year and writes manifest rows only afterwards, so a year in RELOAD shows zero manifest progress for its entire duration (~2h for `fhvhv_2019`). Progress must be sampled from **table row counts**, not the manifest.

## Global Constraints

- **Exit codes are unchanged by this plan:** `0` clean, `1` needs-review, `2` operational failure. Logging must never change a return code.
- **Never print the password**, and never put it in argv — `mssql.py` already owns that rule via `SQLCMDPASSWORD`.
- **Heartbeat failures are non-fatal.** A sampling error must be swallowed and reported as an unknown value, never raised — a broken progress line must not kill a 13-hour load.
- **All timestamps are UTC**, formatted `HH:MM:SS`, to match the container's clock and `docker logs --timestamps`.
- **Pure/impure split is a repo convention.** Formatting goes in pure functions with no clock and no IO; anything with a thread, a socket, or a subprocess takes an injected collaborator.
- **Prose says "trip type"**, the identifier stays `data_type` (see `CONTEXT.md`).
- **Default heartbeat interval: 60s.** Configurable via `TAXI_PROGRESS_INTERVAL_S`; `0` disables the heartbeat entirely.

## File Structure

```
seeder/
  Dockerfile                          + ENV PYTHONUNBUFFERED=1
  src/taxi_seeder/
    progress.py       NEW  pure formatters + Heartbeat (injected sample/clock/out)
    sql.py            +    loaded_table_rows(schema, data_type)
    report.py         +    table_rows(settings, data_type)
    config.py         +    progress_interval_s setting
    cli.py            ~    stage announcements + Heartbeat wiring
tests/taxi_seeder/
    test_progress.py  NEW
    test_sql.py       +    loaded_table_rows cases
    test_report.py    +    table_rows cases
    test_config.py    +    progress_interval_s cases
    test_cli.py       +    announcement + heartbeat wiring cases
```

`progress.py` is a new file rather than an addition to `report.py` because `report.py` renders a **final** summary once, from a finished state; `progress.py` renders a **running** one repeatedly, from a moving state. They share no logic and have opposite lifetimes.

---

### Task 1: Unbuffered output and a stamped emit

**Files:**
- Create: `seeder/src/taxi_seeder/progress.py`
- Modify: `seeder/Dockerfile`
- Test: `tests/taxi_seeder/test_progress.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `progress.emit(message: str, *, now: str | None = None, out=print) -> None`, `progress.utc_hms(epoch_s: float) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/taxi_seeder/test_progress.py
from taxi_seeder import progress


def test_utc_hms_formats_epoch_as_utc_clock_time():
    # 2026-08-15T07:13:42Z
    assert progress.utc_hms(1786778022.0) == "07:13:42"


def test_emit_prefixes_a_utc_timestamp():
    written = []
    progress.emit("load fhvhv: starting", now="07:13:42", out=written.append)
    assert written == ["[07:13:42] load fhvhv: starting"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/taxi_seeder/test_progress.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_seeder.progress'`

- [ ] **Step 3: Write minimal implementation**

```python
# seeder/src/taxi_seeder/progress.py
"""Progress for a run that spends hours inside a single subprocess.

Everything above the Heartbeat class is pure: no clock, no database, no IO.
That is deliberate -- the formatting is the part worth testing, and it is
testable only while it stays free of the thread that drives it.
"""
from __future__ import annotations

import time


def utc_hms(epoch_s: float) -> str:
    return time.strftime("%H:%M:%S", time.gmtime(epoch_s))


def emit(message: str, *, now: str | None = None, out=print) -> None:
    """One progress line. `now` is injectable so tests never touch the clock."""
    stamp = now if now is not None else utc_hms(time.time())
    out(f"[{stamp}] {message}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/taxi_seeder/test_progress.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Set PYTHONUNBUFFERED in the image**

In `seeder/Dockerfile`, immediately after the `ENV PATH="/opt/mssql-tools18/bin:${PATH}"` line, add:

```dockerfile
# Child stdout is a pipe under `docker compose up`, so Python block-buffers it
# and a progress line can sit unflushed for minutes. This applies to taxi-seed's
# CLIs too -- they inherit the environment through stages.run.
ENV PYTHONUNBUFFERED=1
```

- [ ] **Step 6: Commit**

```bash
git add seeder/src/taxi_seeder/progress.py tests/taxi_seeder/test_progress.py seeder/Dockerfile
git commit -m "feat(seeder): add stamped progress emit, unbuffer container stdout"
```

---

### Task 2: Pure formatters — counts, durations, rates, ETA

**Files:**
- Modify: `seeder/src/taxi_seeder/progress.py`
- Test: `tests/taxi_seeder/test_progress.py`

**Interfaces:**
- Consumes: `progress.emit`, `progress.utc_hms` (Task 1).
- Produces: `humanize_count(n: int) -> str`, `humanize_duration(seconds: float) -> str`, `rows_per_min(delta_rows: int, delta_s: float) -> float | None`, `eta_seconds(remaining: int, per_min: float | None) -> float | None`, `format_progress(label: str, rows: int | None, total: int | None, per_min: float | None, elapsed_s: float) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/taxi_seeder/test_progress.py
import pytest


@pytest.mark.parametrize("n,expected", [
    (0, "0"), (495, "495"), (34_622_078, "34.6M"),
    (1_607_275_004, "1.61B"), (83_295, "83.3K"),
])
def test_humanize_count(n, expected):
    assert progress.humanize_count(n) == expected


@pytest.mark.parametrize("seconds,expected", [
    (5, "5s"), (90, "1m30s"), (764 * 60, "12h44m"), (0, "0s"),
])
def test_humanize_duration(seconds, expected):
    assert progress.humanize_duration(seconds) == expected


def test_rows_per_min_is_none_without_elapsed_time():
    assert progress.rows_per_min(1000, 0) is None


def test_rows_per_min_scales_to_a_minute():
    assert progress.rows_per_min(4_114_432, 120.0) == pytest.approx(2_057_216)


def test_eta_seconds_is_none_without_a_rate():
    assert progress.eta_seconds(1000, None) is None
    assert progress.eta_seconds(1000, 0) is None


def test_eta_seconds_divides_remaining_by_rate():
    assert progress.eta_seconds(2_000_000, 1_000_000) == pytest.approx(120.0)


def test_format_progress_with_everything_known():
    line = progress.format_progress(
        "load fhvhv", rows=34_622_078, total=1_607_275_004,
        per_min=2_057_216, elapsed_s=1020,
    )
    assert line == (
        "load fhvhv: 34.6M/1.61B rows (2.2%), 2.06M rows/min, "
        "elapsed 17m0s, eta 12h44m"
    )


def test_format_progress_without_a_total_omits_percent_and_eta():
    line = progress.format_progress(
        "load fhvhv", rows=34_622_078, total=None,
        per_min=2_057_216, elapsed_s=1020,
    )
    assert line == "load fhvhv: 34.6M rows, 2.06M rows/min, elapsed 17m0s"


def test_format_progress_survives_an_unknown_sample():
    line = progress.format_progress(
        "load fhvhv", rows=None, total=None, per_min=None, elapsed_s=30,
    )
    assert line == "load fhvhv: rows unavailable, elapsed 30s"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/taxi_seeder/test_progress.py -v`
Expected: FAIL — `AttributeError: module 'taxi_seeder.progress' has no attribute 'humanize_count'`

- [ ] **Step 3: Write minimal implementation**

Append to `seeder/src/taxi_seeder/progress.py`:

```python
def humanize_count(n: int) -> str:
    """Row counts in the billions are unreadable in full, and the exact digit
    never matters in a progress line -- the final report prints it exactly."""
    for limit, suffix, div in (
        (1_000_000_000, "B", 1_000_000_000.0),
        (1_000_000, "M", 1_000_000.0),
        (1_000, "K", 1_000.0),
    ):
        if n >= limit:
            return f"{n / div:.3g}{suffix}"
    return str(n)


def humanize_duration(seconds: float) -> str:
    total = int(seconds)
    if total >= 3600:
        return f"{total // 3600}h{(total % 3600) // 60:02d}m"
    if total >= 60:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total}s"


def rows_per_min(delta_rows: int, delta_s: float) -> float | None:
    if delta_s <= 0:
        return None
    return delta_rows * 60.0 / delta_s


def eta_seconds(remaining: int, per_min: float | None) -> float | None:
    if not per_min or per_min <= 0 or remaining <= 0:
        return None
    return remaining * 60.0 / per_min


def format_progress(label: str, *, rows: int | None, total: int | None,
                    per_min: float | None, elapsed_s: float) -> str:
    if rows is None:
        return f"{label}: rows unavailable, elapsed {humanize_duration(elapsed_s)}"

    parts = [f"{humanize_count(rows)}"]
    if total:
        parts[0] += f"/{humanize_count(total)} rows ({rows * 100.0 / total:.1f}%)"
    else:
        parts[0] += " rows"
    if per_min:
        parts.append(f"{humanize_count(int(per_min))} rows/min")
    parts.append(f"elapsed {humanize_duration(elapsed_s)}")
    remaining = eta_seconds((total or 0) - rows, per_min)
    if remaining is not None:
        parts.append(f"eta {humanize_duration(remaining)}")
    return f"{label}: " + ", ".join(parts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/taxi_seeder/test_progress.py -v`
Expected: PASS (all cases)

- [ ] **Step 5: Commit**

```bash
git add seeder/src/taxi_seeder/progress.py tests/taxi_seeder/test_progress.py
git commit -m "feat(seeder): add pure progress formatters for counts, rates and eta"
```

---

### Task 3: Sample live row counts from the tables, not the manifest

**Files:**
- Modify: `seeder/src/taxi_seeder/sql.py`, `seeder/src/taxi_seeder/report.py`
- Test: `tests/taxi_seeder/test_sql.py`, `tests/taxi_seeder/test_report.py`

**Interfaces:**
- Consumes: `sql.validate_identifier`, `sql._lit`, `mssql.scalar` (all existing).
- Produces: `sql.loaded_table_rows(schema: str, data_type: str) -> str`, `report.table_rows(settings, data_type, *, runner=None) -> int | None`.

Why not `_load_manifest`: a year in RELOAD writes no manifest rows until the whole year's `COPY` finishes, so the manifest reads as zero progress for hours. `sys.partitions` moves continuously.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/taxi_seeder/test_sql.py
import pytest
from taxi_seeder import sql


def test_loaded_table_rows_targets_the_year_tables_of_one_type():
    out = sql.loaded_table_rows("dbo", "fhvhv")
    assert "sys.partitions" in out
    assert "index_id IN (0, 1)" in out
    assert "s.name = 'dbo'" in out
    # The underscore in "fhvhv_2019" is a LIKE wildcard and MUST be escaped,
    # or "fhvhvX2019" would match too.
    assert r"t.name LIKE 'fhvhv\_%' ESCAPE '\'" in out


def test_loaded_table_rows_rejects_a_bad_schema():
    with pytest.raises(sql.SqlIdentifierError):
        sql.loaded_table_rows("dbo; DROP TABLE x --", "fhvhv")


def test_loaded_table_rows_rejects_a_bad_type():
    with pytest.raises(sql.SqlIdentifierError):
        sql.loaded_table_rows("dbo", "fhvhv'; --")
```

```python
# append to tests/taxi_seeder/test_report.py
from taxi_seeder import report


def test_table_rows_parses_the_scalar(settings):
    def runner(cmd, env, stdin_text):
        return _Proc(0, "28400254\n")
    assert report.table_rows(settings, "fhvhv", runner=runner) == 28400254


def test_table_rows_returns_none_when_sampling_fails(settings):
    def runner(cmd, env, stdin_text):
        return _Proc(1, "", "connection reset")
    assert report.table_rows(settings, "fhvhv", runner=runner) is None
```

Reuse whatever `_Proc` stub and `settings` fixture `tests/taxi_seeder/test_report.py` already defines; if it defines neither, add:

```python
import types
from pathlib import Path
from taxi_seeder.config import Settings


def _Proc(returncode, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/taxi_seeder/test_sql.py tests/taxi_seeder/test_report.py -v`
Expected: FAIL — `AttributeError: module 'taxi_seeder.sql' has no attribute 'loaded_table_rows'`

- [ ] **Step 3: Write minimal implementation**

Append to `seeder/src/taxi_seeder/sql.py`:

```python
def loaded_table_rows(schema: str, data_type: str) -> str:
    """Committed rows across every year table of one trip type.

    Deliberately not _load_manifest: execute_year_plan's RELOAD branch copies a
    whole year in one statement and writes its manifest rows only afterwards, so
    the manifest reports zero for the entire two hours a large year takes.
    sys.partitions moves the whole time, which is the point of a heartbeat.
    """
    sch = validate_identifier(schema, "schema")
    dt = validate_identifier(data_type, "trip type")
    return (
        "SELECT ISNULL(SUM(p.rows), 0) FROM sys.tables t "
        "JOIN sys.schemas s ON s.schema_id = t.schema_id "
        "JOIN sys.partitions p ON p.object_id = t.object_id "
        "AND p.index_id IN (0, 1) "
        f"WHERE s.name = '{_lit(sch)}' "
        f"AND t.name LIKE '{_lit(dt)}\\_%' ESCAPE '\\'"
    )
```

Append to `seeder/src/taxi_seeder/report.py`:

```python
def table_rows(settings: Settings, data_type: str, *, runner=None) -> int | None:
    """Live committed row count for one trip type, or None if it can't be read.

    None rather than an exception: this is called from the heartbeat thread, and
    a progress line must never be able to kill a load that is going fine.
    """
    try:
        raw = mssql.scalar(settings, sql.loaded_table_rows(settings.schema, data_type),
                           database=settings.database, runner=runner)
    except mssql.MssqlError:
        return None
    if raw is None or not raw.isdigit():
        return None
    return int(raw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/taxi_seeder/test_sql.py tests/taxi_seeder/test_report.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add seeder/src/taxi_seeder/sql.py seeder/src/taxi_seeder/report.py \
        tests/taxi_seeder/test_sql.py tests/taxi_seeder/test_report.py
git commit -m "feat(seeder): sample live row counts from sys.partitions for progress"
```

---

### Task 4: Total expected rows from parquet footers

**Files:**
- Modify: `seeder/src/taxi_seeder/progress.py`
- Test: `tests/taxi_seeder/test_progress.py`

**Interfaces:**
- Consumes: `Settings.raw_normalized_dir`.
- Produces: `progress.expected_rows(settings, data_type, *, reader=None) -> int | None`.

This is what makes the ETA possible. Reading parquet footers is cheap — metadata only, no row scan. On the 2026-08-15 dataset the whole `fhvhv` tree answered in under a second.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/taxi_seeder/test_progress.py
from pathlib import Path


def test_expected_rows_sums_the_footers(tmp_path, settings_for):
    settings = settings_for(tmp_path)
    d = tmp_path / "raw-normalized" / "fhvhv" / "2019"
    d.mkdir(parents=True)
    (d / "fhvhv_tripdata_2019-02.parquet").touch()
    (d / "fhvhv_tripdata_2019-03.parquet").touch()

    seen = {}

    def reader(paths):
        seen["paths"] = sorted(paths)
        return 234_630_264

    assert progress.expected_rows(settings, "fhvhv", reader=reader) == 234_630_264
    assert len(seen["paths"]) == 2


def test_expected_rows_is_none_when_the_tree_is_absent(tmp_path, settings_for):
    settings = settings_for(tmp_path)
    assert progress.expected_rows(settings, "fhvhv", reader=lambda p: 1) is None


def test_expected_rows_is_none_when_the_reader_fails(tmp_path, settings_for):
    settings = settings_for(tmp_path)
    d = tmp_path / "raw-normalized" / "fhvhv" / "2019"
    d.mkdir(parents=True)
    (d / "fhvhv_tripdata_2019-02.parquet").touch()

    def reader(paths):
        raise RuntimeError("duckdb exploded")

    assert progress.expected_rows(settings, "fhvhv", reader=reader) is None
```

Add this fixture to `tests/taxi_seeder/test_progress.py` (mirror the field list from `config.Settings`):

```python
import pytest
from taxi_seeder.config import Settings


@pytest.fixture
def settings_for():
    def make(data_dir):
        return Settings(
            data_dir=Path(data_dir), types=("fhvhv",), recent=None,
            password="pw", host="mssql", port=1433, database="taxi",
            schema="dbo", user="sa", data_factor=3.0, data_size_gb=None,
            log_size_gb=None, filegrowth_mb=1024,
        )
    return make


@pytest.fixture
def settings(tmp_path, settings_for):
    return settings_for(tmp_path)
```

> If Task 1 of the **parallel loading** plan has already landed, `Settings` will have gained `mssql_memory_limit_mb`; add it to this fixture as `None`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/taxi_seeder/test_progress.py -v -k expected_rows`
Expected: FAIL — `AttributeError: module 'taxi_seeder.progress' has no attribute 'expected_rows'`

- [ ] **Step 3: Write minimal implementation**

Append to `seeder/src/taxi_seeder/progress.py`:

```python
def _duckdb_footer_rows(paths: list[str]) -> int:
    """Sum num_rows from the parquet footers. Metadata only -- no row scan."""
    import duckdb

    con = duckdb.connect()
    try:
        row = con.execute(
            "SELECT SUM(num_rows) FROM parquet_file_metadata(?)", [paths]
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        con.close()


def expected_rows(settings, data_type: str, *, reader=None) -> int | None:
    """Total rows the normalized parquet holds for one trip type.

    None when the tree is missing or unreadable -- the heartbeat then simply
    omits the percentage and the ETA rather than failing.
    """
    reader = reader or _duckdb_footer_rows
    base = settings.raw_normalized_dir / data_type
    if not base.is_dir():
        return None
    paths = [str(p) for p in sorted(base.rglob("*.parquet"))]
    if not paths:
        return None
    try:
        return reader(paths)
    except Exception:
        return None
```

The bare `except Exception` is deliberate and is the one place this plan allows it: the caller is a progress line, `reader` is duckdb, and no duckdb failure justifies aborting a 13-hour load.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/taxi_seeder/test_progress.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add seeder/src/taxi_seeder/progress.py tests/taxi_seeder/test_progress.py
git commit -m "feat(seeder): read expected row totals from parquet footers"
```

---

### Task 5: The Heartbeat thread

**Files:**
- Modify: `seeder/src/taxi_seeder/progress.py`
- Test: `tests/taxi_seeder/test_progress.py`

**Interfaces:**
- Consumes: `format_progress`, `rows_per_min`, `emit` (Tasks 1–2).
- Produces: `progress.Heartbeat(label, sample, *, total=None, interval_s=60, out=print, clock=time.monotonic, stamp=None)` as a context manager.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/taxi_seeder/test_progress.py
import threading


def test_heartbeat_emits_nothing_when_disabled():
    written = []
    with progress.Heartbeat("load fhvhv", lambda: 1, interval_s=0,
                            out=written.append):
        pass
    assert written == []


def test_heartbeat_emits_a_line_per_tick_with_a_rate():
    written = []
    samples = iter([10_000_000, 12_000_000, 14_000_000])
    ticks = iter([0.0, 60.0, 120.0, 180.0])

    with progress.Heartbeat(
        "load fhvhv", lambda: next(samples), total=100_000_000,
        interval_s=60, out=written.append, clock=lambda: next(ticks),
        stamp=lambda: "07:13:42", sleeper=lambda s: None, max_ticks=3,
    ):
        pass

    assert len(written) == 3
    assert written[0].startswith("[07:13:42] load fhvhv: 10.0M/100M rows (10.0%)")
    # Second tick has a previous sample, so it can report a rate.
    assert "2.00M rows/min" in written[1]


def test_heartbeat_reports_unavailable_when_sampling_raises():
    written = []

    def sample():
        raise RuntimeError("sqlcmd died")

    with progress.Heartbeat(
        "load fhvhv", sample, interval_s=60, out=written.append,
        clock=iter([0.0, 60.0]).__next__, stamp=lambda: "07:13:42",
        sleeper=lambda s: None, max_ticks=1,
    ):
        pass

    assert "rows unavailable" in written[0]


def test_heartbeat_thread_is_joined_on_exit():
    before = threading.active_count()
    with progress.Heartbeat("load fhvhv", lambda: 1, interval_s=60,
                            out=lambda m: None, sleeper=lambda s: None,
                            max_ticks=1, stamp=lambda: "07:13:42"):
        pass
    assert threading.active_count() == before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/taxi_seeder/test_progress.py -v -k heartbeat`
Expected: FAIL — `AttributeError: module 'taxi_seeder.progress' has no attribute 'Heartbeat'`

- [ ] **Step 3: Write minimal implementation**

Append to `seeder/src/taxi_seeder/progress.py`:

```python
import threading


class Heartbeat:
    """Emit one progress line every `interval_s` while the body runs.

    The thread is a daemon and every collaborator is injectable, so the tests
    drive it with a fake clock and a fake sleeper and never wait on wall time.
    `interval_s=0` disables it and starts no thread at all.
    """

    def __init__(self, label: str, sample, *, total: int | None = None,
                 interval_s: int = 60, out=print, clock=time.monotonic,
                 stamp=None, sleeper=None, max_ticks: int | None = None):
        self._label = label
        self._sample = sample
        self._total = total
        self._interval = interval_s
        self._out = out
        self._clock = clock
        self._stamp = stamp or (lambda: utc_hms(time.time()))
        self._sleeper = sleeper or time.sleep
        self._max_ticks = max_ticks
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "Heartbeat":
        if self._interval > 0:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 5)
        return False

    def _loop(self) -> None:
        started = self._clock()
        prev_rows: int | None = None
        prev_at: float | None = None
        ticks = 0
        while not self._stop.is_set():
            if self._max_ticks is None:
                self._stop.wait(self._interval)
                if self._stop.is_set():
                    return
            else:
                self._sleeper(self._interval)

            now = self._clock()
            try:
                rows = self._sample()
            except Exception:
                rows = None

            per_min = None
            if rows is not None and prev_rows is not None and prev_at is not None:
                per_min = rows_per_min(rows - prev_rows, now - prev_at)
            if rows is not None:
                prev_rows, prev_at = rows, now

            emit(
                format_progress(self._label, rows=rows, total=self._total,
                                per_min=per_min, elapsed_s=now - started),
                now=self._stamp(), out=self._out,
            )

            ticks += 1
            if self._max_ticks is not None and ticks >= self._max_ticks:
                return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/taxi_seeder/test_progress.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add seeder/src/taxi_seeder/progress.py tests/taxi_seeder/test_progress.py
git commit -m "feat(seeder): add injectable Heartbeat for long-running stages"
```

---

### Task 6: Configure the interval

**Files:**
- Modify: `seeder/src/taxi_seeder/config.py`, `seeder/docker-compose.yml`, `seeder/src/taxi_seeder/cli.py` (help epilog only)
- Test: `tests/taxi_seeder/test_config.py`

**Interfaces:**
- Consumes: `config._int` (existing).
- Produces: `Settings.progress_interval_s: int`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/taxi_seeder/test_config.py
def test_progress_interval_defaults_to_60():
    s = from_env({"MSSQL_PASSWORD": "pw"})
    assert s.progress_interval_s == 60


def test_progress_interval_can_be_disabled():
    s = from_env({"MSSQL_PASSWORD": "pw", "TAXI_PROGRESS_INTERVAL_S": "0"})
    assert s.progress_interval_s == 0


def test_progress_interval_rejects_a_negative():
    with pytest.raises(ConfigError, match="TAXI_PROGRESS_INTERVAL_S"):
        from_env({"MSSQL_PASSWORD": "pw", "TAXI_PROGRESS_INTERVAL_S": "-1"})


def test_progress_interval_treats_empty_as_unset():
    s = from_env({"MSSQL_PASSWORD": "pw", "TAXI_PROGRESS_INTERVAL_S": ""})
    assert s.progress_interval_s == 60
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/taxi_seeder/test_config.py -v -k progress_interval`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'progress_interval_s'`

- [ ] **Step 3: Write minimal implementation**

In `seeder/src/taxi_seeder/config.py`, add to the `Settings` dataclass after `filegrowth_mb`:

```python
    progress_interval_s: int
```

and in `from_env`, before the `return Settings(`:

```python
    progress_interval_s = _int(env, "TAXI_PROGRESS_INTERVAL_S", 60)
    if progress_interval_s < 0:
        raise ConfigError(
            f"TAXI_PROGRESS_INTERVAL_S must be >= 0 (0 disables the heartbeat), "
            f"got {progress_interval_s}"
        )
```

and add to the constructor call:

```python
        progress_interval_s=progress_interval_s,
```

In `seeder/docker-compose.yml`, add to the `seeder` service `environment:` block:

```yaml
      # Heartbeat cadence while a stage is inside a long subprocess. 0 disables.
      TAXI_PROGRESS_INTERVAL_S: ${TAXI_PROGRESS_INTERVAL_S:-}
```

In `seeder/src/taxi_seeder/cli.py`, add to `_ENV_VARS_HELP` after the `MSSQL_FILEGROWTH_MB` entry:

```
  TAXI_PROGRESS_INTERVAL_S
                        seconds between progress lines during mirror and load
                        (default: 60; 0 disables)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/taxi_seeder/ -v`
Expected: PASS. Any other test constructing `Settings(...)` positionally or exhaustively will now fail — add `progress_interval_s=60` to those constructions.

- [ ] **Step 5: Commit**

```bash
git add seeder/src/taxi_seeder/config.py seeder/src/taxi_seeder/cli.py \
        seeder/docker-compose.yml tests/taxi_seeder/test_config.py
git commit -m "feat(seeder): make the progress heartbeat interval configurable"
```

---

### Task 7: Announce every stage and wire the heartbeat

**Files:**
- Modify: `seeder/src/taxi_seeder/cli.py`
- Test: `tests/taxi_seeder/test_cli.py`

**Interfaces:**
- Consumes: `progress.emit`, `progress.Heartbeat`, `progress.expected_rows`, `report.table_rows`, `Settings.progress_interval_s`.
- Produces: no new public API — `main()` keeps its signature and every existing exit code.

This is the task that closes the original complaint. After it, a silent hour looks like this instead:

```
[07:12:56] mirror yellow: starting
[07:12:57] mirror yellow: done in 1s (exit 0)
[07:13:42] load fhvhv: starting (1.61B rows expected)
[07:14:42] load fhvhv: 2.1M/1.61B rows (0.1%), 2.06M rows/min, elapsed 1m00s, eta 13h01m
[07:15:42] load fhvhv: 4.2M/1.61B rows (0.3%), 2.06M rows/min, elapsed 2m00s, eta 12h59m
```

- [ ] **Step 1: Write the failing test**

```python
# append to tests/taxi_seeder/test_cli.py
from taxi_seeder import cli, progress


def test_main_announces_each_stage(monkeypatch, capsys, base_env):
    monkeypatch.setattr(cli.preflight, "run", lambda s: None)
    monkeypatch.setattr(cli.provision, "run", lambda s: None)
    monkeypatch.setattr(cli.ola, "run", lambda s: None)
    monkeypatch.setattr(cli.backup, "run", lambda s, **kw: False)
    monkeypatch.setattr(cli.backup, "newest_backup", lambda s: None)
    monkeypatch.setattr(cli.report, "loaded_rows", lambda s, **kw: {})
    monkeypatch.setattr(cli.report, "table_rows", lambda s, t, **kw: 5)
    monkeypatch.setattr(cli.progress, "expected_rows", lambda s, t, **kw: 100)
    monkeypatch.setattr(cli.stages, "mirror", lambda s, t, **kw: 0)
    monkeypatch.setattr(cli.stages, "load", lambda s, t, **kw: 0)

    env = {**base_env, "TAXI_TYPES": "green", "TAXI_PROGRESS_INTERVAL_S": "0"}
    monkeypatch.setattr(cli.os, "environ", env)

    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "mirror green: starting" in out
    assert "mirror green: done" in out
    assert "load green: starting" in out
    assert "load green: done" in out
    assert "provision: starting" in out
    assert "backup: starting" in out


def test_main_announces_a_failing_stage_and_still_returns_its_code(
        monkeypatch, capsys, base_env):
    monkeypatch.setattr(cli.preflight, "run", lambda s: None)
    monkeypatch.setattr(cli.stages, "mirror", lambda s, t, **kw: 2)

    env = {**base_env, "TAXI_TYPES": "green", "TAXI_PROGRESS_INTERVAL_S": "0"}
    monkeypatch.setattr(cli.os, "environ", env)

    assert cli.main([]) == 2
    out = capsys.readouterr().out
    assert "mirror green: starting" in out
```

Define `base_env` as a fixture in `tests/taxi_seeder/test_cli.py` if it does not already exist:

```python
@pytest.fixture
def base_env():
    return {"MSSQL_PASSWORD": "pw", "TAXI_DATA_DIR": "/data"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/taxi_seeder/test_cli.py -v -k announce`
Expected: FAIL — `assert 'mirror green: starting' in ''`

- [ ] **Step 3: Write minimal implementation**

In `seeder/src/taxi_seeder/cli.py`, add `progress` to the package import:

```python
from taxi_seeder import (backup, mssql, ola, preflight, progress, provision,
                         report, sql, stages)
```

Add this helper above `main`:

```python
def _timed(settings, label: str, fn):
    """Announce a stage, run it, announce how long it took and what it returned.

    Returns whatever `fn` returns. Deliberately does not catch: cli.main's
    existing except block owns the error-to-exit-code mapping, and swallowing
    here would change exit codes, which this plan forbids.
    """
    started = time.monotonic()
    progress.emit(f"{label}: starting")
    result = fn()
    elapsed = progress.humanize_duration(time.monotonic() - started)
    suffix = f" (exit {result})" if isinstance(result, int) else ""
    progress.emit(f"{label}: done in {elapsed}{suffix}")
    return result
```

and add `import time` to the imports at the top.

Then replace the body of the `try:` block in `main` with:

```python
        _timed(settings, "preflight", lambda: preflight.run(settings))

        for data_type in settings.types:
            rc = _timed(settings, f"mirror {data_type}",
                        lambda dt=data_type: stages.mirror(settings, dt))
            codes.append(rc)
            if rc != CLEAN:
                print(f"{_halt_label(rc)}: mirror stage for {data_type} exited {rc}; "
                      f"stopping", file=sys.stderr)
                return worst(codes)

        _timed(settings, "provision", lambda: provision.run(settings))
        _timed(settings, "ola", lambda: ola.run(settings))

        for data_type in settings.types:
            total = progress.expected_rows(settings, data_type)
            hint = f" ({progress.humanize_count(total)} rows expected)" if total else ""
            progress.emit(f"load {data_type}: starting{hint}")
            started = time.monotonic()
            with progress.Heartbeat(
                f"load {data_type}",
                lambda dt=data_type: report.table_rows(settings, dt),
                total=total,
                interval_s=settings.progress_interval_s,
            ):
                child_rc = stages.load(settings, data_type)
            progress.emit(
                f"load {data_type}: done in "
                f"{progress.humanize_duration(time.monotonic() - started)} "
                f"(exit {child_rc})"
            )
            rc = load_exit_code(child_rc)
            codes.append(rc)
            if rc != CLEAN:
                detail = (" (failed mid-load -- re-running resumes from the "
                          "manifest)" if rc != child_rc else "")
                print(f"{_halt_label(rc)}: load stage for {data_type} exited "
                      f"{child_rc}{detail}; stopping", file=sys.stderr)
                return worst(codes)

        _timed(settings, "backup", lambda: backup.run(settings))
```

The `dt=data_type` default-argument binding in both lambdas is load-bearing: without it every closure would capture the loop variable and report the last trip type.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/taxi_seeder/ -v`
Expected: PASS (whole suite)

- [ ] **Step 5: Commit**

```bash
git add seeder/src/taxi_seeder/cli.py tests/taxi_seeder/test_cli.py
git commit -m "feat(seeder): announce every stage and heartbeat during loads"
```

---

### Task 8: Document it, and stop `docker compose up` looking like a crash

**Files:**
- Modify: `README.md`
- Test: manual (documentation task)

The 2026-08-15 incident was not a code failure: the tmux server hosting `docker compose up` died, the client went with it, the containers kept running, and the next `docker compose up` **recreated the seeder and destroyed ~50 minutes of in-flight work**. The docs should make the detached form the default advice.

- [ ] **Step 1: Add a "Watching a run" section to `README.md`**

````markdown
## Watching a run

Start the stack detached, then follow the logs. `docker compose up` in the
foreground ties the run to your terminal — if the terminal dies, the containers
keep running but you lose the output, and running `docker compose up` a second
time **recreates the seeder and discards whatever it was loading**.

```bash
docker compose up -d          # start detached
docker compose logs -f seeder # follow; Ctrl-C detaches, nothing is killed
```

The seeder announces every stage and, during mirror and load, prints a progress
line every 60 seconds:

```
[07:13:42] load fhvhv: starting (1.61B rows expected)
[07:14:42] load fhvhv: 2.1M/1.61B rows (0.1%), 2.06M rows/min, elapsed 1m00s, eta 13h01m
```

Set `TAXI_PROGRESS_INTERVAL_S` in `.env` to change the cadence, or `0` to
switch the heartbeat off.

To check on a run from another shell without touching it:

```bash
docker compose ps
docker compose logs --tail 20 seeder
```
````

- [ ] **Step 2: Verify the commands in the README actually work**

Run each command in the block against a running stack and confirm the output matches what the README claims.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document detached runs and the progress heartbeat"
```

---

## Self-Review

**Coverage against the Background findings:**

| Finding | Task |
|---|---|
| One line per trip type, nothing for hours | 5, 7 (heartbeat) |
| No stage announcements | 7 (`_timed`) |
| No `PYTHONUNBUFFERED` | 1 |
| Manifest unusable as a progress source during RELOAD | 3 (`sys.partitions`) |
| No way to tell how far along / how long left | 4 (totals), 2 (ETA) |
| `docker compose up` destroys in-flight work | 8 (docs) |

**Type consistency:** `format_progress` is keyword-only after `label` in both its definition (Task 2) and its call site in `Heartbeat._loop` (Task 5). `report.table_rows` returns `int | None` (Task 3) and is consumed as the `sample` callable in Task 7, which `Heartbeat` allows to return `None`. `progress.expected_rows` returns `int | None` (Task 4) and feeds `Heartbeat(total=...)`, which is `int | None` (Task 5).

**Known follow-on:** Task 6 adds a field to the frozen `Settings` dataclass. Every test that constructs `Settings` exhaustively must gain `progress_interval_s=60`. Task 6 Step 4 calls this out.
