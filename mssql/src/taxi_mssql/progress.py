"""Progress for a run that spends hours inside a single subprocess.

Everything above the Heartbeat class is pure: no clock, no database, no IO.
That is deliberate -- the formatting is the part worth testing, and it is
testable only while it stays free of the thread that drives it.
"""
from __future__ import annotations

import threading
import time

from taxi_mssql.config import Settings


def utc_hms(epoch_s: float) -> str:
    return time.strftime("%H:%M:%S", time.gmtime(epoch_s))


def emit(message: str, *, now: str | None = None, out=print) -> None:
    """One progress line. `now` is injectable so tests never touch the clock."""
    stamp = now if now is not None else utc_hms(time.time())
    out(f"[{stamp}] {message}")


def humanize_count(n: int) -> str:
    """Row counts in the billions are unreadable in full, and the exact digit
    never matters in a progress line -- the final report prints it exactly.

    Three significant digits, and `#` keeps the trailing zeros: without it a
    steady rate renders as "2M" one minute and "2.06M" the next, which reads
    like the load fell off a cliff. The rstrip drops the bare trailing point
    `#` leaves on a three-digit value ("100." -> "100").
    """
    for limit, suffix, div in (
        (1_000_000_000, "B", 1_000_000_000.0),
        (1_000_000, "M", 1_000_000.0),
        (1_000, "K", 1_000.0),
    ):
        if n >= limit:
            return f"{n / div:#.3g}".rstrip(".") + suffix
    return str(n)


def humanize_duration(seconds: float) -> str:
    """The largest two units, the smaller one zero-padded so consecutive
    heartbeat lines stay column-aligned."""
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
                    per_min: float | None, elapsed_s: float,
                    unit: str = "rows") -> str:
    """One heartbeat line.

    `unit` exists because mirror has no row count to sample: it counts
    normalized parquet files, and the arithmetic is otherwise identical.
    """
    if rows is None:
        return f"{label}: {unit} unavailable, elapsed {humanize_duration(elapsed_s)}"

    parts = [humanize_count(rows)]
    if total:
        parts[0] += f"/{humanize_count(total)} {unit} ({rows * 100.0 / total:.1f}%)"
    else:
        parts[0] += f" {unit}"
    if per_min:
        parts.append(f"{humanize_count(int(per_min))} {unit}/min")
    parts.append(f"elapsed {humanize_duration(elapsed_s)}")
    remaining = eta_seconds((total or 0) - rows, per_min)
    if remaining is not None:
        parts.append(f"eta {humanize_duration(remaining)}")
    return f"{label}: " + ", ".join(parts)


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


def expected_rows(settings: Settings, data_type: str, *, reader=None) -> int | None:
    """Total rows the normalized parquet holds for one trip type.

    This is what makes a percentage and an ETA possible at all. None when the
    tree is missing or unreadable -- the heartbeat then simply omits both rather
    than failing.
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
        # The one place this module allows a bare except: the caller is a
        # progress line, the reader is duckdb, and no duckdb failure justifies
        # aborting a 13-hour load.
        return None


def normalized_files(settings: Settings, data_type: str) -> int | None:
    """Normalized parquet files on disk for one trip type -- mirror's sample.

    Mirror has no row count to read: the rows are not in the database yet, and
    the months still to download are not on disk to be counted. The file count
    is the only thing that moves, so the mirror line carries a count and a rate
    but no percentage and no ETA.

    Zero rather than None for an absent directory: a mirror that has not written
    its first file is at zero, which is an observation, not a failure to observe.
    """
    base = settings.raw_normalized_dir / data_type
    try:
        return sum(1 for _ in base.rglob("*.parquet")) if base.is_dir() else 0
    except OSError:
        return None


class Heartbeat:
    """Emit one progress line every `interval_s` while the body runs.

    The thread is a daemon and every collaborator is injectable, so the tests
    drive it with a fake clock and a fake sleeper and never wait on wall time.
    `interval_s=0` disables it and starts no thread at all.

    `sleeper(seconds)` returns true when the run ended during the interval --
    threading.Event.wait's own contract, which is why it is the default. That
    one convention is what keeps a stage which finishes three seconds into a
    sixty-second interval from printing a stale line after its "done" line.
    """

    def __init__(self, label: str, sample, *, total: int | None = None,
                 interval_s: int = 60, unit: str = "rows", out=print,
                 clock=time.monotonic, stamp=None, sleeper=None,
                 max_ticks: int | None = None):
        self._label = label
        self._sample = sample
        self._total = total
        self._interval = interval_s
        self._unit = unit
        self._out = out
        self._clock = clock
        self._stamp = stamp or (lambda: utc_hms(time.time()))
        self._stop = threading.Event()
        self._sleeper = sleeper or self._stop.wait
        # Bounds the loop so the tests get an exact number of ticks instead of
        # racing __exit__ for them. Production leaves it None.
        self._max_ticks = max_ticks
        self._started: float | None = None
        self._prev_rows: int | None = None
        self._prev_at: float | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "Heartbeat":
        self._started = self._clock()
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
        ticks = 0
        while self._max_ticks is None or ticks < self._max_ticks:
            if self._sleeper(self._interval):
                return
            self._tick()
            ticks += 1

    def _tick(self) -> None:
        now = self._clock()
        # `self._started or now` would be wrong: a monotonic clock legitimately
        # reads 0.0, and that is exactly what the tests inject.
        started = now if self._started is None else self._started
        try:
            rows = self._sample()
        except Exception:
            # A sampling failure costs a progress line, never the load.
            rows = None

        per_min = None
        if rows is not None and self._prev_rows is not None and self._prev_at is not None:
            per_min = rows_per_min(rows - self._prev_rows, now - self._prev_at)
        if rows is not None:
            self._prev_rows, self._prev_at = rows, now

        emit(
            format_progress(self._label, rows=rows, total=self._total,
                            per_min=per_min, elapsed_s=now - started,
                            unit=self._unit),
            now=self._stamp(), out=self._out,
        )


def expected_rows_total(settings: Settings, data_types, *, reader=None) -> int | None:
    """Expected rows across every selected trip type, or None if any is unknown.

    The load phase is one fan-out over every type at once, so its percentage and
    ETA are computed against this rather than one type's total.
    """
    total = 0
    seen = False
    for data_type in data_types:
        rows = expected_rows(settings, data_type, reader=reader)
        if rows is None:
            return None
        total += rows
        seen = True
    return total if seen else None
