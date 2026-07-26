# tests/downloader/test_fetch.py
"""fetch_one + download_month against the stub server. No real network."""
import socket

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


def test_fetch_one_403_accessdenied_is_notfound(client, stub, tmp_path, monkeypatch):
    _point_at_stub(monkeypatch, stub)
    stub.force["yellow_tripdata_2025-06.parquet"] = (
        403,
        b"<Error><Code>AccessDenied</Code></Error>",
    )
    dest = tmp_path / "yellow_tripdata_2025-06.parquet"
    assert fetch_one(client, dl.url_for("yellow", 2025, 6), dest) is FetchResult.NOTFOUND
    assert not (dest.parent / (dest.name + ".part")).exists()


def test_fetch_one_403_nosuchkey_is_notfound(client, stub, tmp_path, monkeypatch):
    _point_at_stub(monkeypatch, stub)
    stub.force["yellow_tripdata_2025-06.parquet"] = (
        403,
        b"<Error><Code>NoSuchKey</Code></Error>",
    )
    dest = tmp_path / "yellow_tripdata_2025-06.parquet"
    assert fetch_one(client, dl.url_for("yellow", 2025, 6), dest) is FetchResult.NOTFOUND
    assert not (dest.parent / (dest.name + ".part")).exists()


def test_fetch_one_403_cloudfront_block_is_ratelimit(client, stub, tmp_path, monkeypatch):
    _point_at_stub(monkeypatch, stub)
    stub.force["yellow_tripdata_2025-06.parquet"] = (403, b"Request blocked")
    dest = tmp_path / "yellow_tripdata_2025-06.parquet"
    assert fetch_one(client, dl.url_for("yellow", 2025, 6), dest) is FetchResult.RATELIMIT
    assert not (dest.parent / (dest.name + ".part")).exists()


def test_fetch_one_5xx_is_ratelimit(client, stub, tmp_path, monkeypatch):
    _point_at_stub(monkeypatch, stub)
    stub.force["yellow_tripdata_2025-06.parquet"] = (500, b"boom")
    dest = tmp_path / "yellow_tripdata_2025-06.parquet"
    assert fetch_one(client, dl.url_for("yellow", 2025, 6), dest) is FetchResult.RATELIMIT
    assert not (dest.parent / (dest.name + ".part")).exists()


def test_fetch_one_transport_error_is_neterror(client, tmp_path):
    # Grab a free port, then close it so nothing is listening -> ConnectError.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()
    url = f"http://127.0.0.1:{dead_port}/yellow_tripdata_2025-06.parquet"
    dest = tmp_path / "yellow_tripdata_2025-06.parquet"
    assert fetch_one(client, url, dest) is FetchResult.NETERROR
    assert not (dest.parent / (dest.name + ".part")).exists()


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
