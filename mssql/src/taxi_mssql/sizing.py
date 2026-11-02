"""Turn normalized-parquet bytes into SQL Server file sizes. Pure arithmetic."""
from __future__ import annotations

import math
from collections.abc import Iterable
from pathlib import Path

GB = 1024 ** 3
GRANULARITY_GB = 10
SMALL_GRANULARITY_GB = 1
WARN_THRESHOLD = 1.25

# ldf default, applied only when MSSQL_LOG_SIZE_GB is unset. Under SIMPLE
# recovery with TABLOCK bulk loads the log truncates on checkpoint and never
# tracks data volume, so this was always meant as a generous ceiling on the
# default, not a floor and not a target -- hence scaling it down for small
# databases rather than treating 16 GB as a flat requirement.
LOG_DEFAULT_FRACTION = 0.25
LOG_DEFAULT_FLOOR_GB = 1
LOG_DEFAULT_CAP_GB = 16

# Safety margin added on top of the raw mdf+ldf allocation before checking it
# against free disk space. Presizing already asks for everything the database
# will ever need up front, so the risk is running the volume to zero, not
# missing a target by a few percent -- a flat margin covers filesystem
# bookkeeping without chasing 100% utilization.
DISK_HEADROOM_MB = 1024


def parquet_bytes(root: Path, types: Iterable[str]) -> int:
    """Total bytes of *.parquet under root/<type>/ for the selected types only.

    Only the selected types are summed: the directory may still hold types from
    an earlier, broader run, and only these types will reach the database.
    """
    total = 0
    for data_type in types:
        type_dir = root / data_type
        if not type_dir.is_dir():
            continue
        for f in type_dir.rglob("*.parquet"):
            total += f.stat().st_size
    return total


def data_size_mb(parquet_bytes: int, factor: float,
                 granularity_gb: int = GRANULARITY_GB) -> int:
    """Estimated mdf size in MB, rounded up to a size-appropriate boundary.

    Below granularity_gb the granularity itself scales down to 1 GB, so a
    128 MB smoke run gets a 1 GB file instead of the flat granularity_gb (10
    GB) every run used to demand regardless of scope. At or above
    granularity_gb, rounding is unchanged: the next granularity_gb boundary,
    same as before. Floors at one small granule (1 GB, never 0) so a tiny or
    empty mirror still gets a usable, nonzero file.
    """
    raw_gb = parquet_bytes * factor / GB
    step_gb = SMALL_GRANULARITY_GB if raw_gb < granularity_gb else granularity_gb
    granules = max(1, math.ceil(raw_gb / step_gb))
    return granules * step_gb * 1024


def log_size_mb(data_mb: int, log_size_gb: int | None) -> int:
    """ldf size in MB.

    An explicit MSSQL_LOG_SIZE_GB is honoured verbatim, mirroring how
    MSSQL_DATA_SIZE_GB overrides the data estimate. Left unset, the log scales
    with the data instead of a flat default: 25% of the data size, floored at
    1 GB and capped at 16 GB -- the cap keeps a full-history run at exactly the
    16 GB the old flat default gave it, while a 1 GB database gets a 1 GB log
    instead of 16.
    """
    if log_size_gb is not None:
        return log_size_gb * 1024
    default_mb = data_mb * LOG_DEFAULT_FRACTION
    capped_mb = min(LOG_DEFAULT_CAP_GB * 1024, default_mb)
    return int(max(LOG_DEFAULT_FLOOR_GB * 1024, capped_mb))


def required_disk_mb(data_mb: int, log_mb: int,
                     headroom_mb: int = DISK_HEADROOM_MB) -> int:
    """Total free space CREATE DATABASE needs, including headroom."""
    return data_mb + log_mb + headroom_mb


def fits_on_disk(required_mb: int, free_mb: int) -> bool:
    """True when free_mb covers required_mb, including its headroom."""
    return free_mb >= required_mb


def needs_growth_warning(implied_mb: int, allocated_mb: int,
                         threshold: float = WARN_THRESHOLD) -> bool:
    """True when the existing mdf is materially smaller than this run implies.

    allocated_mb == 0 means the allocation could not be read; stay silent rather
    than warn on missing information.
    """
    if allocated_mb <= 0:
        return False
    return implied_mb > allocated_mb * threshold


MIN_SERVER_MEMORY_MB = 1024


def max_server_memory_mb(host_total_mb: int) -> int:
    """How much RAM SQL Server may have, given the whole host.

    Aggressive but not greedy: reserve a quarter of the box, never less than
    4 GB, for the OS, dockerd, and the duckdb side of every load stream. Left
    alone, SQL Server on Linux takes 80% of the host and never gives it back --
    measured at 22,959 MB committed on a 32 GB box on 2026-08-15.
    """
    if host_total_mb < 1:
        raise ValueError(f"host_total_mb must be >= 1, got {host_total_mb}")
    reserve = max(4096, host_total_mb // 4)
    return max(MIN_SERVER_MEMORY_MB, host_total_mb - reserve)
