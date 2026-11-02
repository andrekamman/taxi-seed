# Seeder Container Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Docker Compose stack that advances one bind-mounted folder toward a complete deliverable set — raw parquet, normalized parquet, a presized page-compressed SQL Server database, and a verified compressed Ola Hallengren backup — resuming from wherever the last run stopped.

**Architecture:** Two services share one bind mount at `/data`. The `seeder` service is a Python CLI that runs seven ordered steps, shelling out to taxi-seed's CLIs (`taxi-run`, `taxi-load`) for the pipeline and to `sqlcmd` for everything database-shaped. All decision logic is pure and unit-tested; a thin execution layer runs what the pure functions produce.

**Tech Stack:** Python 3.12, Docker Compose, SQL Server 2022 (Developer edition), `mssql-tools18`/`sqlcmd`, Ola Hallengren MaintenanceSolution, taxi-seed CLIs, pytest.

**Spec:** [`docs/specs/2026-08-01-seeder-container-design.md`](../specs/2026-08-01-seeder-container-design.md)

## Global Constraints

- **Exit codes mirror taxi-seed exactly:** `0` clean, `1` needs-review, `2` operational failure. Higher wins when aggregating (`2 > 1 > 0`).
- **Database name:** `taxi`. **Schema:** `dbo`. **SQL Server host (from seeder):** `mssql`, port `1433`, user `sa`.
- **Container hostname for mssql MUST be `taxi-sql`** — Ola's backup path is `<Directory>/<@@SERVERNAME>/<db>/<type>/`, and an unpinned hostname is random per run, so cleanup would never find the previous backup.
- **Recovery model:** `SIMPLE`.
- **Default `TAXI_TYPES`:** `yellow,green,fhv,fhvhv`. **Default `TAXI_DATA_FACTOR`:** `3.0`. **Default `MSSQL_LOG_SIZE_GB`:** `16`. **Default `MSSQL_FILEGROWTH_MB`:** `1024`. **Sizing granularity:** round up to the next multiple of `10 GB`. **Autogrowth warning threshold:** implied > allocated × `1.25`.
- **There is no year-range variable.** `taxi-download` exposes only `data_type`, `--recent N`, `--data-dir`.
- **Passwords never appear in argv.** `sqlcmd` reads `SQLCMDPASSWORD` from the environment; subprocess calls to taxi-seed CLIs pass `MSSQL_PASSWORD` via env.
- **Ola is installed with `@CreateJobs = 'N'`** — SQL Agent does not run in the container.
- **Backup call is fixed:** `@BackupType='FULL'`, `@Compress='Y'`, `@CheckSum='Y'`, `@Verify='Y'`, `@CleanupTime=1`, `@CleanupMode='AFTER_BACKUP'`.
- **Everything runs with CWD `/data`**, because `normalize` resolves mappings from the relative path `normalize/mappings/<type>.yaml`.
- **Sizing sums only the selected types**, never the whole `raw-normalized/` directory.
- **Prerequisite:** Task 1 must land and be released before Tasks 12–13 can pass.

## File Structure

```
seeder/
  Dockerfile                        image: python + mssql-tools18 + taxi-seed + seeder
  docker-compose.yml                the two services
  .env.example                      documented env template
  sql/MaintenanceSolution.sql       vendored Ola script, pristine
  src/taxi_seeder/
    __init__.py
    config.py       env -> Settings (pure)
    sizing.py       parquet bytes -> file sizes, growth warning (pure)
    sql.py          T-SQL string builders (pure)
    mssql.py        sqlcmd execution layer (injectable runner)
    stages.py       taxi-run / taxi-load subprocess wrappers
    preflight.py    directories, mappings seeding, wait-for-login
    provision.py    create-if-absent + autogrowth warning
    ola.py          MaintenanceSolution preparation + install
    backup.py       staleness test + DatabaseBackup invocation
    report.py       final summary
    cli.py          the seven-step sequence and exit-code aggregation
tests/taxi_seeder/
    test_config.py  test_sizing.py  test_sql.py  test_mssql.py
    test_stages.py  test_preflight.py  test_provision.py  test_ola.py
    test_backup.py  test_report.py  test_cli.py
    test_integration_compose.py
```

This refines the spec's illustrative module list by splitting the pure helpers (`config`, `sizing`, `sql`) out from the step modules, so the logic carrying the risk is testable without a database.

---

### Task 1: taxi-seed — page compression by default

**Repo: `taxi-seed`, not taxi-lab.** Nothing downstream can be integration-tested until this lands and is released.

**Files:**
- Modify: `shared/src/taxi_shared/sql_generator.py:39-41`
- Test: `tests/shared/test_sql_generator.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `generate_create_table_sql(table: str, columns: dict[str, str]) -> str` now ends with `) WITH (DATA_COMPRESSION = PAGE);`

- [ ] **Step 1: Write the failing test**

In `tests/shared/test_sql_generator.py`:

```python
def test_create_table_is_page_compressed():
    sql = generate_create_table_sql("dbo.yellow_2015", {"a": "BIGINT"})
    assert sql.endswith(") WITH (DATA_COMPRESSION = PAGE);")


def test_create_table_still_lists_columns():
    sql = generate_create_table_sql("dbo.t", {"a": "BIGINT", "b": "DATETIME2"})
    assert "a BIGINT" in sql
    assert "b DATETIME2" in sql
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/shared/test_sql_generator.py -v`
Expected: FAIL — `test_create_table_is_page_compressed`, because the current output ends `\n);`

- [ ] **Step 3: Write minimal implementation**

Replace the body of `generate_create_table_sql`:

```python
def generate_create_table_sql(table: str, columns: dict[str, str]) -> str:
    col_defs = [f"    {name} {sql_type}" for name, sql_type in columns.items()]
    return (
        f"CREATE TABLE {table} (\n{',\n'.join(col_defs)}\n"
        f") WITH (DATA_COMPRESSION = PAGE);"
    )
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS. `loader/src/taxi_loader/load.py:35` strips the trailing `;` via `.rstrip(";\n ")` before `mssql_exec`, which leaves `... ) WITH (DATA_COMPRESSION = PAGE)` — still one valid statement. `manifest.py:41` splices the PK constraint in via `removesuffix(")")`; that now operates on a string ending `PAGE)`, so **fix any manifest test that breaks** by updating `build_manifest_ddl` to strip the compression clause before splicing:

```python
    body = create.removesuffix(";").removesuffix(" WITH (DATA_COMPRESSION = PAGE)")
    body = body.removesuffix(")").rstrip()
```

and re-append it after the constraint:

```python
    ddl = (
        f"{body},\n"
        f"    CONSTRAINT {pk_name} PRIMARY KEY (data_type, year, month)\n"
        f") WITH (DATA_COMPRESSION = PAGE)"
    )
```

- [ ] **Step 5: Commit and release**

```bash
git add shared/src/taxi_shared/sql_generator.py loader/src/taxi_loader/manifest.py tests/
git commit -m "feat: page-compress generated tables by default"
git tag v0.2.0
git push origin main --tags
```

Confirm the release wheel no longer ships `k6_loadtest` (removed in `2a8909f`, never released):

```bash
python -c "import zipfile,glob; print([n for n in zipfile.ZipFile(glob.glob('dist/*.whl')[0]).namelist() if 'k6' in n] or 'clean')"
```

Then in **taxi-lab**, replace the `[tool.uv.sources]` block in `pyproject.toml` with a pin: `"taxi-seed>=0.2.0"`, and run `uv lock && uv sync --extra test`.

---

### Task 2: Seeder package scaffold and configuration

**Files:**
- Create: `seeder/src/taxi_seeder/__init__.py`, `seeder/src/taxi_seeder/config.py`
- Modify: `pyproject.toml`
- Test: `tests/taxi_seeder/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings` frozen dataclass with fields `data_dir: Path`, `types: tuple[str, ...]`, `recent: int | None`, `password: str`, `host: str`, `port: int`, `database: str`, `schema: str`, `user: str`, `data_factor: float`, `data_size_gb: int | None`, `log_size_gb: int`, `filegrowth_mb: int`; `from_env(env: Mapping[str, str]) -> Settings`; `ConfigError(Exception)`. Properties: `raw_normalized_dir`, `mssql_data_dir`, `mssql_log_dir`, `backup_dir`, `mdf_path`, `ldf_path`.

- [ ] **Step 1: Write the failing test**

Create `tests/taxi_seeder/test_config.py`:

```python
import pytest
from pathlib import Path

from taxi_seeder.config import ConfigError, Settings, from_env

BASE = {"MSSQL_PASSWORD": "pw", "TAXI_DATA_DIR": "/data"}


def test_defaults():
    s = from_env(BASE)
    assert s.types == ("yellow", "green", "fhv", "fhvhv")
    assert s.recent is None
    assert s.data_factor == 3.0
    assert s.log_size_gb == 16
    assert s.filegrowth_mb == 1024
    assert s.data_size_gb is None
    assert s.database == "taxi"
    assert s.host == "mssql"


def test_password_is_required():
    with pytest.raises(ConfigError, match="MSSQL_PASSWORD"):
        from_env({})


def test_types_are_parsed_and_ordered_canonically():
    s = from_env({**BASE, "TAXI_TYPES": "fhv, yellow"})
    assert s.types == ("yellow", "fhv")


def test_unknown_type_rejected():
    with pytest.raises(ConfigError, match="banana"):
        from_env({**BASE, "TAXI_TYPES": "banana"})


def test_recent_parsed():
    assert from_env({**BASE, "TAXI_RECENT": "3"}).recent == 3


def test_blank_recent_means_full_history():
    assert from_env({**BASE, "TAXI_RECENT": ""}).recent is None


def test_recent_must_be_positive():
    with pytest.raises(ConfigError, match="TAXI_RECENT"):
        from_env({**BASE, "TAXI_RECENT": "0"})


def test_factor_must_be_positive():
    with pytest.raises(ConfigError, match="TAXI_DATA_FACTOR"):
        from_env({**BASE, "TAXI_DATA_FACTOR": "0"})


def test_explicit_data_size_overrides_estimate():
    assert from_env({**BASE, "MSSQL_DATA_SIZE_GB": "500"}).data_size_gb == 500


def test_derived_paths():
    s = from_env(BASE)
    assert s.raw_normalized_dir == Path("/data/raw-normalized")
    assert s.mdf_path == Path("/data/mssql/data/taxi.mdf")
    assert s.ldf_path == Path("/data/mssql/log/taxi_log.ldf")
    assert s.backup_dir == Path("/data/mssql/backup")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/taxi_seeder/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_seeder'`

