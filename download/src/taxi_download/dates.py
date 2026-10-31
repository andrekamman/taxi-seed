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
