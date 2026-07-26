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