- [ ] **Step 3: Write minimal implementation**

Create `seeder/src/taxi_seeder/__init__.py` (empty) and `seeder/src/taxi_seeder/config.py`:

```python
"""Environment -> Settings. Pure: no filesystem, no network, no database."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

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
    log_size_gb: int
    filegrowth_mb: int

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

    return Settings(
        data_dir=Path(env.get("TAXI_DATA_DIR") or "/data"),
        types=_types(env.get("TAXI_TYPES") or ",".join(DATA_TYPES)),
        recent=recent,
        password=password,
        host=env.get("MSSQL_HOST") or "mssql",
        port=_int(env, "MSSQL_PORT", 1433),
        database=env.get("MSSQL_DATABASE") or "taxi",
        schema=env.get("MSSQL_SCHEMA") or "dbo",
        user=env.get("MSSQL_USER") or "sa",
        data_factor=factor,
        data_size_gb=data_size_gb,
        log_size_gb=_int(env, "MSSQL_LOG_SIZE_GB", 16),
        filegrowth_mb=_int(env, "MSSQL_FILEGROWTH_MB", 1024),
    )
```

- [ ] **Step 4: Register the package**

In `pyproject.toml`, add to `[tool.hatch.build.targets.wheel]` packages:

```toml
packages = [
  "k6-loadtest/src/k6_loadtest",
  "seeder/src/taxi_seeder",
]
```

and add to `[project.scripts]`:

```toml
taxi-seeder = "taxi_seeder.cli:main"
```

Then: `uv sync --extra test`

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/taxi_seeder/test_config.py -v`
Expected: PASS (11 tests)

- [ ] **Step 6: Commit**

```bash
git add seeder/src/taxi_seeder/ tests/taxi_seeder/test_config.py pyproject.toml uv.lock
git commit -m "feat(seeder): settings parsed from environment"
```

---

### Task 3: Sizing

**Files:**
- Create: `seeder/src/taxi_seeder/sizing.py`
- Test: `tests/taxi_seeder/test_sizing.py`

**Interfaces:**
- Consumes: `Settings` from Task 2.
- Produces: `parquet_bytes(root: Path, types: Iterable[str]) -> int`; `data_size_mb(parquet_bytes: int, factor: float, granularity_gb: int = 10) -> int`; `log_size_mb(log_size_gb: int) -> int`; `needs_growth_warning(implied_mb: int, allocated_mb: int, threshold: float = 1.25) -> bool`; `GRANULARITY_GB = 10`; `WARN_THRESHOLD = 1.25`.

- [ ] **Step 1: Write the failing test**

Create `tests/taxi_seeder/test_sizing.py`:

```python
from pathlib import Path

from taxi_seeder.sizing import (
    data_size_mb,
    log_size_mb,
    needs_growth_warning,
    parquet_bytes,
)

GB = 1024 ** 3


def _write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)


def test_parquet_bytes_sums_only_selected_types(tmp_path):
    _write(tmp_path / "yellow" / "2015" / "a.parquet", 100)
    _write(tmp_path / "yellow" / "2016" / "b.parquet", 50)
    _write(tmp_path / "green" / "2015" / "c.parquet", 999)
    assert parquet_bytes(tmp_path, ["yellow"]) == 150


def test_parquet_bytes_ignores_non_parquet(tmp_path):
    _write(tmp_path / "yellow" / "a.parquet", 100)
    _write(tmp_path / "yellow" / "notes.txt", 500)
    assert parquet_bytes(tmp_path, ["yellow"]) == 100


def test_parquet_bytes_tolerates_missing_type_dir(tmp_path):
    assert parquet_bytes(tmp_path, ["yellow", "fhvhv"]) == 0


def test_data_size_rounds_up_to_next_10gb():
    # 4 GB of parquet x 3.0 = 12 GB -> 20 GB
    assert data_size_mb(4 * GB, 3.0) == 20 * 1024


def test_data_size_exact_multiple_is_not_inflated():
    # 10 GB x 3.0 = 30 GB, already a multiple of 10
    assert data_size_mb(10 * GB, 3.0) == 30 * 1024


def test_data_size_has_a_one_granule_floor():
    assert data_size_mb(1, 3.0) == 10 * 1024


def test_data_size_of_empty_input_still_floors():
    assert data_size_mb(0, 3.0) == 10 * 1024


def test_log_size_mb():
    assert log_size_mb(16) == 16 * 1024


def test_growth_warning_fires_above_threshold():
    assert needs_growth_warning(implied_mb=1300, allocated_mb=1000) is True


def test_growth_warning_silent_at_threshold():
    assert needs_growth_warning(implied_mb=1250, allocated_mb=1000) is False


def test_growth_warning_silent_when_allocated_is_larger():
    assert needs_growth_warning(implied_mb=500, allocated_mb=1000) is False


def test_growth_warning_silent_when_allocation_unknown():
    assert needs_growth_warning(implied_mb=5000, allocated_mb=0) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/taxi_seeder/test_sizing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_seeder.sizing'`

- [ ] **Step 3: Write minimal implementation**

Create `seeder/src/taxi_seeder/sizing.py`:

```python
"""Turn normalized-parquet bytes into SQL Server file sizes. Pure arithmetic."""
from __future__ import annotations

import math
from collections.abc import Iterable
from pathlib import Path

GB = 1024 ** 3
GRANULARITY_GB = 10
WARN_THRESHOLD = 1.25


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
    """Estimated mdf size in MB, rounded up to the next granularity boundary.

    Floors at one granule so a tiny or empty mirror still gets a usable file.
    """
    raw_gb = parquet_bytes * factor / GB
    granules = max(1, math.ceil(raw_gb / granularity_gb))
    return granules * granularity_gb * 1024


def log_size_mb(log_size_gb: int) -> int:
    return log_size_gb * 1024


def needs_growth_warning(implied_mb: int, allocated_mb: int,
                         threshold: float = WARN_THRESHOLD) -> bool:
    """True when the existing mdf is materially smaller than this run implies.

    allocated_mb == 0 means the allocation could not be read; stay silent rather
    than warn on missing information.
    """
    if allocated_mb <= 0:
        return False
    return implied_mb > allocated_mb * threshold
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/taxi_seeder/test_sizing.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add seeder/src/taxi_seeder/sizing.py tests/taxi_seeder/test_sizing.py
git commit -m "feat(seeder): file sizing from normalized parquet bytes"
```

---

### Task 4: T-SQL builders

**Files:**
- Create: `seeder/src/taxi_seeder/sql.py`
- Test: `tests/taxi_seeder/test_sql.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `validate_identifier(name: str, what: str) -> str`; `SqlIdentifierError(Exception)`; `create_database(database, mdf_path, ldf_path, data_mb, log_mb, growth_mb) -> str`; `set_recovery_simple(database) -> str`; `database_exists(database) -> str`; `data_file_size_mb(database) -> str`; `ola_installed() -> str`; `max_loaded_at(schema) -> str`; `database_backup(database, directory) -> str`; `prepare_maintenance_solution(text: str, backup_directory: str) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/taxi_seeder/test_sql.py`:

```python
import pytest

from taxi_seeder import sql


def test_create_database_places_files_and_sizes_them():
    stmt = sql.create_database(
        "taxi", "/data/mssql/data/taxi.mdf", "/data/mssql/log/taxi_log.ldf",
        data_mb=20480, log_mb=16384, growth_mb=1024,
    )
    assert "CREATE DATABASE [taxi]" in stmt
    assert "FILENAME = N'/data/mssql/data/taxi.mdf'" in stmt
    assert "FILENAME = N'/data/mssql/log/taxi_log.ldf'" in stmt
    assert "SIZE = 20480MB" in stmt
    assert "SIZE = 16384MB" in stmt
    assert "FILEGROWTH = 1024MB" in stmt


def test_set_recovery_simple():
    assert sql.set_recovery_simple("taxi") == "ALTER DATABASE [taxi] SET RECOVERY SIMPLE"


def test_database_exists_query():
    assert "DB_ID('taxi')" in sql.database_exists("taxi")


def test_data_file_size_query_targets_the_rows_file():
    q = sql.data_file_size_mb("taxi")
    assert "sys.database_files" in q
    assert "type_desc = 'ROWS'" in q


def test_max_loaded_at_query_uses_the_schema():
    q = sql.max_loaded_at("dbo")
    assert "dbo._load_manifest" in q
    assert "MAX(loaded_at)" in q


def test_ola_installed_query():
    assert "dbo.DatabaseBackup" in sql.ola_installed()


def test_rows_per_type_query_groups_by_type():
    q = sql.rows_per_type("dbo")
    assert "dbo._load_manifest" in q
    assert "SUM(row_count)" in q
    assert "GROUP BY data_type" in q


def test_database_backup_uses_the_agreed_parameters():
    stmt = sql.database_backup("taxi", "/data/mssql/backup")
    for fragment in [
        "@Databases = N'taxi'",
        "@Directory = N'/data/mssql/backup'",
        "@BackupType = 'FULL'",
        "@Compress = 'Y'",
        "@CheckSum = 'Y'",
        "@Verify = 'Y'",
        "@CleanupTime = 1",
        "@CleanupMode = 'AFTER_BACKUP'",
    ]:
        assert fragment in stmt


def test_identifier_validation_rejects_injection():
    with pytest.raises(sql.SqlIdentifierError):
        sql.validate_identifier("taxi]; DROP DATABASE [x", "database")


def test_identifier_validation_accepts_normal_names():
    assert sql.validate_identifier("taxi", "database") == "taxi"


def test_create_database_rejects_a_bad_identifier():
    with pytest.raises(sql.SqlIdentifierError):
        sql.create_database("bad name", "/a.mdf", "/b.ldf", 1, 1, 1)


def test_paths_with_quotes_are_escaped():
    stmt = sql.create_database("taxi", "/data/o'brien.mdf", "/b.ldf", 1, 1, 1)
    assert "N'/data/o''brien.mdf'" in stmt


