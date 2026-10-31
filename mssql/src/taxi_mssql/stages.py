"""Steps 2 and 5: shell out to taxi-seed's CLIs.

Coupled to the documented interface and exit codes, not to taxi-seed internals.

There is no fan-out here any more. `taxi-load` drives DuckDB's own in-process
`mssql_copy_parallel_writers`, which measured 1.65x faster than the process-per-month
supervisor this module used to run, and 1.44x faster at matched parallelism -- so the
advantage was the mechanism, not the degree. The shard discovery, the thread pool and the
year-level integrity gate that existed to clean up after a killed worker all went with it.
"""
from __future__ import annotations

import os
import subprocess

from taxi_mssql.config import Settings


class StageCommandNotFoundError(Exception):
    """A taxi-seed CLI is not on PATH. Maps to exit code 2."""


def _default_runner(cmd, env, cwd):
    return subprocess.run(cmd, env=env, cwd=cwd, check=False)


def mirror_cmd(settings: Settings, data_type: str) -> list[str]:
    """download -> normalize for one type.

    Deliberately no --load. The database must exist, presized, before anything loads into
    it, and provisioning needs the normalized data on disk first to size it -- so the
    pipeline is split around the provision step rather than run end to end.
    """
    cmd = ["taxi-pipeline", data_type, "--data-dir", str(settings.data_dir)]
    if settings.recent is not None:
        cmd += ["--recent", str(settings.recent)]
    return cmd


def load_cmd(settings: Settings, data_type: str) -> list[str]:
    """Load every year of one trip type.

    `--require-existing-database` is the guard that makes splitting the pipeline safe: by
    this point provisioning has created the presized database, and if that step was skipped
    or failed soft, `taxi-load` must refuse rather than quietly create a
    server-default-sized one that nobody notices until it fills.
    """
    return [
        "taxi-load", data_type,
        "--data-dir", str(settings.data_dir),
        "--host", settings.host,
        "--port", str(settings.port),
        "--database", settings.database,
        "--schema", settings.schema,
        "--user", settings.user,
        "--require-existing-database",
    ]


def run(cmd: list[str], settings: Settings, *, runner=None) -> int:
    runner = runner or _default_runner
    # CWD is the data dir because `taxi-normalize` resolves its mappings from the
    # relative path normalize/mappings/<type>.yaml.
    env = {**os.environ, "MSSQL_PASSWORD": settings.password}
    try:
        proc = runner(cmd, env, str(settings.data_dir))
    except FileNotFoundError as e:
        # subprocess.run raises this when the executable is not on PATH. Untyped
        # it escapes cli.main's exception-to-exit-code mapping as a traceback
        # with Python's exit code 1 -- which this project reserves for
        # needs-review. Same hardening ola.py already has.
        raise StageCommandNotFoundError(
            f"{cmd[0]!r} is not on PATH. The taxi-mssql image installs taxi-seed's "
            f"CLIs; a run outside that image needs `uv sync` (or an equivalent "
            f"install of taxi-seed) first."
        ) from e
    return proc.returncode


def mirror(settings: Settings, data_type: str, *, runner=None) -> int:
    return run(mirror_cmd(settings, data_type), settings, runner=runner)


def load(settings: Settings, data_type: str, *, runner=None) -> int:
    return run(load_cmd(settings, data_type), settings, runner=runner)
