"""Environment -> Settings. Pure: no filesystem, no network, no database."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from taxi_mssql.sql import SqlIdentifierError, validate_identifier

DATA_TYPES = ("yellow", "green", "fhv", "fhvhv")

class ConfigError(Exception):
    """Bad or missing configuration. Maps to exit code 2."""


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    types: tuple[str, ...]
    recent: int | None
    password: str
    host: str
    port: int
    database: str
    schema: str
    user: str
    data_factor: float
    data_size_gb: int | None
    log_size_gb: int | None
    filegrowth_mb: int
    progress_interval_s: int
    mssql_max_memory_mb: int | None

    @property
    def raw_normalized_dir(self) -> Path:
        return self.data_dir / "raw-normalized"

    @property
    def mssql_data_dir(self) -> Path:
        return self.data_dir / "mssql" / "data"

    @property
    def mssql_log_dir(self) -> Path:
        return self.data_dir / "mssql" / "log"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "mssql" / "backup"

    @property
    def mdf_path(self) -> Path:
        return self.mssql_data_dir / f"{self.database}.mdf"

    @property
    def ldf_path(self) -> Path:
        return self.mssql_log_dir / f"{self.database}_log.ldf"


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from e


def _identifier(env: Mapping[str, str], key: str, default: str) -> str:
    """A setting that ends up as a T-SQL identifier, validated here.

    Deferring this to the SQL builders means a name like `taxi-db` -- legal in
    SQL Server, rejected by sql._IDENT_RE -- passes preflight, runs the mirror
    stage for hours, and only then dies in provision. And it would die as a
    SqlIdentifierError, which cli.main's config block does not catch. Fail at
    config time, as a ConfigError, before anything has been done.
    """
    value = (env.get(key) or "").strip() or default
    try:
        return validate_identifier(value, key)
    except SqlIdentifierError as e:
        raise ConfigError(f"{e} (it is used as a T-SQL identifier)") from e


def _types(raw: str) -> tuple[str, ...]:
    wanted = [t.strip() for t in raw.split(",") if t.strip()]
    unknown = [t for t in wanted if t not in DATA_TYPES]
    if unknown:
        raise ConfigError(
            f"TAXI_TYPES contains unknown type(s): {', '.join(unknown)}. "
            f"Valid: {', '.join(DATA_TYPES)}"
        )
    if not wanted:
        raise ConfigError("TAXI_TYPES is empty")
    # Canonical order keeps sizing and reporting deterministic.
    return tuple(t for t in DATA_TYPES if t in wanted)


def from_env(env: Mapping[str, str]) -> Settings:
    password = (env.get("MSSQL_PASSWORD") or "").strip()
    if not password:
        raise ConfigError("MSSQL_PASSWORD environment variable is required")

    recent_raw = (env.get("TAXI_RECENT") or "").strip()
    recent = None
    if recent_raw:
        recent = _int(env, "TAXI_RECENT", 0)
        if recent < 1:
            raise ConfigError(f"TAXI_RECENT must be >= 1, got {recent}")

    factor_raw = (env.get("TAXI_DATA_FACTOR") or "").strip() or "3.0"
    try:
        factor = float(factor_raw)
    except ValueError as e:
        raise ConfigError(f"TAXI_DATA_FACTOR must be a number, got {factor_raw!r}") from e
    if factor <= 0:
        raise ConfigError(f"TAXI_DATA_FACTOR must be > 0, got {factor}")

    data_size_raw = (env.get("MSSQL_DATA_SIZE_GB") or "").strip()
    data_size_gb = _int(env, "MSSQL_DATA_SIZE_GB", 0) if data_size_raw else None
    if data_size_gb is not None and data_size_gb < 1:
        raise ConfigError(f"MSSQL_DATA_SIZE_GB must be >= 1, got {data_size_gb}")

    log_size_raw = (env.get("MSSQL_LOG_SIZE_GB") or "").strip()
    log_size_gb = _int(env, "MSSQL_LOG_SIZE_GB", 0) if log_size_raw else None
    if log_size_gb is not None and log_size_gb < 1:
        raise ConfigError(f"MSSQL_LOG_SIZE_GB must be >= 1, got {log_size_gb}")

    max_mem_raw = (env.get("MSSQL_MAX_MEMORY_MB") or "").strip()
    mssql_max_memory_mb = _int(env, "MSSQL_MAX_MEMORY_MB", 0) if max_mem_raw else None
    if mssql_max_memory_mb is not None and mssql_max_memory_mb < 1024:
        raise ConfigError(
            f"MSSQL_MAX_MEMORY_MB must be >= 1024, got {mssql_max_memory_mb}"
        )

    progress_interval_s = _int(env, "TAXI_PROGRESS_INTERVAL_S", 60)
    if progress_interval_s < 0:
        raise ConfigError(
            f"TAXI_PROGRESS_INTERVAL_S must be >= 0 (0 disables the heartbeat), "
            f"got {progress_interval_s}"
        )

    return Settings(
        data_dir=Path(env.get("TAXI_DATA_DIR") or "/data"),
        types=_types(env.get("TAXI_TYPES") or ",".join(DATA_TYPES)),
        recent=recent,
        password=password,
        host=env.get("MSSQL_HOST") or "mssql",
        port=_int(env, "MSSQL_PORT", 1433),
        database=_identifier(env, "MSSQL_DATABASE", "taxi"),
        schema=_identifier(env, "MSSQL_SCHEMA", "dbo"),
        # Not validated as an identifier: the login only ever reaches sqlcmd's
        # -U and taxi-load's --user, never a SQL string, and real logins may
        # contain characters _IDENT_RE rejects.
        user=(env.get("MSSQL_USER") or "").strip() or "sa",
        data_factor=factor,
        data_size_gb=data_size_gb,
        log_size_gb=log_size_gb,
        filegrowth_mb=_int(env, "MSSQL_FILEGROWTH_MB", 1024),
        progress_interval_s=progress_interval_s,
        mssql_max_memory_mb=mssql_max_memory_mb,
    )
