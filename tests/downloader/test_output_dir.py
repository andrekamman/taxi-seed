"""The downloader resolves its output dir from --data-dir / OUTPUT_DIR without downloading."""
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "downloader" / "download_taxi_data.sh"


def _net_shim(tmp_path):
    """A PATH prefix whose wget/curl/aria2c are instant no-ops, so even if the
    walker reaches a download call, nothing hits the network."""
    binp = tmp_path / "shimbin"
    binp.mkdir()
    for tool in ("wget", "curl", "aria2c"):
        p = binp / tool
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)
    return f"{binp}{os.pathsep}{os.environ['PATH']}"


def _run(tmp_path, *args, **env):
    e = {**os.environ, "PATH": _net_shim(tmp_path), **env}
    # --recent 0 => zero download iterations; the shim covers any stray call.
    return subprocess.run(
        ["bash", str(SCRIPT), "--recent", "0", "yellow", *args],
        env=e, capture_output=True, text=True, timeout=60,
    )


def test_data_dir_creates_base_raw(tmp_path):
    base = tmp_path / "elsewhere"
    r = _run(tmp_path, "--data-dir", str(base))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (base / "raw").is_dir()


def test_output_dir_env_overrides_data_dir(tmp_path):
    base = tmp_path / "base"
    custom = tmp_path / "custom"
    r = _run(tmp_path, "--data-dir", str(base), OUTPUT_DIR=str(custom))
    assert r.returncode == 0, r.stdout + r.stderr
    assert custom.is_dir()                 # OUTPUT_DIR wins
    assert not (base / "raw").exists()     # --data-dir ignored when OUTPUT_DIR set