def test_prepare_maintenance_solution_disables_jobs_and_sets_directory():
    original = (
        "DECLARE @CreateJobs nvarchar(max)          = 'Y'   -- create jobs\n"
        "DECLARE @BackupDirectory nvarchar(max)     = NULL  -- backup root\n"
        "DECLARE @OutputFileDirectory nvarchar(max) = NULL\n"
    )
    out = sql.prepare_maintenance_solution(original, "/data/mssql/backup")
    assert "= 'N'" in out
    assert "= 'Y'" not in out
    assert "= N'/data/mssql/backup'" in out
    # alignment and trailing comments survive
    assert "-- create jobs" in out
    assert "-- backup root" in out


def test_prepare_maintenance_solution_is_idempotent():
    original = ("DECLARE @CreateJobs nvarchar(max) = 'Y'\n"
                "DECLARE @BackupDirectory nvarchar(max) = NULL\n")
    once = sql.prepare_maintenance_solution(original, "/b")
    assert sql.prepare_maintenance_solution(once, "/b") == once


def test_prepare_maintenance_solution_fails_loudly_if_markers_missing():
    with pytest.raises(sql.SqlIdentifierError, match="CreateJobs"):
        sql.prepare_maintenance_solution("-- nothing here\n", "/b")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/taxi_seeder/test_sql.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_seeder.sql'`

- [ ] **Step 3: Write minimal implementation**

Create `seeder/src/taxi_seeder/sql.py`:

```python
"""T-SQL string builders. Pure: these return SQL, they never execute it."""
from __future__ import annotations

import re

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SqlIdentifierError(Exception):
    """An identifier or vendored script failed validation. Exit code 2."""


def validate_identifier(name: str, what: str) -> str:
    if not _IDENT_RE.match(name or ""):
        raise SqlIdentifierError(
            f"invalid {what} {name!r}: must match [A-Za-z_][A-Za-z0-9_]*"
        )
    return name


def _lit(value: str) -> str:
    """Escape a value for a T-SQL single-quoted string literal."""
    return value.replace("'", "''")


def create_database(database: str, mdf_path: str, ldf_path: str,
                    data_mb: int, log_mb: int, growth_mb: int) -> str:
    db = validate_identifier(database, "database")
    return (
        f"CREATE DATABASE [{db}]\n"
        f"  ON PRIMARY (NAME = N'{db}', FILENAME = N'{_lit(str(mdf_path))}',\n"
        f"    SIZE = {int(data_mb)}MB, FILEGROWTH = {int(growth_mb)}MB)\n"
        f"  LOG ON (NAME = N'{db}_log', FILENAME = N'{_lit(str(ldf_path))}',\n"
        f"    SIZE = {int(log_mb)}MB, FILEGROWTH = {int(growth_mb)}MB)"
    )


def set_recovery_simple(database: str) -> str:
    return f"ALTER DATABASE [{validate_identifier(database, 'database')}] SET RECOVERY SIMPLE"


def database_exists(database: str) -> str:
    return f"SELECT CASE WHEN DB_ID('{_lit(database)}') IS NULL THEN 0 ELSE 1 END"


def data_file_size_mb(database: str) -> str:
    db = validate_identifier(database, "database")
    return (
        f"SELECT CAST(SUM(size) * 8 / 1024 AS BIGINT) "
        f"FROM [{db}].sys.database_files WHERE type_desc = 'ROWS'"
    )


def ola_installed() -> str:
    return "SELECT CASE WHEN OBJECT_ID('dbo.DatabaseBackup') IS NULL THEN 0 ELSE 1 END"


def max_loaded_at(schema: str) -> str:
    sch = validate_identifier(schema, "schema")
    return (
        f"SELECT CONVERT(VARCHAR(27), MAX(loaded_at), 126) "
        f"FROM {sch}._load_manifest"
    )


def rows_per_type(schema: str) -> str:
    sch = validate_identifier(schema, "schema")
    return (
        f"SELECT data_type, SUM(row_count) FROM {sch}._load_manifest "
        f"GROUP BY data_type ORDER BY data_type"
    )


def database_backup(database: str, directory: str) -> str:
    return (
        "EXECUTE dbo.DatabaseBackup "
        f"@Databases = N'{_lit(database)}', "
        f"@Directory = N'{_lit(str(directory))}', "
        "@BackupType = 'FULL', "
        "@Compress = 'Y', "
        "@CheckSum = 'Y', "
        "@Verify = 'Y', "
        "@CleanupTime = 1, "
        "@CleanupMode = 'AFTER_BACKUP'"
    )


def prepare_maintenance_solution(text: str, backup_directory: str) -> str:
    """Patch the vendored Ola script: no SQL Agent jobs, fixed backup directory.

    The file is vendored pristine so it stays diffable against upstream; the two
    edits happen here, where they are testable. Raises if either marker is
    absent, which is how an upstream reformat surfaces instead of silently
    creating SQL Agent jobs that cannot run in the container.
    """
    # The real MaintenanceSolution.sql declares these as
    #   DECLARE @CreateJobs nvarchar(max)      = 'Y'   -- trailing comment
    # not "SET @X = ...". The prefix is captured so the substitution replaces
    # only the value and leaves the alignment and trailing comment intact.
    # The quote-aware N'(?:[^']|'')*' keeps re-application idempotent, and
    # requiring the "= <value>" suffix skips the second, valueless
    # `DECLARE @BackupDirectory nvarchar(max)` inside a procedure body.
    jobs = re.compile(r"(DECLARE @CreateJobs nvarchar\(max\)\s*=\s*)'[YN]'")
    directory = re.compile(
        r"(DECLARE @BackupDirectory nvarchar\(max\)\s*=\s*)(?:NULL|N'(?:[^']|'')*')"
    )

    if not jobs.search(text):
        raise SqlIdentifierError(
            "vendored MaintenanceSolution.sql has no "
            "'DECLARE @CreateJobs nvarchar(max) = ...' line; "
            "re-vendor and re-check the patch markers"
        )
    if not directory.search(text):
        raise SqlIdentifierError(
            "vendored MaintenanceSolution.sql has no "
            "'DECLARE @BackupDirectory nvarchar(max) = ...' line; "
            "re-vendor and re-check the patch markers"
        )

    # Lambdas rather than template strings: a replacement containing a
    # backslash would otherwise be interpreted as a group reference.
    text = jobs.sub(lambda m: m.group(1) + "'N'", text, count=1)
    return directory.sub(
        lambda m: m.group(1) + f"N'{_lit(str(backup_directory))}'", text, count=1
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/taxi_seeder/test_sql.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add seeder/src/taxi_seeder/sql.py tests/taxi_seeder/test_sql.py
git commit -m "feat(seeder): T-SQL builders with identifier validation"
```

---

### Task 5: sqlcmd execution layer

**Files:**
- Create: `seeder/src/taxi_seeder/mssql.py`
- Test: `tests/taxi_seeder/test_mssql.py`

**Interfaces:**
- Consumes: `Settings` (Task 2).
- Produces: `MssqlError(Exception)`; `build_cmd(settings, *, database, sql=None, input_file=None) -> list[str]`; `run(settings, sql, *, database="master", runner=None) -> str`; `run_file(settings, path, *, database="master", runner=None) -> str`; `scalar(settings, sql, *, database="master", runner=None) -> str | None`; `wait_for_login(settings, *, timeout_s=300, sleep_s=5, runner=None, sleeper=None) -> None`. `runner` is `Callable[[list[str], dict[str, str], str | None], subprocess.CompletedProcess]` so tests inject a fake.

- [ ] **Step 1: Write the failing test**

Create `tests/taxi_seeder/test_mssql.py`:

```python
import subprocess

import pytest

from taxi_seeder import mssql
from taxi_seeder.config import from_env

SETTINGS = from_env({"MSSQL_PASSWORD": "s3cret", "TAXI_DATA_DIR": "/data"})


def fake_runner(result_stdout="", returncode=0, record=None):
    def run(cmd, env, stdin_text):
        if record is not None:
            record.append((cmd, env, stdin_text))
        return subprocess.CompletedProcess(cmd, returncode, result_stdout, "")
    return run


def test_password_is_never_in_argv():
    cmd = mssql.build_cmd(SETTINGS, database="master", sql="SELECT 1")
    assert "s3cret" not in " ".join(cmd)


def test_password_is_passed_via_environment():
    record = []
    mssql.run(SETTINGS, "SELECT 1", runner=fake_runner(record=record))
    _cmd, env, _stdin = record[0]
    assert env["SQLCMDPASSWORD"] == "s3cret"


def test_cmd_targets_host_port_and_database():
    cmd = mssql.build_cmd(SETTINGS, database="taxi", sql="SELECT 1")
    assert "-S" in cmd and "mssql,1433" in cmd
    assert "-d" in cmd and "taxi" in cmd
    assert "-C" in cmd  # trust the container's self-signed certificate
    assert "-b" in cmd  # non-zero exit on SQL error


def test_run_raises_on_failure():
    with pytest.raises(mssql.MssqlError, match="exit 1"):
        mssql.run(SETTINGS, "SELECT 1", runner=fake_runner(returncode=1))


def test_scalar_strips_and_returns_value():
    assert mssql.scalar(SETTINGS, "SELECT 1", runner=fake_runner("  7  \n")) == "7"


def test_scalar_returns_none_for_sql_null():
    assert mssql.scalar(SETTINGS, "SELECT NULL", runner=fake_runner("NULL\n")) is None


def test_scalar_returns_none_for_empty_output():
    assert mssql.scalar(SETTINGS, "SELECT NULL", runner=fake_runner("\n")) is None


def test_wait_for_login_returns_once_the_server_answers():
    attempts = {"n": 0}

    def flaky(cmd, env, stdin_text):
        attempts["n"] += 1
        rc = 0 if attempts["n"] >= 3 else 1
        return subprocess.CompletedProcess(cmd, rc, "1", "")

    mssql.wait_for_login(SETTINGS, timeout_s=60, sleep_s=0,
                         runner=flaky, sleeper=lambda _s: None)
    assert attempts["n"] == 3


def test_wait_for_login_gives_up_and_raises():
    elapsed = {"t": 0.0}

    def clock():
        return elapsed["t"]

    def sleeper(seconds):
        elapsed["t"] += 10

    with pytest.raises(mssql.MssqlError, match="not accept a login"):
        mssql.wait_for_login(SETTINGS, timeout_s=30, sleep_s=10,
                             runner=fake_runner(returncode=1),
                             sleeper=sleeper, clock=clock)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/taxi_seeder/test_mssql.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_seeder.mssql'`

