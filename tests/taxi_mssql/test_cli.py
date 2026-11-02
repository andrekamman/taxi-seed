import pytest

from taxi_mssql import (backup, cli, mssql, ola, preflight, provision, report, stages)


@pytest.fixture(autouse=True)
def no_memory_cap(monkeypatch):
    """The memory cap shells out to sqlcmd, so every pipeline test would try to
    run it for real. The test that exercises it overrides this."""
    monkeypatch.setattr(provision, "apply_memory_cap", lambda s, **kw: None)


def test_help_exits_0_and_prints_usage_without_touching_pipeline(monkeypatch, capsys):
    monkeypatch.setattr(preflight, "run", _never_called("preflight.run"))

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])

    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "usage: taxi-mssql" in out
    assert "TAXI_DATA_DIR" in out
    assert "MSSQL_PASSWORD" in out


def test_unknown_flag_exits_2_without_touching_pipeline(monkeypatch, capsys):
    monkeypatch.setattr(preflight, "run", _never_called("preflight.run"))

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--bogus"])

    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "usage: taxi-mssql" in err


def test_help_documents_every_variable_the_config_reads(capsys):
    """An undocumented variable that silently changes what gets created is a
    trap; MSSQL_DATABASE in particular decides what the run builds."""
    with pytest.raises(SystemExit):
        cli.main(["--help"])

    out = capsys.readouterr().out
    for var in ("TAXI_DATA_DIR", "MSSQL_PASSWORD", "TAXI_TYPES", "TAXI_RECENT",
                "TAXI_DATA_FACTOR", "MSSQL_DATA_SIZE_GB", "MSSQL_LOG_SIZE_GB",
                "MSSQL_FILEGROWTH_MB", "MSSQL_HOST", "MSSQL_PORT",
                "MSSQL_DATABASE", "MSSQL_SCHEMA", "MSSQL_USER",
                "TAXI_PROGRESS_INTERVAL_S", "MSSQL_MAX_MEMORY_MB",):
        assert var in out


def test_worst_prefers_failure():
    assert cli.worst([0, 1, 2]) == 2


def test_worst_prefers_needs_review_over_clean():
    assert cli.worst([0, 1, 0]) == 1


def test_worst_of_nothing_is_clean():
    assert cli.worst([]) == 0


def test_load_exit_1_is_an_operational_failure_not_needs_review():
    """taxi-load's 1 is 'failed mid-load' -- taxi-seed's own orchestrator calls
    that PARTIAL and maps it to 2. Only normalize's drift halt is needs-review."""
    assert cli.load_exit_code(1) == 2


def test_load_exit_codes_are_otherwise_passed_through():
    assert cli.load_exit_code(0) == 0
    assert cli.load_exit_code(2) == 2


def test_missing_password_is_an_operational_failure(monkeypatch, capsys):
    monkeypatch.delenv("MSSQL_PASSWORD", raising=False)
    assert cli.main([]) == 2
    assert "MSSQL_PASSWORD" in capsys.readouterr().err


def _base_env(monkeypatch, tmp_path, types):
    monkeypatch.setenv("MSSQL_PASSWORD", "pw")
    monkeypatch.setenv("TAXI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TAXI_TYPES", types)


def _never_called(name):
    def fake(*args, **kwargs):
        raise AssertionError(f"{name} should not have been called")
    return fake


def test_main_runs_the_seven_steps_in_order(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path, "yellow,green")
    calls: list = []

    def fake_preflight(settings, **kw):
        calls.append("preflight")
    monkeypatch.setattr(preflight, "run", fake_preflight)

    def fake_mirror(settings, data_type, **kw):
        calls.append(("mirror", data_type))
        return 0
    monkeypatch.setattr(stages, "mirror", fake_mirror)

    def fake_provision(settings, **kw):
        calls.append("provision")
        return True
    monkeypatch.setattr(provision, "run", fake_provision)

    def fake_ola(settings, **kw):
        calls.append("ola")
        return True
    monkeypatch.setattr(ola, "run", fake_ola)

    monkeypatch.setattr(cli.progress, "expected_rows_total", lambda s, ty, **kw: None)

    def fake_load(settings, data_type, **kw):
        calls.append(("load", data_type))
        return 0
    monkeypatch.setattr(stages, "load", fake_load)

    def fake_backup(settings, **kw):
        calls.append("backup")
        return True
    monkeypatch.setattr(backup, "run", fake_backup)

    monkeypatch.setattr(backup, "newest_backup", lambda settings: None)
    monkeypatch.setattr(report, "loaded_rows", lambda settings, **kw: {})
    monkeypatch.setattr(report, "render", lambda settings, **kw: "report")

    assert cli.main([]) == 0
    assert calls == [
        "preflight",
        ("mirror", "yellow"), ("mirror", "green"),
        "provision",
        "ola",
        # One taxi-load per trip type, in the configured order.
        ("load", "yellow"), ("load", "green"),
        "backup",
    ]


