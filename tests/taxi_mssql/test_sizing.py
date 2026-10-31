from pathlib import Path

import pytest

from taxi_mssql.sizing import (
    data_size_mb,
    fits_on_disk,
    log_size_mb,
    max_server_memory_mb,
    needs_growth_warning,
    parquet_bytes,
    required_disk_mb,
)

GB = 1024 ** 3
MB = 1024 ** 2


def _write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)


def test_parquet_bytes_sums_only_selected_types(tmp_path):
    _write(tmp_path / "yellow" / "2015" / "a.parquet", 100)
    _write(tmp_path / "yellow" / "2016" / "b.parquet", 50)
    _write(tmp_path / "green" / "2015" / "c.parquet", 999)
    assert parquet_bytes(tmp_path, ["yellow"]) == 150


def test_parquet_bytes_ignores_non_parquet(tmp_path):
    _write(tmp_path / "yellow" / "a.parquet", 100)
    _write(tmp_path / "yellow" / "notes.txt", 500)
    assert parquet_bytes(tmp_path, ["yellow"]) == 100


def test_parquet_bytes_tolerates_missing_type_dir(tmp_path):
    assert parquet_bytes(tmp_path, ["yellow", "fhvhv"]) == 0


def test_data_size_rounds_up_to_next_10gb_at_or_above_the_switchover():
    # 4 GB of parquet x 3.0 = 12 GB -> 20 GB (>= 10 GB implied: 10 GB granularity)
    assert data_size_mb(4 * GB, 3.0) == 20 * 1024


def test_data_size_exact_multiple_is_not_inflated():
    # 10 GB x 3.0 = 30 GB, already a multiple of 10
    assert data_size_mb(10 * GB, 3.0) == 30 * 1024


def test_data_size_boundary_at_exactly_10gb_uses_10gb_granularity():
    # raw implied size is exactly 10 GB -- not "under 10", so still the 10 GB step
    assert data_size_mb(10 * GB, 1.0) == 10 * 1024


def test_data_size_of_a_small_smoke_run_rounds_to_1gb_not_10gb():
    # 128 MB of parquet x 3.0 ~= 384 MB -- under the 10 GB switchover, so it
    # rounds up to the next 1 GB instead of the old flat 10 GB floor.
    assert data_size_mb(128 * MB, 3.0) == 1 * 1024


def test_data_size_below_10gb_rounds_up_within_1gb_granularity():
    # 2.1 GB implied -> rounds up to 3 GB, not down to 2 or up to 10
    assert data_size_mb(int(2.1 * GB), 1.0) == 3 * 1024


def test_data_size_has_a_1gb_floor():
    assert data_size_mb(1, 3.0) == 1 * 1024


def test_data_size_of_empty_input_still_floors_at_1gb_not_0():
    assert data_size_mb(0, 3.0) == 1 * 1024


def test_log_size_explicit_is_honoured_verbatim():
    assert log_size_mb(data_mb=999_999, log_size_gb=16) == 16 * 1024


def test_log_size_default_scales_to_a_quarter_of_the_data_size():
    assert log_size_mb(data_mb=20 * 1024, log_size_gb=None) == 5 * 1024


def test_log_size_default_caps_at_16gb_for_a_full_history_run():
    # 300 GB data (the full-history ballpark) -> 75 GB implied, capped at 16 GB,
    # exactly matching what the old flat default gave every run.
    assert log_size_mb(data_mb=300 * 1024, log_size_gb=None) == 16 * 1024


def test_log_size_default_floors_at_1gb_for_a_tiny_database():
    assert log_size_mb(data_mb=1 * 1024, log_size_gb=None) == 1 * 1024


def test_log_size_default_of_zero_data_still_floors_at_1gb():
    assert log_size_mb(data_mb=0, log_size_gb=None) == 1 * 1024


def test_growth_warning_fires_above_threshold():
    assert needs_growth_warning(implied_mb=1300, allocated_mb=1000) is True


def test_growth_warning_silent_at_threshold():
    assert needs_growth_warning(implied_mb=1250, allocated_mb=1000) is False


def test_growth_warning_silent_when_allocated_is_larger():
    assert needs_growth_warning(implied_mb=500, allocated_mb=1000) is False


def test_growth_warning_silent_when_allocation_unknown():
    assert needs_growth_warning(implied_mb=5000, allocated_mb=0) is False


def test_required_disk_mb_sums_data_and_log_plus_headroom():
    assert required_disk_mb(20 * 1024, 5 * 1024, headroom_mb=1024) == 26 * 1024


def test_required_disk_mb_default_headroom_is_nonzero():
    # The default headroom must itself be applied by default -- a zero default
    # would make callers who forget to pass it get an exact-fit check.
    assert required_disk_mb(0, 0) > 0


def test_fits_on_disk_true_when_ample():
    assert fits_on_disk(required_mb=1000, free_mb=2000) is True


def test_fits_on_disk_false_when_short():
    assert fits_on_disk(required_mb=2000, free_mb=1000) is False


def test_fits_on_disk_true_at_exact_boundary():
    assert fits_on_disk(required_mb=1000, free_mb=1000) is True


@pytest.mark.parametrize("host_mb,expected", [
    (32076, 24057),   # the 32 GB devbox -> ~24 GB, reserve 25%
    (16384, 12288),   # 16 GB -> reserve the 4 GB floor
    (8192, 4096),     # 8 GB  -> reserve the 4 GB floor
    (65536, 49152),   # 64 GB -> reserve 25%
    (2048, 1024),     # tiny  -> clamped to the 1 GB floor
])
def test_max_server_memory_mb(host_mb, expected):
    assert max_server_memory_mb(host_mb) == expected


def test_max_server_memory_mb_rejects_nonsense():
    with pytest.raises(ValueError):
        max_server_memory_mb(0)
