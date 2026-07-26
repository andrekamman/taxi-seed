# Python downloader (`taxi_download`) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 488-line bash downloader with a Python package `taxi_download` (console script `taxi-download`, module `python -m taxi_download.cli`) that ships in the wheel, so `pip install taxi-seed` can download TLC trip-data with no repo checkout and no `bash`/`curl` shell-out.

**Architecture:** Three modules under `downloader/src/taxi_download/`: `dates.py` (pure month arithmetic, no I/O), `download.py` (fetch + PAR1 validation + forward/backward walkers, all injectable with `client`/`today`/`sleeper` so every branch is testable offline and instantly), and `cli.py` (argparse + `main`). The orchestrator switches its download stage from `bash …/download_taxi_data.sh` to `python -m taxi_download.cli`, and its repo-root marker from the going-away bash script to `pyproject.toml` + `normalize/mappings/`. Tests never touch the network: pure units plus a stdlib `http.server` stub serving DuckDB-built parquet.

**Tech Stack:** Python 3.12+ (argparse, `pathlib`, `enum`, `dataclasses`), `httpx>=0.27` (new runtime dep), `duckdb` (test fixture parquet), `http.server` (test stub), pytest, `uv`.

## Global Constraints

_Every task's requirements implicitly include this section._

- **New runtime dependency:** `httpx>=0.27`. Final runtime deps are exactly `duckdb`, `pyyaml`, `httpx`. No other new deps.
- **Python floor:** `requires-python = ">=3.12"`. Use only 3.12+ stdlib.
- **CloudFront URL scheme (verbatim):** `https://d37ci6vzurychx.cloudfront.net/trip-data/<type>_tripdata_<year>-<mm>.parquet` — month is **always 2-digit zero-padded**, year is 4-digit unpadded.
- **Four types + start dates (verbatim):** `yellow` (2009, 1), `green` (2013, 8), `fhv` (2015, 1), `fhvhv` (2019, 2).
- **Output layout (verbatim):** `<data-dir>/raw/<type>/<year>/<file>`. Skip files that already exist.
- **PAR1 magic:** a valid parquet is ≥ 8 bytes with `b"PAR1"` at both the first 4 and last 4 bytes.
- **`--recent [N]` const default is `3`** (matches the bash default when N is omitted). `--recent 0` from the orchestrator means "N defaulted", handled at the orchestrator layer.
- **`MAX_LOOKBACK = 18`**, backoff constants `BACKOFF_BASE_S, BACKOFF_FACTOR, BACKOFF_CAP_S, MAX_RETRIES = 30, 3, 3600, 4`.
- **No real network in any test. No committed `.parquet`.** Build sample parquet with DuckDB at test time; inject `today` and `sleeper` so walkers/backoff are wall-clock-free and instant.
- **Determinism in scripts/tests:** never call `date.today()` inside a walker — always pass `today` in.

---

## Task 1: Package scaffold + `dates.py` (pure month arithmetic)

Creates the package so `import taxi_download` works in tests, and lands the pure date module. No network, no `httpx` yet.

**Files:**
- Create: `downloader/src/taxi_download/__init__.py`
- Create: `downloader/src/taxi_download/dates.py`
- Modify: `pyproject.toml` (`[tool.hatch.build.targets.wheel].packages` — add the new package so `uv sync` makes it importable)
- Test: `tests/downloader/test_dates.py`

**Interfaces:**
- Produces:
  - `START_DATES: dict[str, tuple[int, int]]` = `{"yellow": (2009, 1), "green": (2013, 8), "fhv": (2015, 1), "fhvhv": (2019, 2)}`
  - `previous_month(today: datetime.date) -> tuple[int, int]` — (year, month) of the calendar month before `today`.
  - `months_forward(start: tuple[int, int], end: tuple[int, int]) -> Iterator[tuple[int, int]]` — ascending, inclusive of both endpoints.
  - `months_backward(start: tuple[int, int]) -> Iterator[tuple[int, int]]` — descending, unbounded (caller stops).

- [ ] **Step 1: Create the package `__init__.py`**

```python
# downloader/src/taxi_download/__init__.py
"""taxi_download — download NYC TLC trip-data parquet from CloudFront."""
```

- [ ] **Step 2: Register the package in the wheel so it is importable**

In `pyproject.toml`, add the new path to the existing `[tool.hatch.build.targets.wheel]` `packages` list (keep the list alphabetized as it is now):

```toml
[tool.hatch.build.targets.wheel]
packages = [
  "downloader/src/taxi_download",
  "loader/src/taxi_loader",
  "normalize/src/taxi_normalize",
  "orchestrator/src/taxi_orchestrate",
  "schema-drift/src/schema_drift",
  "shared/src/taxi_shared",
]
```

- [ ] **Step 3: Sync so the editable install picks up the new package**

Run: `uv sync --extra test`
Expected: succeeds; `uv run python -c "import taxi_download"` prints nothing and exits 0.

- [ ] **Step 4: Write the failing test**

```python
# tests/downloader/test_dates.py
"""Pure month arithmetic — no I/O, no network."""
from datetime import date

from taxi_download.dates import (
    START_DATES,
    months_backward,
    months_forward,
    previous_month,
)


def test_start_dates_exact():
    assert START_DATES == {
        "yellow": (2009, 1),
        "green": (2013, 8),
        "fhv": (2015, 1),
        "fhvhv": (2019, 2),
    }


def test_previous_month_mid_year():
    assert previous_month(date(2026, 7, 26)) == (2026, 6)


def test_previous_month_january_wraps():
    assert previous_month(date(2026, 1, 15)) == (2025, 12)


def test_months_forward_inclusive_and_year_rollover():
    got = list(months_forward((2013, 11), (2014, 2)))
    assert got == [(2013, 11), (2013, 12), (2014, 1), (2014, 2)]


def test_months_forward_single_month():
    assert list(months_forward((2020, 5), (2020, 5))) == [(2020, 5)]


def test_months_forward_empty_when_start_after_end():
    assert list(months_forward((2021, 3), (2021, 2))) == []


def test_months_backward_descends_and_wraps():
    it = months_backward((2020, 2))
    got = [next(it) for _ in range(4)]
    assert got == [(2020, 2), (2020, 1), (2019, 12), (2019, 11)]
```

