"""Progress formatting and the heartbeat that drives it.

Nothing here waits on wall time: every collaborator that would -- the clock, the
sleeper, the sampler -- is injected, which is the reason progress.py splits pure
formatting from the thread in the first place.
"""
import threading

import pytest

from taxi_mssql import progress
from taxi_mssql.config import from_env


def settings_for(tmp_path, **env):
    return from_env({"MSSQL_PASSWORD": "pw", "TAXI_DATA_DIR": str(tmp_path), **env})


def _normalized(tmp_path, *names, year="2019", data_type="fhvhv"):
    d = tmp_path / "raw-normalized" / data_type / year
    d.mkdir(parents=True, exist_ok=True)
    for name in names:
        (d / name).touch()
    return d


def test_utc_hms_formats_epoch_as_utc_clock_time():
    # 2026-08-15T07:13:42Z
    assert progress.utc_hms(1786778022.0) == "07:13:42"


def test_emit_prefixes_a_utc_timestamp():
    written = []
    progress.emit("load fhvhv: starting", now="07:13:42", out=written.append)
    assert written == ["[07:13:42] load fhvhv: starting"]


@pytest.mark.parametrize("n,expected", [
    (0, "0"), (495, "495"), (34_622_078, "34.6M"),
    (1_607_275_004, "1.61B"), (83_295, "83.3K"),
    # Three significant digits, trailing zeros kept: a heartbeat whose rate
    # alternates between "2M" and "2.06M" reads as though the rate collapsed.
    (10_000_000, "10.0M"), (2_000_000, "2.00M"), (100_000_000, "100M"),
])
def test_humanize_count(n, expected):
    assert progress.humanize_count(n) == expected


@pytest.mark.parametrize("seconds,expected", [
    (5, "5s"), (90, "1m30s"), (764 * 60, "12h44m"), (0, "0s"),
    # Zero-padded below the leading unit, so consecutive lines stay aligned.
    (60, "1m00s"), (1020, "17m00s"), (3600, "1h00m"),
])
def test_humanize_duration(seconds, expected):
    assert progress.humanize_duration(seconds) == expected


def test_rows_per_min_is_none_without_elapsed_time():
    assert progress.rows_per_min(1000, 0) is None


def test_rows_per_min_scales_to_a_minute():
    assert progress.rows_per_min(4_114_432, 120.0) == pytest.approx(2_057_216)


def test_eta_seconds_is_none_without_a_rate():
    assert progress.eta_seconds(1000, None) is None
    assert progress.eta_seconds(1000, 0) is None


def test_eta_seconds_divides_remaining_by_rate():
    assert progress.eta_seconds(2_000_000, 1_000_000) == pytest.approx(120.0)


def test_format_progress_with_everything_known():
    line = progress.format_progress(
        "load fhvhv", rows=34_622_078, total=1_607_275_004,
        per_min=2_057_216, elapsed_s=1020,
    )
    assert line == (
        "load fhvhv: 34.6M/1.61B rows (2.2%), 2.06M rows/min, "
        "elapsed 17m00s, eta 12h44m"
    )


def test_format_progress_without_a_total_omits_percent_and_eta():
    line = progress.format_progress(
        "load fhvhv", rows=34_622_078, total=None,
        per_min=2_057_216, elapsed_s=1020,
    )
    assert line == "load fhvhv: 34.6M rows, 2.06M rows/min, elapsed 17m00s"


def test_format_progress_survives_an_unknown_sample():
    line = progress.format_progress(
        "load fhvhv", rows=None, total=None, per_min=None, elapsed_s=30,
    )
    assert line == "load fhvhv: rows unavailable, elapsed 30s"


def test_format_progress_counts_a_caller_supplied_unit():
    """Mirror has no row count to sample -- it counts normalized files."""
    line = progress.format_progress(
        "mirror yellow", rows=37, total=None, per_min=12, elapsed_s=60,
        unit="files",
    )
    assert line == "mirror yellow: 37 files, 12 files/min, elapsed 1m00s"


def test_format_progress_names_the_unit_when_the_sample_is_unknown():
    line = progress.format_progress(
        "mirror yellow", rows=None, total=None, per_min=None, elapsed_s=30,
        unit="files",
    )
    assert line == "mirror yellow: files unavailable, elapsed 30s"


def test_expected_rows_sums_the_footers(tmp_path):
    _normalized(tmp_path, "fhvhv_tripdata_2019-02.parquet",
                "fhvhv_tripdata_2019-03.parquet")
    seen = {}

    def reader(paths):
        seen["paths"] = sorted(paths)
        return 234_630_264

    assert progress.expected_rows(
        settings_for(tmp_path), "fhvhv", reader=reader) == 234_630_264
    assert len(seen["paths"]) == 2


def test_expected_rows_is_none_when_the_tree_is_absent(tmp_path):
    assert progress.expected_rows(
        settings_for(tmp_path), "fhvhv", reader=lambda p: 1) is None


def test_expected_rows_is_none_when_the_tree_is_empty(tmp_path):
    (tmp_path / "raw-normalized" / "fhvhv").mkdir(parents=True)
    assert progress.expected_rows(
        settings_for(tmp_path), "fhvhv", reader=lambda p: 1) is None


def test_expected_rows_is_none_when_the_reader_fails(tmp_path):
    """No duckdb failure justifies aborting a 13-hour load."""
    _normalized(tmp_path, "fhvhv_tripdata_2019-02.parquet")

    def reader(paths):
        raise RuntimeError("duckdb exploded")

    assert progress.expected_rows(settings_for(tmp_path), "fhvhv",
                                  reader=reader) is None


