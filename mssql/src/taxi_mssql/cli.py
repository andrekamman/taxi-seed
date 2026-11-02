"""The seven-step sequence, and the exit code that summarises it.

Exit codes mirror taxi-seed: 0 clean, 1 needs-review, 2 operational failure.
A step that returns 1 or 2 stops the run -- no type is loaded whose normalize
halted, and no database is backed up whose load failed. Partial progress is
always safe because every step resumes from an observable.

The load step is the one exception to "stops the run": a trip type that fails
to load does not abandon the others, because each is independent and a partial
data set is more useful than none. Every type is attempted, every failure is
reported, the worst code wins, and the backup is skipped unless everything was
clean.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Iterable

from taxi_mssql import (backup, mssql, ola, preflight, progress, provision,
                        report, sql, stages)
from taxi_mssql.config import ConfigError, from_env

CLEAN = 0
NEEDS_REVIEW = 1
FAILURE = 2

# The image's ENTRYPOINT is ["taxi-mssql"] with the mssql service started via
# depends_on: condition: service_healthy, so with no arg parsing at all,
# `taxi-mssql --help` used to start a real SQL Server and run the live
# seven-step pipeline against whatever data directory was mounted. Parsing
# argv -- and doing it before from_env, which touches the environment, not
# argv -- means -h/--help and unknown flags are handled before anything else
# runs.
_ENV_VARS_HELP = """\
environment variables:
  TAXI_DATA_DIR         bind-mounted data directory (default: /data)
  MSSQL_PASSWORD        SA password (required)
  TAXI_TYPES            comma-separated taxi types to mirror/normalize/load
                        (default: yellow,green,fhv,fhvhv)
  TAXI_RECENT           most recent N months to mirror; blank means full
                        history
  TAXI_DATA_FACTOR      mdf size = normalized parquet bytes x this factor,
                        rounded up to 1 GB below 10 GB implied, else 10 GB
                        (default: 3.0)
  MSSQL_DATA_SIZE_GB    skip the size estimate and use this instead; only
                        consulted at database creation
  MSSQL_LOG_SIZE_GB     ldf size in GB (default: 25% of the mdf size,
                        floored at 1 GB and capped at 16 GB)
  MSSQL_FILEGROWTH_MB   data/log filegrowth in MB (default: 1024)
  MSSQL_HOST            SQL Server host (default: mssql, the compose service)
  MSSQL_PORT            SQL Server port (default: 1433)
  MSSQL_DATABASE        database to create and load (default: taxi); must be a
                        plain identifier -- it is interpolated into T-SQL
  MSSQL_SCHEMA          schema holding the loaded tables (default: dbo); same
                        identifier rule
  MSSQL_USER            SQL login (default: sa)
  MSSQL_MAX_MEMORY_MB   SQL Server 'max server memory' in MB (default:
                        host RAM minus the larger of 4 GB and 25%)
  TAXI_PROGRESS_INTERVAL_S
                        seconds between progress lines during mirror and load
                        (default: 60; 0 disables)
"""


def _build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="taxi-mssql",
        description=(
            "Advance one bind-mounted data directory through mirror, "
            "provision, load, and backup. Configured entirely by "
            "environment variables -- there are no positional or optional "
            "arguments to set here."
        ),
        epilog=_ENV_VARS_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def worst(codes: Iterable[int]) -> int:
    return max(codes, default=CLEAN)


def _halt_label(rc: int) -> str:
    """1 is an expected, non-catastrophic halt (e.g. unacknowledged schema
    drift) -- label it distinctly from a genuine operational failure so log
    skimmers don't mistake one for the other."""
    return "needs-review" if rc == NEEDS_REVIEW else "error"


def load_exit_code(child_rc: int) -> int:
    """Translate `taxi-load`'s exit code into this project's contract.

    `taxi-pipeline` returns 1 only for normalize's schema-drift halt -- a decision a
    human genuinely has to make, which is what 1 means here. `taxi-load` returns
    1 for "failed mid-load" (a dropped connection, a duckdb error on one type),
    and taxi-seed's own orchestrator classifies that as PARTIAL and maps it to
    2. Passing it through as 1 tells CI and operators to go inspect a drift
    acknowledgment that does not exist, when a plain re-run is the remedy.
    """
    return FAILURE if child_rc == NEEDS_REVIEW else child_rc


