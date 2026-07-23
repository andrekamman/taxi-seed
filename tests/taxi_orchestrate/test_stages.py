import sys
from pathlib import Path

from taxi_orchestrate.stages import (
    LoadConn, build_download_cmd, build_load_cmd, build_normalize_cmd, run,
)


def _conn(**kw):
    base = dict(host="h", port=1433, database="taxi", schema="dbo", user="sa",
                flush_rows=100000, full_refresh=False)
    base.update(kw)
    return LoadConn(**base)


def test_download_full_all_types():
    cmd = build_download_cmd(Path("/repo"), None, None)
    assert cmd == ["bash", "/repo/downloader/download_taxi_data.sh"]


def test_download_full_one_type():
    cmd = build_download_cmd(Path("/repo"), "yellow", None)
    assert cmd[-1] == "yellow" and "--recent" not in cmd


def test_download_recent_default_n():
    cmd = build_download_cmd(Path("/repo"), "green", 0)
    assert "--recent" in cmd and "green" == cmd[-1]
    assert cmd[cmd.index("--recent") + 1] == "green"  # no numeric N inserted


def test_download_recent_explicit_n():
    cmd = build_download_cmd(Path("/repo"), "green", 3)
    i = cmd.index("--recent")
    assert cmd[i + 1] == "3" and cmd[-1] == "green"


def test_normalize_cmd():
    cmd = build_normalize_cmd("yellow", "50%")
    assert cmd == [sys.executable, "-m", "taxi_normalize.cli", "yellow", "--sample", "50%"]


def test_normalize_cmd_no_sample():
    cmd = build_normalize_cmd("fhv", None)
    assert cmd == [sys.executable, "-m", "taxi_normalize.cli", "fhv"]


def test_load_cmd_forwards_flags():
    cmd = build_load_cmd("yellow", "raw-normalized", _conn(host="db1", port=1444, full_refresh=True))
    assert cmd[:4] == [sys.executable, "-m", "taxi_loader.cli", "yellow"]
    assert "--input-dir" in cmd and cmd[cmd.index("--input-dir") + 1] == "raw-normalized"
    assert cmd[cmd.index("--host") + 1] == "db1"
    assert cmd[cmd.index("--port") + 1] == "1444"
    assert "--full-refresh" in cmd
    # password is NEVER on the command line
    assert not any("password" in c.lower() for c in cmd)


def test_run_returns_child_exit_code(tmp_path):
    rc = run([sys.executable, "-c", "import sys; sys.exit(7)"], cwd=tmp_path)
    assert rc == 7


def test_run_passes_extra_env(tmp_path):
    code = "import os,sys; sys.exit(0 if os.environ.get('X_TEST')=='v' else 5)"
    rc = run([sys.executable, "-c", code], cwd=tmp_path, extra_env={"X_TEST": "v"})
    assert rc == 0
