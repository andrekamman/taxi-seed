"""Step 3: create the presized database, attach one whose files outlived the
server's memory of it, or explain why it was skipped."""
from __future__ import annotations

import shutil

from taxi_mssql import mssql, sizing, sql
from taxi_mssql.config import Settings


class InsufficientDiskSpaceError(Exception):
    """The data volume cannot fit the planned mdf/ldf allocation. Exit code 2."""


def implied_data_mb(settings: Settings) -> int:
    """The mdf size this run's scope implies, in MB."""
    if settings.data_size_gb is not None:
        return settings.data_size_gb * 1024
    measured = sizing.parquet_bytes(settings.raw_normalized_dir, settings.types)
    return sizing.data_size_mb(measured, settings.data_factor)


def _allocated_mb(settings: Settings, runner) -> int:
    raw = mssql.scalar(settings, sql.data_file_size_mb(settings.database),
                       database="master", runner=runner)
    try:
        return int(raw) if raw is not None else 0
    except ValueError:
        return 0


def _attach(settings: Settings, runner) -> None:
    print(
        f"provision: {settings.database!r} is not registered on this server, but "
        f"{settings.mdf_path} already exists -- attaching the existing files "
        f"instead of creating new ones"
    )
    mssql.run(
        settings,
        sql.attach_database(settings.database, str(settings.mdf_path),
                            str(settings.ldf_path)),
        database="master", runner=runner,
    )


def run(settings: Settings, *, runner=None) -> bool:
    """Create or attach the database if absent. True when it did either.

    The attach branch is the normal warm-start path, taken on every restart:
    `/var/opt/mssql` is deliberately not persisted, so each container start
    gets a fresh `master` with no memory of 'taxi', while the mdf/ldf live on
    the bind mount and survive `docker compose down` regardless. Creating over
    an existing file fails with Msg 5170 and keeps failing on every subsequent
    run, so the orphaned mdf is attached, not recreated -- and this is also
    what makes a moved TAXI_DATA_DIR and a rotated MSSQL_PASSWORD work, since
    there is never a stale master to disagree with the new state.
    """
    exists = mssql.scalar(settings, sql.database_exists(settings.database),
                          database="master", runner=runner) == "1"

    data_mb = implied_data_mb(settings)

    if exists:
        allocated = _allocated_mb(settings, runner)
        if sizing.needs_growth_warning(data_mb, allocated):
            print(
                f"provision: WARNING database {settings.database!r} already exists with "
                f"{allocated / 1024:.0f} GB allocated, but this run's scope implies "
                f"{data_mb / 1024:.0f} GB.\n"
                f"           Sizing only runs at creation, so the file will autogrow in "
                f"{settings.filegrowth_mb} MB steps -- the fragmentation presizing exists "
                f"to avoid.\n"
                f"           Set MSSQL_DATA_SIZE_GB on a fresh database, or "
                f"re-presize: run DROP DATABASE [{settings.database}], then delete "
                f"{settings.mdf_path} and {settings.ldf_path}, then re-run to "
                f"re-presize and reload from parquet.\n"
                f"           Deleting the files first is not enough within this "
                f"server's current lifetime -- master still has "
                f"{settings.database!r} registered, so the next run before a "
                f"restart skips provisioning and then fails reading the file "
                f"sizes of a database it cannot open. Restarting mssql resets "
                f"master and would let a later run recreate cleanly, but do not "
                f"rely on that -- DROP DATABASE first, always."
            )
        else:
            print(f"provision: database {settings.database!r} exists, skipping")
        return False

    if settings.mdf_path.exists():
        _attach(settings, runner)
        mssql.run(settings, sql.set_recovery_simple(settings.database),
                  database="master", runner=runner)
        return True

    log_mb = sizing.log_size_mb(data_mb, settings.log_size_gb)

    required_mb = sizing.required_disk_mb(data_mb, log_mb)
    free_mb = shutil.disk_usage(settings.data_dir).free // (1024 ** 2)
    if not sizing.fits_on_disk(required_mb, free_mb):
        raise InsufficientDiskSpaceError(
            f"provisioning {settings.database!r} needs "
            f"{required_mb / 1024:.1f} GB free on {settings.data_dir} "
            f"({data_mb / 1024:.0f} GB data + {log_mb / 1024:.0f} GB log, "
            f"plus headroom), but only {free_mb / 1024:.1f} GB is free. "
            f"Lower MSSQL_DATA_SIZE_GB and/or MSSQL_LOG_SIZE_GB, or use a "
            f"bigger disk."
        )

    print(
        f"provision: creating {settings.database!r} with "
        f"{data_mb / 1024:.0f} GB data + {log_mb / 1024:.0f} GB log"
    )
    # Whether SQL Server on Linux gets instant file initialization depends on the
    # host filesystem and is still unconfirmed (see the spec's open risk). If it
    # does not, both files are zero-filled inside this one statement, which then
    # produces no output for a long time. Say so before it happens, so a silent
    # terminal is not mistaken for a hang.
    print(
        f"provision: allocating {(data_mb + log_mb) / 1024:.0f} GB of files now. "
        f"This is a single CREATE DATABASE with no progress output, and if the "
        f"host filesystem does not support instant file initialization the files "
        f"are zero-filled first -- on a large database that can take a long time. "
        f"It is not hung."
    )
    mssql.run(
        settings,
        sql.create_database(
            settings.database, str(settings.mdf_path), str(settings.ldf_path),
            data_mb, log_mb, settings.filegrowth_mb,
        ),
        database="master", runner=runner,
    )
    mssql.run(settings, sql.set_recovery_simple(settings.database),
              database="master", runner=runner)
    return True


def _host_total_mb() -> int | None:
    """Host RAM in MB from /proc/meminfo.

    The mssql service has no container memory limit, so the host total is the
    number that matters. None when unreadable -- on a platform where this file
    is absent we leave SQL Server's own default alone rather than guess.
    """
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def apply_memory_cap(settings: Settings, *, runner=None, meminfo=None) -> int | None:
    """Set `max server memory`. Returns the cap applied, or None if skipped.

    sp_configure rather than the container's MSSQL_MEMORY_LIMIT_MB because it is
    dynamic -- no restart -- and it lets the default be computed from the actual
    host instead of hardcoded per machine.
    """
    if settings.mssql_max_memory_mb is not None:
        cap = settings.mssql_max_memory_mb
        source = "MSSQL_MAX_MEMORY_MB"
    else:
        host_mb = (meminfo or _host_total_mb)()
        if host_mb is None:
            print("memory: host RAM unreadable, leaving max server memory at "
                  "SQL Server's default (80% of host)")
            return None
        cap = sizing.max_server_memory_mb(host_mb)
        source = f"derived from {host_mb} MB host RAM"
    mssql.run(settings, sql.set_max_server_memory(cap),
              database="master", runner=runner)
    print(f"memory: max server memory set to {cap} MB ({source})")
    return cap