def test_main_stops_after_mirror_halts_and_labels_it_needs_review(
    monkeypatch, tmp_path, capsys
):
    _base_env(monkeypatch, tmp_path, "yellow,green,fhv")
    calls: list = []

    monkeypatch.setattr(preflight, "run", lambda settings, **kw: calls.append("preflight"))

    def fake_mirror(settings, data_type, **kw):
        calls.append(("mirror", data_type))
        return 1 if data_type == "green" else 0
    monkeypatch.setattr(stages, "mirror", fake_mirror)

    monkeypatch.setattr(provision, "run", _never_called("provision.run"))
    monkeypatch.setattr(ola, "run", _never_called("ola.run"))
    monkeypatch.setattr(stages, "load", _never_called("stages.load"))
    monkeypatch.setattr(backup, "run", _never_called("backup.run"))

    assert cli.main([]) == 1
    # fhv is never reached: the loop stops at the first non-clean return.
    assert calls == ["preflight", ("mirror", "yellow"), ("mirror", "green")]
    err = capsys.readouterr().err
    assert "needs-review:" in err
    assert "error:" not in err


def test_main_stops_after_load_fails_and_never_backs_up(monkeypatch, tmp_path, capsys):
    _base_env(monkeypatch, tmp_path, "yellow,green")
    calls: list = []

    monkeypatch.setattr(preflight, "run", lambda settings, **kw: calls.append("preflight"))

    def fake_mirror(settings, data_type, **kw):
        calls.append(("mirror", data_type))
        return 0
    monkeypatch.setattr(stages, "mirror", fake_mirror)

    def fake_provision(settings, **kw):
        calls.append("provision")
        return True
    monkeypatch.setattr(provision, "run", fake_provision)

    def fake_ola(settings, **kw):
        calls.append("ola")
        return True
    monkeypatch.setattr(ola, "run", fake_ola)

    monkeypatch.setattr(cli.progress, "expected_rows_total", lambda s, ty, **kw: None)

    def fake_load(settings, data_type, **kw):
        calls.append("load")
        # A failing type does not abandon the others -- every type is attempted.
        return 2 if data_type == "yellow" else 0
    monkeypatch.setattr(stages, "load", fake_load)

    monkeypatch.setattr(backup, "run", _never_called("backup.run"))

    assert cli.main([]) == 2
    assert calls == [
        "preflight", ("mirror", "yellow"), ("mirror", "green"),
        "provision", "ola",
        # Both types attempted even though yellow failed.
        "load", "load",
    ]
    err = capsys.readouterr().err
    assert "error:" in err
    assert "skipping backup" in err


def test_main_maps_a_mid_load_failure_to_exit_2(monkeypatch, tmp_path, capsys):
    """A connection blip mid-load must not be reported as blocked-on-a-human:
    exit 1 would tell CI a schema-drift decision is pending when a re-run is the
    remedy."""
    _base_env(monkeypatch, tmp_path, "yellow,green")

    monkeypatch.setattr(preflight, "run", lambda settings, **kw: None)
    monkeypatch.setattr(stages, "mirror", lambda settings, data_type, **kw: 0)
    monkeypatch.setattr(provision, "run", lambda settings, **kw: True)
    monkeypatch.setattr(ola, "run", lambda settings, **kw: True)
    monkeypatch.setattr(cli.progress, "expected_rows_total", lambda s, ty, **kw: None)
    # taxi-load exit 1 = "failed mid-load", which this project maps to 2.
    monkeypatch.setattr(stages, "load",
                        lambda settings, data_type, **kw: 1 if data_type == "yellow" else 0)
    monkeypatch.setattr(backup, "run", _never_called("backup.run"))

    assert cli.main([]) == 2
    err = capsys.readouterr().err
    assert "error:" in err
    assert "needs-review" not in err
    assert "exited 1" in err  # the child's own code is still reported