- [ ] **Step 3: Write minimal implementation**

Create `seeder/src/taxi_seeder/mssql.py`:

```python
"""Run T-SQL via sqlcmd. The only module that talks to SQL Server."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from taxi_seeder.config import Settings

SQLCMD = os.environ.get("SQLCMD_PATH", "/opt/mssql-tools18/bin/sqlcmd")


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
        cmd += ["-Q", sql]
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


def scalar(settings: Settings, sql: str, *,
           database: str = "master", runner=None) -> str | None:
    out = run(settings, sql, database=database, runner=runner).strip()
    if not out or out == "NULL":
        return None
    return out.splitlines()[0].strip() or None


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/taxi_seeder/test_mssql.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add seeder/src/taxi_seeder/mssql.py tests/taxi_seeder/test_mssql.py
git commit -m "feat(seeder): sqlcmd execution layer with injectable runner"
```

---

### Task 6: Preflight

**Files:**
- Create: `seeder/src/taxi_seeder/preflight.py`
- Test: `tests/taxi_seeder/test_preflight.py`

**Interfaces:**
- Consumes: `Settings` (Task 2), `mssql.wait_for_login` (Task 5).
- Produces: `BAKED_MAPPINGS_DIR: Path`; `ensure_directories(settings) -> None`; `seed_mappings(settings, baked_dir=BAKED_MAPPINGS_DIR) -> list[str]`; `run(settings, *, baked_dir=BAKED_MAPPINGS_DIR, runner=None) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/taxi_seeder/test_preflight.py`:

```python
from pathlib import Path

from taxi_seeder import preflight
from taxi_seeder.config import from_env


def settings_for(tmp_path):
    return from_env({"MSSQL_PASSWORD": "pw", "TAXI_DATA_DIR": str(tmp_path)})


def baked(tmp_path):
    d = tmp_path / "baked"
    d.mkdir()
    for t in ("yellow", "green", "fhv", "fhvhv"):
        (d / f"{t}.yaml").write_text(f"# pristine {t}\n")
    return d


def test_ensure_directories_creates_the_full_layout(tmp_path):
    s = settings_for(tmp_path)
    preflight.ensure_directories(s)
    assert (tmp_path / "raw").is_dir()
    assert (tmp_path / "raw-normalized").is_dir()
    assert (tmp_path / "mssql" / "data").is_dir()
    assert (tmp_path / "mssql" / "log").is_dir()
    assert (tmp_path / "mssql" / "backup").is_dir()
    assert (tmp_path / "normalize" / "mappings").is_dir()


def test_ensure_directories_is_idempotent(tmp_path):
    s = settings_for(tmp_path)
    preflight.ensure_directories(s)
    preflight.ensure_directories(s)
    assert (tmp_path / "mssql" / "data").is_dir()


def test_seed_mappings_copies_when_absent(tmp_path):
    s = settings_for(tmp_path)
    preflight.ensure_directories(s)
    copied = preflight.seed_mappings(s, baked_dir=baked(tmp_path))
    assert sorted(copied) == ["fhv", "fhvhv", "green", "yellow"]
    assert (tmp_path / "normalize" / "mappings" / "yellow.yaml").read_text() == (
        "# pristine yellow\n"
    )


def test_seed_mappings_never_overwrites_an_amended_mapping(tmp_path):
    s = settings_for(tmp_path)
    preflight.ensure_directories(s)
    target = tmp_path / "normalize" / "mappings" / "yellow.yaml"
    target.write_text("# amended after schema drift\n")

    copied = preflight.seed_mappings(s, baked_dir=baked(tmp_path))

    assert "yellow" not in copied
    assert target.read_text() == "# amended after schema drift\n"


def test_seed_mappings_tolerates_a_missing_baked_dir(tmp_path):
    s = settings_for(tmp_path)
    preflight.ensure_directories(s)
    assert preflight.seed_mappings(s, baked_dir=tmp_path / "nope") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/taxi_seeder/test_preflight.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_seeder.preflight'`

- [ ] **Step 3: Write minimal implementation**

Create `seeder/src/taxi_seeder/preflight.py`:

```python
"""Step 1: make /data safe to work in, then prove SQL Server accepts a login."""
from __future__ import annotations

import shutil
from pathlib import Path

from taxi_seeder import mssql
from taxi_seeder.config import DATA_TYPES, Settings

BAKED_MAPPINGS_DIR = Path("/opt/taxi-seed/mappings")


def ensure_directories(settings: Settings) -> None:
    for d in (
        settings.data_dir / "raw",
        settings.raw_normalized_dir,
        settings.mssql_data_dir,
        settings.mssql_log_dir,
        settings.backup_dir,
        settings.data_dir / "normalize" / "mappings",
    ):
        d.mkdir(parents=True, exist_ok=True)


def seed_mappings(settings: Settings,
                  baked_dir: Path = BAKED_MAPPINGS_DIR) -> list[str]:
    """Copy pristine mappings onto the volume, never overwriting existing ones.

    `normalize` resolves mappings from the relative path
    normalize/mappings/<type>.yaml and amends them on schema drift. They live on
    the volume so those amendments survive the container; overwriting one would
    discard a drift acknowledgment and re-halt every run.
    """
    target_dir = settings.data_dir / "normalize" / "mappings"
    target_dir.mkdir(parents=True, exist_ok=True)
    if not baked_dir.is_dir():
        return []

    copied: list[str] = []
    for data_type in DATA_TYPES:
        source = baked_dir / f"{data_type}.yaml"
        target = target_dir / f"{data_type}.yaml"
        if source.is_file() and not target.exists():
            shutil.copyfile(source, target)
            copied.append(data_type)
    return copied


def run(settings: Settings, *, baked_dir: Path = BAKED_MAPPINGS_DIR,
        runner=None) -> None:
    ensure_directories(settings)
    copied = seed_mappings(settings, baked_dir=baked_dir)
    if copied:
        print(f"preflight: seeded mappings for {', '.join(copied)}")
    print(f"preflight: waiting for {settings.host}:{settings.port} to accept a login")
    mssql.wait_for_login(settings, runner=runner)
    print("preflight: SQL Server is reachable")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/taxi_seeder/test_preflight.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add seeder/src/taxi_seeder/preflight.py tests/taxi_seeder/test_preflight.py
git commit -m "feat(seeder): preflight directories, mappings seeding, login wait"
```

---

### Task 7: Provisioning and the autogrowth warning

**Files:**
- Create: `seeder/src/taxi_seeder/provision.py`
- Test: `tests/taxi_seeder/test_provision.py`

**Interfaces:**
- Consumes: `Settings` (2), `sizing` (3), `sql` (4), `mssql` (5).
- Produces: `implied_data_mb(settings) -> int`; `run(settings, *, runner=None) -> bool` (True when it created the database, False when it skipped).

- [ ] **Step 1: Write the failing test**

Create `tests/taxi_seeder/test_provision.py`:

```python
import subprocess

from taxi_seeder import provision
from taxi_seeder.config import from_env

GB = 1024 ** 3


def settings_for(tmp_path, **env):
    return from_env({"MSSQL_PASSWORD": "pw", "TAXI_DATA_DIR": str(tmp_path), **env})


def write_parquet(tmp_path, data_type, size):
    d = tmp_path / "raw-normalized" / data_type / "2015"
    d.mkdir(parents=True, exist_ok=True)
    (d / "a.parquet").write_bytes(b"\0" * size)


class Recorder:
    """Answers scalar queries from a script, records every statement."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.statements = []

    def __call__(self, cmd, env, stdin_text):
        sql = cmd[cmd.index("-Q") + 1] if "-Q" in cmd else ""
        self.statements.append(sql)
        out = self.answers.pop(0) if self.answers else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")


def test_creates_a_presized_database_when_absent(tmp_path):
    s = settings_for(tmp_path)
    write_parquet(tmp_path, "yellow", 4 * GB)
    rec = Recorder(["0"])  # database_exists -> 0

    created = provision.run(s, runner=rec)

    assert created is True
    joined = "\n".join(rec.statements)
    assert "CREATE DATABASE [taxi]" in joined
    assert "SIZE = 20480MB" in joined      # 4 GB x 3.0 = 12 -> 20 GB
    assert "SIZE = 16384MB" in joined      # log default
    assert "SET RECOVERY SIMPLE" in joined


def test_explicit_size_wins_over_the_estimate(tmp_path):
    s = settings_for(tmp_path, MSSQL_DATA_SIZE_GB="500")
    write_parquet(tmp_path, "yellow", 4 * GB)
    rec = Recorder(["0"])

    provision.run(s, runner=rec)

    assert "SIZE = 512000MB" in "\n".join(rec.statements)


def test_sizing_counts_only_selected_types(tmp_path):
    s = settings_for(tmp_path, TAXI_TYPES="yellow")
    write_parquet(tmp_path, "yellow", 4 * GB)
    write_parquet(tmp_path, "green", 40 * GB)
    rec = Recorder(["0"])

    provision.run(s, runner=rec)

    assert "SIZE = 20480MB" in "\n".join(rec.statements)


def test_skips_creation_when_the_database_exists(tmp_path):
    s = settings_for(tmp_path)
    write_parquet(tmp_path, "yellow", 1 * GB)
    rec = Recorder(["1", "10240"])  # exists -> 1, allocated 10 GB

    created = provision.run(s, runner=rec)

    assert created is False
    assert "CREATE DATABASE" not in "\n".join(rec.statements)


def test_warns_when_the_existing_file_is_too_small(tmp_path, capsys):
    s = settings_for(tmp_path)
    write_parquet(tmp_path, "yellow", 40 * GB)  # implies 120 GB
    rec = Recorder(["1", "10240"])              # allocated 10 GB

    provision.run(s, runner=rec)

    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "autogrow" in out.lower()
    assert "MSSQL_DATA_SIZE_GB" in out


def test_stays_quiet_when_the_existing_file_is_ample(tmp_path, capsys):
    s = settings_for(tmp_path)
    write_parquet(tmp_path, "yellow", 1 * GB)   # implies 10 GB
    rec = Recorder(["1", "102400"])             # allocated 100 GB

    provision.run(s, runner=rec)

    assert "WARNING" not in capsys.readouterr().out


def test_stays_quiet_when_the_allocation_cannot_be_read(tmp_path, capsys):
    s = settings_for(tmp_path)
    write_parquet(tmp_path, "yellow", 40 * GB)
    rec = Recorder(["1", "NULL"])

    provision.run(s, runner=rec)

    assert "WARNING" not in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/taxi_seeder/test_provision.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_seeder.provision'`