- [ ] **Step 5: Run test to verify it fails**

Run: `uv run --extra test pytest tests/downloader/test_dates.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_download.dates'`.

- [ ] **Step 6: Implement `dates.py`**

```python
# downloader/src/taxi_download/dates.py
"""Pure month arithmetic. No I/O, no wall-clock, no network."""
from __future__ import annotations

from datetime import date
from typing import Iterator

START_DATES: dict[str, tuple[int, int]] = {
    "yellow": (2009, 1),
    "green": (2013, 8),
    "fhv": (2015, 1),
    "fhvhv": (2019, 2),
}


def previous_month(today: date) -> tuple[int, int]:
    """(year, month) of the calendar month before `today`."""
    if today.month == 1:
        return (today.year - 1, 12)
    return (today.year, today.month - 1)


def months_forward(start: tuple[int, int], end: tuple[int, int]) -> Iterator[tuple[int, int]]:
    """Ascending (year, month) from `start` through `end`, inclusive."""
    y, m = start
    while (y, m) <= end:
        yield (y, m)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def months_backward(start: tuple[int, int]) -> Iterator[tuple[int, int]]:
    """Descending (year, month) from `start`, unbounded — the caller stops it."""
    y, m = start
    while True:
        yield (y, m)
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/downloader/test_dates.py -q`
Expected: PASS (6 tests).

- [ ] **Step 8: Commit**

```bash
git add downloader/src/taxi_download/__init__.py downloader/src/taxi_download/dates.py pyproject.toml uv.lock tests/downloader/test_dates.py
git commit -m "feat(downloader): taxi_download package scaffold + pure dates module"
```

---

## Task 2: `download.py` — constants, path helpers, PAR1 validation

Pure, I/O-light helpers plus the `FetchResult` enum. Adds the `httpx` runtime dep (imported at module top for the walker signatures in later tasks).

**Files:**
- Create: `downloader/src/taxi_download/download.py`
- Modify: `pyproject.toml` (`[project.dependencies]` — add `httpx`)
- Test: `tests/downloader/test_download_helpers.py`

**Interfaces:**
- Consumes: `taxi_download.dates` (imported for later tasks; not exercised here).
- Produces (all in `taxi_download.download`):
  - Constants: `BASE_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data"`, `DATA_TYPES = ("yellow", "green", "fhv", "fhvhv")`, `MAX_LOOKBACK = 18`, `PARQUET_MAGIC = b"PAR1"`, `BACKOFF_BASE_S, BACKOFF_FACTOR, BACKOFF_CAP_S, MAX_RETRIES = 30, 3, 3600, 4`.
  - `class FetchResult(Enum)` with members `OK`, `NOTFOUND`, `RATELIMIT`, `NETERROR`.
  - `filename(data_type: str, year: int, month: int) -> str`
  - `url_for(data_type: str, year: int, month: int) -> str`
  - `target_path(raw_dir: Path, data_type: str, year: int, month: int) -> Path`
  - `is_valid_parquet(path) -> bool`
  - `clean_corrupt(raw_dir) -> int`

- [ ] **Step 1: Add the `httpx` runtime dependency**

In `pyproject.toml`, `[project.dependencies]`:

```toml
dependencies = [
    "duckdb>=1.4.4",
    "httpx>=0.27",
    "pyyaml>=6.0",
]
```

Run: `uv sync --extra test`
Expected: resolves cleanly; `uv run python -c "import httpx"` exits 0. If resolution conflicts with `duckdb`/`pyyaml`, stop and report — do not pin around it silently.

- [ ] **Step 2: Write the failing test**

```python
# tests/downloader/test_download_helpers.py
"""Path builders, PAR1 validation, and the corrupt-file cleanup pass."""
from pathlib import Path

import duckdb

from taxi_download.download import (
    BASE_URL,
    FetchResult,
    clean_corrupt,
    filename,
    is_valid_parquet,
    target_path,
    url_for,
)


def _write_valid_parquet(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    duckdb.execute(f"COPY (SELECT 1 AS a) TO '{path}' (FORMAT PARQUET)")


def test_filename_pads_month_not_year():
    assert filename("yellow", 2009, 1) == "yellow_tripdata_2009-01.parquet"
    assert filename("fhvhv", 2019, 12) == "fhvhv_tripdata_2019-12.parquet"


def test_url_for_full_scheme():
    assert url_for("green", 2013, 8) == f"{BASE_URL}/green_tripdata_2013-08.parquet"


def test_target_path_layout():
    p = target_path(Path("/data/raw"), "yellow", 2020, 3)
    assert p == Path("/data/raw/yellow/2020/yellow_tripdata_2020-03.parquet")


def test_fetchresult_members():
    assert {m.name for m in FetchResult} == {"OK", "NOTFOUND", "RATELIMIT", "NETERROR"}


def test_is_valid_parquet_true_for_real_file(tmp_path):
    p = tmp_path / "good.parquet"
    _write_valid_parquet(p)
    assert is_valid_parquet(p) is True


def test_is_valid_parquet_false_for_short_file(tmp_path):
    p = tmp_path / "short.parquet"
    p.write_bytes(b"PAR1")  # only 4 bytes
    assert is_valid_parquet(p) is False


def test_is_valid_parquet_false_for_bad_magic(tmp_path):
    p = tmp_path / "html.parquet"
    p.write_bytes(b"<html>rate limited</html>\n")  # >= 8 bytes, wrong magic
    assert is_valid_parquet(p) is False


def test_is_valid_parquet_false_for_missing_file(tmp_path):
    assert is_valid_parquet(tmp_path / "nope.parquet") is False


def test_clean_corrupt_deletes_only_invalid(tmp_path):
    raw = tmp_path / "raw"
    good = raw / "yellow" / "2020" / "yellow_tripdata_2020-01.parquet"
    bad = raw / "yellow" / "2020" / "yellow_tripdata_2020-02.parquet"
    _write_valid_parquet(good)
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"<html>nope</html>\n")

    removed = clean_corrupt(raw)

    assert removed == 1
    assert good.exists()
    assert not bad.exists()


def test_clean_corrupt_zero_when_dir_absent(tmp_path):
    assert clean_corrupt(tmp_path / "does-not-exist") == 0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run --extra test pytest tests/downloader/test_download_helpers.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_download.download'`.

