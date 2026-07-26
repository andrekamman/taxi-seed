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