def test_main_maps_missing_ola_script_to_exit_2(monkeypatch, tmp_path, capsys):
    _base_env(monkeypatch, tmp_path, "yellow")

    monkeypatch.setattr(preflight, "run", lambda settings, **kw: None)
    monkeypatch.setattr(stages, "mirror", lambda settings, data_type, **kw: 0)
    monkeypatch.setattr(provision, "run", lambda settings, **kw: True)

    def fake_ola(settings, **kw):
        raise ola.OlaScriptMissingError("vendored MaintenanceSolution.sql not found")
    monkeypatch.setattr(ola, "run", fake_ola)

    monkeypatch.setattr(stages, "load", _never_called("stages.load"))
    monkeypatch.setattr(backup, "run", _never_called("backup.run"))

    assert cli.main([]) == 2
    err = capsys.readouterr().err
    assert "vendored MaintenanceSolution.sql not found" in err


def test_main_maps_a_missing_taxi_seed_cli_to_exit_2(monkeypatch, tmp_path, capsys):
    _base_env(monkeypatch, tmp_path, "yellow")

    monkeypatch.setattr(preflight, "run", lambda settings, **kw: None)

    def fake_mirror(settings, data_type, **kw):
        raise stages.StageCommandNotFoundError("'taxi-pipeline' is not on PATH.")
    monkeypatch.setattr(stages, "mirror", fake_mirror)

    monkeypatch.setattr(provision, "run", _never_called("provision.run"))
    monkeypatch.setattr(ola, "run", _never_called("ola.run"))
    monkeypatch.setattr(backup, "run", _never_called("backup.run"))

    assert cli.main([]) == 2
    assert "not on PATH" in capsys.readouterr().err


def test_main_maps_mssql_error_to_exit_2(monkeypatch, tmp_path, capsys):
    _base_env(monkeypatch, tmp_path, "yellow")

    monkeypatch.setattr(preflight, "run", lambda settings, **kw: None)
    monkeypatch.setattr(stages, "mirror", lambda settings, data_type, **kw: 0)

    def fake_provision(settings, **kw):
        raise mssql.MssqlError("sqlcmd failed (exit 1): connection refused")
    monkeypatch.setattr(provision, "run", fake_provision)

    monkeypatch.setattr(ola, "run", _never_called("ola.run"))
    monkeypatch.setattr(stages, "load", _never_called("stages.load"))
    monkeypatch.setattr(backup, "run", _never_called("backup.run"))

    assert cli.main([]) == 2
    err = capsys.readouterr().err
    assert "connection refused" in err


class _FakeHeartbeat:
    """Records how cli.main wired each stage up, and runs no thread."""

    calls: list = []

    def __init__(self, label, sample, **kwargs):
        self.label = label
        self.sample = sample
        self.kwargs = kwargs
        _FakeHeartbeat.calls.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def recorded_heartbeats(monkeypatch):
    _FakeHeartbeat.calls = []
    monkeypatch.setattr(cli.progress, "Heartbeat", _FakeHeartbeat)
    return _FakeHeartbeat.calls


def _stub_a_clean_run(monkeypatch):
    monkeypatch.setattr(preflight, "run", lambda settings, **kw: None)
    monkeypatch.setattr(stages, "mirror", lambda settings, data_type, **kw: 0)
    monkeypatch.setattr(provision, "run", lambda settings, **kw: True)
    monkeypatch.setattr(ola, "run", lambda settings, **kw: True)
    monkeypatch.setattr(stages, "load", lambda settings, data_type, **kw: 0)
    monkeypatch.setattr(backup, "run", lambda settings, **kw: True)
    monkeypatch.setattr(backup, "newest_backup", lambda settings: None)
    monkeypatch.setattr(report, "loaded_rows", lambda settings, **kw: {})


def test_main_announces_every_stage(monkeypatch, tmp_path, capsys):
    """The complaint this whole feature answers: a run that says nothing for
    seven hours is indistinguishable from a hung one."""
    _base_env(monkeypatch, tmp_path, "green")
    monkeypatch.setenv("TAXI_PROGRESS_INTERVAL_S", "0")
    _stub_a_clean_run(monkeypatch)
    monkeypatch.setattr(cli.progress, "expected_rows_total", lambda s, ty, **kw: None)

    assert cli.main([]) == 0

    out = capsys.readouterr().out
    for expected in ("preflight: starting", "preflight: done in",
                     "mirror green: starting", "mirror green: done in",
                     "provision: starting", "ola: starting",
                     "load green: starting", "load green: done in",
                     "backup: starting", "backup: done in"):
        assert expected in out, expected