- [ ] **Step 4: Implement `download.py` (helpers only)**

```python
# downloader/src/taxi_download/download.py
"""Fetch + PAR1 validation + walkers. Every function is injectable
(`client`, `today`, `sleeper`) so all branches test offline and instantly."""
from __future__ import annotations

from enum import Enum
from pathlib import Path

import httpx

from taxi_download.dates import (
    START_DATES,
    months_backward,
    months_forward,
    previous_month,
)

BASE_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data"
DATA_TYPES = ("yellow", "green", "fhv", "fhvhv")
MAX_LOOKBACK = 18
PARQUET_MAGIC = b"PAR1"
# capped exponential backoff: 30s, 90s, 270s, … capped at 3600s, up to MAX_RETRIES tries
BACKOFF_BASE_S, BACKOFF_FACTOR, BACKOFF_CAP_S, MAX_RETRIES = 30, 3, 3600, 4


class FetchResult(Enum):
    OK = "OK"
    NOTFOUND = "NOTFOUND"
    RATELIMIT = "RATELIMIT"
    NETERROR = "NETERROR"


def filename(data_type: str, year: int, month: int) -> str:
    return f"{data_type}_tripdata_{year}-{month:02d}.parquet"


def url_for(data_type: str, year: int, month: int) -> str:
    return f"{BASE_URL}/{filename(data_type, year, month)}"


def target_path(raw_dir, data_type: str, year: int, month: int) -> Path:
    return Path(raw_dir) / data_type / str(year) / filename(data_type, year, month)


def is_valid_parquet(path) -> bool:
    """A valid parquet is >= 8 bytes with PAR1 at both the first and last 4 bytes."""
    p = Path(path)
    try:
        if p.stat().st_size < 8:
            return False
        with p.open("rb") as f:
            head = f.read(4)
            f.seek(-4, 2)
            tail = f.read(4)
    except OSError:
        return False
    return head == PARQUET_MAGIC and tail == PARQUET_MAGIC


def clean_corrupt(raw_dir) -> int:
    """Scan raw_dir for *.parquet, delete any that fail PAR1 validation, return the count."""
    raw = Path(raw_dir)
    if not raw.exists():
        return 0
    removed = 0
    for p in raw.rglob("*.parquet"):
        if not is_valid_parquet(p):
            p.unlink()
            removed += 1
    return removed
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/downloader/test_download_helpers.py -q`
Expected: PASS (10 tests).

- [ ] **Step 6: Commit**

```bash
git add downloader/src/taxi_download/download.py pyproject.toml uv.lock tests/downloader/test_download_helpers.py
git commit -m "feat(downloader): download helpers, PAR1 validation, httpx dep"
```

---

## Task 3: `fetch_one` + `download_month` (single-file fetch with backoff)

The network boundary. `fetch_one` does one GET with no retry; `download_month` owns the capped-exponential-backoff retry loop. Tested against a stdlib stub HTTP server — no real network.

**Files:**
- Modify: `downloader/src/taxi_download/download.py` (append `_classify_status`, `fetch_one`, `download_month`)
- Create: `tests/downloader/conftest.py` (stub-server fixture + parquet builder — shared with Task 4)
- Test: `tests/downloader/test_fetch.py`

**Interfaces:**
- Consumes: `FetchResult`, `is_valid_parquet`, `target_path`, `url_for`, backoff constants (Task 2).
- Produces (in `taxi_download.download`):
  - `fetch_one(client: httpx.Client, url: str, dest: Path) -> FetchResult` — streams a GET to a `.part` temp file; on `200` validates PAR1 (invalid → `RATELIMIT`, the HTML-intercept case) and renames into place (`OK`); maps non-200 via `_classify_status`; transport errors → `NETERROR`. **No retry.** Never leaves a `.part` file behind.
  - `download_month(client, data_type, year, month, raw_dir, sleeper) -> FetchResult` — if the target already exists, returns `OK` without fetching; otherwise calls `fetch_one`, retrying on `RATELIMIT`/`NETERROR` up to `MAX_RETRIES` with `sleeper(delay)` between tries (delay = `min(BACKOFF_BASE_S * BACKOFF_FACTOR**attempt, BACKOFF_CAP_S)`), returning `OK`/`NOTFOUND` immediately and the last result on give-up. `sleeper` is an injected `Callable[[float], None]`.

- [ ] **Step 1: Create the shared stub-server fixture**

```python
# tests/downloader/conftest.py
"""A stdlib HTTP stub that serves real (DuckDB-built) parquet for a chosen set of
months, 404 for anything else, and a designated month that returns 429 a fixed
number of times before succeeding — so backoff/walker tests never hit the network.

Usage: `stub` yields an object with `.base_url` (point BASE_URL at it),
`.present` (set of "<type>_tripdata_<yyyy>-<mm>.parquet" filenames it serves),
and `.ratelimit` (dict filename -> remaining 429s to emit before a 200)."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import duckdb
import pytest


def _valid_parquet_bytes(tmp_path) -> bytes:
    p = tmp_path / "_sample.parquet"
    duckdb.execute(f"COPY (SELECT 1 AS a) TO '{p}' (FORMAT PARQUET)")
    return p.read_bytes()


class _State:
    def __init__(self, body: bytes):
        self.body = body
        self.present: set[str] = set()
        self.ratelimit: dict[str, int] = {}
        self.base_url = ""


@pytest.fixture
def stub(tmp_path):
    state = _State(_valid_parquet_bytes(tmp_path))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence
            pass

        def do_GET(self):
            name = self.path.rsplit("/", 1)[-1]
            if state.ratelimit.get(name, 0) > 0:
                state.ratelimit[name] -= 1
                self.send_response(429)
                self.end_headers()
                self.wfile.write(b"slow down")
                return
            if name in state.present:
                self.send_response(200)
                self.send_header("Content-Length", str(len(state.body)))
                self.end_headers()
                self.wfile.write(state.body)
                return
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"NoSuchKey")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    state.base_url = f"http://{host}:{port}/trip-data"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
```

