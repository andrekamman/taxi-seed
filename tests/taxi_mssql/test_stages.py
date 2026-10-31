import os
import subprocess

import pytest

from taxi_mssql import stages
from taxi_mssql.config import from_env


def settings_for(**env):
    return from_env({"MSSQL_PASSWORD": "pw", "TAXI_DATA_DIR": "/data", **env})


def test_mirror_omits_load_so_provisioning_can_interpose():
    cmd = stages.mirror_cmd(settings_for(), "yellow")
    assert cmd[:2] == ["taxi-pipeline", "yellow"]
    assert "--data-dir" in cmd and "/data" in cmd
    assert "--load" not in cmd


def test_mirror_passes_recent_when_set():
    cmd = stages.mirror_cmd(settings_for(TAXI_RECENT="3"), "yellow")
    assert "--recent" in cmd
    assert cmd[cmd.index("--recent") + 1] == "3"


def test_mirror_omits_recent_for_full_history():
    assert "--recent" not in stages.mirror_cmd(settings_for(), "yellow")


def test_load_targets_the_compose_service():
    cmd = stages.load_cmd(settings_for(), "yellow")
    assert cmd[:2] == ["taxi-load", "yellow"]
    assert cmd[cmd.index("--host") + 1] == "mssql"
    assert cmd[cmd.index("--port") + 1] == "1433"
    assert cmd[cmd.index("--database") + 1] == "taxi"
    assert cmd[cmd.index("--data-dir") + 1] == "/data"


def test_password_reaches_the_subprocess_via_env_not_argv():
    captured = {}

    def runner(cmd, env, cwd):
        captured["cmd"] = cmd
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 0, "", "")

    stages.load(settings_for(), "yellow", runner=runner)

    assert "pw" not in " ".join(captured["cmd"])
    assert captured["env"]["MSSQL_PASSWORD"] == "pw"


def test_run_executes_with_cwd_data_dir_for_mapping_resolution():
    captured = {}

    def runner(cmd, env, cwd):
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    stages.mirror(settings_for(), "yellow", runner=runner)

    assert captured["cwd"] == "/data"


def test_run_returns_the_child_exit_code():
    def runner(cmd, env, cwd):
        return subprocess.CompletedProcess(cmd, 1, "", "")

    assert stages.mirror(settings_for(), "yellow", runner=runner) == 1


def test_load_passes_the_schema_and_user():
    cmd = stages.load_cmd(settings_for(), "yellow")
    assert cmd[cmd.index("--schema") + 1] == "dbo"
    assert cmd[cmd.index("--user") + 1] == "sa"


def test_the_child_env_is_a_full_environ_merge():
    """The child needs PATH to find taxi-pipeline/taxi-load at all; a regression that
    replaced the merge with a bare dict would be maddening to diagnose from the
    resulting FileNotFoundError."""
    captured = {}

    def runner(cmd, env, cwd):
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 0, "", "")

    stages.mirror(settings_for(), "yellow", runner=runner)

    assert set(os.environ) <= set(captured["env"])
    assert captured["env"].get("PATH") == os.environ.get("PATH")


def test_a_missing_taxi_seed_cli_is_a_typed_error_not_a_traceback():
    """subprocess.run raises FileNotFoundError when the executable is not on
    PATH; untyped it escapes cli.main's mapping as a traceback with Python's
    exit code 1, which this project reserves for needs-review."""
    def runner(cmd, env, cwd):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])

    with pytest.raises(stages.StageCommandNotFoundError, match="taxi-load"):
        stages.load(settings_for(), "yellow", runner=runner)


def test_load_requires_an_existing_database():
    """The guard that makes splitting the pipeline around provisioning safe.

    taxi-mssql provisions a presized database several steps before this one runs. If that
    step were skipped or failed soft, taxi-load's default behaviour would quietly create a
    server-default-sized database that nothing notices until it fills.
    """
    assert "--require-existing-database" in stages.load_cmd(settings_for(), "yellow")


def test_load_does_not_shard():
    """One process per trip type, every year in one taxi-load call.

    The month-shard fan-out this module used to run was replaced by DuckDB's in-process
    mssql_copy_parallel_writers, which measured 1.65x faster -- and 1.44x at matched
    parallelism, so the win was the mechanism rather than the degree.
    """
    cmd = stages.load_cmd(settings_for(), "fhvhv")
    assert "--year" not in cmd and "--month" not in cmd
    assert not hasattr(stages, "load_concurrent")
    assert not hasattr(stages, "load_shard")
