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