- [ ] **Step 3: Write minimal implementation**

Create `seeder/src/taxi_seeder/provision.py`:

```python
"""Step 3: create the presized database, or explain why it was skipped."""
from __future__ import annotations

from taxi_seeder import mssql, sizing, sql
from taxi_seeder.config import Settings


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


def run(settings: Settings, *, runner=None) -> bool:
    """Create the database if absent. Returns True when it created it."""
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
                f"           Set MSSQL_DATA_SIZE_GB on a fresh database, or delete "
                f"{settings.mdf_path} and {settings.ldf_path} and re-run to re-presize "
                f"and reload from parquet."
            )
        else:
            print(f"provision: database {settings.database!r} exists, skipping")
        return False

    log_mb = sizing.log_size_mb(settings.log_size_gb)
    print(
        f"provision: creating {settings.database!r} with "
        f"{data_mb / 1024:.0f} GB data + {log_mb / 1024:.0f} GB log"
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/taxi_seeder/test_provision.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add seeder/src/taxi_seeder/provision.py tests/taxi_seeder/test_provision.py
git commit -m "feat(seeder): presized provisioning with autogrowth warning"
```

---

### Task 8: Ola Hallengren installation

**Files:**
- Create: `seeder/src/taxi_seeder/ola.py`, `seeder/sql/MaintenanceSolution.sql`, `seeder/sql/VERSION`
- Modify: `pyproject.toml` (force-include, see Step 1a)
- Test: `tests/taxi_seeder/test_ola.py`

**Interfaces:**
- Consumes: `Settings` (2), `sql.prepare_maintenance_solution` (4), `mssql` (5).
- Produces: `VENDORED_SCRIPT: Path`; `is_installed(settings, *, runner=None) -> bool`; `run(settings, *, script=VENDORED_SCRIPT, runner=None) -> bool` (True when it installed).

- [ ] **Step 1: Vendor the script**

```bash
mkdir -p seeder/sql
curl -fsSL -o seeder/sql/MaintenanceSolution.sql \
  https://raw.githubusercontent.com/olahallengren/sql-server-maintenance-solution/20260723_232218/MaintenanceSolution.sql
grep -c "DECLARE @CreateJobs nvarchar" seeder/sql/MaintenanceSolution.sql   # expect 1
grep -cE "DECLARE @BackupDirectory nvarchar\(max\)\s*=" seeder/sql/MaintenanceSolution.sql  # expect 1
printf '20260723_232218\n' > seeder/sql/VERSION
```

The file stays pristine so it remains diffable against upstream; the two edits happen in `prepare_maintenance_solution` (Task 4).

- [ ] **Step 1a: Ship `sql/` as package data**

`seeder/sql/` is outside `src/`, so the wheel would not contain it and the container would resolve `VENDORED_SCRIPT` to a path that does not exist — a failure no unit test catches, because the tests inject `script=`. Add to `pyproject.toml`:

```toml
[tool.hatch.build.targets.wheel.force-include]
"seeder/sql" = "taxi_seeder/sql"
```

Then `uv sync --extra test` and confirm the file resolves through the installed package:

```bash
uv run python -c "from taxi_seeder.ola import VENDORED_SCRIPT; print(VENDORED_SCRIPT, VENDORED_SCRIPT.is_file())"
```

Expected: a path ending `taxi_seeder/sql/MaintenanceSolution.sql` and `True`.

- [ ] **Step 2: Write the failing test**

Create `tests/taxi_seeder/test_ola.py`:

```python
import subprocess

from taxi_seeder import ola
from taxi_seeder.config import from_env

SETTINGS = from_env({"MSSQL_PASSWORD": "pw", "TAXI_DATA_DIR": "/data"})


class Recorder:
    """Captures the -i file's CONTENT at call time: ola.run deletes the temp
    file before returning, so reading the path afterwards would fail."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.statements = []
        self.file_contents = []

    def __call__(self, cmd, env, stdin_text):
        if "-Q" in cmd:
            self.statements.append(cmd[cmd.index("-Q") + 1])
        if "-i" in cmd:
            self.file_contents.append(open(cmd[cmd.index("-i") + 1]).read())
        out = self.answers.pop(0) if self.answers else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")


def vendored(tmp_path):
    p = tmp_path / "MaintenanceSolution.sql"
    p.write_text("DECLARE @CreateJobs nvarchar(max) = 'Y'\n"
                 "DECLARE @BackupDirectory nvarchar(max) = NULL\nGO\n")
    return p


def test_skips_when_already_installed(tmp_path):
    rec = Recorder(["1"])
    installed = ola.run(SETTINGS, script=vendored(tmp_path), runner=rec)
    assert installed is False
    assert rec.file_contents == []


def test_installs_a_patched_copy_when_absent(tmp_path):
    rec = Recorder(["0"])
    installed = ola.run(SETTINGS, script=vendored(tmp_path), runner=rec)

    assert installed is True
    assert len(rec.file_contents) == 1
    assert "DECLARE @CreateJobs nvarchar(max) = 'N'" in rec.file_contents[0]
    assert (
        "DECLARE @BackupDirectory nvarchar(max) = N'/data/mssql/backup'"
        in rec.file_contents[0]
    )


def test_the_vendored_file_is_left_untouched(tmp_path):
    script = vendored(tmp_path)
    before = script.read_text()
    ola.run(SETTINGS, script=script, runner=Recorder(["0"]))
    assert script.read_text() == before


def test_is_installed_reads_the_object_id_probe():
    rec = Recorder(["1"])
    assert ola.is_installed(SETTINGS, runner=rec) is True
    assert "dbo.DatabaseBackup" in rec.statements[0]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/taxi_seeder/test_ola.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_seeder.ola'`

- [ ] **Step 4: Write minimal implementation**

Create `seeder/src/taxi_seeder/ola.py`:

```python
"""Step 4: install Ola Hallengren's MaintenanceSolution into master, once."""
from __future__ import annotations

import tempfile
from pathlib import Path

from taxi_seeder import mssql, sql
from taxi_seeder.config import Settings

# seeder/sql is force-included into the wheel as taxi_seeder/sql (see
# pyproject.toml), so this one hop resolves identically in an editable dev
# install, a built wheel, and the container image.
VENDORED_SCRIPT = Path(__file__).resolve().parent / "sql" / "MaintenanceSolution.sql"


def is_installed(settings: Settings, *, runner=None) -> bool:
    return mssql.scalar(settings, sql.ola_installed(),
                        database="master", runner=runner) == "1"


def run(settings: Settings, *, script: Path = VENDORED_SCRIPT, runner=None) -> bool:
    """Install if dbo.DatabaseBackup is absent. Returns True when it installed."""
    if is_installed(settings, runner=runner):
        print("ola: MaintenanceSolution already installed, skipping")
        return False

    patched = sql.prepare_maintenance_solution(
        Path(script).read_text(), str(settings.backup_dir)
    )
    # sqlcmd -i is required: the script is batch-separated by GO, which only the
    # client understands. The patched copy is temporary; the vendored file on
    # disk stays pristine and diffable against upstream.
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as fh:
        fh.write(patched)
        temp_path = fh.name
    try:
        print("ola: installing MaintenanceSolution (@CreateJobs = 'N')")
        mssql.run_file(settings, temp_path, database="master", runner=runner)
    finally:
        Path(temp_path).unlink(missing_ok=True)
    return True
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/taxi_seeder/test_ola.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add seeder/src/taxi_seeder/ola.py seeder/sql/ tests/taxi_seeder/test_ola.py
git commit -m "feat(seeder): vendored Ola MaintenanceSolution, installed once"
```

---

### Task 9: taxi-seed CLI stages

**Files:**
- Create: `seeder/src/taxi_seeder/stages.py`
- Test: `tests/taxi_seeder/test_stages.py`

**Interfaces:**
- Consumes: `Settings` (2).
- Produces: `mirror_cmd(settings, data_type) -> list[str]`; `load_cmd(settings, data_type) -> list[str]`; `run(cmd, settings, *, runner=None) -> int`; `mirror(settings, data_type, *, runner=None) -> int`; `load(settings, data_type, *, runner=None) -> int`.

- [ ] **Step 1: Write the failing test**

Create `tests/taxi_seeder/test_stages.py`:

```python
import subprocess

from taxi_seeder import stages
from taxi_seeder.config import from_env


def settings_for(**env):
    return from_env({"MSSQL_PASSWORD": "pw", "TAXI_DATA_DIR": "/data", **env})


def test_mirror_omits_load_so_provisioning_can_interpose():
    cmd = stages.mirror_cmd(settings_for(), "yellow")
    assert cmd[:2] == ["taxi-run", "yellow"]
    assert "--data-dir" in cmd and "/data" in cmd
    assert "--load" not in cmd


def test_mirror_passes_recent_when_set():
    cmd = stages.mirror_cmd(settings_for(TAXI_RECENT="3"), "yellow")
    assert "--recent" in cmd
    assert cmd[cmd.index("--recent") + 1] == "3"


def test_mirror_omits_recent_for_full_history():
    assert "--recent" not in stages.mirror_cmd(settings_for(), "yellow")


def test_load_targets_the_compose_service():
    cmd = stages.load_cmd(settings_for(), "yellow")
    assert cmd[:2] == ["taxi-load", "yellow"]
    assert cmd[cmd.index("--host") + 1] == "mssql"
    assert cmd[cmd.index("--port") + 1] == "1433"
    assert cmd[cmd.index("--database") + 1] == "taxi"
    assert cmd[cmd.index("--data-dir") + 1] == "/data"


def test_password_reaches_the_subprocess_via_env_not_argv():
    captured = {}

    def runner(cmd, env, cwd):
        captured["cmd"] = cmd
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 0, "", "")

    stages.load(settings_for(), "yellow", runner=runner)

    assert "pw" not in " ".join(captured["cmd"])
    assert captured["env"]["MSSQL_PASSWORD"] == "pw"


def test_run_executes_with_cwd_data_dir_for_mapping_resolution():
    captured = {}

    def runner(cmd, env, cwd):
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    stages.mirror(settings_for(), "yellow", runner=runner)

    assert captured["cwd"] == "/data"


def test_run_returns_the_child_exit_code():
    def runner(cmd, env, cwd):
        return subprocess.CompletedProcess(cmd, 1, "", "")

    assert stages.mirror(settings_for(), "yellow", runner=runner) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/taxi_seeder/test_stages.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_seeder.stages'`