- [ ] **Step 2: Write the failing test**

```python
# tests/downloader/test_fetch.py
"""fetch_one + download_month against the stub server. No real network."""
import httpx
import pytest

from taxi_download import download as dl
from taxi_download.download import FetchResult, fetch_one, download_month, target_path


@pytest.fixture
def client():
    with httpx.Client(follow_redirects=True, timeout=5.0) as c:
        yield c


def _point_at_stub(monkeypatch, stub):
    monkeypatch.setattr(dl, "BASE_URL", stub.base_url)


def test_fetch_one_ok_writes_valid_file(client, stub, tmp_path, monkeypatch):
    _point_at_stub(monkeypatch, stub)
    stub.present.add("yellow_tripdata_2025-06.parquet")
    dest = tmp_path / "raw" / "yellow" / "2025" / "yellow_tripdata_2025-06.parquet"
    res = fetch_one(client, dl.url_for("yellow", 2025, 6), dest)
    assert res is FetchResult.OK
    assert dl.is_valid_parquet(dest)
    assert not (dest.parent / (dest.name + ".part")).exists()


def test_fetch_one_404_is_notfound(client, stub, tmp_path, monkeypatch):
    _point_at_stub(monkeypatch, stub)  # nothing in stub.present
    dest = tmp_path / "green_tripdata_2013-07.parquet"
    assert fetch_one(client, dl.url_for("green", 2013, 7), dest) is FetchResult.NOTFOUND
    assert not dest.exists()


def test_fetch_one_429_is_ratelimit(client, stub, tmp_path, monkeypatch):
    _point_at_stub(monkeypatch, stub)
    stub.ratelimit["yellow_tripdata_2025-06.parquet"] = 99
    dest = tmp_path / "yellow_tripdata_2025-06.parquet"
    assert fetch_one(client, dl.url_for("yellow", 2025, 6), dest) is FetchResult.RATELIMIT


def test_download_month_skips_existing(client, stub, tmp_path, monkeypatch):
    _point_at_stub(monkeypatch, stub)
    dest = target_path(tmp_path, "yellow", 2025, 6)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"PAR1____PAR1")  # any existing file
    calls = []
    res = download_month(client, "yellow", 2025, 6, tmp_path, calls.append)
    assert res is FetchResult.OK
    assert calls == []  # never slept, and (stub has nothing) never fetched


def test_download_month_backs_off_then_succeeds(client, stub, tmp_path, monkeypatch):
    _point_at_stub(monkeypatch, stub)
    stub.present.add("yellow_tripdata_2025-06.parquet")
    stub.ratelimit["yellow_tripdata_2025-06.parquet"] = 2  # two 429s, then 200
    slept = []
    res = download_month(client, "yellow", 2025, 6, tmp_path, slept.append)
    assert res is FetchResult.OK
    assert slept == [30, 90]  # backoff before attempts 2 and 3
    assert dl.is_valid_parquet(target_path(tmp_path, "yellow", 2025, 6))


def test_download_month_gives_up_on_persistent_ratelimit(client, stub, tmp_path, monkeypatch):
    _point_at_stub(monkeypatch, stub)
    stub.present.add("yellow_tripdata_2025-06.parquet")
    stub.ratelimit["yellow_tripdata_2025-06.parquet"] = 99  # always 429
    slept = []
    res = download_month(client, "yellow", 2025, 6, tmp_path, slept.append)
    assert res is FetchResult.RATELIMIT
    assert slept == [30, 90, 270]  # MAX_RETRIES=4 attempts => 3 sleeps
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run --extra test pytest tests/downloader/test_fetch.py -q`
Expected: FAIL — `ImportError: cannot import name 'fetch_one'`.

- [ ] **Step 4: Implement `_classify_status`, `fetch_one`, `download_month`**

Append to `downloader/src/taxi_download/download.py`:

```python
def _classify_status(status: int, body: bytes) -> FetchResult:
    text = body.decode("utf-8", "replace").lower()
    if status == 404:
        return FetchResult.NOTFOUND
    if status == 403:
        if "accessdenied" in text or "nosuchkey" in text:
            return FetchResult.NOTFOUND
        return FetchResult.RATELIMIT  # cloudfront block page
    if status == 429 or 500 <= status < 600:
        return FetchResult.RATELIMIT
    return FetchResult.NETERROR


def fetch_one(client: httpx.Client, url: str, dest) -> FetchResult:
    """One GET, no retry. Streams to a .part temp file; validates PAR1 on 200.
    An HTML rate-limit intercept page (200 but bad magic) maps to RATELIMIT."""
    dest = Path(dest)
    tmp = dest.with_name(dest.name + ".part")
    try:
        with client.stream("GET", url) as resp:
            if resp.status_code == 200:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tmp.open("wb") as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
                if not is_valid_parquet(tmp):
                    tmp.unlink(missing_ok=True)
                    return FetchResult.RATELIMIT
                tmp.replace(dest)
                return FetchResult.OK
            body = resp.read()
    except httpx.HTTPError:
        tmp.unlink(missing_ok=True)
        return FetchResult.NETERROR
    tmp.unlink(missing_ok=True)
    return _classify_status(resp.status_code, body)


def download_month(client, data_type: str, year: int, month: int, raw_dir, sleeper) -> FetchResult:
    """Skip if the target already exists; else fetch with capped-exponential
    backoff retry on RATELIMIT/NETERROR up to MAX_RETRIES. `sleeper(delay_s)` is injected."""
    dest = target_path(raw_dir, data_type, year, month)
    if dest.exists():
        return FetchResult.OK
    url = url_for(data_type, year, month)
    result = FetchResult.NETERROR
    for attempt in range(MAX_RETRIES):
        result = fetch_one(client, url, dest)
        if result in (FetchResult.OK, FetchResult.NOTFOUND):
            return result
        if attempt < MAX_RETRIES - 1:
            delay = min(BACKOFF_BASE_S * (BACKOFF_FACTOR ** attempt), BACKOFF_CAP_S)
            sleeper(delay)
    return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/downloader/test_fetch.py -q`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
