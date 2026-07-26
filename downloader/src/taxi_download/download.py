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