- [ ] **Step 3: Write minimal implementation**

Create `seeder/src/taxi_seeder/stages.py`:

```python
"""Steps 2 and 5: shell out to taxi-seed's CLIs.

Coupled to the documented interface and exit codes, not to taxi-seed internals.
"""
from __future__ import annotations

import os
import subprocess

from taxi_seeder.config import Settings


def _default_runner(cmd, env, cwd):
    return subprocess.run(cmd, env=env, cwd=cwd, check=False)


def mirror_cmd(settings: Settings, data_type: str) -> list[str]:
    """download -> normalize for one type. Deliberately no --load: the database
    must be created presized first, and taxi-run's own ensure_database would
    create it with server-default paths and sizes."""
    cmd = ["taxi-run", data_type, "--data-dir", str(settings.data_dir)]
    if settings.recent is not None:
        cmd += ["--recent", str(settings.recent)]
    return cmd


def load_cmd(settings: Settings, data_type: str) -> list[str]:
    return [
        "taxi-load", data_type,
        "--data-dir", str(settings.data_dir),
        "--host", settings.host,
        "--port", str(settings.port),
        "--database", settings.database,
        "--schema", settings.schema,
        "--user", settings.user,
    ]


def run(cmd: list[str], settings: Settings, *, runner=None) -> int:
    runner = runner or _default_runner
    # CWD is the data dir because `normalize` resolves its mappings from the
    # relative path normalize/mappings/<type>.yaml.
    env = {**os.environ, "MSSQL_PASSWORD": settings.password}
    proc = runner(cmd, env, str(settings.data_dir))
    return proc.returncode


def mirror(settings: Settings, data_type: str, *, runner=None) -> int:
    return run(mirror_cmd(settings, data_type), settings, runner=runner)


def load(settings: Settings, data_type: str, *, runner=None) -> int:
    return run(load_cmd(settings, data_type), settings, runner=runner)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/taxi_seeder/test_stages.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add seeder/src/taxi_seeder/stages.py tests/taxi_seeder/test_stages.py
git commit -m "feat(seeder): taxi-run and taxi-load subprocess stages"
```

---

### Task 10: Backup staleness and invocation

**Files:**
- Create: `seeder/src/taxi_seeder/backup.py`
- Test: `tests/taxi_seeder/test_backup.py`

**Interfaces:**
- Consumes: `Settings` (2), `sql` (4), `mssql` (5).
- Produces: `newest_backup(settings) -> Path | None`; `backup_mtime(path) -> datetime | None`; `max_loaded_at(settings, *, runner=None) -> datetime | None`; `is_stale(max_loaded_at, newest_backup_mtime) -> bool`; `run(settings, *, runner=None) -> bool` (True when it took a backup).

- [ ] **Step 1: Write the failing test**

Create `tests/taxi_seeder/test_backup.py`:

```python
import os
import subprocess
from datetime import datetime, timedelta, timezone

from taxi_seeder import backup
from taxi_seeder.config import from_env

UTC = timezone.utc
T0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


def settings_for(tmp_path):
    return from_env({"MSSQL_PASSWORD": "pw", "TAXI_DATA_DIR": str(tmp_path)})


class Recorder:
    def __init__(self, answers):
        self.answers = list(answers)
        self.statements = []

    def __call__(self, cmd, env, stdin_text):
        if "-Q" in cmd:
            self.statements.append(cmd[cmd.index("-Q") + 1])
        out = self.answers.pop(0) if self.answers else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")


def make_bak(settings, when: datetime, name="taxi-sql_taxi_FULL_20260801_120000.bak"):
    d = settings.backup_dir / "taxi-sql" / "taxi" / "FULL"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(b"bak")
    os.utime(p, (when.timestamp(), when.timestamp()))
    return p


# --- the staleness rule ---

def test_no_backup_is_always_stale():
    assert backup.is_stale(T0, None) is True


def test_no_backup_and_nothing_loaded_is_still_stale():
    """The invariant: a successful run always leaves a .bak, even for an empty DB."""
    assert backup.is_stale(None, None) is True


def test_backup_newer_than_the_last_load_is_current():
    assert backup.is_stale(T0, T0 + timedelta(minutes=1)) is False


def test_load_newer_than_the_backup_is_stale():
    """The died-before-backup case: a run loaded rows then crashed. The next run
    loads nothing, so 'did this run load anything?' would never back up again."""
    assert backup.is_stale(T0, T0 - timedelta(minutes=1)) is True


def test_nothing_ever_loaded_but_a_backup_exists_is_current():
    assert backup.is_stale(None, T0) is False


def test_equal_timestamps_are_not_stale():
    assert backup.is_stale(T0, T0) is False


# --- discovery ---

def test_newest_backup_picks_the_latest_of_several(tmp_path):
    s = settings_for(tmp_path)
    make_bak(s, T0 - timedelta(hours=2), "a.bak")
    newest = make_bak(s, T0, "b.bak")
    assert backup.newest_backup(s) == newest


def test_newest_backup_is_none_when_absent(tmp_path):
    assert backup.newest_backup(settings_for(tmp_path)) is None


def test_backup_mtime_is_utc_aware(tmp_path):
    s = settings_for(tmp_path)
    p = make_bak(s, T0)
    assert backup.backup_mtime(p).tzinfo is not None


# --- manifest reading ---

def test_max_loaded_at_parses_iso_as_utc(tmp_path):
    rec = Recorder(["2026-08-01T12:00:00.000"])
    got = backup.max_loaded_at(settings_for(tmp_path), runner=rec)
    assert got == T0


def test_max_loaded_at_is_none_when_the_manifest_is_missing(tmp_path):
    def failing(cmd, env, stdin_text):
        return subprocess.CompletedProcess(cmd, 1, "", "Invalid object name")

    assert backup.max_loaded_at(settings_for(tmp_path), runner=failing) is None


def test_max_loaded_at_is_none_when_the_manifest_is_empty(tmp_path):
    assert backup.max_loaded_at(settings_for(tmp_path), runner=Recorder(["NULL"])) is None


# --- the step ---

def test_run_backs_up_when_stale(tmp_path):
    s = settings_for(tmp_path)
    rec = Recorder(["2026-08-01T12:00:00.000"])  # loaded, no .bak on disk
    assert backup.run(s, runner=rec) is True
    assert "DatabaseBackup" in "\n".join(rec.statements)


def test_run_skips_when_current(tmp_path):
    s = settings_for(tmp_path)
    make_bak(s, T0 + timedelta(hours=1))
    rec = Recorder(["2026-08-01T12:00:00.000"])
    assert backup.run(s, runner=rec) is False
    assert "DatabaseBackup" not in "\n".join(rec.statements)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/taxi_seeder/test_backup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_seeder.backup'`

- [ ] **Step 3: Write minimal implementation**

Create `seeder/src/taxi_seeder/backup.py`:

```python
"""Step 6: take a verified compressed FULL backup, but only when stale."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from taxi_seeder import mssql, sql
from taxi_seeder.config import Settings


def newest_backup(settings: Settings) -> Path | None:
    """The most recent .bak anywhere under the Ola directory root.

    Searched recursively rather than at a fixed path because Ola's layout is
    <Directory>/<@@SERVERNAME>/<database>/<type>/, and the server name is the
    container hostname.
    """
    if not settings.backup_dir.is_dir():
        return None
    baks = list(settings.backup_dir.rglob("*.bak"))
    if not baks:
        return None
    return max(baks, key=lambda p: p.stat().st_mtime)


def backup_mtime(path: Path) -> datetime | None:
    if path is None:
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def max_loaded_at(settings: Settings, *, runner=None) -> datetime | None:
    """Newest loaded_at in taxi-seed's _load_manifest, as an aware UTC datetime.

    Returns None when the table does not exist yet (nothing has ever been
    loaded) or when it is empty.
    """
    try:
        raw = mssql.scalar(settings, sql.max_loaded_at(settings.schema),
                           database=settings.database, runner=runner)
    except mssql.MssqlError:
        return None
    if not raw:
        return None
    # loaded_at is written with SYSUTCDATETIME(); style 126 gives naive ISO-8601.
    return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)


def is_stale(max_loaded_at: datetime | None,
             newest_backup_mtime: datetime | None) -> bool:
    """Should this run take a backup?

    No .bak at all -> always, so a successful run never leaves the deliverable
    set incomplete. Otherwise back up only when a load is newer than the backup;
    that is what catches a previous run that loaded rows and then died before
    backing up.
    """
    if newest_backup_mtime is None:
        return True
    if max_loaded_at is None:
        return False
    return max_loaded_at > newest_backup_mtime


def run(settings: Settings, *, runner=None) -> bool:
    """Back up when stale. Returns True when a backup was taken."""
    loaded_at = max_loaded_at(settings, runner=runner)
    existing = newest_backup(settings)
    existing_mtime = backup_mtime(existing) if existing else None

    if not is_stale(loaded_at, existing_mtime):
        print(f"backup: current ({existing.name}), skipping")
        return False

    print(f"backup: taking a verified compressed FULL backup to {settings.backup_dir}")
    mssql.run(settings, sql.database_backup(settings.database, str(settings.backup_dir)),
              database="master", runner=runner)
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/taxi_seeder/test_backup.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add seeder/src/taxi_seeder/backup.py tests/taxi_seeder/test_backup.py
git commit -m "feat(seeder): backup staleness against the load manifest"
```

---

### Task 11: Report and CLI