git add downloader/src/taxi_download/download.py tests/downloader/conftest.py tests/downloader/test_fetch.py
git commit -m "feat(downloader): fetch_one + download_month with backoff (stub-server tested)"
```

---

## Task 4: `download_full` + `download_recent` walkers

The forward (full-history) and backward (recent-N) walkers, returning a `WalkSummary(downloaded, gaveup)` so the CLI can distinguish "nothing new because all present" (exit 0) from "no progress because rate-limited" (exit 2).

> **Interface note:** the design spec sketches these as `-> int`. This plan returns a small `WalkSummary` dataclass instead — the exit-code rule ("a rate-limit give-up prevented all progress for a requested type") needs the give-up count, which a bare int cannot carry. `WalkSummary.downloaded` is the count the spec referred to.

**Files:**
- Modify: `downloader/src/taxi_download/download.py` (add `WalkSummary`, `download_full`, `download_recent`)
- Test: `tests/downloader/test_walkers.py`

**Interfaces:**
- Consumes: `START_DATES`, `previous_month`, `months_forward`, `months_backward` (Task 1); `download_month`, `target_path`, `FetchResult`, `MAX_LOOKBACK` (Tasks 2–3).
- Produces (in `taxi_download.download`):
  - `@dataclass class WalkSummary: downloaded: int; gaveup: int`
  - `download_full(client, data_type, raw_dir, today, sleeper) -> WalkSummary` — forward-walk `START_DATES[data_type] .. previous_month(today)`. Skip existing (counts as data seen). On `NOTFOUND`: if data has already been seen, stop (end-of-series); else keep walking (pre-series gap). On `OK`: `downloaded += 1`. On give-up (`RATELIMIT`/`NETERROR`): `gaveup += 1`, continue.
  - `download_recent(client, data_type, raw_dir, n, today, sleeper) -> WalkSummary` — backward-walk from `previous_month(today)`. Stop when `downloaded == n`, when `MAX_LOOKBACK` months have been examined, or when an already-present local file is hit. On `NOTFOUND`: keep walking back. On give-up: `gaveup += 1`, continue.

- [ ] **Step 1: Write the failing test**

```python
# tests/downloader/test_walkers.py
"""Forward/backward walkers against the stub server. today + sleeper injected."""
from datetime import date

import httpx
import pytest

from taxi_download import download as dl
from taxi_download.download import (
    WalkSummary,
    download_full,
    download_recent,
    target_path,
)

NOOP = lambda _delay: None


@pytest.fixture
def client():
    with httpx.Client(follow_redirects=True, timeout=5.0) as c:
        yield c


def _point_at_stub(monkeypatch, stub):
    monkeypatch.setattr(dl, "BASE_URL", stub.base_url)


def _present(stub, data_type, *months):
    for (y, m) in months:
        stub.present.add(dl.filename(data_type, y, m))


def test_full_downloads_contiguous_run_then_stops_at_gap(client, stub, tmp_path, monkeypatch):
    _point_at_stub(monkeypatch, stub)
    # fhvhv starts 2019-02; serve 2019-02..2019-04, then a hole => end of series.
    _present(stub, "fhvhv", (2019, 2), (2019, 3), (2019, 4))
    summ = download_full(client, "fhvhv", tmp_path, today=date(2019, 7, 1), sleeper=NOOP)
    assert summ == WalkSummary(downloaded=3, gaveup=0)
    assert target_path(tmp_path, "fhvhv", 2019, 4).exists()
    assert not target_path(tmp_path, "fhvhv", 2019, 5).exists()


def test_full_skips_pre_series_404s_before_first_data(client, stub, tmp_path, monkeypatch):
    _point_at_stub(monkeypatch, stub)
    # Only 2019-05 exists; 2019-02..2019-04 all 404 (pre-series) must not stop the walk.
    _present(stub, "fhvhv", (2019, 5))
    summ = download_full(client, "fhvhv", tmp_path, today=date(2019, 7, 1), sleeper=NOOP)
    assert summ == WalkSummary(downloaded=1, gaveup=0)
    assert target_path(tmp_path, "fhvhv", 2019, 5).exists()


def test_full_skips_existing_local_files(client, stub, tmp_path, monkeypatch):
    _point_at_stub(monkeypatch, stub)
    _present(stub, "fhvhv", (2019, 2), (2019, 3))
    # 2019-02 already on disk => must be skipped, only 2019-03 counts as a new download.
    existing = target_path(tmp_path, "fhvhv", 2019, 2)
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"PAR1____PAR1")
    summ = download_full(client, "fhvhv", tmp_path, today=date(2019, 5, 1), sleeper=NOOP)
    assert summ == WalkSummary(downloaded=1, gaveup=0)


def test_recent_downloads_n_most_recent(client, stub, tmp_path, monkeypatch):
    _point_at_stub(monkeypatch, stub)
    # today 2025-07 => previous month 2025-06; serve the three months back.
    _present(stub, "yellow", (2025, 6), (2025, 5), (2025, 4))
    summ = download_recent(client, "yellow", tmp_path, n=3, today=date(2025, 7, 10), sleeper=NOOP)
    assert summ == WalkSummary(downloaded=3, gaveup=0)
    assert target_path(tmp_path, "yellow", 2025, 4).exists()
    assert not target_path(tmp_path, "yellow", 2025, 3).exists()


def test_recent_stops_early_on_existing_file(client, stub, tmp_path, monkeypatch):
    _point_at_stub(monkeypatch, stub)
    _present(stub, "yellow", (2025, 6), (2025, 5), (2025, 4))
    # 2025-05 already present => walk stops after downloading only 2025-06.
    existing = target_path(tmp_path, "yellow", 2025, 5)
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"PAR1____PAR1")
    summ = download_recent(client, "yellow", tmp_path, n=3, today=date(2025, 7, 10), sleeper=NOOP)
    assert summ == WalkSummary(downloaded=1, gaveup=0)


