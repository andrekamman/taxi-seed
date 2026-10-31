"""Run T-SQL via sqlcmd. The only module that talks to SQL Server."""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from taxi_mssql.config import Settings

SQLCMD = os.environ.get("SQLCMD_PATH", "/opt/mssql-tools18/bin/sqlcmd")

# sqlcmd's "-h -1" suppresses column headers but NOT the row-count trailer, and
# it goes to stdout mixed in with the results. NOCOUNT stops it being emitted;
# the pattern below is the belt to that pair of braces, because a batch that
# sets it back on (a vendored script, a future query) must not corrupt a scalar.
_NOCOUNT = "SET NOCOUNT ON;"
_ROWCOUNT_RE = re.compile(r"\A\(\d+ rows? affected\)\Z", re.IGNORECASE)


class MssqlError(Exception):
    """sqlcmd failed, or the server never became reachable. Exit code 2."""


def _default_runner(cmd, env, stdin_text):
    return subprocess.run(cmd, env=env, input=stdin_text, capture_output=True,
                          text=True, check=False)


def build_cmd(settings: Settings, *, database: str,
              sql: str | None = None, input_file: str | None = None) -> list[str]:
    """sqlcmd argv. The password is deliberately absent -- see _env()."""
    cmd = [
        SQLCMD,
        "-S", f"{settings.host},{settings.port}",
        "-U", settings.user,
        "-d", database,
        "-C",        # trust the container's self-signed certificate
        "-b",        # exit non-zero on a SQL error
        "-h", "-1",  # no column headers
        "-W",        # strip trailing whitespace
    ]
    if sql is not None:
        cmd += ["-Q", f"{_NOCOUNT}\n{sql}"]
    if input_file is not None:
        cmd += ["-i", str(input_file)]
    return cmd


def _env(settings: Settings) -> dict[str, str]:
    # SQLCMDPASSWORD keeps the password out of argv, which is world-readable
    # via /proc on the host.
    return {**os.environ, "SQLCMDPASSWORD": settings.password}


def _execute(settings: Settings, cmd: list[str], runner) -> str:
    runner = runner or _default_runner
    proc = runner(cmd, _env(settings), None)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise MssqlError(f"sqlcmd failed (exit {proc.returncode}): {detail}")
    return proc.stdout or ""


def run(settings: Settings, sql: str, *, database: str = "master", runner=None) -> str:
    return _execute(settings, build_cmd(settings, database=database, sql=sql), runner)


def run_file(settings: Settings, path: str | Path, *,
             database: str = "master", runner=None) -> str:
    return _execute(
        settings, build_cmd(settings, database=database, input_file=str(path)), runner
    )


def first_value(out: str) -> str | None:
    """The first real value in sqlcmd's stdout, or None for no value / SQL NULL.

    Real output is not a bare token: a NULL scalar arrives as
    "NULL\\n\\n(1 rows affected)", so blank lines and the row-count trailer have
    to be skipped before the first line can be trusted. Getting this wrong
    returned the *string* "NULL" to callers, which is truthy and blew up in
    datetime.fromisoformat as an untyped ValueError -- a bare traceback with
    Python's exit code 1, which this project reserves for "needs review".
    """
    for line in out.splitlines():
        line = line.strip()
        if not line or _ROWCOUNT_RE.match(line):
            continue
        return None if line == "NULL" else line
    return None


def scalar(settings: Settings, sql: str, *,
           database: str = "master", runner=None) -> str | None:
    return first_value(run(settings, sql, database=database, runner=runner))


def wait_for_login(settings: Settings, *, timeout_s: int = 300, sleep_s: int = 5,
                   runner=None, sleeper=None, clock=None) -> None:
    """Poll a real login until it succeeds.

    Compose's healthcheck already gates startup, but it proves the server is up,
    not that this user can log in to this port -- so prove the thing we depend on.
    """
    sleeper = sleeper or time.sleep
    clock = clock or time.monotonic
    deadline = clock() + timeout_s
    last = ""
    while True:
        try:
            run(settings, "SELECT 1", database="master", runner=runner)
            return
        except MssqlError as e:
            last = str(e)
        if clock() >= deadline:
            raise MssqlError(
                f"SQL Server at {settings.host}:{settings.port} did not accept a "
                f"login within {timeout_s}s. Last error: {last}"
            )
        sleeper(sleep_s)
