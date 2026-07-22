# SQL Server Loader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bulk-load normalized parquet (`raw-normalized/<type>/<year>/*.parquet`) into SQL Server, one table per year per type, idempotently, entirely inside a DuckDB session via the `mssql` community extension.

**Architecture:** A new `loader/` component exposes a `taxi-load` CLI. DuckDB reads the parquet and writes to SQL Server over the native-TDS `mssql` extension (`COPY … (FORMAT 'bcp')`). All bookkeeping lives in one out-of-band `<schema>._load_manifest` table. The load decision per `(type, year)` is computed by a **pure function** (`reconcile.py`) from three inputs the CLI gathers (disk parquet footers, the manifest, per-year `COUNT(*)`), so the core logic is exhaustively unit-testable with no database. `load.py` is a thin executor of the returned plan. Table DDL is built explicitly (DESCRIBE → `taxi_shared` type map → `CREATE TABLE`) rather than relying on the extension's auto-create, giving deterministic, bounded, index-friendly column types.

**Tech Stack:** Python 3.12, DuckDB ≥ 1.4.4, the `mssql` DuckDB community extension, `taxi_shared` (existing), pytest, SQL Server 2022 in Docker (integration only).

## Global Constraints

- **DuckDB pin:** `duckdb>=1.4.4` (already in `pyproject.toml`); the `mssql` extension needs DuckDB ≥ 1.4.1. Do not lower.
- **No new runtime dependency.** The `mssql` extension is installed at runtime via `INSTALL mssql FROM community; LOAD mssql;`. Do not add it to `dependencies`.
- **Extension version is asserted at startup.** A single constant `EXPECTED_MSSQL_EXT_VERSION` in `connection.py` is compared against the actually-installed version; mismatch is a hard error (exit 2). The spec nominates `v0.2.1`; **Task 1 (spike) sets this constant to the version that actually installs and passes the integration suite** — treat the exact value as spike output, not a given.
- **Password comes from the `MSSQL_PASSWORD` environment variable only.** Never a CLI arg, never logged, never printed in `--dry-run` output. It appears only inside `build_conn_string`.
- **Data types:** `DATA_TYPES = ("yellow", "green", "fhv", "fhvhv")`. `taxi-load` with no positional arg processes all four in order.
- **Table naming:** `<schema>.<type>_<year>` (e.g. `dbo.yellow_2024`). Schema defaults to `dbo`, database to `taxi`; both configurable.
- **Identifier safety:** `--database` and `--schema` values are validated against `^[A-Za-z_][A-Za-z0-9_]*$` before use (exit 2 on violation). `<type>` is whitelisted to `DATA_TYPES`; `<year>` is an int parsed from the directory name. These are the only values interpolated into SQL identifiers.
- **The unit of change is a month file (append) or a year table (truncate+reload).** Never a single-row or single-month `DELETE` on a *data* table. (`DELETE` on the *manifest* table for a whole year during reload is allowed and expected.)
- **Exit codes:** `0` success (incl. all-skip and successful `--dry-run`); `1` one or more types failed mid-load, others still processed; `2` connection / auth / provisioning / config error (nothing loaded). Precedence when combining across types: `2` > `1` > `0`.
- **Reuse `taxi_shared`:** `taxi_shared.type_mapping.map_duckdb_to_mssql` for column types and `taxi_shared.sql_generator.generate_create_table_sql` for `CREATE TABLE` text. `TypeMappingError` surfaces as exit 2 naming the offending column.

---

## Design decisions resolving spec ambiguities

Two places in the spec are internally inconsistent; both are resolved here and noted so the implementer does not "fix" them back.

1. **Reload uses explicit DROP + explicit CREATE, not `REPLACE true`.** The spec's DDL section requires explicit `CREATE TABLE` (from `taxi_shared`) with `COPY … (CREATE_TABLE false)` to avoid the extension's auto-DDL defaulting strings to `NVARCHAR(MAX)`. The reconcile section separately says reload uses `COPY … (REPLACE true)`, which would re-introduce that auto-DDL. To honor the *intent* (deterministic, bounded types), **reload = `DROP TABLE IF EXISTS` → explicit `CREATE TABLE` → `COPY … (CREATE_TABLE false, TABLOCK true)`.** `REPLACE true` is not used anywhere.

2. **Manifest column types are hand-chosen (bounded), not derived from `map_duckdb_to_mssql`.** The manifest needs a primary key on `(data_type, year, month)`, but `map_duckdb_to_mssql` maps `VARCHAR → NVARCHAR(MAX)`, which cannot participate in a PK. The manifest DDL therefore passes an explicit ordered `{column: sql_type}` dict (with `NVARCHAR(16)` for `data_type`, etc.) to `taxi_shared.generate_create_table_sql` — still using the shared generator, but with PK-compatible types — then adds the PK via a follow-up `ALTER TABLE`.

3. **All SQL Server *reads* go through `mssql_scan(catalog, query)` and all non-COPY *writes* through `mssql_exec(catalog, sql)`**, rather than catalog-integrated `SELECT … FROM mssql.schema.table`. This runs live SQL and sidesteps the extension's metadata cache (`mssql_catalog_cache_ttl`) returning stale results for tables we create earlier in the same session. Only the bulk data path uses `COPY … TO 'mssql://…'`.

---

### Task 1: Extension spike (de-risk the whole build)

**Files:**
- Create: `docs/superpowers/notes/2026-07-22-mssql-extension-spike.md` (findings)
- Create: `loader/src/taxi_loader/__init__.py` (empty, so the package dir exists)

**Interfaces:**
- Produces: the confirmed value of `EXPECTED_MSSQL_EXT_VERSION`, and confirmation that `mssql_exec` / `mssql_scan` / `COPY … (FORMAT 'bcp')` behave as the later tasks assume. Every later task's SQL is written against these findings.

This task is a manual investigation against a throwaway Docker SQL Server; its deliverable is written-down facts, not production code.

- [ ] **Step 1: Start a throwaway SQL Server**

```bash
docker run -d --name mssql-spike -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=Str0ng_Passw0rd!" \
  -p 1433:1433 mcr.microsoft.com/mssql/server:2022-latest
sleep 20  # give it time to come up
```

- [ ] **Step 2: Confirm install/load and capture the installed version**

Run:
```bash
MSSQL_PASSWORD='Str0ng_Passw0rd!' uv run python - <<'PY'
import duckdb
c = duckdb.connect(":memory:")
c.execute("INSTALL mssql FROM community; LOAD mssql;")
print("ext version:", c.execute(
    "SELECT extension_version FROM duckdb_extensions() WHERE extension_name='mssql'"
).fetchone()[0])
PY
```
Record the printed version. This becomes `EXPECTED_MSSQL_EXT_VERSION` in Task 5 (the spec nominates `0.2.1`; if the community repo only serves a newer build for DuckDB 1.4.x, record that instead and note the deviation).

- [ ] **Step 3: Confirm provisioning, attach, exec, scan, and both COPY paths**

Run this end-to-end and record any statement whose syntax differs from what's used below:
```bash
MSSQL_PASSWORD='Str0ng_Passw0rd!' uv run python - <<'PY'
import duckdb
c = duckdb.connect(":memory:")
c.execute("INSTALL mssql FROM community; LOAD mssql;")
cs = "Server=localhost,1433;Database={db};User Id=sa;Password=Str0ng_Passw0rd!;Encrypt=yes;TrustServerCertificate=yes"
# provision DB via master
c.execute("ATTACH ? AS boot (TYPE mssql)", [cs.format(db="master")])
c.execute("SELECT mssql_exec('boot', ?)", ["IF DB_ID('taxi') IS NULL EXEC('CREATE DATABASE [taxi]')"])
c.execute("DETACH boot")
# attach target + schema + a table + bcp COPY
c.execute("ATTACH ? AS mssql (TYPE mssql)", [cs.format(db="taxi")])
c.execute("SELECT mssql_exec('mssql', ?)", ["IF OBJECT_ID('dbo.spike','U') IS NULL EXEC('CREATE TABLE dbo.spike (id INT, name NVARCHAR(50))')"])
c.execute("COPY (SELECT i AS id, 'r'||i AS name FROM range(5) t(i)) TO 'mssql://mssql/dbo/spike' (FORMAT 'bcp', CREATE_TABLE false, TABLOCK true, FLUSH_ROWS 100000)")
print("count:", c.execute("SELECT c FROM mssql_scan('mssql','SELECT COUNT_BIG(*) AS c FROM dbo.spike')").fetchone()[0])
print("object_id:", c.execute("SELECT o FROM mssql_scan('mssql','SELECT OBJECT_ID(''dbo.spike'',''U'') AS o')").fetchone()[0])
c.execute("SELECT mssql_exec('mssql', ?)", ["DROP TABLE IF EXISTS dbo.spike"])
PY
```
Confirm: (a) `CREATE DATABASE` via `mssql_exec` on the `master` attach works; (b) `mssql_exec` accepts multi-statement `IF … EXEC('…')`; (c) `COPY … (FORMAT 'bcp', CREATE_TABLE false)` appends into a pre-created table; (d) `mssql_scan` returns scalar query results; (e) whether `CREATE TABLE` DDL must be wrapped in `EXEC('…')` or can be passed to `mssql_exec` directly.