def test_recent_persistent_ratelimit_reports_giveup(client, stub, tmp_path, monkeypatch):
    _point_at_stub(monkeypatch, stub)
    stub.present.add(dl.filename("yellow", 2025, 6))
    stub.ratelimit[dl.filename("yellow", 2025, 6)] = 99  # never succeeds
    summ = download_recent(client, "yellow", tmp_path, n=1, today=date(2025, 7, 10), sleeper=NOOP)
    assert summ.downloaded == 0
    assert summ.gaveup >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/downloader/test_walkers.py -q`
Expected: FAIL — `ImportError: cannot import name 'WalkSummary'`.

- [ ] **Step 3: Implement `WalkSummary` and the two walkers**

Add the import and dataclass near the top of `download.py` (put `from dataclasses import dataclass` with the other stdlib imports), then append the walkers:

```python
@dataclass
class WalkSummary:
    downloaded: int
    gaveup: int


def download_full(client, data_type: str, raw_dir, today, sleeper) -> WalkSummary:
    end = previous_month(today)
    downloaded = gaveup = 0
    seen_data = False
    for (year, month) in months_forward(START_DATES[data_type], end):
        if target_path(raw_dir, data_type, year, month).exists():
            seen_data = True
            continue
        result = download_month(client, data_type, year, month, raw_dir, sleeper)
        if result is FetchResult.OK:
            downloaded += 1
            seen_data = True
        elif result is FetchResult.NOTFOUND:
            if seen_data:
                break  # end of series
            continue  # pre-series gap — keep walking forward
        else:
            gaveup += 1
    return WalkSummary(downloaded, gaveup)


def download_recent(client, data_type: str, raw_dir, n: int, today, sleeper) -> WalkSummary:
    downloaded = gaveup = examined = 0
    for (year, month) in months_backward(previous_month(today)):
        if downloaded >= n or examined >= MAX_LOOKBACK:
            break
        examined += 1
        if target_path(raw_dir, data_type, year, month).exists():
            break  # stop early on an already-present local file
        result = download_month(client, data_type, year, month, raw_dir, sleeper)
        if result is FetchResult.OK:
            downloaded += 1
        elif result is FetchResult.NOTFOUND:
            continue  # month not published — keep looking back
        else:
            gaveup += 1
    return WalkSummary(downloaded, gaveup)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/downloader/test_walkers.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add downloader/src/taxi_download/download.py tests/downloader/test_walkers.py
git commit -m "feat(downloader): full + recent walkers with WalkSummary"
```

---

## Task 5: `cli.py` — argparse, `main`, console script

The user-facing entry point: clean corrupt files, build an `httpx.Client`, run the right walker per type, print a summary, return the exit code. Registers the `taxi-download` console script.

**Files:**
- Create: `downloader/src/taxi_download/cli.py`
- Modify: `pyproject.toml` (`[project.scripts]` — add `taxi-download`)
- Test: `tests/downloader/test_cli.py`

**Interfaces:**
- Consumes: `DATA_TYPES`, `clean_corrupt`, `download_full`, `download_recent`, `WalkSummary` (Tasks 2–4).
- Produces (in `taxi_download.cli`):
  - `parse_args(argv=None) -> argparse.Namespace` — positional `data_type` (`nargs="?"`, `choices=DATA_TYPES`), `--recent` (`nargs="?"`, `type=int`, `const=3`, `default=None`), `--data-dir` (`default="."`).
  - `_today() -> datetime.date` — thin wrapper over `date.today()` so tests monkeypatch it.
  - `main(argv=None) -> int` — resolves `raw_dir = Path(args.data_dir) / "raw"`, runs `clean_corrupt`, opens one `httpx.Client(follow_redirects=True, timeout=...)`, runs `download_recent` (when `--recent` given) or `download_full` per selected type, prints per-type + total summary, and returns `2` if any requested type made zero progress while giving up on a rate limit, else `0`.

- [ ] **Step 1: Register the console script**

In `pyproject.toml`, `[project.scripts]` (keep alphabetized as it is now):

```toml
[project.scripts]
normalize = "taxi_normalize.cli:main"
schema-drift = "schema_drift.cli:main"
taxi-curate-mappings = "taxi_orchestrate.curate:main"
taxi-download = "taxi_download.cli:main"
taxi-load = "taxi_loader.cli:main"
taxi-run = "taxi_orchestrate.cli:main"
```

Run: `uv sync --extra test`
Expected: succeeds; `uv run taxi-download --help` exits 0 (after Step 4 lands `main`).

- [ ] **Step 2: Write the failing test**

```python
# tests/downloader/test_cli.py
"""CLI arg parsing + end-to-end main() against the stub server."""
from datetime import date

import pytest

from taxi_download import cli
from taxi_download import download as dl


def test_parse_defaults_all_types_no_recent():
    args = cli.parse_args([])
    assert args.data_type is None
    assert args.recent is None
    assert args.data_dir == "."


def test_parse_recent_bare_defaults_to_3():
    assert cli.parse_args(["--recent"]).recent == 3


def test_parse_recent_explicit_value():
    assert cli.parse_args(["--recent", "5"]).recent == 5


def test_parse_type_and_data_dir():
    args = cli.parse_args(["yellow", "--data-dir", "/tmp/x"])
    assert args.data_type == "yellow"
    assert args.data_dir == "/tmp/x"


def test_parse_rejects_unknown_type():
    with pytest.raises(SystemExit):
        cli.parse_args(["purple"])


def _freeze_today(monkeypatch, y, m, d):
    monkeypatch.setattr(cli, "_today", lambda: date(y, m, d))


def test_main_recent_downloads_and_returns_zero(stub, tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "BASE_URL", stub.base_url)
    _freeze_today(monkeypatch, 2025, 7, 10)
    stub.present.update({
        dl.filename("yellow", 2025, 6),
        dl.filename("yellow", 2025, 5),
    })
    rc = cli.main(["yellow", "--recent", "2", "--data-dir", str(tmp_path)])
    assert rc == 0
    assert dl.target_path(tmp_path / "raw", "yellow", 2025, 6).exists()
    assert dl.target_path(tmp_path / "raw", "yellow", 2025, 5).exists()


def test_main_cleans_corrupt_before_download(stub, tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "BASE_URL", stub.base_url)
    _freeze_today(monkeypatch, 2025, 7, 10)
    corrupt = dl.target_path(tmp_path / "raw", "yellow", 2025, 6)
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(b"<html>rate limited</html>\n")  # invalid PAR1
    stub.present.add(dl.filename("yellow", 2025, 6))
    rc = cli.main(["yellow", "--recent", "1", "--data-dir", str(tmp_path)])
    assert rc == 0
    assert dl.is_valid_parquet(corrupt)  # re-downloaded fresh & valid