**Files:**
- Create: `seeder/src/taxi_seeder/report.py`, `seeder/src/taxi_seeder/cli.py`
- Test: `tests/taxi_seeder/test_report.py`, `tests/taxi_seeder/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `report.loaded_rows(settings, *, runner=None) -> dict[str, int]`; `report.render(settings, *, backup_path=None, rows_by_type=None) -> str`; `cli.main(argv=None) -> int`; `cli.CLEAN = 0`, `cli.NEEDS_REVIEW = 1`, `cli.FAILURE = 2`; `cli.worst(codes: Iterable[int]) -> int`.

- [ ] **Step 1: Write the failing test**

Create `tests/taxi_seeder/test_report.py`:

```python
from taxi_seeder import report
from taxi_seeder.config import from_env


def settings_for(tmp_path, **env):
    return from_env({"MSSQL_PASSWORD": "pw", "TAXI_DATA_DIR": str(tmp_path), **env})


def test_report_counts_parquet_per_stage(tmp_path):
    s = settings_for(tmp_path, TAXI_TYPES="yellow")
    raw = tmp_path / "raw" / "yellow" / "2015"
    raw.mkdir(parents=True)
    (raw / "yellow_2015-01.parquet").write_bytes(b"x")
    (raw / "yellow_2015-02.parquet").write_bytes(b"x")
    norm = tmp_path / "raw-normalized" / "yellow" / "2015"
    norm.mkdir(parents=True)
    (norm / "yellow_2015-01.parquet").write_bytes(b"x")

    out = report.render(s)

    assert "yellow" in out
    assert "2 raw" in out
    assert "1 normalized" in out


def test_report_names_the_backup_when_present(tmp_path):
    s = settings_for(tmp_path)
    d = tmp_path / "mssql" / "backup" / "taxi-sql" / "taxi" / "FULL"
    d.mkdir(parents=True)
    bak = d / "taxi-sql_taxi_FULL_20260801_120000.bak"
    bak.write_bytes(b"\0" * 2048)

    out = report.render(s, backup_path=bak)

    assert "taxi-sql_taxi_FULL_20260801_120000.bak" in out


def test_report_says_so_when_there_is_no_backup(tmp_path):
    assert "none" in report.render(settings_for(tmp_path)).lower()


def test_report_shows_loaded_rows_per_type(tmp_path):
    s = settings_for(tmp_path, TAXI_TYPES="yellow,green")
    out = report.render(s, rows_by_type={"yellow": 1234567, "green": 890})
    assert "1,234,567 row" in out
    assert "890 row" in out


def test_report_tolerates_unknown_row_counts(tmp_path):
    out = report.render(settings_for(tmp_path, TAXI_TYPES="yellow"))
    assert "yellow" in out
    assert "row" not in out.split("yellow")[1].split("\n")[0]


def test_loaded_rows_parses_the_manifest_grouping(tmp_path):
    import subprocess

    def runner(cmd, env, stdin_text):
        return subprocess.CompletedProcess(cmd, 0, "yellow 1234567\ngreen 890\n", "")

    assert report.loaded_rows(settings_for(tmp_path), runner=runner) == {
        "yellow": 1234567, "green": 890,
    }


def test_loaded_rows_is_empty_when_the_manifest_is_missing(tmp_path):
    import subprocess

    def failing(cmd, env, stdin_text):
        return subprocess.CompletedProcess(cmd, 1, "", "Invalid object name")

    assert report.loaded_rows(settings_for(tmp_path), runner=failing) == {}
```

Create `tests/taxi_seeder/test_cli.py`:

```python
from taxi_seeder import cli


def test_worst_prefers_failure():
    assert cli.worst([0, 1, 2]) == 2


def test_worst_prefers_needs_review_over_clean():
    assert cli.worst([0, 1, 0]) == 1


def test_worst_of_nothing_is_clean():
    assert cli.worst([]) == 0


def test_missing_password_is_an_operational_failure(monkeypatch, capsys):
    monkeypatch.delenv("MSSQL_PASSWORD", raising=False)
    assert cli.main([]) == 2
    assert "MSSQL_PASSWORD" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/taxi_seeder/test_report.py tests/taxi_seeder/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError` for `taxi_seeder.report` and `taxi_seeder.cli`

- [ ] **Step 3: Write report.py**

```python
"""Step 7: say what exists now, so the run's value is visible without digging."""
from __future__ import annotations

from pathlib import Path

from taxi_seeder import mssql, sql
from taxi_seeder.config import Settings


def loaded_rows(settings: Settings, *, runner=None) -> dict[str, int]:
    """{data_type: rows} from taxi-seed's _load_manifest.

    Empty when the manifest does not exist yet -- nothing has been loaded, which
    is a normal first-run state, not an error.
    """
    try:
        out = mssql.run(settings, sql.rows_per_type(settings.schema),
                        database=settings.database, runner=runner)
    except mssql.MssqlError:
        return {}
    rows: dict[str, int] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            rows[parts[0]] = int(parts[1])
    return rows


def _count(root: Path, data_type: str) -> int:
    d = root / data_type
    return len(list(d.rglob("*.parquet"))) if d.is_dir() else 0


