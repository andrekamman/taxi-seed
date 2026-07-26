"""Argv builders for each pipeline stage + a thin subprocess runner."""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class LoadConn:
    host: str
    port: int
    database: str
    schema: str
    user: str
    flush_rows: int
    full_refresh: bool


def build_download_cmd(data_type: Optional[str], recent: Optional[int],
                       data_dir: Path) -> list[str]:
    cmd = [sys.executable, "-m", "taxi_download.cli"]
    if recent is not None:
        cmd.append("--recent")
        if recent > 0:
            cmd.append(str(recent))
    if data_type:
        cmd.append(data_type)          # keep TYPE adjacent to the recent group
    cmd += ["--data-dir", str(data_dir)]
    return cmd


def build_normalize_cmd(data_type: str, sample: Optional[str], data_dir: Path) -> list[str]:
    cmd = [sys.executable, "-m", "taxi_normalize.cli", data_type]
    if sample:
        cmd += ["--sample", sample]
    cmd += ["--data-dir", str(data_dir)]
    return cmd


def build_load_cmd(data_type: str, conn: LoadConn, data_dir: Path) -> list[str]:
    cmd = [
        sys.executable, "-m", "taxi_loader.cli", data_type,
        "--data-dir", str(data_dir),
        "--host", conn.host,
        "--port", str(conn.port),
        "--database", conn.database,
        "--schema", conn.schema,
        "--user", conn.user,
        "--flush-rows", str(conn.flush_rows),
    ]
    if conn.full_refresh:
        cmd.append("--full-refresh")
    return cmd


def run(cmd: list[str], cwd: Path, extra_env: Optional[dict] = None) -> int:
    env = None
    if extra_env:
        env = {**os.environ, **extra_env}
    return subprocess.run(cmd, cwd=str(cwd), env=env).returncode
