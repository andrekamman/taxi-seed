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