def _human(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def render(settings: Settings, *, backup_path: Path | None = None,
           rows_by_type: dict[str, int] | None = None) -> str:
    lines = ["", f"=== {settings.data_dir} ==="]
    for data_type in settings.types:
        raw = _count(settings.data_dir / "raw", data_type)
        norm = _count(settings.raw_normalized_dir, data_type)
        line = f"  {data_type:6} {raw:5} raw, {norm:5} normalized month file(s)"
        if rows_by_type and data_type in rows_by_type:
            line += f", {rows_by_type[data_type]:,} row(s) loaded"
        lines.append(line)

    lines.append(f"  database  {settings.database} "
                 f"(mdf {settings.mdf_path}, ldf {settings.ldf_path})")

    if backup_path is not None and backup_path.exists():
        lines.append(f"  backup    {backup_path.name} "
                     f"({_human(backup_path.stat().st_size)})")
        lines.append(f"            {backup_path.parent}")
    else:
        lines.append("  backup    none")
    return "\n".join(lines)
```

- [ ] **Step 4: Write cli.py**

```python
"""The seven-step sequence, and the exit code that summarises it.

Exit codes mirror taxi-seed: 0 clean, 1 needs-review, 2 operational failure.
A step that returns 1 or 2 stops the run -- no type is loaded whose normalize
halted, and no database is backed up whose load failed. Partial progress is
always safe because every step resumes from an observable.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Iterable

from taxi_seeder import backup, mssql, ola, preflight, provision, report, sql, stages
from taxi_seeder.config import ConfigError, from_env

CLEAN = 0
NEEDS_REVIEW = 1
FAILURE = 2


def worst(codes: Iterable[int]) -> int:
    return max(codes, default=CLEAN)


def main(argv=None) -> int:
    try:
        settings = from_env(os.environ)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return FAILURE

    codes: list[int] = []
    try:
        preflight.run(settings)

        for data_type in settings.types:
            rc = stages.mirror(settings, data_type)
            codes.append(rc)
            if rc != CLEAN:
                print(f"error: mirror stage for {data_type} exited {rc}; stopping",
                      file=sys.stderr)
                return worst(codes)

        provision.run(settings)
        ola.run(settings)

        for data_type in settings.types:
            rc = stages.load(settings, data_type)
            codes.append(rc)
            if rc != CLEAN:
                print(f"error: load stage for {data_type} exited {rc}; stopping",
                      file=sys.stderr)
                return worst(codes)

        backup.run(settings)

    except (mssql.MssqlError, sql.SqlIdentifierError) as e:
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/taxi_seeder/ -v`
Expected: PASS — all unit tests across Tasks 2–11

- [ ] **Step 6: Commit**

```bash
git add seeder/src/taxi_seeder/report.py seeder/src/taxi_seeder/cli.py tests/taxi_seeder/
git commit -m "feat(seeder): seven-step CLI with taxi-seed exit code semantics"
```

---

### Task 12: Image and Compose stack

**Files:**
- Create: `seeder/Dockerfile`, `seeder/docker-compose.yml`, `seeder/.env.example`
- Modify: `README.md`

**Interfaces:**
- Consumes: `taxi-seeder` console script (Task 2), vendored Ola script (Task 8).
- Produces: a `seeder` image whose entrypoint is `taxi-seeder`, and a two-service Compose stack.

- [ ] **Step 1: Write the Dockerfile**

Create `seeder/Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Pin the taxi-seed ref the curated normalize mappings are baked from. They are
# NOT in the wheel -- [tool.hatch.build.targets.wheel] ships only src/ packages --
# so they have to come from the repo. Pin to the release tag once one exists.
ARG TAXI_SEED_REF=main

# sqlcmd: needed for Ola's MaintenanceSolution.sql, which is GO-batch separated
# and can only be run by a client that understands GO.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
 && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
      | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
      > /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends mssql-tools18 \
 && apt-get purge -y gnupg && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*
ENV PATH="/opt/mssql-tools18/bin:${PATH}"

# Pristine mappings. preflight copies them onto the volume on first run and
# never overwrites, so drift amendments survive the container.
RUN mkdir -p /opt/taxi-seed/mappings \
 && for t in yellow green fhv fhvhv; do \
      curl -fsSL -o "/opt/taxi-seed/mappings/$t.yaml" \
        "https://raw.githubusercontent.com/andrekamman/taxi-seed/${TAXI_SEED_REF}/normalize/mappings/$t.yaml"; \
    done

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY seeder/ ./seeder/
COPY k6-loadtest/ ./k6-loadtest/
RUN pip install --no-cache-dir .

# CWD is the data dir: `normalize` resolves mappings from the RELATIVE path
# normalize/mappings/<type>.yaml, so this is load-bearing, not cosmetic.
WORKDIR /data
ENTRYPOINT ["taxi-seeder"]
```

- [ ] **Step 2: Write the Compose file**

Create `seeder/docker-compose.yml`:

```yaml
name: taxi-seeder

services:
  mssql:
    image: mcr.microsoft.com/mssql/server:2022-latest
    # Ola's backup path is <Directory>/<@@SERVERNAME>/<db>/<type>/ and
    # @@SERVERNAME follows the container hostname. Unpinned it is random per
    # run, so every run would write to a new folder and cleanup would never
    # find the previous backup.
    hostname: taxi-sql
    # The image runs as uid 10001, which cannot write into a fresh bind-mount
    # subdirectory it does not own -- and the seeder cannot pre-chown them
    # because Compose starts this service first. Alternative: chown -R 10001:0
    # the host folder and drop this line.
    user: root
    environment:
      ACCEPT_EULA: "Y"
      MSSQL_SA_PASSWORD: ${MSSQL_PASSWORD:?set MSSQL_PASSWORD in .env}
      # Developer edition: backup compression is unavailable in Express.
      MSSQL_PID: Developer
    volumes:
      - ${TAXI_DATA_DIR:?set TAXI_DATA_DIR in .env}:/data
    healthcheck:
      test: ["CMD-SHELL", "/opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P \"$$MSSQL_SA_PASSWORD\" -C -Q 'SELECT 1' -b"]
      interval: 10s
      timeout: 5s
      retries: 30
      start_period: 60s

  seeder:
    build:
      context: ..
      dockerfile: seeder/Dockerfile
    depends_on:
      mssql:
        condition: service_healthy
    environment:
      MSSQL_PASSWORD: ${MSSQL_PASSWORD:?}
      TAXI_DATA_DIR: /data
      TAXI_TYPES: ${TAXI_TYPES:-yellow,green,fhv,fhvhv}
      TAXI_RECENT: ${TAXI_RECENT:-}
      TAXI_DATA_FACTOR: ${TAXI_DATA_FACTOR:-3.0}
      MSSQL_DATA_SIZE_GB: ${MSSQL_DATA_SIZE_GB:-}
      MSSQL_LOG_SIZE_GB: ${MSSQL_LOG_SIZE_GB:-16}
      MSSQL_FILEGROWTH_MB: ${MSSQL_FILEGROWTH_MB:-1024}
    volumes:
      - ${TAXI_DATA_DIR}:/data
    working_dir: /data
    restart: "no"
```

- [ ] **Step 3: Write the env template**

Create `seeder/.env.example`:

```bash
# Host folder that will hold everything: raw parquet, normalized parquet,
# mdf/ldf, and backups. Must exist before the first run.
TAXI_DATA_DIR=/srv/taxi

# SA password. Must satisfy SQL Server's complexity policy or the mssql
# container exits during startup.
MSSQL_PASSWORD=change-me-Str0ng!

# Which series to mirror, normalize and load. Default is all four.
TAXI_TYPES=yellow,green,fhv,fhvhv

# Blank means full history. Set to N for the N most recent months --
# TAXI_RECENT=1 is the fast smoke path.
TAXI_RECENT=

# mdf size = normalized parquet bytes x this factor, rounded up to 10 GB.
# ESTIMATE, not a measurement -- calibrate after the first full run.
TAXI_DATA_FACTOR=3.0

# Skip the estimate entirely. Use on a first run when the eventual scope is
# already known; sizing only ever runs at database creation.
MSSQL_DATA_SIZE_GB=

MSSQL_LOG_SIZE_GB=16
MSSQL_FILEGROWTH_MB=1024
```

- [ ] **Step 4: Verify the image builds and the CLI is wired**

```bash
cd seeder
cp .env.example .env
docker compose build seeder
docker compose run --rm --entrypoint taxi-seeder seeder --help || true
docker compose run --rm --entrypoint sh seeder -c 'ls /opt/taxi-seed/mappings && which sqlcmd'
```

Expected: four `.yaml` files listed, and `sqlcmd` resolving to `/opt/mssql-tools18/bin/sqlcmd`.

- [ ] **Step 5: Document it in the README**

Replace the `seeder/` bullet under "Components" in `README.md`:

```markdown
- [`seeder/`](seeder/) — Docker Compose stack that advances one bind-mounted folder toward
  a complete deliverable set: raw parquet, normalized parquet, a presized page-compressed
  SQL Server database, and a verified compressed Ola Hallengren backup. Re-running resumes
  from wherever the last run stopped.
  See the [design spec](docs/specs/2026-08-01-seeder-container-design.md).

  ```bash
  cd seeder && cp .env.example .env   # set TAXI_DATA_DIR and MSSQL_PASSWORD
  docker compose up --build
  ```
```

- [ ] **Step 6: Commit**

```bash
git add seeder/Dockerfile seeder/docker-compose.yml seeder/.env.example README.md
git commit -m "feat(seeder): Dockerfile and two-service Compose stack"
```

---

### Task 13: Integration test

**Files:**
- Create: `tests/taxi_seeder/test_integration_compose.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: the Compose stack (Task 12).
- Produces: nothing downstream. This is the test of the resumability premise.

**Blocked on Task 1** — page compression cannot be asserted until taxi-seed ships it.

- [ ] **Step 1: Register the marker**

In `pyproject.toml`, under `[tool.pytest.ini_options]`:

```toml
markers = [
    "integration: needs Docker and a real SQL Server; deselect with -m 'not integration'",
]
```

- [ ] **Step 2: Write the test**

Create `tests/taxi_seeder/test_integration_compose.py`:

```python
"""End-to-end against the real Compose stack.

Run:  uv run pytest -m integration -v
Skip: uv run pytest -m 'not integration'
"""
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

COMPOSE_DIR = Path(__file__).resolve().parents[2] / "seeder"
PASSWORD = "Integration-Str0ng!"


def compose(*args, data_dir, check=True):
    env = {
        **os.environ,
        "TAXI_DATA_DIR": str(data_dir),
        "MSSQL_PASSWORD": PASSWORD,
        "TAXI_TYPES": "yellow",
        "TAXI_RECENT": "1",
    }
    return subprocess.run(
        ["docker", "compose", *args], cwd=COMPOSE_DIR, env=env,
        capture_output=True, text=True, check=check,
    )


def sqlcmd(data_dir, query):
    proc = compose(
        "exec", "-T", "mssql",
        "/opt/mssql-tools18/bin/sqlcmd", "-S", "localhost", "-U", "sa",
        "-P", PASSWORD, "-C", "-b", "-h", "-1", "-W", "-Q", query,
        data_dir=data_dir,
    )
    return proc.stdout.strip()


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("taxi-integration")
    yield d
    compose("down", "-v", data_dir=d, check=False)


def test_first_run_produces_the_full_deliverable_set(data_dir):
    result = compose("up", "--build", "--abort-on-container-exit",
                     "--exit-code-from", "seeder", data_dir=data_dir, check=False)
    assert result.returncode == 0, result.stdout + result.stderr

    assert list((data_dir / "raw" / "yellow").rglob("*.parquet"))
    assert list((data_dir / "raw-normalized" / "yellow").rglob("*.parquet"))
    assert (data_dir / "mssql" / "data" / "taxi.mdf").exists()
    assert (data_dir / "mssql" / "log" / "taxi_log.ldf").exists()

    baks = list((data_dir / "mssql" / "backup").rglob("*.bak"))
    assert len(baks) == 1
    # Ola's layout, with the hostname pinned so the path is stable across runs.
    assert baks[0].parent == data_dir / "mssql" / "backup" / "taxi-sql" / "taxi" / "FULL"


def test_loaded_tables_are_page_compressed(data_dir):
    """Requires taxi-seed >= 0.2.0 (Task 1)."""
    out = sqlcmd(data_dir, (
        "SELECT COUNT(*) FROM taxi.sys.partitions p "
        "JOIN taxi.sys.tables t ON t.object_id = p.object_id "
        "WHERE t.name LIKE 'yellow[_]%' AND p.data_compression_desc <> 'PAGE'"
    ))
    assert out == "0"


def test_database_is_in_simple_recovery(data_dir):
    assert sqlcmd(data_dir, "SELECT recovery_model_desc FROM sys.databases "
                            "WHERE name = 'taxi'") == "SIMPLE"


def test_second_run_is_a_near_noop(data_dir):
    """The real test of resumability: every skip condition must hold."""
    before = {p: p.stat().st_mtime
              for p in (data_dir / "mssql" / "backup").rglob("*.bak")}

    result = compose("up", "--abort-on-container-exit",
                     "--exit-code-from", "seeder", data_dir=data_dir, check=False)
    assert result.returncode == 0, result.stdout + result.stderr

    after = {p: p.stat().st_mtime
             for p in (data_dir / "mssql" / "backup").rglob("*.bak")}
    assert after == before, "a no-op run must not produce a new backup"
    assert "0 row(s) loaded" in result.stdout or "skip" in result.stdout.lower()
```

- [ ] **Step 3: Run the integration test**

Run: `uv run pytest -m integration -v`
Expected: PASS. First run takes several minutes (image build, SQL Server startup, ~200 MB download).

- [ ] **Step 4: Confirm the unit suite still runs without Docker**

Run: `uv run pytest -m 'not integration' -q`
Expected: PASS, fast, no Docker required.

- [ ] **Step 5: Calibrate the sizing factor**

From the first run, record the real ratio and update both the spec and the default:

```bash
du -sb "$TAXI_DATA_DIR/raw-normalized/yellow"
ls -l "$TAXI_DATA_DIR/mssql/data/taxi.mdf"
```

Compute `actual_used_mdf_bytes / normalized_parquet_bytes`, then update `TAXI_DATA_FACTOR`'s default in `config.py`, `.env.example`, and the spec's sizing section, replacing "estimate, not a measurement" with the measured value and the scope it came from.

- [ ] **Step 6: Commit**

```bash
git add tests/taxi_seeder/test_integration_compose.py pyproject.toml
git commit -m "test(seeder): compose integration covering resumability"
```

---

## Verification

Run before considering this done:

```bash
uv run pytest -m 'not integration' -q     # fast unit suite, no Docker
uv run pytest -m integration -v           # full stack, needs Docker
```

Deliverables to confirm by hand in `$TAXI_DATA_DIR` after an integration run:
`raw/`, `raw-normalized/`, `mssql/data/taxi.mdf`, `mssql/log/taxi_log.ldf`, and exactly one `.bak` under `mssql/backup/taxi-sql/taxi/FULL/`.

## Known open risk

**Instant file initialization on SQL Server on Linux is unconfirmed** (spec, "Instant file initialization — open risk"). Presizing a multi-hundred-GB mdf is only fast if the file is not zero-filled first. Measure early — time a `CREATE DATABASE` with a 50 GB `SIZE` against the target filesystem before trusting the default path for a full-history run. If it zero-fills, fall back to a modest initial size plus 1 GB autogrowth and record the finding in the spec. Log files are always zero-filled on every platform, which is why the ldf default stays at 16 GB.