def test_main_exit_2_when_ratelimit_blocks_all_progress(stub, tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "BASE_URL", stub.base_url)
    monkeypatch.setattr("time.sleep", lambda _s: None)  # no real backoff wait
    _freeze_today(monkeypatch, 2025, 7, 10)
    stub.present.add(dl.filename("yellow", 2025, 6))
    stub.ratelimit[dl.filename("yellow", 2025, 6)] = 99  # persistent 429
    rc = cli.main(["yellow", "--recent", "1", "--data-dir", str(tmp_path)])
    assert rc == 2
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run --extra test pytest tests/downloader/test_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_download.cli'`.

- [ ] **Step 4: Implement `cli.py`**

```python
# downloader/src/taxi_download/cli.py
"""Entry point for the `taxi-download` command (and `python -m taxi_download.cli`)."""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import httpx

from taxi_download.download import (
    DATA_TYPES,
    WalkSummary,
    clean_corrupt,
    download_full,
    download_recent,
)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="taxi-download",
        description="Download NYC TLC trip-data parquet files from CloudFront.",
    )
    p.add_argument("data_type", nargs="?", choices=DATA_TYPES,
                   help="one of the four types; omit to download all four")
    p.add_argument("--recent", nargs="?", type=int, const=3, default=None, metavar="N",
                   help="download the N most recent months (default 3 when N is omitted)")
    p.add_argument("--data-dir", default=".",
                   help="base directory; files land under <DIR>/raw (default: .)")
    return p.parse_args(argv)


def _today() -> date:
    return date.today()


def main(argv=None) -> int:
    args = parse_args(argv)
    raw_dir = Path(args.data_dir) / "raw"

    removed = clean_corrupt(raw_dir)
    if removed:
        print(f"cleaned {removed} corrupt parquet file(s)")

    types = [args.data_type] if args.data_type else list(DATA_TYPES)
    today = _today()
    total = 0
    exit_code = 0

    with httpx.Client(follow_redirects=True,
                      timeout=httpx.Timeout(120.0, connect=30.0)) as client:
        for t in types:
            if args.recent is not None:
                summ = download_recent(client, t, raw_dir, args.recent, today, time.sleep)
            else:
                summ = download_full(client, t, raw_dir, today, time.sleep)
            total += summ.downloaded
            print(f"{t}: downloaded {summ.downloaded}, gave up on {summ.gaveup}")
            if summ.downloaded == 0 and summ.gaveup > 0:
                exit_code = 2

    print(f"total downloaded: {total}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/downloader/test_cli.py -q`
Expected: PASS (8 tests).

- [ ] **Step 6: Sanity-check the console script and module invocation**

Run: `uv run taxi-download --help` and `uv run python -m taxi_download.cli --help`
Expected: both print the same usage and exit 0.

- [ ] **Step 7: Commit**

```bash
git add downloader/src/taxi_download/cli.py pyproject.toml uv.lock tests/downloader/test_cli.py
git commit -m "feat(downloader): taxi-download CLI + console script"
```

---

## Task 6: Orchestrator integration

Point the orchestrator's download stage at the new module, and fix the repo-root marker that referenced the going-away bash script.

**Files:**
- Modify: `orchestrator/src/taxi_orchestrate/stages.py` (`build_download_cmd`)
- Modify: `orchestrator/src/taxi_orchestrate/cli.py` (`find_repo_root` marker + the `build_download_cmd` call site)
- Test: `tests/taxi_orchestrate/test_stages.py` (update download-command assertions)

**Interfaces:**
- Produces: `build_download_cmd(data_type: Optional[str], recent: Optional[int], data_dir: Path) -> list[str]` — **drops the `repo_root` first parameter** (the module is importable; the command no longer references the repo). Emits `[sys.executable, "-m", "taxi_download.cli", *recent_group, *type, "--data-dir", str(data_dir)]`, where `recent_group` is `["--recent"]` plus `[str(recent)]` when `recent > 0`, and `type` is `[data_type]` when set.
- `find_repo_root(start: Path) -> Path` — marker becomes `pyproject.toml` **and** `normalize/mappings/` (both must exist), replacing the `downloader/download_taxi_data.sh` check.

- [ ] **Step 1: Update the failing tests first**

Replace the four download tests in `tests/taxi_orchestrate/test_stages.py` with these (note the new `build_download_cmd` signature — no `repo_root`):

```python
def test_download_full_all_types():
    cmd = build_download_cmd(None, None, Path("/data"))
    assert cmd[:3] == [sys.executable, "-m", "taxi_download.cli"]
    assert "--recent" not in cmd
    assert cmd[-2:] == ["--data-dir", "/data"]


def test_download_full_one_type():
    cmd = build_download_cmd("yellow", None, Path("/data"))
    assert cmd[:3] == [sys.executable, "-m", "taxi_download.cli"]
    assert "yellow" in cmd and "--recent" not in cmd
    assert cmd[-2:] == ["--data-dir", "/data"]


def test_download_recent_default_n():
    cmd = build_download_cmd("green", 0, Path("/data"))
    assert "--recent" in cmd
    assert cmd[cmd.index("--recent") + 1] == "green"  # no numeric N inserted
    assert cmd[-2:] == ["--data-dir", "/data"]


def test_download_recent_explicit_n():
    cmd = build_download_cmd("green", 3, Path("/data"))
    i = cmd.index("--recent")
    assert cmd[i + 1] == "3" and cmd[i + 2] == "green"
    assert cmd[-2:] == ["--data-dir", "/data"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/taxi_orchestrate/test_stages.py -q`
Expected: FAIL — `build_download_cmd()` still requires the old `repo_root` arg / still emits `bash`.

- [ ] **Step 3: Rewrite `build_download_cmd`**

In `orchestrator/src/taxi_orchestrate/stages.py`, replace the function:

