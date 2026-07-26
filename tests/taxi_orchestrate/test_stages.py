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
    cmd = build_download_cmd(Path("/repo"), None, None, Path("/data"))
    assert cmd[:2] == ["bash", "/repo/downloader/download_taxi_data.sh"]
    assert cmd[cmd.index("--data-dir") + 1] == "/data"


def test_download_full_one_type():
    cmd = build_download_cmd(Path("/repo"), "yellow", None, Path("/data"))
    assert "yellow" in cmd and "--recent" not in cmd
    # data_type stays adjacent to the recent group; --data-dir is appended last
    assert cmd[-2:] == ["--data-dir", "/data"]


def test_download_recent_default_n():
    cmd = build_download_cmd(Path("/repo"), "green", 0, Path("/data"))
    assert "--recent" in cmd
    assert cmd[cmd.index("--recent") + 1] == "green"  # no numeric N inserted
    assert cmd[-2:] == ["--data-dir", "/data"]


def test_download_recent_explicit_n():
    cmd = build_download_cmd(Path("/repo"), "green", 3, Path("/data"))
    i = cmd.index("--recent")
    assert cmd[i + 1] == "3" and cmd[i + 2] == "green"
    assert cmd[-2:] == ["--data-dir", "/data"]


def test_normalize_cmd():
    cmd = build_normalize_cmd("yellow", "50%", Path("/data"))
    assert cmd[:4] == [sys.executable, "-m", "taxi_normalize.cli", "yellow"]
    assert cmd[cmd.index("--sample") + 1] == "50%"
    assert cmd[cmd.index("--data-dir") + 1] == "/data"


def test_normalize_cmd_no_sample():
    cmd = build_normalize_cmd("fhv", None, Path("/data"))
    assert cmd == [sys.executable, "-m", "taxi_normalize.cli", "fhv", "--data-dir", "/data"]


def test_load_cmd_forwards_flags():
    cmd = build_load_cmd("yellow", _conn(host="db1", port=1444, full_refresh=True), Path("/data"))
    assert cmd[:4] == [sys.executable, "-m", "taxi_loader.cli", "yellow"]
    assert cmd[cmd.index("--data-dir") + 1] == "/data"
    assert "--input-dir" not in cmd
    assert cmd[cmd.index("--host") + 1] == "db1"
    assert cmd[cmd.index("--port") + 1] == "1444"
    assert "--full-refresh" in cmd
    assert not any("password" in c.lower() for c in cmd)


def test_run_returns_child_exit_code(tmp_path):
    rc = run([sys.executable, "-c", "import sys; sys.exit(7)"], cwd=tmp_path)
    assert rc == 7


def test_run_passes_extra_env(tmp_path):
    code = "import os,sys; sys.exit(0 if os.environ.get('X_TEST')=='v' else 5)"
    rc = run([sys.executable, "-c", code], cwd=tmp_path, extra_env={"X_TEST": "v"})
    assert rc == 0