- [ ] **Step 4: Write findings and clean up**

Write `docs/superpowers/notes/2026-07-22-mssql-extension-spike.md` recording: installed extension version, exact working spellings for provisioning / attach / exec / scan / append-COPY / drop, and any deviation from this plan's assumed SQL. Then:
```bash
docker rm -f mssql-spike
```

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/notes/2026-07-22-mssql-extension-spike.md loader/src/taxi_loader/__init__.py
git commit -m "chore(loader): spike mssql extension; record confirmed API"
```

---

### Task 2: Component skeleton + packaging (so `taxi_loader` is importable)

**Files:**
- Create: `loader/src/taxi_loader/cli.py` (temporary stub `main`)
- Create: `loader/README.md`
- Modify: `pyproject.toml:15-21` (add wheel package) and `pyproject.toml:31-34` (add script)

**Interfaces:**
- Produces: import name `taxi_loader`, console script `taxi-load` resolving to `taxi_loader.cli:main`. Every later task imports from `taxi_loader.*` and relies on this being installed.

Done early (the spec lists packaging last) so all subsequent tests can `import taxi_loader`.

- [ ] **Step 1: Write the packaging test**

Create `tests/taxi_loader/test_packaging.py`:
```python
def test_taxi_loader_importable():
    import taxi_loader  # noqa: F401


def test_cli_module_has_main():
    from taxi_loader import cli
    assert callable(cli.main)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --extra test pytest tests/taxi_loader/test_packaging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_loader'`.

- [ ] **Step 3: Add the stub CLI**

Create `loader/src/taxi_loader/cli.py`:
```python
"""Entry point for the `taxi-load` command (stub; implemented in a later task)."""
from __future__ import annotations

import sys


def main() -> int:
    print("taxi-load: not yet implemented")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Wire packaging**

In `pyproject.toml`, add `"loader/src/taxi_loader",` to `[tool.hatch.build.targets.wheel] packages` (keep the list alphabetical-ish, matching the existing style):
```toml
[tool.hatch.build.targets.wheel]
packages = [
  "k6-loadtest/src/k6_loadtest",
  "loader/src/taxi_loader",
  "normalize/src/taxi_normalize",
  "schema-drift/src/schema_drift",
  "shared/src/taxi_shared",
]
```
And add to `[project.scripts]`:
```toml
taxi-load = "taxi_loader.cli:main"
```

- [ ] **Step 5: Sync and run the test**

Run:
```bash
uv sync --extra test
uv run --extra test pytest tests/taxi_loader/test_packaging.py -v
```
Expected: PASS (both tests). If `import taxi_loader` still fails, re-run `uv sync` — the new wheel package must register on the editable path.

- [ ] **Step 6: Write the README pointer**

Create `loader/README.md`:
```markdown
# loader

Bulk-loads normalized TLC parquet (`raw-normalized/<type>/<year>/`) into SQL Server —
one table per year per type — entirely through DuckDB and the `mssql` community
extension. Idempotent: a month not loaded is appended; a complete year is skipped;
a changed or incomplete year is truncated and reloaded.