def test_duckdb_footer_rows_reads_real_parquet(tmp_path):
    """Guards the default reader against a duckdb API change: the plan's
    parameterised table-function call is the part that could quietly break."""
    import duckdb

    con = duckdb.connect()
    a, b = tmp_path / "a.parquet", tmp_path / "b.parquet"
    con.execute(f"COPY (SELECT range FROM range(1000)) TO '{a}' (FORMAT PARQUET)")
    con.execute(f"COPY (SELECT range FROM range(234)) TO '{b}' (FORMAT PARQUET)")
    con.close()

    assert progress._duckdb_footer_rows([str(a), str(b)]) == 1234


def test_normalized_files_counts_the_whole_tree(tmp_path):
    _normalized(tmp_path, "fhvhv_tripdata_2019-02.parquet",
                "fhvhv_tripdata_2019-03.parquet")
    _normalized(tmp_path, "fhvhv_tripdata_2020-01.parquet", year="2020")
    assert progress.normalized_files(settings_for(tmp_path), "fhvhv") == 3


def test_normalized_files_is_zero_before_the_tree_exists(tmp_path):
    """A mirror that has not written its first file yet is at zero, not
    unknown -- an absent directory is an observation, not a failure to observe."""
    assert progress.normalized_files(settings_for(tmp_path), "fhvhv") == 0


def test_normalized_files_ignores_other_trip_types(tmp_path):
    _normalized(tmp_path, "yellow_tripdata_2019-02.parquet", data_type="yellow")
    assert progress.normalized_files(settings_for(tmp_path), "fhvhv") == 0


def test_heartbeat_emits_nothing_when_disabled():
    written = []
    with progress.Heartbeat("load fhvhv", lambda: 1, interval_s=0,
                            out=written.append):
        pass
    assert written == []


def test_heartbeat_starts_no_thread_when_disabled():
    before = threading.active_count()
    with progress.Heartbeat("load fhvhv", lambda: 1, interval_s=0,
                            out=lambda m: None):
        assert threading.active_count() == before


def test_heartbeat_emits_a_line_per_tick_with_a_rate():
    written = []
    samples = iter([10_000_000, 12_000_000, 14_000_000])
    ticks = iter([0.0, 60.0, 120.0, 180.0])

    with progress.Heartbeat(
        "load fhvhv", lambda: next(samples), total=100_000_000,
        interval_s=60, out=written.append, clock=lambda: next(ticks),
        stamp=lambda: "07:13:42", sleeper=lambda s: None, max_ticks=3,
    ):
        pass

    assert len(written) == 3
    assert written[0].startswith("[07:13:42] load fhvhv: 10.0M/100M rows (10.0%)")
    # The first tick has no previous sample, so it cannot report a rate.
    assert "rows/min" not in written[0]
    assert "2.00M rows/min" in written[1]
    assert written[1].endswith("elapsed 2m00s, eta 44m00s")


def test_heartbeat_passes_its_unit_through():
    written = []
    with progress.Heartbeat(
        "mirror yellow", lambda: 37, interval_s=60, unit="files",
        out=written.append, clock=iter([0.0, 60.0]).__next__,
        stamp=lambda: "07:13:42", sleeper=lambda s: None, max_ticks=1,
    ):
        pass
    assert written == ["[07:13:42] mirror yellow: 37 files, elapsed 1m00s"]


def test_heartbeat_reports_unavailable_when_sampling_raises():
    written = []

    def sample():
        raise RuntimeError("sqlcmd died")

    with progress.Heartbeat(
        "load fhvhv", sample, interval_s=60, out=written.append,
        clock=iter([0.0, 60.0]).__next__, stamp=lambda: "07:13:42",
        sleeper=lambda s: None, max_ticks=1,
    ):
        pass

    assert "rows unavailable" in written[0]


def test_heartbeat_emits_nothing_when_the_stage_ends_during_an_interval():
    """A stage that finishes 3 seconds into a 60-second interval must not have
    a stale line appended after its "done" announcement."""
    written = []
    with progress.Heartbeat(
        "load fhvhv", lambda: 1, interval_s=60, out=written.append,
        stamp=lambda: "07:13:42", sleeper=lambda s: True,
    ):
        pass
    assert written == []


def test_heartbeat_thread_is_joined_on_exit():
    before = threading.active_count()
    with progress.Heartbeat("load fhvhv", lambda: 1, interval_s=60,
                            out=lambda m: None, sleeper=lambda s: None,
                            max_ticks=1, stamp=lambda: "07:13:42"):
        pass
    assert threading.active_count() == before


def test_heartbeat_does_not_swallow_the_body_exception():
    with pytest.raises(ValueError):
        with progress.Heartbeat("load fhvhv", lambda: 1, interval_s=0,
                                out=lambda m: None):
            raise ValueError("the stage blew up")


def test_expected_rows_total_sums_every_trip_type(tmp_path):
    _normalized(tmp_path, "fhvhv_tripdata_2019-02.parquet")
    _normalized(tmp_path, "green_tripdata_2025-01.parquet", data_type="green",
                year="2025")
    s = settings_for(tmp_path, TAXI_TYPES="green,fhvhv")
    assert progress.expected_rows_total(s, s.types, reader=lambda p: 50) == 100


def test_expected_rows_total_is_none_when_a_type_is_unknown(tmp_path):
    _normalized(tmp_path, "fhvhv_tripdata_2019-02.parquet")
    s = settings_for(tmp_path, TAXI_TYPES="green,fhvhv")
    # green has nothing normalized yet, so the total is not knowable.
    assert progress.expected_rows_total(s, s.types, reader=lambda p: 50) is None


def test_expected_rows_total_of_no_types(tmp_path):
    assert progress.expected_rows_total(settings_for(tmp_path), ()) is None
