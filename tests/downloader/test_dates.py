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