→ **[Full guide](https://andrekamman.github.io/taxi/guides/loader/)**
```

- [ ] **Step 7: Commit**

```bash
git add loader/ pyproject.toml tests/taxi_loader/test_packaging.py
git commit -m "feat(loader): component skeleton + taxi-load script wiring"
```

---

### Task 3: Explicit table DDL builder + synthetic-parquet test fixtures

**Files:**
- Create: `loader/src/taxi_loader/load.py` (DDL half only in this task)
- Create: `tests/taxi_loader/conftest.py` (synthetic normalized parquet fixtures)
- Test: `tests/taxi_loader/test_ddl.py`

**Interfaces:**
- Consumes: `taxi_shared.type_mapping.map_duckdb_to_mssql`, `taxi_shared.sql_generator.generate_create_table_sql`.
- Produces:
  - `describe_parquet_types(conn: duckdb.DuckDBPyConnection, parquet_path: str | Path) -> dict[str, str]`
  - `build_create_table_sql(conn, fq_table: str, sample_parquet: str | Path) -> str` — returns a single `CREATE TABLE` statement (no trailing `;`).
  - conftest fixture `normalized_family(tmp_path) -> Path` returning an input-dir root, plus helpers `write_month(conn, root, data_type, year, month, rows)` and `TYPE_COLUMNS`.

- [ ] **Step 1: Write the conftest fixtures**

Create `tests/taxi_loader/conftest.py`:
```python
"""Synthetic *normalized* parquet families for taxi_loader tests.

A normalized family has ONE uniform schema across every file of a type, laid out
as <root>/<type>/<year>/<type>_tripdata_<year>-<mm>.parquet — matching the
normalizer's raw-normalized/ output.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

# One uniform schema per type. Chosen to exercise the type map: BIGINT, DOUBLE,
# VARCHAR, TIMESTAMP.
TYPE_COLUMNS = {
    "yellow": ["vendorid BIGINT", "tpep_pickup_datetime TIMESTAMP",
               "trip_distance DOUBLE", "store_and_fwd_flag VARCHAR"],
    "green": ["vendorid BIGINT", "lpep_pickup_datetime TIMESTAMP",
              "trip_distance DOUBLE", "store_and_fwd_flag VARCHAR"],
}


def write_month(conn: duckdb.DuckDBPyConnection, root: Path, data_type: str,
                year: int, month: int, rows: int) -> Path:
    """Write one synthetic normalized month file; return its path."""
    d = root / data_type / str(year)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{data_type}_tripdata_{year}-{month:02d}.parquet"
    pick = "tpep_pickup_datetime" if data_type == "yellow" else "lpep_pickup_datetime"
    conn.execute(f"""
        COPY (
            SELECT i AS vendorid,
                   TIMESTAMP '{year}-{month:02d}-01' + (i * INTERVAL 1 HOUR) AS {pick},
                   (i * 1.5) AS trip_distance,
                   CASE WHEN i % 2 = 0 THEN 'N' ELSE 'Y' END AS store_and_fwd_flag
            FROM range({rows}) t(i)
        ) TO '{path}' (FORMAT PARQUET)
    """)
    return path


@pytest.fixture
def normalized_family(tmp_path: Path) -> Path:
    """Build yellow/2023 (2 months) and yellow/2024 (1 month). Return the root dir."""
    root = tmp_path / "raw-normalized"
    conn = duckdb.connect(":memory:")
    write_month(conn, root, "yellow", 2023, 1, rows=3)
    write_month(conn, root, "yellow", 2023, 2, rows=4)
    write_month(conn, root, "yellow", 2024, 1, rows=5)
    conn.close()
    return root
```

- [ ] **Step 2: Write the failing DDL test**

Create `tests/taxi_loader/test_ddl.py`:
```python
import duckdb

from taxi_loader.load import build_create_table_sql, describe_parquet_types


def test_describe_returns_column_types(normalized_family):
    conn = duckdb.connect(":memory:")
    f = normalized_family / "yellow" / "2024" / "yellow_tripdata_2024-01.parquet"
    types = describe_parquet_types(conn, f)
    assert types["vendorid"] == "BIGINT"
    assert types["trip_distance"] == "DOUBLE"
    assert types["store_and_fwd_flag"] == "VARCHAR"


def test_create_table_sql_maps_types_and_keeps_column_order(normalized_family):
    conn = duckdb.connect(":memory:")
    f = normalized_family / "yellow" / "2024" / "yellow_tripdata_2024-01.parquet"
    sql = build_create_table_sql(conn, "dbo.yellow_2024", f)
    assert sql.startswith("CREATE TABLE dbo.yellow_2024 (")
    assert "vendorid BIGINT" in sql
    assert "trip_distance FLOAT" in sql            # DOUBLE -> FLOAT via taxi_shared
    assert "store_and_fwd_flag NVARCHAR(MAX)" in sql
    assert "tpep_pickup_datetime DATETIME2" in sql
    assert not sql.rstrip().endswith(";")          # single statement for mssql_exec
    # column order matches parquet order
    assert sql.index("vendorid") < sql.index("tpep_pickup_datetime") < sql.index("trip_distance")
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run --extra test pytest tests/taxi_loader/test_ddl.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_create_table_sql'`.

- [ ] **Step 4: Implement the DDL half of `load.py`**

Create `loader/src/taxi_loader/load.py`:
```python
"""DuckDB↔SQL Server load: DDL construction and (later) COPY execution."""
from __future__ import annotations

from pathlib import Path

import duckdb

from taxi_shared.sql_generator import generate_create_table_sql
from taxi_shared.type_mapping import map_duckdb_to_mssql


def describe_parquet_types(conn: duckdb.DuckDBPyConnection,
                           parquet_path: str | Path) -> dict[str, str]:
    """{column_name: duckdb_type} from one parquet file, in file column order."""
    rows = conn.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{parquet_path}')"
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def build_create_table_sql(conn: duckdb.DuckDBPyConnection, fq_table: str,
                           sample_parquet: str | Path) -> str:
    """Explicit CREATE TABLE for a (type, year) table, from one sample file.

    DESCRIBE -> taxi_shared type map -> generate_create_table_sql. Returns a
    single statement with no trailing ';' (mssql_exec wants one statement).
    Raises taxi_shared.type_mapping.TypeMappingError for unmapped columns.
    """
    duck_types = describe_parquet_types(conn, sample_parquet)
    mssql_cols = {name: map_duckdb_to_mssql(dt) for name, dt in duck_types.items()}
    return generate_create_table_sql(fq_table, mssql_cols).rstrip(";\n ")
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run --extra test pytest tests/taxi_loader/test_ddl.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add loader/src/taxi_loader/load.py tests/taxi_loader/conftest.py tests/taxi_loader/test_ddl.py
git commit -m "feat(loader): explicit CREATE TABLE builder via taxi_shared + parquet fixtures"
```

---

### Task 4: `reconcile.py` pure decision function (the bulk of coverage)

**Files:**
- Create: `loader/src/taxi_loader/reconcile.py`
- Test: `tests/taxi_loader/test_reconcile.py`

**Interfaces:**
- Produces (relied on by `load.py`, `manifest.py`, `cli.py`):
  - `MonthFile(year: int, month: int, path: str, source_row_count: int)` — frozen dataclass
  - `ManifestRow(year: int, month: int, row_count: int)` — frozen dataclass
  - `SKIP = "skip"`, `APPEND = "append"`, `RELOAD = "reload"`
  - `YearPlan(year: int, action: str, months: list[MonthFile])` — dataclass; `months` is `[]` for SKIP, the months to append for APPEND, and all disk months for the year for RELOAD.
  - `reconcile(disk_months, manifest_rows, table_row_counts: dict[int, int], full_refresh: bool) -> list[YearPlan]` — sorted by year; no database access.

Decision rule per year (evaluated after an integrity gate), from the spec's table:
- `full_refresh` → RELOAD.
- table `COUNT(*)` ≠ sum of manifest `row_count` for the year → RELOAD (covers table-missing-but-manifest-has-rows: absent table counts as 0).
- any manifest month not present on disk → RELOAD.
- any disk month whose manifest `row_count` ≠ its `source_row_count` → RELOAD.
- else: disk months with no manifest row → APPEND those (fresh year → all months append; the executor creates the table first).
- else → SKIP.

- [ ] **Step 1: Write the failing tests (full decision table)**

Create `tests/taxi_loader/test_reconcile.py`:
```python
from taxi_loader.reconcile import (
    APPEND, RELOAD, SKIP, ManifestRow, MonthFile, YearPlan, reconcile,
)


def mf(year, month, rows, path=None):
    return MonthFile(year, month, path or f"/x/{year}-{month:02d}.parquet", rows)


def mr(year, month, rows):
    return ManifestRow(year, month, rows)


def only(plans, year):
    return next(p for p in plans if p.year == year)


def test_fresh_year_appends_all_months():
    disk = [mf(2024, 1, 5), mf(2024, 2, 6)]
    plans = reconcile(disk, [], {}, full_refresh=False)
    p = only(plans, 2024)
    assert p.action == APPEND
    assert {m.month for m in p.months} == {1, 2}


def test_all_present_and_matching_skips():
    disk = [mf(2024, 1, 5), mf(2024, 2, 6)]
    man = [mr(2024, 1, 5), mr(2024, 2, 6)]
    counts = {2024: 11}
    plans = reconcile(disk, man, counts, full_refresh=False)
    assert only(plans, 2024).action == SKIP


def test_one_new_month_appends_only_it():
    disk = [mf(2024, 1, 5), mf(2024, 2, 6), mf(2024, 3, 7)]
    man = [mr(2024, 1, 5), mr(2024, 2, 6)]
    counts = {2024: 11}
    p = only(reconcile(disk, man, counts, full_refresh=False), 2024)
    assert p.action == APPEND
    assert [m.month for m in p.months] == [3]


def test_source_count_changed_reloads():
    disk = [mf(2024, 1, 5), mf(2024, 2, 99)]   # month 2 grew on disk
    man = [mr(2024, 1, 5), mr(2024, 2, 6)]
    counts = {2024: 11}
    p = only(reconcile(disk, man, counts, full_refresh=False), 2024)
    assert p.action == RELOAD
    assert {m.month for m in p.months} == {1, 2}


def test_manifest_month_vanished_reloads():
    disk = [mf(2024, 1, 5)]                     # month 2 removed from disk
    man = [mr(2024, 1, 5), mr(2024, 2, 6)]
    counts = {2024: 11}
    assert only(reconcile(disk, man, counts, full_refresh=False), 2024).action == RELOAD


def test_integrity_mismatch_reloads():
    disk = [mf(2024, 1, 5), mf(2024, 2, 6)]
    man = [mr(2024, 1, 5), mr(2024, 2, 6)]      # manifest sum 11
    counts = {2024: 14}                          # table has 3 extra (partial prior load)
    assert only(reconcile(disk, man, counts, full_refresh=False), 2024).action == RELOAD


def test_table_missing_but_manifest_has_rows_reloads():
    disk = [mf(2024, 1, 5)]
    man = [mr(2024, 1, 5)]
    counts = {}                                  # table absent -> 0 != 5
    assert only(reconcile(disk, man, counts, full_refresh=False), 2024).action == RELOAD


def test_full_refresh_reloads_even_when_matching():
    disk = [mf(2024, 1, 5)]
    man = [mr(2024, 1, 5)]
    counts = {2024: 5}
    p = only(reconcile(disk, man, counts, full_refresh=True), 2024)
    assert p.action == RELOAD
    assert {m.month for m in p.months} == {1}


def test_multiple_years_decided_independently():
    disk = [mf(2023, 1, 5), mf(2024, 1, 9)]
    man = [mr(2023, 1, 5)]                        # 2023 complete, 2024 fresh
    counts = {2023: 5}
    plans = reconcile(disk, man, counts, full_refresh=False)
    assert only(plans, 2023).action == SKIP
    assert only(plans, 2024).action == APPEND
    assert [p.year for p in plans] == [2023, 2024]   # sorted


def test_returns_year_plan_type():
    plans = reconcile([mf(2024, 1, 5)], [], {}, full_refresh=False)
    assert isinstance(plans[0], YearPlan)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/taxi_loader/test_reconcile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_loader.reconcile'`.

- [ ] **Step 3: Implement `reconcile.py`**

Create `loader/src/taxi_loader/reconcile.py`:
```python
"""Pure per-(type, year) load decision. No database access.

Inputs are gathered by the CLI (disk parquet footers, the manifest table, and
per-year COUNT(*)); the returned plan is executed by load.py.
"""
from __future__ import annotations

from dataclasses import dataclass

SKIP = "skip"
APPEND = "append"
RELOAD = "reload"


@dataclass(frozen=True)
class MonthFile:
    year: int
    month: int
    path: str
    source_row_count: int


@dataclass(frozen=True)
class ManifestRow:
    year: int
    month: int
    row_count: int


@dataclass
class YearPlan:
    year: int
    action: str                # SKIP | APPEND | RELOAD
    months: list[MonthFile]    # [] for SKIP; months to append for APPEND; all disk months for RELOAD


def reconcile(disk_months: list[MonthFile],
              manifest_rows: list[ManifestRow],
              table_row_counts: dict[int, int],
              full_refresh: bool) -> list[YearPlan]:
    years = sorted({m.year for m in disk_months} | {r.year for r in manifest_rows})
    plans: list[YearPlan] = []
    for year in years:
        disk = sorted((m for m in disk_months if m.year == year), key=lambda m: m.month)
        man_by_month = {r.month: r.row_count for r in manifest_rows if r.year == year}
        disk_by_month = {m.month: m for m in disk}
        manifest_sum = sum(man_by_month.values())
        table_count = table_row_counts.get(year, 0)

        if full_refresh:
            plans.append(YearPlan(year, RELOAD, disk))
            continue
        # Integrity gate: committed table rows must equal recorded manifest rows.
        if table_count != manifest_sum:
            plans.append(YearPlan(year, RELOAD, disk))
            continue
        # A manifest month that no longer exists on disk -> rebuild.
        if any(month not in disk_by_month for month in man_by_month):
            plans.append(YearPlan(year, RELOAD, disk))
            continue
        # Per-month decision.
        to_append: list[MonthFile] = []
        changed = False
        for m in disk:
            if m.month not in man_by_month:
                to_append.append(m)
            elif man_by_month[m.month] != m.source_row_count:
                changed = True
                break
        if changed:
            plans.append(YearPlan(year, RELOAD, disk))
        elif to_append:
            plans.append(YearPlan(year, APPEND, to_append))
        else:
            plans.append(YearPlan(year, SKIP, []))
    return plans
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --extra test pytest tests/taxi_loader/test_reconcile.py -v`
Expected: PASS (all ten tests).

- [ ] **Step 5: Commit**

```bash
git add loader/src/taxi_loader/reconcile.py tests/taxi_loader/test_reconcile.py
git commit -m "feat(loader): pure reconcile() decision function + exhaustive tests"
```

---

### Task 5: `connection.py` — install/load/version-assert/attach/provision

**Files:**
- Create: `loader/src/taxi_loader/connection.py`
- Test: `tests/taxi_loader/test_connection.py`

**Interfaces:**
- Consumes: the confirmed version and SQL spellings from Task 1.
- Produces (relied on by `manifest.py`, `load.py`, `cli.py`):
  - `ATTACH_NAME = "mssql"`, `BOOT_ATTACH_NAME = "mssql_boot"`, `EXPECTED_MSSQL_EXT_VERSION = "<from spike>"`
  - `class LoaderError(Exception)` with subclasses `LoaderConnectionError`, `LoaderConfigError` (both → exit 2)
  - `ConnConfig(host, port, database, schema, user, password)` — dataclass
  - `validate_identifier(name: str, what: str) -> str` — returns `name` or raises `LoaderConfigError`
  - `build_conn_string(cfg: ConnConfig, database: str) -> str`
  - `connect_duckdb() -> duckdb.DuckDBPyConnection` — install/load/assert version
  - `ensure_database(conn, cfg) -> None`
  - `attach_target(conn, cfg) -> None` — ATTACH `cfg.database` as `mssql` and ensure schema
  - `_sql_str(s: str) -> str` — double single quotes for literal embedding

- [ ] **Step 1: Write the failing (no-DB) tests**

Create `tests/taxi_loader/test_connection.py`:
```python
import pytest

from taxi_loader.connection import (
    ConnConfig, LoaderConfigError, build_conn_string, validate_identifier,
)


def cfg(**kw):
    base = dict(host="h", port=1433, database="taxi", schema="dbo",
               user="sa", password="secret-pw")
    base.update(kw)
    return ConnConfig(**base)


def test_validate_identifier_accepts_plain():
    assert validate_identifier("dbo", "schema") == "dbo"
    assert validate_identifier("taxi_2", "database") == "taxi_2"


@pytest.mark.parametrize("bad", ["a-b", "1x", "a b", "a;drop", "", "a'b"])
def test_validate_identifier_rejects(bad):
    with pytest.raises(LoaderConfigError):
        validate_identifier(bad, "schema")


def test_conn_string_has_fields_and_password():
    s = build_conn_string(cfg(host="db1", port=1444, password="p@ss"), "taxi")
    assert "Server=db1,1444" in s
    assert "Database=taxi" in s
    assert "User Id=sa" in s
    assert "Password=p@ss" in s
    assert "Encrypt=yes" in s
    assert "TrustServerCertificate=yes" in s


def test_conn_string_targets_requested_database():
    s = build_conn_string(cfg(), "master")
    assert "Database=master" in s
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/taxi_loader/test_connection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_loader.connection'`.

- [ ] **Step 3: Implement `connection.py`**

Use the exact SQL spellings confirmed in Task 1. If the spike found `CREATE TABLE`/`CREATE DATABASE` must be wrapped in `EXEC('…')`, keep the `EXEC(...)` wrappers below; otherwise pass the DDL directly.

Create `loader/src/taxi_loader/connection.py`:
```python
"""Connect DuckDB to SQL Server via the mssql community extension.

INSTALL/LOAD the extension, assert its version, provision the database and
schema, and ATTACH the target. All errors here are exit-2 (nothing loaded).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import duckdb

ATTACH_NAME = "mssql"
BOOT_ATTACH_NAME = "mssql_boot"
# Set from the Task 1 spike (spec nominates 0.2.1).
EXPECTED_MSSQL_EXT_VERSION = "0.2.1"

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class LoaderError(Exception):
    """Base for loader errors that map to exit code 2."""


class LoaderConnectionError(LoaderError):
    """Install/load/attach/provision failure."""


class LoaderConfigError(LoaderError):
    """Bad configuration (identifier, missing password, unmapped type)."""


@dataclass
class ConnConfig:
    host: str
    port: int
    database: str
    schema: str
    user: str
    password: str


def validate_identifier(name: str, what: str) -> str:
    if not _IDENT_RE.match(name or ""):
        raise LoaderConfigError(
            f"invalid {what} {name!r}: must match [A-Za-z_][A-Za-z0-9_]*"
        )
    return name


def _sql_str(s: str) -> str:
    """Escape a value for embedding as a T-SQL single-quoted string literal."""
    return s.replace("'", "''")


def build_conn_string(cfg: ConnConfig, database: str) -> str:
    return (
        f"Server={cfg.host},{cfg.port};"
        f"Database={database};"
        f"User Id={cfg.user};"
        f"Password={cfg.password};"
        f"Encrypt=yes;TrustServerCertificate=yes"
    )


def connect_duckdb() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("INSTALL mssql FROM community;")
        conn.execute("LOAD mssql;")
    except duckdb.Error as e:
        raise LoaderConnectionError(f"failed to install/load mssql extension: {e}") from e
    row = conn.execute(
        "SELECT extension_version FROM duckdb_extensions() WHERE extension_name = 'mssql'"
    ).fetchone()
    version = row[0] if row else None
    if version != EXPECTED_MSSQL_EXT_VERSION:
        raise LoaderConnectionError(
            f"mssql extension version {version!r} != expected "
            f"{EXPECTED_MSSQL_EXT_VERSION!r}; a version bump must be a deliberate, "
            f"tested change (update EXPECTED_MSSQL_EXT_VERSION)."
        )
    return conn


def ensure_database(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig) -> None:
    """Create cfg.database if absent, by attaching master and running CREATE DATABASE."""
    try:
        conn.execute(
            f"ATTACH ? AS {BOOT_ATTACH_NAME} (TYPE mssql)",
            [build_conn_string(cfg, "master")],
        )
        stmt = (
            f"IF DB_ID('{_sql_str(cfg.database)}') IS NULL "
            f"EXEC('CREATE DATABASE [{cfg.database}]')"
        )
        conn.execute(f"SELECT mssql_exec('{BOOT_ATTACH_NAME}', ?)", [stmt])
    except duckdb.Error as e:
        raise LoaderConnectionError(f"failed to provision database {cfg.database!r}: {e}") from e
    finally:
        try:
            conn.execute(f"DETACH {BOOT_ATTACH_NAME}")
        except duckdb.Error:
            pass


def attach_target(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig) -> None:
    """ATTACH the target database as `mssql` and create the schema if non-default."""
    try:
        conn.execute(
            f"ATTACH ? AS {ATTACH_NAME} (TYPE mssql)",
            [build_conn_string(cfg, cfg.database)],
        )
        if cfg.schema != "dbo":
            stmt = (
                f"IF SCHEMA_ID('{_sql_str(cfg.schema)}') IS NULL "
                f"EXEC('CREATE SCHEMA [{cfg.schema}]')"
            )
            conn.execute(f"SELECT mssql_exec('{ATTACH_NAME}', ?)", [stmt])
    except duckdb.Error as e:
        raise LoaderConnectionError(
            f"failed to attach database {cfg.database!r} / schema {cfg.schema!r}: {e}"
        ) from e
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --extra test pytest tests/taxi_loader/test_connection.py -v`
Expected: PASS (all no-DB tests). The connect/attach/provision functions are exercised by the integration suite in Task 8.

- [ ] **Step 5: Commit**

```bash
git add loader/src/taxi_loader/connection.py tests/taxi_loader/test_connection.py
git commit -m "feat(loader): mssql connection, version assert, DB/schema provisioning"
```

---

### Task 6: `manifest.py` — bookkeeping table create/read/write

**Files:**
- Create: `loader/src/taxi_loader/manifest.py`
- Test: `tests/taxi_loader/test_manifest.py` (DDL-string test no-DB; round-trip test integration, env-gated)

**Interfaces:**
- Consumes: `connection.ConnConfig`, `connection.ATTACH_NAME`, `connection._sql_str`; `reconcile.ManifestRow`; `taxi_shared.sql_generator.generate_create_table_sql`.
- Produces (relied on by `load.py`, `cli.py`):
  - `MANIFEST_TABLE = "_load_manifest"`, `MANIFEST_COLUMNS: dict[str, str]` (ordered)
  - `manifest_fq(schema: str) -> str`
  - `build_manifest_ddl(schema: str) -> list[str]` — `[CREATE TABLE stmt, ALTER TABLE ADD PK stmt]`
  - `ensure_manifest_table(conn, cfg) -> None`
  - `read_manifest(conn, cfg, data_type: str) -> list[ManifestRow]`
  - `write_month_row(conn, cfg, data_type, year, month, source_file, row_count) -> None`
  - `delete_year_rows(conn, cfg, data_type, year) -> None`

- [ ] **Step 1: Write the failing no-DB DDL test**

Create `tests/taxi_loader/test_manifest.py`:
```python
from taxi_loader.manifest import (
    MANIFEST_COLUMNS, build_manifest_ddl, manifest_fq,
)


def test_manifest_fq_uses_schema():
    assert manifest_fq("dbo") == "dbo._load_manifest"
    assert manifest_fq("stage") == "stage._load_manifest"


def test_manifest_columns_are_pk_compatible():
    # data_type must be bounded (not NVARCHAR(MAX)) to sit in the PK.
    assert MANIFEST_COLUMNS["data_type"].startswith("NVARCHAR(")
    assert "MAX" not in MANIFEST_COLUMNS["data_type"]
    assert MANIFEST_COLUMNS["year"] == "INT"
    assert MANIFEST_COLUMNS["row_count"] == "BIGINT"


def test_build_manifest_ddl_has_create_and_pk():
    create, pk = build_manifest_ddl("dbo")
    assert create.startswith("CREATE TABLE dbo._load_manifest (")
    assert "data_type NVARCHAR(16)" in create
    assert not create.rstrip().endswith(";")
    assert "ALTER TABLE dbo._load_manifest ADD CONSTRAINT" in pk
    assert "PRIMARY KEY (data_type, year, month)" in pk
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/taxi_loader/test_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_loader.manifest'`.

- [ ] **Step 3: Implement `manifest.py`**

Create `loader/src/taxi_loader/manifest.py`:
```python
"""The _load_manifest bookkeeping table: create / read / write.

One row per loaded month; PK (data_type, year, month). All access goes through
the mssql extension functions (mssql_scan for reads, mssql_exec for writes) to
avoid the catalog metadata cache returning stale results within a session.
"""
from __future__ import annotations

import duckdb

from taxi_shared.sql_generator import generate_create_table_sql

from taxi_loader.connection import ATTACH_NAME, ConnConfig, _sql_str
from taxi_loader.reconcile import ManifestRow

MANIFEST_TABLE = "_load_manifest"

# Explicit, bounded, PK-compatible types (see plan design decision #2).
MANIFEST_COLUMNS: dict[str, str] = {
    "data_type": "NVARCHAR(16)",
    "year": "INT",
    "month": "INT",
    "source_file": "NVARCHAR(400)",
    "row_count": "BIGINT",
    "loaded_at": "DATETIME2",
}


def manifest_fq(schema: str) -> str:
    return f"{schema}.{MANIFEST_TABLE}"


def build_manifest_ddl(schema: str) -> list[str]:
    fq = manifest_fq(schema)
    create = generate_create_table_sql(fq, MANIFEST_COLUMNS).rstrip(";\n ")
    pk = (
        f"ALTER TABLE {fq} ADD CONSTRAINT PK_{schema}_{MANIFEST_TABLE} "
        f"PRIMARY KEY (data_type, year, month)"
    )
    return [create, pk]


def _exec(conn: duckdb.DuckDBPyConnection, sql: str) -> None:
    conn.execute(f"SELECT mssql_exec('{ATTACH_NAME}', ?)", [sql])


def ensure_manifest_table(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig) -> None:
    fq = manifest_fq(cfg.schema)
    exists = conn.execute(
        f"SELECT o FROM mssql_scan('{ATTACH_NAME}', ?)",
        [f"SELECT OBJECT_ID('{_sql_str(fq)}','U') AS o"],
    ).fetchone()
    if exists and exists[0] is not None:
        return
    for stmt in build_manifest_ddl(cfg.schema):
        _exec(conn, stmt)


def read_manifest(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig,
                  data_type: str) -> list[ManifestRow]:
    fq = manifest_fq(cfg.schema)
    query = (
        f"SELECT year, month, row_count FROM {fq} "
        f"WHERE data_type = '{_sql_str(data_type)}'"
    )
    rows = conn.execute(
        f"SELECT year, month, row_count FROM mssql_scan('{ATTACH_NAME}', ?)",
        [query],
    ).fetchall()
    return [ManifestRow(int(y), int(m), int(rc)) for (y, m, rc) in rows]


def write_month_row(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig,
                    data_type: str, year: int, month: int,
                    source_file: str, row_count: int) -> None:
    fq = manifest_fq(cfg.schema)
    stmt = (
        f"INSERT INTO {fq} (data_type, year, month, source_file, row_count, loaded_at) "
        f"VALUES ('{_sql_str(data_type)}', {int(year)}, {int(month)}, "
        f"'{_sql_str(source_file)}', {int(row_count)}, SYSUTCDATETIME())"
    )
    _exec(conn, stmt)


def delete_year_rows(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig,
                     data_type: str, year: int) -> None:
    fq = manifest_fq(cfg.schema)
    stmt = (
        f"DELETE FROM {fq} "
        f"WHERE data_type = '{_sql_str(data_type)}' AND year = {int(year)}"
    )
    _exec(conn, stmt)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --extra test pytest tests/taxi_loader/test_manifest.py -v`
Expected: PASS (the three no-DB tests). Round-trip behavior is covered by Task 8's integration suite.

- [ ] **Step 5: Commit**

```bash
git add loader/src/taxi_loader/manifest.py tests/taxi_loader/test_manifest.py
git commit -m "feat(loader): _load_manifest table create/read/write via mssql extension"
```

---

### Task 7: `load.py` executor — COPY builders + `execute_year_plan`

**Files:**
- Modify: `loader/src/taxi_loader/load.py` (add the executor half)
- Test: `tests/taxi_loader/test_load.py` (COPY/URL string builders, no DB)

**Interfaces:**
- Consumes: `reconcile.{YearPlan, MonthFile, SKIP, APPEND, RELOAD}`, `connection.{ConnConfig, ATTACH_NAME, _sql_str}`, `manifest.{write_month_row, delete_year_rows}`, plus `build_create_table_sql` from Task 3.
- Produces (relied on by `cli.py`):
  - `dest_url(schema: str, table: str) -> str` → `mssql://mssql/<schema>/<table>`
  - `year_table(data_type: str, year: int) -> str` → `<type>_<year>`
  - `build_copy_sql(parquet_paths, dest_url, *, create_table, replace, flush_rows, tablock) -> str`
  - `parquet_row_count(conn, path) -> int`
  - `table_exists(conn, cfg, table: str) -> bool`
  - `count_year_table(conn, cfg, table: str) -> int`
  - `execute_year_plan(conn, cfg, data_type, plan: YearPlan, *, flush_rows: int) -> int`

- [ ] **Step 1: Write the failing no-DB builder tests**

Create `tests/taxi_loader/test_load.py`:
```python
import duckdb

from taxi_loader.load import (
    build_copy_sql, dest_url, parquet_row_count, year_table,
)


def test_year_table_and_dest_url():
    assert year_table("yellow", 2024) == "yellow_2024"
    assert dest_url("dbo", "yellow_2024") == "mssql://mssql/dbo/yellow_2024"


def test_build_copy_sql_append_options():
    sql = build_copy_sql(
        ["/a/2024-01.parquet"], "mssql://mssql/dbo/yellow_2024",
        create_table=False, replace=False, flush_rows=100000, tablock=True,
    )
    assert "read_parquet(['/a/2024-01.parquet'])" in sql
    assert "TO 'mssql://mssql/dbo/yellow_2024'" in sql
    assert "FORMAT 'bcp'" in sql
    assert "CREATE_TABLE false" in sql
    assert "REPLACE false" in sql
    assert "FLUSH_ROWS 100000" in sql
    assert "TABLOCK true" in sql


def test_build_copy_sql_multi_file_list():
    sql = build_copy_sql(
        ["/a/2024-01.parquet", "/a/2024-02.parquet"], "mssql://mssql/dbo/yellow_2024",
        create_table=False, replace=False, flush_rows=50000, tablock=True,
    )
    assert "'/a/2024-01.parquet', '/a/2024-02.parquet'" in sql


def test_parquet_row_count(tmp_path):
    conn = duckdb.connect(":memory:")
    p = tmp_path / "x.parquet"
    conn.execute(f"COPY (SELECT i FROM range(7) t(i)) TO '{p}' (FORMAT PARQUET)")
    assert parquet_row_count(conn, p) == 7
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/taxi_loader/test_load.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_copy_sql'`.

- [ ] **Step 3: Add the executor half to `load.py`**

Append to `loader/src/taxi_loader/load.py` (keep the existing DDL functions and imports; add these imports at the top: `from taxi_loader.connection import ATTACH_NAME, ConnConfig, _sql_str`, `from taxi_loader import manifest`, `from taxi_loader.reconcile import APPEND, RELOAD, SKIP, YearPlan`):
```python
def year_table(data_type: str, year: int) -> str:
    return f"{data_type}_{year}"


def dest_url(schema: str, table: str) -> str:
    return f"mssql://{ATTACH_NAME}/{schema}/{table}"


def build_copy_sql(parquet_paths, dest, *, create_table: bool, replace: bool,
                   flush_rows: int, tablock: bool) -> str:
    files = ", ".join(f"'{_sql_str(str(p))}'" for p in parquet_paths)
    return (
        f"COPY (SELECT * FROM read_parquet([{files}])) "
        f"TO '{_sql_str(dest)}' "
        f"(FORMAT 'bcp', CREATE_TABLE {str(create_table).lower()}, "
        f"REPLACE {str(replace).lower()}, FLUSH_ROWS {int(flush_rows)}, "
        f"TABLOCK {str(tablock).lower()})"
    )


def parquet_row_count(conn: duckdb.DuckDBPyConnection, path) -> int:
    row = conn.execute(
        f"SELECT num_rows FROM parquet_file_metadata('{path}')"
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _fq(cfg: ConnConfig, table: str) -> str:
    return f"{cfg.schema}.{table}"


def table_exists(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig, table: str) -> bool:
    fq = _fq(cfg, table)
    row = conn.execute(
        f"SELECT o FROM mssql_scan('{ATTACH_NAME}', ?)",
        [f"SELECT OBJECT_ID('{_sql_str(fq)}','U') AS o"],
    ).fetchone()
    return bool(row and row[0] is not None)


def count_year_table(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig, table: str) -> int:
    if not table_exists(conn, cfg, table):
        return 0
    fq = _fq(cfg, table)
    row = conn.execute(
        f"SELECT c FROM mssql_scan('{ATTACH_NAME}', ?)",
        [f"SELECT COUNT_BIG(*) AS c FROM {fq}"],
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _ensure_table(conn, cfg, table: str, sample_parquet) -> None:
    if not table_exists(conn, cfg, table):
        ddl = build_create_table_sql(conn, _fq(cfg, table), sample_parquet)
        conn.execute(f"SELECT mssql_exec('{ATTACH_NAME}', ?)", [ddl])


def execute_year_plan(conn: duckdb.DuckDBPyConnection, cfg: ConnConfig,
                      data_type: str, plan: YearPlan, *, flush_rows: int) -> int:
    """Execute one YearPlan. Returns rows loaded. Writes manifest rows per the
    durability model (append: manifest row only after that month's COPY; reload:
    drop -> create -> delete manifest year -> COPY all -> write manifest all)."""
    if plan.action == SKIP or not plan.months:
        if plan.action == RELOAD:
            # Whole year vanished from disk: rebuild to empty + clear manifest.
            table = year_table(data_type, plan.year)
            conn.execute(f"SELECT mssql_exec('{ATTACH_NAME}', ?)",
                         [f"DROP TABLE IF EXISTS {_fq(cfg, table)}"])
            manifest.delete_year_rows(conn, cfg, data_type, plan.year)
        return 0

    table = year_table(data_type, plan.year)
    fq = _fq(cfg, table)
    url = dest_url(cfg.schema, table)
    loaded = 0

    if plan.action == RELOAD:
        conn.execute(f"SELECT mssql_exec('{ATTACH_NAME}', ?)",
                     [f"DROP TABLE IF EXISTS {fq}"])
        ddl = build_create_table_sql(conn, fq, plan.months[0].path)
        conn.execute(f"SELECT mssql_exec('{ATTACH_NAME}', ?)", [ddl])
        manifest.delete_year_rows(conn, cfg, data_type, plan.year)
        copy_sql = build_copy_sql(
            [m.path for m in plan.months], url,
            create_table=False, replace=False, flush_rows=flush_rows, tablock=True,
        )
        conn.execute(copy_sql)
        for m in plan.months:
            manifest.write_month_row(conn, cfg, data_type, m.year, m.month,
                                     m.path, m.source_row_count)
            loaded += m.source_row_count
        return loaded

    # APPEND: ensure the table exists (fresh year), then load month-by-month so a
    # month's manifest row is written only after its own COPY succeeds.
    _ensure_table(conn, cfg, table, plan.months[0].path)
    for m in plan.months:
        copy_sql = build_copy_sql(
            [m.path], url,
            create_table=False, replace=False, flush_rows=flush_rows, tablock=True,
        )
        conn.execute(copy_sql)
        manifest.write_month_row(conn, cfg, data_type, m.year, m.month,
                                 m.path, m.source_row_count)
        loaded += m.source_row_count
    return loaded
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --extra test pytest tests/taxi_loader/test_load.py -v`
Expected: PASS (all four builder tests).

- [ ] **Step 5: Commit**

```bash
git add loader/src/taxi_loader/load.py tests/taxi_loader/test_load.py
git commit -m "feat(loader): COPY builders + execute_year_plan (append/reload)"
```

---

### Task 8: `cli.py` (arg parsing, dry-run, exit codes) + integration suite

**Files:**
- Modify: `loader/src/taxi_loader/cli.py` (replace the stub)
- Test: `tests/taxi_loader/test_cli.py` (no-DB: discovery, arg parse, missing-password)
- Test: `tests/taxi_loader/test_load_integration.py` (env-gated end-to-end)

**Interfaces:**
- Consumes: everything from Tasks 3–7.
- Produces: `main() -> int`; helpers `discover_month_files(conn, input_dir, data_type) -> list[MonthFile]`, `parse_args(argv) -> argparse.Namespace`.

- [ ] **Step 1: Write the failing no-DB CLI tests**

Create `tests/taxi_loader/test_cli.py`:
```python
import duckdb
import pytest

from taxi_loader.cli import discover_month_files, main, parse_args


def test_parse_args_defaults():
    ns = parse_args([])
    assert ns.data_type is None
    assert ns.host == "localhost"
    assert ns.port == 1433
    assert ns.database == "taxi"
    assert ns.schema == "dbo"
    assert ns.user == "sa"
    assert ns.input_dir == "raw-normalized"
    assert ns.flush_rows == 100000
    assert ns.full_refresh is False
    assert ns.dry_run is False


def test_discover_month_files(normalized_family):
    conn = duckdb.connect(":memory:")
    months = discover_month_files(conn, normalized_family, "yellow")
    got = sorted((m.year, m.month, m.source_row_count) for m in months)
    assert got == [(2023, 1, 3), (2023, 2, 4), (2024, 1, 5)]


def test_missing_password_is_exit_2(monkeypatch, normalized_family):
    monkeypatch.delenv("MSSQL_PASSWORD", raising=False)
    rc = main(["yellow", "--input-dir", str(normalized_family)])
    assert rc == 2


def test_bad_schema_is_exit_2(monkeypatch, normalized_family):
    monkeypatch.setenv("MSSQL_PASSWORD", "pw")
    rc = main(["yellow", "--schema", "bad-schema", "--input-dir", str(normalized_family)])
    assert rc == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/taxi_loader/test_cli.py -v`
Expected: FAIL — `ImportError: cannot import name 'discover_month_files'` (and `parse_args`).

- [ ] **Step 3: Implement `cli.py`**

Replace `loader/src/taxi_loader/cli.py`:
```python
"""Entry point for the `taxi-load` command.

taxi-load [TYPE]  — bulk-load raw-normalized/<type>/<year>/*.parquet into SQL
Server, one table per year per type, idempotently. TYPE omitted = all four.
Password comes from MSSQL_PASSWORD only.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import duckdb

from taxi_loader import load, manifest
from taxi_loader.connection import (
    ConnConfig, LoaderConfigError, LoaderError, attach_target, connect_duckdb,
    ensure_database, validate_identifier,
)
from taxi_loader.reconcile import APPEND, RELOAD, SKIP, MonthFile, reconcile
from taxi_shared.type_mapping import TypeMappingError

DATA_TYPES = ("yellow", "green", "fhv", "fhvhv")
_MONTH_RE = re.compile(r"(\d{4})-(\d{2})")


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="taxi-load",
        description="Bulk-load normalized TLC parquet into SQL Server.",
    )
    p.add_argument("data_type", nargs="?",
                   help="yellow/green/fhv/fhvhv. Omit to load all four.")
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=1433)
    p.add_argument("--database", default="taxi")
    p.add_argument("--schema", default="dbo")
    p.add_argument("--user", default="sa")
    p.add_argument("--input-dir", default="raw-normalized",
                   help="reads <input-dir>/<type>/<year>/*.parquet")
    p.add_argument("--flush-rows", type=int, default=100000,
                   help="BCP commit batch size")
    p.add_argument("--full-refresh", action="store_true",
                   help="force truncate+reload of every year")
    p.add_argument("--dry-run", action="store_true",
                   help="print the reconciliation plan and exit without writing")
    return p.parse_args(argv)


def discover_month_files(conn: duckdb.DuckDBPyConnection, input_dir,
                         data_type: str) -> list[MonthFile]:
    base = Path(input_dir) / data_type
    months: list[MonthFile] = []
    if not base.exists():
        return months
    for f in sorted(base.rglob("*.parquet")):
        m = _MONTH_RE.search(f.name)
        if not m:
            continue
        year, month = int(m.group(1)), int(m.group(2))
        months.append(MonthFile(year, month, str(f),
                                load.parquet_row_count(conn, f)))
    return months


def _describe_plan(data_type: str, plans) -> None:
    for plan in plans:
        if plan.action == SKIP:
            print(f"  {data_type} {plan.year}: skip")
        elif plan.action == APPEND:
            mm = ", ".join(f"{m.month:02d}" for m in plan.months)
            print(f"  {data_type} {plan.year}: append month(s) {mm}")
        else:
            print(f"  {data_type} {plan.year}: truncate + reload "
                  f"({len(plan.months)} month file(s))")


def _process_type(conn, cfg, data_type: str, input_dir: str,
                  flush_rows: int, full_refresh: bool, dry_run: bool) -> int:
    disk = discover_month_files(conn, input_dir, data_type)
    if not disk:
        print(f"{data_type}: no parquet under {input_dir}/{data_type}, skipping")
        return 0

    manifest_rows = manifest.read_manifest(conn, cfg, data_type)
    years = sorted({m.year for m in disk} | {r.year for r in manifest_rows})
    table_counts = {
        y: load.count_year_table(conn, cfg, load.year_table(data_type, y))
        for y in years
    }
    plans = reconcile(disk, manifest_rows, table_counts, full_refresh)

    if dry_run:
        print(f"{data_type}: plan")
        _describe_plan(data_type, plans)
        return 0

    total = 0
    for plan in plans:
        total += load.execute_year_plan(conn, cfg, data_type, plan,
                                        flush_rows=flush_rows)
    n_reload = sum(1 for p in plans if p.action == RELOAD)
    n_append = sum(1 for p in plans if p.action == APPEND)
    print(f"{data_type}: {total} row(s) loaded "
          f"({n_append} append, {n_reload} reload year(s)).")
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)

    try:
        schema = validate_identifier(args.schema, "schema")
        database = validate_identifier(args.database, "database")
    except LoaderConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    password = os.environ.get("MSSQL_PASSWORD")
    if not password:
        print("error: MSSQL_PASSWORD environment variable is required",
              file=sys.stderr)
        return 2

    types = [args.data_type] if args.data_type else list(DATA_TYPES)

    cfg = ConnConfig(host=args.host, port=args.port, database=database,
                     schema=schema, user=args.user, password=password)

    # Connection / provisioning failures are exit 2 (nothing loaded).
    try:
        conn = connect_duckdb()
        ensure_database(conn, cfg)
        attach_target(conn, cfg)
        manifest.ensure_manifest_table(conn, cfg)
    except LoaderError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    overall = 0
    for data_type in types:
        try:
            _process_type(conn, cfg, data_type, args.input_dir,
                          args.flush_rows, args.full_refresh, args.dry_run)
        except TypeMappingError as e:
            print(f"error: {data_type}: {e}", file=sys.stderr)
            overall = max(overall, 2)
        except (duckdb.Error, LoaderError) as e:
            print(f"error: {data_type} failed mid-load: {e}", file=sys.stderr)
            overall = max(overall, 1)
    return overall


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the no-DB CLI tests**

Run: `uv run --extra test pytest tests/taxi_loader/test_cli.py -v`
Expected: PASS (all four). `test_missing_password_is_exit_2` and `test_bad_schema_is_exit_2` return 2 before any connection attempt.

- [ ] **Step 5: Write the env-gated integration suite**

Create `tests/taxi_loader/test_load_integration.py`:
```python
"""End-to-end against SQL Server in Docker. Skips when MSSQL_PASSWORD is unset,
so `pytest` stays green on a laptop with no SQL Server.

Bring a server up first, e.g.:
  docker run -d --name mssql-it -e ACCEPT_EULA=Y \
    -e MSSQL_SA_PASSWORD='Str0ng_Passw0rd!' -p 1433:1433 \
    mcr.microsoft.com/mssql/server:2022-latest
Then: MSSQL_PASSWORD='Str0ng_Passw0rd!' uv run --extra test pytest tests/taxi_loader/test_load_integration.py
"""
from __future__ import annotations

import os
import uuid

import duckdb
import pytest

from taxi_loader import load, manifest
from taxi_loader.cli import main
from taxi_loader.connection import (
    ConnConfig, attach_target, connect_duckdb, ensure_database,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("MSSQL_PASSWORD"),
    reason="MSSQL_PASSWORD unset; skipping SQL Server integration tests",
)


@pytest.fixture
def cfg():
    # Unique schema per test run for isolation within the shared 'taxi' DB.
    schema = "t" + uuid.uuid4().hex[:8]
    return ConnConfig(
        host=os.environ.get("MSSQL_HOST", "localhost"),
        port=int(os.environ.get("MSSQL_PORT", "1433")),
        database="taxi", schema=schema, user=os.environ.get("MSSQL_USER", "sa"),
        password=os.environ["MSSQL_PASSWORD"],
    )


@pytest.fixture
def prepared(cfg):
    conn = connect_duckdb()
    ensure_database(conn, cfg)
    attach_target(conn, cfg)          # creates the unique schema
    manifest.ensure_manifest_table(conn, cfg)
    yield conn, cfg
    conn.close()


def _count(conn, cfg, table):
    return load.count_year_table(conn, cfg, table)


def _run(cfg, root, extra=None):
    argv = ["yellow", "--host", cfg.host, "--port", str(cfg.port),
            "--database", cfg.database, "--schema", cfg.schema,
            "--user", cfg.user, "--input-dir", str(root)]
    return main(argv + (extra or []))


def test_end_to_end_load_counts_and_manifest(prepared, normalized_family):
    conn, cfg = prepared
    assert _run(cfg, normalized_family) == 0
    assert _count(conn, cfg, "yellow_2023") == 7      # 3 + 4
    assert _count(conn, cfg, "yellow_2024") == 5
    rows = manifest.read_manifest(conn, cfg, "yellow")
    assert sorted((r.year, r.month, r.row_count) for r in rows) == \
        [(2023, 1, 3), (2023, 2, 4), (2024, 1, 5)]


def test_immediate_rerun_is_full_noop(prepared, normalized_family):
    conn, cfg = prepared
    assert _run(cfg, normalized_family) == 0
    assert _run(cfg, normalized_family) == 0
    assert _count(conn, cfg, "yellow_2023") == 7      # unchanged, no duplicates
    assert _count(conn, cfg, "yellow_2024") == 5


def test_new_month_appends_only_it(prepared, normalized_family):
    conn, cfg = prepared
    assert _run(cfg, normalized_family) == 0
    # Drop a new month into 2024.
    from tests.taxi_loader.conftest import write_month  # helper
    write_month(duckdb.connect(":memory:"), normalized_family, "yellow", 2024, 2, rows=8)
    assert _run(cfg, normalized_family) == 0
    assert _count(conn, cfg, "yellow_2024") == 13      # 5 + 8
    assert _count(conn, cfg, "yellow_2023") == 7       # untouched


def test_changed_month_reloads_whole_year(prepared, normalized_family):
    conn, cfg = prepared
    assert _run(cfg, normalized_family) == 0
    # Rewrite 2023-01 with a different row count -> whole 2023 rebuilds.
    write = duckdb.connect(":memory:")
    from tests.taxi_loader.conftest import write_month
    write_month(write, normalized_family, "yellow", 2023, 1, rows=10)
    assert _run(cfg, normalized_family) == 0
    assert _count(conn, cfg, "yellow_2023") == 14      # 10 + 4
    rows = {(r.month): r.row_count for r in manifest.read_manifest(conn, cfg, "yellow")
            if r.year == 2023}
    assert rows == {1: 10, 2: 4}


def test_partial_load_recovery_via_integrity_check(prepared, normalized_family):
    conn, cfg = prepared
    assert _run(cfg, normalized_family) == 0
    # Simulate a partial prior load: extra committed rows with no manifest row.
    table = load.year_table("yellow", 2024)
    conn.execute("SELECT mssql_exec('mssql', ?)",
                 [f"INSERT INTO {cfg.schema}.{table} "
                  f"(vendorid, tpep_pickup_datetime, trip_distance, store_and_fwd_flag) "
                  f"VALUES (99, SYSUTCDATETIME(), 1.0, 'N')"])
    assert _count(conn, cfg, table) == 6               # 5 + 1 injected
    # Next run detects table(6) != manifest(5) -> reload year cleanly.
    assert _run(cfg, normalized_family) == 0
    assert _count(conn, cfg, table) == 5               # rebuilt, no duplicate/injected row


def test_dry_run_touches_nothing(prepared, normalized_family, capsys):
    conn, cfg = prepared
    assert _run(cfg, normalized_family, extra=["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "append" in out
    assert not load.table_exists(conn, cfg, "yellow_2024")   # nothing created
    assert cfg.password not in out                            # never logged
```

- [ ] **Step 6: Run the full loader suite**

Run (no server — integration skips):
```bash
uv run --extra test pytest tests/taxi_loader/ -v
```
Expected: all no-DB tests PASS; integration tests SKIPPED.

Then, with a server up (see the docstring in `test_load_integration.py`):
```bash
MSSQL_PASSWORD='Str0ng_Passw0rd!' uv run --extra test pytest tests/taxi_loader/test_load_integration.py -v
```
Expected: all integration tests PASS.

- [ ] **Step 7: Commit**

```bash
git add loader/src/taxi_loader/cli.py tests/taxi_loader/test_cli.py tests/taxi_loader/test_load_integration.py
git commit -m "feat(loader): taxi-load CLI (dry-run, exit codes) + integration suite"
```

---

### Task 9: Full-suite verification + guide pointer sanity

**Files:** none (verification task); optional docs follow-up noted below.

- [ ] **Step 1: Run the entire repo test suite**

Run: `uv run --extra test pytest -q`
Expected: PASS with the loader's integration tests SKIPPED (no server). No existing tests regressed.

- [ ] **Step 2: Smoke the console script**

Run:
```bash
uv run taxi-load --help
```
Expected: argparse help listing `[data_type]`, `--host`, `--port`, `--database`, `--schema`, `--user`, `--input-dir`, `--flush-rows`, `--full-refresh`, `--dry-run`.

- [ ] **Step 3: Verify no password leakage path**

Run: `uv run --extra test pytest tests/taxi_loader/test_load_integration.py::test_dry_run_touches_nothing -v` (with server) — asserts the password never appears in output. Without a server this is SKIPPED; note it as a CI-only guarantee.

- [ ] **Step 4: Commit any final tidy-ups**

```bash
git add -A
git commit -m "test(loader): full-suite verification pass"
```

> **Docs note (out of scope for this plan, hand off to docs sub-project):** the `loader/README.md` points at `guides/loader/`, which does not exist yet. Creating that MkDocs guide and adding it to `mkdocs.yml` nav belongs to the docs effort, mirroring `guides/normalize.md`.

---

## Self-review against the spec

- **Component layout** (`cli/connection/manifest/reconcile/load` + `README`): Tasks 2–8. ✅
- **`mssql` extension use** (INSTALL/LOAD, ATTACH, `COPY (FORMAT 'bcp')`, `mssql_exec`/`mssql_scan`): Tasks 1, 5, 6, 7. ✅
- **Version pin + startup assertion:** `EXPECTED_MSSQL_EXT_VERSION`, Task 5; value confirmed in Task 1. ✅
- **CLI surface** (all flags, TYPE optional, password from env only): Task 8 `parse_args`. ✅
- **DB/schema/table naming + provisioning** (create DB if absent, create non-`dbo` schema): Task 5. ✅
- **Explicit DDL via `taxi_shared`** (DESCRIBE → map → CREATE, `CREATE_TABLE false`): Task 3 + Task 7. ✅ (design decision #1 keeps `CREATE_TABLE false` on reload too.)
- **Manifest table** (columns, PK, create/read/write): Task 6. ✅ (design decision #2 for PK-compatible types.)
- **Durability model** (batched commits, manifest-after-COPY, integrity gate, drop-year on partial): Task 7 `execute_year_plan` + Task 4 gate + `test_partial_load_recovery_via_integrity_check`. ✅
- **Per-(type, year) decision table** (every row): Task 4 `reconcile` + `test_reconcile.py`. ✅
- **`reconcile` as a pure function** (three inputs, no DB): Task 4. ✅
- **Exit codes** (0/1/2 with precedence): Task 8 `main`. ✅
- **Testing strategy** (`test_reconcile` no-DB bulk; integration idempotency/append/reload; skip without env): Tasks 4, 8. ✅ Target count ~20–25: ~10 reconcile + ~15 others. ✅
- **Success criteria** (each bullet): covered by the integration suite in Task 8. ✅