```python
def build_download_cmd(data_type: Optional[str], recent: Optional[int],
                       data_dir: Path) -> list[str]:
    cmd = [sys.executable, "-m", "taxi_download.cli"]
    if recent is not None:
        cmd.append("--recent")
        if recent > 0:
            cmd.append(str(recent))
    if data_type:
        cmd.append(data_type)          # keep TYPE adjacent to the recent group
    cmd += ["--data-dir", str(data_dir)]
    return cmd
```

- [ ] **Step 4: Update the call site in `cli.py`**

In `orchestrator/src/taxi_orchestrate/cli.py`, the download branch (currently line ~111) drops the `repo_root` argument to `build_download_cmd` (the stage still runs with `cwd=repo_root`):

```python
            if stage == pipeline.DOWNLOAD:
                rc = stages.run(stages.build_download_cmd(t, args.recent, data_dir), repo_root)
```

- [ ] **Step 5: Update `find_repo_root` marker**

In `orchestrator/src/taxi_orchestrate/cli.py`, replace the marker check:

```python
def find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for d in [cur, *cur.parents]:
        if (d / "pyproject.toml").exists() and (d / "normalize" / "mappings").is_dir():
            return d
    return cur
```

- [ ] **Step 6: Run the orchestrator suite to verify it passes**

Run: `uv run --extra test pytest tests/taxi_orchestrate/ -q`
Expected: PASS.

- [ ] **Step 7: Confirm the dry-run shows the new command**

Run: `uv run taxi-run --dry-run --download-only`
Expected: the plan prints and the download stage would invoke `python -m taxi_download.cli` (no `bash`). (`--dry-run` prints the plan; if you want to eyeball the exact argv, temporarily add a `print` or trust the test assertions from Step 1.)

- [ ] **Step 8: Commit**

```bash
git add orchestrator/src/taxi_orchestrate/stages.py orchestrator/src/taxi_orchestrate/cli.py tests/taxi_orchestrate/test_stages.py
git commit -m "feat(orchestrator): drive download via python -m taxi_download.cli; repo-root marker -> pyproject + mappings"
```

---

## Task 7: Remove the bash downloader and refresh its README

Delete the superseded bash script and its bash-driven test, and repoint the `downloader/README.md` stub at the Python module. Then run the whole suite green.

**Files:**
- Delete: `downloader/download_taxi_data.sh`
- Delete: `tests/downloader/test_output_dir.py` (it drives the bash script; replaced by the new Python tests in Tasks 1–5)
- Modify: `downloader/README.md`

**Interfaces:** none (removal + docs only).

- [ ] **Step 1: Delete the bash script and its test**

```bash
git rm downloader/download_taxi_data.sh tests/downloader/test_output_dir.py
```

- [ ] **Step 2: Repoint the downloader README**

Replace `downloader/README.md` with:

```markdown
# downloader

`taxi_download` — a Python package that mirrors NYC TLC parquet trip data from
CloudFront to a local `raw/` directory. Rate-limit-aware backoff, PAR1
corrupt-file validation, end-of-series detection, incremental catch-up.

Run it as a console script or a module:

```bash
taxi-download                       # all four types, full history
taxi-download yellow --recent 3     # 3 most recent yellow months
python -m taxi_download.cli --data-dir /data yellow
```

→ **[Full guide](https://andrekamman.github.io/taxi-seed/guides/downloader/)**
```

- [ ] **Step 3: Verify no stale references to the bash script remain in code**

Run: `grep -rn "download_taxi_data.sh" --include=*.py .`
Expected: no output. (Docs under `docs/guides/downloader.md` are Piece 3's job — out of scope here — but there must be no `.py` references.)

- [ ] **Step 4: Run the full test suite**

Run: `uv run --extra test pytest -q`
Expected: PASS (no failures; the deleted `test_output_dir.py` is gone and the new `tests/downloader/` tests cover the module).

- [ ] **Step 5: Commit**

```bash
git add downloader/README.md
git commit -m "chore(downloader): remove bash script + bash test; README points at taxi_download"
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- CloudFront URL scheme, four types + start dates → Task 1 (`START_DATES`), Task 2 (`filename`/`url_for`). ✅
- Full-history forward walk, skip-existing, end-of-series vs pre-series 404 → Task 4 `download_full` + tests. ✅
- `--recent [N]` backward walk, count only successes, stop on existing, `MAX_LOOKBACK` cap → Task 4 `download_recent` + tests. ✅
- Output layout `<data-dir>/raw/<type>/<year>/<file>`, skip existing → Task 2 `target_path`, Task 3 `download_month` skip, Task 5 `raw_dir` resolution. ✅
- PAR1 validation: upfront cleanup pass + re-validate each download → Task 2 `clean_corrupt`, Task 3 `fetch_one` (200-but-bad-magic → RATELIMIT), Task 5 `main` calls `clean_corrupt`. ✅
- Rate-limit resilience incl. 200 HTML intercept → Task 3 `fetch_one`/`_classify_status` + backoff in `download_month`. ✅
- Package structure `dates.py`/`download.py`/`cli.py`, injectable `client`/`today`/`sleeper` → Tasks 1–5. ✅
- Constants, core function list, CLI args, exit codes (0 / 2) → Task 2 constants, Task 5 CLI + exit code. ✅
- Orchestrator `build_download_cmd`, `find_repo_root` marker, test update → Task 6. ✅
- Packaging: `httpx` dep, wheel packages entry, `taxi-download` script, `uv.lock` regen → Tasks 1, 2, 5. ✅
- Removals: bash script, `test_output_dir.py`, README → Task 7. ✅
- Testing strategy: pure units + stdlib stub server + DuckDB-built parquet + injected today/sleeper → Tasks 1–5. ✅

**Deviation from spec (flagged):** walkers return `WalkSummary(downloaded, gaveup)` rather than bare `int`, because the exit-code-2 rule needs the give-up signal. `WalkSummary.downloaded` is the spec's "count".

**Placeholder scan:** no TBD/TODO/"add error handling"/"similar to Task N" — every code and test step is complete. ✅

**Type consistency:** `FetchResult`, `WalkSummary`, `filename`/`url_for`/`target_path`, `download_month(..., sleeper)`, `download_full/recent(..., today, sleeper)`, and `build_download_cmd(data_type, recent, data_dir)` names/signatures match across the tasks that define and consume them. ✅