def test_every_announcement_carries_a_utc_timestamp(monkeypatch, tmp_path, capsys):
    _base_env(monkeypatch, tmp_path, "green")
    monkeypatch.setenv("TAXI_PROGRESS_INTERVAL_S", "0")
    _stub_a_clean_run(monkeypatch)

    cli.main([])

    stage_lines = [line for line in capsys.readouterr().out.splitlines()
                   if ": starting" in line or ": done in" in line]
    assert stage_lines
    for line in stage_lines:
        assert line.startswith("[") and line[9] == "]", line


def test_a_stage_returning_true_is_not_reported_as_exit_true(
        monkeypatch, tmp_path, capsys):
    """bool is an int subclass; provision and ola return True, and "(exit True)"
    reads as though a stage returned a bogus exit code."""
    _base_env(monkeypatch, tmp_path, "green")
    monkeypatch.setenv("TAXI_PROGRESS_INTERVAL_S", "0")
    _stub_a_clean_run(monkeypatch)

    cli.main([])

    out = capsys.readouterr().out
    assert "exit True" not in out
    assert "mirror green: done in 0s (exit 0)" in out


def test_main_announces_a_failing_stage_and_still_returns_its_code(
        monkeypatch, tmp_path, capsys):
    _base_env(monkeypatch, tmp_path, "green")
    monkeypatch.setenv("TAXI_PROGRESS_INTERVAL_S", "0")
    monkeypatch.setattr(preflight, "run", lambda settings, **kw: None)
    monkeypatch.setattr(stages, "mirror", lambda settings, data_type, **kw: 2)
    monkeypatch.setattr(provision, "run", _never_called("provision.run"))

    assert cli.main([]) == 2

    captured = capsys.readouterr()
    assert "mirror green: starting" in captured.out
    assert "mirror green: done in 0s (exit 2)" in captured.out
    assert "error:" in captured.err


def test_main_heartbeats_mirror_and_load_but_not_the_short_stages(
        monkeypatch, tmp_path, recorded_heartbeats):
    _base_env(monkeypatch, tmp_path, "green")
    _stub_a_clean_run(monkeypatch)
    monkeypatch.setattr(cli.progress, "expected_rows_total",
                        lambda settings, types, **kw: 1_607_275_004)

    assert cli.main([]) == 0

    beating = {hb.label: hb.kwargs for hb in recorded_heartbeats
               if hb.kwargs["interval_s"] > 0}
    # Both mirror and load are now per trip type.
    assert set(beating) == {"mirror green", "load green"}
    assert beating["mirror green"]["unit"] == "files"
    assert beating["mirror green"]["total"] is None
    assert beating["load green"]["unit"] == "rows"
    assert beating["load green"]["total"] == 1_607_275_004


def test_the_load_announcement_names_the_expected_total(
        monkeypatch, tmp_path, capsys):
    _base_env(monkeypatch, tmp_path, "green")
    monkeypatch.setenv("TAXI_PROGRESS_INTERVAL_S", "0")
    _stub_a_clean_run(monkeypatch)
    monkeypatch.setattr(cli.progress, "expected_rows_total",
                        lambda settings, types, **kw: 1_607_275_004)

    cli.main([])

    assert "load green: starting (1.61B rows expected)" in capsys.readouterr().out


def test_each_mirror_heartbeat_samples_its_own_trip_type(
        monkeypatch, tmp_path, recorded_heartbeats):
    """Without a default-argument binding, every closure captures the loop
    variable and both mirror heartbeats report the last trip type."""
    _base_env(monkeypatch, tmp_path, "yellow,green")
    _stub_a_clean_run(monkeypatch)
    monkeypatch.setattr(cli.progress, "expected_rows_total",
                        lambda settings, types, **kw: None)
    monkeypatch.setattr(cli.progress, "normalized_files",
                        lambda settings, data_type: {"yellow": 11, "green": 22}[data_type])
    monkeypatch.setattr(report, "table_rows_total",
                        lambda settings, types, **kw: 77)

    assert cli.main([]) == 0

    sampled = {hb.label: hb.sample() for hb in recorded_heartbeats
               if hb.kwargs["interval_s"] > 0}
    # Load is one fan-out, so its sample is the total across every type.
    assert sampled == {"mirror yellow": 11, "mirror green": 22,
                       "load yellow": 77, "load green": 77}