def _stage(settings, label: str, fn, *, sample=None, total: int | None = None,
           unit: str = "rows"):
    """Announce a stage, heartbeat through it if it has a sampler, and say how
    long it took and what it returned.

    Returns whatever `fn` returns. Deliberately does not catch: main's existing
    except block owns the error-to-exit-code mapping, and swallowing here would
    change exit codes.

    A stage with no sampler gets interval_s=0, which is the same switch
    TAXI_PROGRESS_INTERVAL_S=0 throws: the Heartbeat starts no thread at all.
    provision, ola, preflight and backup are minutes at most, and a start/done
    pair is all they need.
    """
    hint = f" ({progress.humanize_count(total)} {unit} expected)" if total else ""
    progress.emit(f"{label}: starting{hint}")

    started = time.monotonic()
    with progress.Heartbeat(
        label,
        sample if sample is not None else (lambda: None),
        total=total,
        unit=unit,
        interval_s=settings.progress_interval_s if sample is not None else 0,
    ):
        result = fn()

    elapsed = progress.humanize_duration(time.monotonic() - started)
    # `type(result) is int` rather than isinstance: bool subclasses int, and
    # provision and ola return True, which would print as "(exit True)".
    suffix = f" (exit {result})" if type(result) is int else ""
    progress.emit(f"{label}: done in {elapsed}{suffix}")
    return result


def main(argv=None) -> int:
    # parse_args() itself calls sys.exit(0) after printing help for -h/--help,
    # and sys.exit(2) after printing usage to stderr for an unrecognised
    # argument -- 2 already matches this module's FAILURE convention.
    _build_parser().parse_args(argv)

    try:
        settings = from_env(os.environ)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return FAILURE

    codes: list[int] = []
    try:
        _stage(settings, "preflight", lambda: preflight.run(settings))

        # The `dt=data_type` bindings below are load-bearing: without them every
        # closure captures the loop variable and each stage reports the last
        # trip type instead of its own.
        for data_type in settings.types:
            rc = _stage(
                settings, f"mirror {data_type}",
                lambda dt=data_type: stages.mirror(settings, dt),
                sample=lambda dt=data_type: progress.normalized_files(settings, dt),
                unit="files",
            )
            codes.append(rc)
            if rc != CLEAN:
                print(f"{_halt_label(rc)}: mirror stage for {data_type} exited {rc}; "
                      f"stopping", file=sys.stderr)
                return worst(codes)

        _stage(settings, "memory", lambda: provision.apply_memory_cap(settings))
        _stage(settings, "provision", lambda: provision.run(settings))
        _stage(settings, "ola", lambda: ola.run(settings))

        # The `dt=data_type` bindings are load-bearing here for the same reason
        # as in the mirror loop: without them every closure captures the loop
        # variable and samples the last trip type instead of its own.
        for data_type in settings.types:
            child_rc = _stage(
                settings, f"load {data_type}",
                lambda dt=data_type: stages.load(settings, dt),
                sample=lambda dt=data_type: report.table_rows_total(settings, [dt]),
                total=progress.expected_rows_total(settings, [data_type]),
            )
            rc = load_exit_code(child_rc)
            codes.append(rc)
            # No early return: a type that fails to load does not make the
            # others worthless, and taxi-load resumes from the manifest, so the
            # remedy for most failures is simply to run again.
            #
            # Report the child's own code, not the mapped one: an operator
            # reading "exited 2" wants to know what taxi-load said, and
            # load_exit_code deliberately rewrites 1 to 2.
            if rc != CLEAN:
                print(f"{_halt_label(rc)}: load stage for {data_type} exited "
                      f"{child_rc} (re-running resumes from the manifest)",
                      file=sys.stderr)

        if worst(codes) != CLEAN:
            print("error: one or more loads failed; skipping backup",
                  file=sys.stderr)
            return worst(codes)

        _stage(settings, "backup", lambda: backup.run(settings))

    except (mssql.MssqlError, sql.SqlIdentifierError, ola.OlaScriptMissingError,
            stages.StageCommandNotFoundError,
            provision.InsufficientDiskSpaceError) as e:
        print(f"error: {e}", file=sys.stderr)
        return FAILURE

    print(report.render(
        settings,
        backup_path=backup.newest_backup(settings),
        rows_by_type=report.loaded_rows(settings),
    ))
    return worst(codes)


if __name__ == "__main__":
    sys.exit(main())
