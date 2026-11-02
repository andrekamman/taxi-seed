from taxi_mssql import report
from taxi_mssql.config import from_env


def settings_for(tmp_path, **env):
    return from_env({"MSSQL_PASSWORD": "pw", "TAXI_DATA_DIR": str(tmp_path), **env})


def test_report_counts_parquet_per_stage(tmp_path):
    s = settings_for(tmp_path, TAXI_TYPES="yellow")
    raw = tmp_path / "raw" / "yellow" / "2015"
    raw.mkdir(parents=True)
    (raw / "yellow_2015-01.parquet").write_bytes(b"x")
    (raw / "yellow_2015-02.parquet").write_bytes(b"x")
    norm = tmp_path / "raw-normalized" / "yellow" / "2015"
    norm.mkdir(parents=True)
    (norm / "yellow_2015-01.parquet").write_bytes(b"x")

    out = report.render(s)

    assert "yellow" in out
    assert "2 raw" in out
    assert "1 normalized" in out


def test_report_names_the_backup_when_present(tmp_path):
    s = settings_for(tmp_path)
    d = tmp_path / "mssql" / "backup" / "taxi-sql" / "taxi" / "FULL"
    d.mkdir(parents=True)
    bak = d / "taxi-sql_taxi_FULL_20260801_120000.bak"
    bak.write_bytes(b"\0" * 2048)

    out = report.render(s, backup_path=bak)

    assert "taxi-sql_taxi_FULL_20260801_120000.bak" in out


def test_report_says_so_when_there_is_no_backup(tmp_path):
    assert "none" in report.render(settings_for(tmp_path)).lower()


def test_report_shows_loaded_rows_per_type(tmp_path):
    s = settings_for(tmp_path, TAXI_TYPES="yellow,green")
    out = report.render(s, rows_by_type={"yellow": 1234567, "green": 890})
    assert "1,234,567 row" in out
    assert "890 row" in out


def test_report_tolerates_unknown_row_counts(tmp_path):
    out = report.render(settings_for(tmp_path, TAXI_TYPES="yellow"))
    assert "yellow" in out
    assert "row" not in out.split("yellow")[1].split("\n")[0]


def test_loaded_rows_parses_the_manifest_grouping(tmp_path):
    import subprocess

    def runner(cmd, env, stdin_text):
        return subprocess.CompletedProcess(cmd, 0, "yellow 1234567\ngreen 890\n", "")

    assert report.loaded_rows(settings_for(tmp_path), runner=runner) == {
        "yellow": 1234567, "green": 890,
    }


def test_loaded_rows_is_empty_when_the_manifest_is_missing(tmp_path):
    import subprocess

    def failing(cmd, env, stdin_text):
        return subprocess.CompletedProcess(cmd, 1, "", "Invalid object name")

    assert report.loaded_rows(settings_for(tmp_path), runner=failing) == {}


def test_table_rows_parses_the_scalar(tmp_path):
    import subprocess

    def runner(cmd, env, stdin_text):
        return subprocess.CompletedProcess(cmd, 0, "28400254\n", "")

    assert report.table_rows(settings_for(tmp_path), "fhvhv", runner=runner) == 28400254


def test_table_rows_returns_none_when_sampling_fails(tmp_path):
    """The heartbeat thread calls this. A dropped connection must cost a
    progress line, not the load."""
    import subprocess

    def failing(cmd, env, stdin_text):
        return subprocess.CompletedProcess(cmd, 1, "", "connection reset")

    assert report.table_rows(settings_for(tmp_path), "fhvhv", runner=failing) is None


def test_table_rows_returns_none_for_a_non_numeric_answer(tmp_path):
    import subprocess

    def runner(cmd, env, stdin_text):
        return subprocess.CompletedProcess(cmd, 0, "NULL\n", "")

    assert report.table_rows(settings_for(tmp_path), "fhvhv", runner=runner) is None


def test_table_rows_total_sums_every_trip_type(tmp_path):
    import subprocess

    def runner(cmd, env, stdin_text):
        return subprocess.CompletedProcess(cmd, 0, "100\n")

    s = settings_for(tmp_path, TAXI_TYPES="yellow,green")
    assert report.table_rows_total(s, s.types, runner=runner) == 200


def test_table_rows_total_is_none_when_any_part_is_unknown(tmp_path):
    """The heartbeat's rule: a partial sum would be a wrong number, not a
    smaller one."""
    import subprocess

    seen = []

    def runner(cmd, env, stdin_text):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, len(seen) - 1, "100\n")

    s = settings_for(tmp_path, TAXI_TYPES="yellow,green")
    assert report.table_rows_total(s, s.types, runner=runner) is None


def test_table_rows_total_of_no_types(tmp_path):
    assert report.table_rows_total(settings_for(tmp_path), (), runner=None) is None