def test_a_heartbeat_is_not_started_when_the_interval_is_zero(
        monkeypatch, tmp_path, recorded_heartbeats):
    _base_env(monkeypatch, tmp_path, "green")
    monkeypatch.setenv("TAXI_PROGRESS_INTERVAL_S", "0")
    _stub_a_clean_run(monkeypatch)

    assert cli.main([]) == 0
    assert all(hb.kwargs["interval_s"] == 0 for hb in recorded_heartbeats)


def test_main_applies_the_memory_cap_before_provisioning(
        monkeypatch, tmp_path, capsys):
    """Server-level configuration, so it is its own step rather than part of
    provision -- and it must land before anything heavy runs."""
    _base_env(monkeypatch, tmp_path, "green")
    monkeypatch.setenv("TAXI_PROGRESS_INTERVAL_S", "0")
    _stub_a_clean_run(monkeypatch)
    calls = []
    monkeypatch.setattr(provision, "apply_memory_cap",
                        lambda s, **kw: calls.append("memory") or 24057)
    monkeypatch.setattr(provision, "run", lambda s, **kw: calls.append("provision"))

    assert cli.main([]) == 0
    assert calls == ["memory", "provision"]
    assert "memory: starting" in capsys.readouterr().out


# --- per-type load ---------------------------------------------------------

def _stub_load_results(monkeypatch, results: dict):
    """Stub taxi-load's exit code per trip type."""
    _stub_a_clean_run(monkeypatch)
    monkeypatch.setattr(cli.progress, "expected_rows_total", lambda s, ty, **kw: None)
    monkeypatch.setattr(report, "render", lambda settings, **kw: "report")
    seen: list = []

    def fake_load(settings, data_type, **kw):
        seen.append(data_type)
        return results.get(data_type, 0)
    monkeypatch.setattr(stages, "load", fake_load)
    return seen


def test_main_loads_every_trip_type_once(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path, "yellow,green,fhv")
    seen = _stub_load_results(monkeypatch, {})
    assert cli.main([]) == 0
    assert seen == ["yellow", "green", "fhv"]


def test_main_returns_the_worst_load_code(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path, "yellow,green")
    # taxi-load's 1 means "failed mid-load", which this project maps to 2.
    _stub_load_results(monkeypatch, {"green": 1})
    assert cli.main([]) == 2


def test_main_skips_the_backup_when_a_load_failed(monkeypatch, tmp_path, capsys):
    _base_env(monkeypatch, tmp_path, "yellow,green")
    _stub_load_results(monkeypatch, {"green": 2})
    backed_up = []
    monkeypatch.setattr(backup, "run", lambda settings, **kw: backed_up.append(1))
    assert cli.main([]) == 2
    assert backed_up == []
    assert "skipping backup" in capsys.readouterr().err


def test_main_backs_up_when_every_load_is_clean(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path, "yellow")
    _stub_load_results(monkeypatch, {})
    backed_up = []
    monkeypatch.setattr(backup, "run", lambda settings, **kw: backed_up.append(1) or True)
    assert cli.main([]) == 0
    assert backed_up == [1]


def test_main_reports_every_failed_type_not_just_the_first(
        monkeypatch, tmp_path, capsys):
    """A failing type does not abandon the others.

    Each trip type is independent and taxi-load resumes from the manifest, so a partial
    data set plus a full accounting of what failed beats stopping at the first problem.
    """
    _base_env(monkeypatch, tmp_path, "yellow,green,fhv")
    seen = _stub_load_results(monkeypatch, {"yellow": 2, "fhv": 2})
    assert cli.main([]) == 2
    assert seen == ["yellow", "green", "fhv"]      # green still ran
    err = capsys.readouterr().err
    assert "yellow" in err and "fhv" in err


def test_main_tells_the_operator_a_re_run_resumes(monkeypatch, tmp_path, capsys):
    _base_env(monkeypatch, tmp_path, "green")
    _stub_load_results(monkeypatch, {"green": 2})
    cli.main([])
    assert "resumes from the manifest" in capsys.readouterr().err
