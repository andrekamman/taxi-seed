# K6 SQL Server Load Testing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a config-driven preprocessor that converts parquet taxi data into chunked JSON for K6 load testing against multiple SQL Server instances.

**Architecture:** Python preprocessor reads YAML config + parquet files via DuckDB, outputs chunked JSON data, scenario manifests, CREATE TABLE scripts, and a generated K6 test script. K6 uses xk6-sql with the MSSQL driver to execute SQL operations with configurable workload mix, think times, and VU profiles.

**Tech Stack:** Python 3.12+, DuckDB, PyYAML, K6, xk6-sql, xk6-sql-driver-mssql

**Spec:** `2026-03-25-k6-sql-load-testing-design.md` (sibling file in this handoff dir)

---

## File Structure

```
loadtest/
  __init__.py                  # package marker
  config.py                    # YAML config parsing and validation
  type_mapping.py              # DuckDB/parquet -> SQL Server type mapping
  data_export.py               # parquet reading + chunked JSON export
  sql_generator.py             # SQL templates + CREATE TABLE scripts
  k6_generator.py              # scenario manifests + test.js generation
  preprocess.py                # CLI entry point, orchestrates all steps
tests/
  loadtest/
    __init__.py
    test_config.py
    test_type_mapping.py
    test_data_export.py
    test_sql_generator.py
    test_k6_generator.py
    test_preprocess_integration.py
    fixtures/
      sample_config.yaml       # valid test config
      sample.parquet           # small parquet fixture (generated in test setup)
```

---

## Task 1: Project setup and dependencies

**Files:**
- Modify: `pyproject.toml`
- Create: `loadtest/__init__.py`
- Create: `tests/loadtest/__init__.py`

- [ ] **Step 1: Add dependencies to pyproject.toml**

Add `pyyaml` to the project dependencies:

```toml
dependencies = [
    "duckdb>=1.4.4",
    "pyyaml>=6.0",
]
```

Also add a test dependency section:

```toml
[project.optional-dependencies]
test = ["pytest>=8.0"]
```

And add a scripts entry point:

```toml
[project.scripts]
preprocess = "loadtest.preprocess:main"
```

- [ ] **Step 2: Create package directories**

```bash
mkdir -p loadtest tests/loadtest tests/loadtest/fixtures
touch loadtest/__init__.py tests/loadtest/__init__.py
```

- [ ] **Step 3: Install dependencies**

```bash
uv sync
uv pip install -e ".[test]"
```

- [ ] **Step 4: Verify pytest runs**

Run: `uv run pytest tests/ -v`
Expected: 0 tests collected, no errors

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml loadtest/ tests/
git commit -m "feat: scaffold loadtest package with dependencies"
```

---

## Task 2: Config parsing and validation (`config.py`)

**Files:**
- Create: `loadtest/config.py`
- Create: `tests/loadtest/test_config.py`
- Create: `tests/loadtest/fixtures/sample_config.yaml`

- [ ] **Step 1: Create the test fixture config**

Write `tests/loadtest/fixtures/sample_config.yaml`:

```yaml
data_sources:
  yellow_trips:
    path: "tests/loadtest/fixtures/*.parquet"
    chunk_size: 100
    key_columns: [pickup_time, dropoff_time]
    columns:
      pickup_time: tpep_pickup_datetime
      dropoff_time: tpep_dropoff_datetime
      passenger_count: passenger_count
      trip_distance: trip_distance
      fare_amount: fare_amount
      tip_amount: tip_amount

targets:
  test_server:
    host: localhost
    port: 1433
    database: test_db
    username: sa
    password: ${MSSQL_PASSWORD}
    table: taxi_trips

scenarios:
  basic_load:
    target: test_server
    data_source: yellow_trips
    ordering: parallel
    workload:
      insert: 80
      update: 15
      delete: 5
    think_time:
      min: 200ms
      max: 1s
    k6:
      executor: constant-vus
      vus: 5
      duration: 1m
```

- [ ] **Step 2: Write failing tests for config parsing**

Write `tests/loadtest/test_config.py`:

```python
import pytest
from pathlib import Path

from loadtest.config import load_config, validate_config, ConfigError


FIXTURES = Path(__file__).parent / "fixtures"


def test_load_config_parses_yaml():
    config = load_config(FIXTURES / "sample_config.yaml")
    assert "data_sources" in config
    assert "targets" in config
    assert "scenarios" in config
    assert "yellow_trips" in config["data_sources"]


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config(Path("/nonexistent/config.yaml"))


def test_validate_config_valid():
    config = load_config(FIXTURES / "sample_config.yaml")
    # Should not raise
    validate_config(config)


def test_validate_config_workload_not_100():
    config = load_config(FIXTURES / "sample_config.yaml")
    config["scenarios"]["basic_load"]["workload"] = {
        "insert": 50,
        "update": 20,
        "delete": 10,
    }
    with pytest.raises(ConfigError, match="must sum to 100"):
        validate_config(config)


def test_validate_config_missing_target_ref():
    config = load_config(FIXTURES / "sample_config.yaml")
    config["scenarios"]["basic_load"]["target"] = "nonexistent_server"
    with pytest.raises(ConfigError, match="nonexistent_server"):
        validate_config(config)


def test_validate_config_missing_data_source_ref():
    config = load_config(FIXTURES / "sample_config.yaml")
    config["scenarios"]["basic_load"]["data_source"] = "nonexistent_source"
    with pytest.raises(ConfigError, match="nonexistent_source"):
        validate_config(config)


def test_validate_config_key_columns_not_in_columns():
    config = load_config(FIXTURES / "sample_config.yaml")
    config["data_sources"]["yellow_trips"]["key_columns"] = ["nonexistent_col"]
    with pytest.raises(ConfigError, match="nonexistent_col"):
        validate_config(config)


def test_validate_config_missing_required_fields():
    config = {"data_sources": {}, "targets": {}}
    # Missing scenarios
    with pytest.raises(ConfigError, match="scenarios"):
        validate_config(config)


def test_validate_config_invalid_ordering():
    config = load_config(FIXTURES / "sample_config.yaml")
    config["scenarios"]["basic_load"]["ordering"] = "random"
    with pytest.raises(ConfigError, match="ordering"):
        validate_config(config)


def test_parse_think_time():
    from loadtest.config import parse_duration_ms
    assert parse_duration_ms("200ms") == 200
    assert parse_duration_ms("1s") == 1000
    assert parse_duration_ms("2s") == 2000
    assert parse_duration_ms("1.5s") == 1500
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/loadtest/test_config.py -v`
Expected: FAIL — `loadtest.config` does not exist yet

- [ ] **Step 4: Implement config.py**

Write `loadtest/config.py`:

```python
"""YAML config parsing and validation for K6 load test preprocessor."""

import re
from pathlib import Path

import yaml


class ConfigError(Exception):
    """Raised when config validation fails."""
    pass


def load_config(path: Path) -> dict:
    """Load and parse a YAML config file."""
    with open(path) as f:
        return yaml.safe_load(f)


def parse_duration_ms(value: str) -> float:
    """Parse a duration string like '200ms' or '1.5s' to milliseconds."""
    match = re.match(r"^(\d+(?:\.\d+)?)(ms|s)$", value)
    if not match:
        raise ConfigError(f"Invalid duration format: {value!r} (expected e.g. '200ms' or '1s')")
    num, unit = float(match.group(1)), match.group(2)
    return num if unit == "ms" else num * 1000


def validate_config(config: dict) -> None:
    """Validate config structure and cross-references."""
    # Required top-level sections
    for section in ("data_sources", "targets", "scenarios"):
        if section not in config:
            raise ConfigError(f"Missing required section: {section}")

    data_sources = config["data_sources"]
    targets = config["targets"]
    scenarios = config["scenarios"]

    # Validate data sources
    for name, ds in data_sources.items():
        for field in ("path", "columns", "key_columns"):
            if field not in ds:
                raise ConfigError(f"Data source {name!r}: missing required field {field!r}")
        # key_columns must reference mapped column names
        columns = ds["columns"]
        for kc in ds["key_columns"]:
            if kc not in columns:
                raise ConfigError(
                    f"Data source {name!r}: key_column {kc!r} not found in columns mapping"
                )
        # Glob must match at least one file
        import glob as globmod
        if not globmod.glob(ds["path"]):
            raise ConfigError(
                f"Data source {name!r}: path {ds['path']!r} matches no files"
            )

    # Validate targets
    for name, target in targets.items():
        for field in ("host", "port", "database", "username", "password", "table"):
            if field not in target:
                raise ConfigError(f"Target {name!r}: missing required field {field!r}")

    # Validate scenarios
    for name, scenario in scenarios.items():
        # Reference checks
        if scenario.get("target") not in targets:
            raise ConfigError(
                f"Scenario {name!r}: target {scenario.get('target')!r} not found in targets"
            )
        if scenario.get("data_source") not in data_sources:
            raise ConfigError(
                f"Scenario {name!r}: data_source {scenario.get('data_source')!r} "
                f"not found in data_sources"
            )

        # Ordering
        ordering = scenario.get("ordering", "parallel")
        if ordering not in ("parallel", "sequential"):
            raise ConfigError(
                f"Scenario {name!r}: ordering must be 'parallel' or 'sequential', "
                f"got {ordering!r}"
            )

        # Workload percentages
        workload = scenario.get("workload", {})
        total = sum(workload.values())
        if total != 100:
            raise ConfigError(
                f"Scenario {name!r}: workload percentages must sum to 100, got {total}"
            )

        # Think time
        think_time = scenario.get("think_time", {})
        for field in ("min", "max"):
            if field in think_time:
                parse_duration_ms(think_time[field])

        # K6 config must exist
        if "k6" not in scenario:
            raise ConfigError(f"Scenario {name!r}: missing required field 'k6'")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/loadtest/test_config.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add loadtest/config.py tests/loadtest/test_config.py tests/loadtest/fixtures/sample_config.yaml
git commit -m "feat: add config parsing and validation"
```

---

## Task 3: Type mapping (`type_mapping.py`)

**Files:**
- Create: `loadtest/type_mapping.py`
- Create: `tests/loadtest/test_type_mapping.py`

- [ ] **Step 1: Write failing tests**

Write `tests/loadtest/test_type_mapping.py`:

```python
import pytest

from loadtest.type_mapping import map_duckdb_to_mssql, TypeMappingError


def test_basic_types():
    assert map_duckdb_to_mssql("BIGINT") == "BIGINT"
    assert map_duckdb_to_mssql("INTEGER") == "INT"
    assert map_duckdb_to_mssql("DOUBLE") == "FLOAT"
    assert map_duckdb_to_mssql("FLOAT") == "REAL"
    assert map_duckdb_to_mssql("VARCHAR") == "NVARCHAR(MAX)"
    assert map_duckdb_to_mssql("TIMESTAMP") == "DATETIME2"
    assert map_duckdb_to_mssql("BOOLEAN") == "BIT"
    assert map_duckdb_to_mssql("DATE") == "DATE"
    assert map_duckdb_to_mssql("SMALLINT") == "SMALLINT"
    assert map_duckdb_to_mssql("TINYINT") == "TINYINT"
    assert map_duckdb_to_mssql("HUGEINT") == "DECIMAL(38,0)"


def test_decimal_with_precision():
    assert map_duckdb_to_mssql("DECIMAL(10,2)") == "DECIMAL(10,2)"
    assert map_duckdb_to_mssql("DECIMAL(18,4)") == "DECIMAL(18,4)"


def test_timestamp_with_timezone():
    assert map_duckdb_to_mssql("TIMESTAMP WITH TIME ZONE") == "DATETIMEOFFSET"
    assert map_duckdb_to_mssql("TIMESTAMP_TZ") == "DATETIMEOFFSET"


def test_int16_int8_aliases():
    assert map_duckdb_to_mssql("INT16") == "SMALLINT"
    assert map_duckdb_to_mssql("INT8") == "TINYINT"


def test_case_insensitive():
    assert map_duckdb_to_mssql("bigint") == "BIGINT"
    assert map_duckdb_to_mssql("Varchar") == "NVARCHAR(MAX)"


def test_unknown_type_raises():
    with pytest.raises(TypeMappingError, match="GEOMETRY"):
        map_duckdb_to_mssql("GEOMETRY")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/loadtest/test_type_mapping.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement type_mapping.py**

Write `loadtest/type_mapping.py`:

```python
"""Map DuckDB/parquet column types to SQL Server types."""

import re


class TypeMappingError(Exception):
    """Raised when a DuckDB type has no SQL Server equivalent."""
    pass


# Direct mappings (case-insensitive lookup)
_TYPE_MAP = {
    "BIGINT": "BIGINT",
    "INTEGER": "INT",
    "INT": "INT",
    "INT32": "INT",
    "DOUBLE": "FLOAT",
    "FLOAT": "REAL",
    "REAL": "REAL",
    "VARCHAR": "NVARCHAR(MAX)",
    "TEXT": "NVARCHAR(MAX)",
    "STRING": "NVARCHAR(MAX)",
    "TIMESTAMP": "DATETIME2",
    "TIMESTAMP_NS": "DATETIME2",
    "TIMESTAMP_S": "DATETIME2",
    "TIMESTAMP_MS": "DATETIME2",
    "TIMESTAMP WITH TIME ZONE": "DATETIMEOFFSET",
    "TIMESTAMP_TZ": "DATETIMEOFFSET",
    "BOOLEAN": "BIT",
    "BOOL": "BIT",
    "DATE": "DATE",
    "SMALLINT": "SMALLINT",
    "INT16": "SMALLINT",
    "TINYINT": "TINYINT",
    "INT8": "TINYINT",
    "HUGEINT": "DECIMAL(38,0)",
}


def map_duckdb_to_mssql(duckdb_type: str) -> str:
    """Map a DuckDB column type to the equivalent SQL Server type.

    Raises TypeMappingError for unmapped types.
    """
    normalized = duckdb_type.strip().upper()

    # Check direct mapping
    if normalized in _TYPE_MAP:
        return _TYPE_MAP[normalized]

    # Handle DECIMAL(p,s) pattern
    match = re.match(r"DECIMAL\((\d+),\s*(\d+)\)", normalized)
    if match:
        return f"DECIMAL({match.group(1)},{match.group(2)})"

    raise TypeMappingError(
        f"No SQL Server mapping for DuckDB type: {duckdb_type!r}. "
        f"Add it to the type mapping or adjust the column config."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/loadtest/test_type_mapping.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add loadtest/type_mapping.py tests/loadtest/test_type_mapping.py
git commit -m "feat: add DuckDB to SQL Server type mapping"
```

---

## Task 4: Data export — parquet to chunked JSON (`data_export.py`)

**Files:**
- Create: `loadtest/data_export.py`
- Create: `tests/loadtest/test_data_export.py`

- [ ] **Step 1: Write failing tests**

Write `tests/loadtest/test_data_export.py`:

```python
import json
import pytest
from pathlib import Path

import duckdb

from loadtest.data_export import export_chunks, get_schema


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_parquet(tmp_path):
    """Create a small parquet file for testing."""
    db = duckdb.connect()
    db.sql(f"""
        COPY (
            SELECT
                '2026-01-15 08:30:00'::TIMESTAMP AS tpep_pickup_datetime,
                '2026-01-15 09:15:00'::TIMESTAMP AS tpep_dropoff_datetime,
                2::INTEGER AS passenger_count,
                3.4::DOUBLE AS trip_distance,
                15.50::DOUBLE AS fare_amount,
                3.00::DOUBLE AS tip_amount
            FROM range(25)
        ) TO '{tmp_path}/test.parquet' (FORMAT PARQUET)
    """)
    return tmp_path


def test_export_chunks_creates_files(sample_parquet, tmp_path):
    output_dir = tmp_path / "output"
    columns = {
        "pickup_time": "tpep_pickup_datetime",
        "dropoff_time": "tpep_dropoff_datetime",
        "passenger_count": "passenger_count",
        "trip_distance": "trip_distance",
        "fare_amount": "fare_amount",
        "tip_amount": "tip_amount",
    }
    num_chunks = export_chunks(
        parquet_glob=str(sample_parquet / "*.parquet"),
        columns=columns,
        chunk_size=10,
        output_dir=output_dir / "yellow_trips",
    )
    assert num_chunks == 3  # 25 rows / 10 per chunk = 3 chunks
    assert (output_dir / "yellow_trips" / "chunk_0000.json").exists()
    assert (output_dir / "yellow_trips" / "chunk_0001.json").exists()
    assert (output_dir / "yellow_trips" / "chunk_0002.json").exists()


def test_export_chunks_json_format(sample_parquet, tmp_path):
    output_dir = tmp_path / "output"
    columns = {
        "pickup_time": "tpep_pickup_datetime",
        "fare_amount": "fare_amount",
    }
    export_chunks(
        parquet_glob=str(sample_parquet / "*.parquet"),
        columns=columns,
        chunk_size=100,
        output_dir=output_dir / "data",
    )
    with open(output_dir / "data" / "chunk_0000.json") as f:
        rows = json.load(f)
    assert len(rows) == 25
    assert "pickup_time" in rows[0]
    assert "fare_amount" in rows[0]
    # Should only have mapped columns, not originals
    assert "tpep_pickup_datetime" not in rows[0]


def test_export_chunks_no_matching_files(tmp_path):
    with pytest.raises(FileNotFoundError, match="No parquet files"):
        export_chunks(
            parquet_glob=str(tmp_path / "nonexistent" / "*.parquet"),
            columns={"a": "b"},
            chunk_size=10,
            output_dir=tmp_path / "output",
        )


def test_get_schema(sample_parquet):
    schema = get_schema(str(sample_parquet / "*.parquet"))
    assert "tpep_pickup_datetime" in schema
    assert "TIMESTAMP" in schema["tpep_pickup_datetime"].upper()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/loadtest/test_data_export.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement data_export.py**

Write `loadtest/data_export.py`:

```python
"""Read parquet files via DuckDB and export as chunked JSON."""

import json
from pathlib import Path

import duckdb


def get_schema(parquet_glob: str) -> dict[str, str]:
    """Get column name -> type mapping from parquet files.

    Returns dict like {"tpep_pickup_datetime": "TIMESTAMP", ...}.
    """
    db = duckdb.connect()
    rows = db.sql(f"DESCRIBE SELECT * FROM '{parquet_glob}'").fetchall()
    return {row[0]: row[1] for row in rows}


def export_chunks(
    parquet_glob: str,
    columns: dict[str, str],
    chunk_size: int,
    output_dir: Path,
) -> int:
    """Export parquet data as chunked JSON files.

    Args:
        parquet_glob: Glob pattern for parquet files (e.g. "raw/yellow/2026/*.parquet").
        columns: Mapping of {output_name: source_column_name}.
        chunk_size: Number of rows per chunk file.
        output_dir: Directory to write chunk_NNNN.json files.

    Returns:
        Number of chunk files created.

    Raises:
        FileNotFoundError: If glob matches no parquet files.
    """
    db = duckdb.connect()

    # Verify files exist
    try:
        total_rows = db.sql(f"SELECT COUNT(*) FROM '{parquet_glob}'").fetchone()[0]
    except (duckdb.IOException, duckdb.CatalogException, duckdb.BinderException):
        raise FileNotFoundError(f"No parquet files matched: {parquet_glob}")

    if total_rows == 0:
        raise FileNotFoundError(f"No parquet files matched: {parquet_glob}")

    # Build SELECT with column renaming
    select_parts = [
        f'"{source}" AS "{target}"' for target, source in columns.items()
    ]
    select_sql = ", ".join(select_parts)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    chunk_index = 0
    offset = 0

    while offset < total_rows:
        rows = db.sql(
            f"SELECT {select_sql} FROM '{parquet_glob}' LIMIT {chunk_size} OFFSET {offset}"
        ).fetchall()

        if not rows:
            break

        # Get column names from the mapping
        col_names = list(columns.keys())

        # Convert to list of dicts, handling timestamp serialization
        chunk_data = []
        for row in rows:
            row_dict = {}
            for i, name in enumerate(col_names):
                val = row[i]
                # Serialize timestamps as ISO 8601
                if hasattr(val, "isoformat"):
                    val = val.isoformat()
                row_dict[name] = val
            chunk_data.append(row_dict)

        chunk_path = output_dir / f"chunk_{chunk_index:04d}.json"
        with open(chunk_path, "w") as f:
            json.dump(chunk_data, f)

        chunk_index += 1
        offset += chunk_size

    return chunk_index
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/loadtest/test_data_export.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add loadtest/data_export.py tests/loadtest/test_data_export.py
git commit -m "feat: add parquet to chunked JSON export"
```

---

## Task 5: SQL generator (`sql_generator.py`)

**Files:**
- Create: `loadtest/sql_generator.py`
- Create: `tests/loadtest/test_sql_generator.py`

- [ ] **Step 1: Write failing tests**

Write `tests/loadtest/test_sql_generator.py`:

```python
import pytest

from loadtest.sql_generator import (
    generate_insert_sql,
    generate_update_sql,
    generate_delete_sql,
    generate_create_table_sql,
)


COLUMNS = {
    "pickup_time": "DATETIME2",
    "dropoff_time": "DATETIME2",
    "passenger_count": "INT",
    "trip_distance": "FLOAT",
    "fare_amount": "FLOAT",
    "tip_amount": "FLOAT",
}
KEY_COLUMNS = ["pickup_time", "dropoff_time"]
TABLE = "taxi_trips"


def test_generate_insert_sql():
    sql = generate_insert_sql(TABLE, COLUMNS)
    assert "INSERT INTO taxi_trips" in sql
    assert "pickup_time" in sql
    assert "tip_amount" in sql
    assert "@p1" in sql
    assert "@p6" in sql


def test_generate_update_sql():
    sql = generate_update_sql(TABLE, COLUMNS, KEY_COLUMNS)
    assert "UPDATE taxi_trips SET" in sql
    # Non-key columns should be in SET clause
    assert "passenger_count" in sql
    assert "fare_amount" in sql
    # Key columns should be in WHERE clause
    assert "WHERE" in sql
    assert "pickup_time = @p" in sql
    assert "dropoff_time = @p" in sql


def test_generate_update_sql_key_not_in_set():
    sql = generate_update_sql(TABLE, COLUMNS, KEY_COLUMNS)
    # Split at WHERE to get SET clause only
    set_clause = sql.split("WHERE")[0]
    # Key columns should NOT appear in the SET clause
    # (they appear in column list before SET but the SET assignments
    # should only have non-key columns)
    set_part = set_clause.split("SET")[1]
    assert "pickup_time" not in set_part
    assert "dropoff_time" not in set_part


def test_generate_delete_sql():
    sql = generate_delete_sql(TABLE, KEY_COLUMNS)
    assert "DELETE FROM taxi_trips" in sql
    assert "WHERE" in sql
    assert "pickup_time = @p1" in sql
    assert "dropoff_time = @p2" in sql


def test_generate_create_table_sql():
    sql = generate_create_table_sql(TABLE, COLUMNS)
    assert "CREATE TABLE taxi_trips" in sql
    assert "pickup_time DATETIME2" in sql
    assert "passenger_count INT" in sql
    assert "fare_amount FLOAT" in sql
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/loadtest/test_sql_generator.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement sql_generator.py**

Write `loadtest/sql_generator.py`:

```python
"""Generate SQL templates and CREATE TABLE scripts."""


def generate_insert_sql(table: str, columns: dict[str, str]) -> str:
    """Generate parameterized INSERT statement.

    Args:
        table: Target table name.
        columns: {column_name: sql_type} mapping.

    Returns:
        SQL like: INSERT INTO t (a, b) VALUES (@p1, @p2)
    """
    col_names = list(columns.keys())
    params = [f"@p{i+1}" for i in range(len(col_names))]
    return (
        f"INSERT INTO {table} ({', '.join(col_names)}) "
        f"VALUES ({', '.join(params)})"
    )


def generate_update_sql(
    table: str, columns: dict[str, str], key_columns: list[str]
) -> str:
    """Generate parameterized UPDATE statement.

    Sets all non-key columns, WHERE uses key columns.
    """
    non_key = [c for c in columns if c not in key_columns]

    param_idx = 1
    set_parts = []
    for col in non_key:
        set_parts.append(f"{col} = @p{param_idx}")
        param_idx += 1

    where_parts = []
    for col in key_columns:
        where_parts.append(f"{col} = @p{param_idx}")
        param_idx += 1

    return (
        f"UPDATE {table} SET {', '.join(set_parts)} "
        f"WHERE {' AND '.join(where_parts)}"
    )


def generate_delete_sql(
    table: str, key_columns: list[str]
) -> str:
    """Generate parameterized DELETE statement.

    WHERE uses key columns only.
    """
    where_parts = [f"{col} = @p{i+1}" for i, col in enumerate(key_columns)]
    return f"DELETE FROM {table} WHERE {' AND '.join(where_parts)}"


def generate_create_table_sql(table: str, columns: dict[str, str]) -> str:
    """Generate CREATE TABLE script.

    Args:
        table: Table name.
        columns: {column_name: sql_server_type} mapping.
    """
    col_defs = [f"    {name} {sql_type}" for name, sql_type in columns.items()]
    return f"CREATE TABLE {table} (\n{',\n'.join(col_defs)}\n);"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/loadtest/test_sql_generator.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add loadtest/sql_generator.py tests/loadtest/test_sql_generator.py
git commit -m "feat: add SQL template and CREATE TABLE generation"
```

---

## Task 6: K6 script and manifest generation (`k6_generator.py`)

**Files:**
- Create: `loadtest/k6_generator.py`
- Create: `tests/loadtest/test_k6_generator.py`

- [ ] **Step 1: Write failing tests**

Write `tests/loadtest/test_k6_generator.py`:

```python
import json
import pytest
from pathlib import Path

from loadtest.k6_generator import generate_manifest, generate_test_js


def test_generate_manifest():
    manifest = generate_manifest(
        scenario_name="basic_load",
        target={
            "host": "localhost",
            "port": 1433,
            "database": "test_db",
            "username": "sa",
            "password": "${MSSQL_PASSWORD}",
            "table": "taxi_trips",
        },
        data_source_name="yellow_trips",
        num_chunks=5,
        ordering="parallel",
        workload={"insert": 80, "update": 15, "delete": 5},
        think_time={"min": "200ms", "max": "1s"},
        sql_templates={
            "insert": "INSERT INTO taxi_trips (...) VALUES (...)",
            "update": "UPDATE taxi_trips SET ... WHERE ...",
            "delete": "DELETE FROM taxi_trips WHERE ...",
        },
        column_order=["pickup_time", "dropoff_time", "passenger_count"],
        key_columns=["pickup_time", "dropoff_time"],
    )
    assert manifest["table"] == "taxi_trips"
    assert manifest["data_source"] == "yellow_trips"
    assert manifest["num_chunks"] == 5
    assert manifest["ordering"] == "parallel"
    assert manifest["workload"]["insert"] == 80
    # Password should preserve ${} placeholder for K6 runtime
    assert "${MSSQL_PASSWORD}" in manifest["connection_string"]
    assert manifest["sql"]["insert"].startswith("INSERT")
    assert manifest["column_order"] == ["pickup_time", "dropoff_time", "passenger_count"]
    assert manifest["key_columns"] == ["pickup_time", "dropoff_time"]


def test_generate_manifest_sequential_warning(capsys):
    manifest = generate_manifest(
        scenario_name="seq_test",
        target={
            "host": "localhost",
            "port": 1433,
            "database": "test_db",
            "username": "sa",
            "password": "pass",
            "table": "t",
        },
        data_source_name="ds",
        num_chunks=3,
        ordering="sequential",
        workload={"insert": 100, "update": 0, "delete": 0},
        think_time={"min": "1s", "max": "2s"},
        sql_templates={"insert": "INSERT ...", "update": "UPDATE ...", "delete": "DELETE ..."},
        column_order=["a"],
        key_columns=["a"],
    )
    assert manifest["ordering"] == "sequential"


def test_generate_test_js_contains_scenarios():
    scenarios_config = {
        "basic_load": {
            "target": "test_server",
            "data_source": "yellow_trips",
            "ordering": "parallel",
            "workload": {"insert": 80, "update": 15, "delete": 5},
            "think_time": {"min": "200ms", "max": "1s"},
            "k6": {"executor": "constant-vus", "vus": 5, "duration": "1m"},
        },
    }
    js = generate_test_js(scenarios_config)
    assert "import sql from" in js
    assert "basic_load" in js
    assert "constant-vus" in js
    assert "export function" in js or "export const" in js
    assert "weightedRandom" in js
    assert "sleep" in js
    assert "SharedArray" in js
    assert "teardown" in js


def test_generate_test_js_sequential_override():
    scenarios_config = {
        "seq_load": {
            "target": "test_server",
            "data_source": "ds",
            "ordering": "sequential",
            "workload": {"insert": 100, "update": 0, "delete": 0},
            "think_time": {"min": "1s", "max": "2s"},
            "k6": {"executor": "constant-vus", "vus": 10, "duration": "5m"},
        },
    }
    js = generate_test_js(scenarios_config)
    # Sequential should override to per-vu-iterations with 1 VU
    assert "per-vu-iterations" in js
    assert '"vus": 1' in js or "'vus': 1" in js


def test_generate_test_js_multiple_scenarios():
    scenarios_config = {
        "load_a": {
            "target": "server_a",
            "data_source": "ds",
            "ordering": "parallel",
            "workload": {"insert": 100, "update": 0, "delete": 0},
            "think_time": {"min": "1s", "max": "2s"},
            "k6": {"executor": "constant-vus", "vus": 5, "duration": "1m"},
        },
        "load_b": {
            "target": "server_b",
            "data_source": "ds",
            "ordering": "parallel",
            "workload": {"insert": 50, "update": 50, "delete": 0},
            "think_time": {"min": "500ms", "max": "1s"},
            "k6": {"executor": "ramping-vus", "startVUs": 1, "stages": [{"duration": "1m", "target": 10}]},
        },
    }
    js = generate_test_js(scenarios_config)
    assert "load_a" in js
    assert "load_b" in js
    assert "loadA" in js  # camelCase function name
    assert "loadB" in js
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/loadtest/test_k6_generator.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement k6_generator.py**

Write `loadtest/k6_generator.py`:

```python
"""Generate K6 scenario manifests and test.js script."""

import json
import re
import sys


def generate_manifest(
    scenario_name: str,
    target: dict,
    data_source_name: str,
    num_chunks: int,
    ordering: str,
    workload: dict,
    think_time: dict,
    sql_templates: dict,
    column_order: list[str],
    key_columns: list[str],
) -> dict:
    """Generate a scenario manifest dict.

    The connection string preserves ${VAR} placeholders for K6 runtime resolution.
    """
    # Build connection string with placeholders preserved
    password = target["password"]
    connection_string = (
        f"server={target['host']},{target['port']};"
        f"database={target['database']};"
        f"user id={target['username']};"
        f"password={password};"
        f"TrustServerCertificate=true"
    )

    return {
        "scenario_name": scenario_name,
        "table": target["table"],
        "connection_string": connection_string,
        "data_source": data_source_name,
        "num_chunks": num_chunks,
        "ordering": ordering,
        "workload": workload,
        "think_time": think_time,
        "sql": sql_templates,
        "column_order": column_order,
        "key_columns": key_columns,
    }


def _to_camel_case(snake_str: str) -> str:
    """Convert snake_case to camelCase."""
    parts = snake_str.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def generate_test_js(scenarios_config: dict) -> str:
    """Generate the K6 test.js script content.

    Args:
        scenarios_config: The 'scenarios' section from the YAML config.

    Returns:
        Complete JavaScript source for the K6 test script.
    """
    # Build K6 scenarios options object
    k6_scenarios = {}
    for name, scenario in scenarios_config.items():
        func_name = _to_camel_case(name)
        k6_config = dict(scenario["k6"])

        if scenario.get("ordering") == "sequential":
            if k6_config.get("vus", 1) > 1 or k6_config.get("startVUs", 1) > 1:
                print(
                    f"Warning: scenario {name!r} uses sequential ordering — "
                    f"overriding to 1 VU",
                    file=sys.stderr,
                )
            # iterations=1 is correct: the single iteration loops through
            # all chunks sequentially inside processChunk()
            k6_config = {
                "executor": "per-vu-iterations",
                "vus": 1,
                "iterations": 1,
            }

        k6_config["exec"] = func_name
        k6_scenarios[name] = k6_config

    scenarios_json = json.dumps(k6_scenarios, indent=4)

    # Build executor functions
    functions = []
    for name, scenario in scenarios_config.items():
        func_name = _to_camel_case(name)
        functions.append(f"""
export function {func_name}() {{
    const manifest = manifests['{name}'];
    processChunk(manifest, '{name}');
}}""")

    functions_str = "\n".join(functions)

    return f"""import sql from 'k6/x/sql';
import {{ SharedArray }} from 'k6/data';
import exec from 'k6/execution';
import {{ sleep }} from 'k6';

// Load scenario manifests
const manifests = {{}};
{_generate_manifest_loaders(scenarios_config)}

// Load chunk file lists per data source into SharedArray (shared across VUs)
const chunkLists = {{}};
{_generate_chunk_list_loaders(scenarios_config)}

export const options = {{
    scenarios: {scenarios_json},
}};

// Per-VU connection cache
const connections = {{}};

function getConnection(manifest) {{
    const key = manifest.scenario_name;
    if (!connections[key]) {{
        // Resolve ${{VAR}} env var placeholders in connection string
        let connStr = manifest.connection_string;
        const envPattern = /\\$\\{{([^}}]+)\\}}/g;
        let match;
        while ((match = envPattern.exec(connStr)) !== null) {{
            const envVal = __ENV[match[1]];
            if (envVal === undefined) {{
                throw new Error(`Environment variable ${{match[1]}} not set`);
            }}
            connStr = connStr.replace(match[0], envVal);
        }}
        connections[key] = sql.open('sqlserver', connStr);
    }}
    return connections[key];
}}

export function teardown() {{
    // Close all cached database connections
    for (const [key, db] of Object.entries(connections)) {{
        db.close();
    }}
}}

function weightedRandom(workload) {{
    const rand = Math.random() * 100;
    let cumulative = 0;
    for (const [op, pct] of Object.entries(workload)) {{
        cumulative += pct;
        if (rand < cumulative) return op;
    }}
    return 'insert'; // fallback
}}

function randomBetween(minStr, maxStr) {{
    // Parse duration strings like "200ms", "1s"
    function parseMs(s) {{
        if (s.endsWith('ms')) return parseFloat(s);
        if (s.endsWith('s')) return parseFloat(s) * 1000;
        return parseFloat(s);
    }}
    const min = parseMs(minStr);
    const max = parseMs(maxStr);
    return (min + Math.random() * (max - min)) / 1000; // K6 sleep uses seconds
}}

function processChunk(manifest, scenarioName) {{
    const chunkFiles = chunkLists[manifest.data_source];
    let chunkIdx;

    if (manifest.ordering === 'sequential') {{
        // Sequential: process all chunks in order in a single iteration
        const db = getConnection(manifest);
        for (let i = 0; i < chunkFiles.length; i++) {{
            const rows = JSON.parse(open(chunkFiles[i]));
            processRows(db, manifest, rows);
        }}
        return;
    }}

    // Parallel: each iteration gets one chunk
    chunkIdx = exec.scenario.iterationInTest;
    if (chunkIdx >= chunkFiles.length) return; // no more chunks

    const rows = JSON.parse(open(chunkFiles[chunkIdx]));
    const db = getConnection(manifest);
    processRows(db, manifest, rows);
}}

function processRows(db, manifest, rows) {{
    const processed = [];

    for (const row of rows) {{
        const op = weightedRandom(manifest.workload);
        const values = manifest.column_order.map(c => row[c]);
        const keyValues = manifest.key_columns.map(c => row[c]);

        if (op === 'insert' || processed.length === 0) {{
            sql.query(db, manifest.sql.insert, ...values);
        }} else if (op === 'update') {{
            const target = processed[Math.floor(Math.random() * processed.length)];
            const nonKeyValues = manifest.column_order
                .filter(c => !manifest.key_columns.includes(c))
                .map(c => row[c]);
            const targetKeyValues = manifest.key_columns.map(c => target[c]);
            sql.query(db, manifest.sql.update, ...nonKeyValues, ...targetKeyValues);
        }} else if (op === 'delete') {{
            const target = processed[Math.floor(Math.random() * processed.length)];
            const targetKeyValues = manifest.key_columns.map(c => target[c]);
            sql.query(db, manifest.sql.delete, ...targetKeyValues);
        }}

        processed.push(row);
        sleep(randomBetween(manifest.think_time.min, manifest.think_time.max));
    }}
}}
{functions_str}
"""


def _generate_manifest_loaders(scenarios_config: dict) -> str:
    """Generate JS code to load manifest files."""
    lines = []
    for name in scenarios_config:
        lines.append(
            f"manifests['{name}'] = JSON.parse(open('./scenarios/{name}.json'));"
        )
    return "\n".join(lines)


def _generate_chunk_list_loaders(scenarios_config: dict) -> str:
    """Generate JS code to build chunk file path arrays per data source."""
    # Collect unique data sources and their scenario references
    data_sources = set()
    for scenario in scenarios_config.values():
        data_sources.add(scenario["data_source"])

    lines = []
    for ds in sorted(data_sources):
        # Chunk list will be populated from manifest at runtime
        # We use a SharedArray that reads a generated index file
        lines.append(
            f"chunkLists['{ds}'] = new SharedArray('{ds}_chunks', "
            f"() => JSON.parse(open('./data/{ds}/chunks.json')));"
        )
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/loadtest/test_k6_generator.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add loadtest/k6_generator.py tests/loadtest/test_k6_generator.py
git commit -m "feat: add K6 manifest and test.js generation"
```

---

## Task 7: CLI entry point — preprocessor orchestration (`preprocess.py`)

**Files:**
- Create: `loadtest/preprocess.py`
- Create: `tests/loadtest/test_preprocess_integration.py`

- [ ] **Step 1: Write failing integration test**

Write `tests/loadtest/test_preprocess_integration.py`:

```python
import json
import pytest
from pathlib import Path

import duckdb

from loadtest.preprocess import run_preprocess


@pytest.fixture
def integration_setup(tmp_path):
    """Create parquet files and config for full integration test."""
    # Create parquet data
    parquet_dir = tmp_path / "raw"
    parquet_dir.mkdir()
    db = duckdb.connect()
    db.sql(f"""
        COPY (
            SELECT
                '2026-01-15 08:30:00'::TIMESTAMP AS tpep_pickup_datetime,
                '2026-01-15 09:15:00'::TIMESTAMP AS tpep_dropoff_datetime,
                2::INTEGER AS passenger_count,
                3.4::DOUBLE AS trip_distance,
                15.50::DOUBLE AS fare_amount,
                3.00::DOUBLE AS tip_amount
            FROM range(50)
        ) TO '{parquet_dir}/test.parquet' (FORMAT PARQUET)
    """)

    # Create config
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"""
data_sources:
  yellow_trips:
    path: "{parquet_dir}/*.parquet"
    chunk_size: 20
    key_columns: [pickup_time, dropoff_time]
    columns:
      pickup_time: tpep_pickup_datetime
      dropoff_time: tpep_dropoff_datetime
      passenger_count: passenger_count
      trip_distance: trip_distance
      fare_amount: fare_amount
      tip_amount: tip_amount

targets:
  test_server:
    host: localhost
    port: 1433
    database: test_db
    username: sa
    password: ${{MSSQL_PASSWORD}}
    table: taxi_trips

scenarios:
  basic_load:
    target: test_server
    data_source: yellow_trips
    ordering: parallel
    workload:
      insert: 80
      update: 15
      delete: 5
    think_time:
      min: 200ms
      max: 1s
    k6:
      executor: constant-vus
      vus: 5
      duration: 1m
""")

    output_dir = tmp_path / "k6_output"
    return config_path, output_dir


def test_full_preprocess(integration_setup):
    config_path, output_dir = integration_setup

    run_preprocess(config_path, output_dir)

    # Check output structure
    assert (output_dir / "test.js").exists()
    assert (output_dir / "schema" / "test_server_taxi_trips.sql").exists()
    assert (output_dir / "scenarios" / "basic_load.json").exists()
    assert (output_dir / "data" / "yellow_trips" / "chunk_0000.json").exists()
    assert (output_dir / "data" / "yellow_trips" / "chunks.json").exists()

    # Check chunk index
    with open(output_dir / "data" / "yellow_trips" / "chunks.json") as f:
        chunk_list = json.load(f)
    assert len(chunk_list) == 3  # 50 rows / 20 per chunk

    # Check manifest
    with open(output_dir / "scenarios" / "basic_load.json") as f:
        manifest = json.load(f)
    assert manifest["table"] == "taxi_trips"
    assert manifest["num_chunks"] == 3
    assert "${MSSQL_PASSWORD}" in manifest["connection_string"]

    # Check CREATE TABLE has correct types
    schema_sql = (output_dir / "schema" / "test_server_taxi_trips.sql").read_text()
    assert "pickup_time DATETIME2" in schema_sql
    assert "passenger_count INT" in schema_sql
    assert "trip_distance FLOAT" in schema_sql

    # Check test.js is valid-looking
    test_js = (output_dir / "test.js").read_text()
    assert "basic_load" in test_js
    assert "import sql from" in test_js
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/loadtest/test_preprocess_integration.py -v`
Expected: FAIL — `run_preprocess` does not exist

- [ ] **Step 3: Implement preprocess.py**

Write `loadtest/preprocess.py`:

```python
"""CLI entry point for the K6 load test preprocessor."""

import argparse
import json
import sys
from pathlib import Path

from loadtest.config import load_config, validate_config
from loadtest.data_export import export_chunks, get_schema
from loadtest.type_mapping import map_duckdb_to_mssql
from loadtest.sql_generator import (
    generate_insert_sql,
    generate_update_sql,
    generate_delete_sql,
    generate_create_table_sql,
)
from loadtest.k6_generator import generate_manifest, generate_test_js


def run_preprocess(config_path: Path, output_dir: Path) -> None:
    """Run the full preprocessing pipeline."""
    # Load and validate config
    config = load_config(config_path)
    validate_config(config)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    data_sources = config["data_sources"]
    targets = config["targets"]
    scenarios = config["scenarios"]

    # Track chunks per data source and schemas
    ds_chunk_counts = {}
    ds_schemas = {}  # {ds_name: {mapped_col: sql_server_type}}

    # Step 1: Export data sources to chunked JSON
    for ds_name, ds_config in data_sources.items():
        print(f"Exporting data source: {ds_name}")
        columns = ds_config["columns"]
        chunk_size = ds_config.get("chunk_size", 5000)

        num_chunks = export_chunks(
            parquet_glob=ds_config["path"],
            columns=columns,
            chunk_size=chunk_size,
            output_dir=output_dir / "data" / ds_name,
        )
        ds_chunk_counts[ds_name] = num_chunks
        print(f"  Exported {num_chunks} chunks to data/{ds_name}/")

        # Write chunk index file
        chunk_files = [
            f"./data/{ds_name}/chunk_{i:04d}.json" for i in range(num_chunks)
        ]
        with open(output_dir / "data" / ds_name / "chunks.json", "w") as f:
            json.dump(chunk_files, f)

        # Get schema and map types
        parquet_schema = get_schema(ds_config["path"])
        mapped_schema = {}
        for mapped_name, source_name in columns.items():
            duckdb_type = parquet_schema[source_name]
            mapped_schema[mapped_name] = map_duckdb_to_mssql(duckdb_type)
        ds_schemas[ds_name] = mapped_schema

    # Step 2: Generate SQL and schema files per target
    schema_dir = output_dir / "schema"
    schema_dir.mkdir(parents=True, exist_ok=True)

    # Collect which data sources each target uses (via scenarios)
    target_ds_map = {}
    for scenario in scenarios.values():
        target_name = scenario["target"]
        ds_name = scenario["data_source"]
        target_ds_map[target_name] = ds_name

    for target_name, ds_name in target_ds_map.items():
        target = targets[target_name]
        table = target["table"]
        schema = ds_schemas[ds_name]

        create_sql = generate_create_table_sql(table, schema)
        schema_file = schema_dir / f"{target_name}_{table}.sql"
        schema_file.write_text(create_sql)
        print(f"  Schema: {schema_file.name}")

    # Step 3: Generate scenario manifests
    scenarios_dir = output_dir / "scenarios"
    scenarios_dir.mkdir(parents=True, exist_ok=True)

    for scenario_name, scenario_config in scenarios.items():
        target = targets[scenario_config["target"]]
        ds_name = scenario_config["data_source"]
        ds_config = data_sources[ds_name]
        schema = ds_schemas[ds_name]
        key_columns = ds_config["key_columns"]

        sql_templates = {
            "insert": generate_insert_sql(target["table"], schema),
            "update": generate_update_sql(target["table"], schema, key_columns),
            "delete": generate_delete_sql(target["table"], key_columns),
        }

        manifest = generate_manifest(
            scenario_name=scenario_name,
            target=target,
            data_source_name=ds_name,
            num_chunks=ds_chunk_counts[ds_name],
            ordering=scenario_config.get("ordering", "parallel"),
            workload=scenario_config["workload"],
            think_time=scenario_config["think_time"],
            sql_templates=sql_templates,
            column_order=list(ds_config["columns"].keys()),
            key_columns=key_columns,
        )

        manifest_path = scenarios_dir / f"{scenario_name}.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
        print(f"  Manifest: {scenario_name}.json")

    # Step 4: Generate K6 test script
    test_js = generate_test_js(scenarios)
    (output_dir / "test.js").write_text(test_js)
    print(f"  Generated test.js")

    print(f"\nDone! Output written to: {output_dir}")
    print(f"Run with: ./k6 run {output_dir}/test.js")


def main():
    parser = argparse.ArgumentParser(
        description="Preprocess parquet data for K6 SQL Server load testing"
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to YAML config file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("k6_output"),
        help="Output directory (default: k6_output/)",
    )
    args = parser.parse_args()

    try:
        run_preprocess(args.config, args.output)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run integration test to verify it passes**

Run: `uv run pytest tests/loadtest/test_preprocess_integration.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add loadtest/preprocess.py tests/loadtest/test_preprocess_integration.py
git commit -m "feat: add preprocessor CLI orchestrating config, export, and K6 generation"
```

---

## Task 8: K6 custom binary build script

**Files:**
- Create: `build_k6.sh`

- [ ] **Step 1: Write the build script**

Write `build_k6.sh`:

```bash
#!/bin/bash
# Build custom K6 binary with xk6-sql and MS SQL driver
#
# Prerequisites: Go 1.21+ installed
# Installs xk6 if not present, then builds k6 with SQL Server support.

set -euo pipefail

echo "Building custom K6 binary with SQL Server support..."

# Install xk6 if not present
if ! command -v xk6 &> /dev/null; then
    echo "Installing xk6..."
    go install go.k6.io/xk6/cmd/xk6@latest
fi

# Build K6 with SQL extensions
xk6 build \
    --with github.com/grafana/xk6-sql \
    --with github.com/grafana/xk6-sql-driver-mssql \
    --output ./k6

echo ""
echo "Build complete: ./k6"
echo "Verify with: ./k6 version"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x build_k6.sh
```

- [ ] **Step 3: Commit**

```bash
git add build_k6.sh
git commit -m "feat: add K6 custom binary build script"
```

---

## Task 9: Sample config and documentation

**Files:**
- Create: `loadtest/config.sample.yaml`

- [ ] **Step 1: Write sample config**

Write `loadtest/config.sample.yaml`:

```yaml
# K6 SQL Server Load Test Configuration
# Copy this file to config.yaml and adjust for your environment.
#
# Usage:
#   1. Build custom K6:  ./build_k6.sh
#   2. Preprocess data:  uv run preprocess --config config.yaml --output k6_output/
#   3. Create tables:    Apply scripts from k6_output/schema/ to your SQL Servers
#   4. Run load test:    MSSQL_PASSWORD=yourpass ./k6 run k6_output/test.js

# Data sources — parquet files to read and column mapping
data_sources:
  yellow_trips:
    path: raw/yellow/2026/*.parquet
    chunk_size: 5000
    key_columns: [pickup_time, dropoff_time]
    columns:
      pickup_time: tpep_pickup_datetime
      dropoff_time: tpep_dropoff_datetime
      passenger_count: passenger_count
      trip_distance: trip_distance
      fare_amount: fare_amount
      tip_amount: tip_amount

  green_trips:
    path: raw/green/2026/*.parquet
    chunk_size: 5000
    key_columns: [pickup_time, dropoff_time]
    columns:
      pickup_time: lpep_pickup_datetime
      dropoff_time: lpep_dropoff_datetime
      passenger_count: passenger_count
      trip_distance: trip_distance
      fare_amount: fare_amount
      tip_amount: tip_amount

# SQL Server targets
targets:
  server_a_sales:
    host: sqlserver-a.local
    port: 1433
    database: sales_db
    username: sa
    password: ${MSSQL_PASSWORD}
    table: taxi_trips

  server_b_analytics:
    host: sqlserver-b.local
    port: 1433
    database: analytics_db
    username: sa
    password: ${MSSQL_PASSWORD}
    table: taxi_trips

# Scenarios — each becomes a K6 scenario running concurrently
scenarios:
  # Heavy mixed workload on server A
  heavy_mixed_server_a:
    target: server_a_sales
    data_source: yellow_trips
    ordering: parallel
    workload:
      insert: 80
      update: 15
      delete: 5
    think_time:
      min: 200ms
      max: 1s
    k6:
      executor: ramping-vus
      startVUs: 2
      stages:
        - duration: 1m
          target: 20
        - duration: 5m
          target: 20
        - duration: 1m
          target: 0

  # Steady sequential inserts on server B
  steady_inserts_server_b:
    target: server_b_analytics
    data_source: green_trips
    ordering: sequential
    workload:
      insert: 95
      update: 5
      delete: 0
    think_time:
      min: 500ms
      max: 2s
    k6:
      executor: constant-vus
      vus: 10
      duration: 7m
```

- [ ] **Step 2: Add k6_output to .gitignore**

Append to `.gitignore`:

```
k6_output/
k6
```

- [ ] **Step 3: Commit**

```bash
git add loadtest/config.sample.yaml .gitignore
git commit -m "feat: add sample config and gitignore k6 output"
```

---

## Task 10: End-to-end smoke test with real data

**Files:**
- No new files — manual verification

- [ ] **Step 1: Run preprocessor against real taxi data**

```bash
cp loadtest/config.sample.yaml config.yaml
# Edit config.yaml: adjust paths to match local data, point targets to localhost
uv run preprocess --config config.yaml --output k6_output/
```

Expected: output directory created with chunks, manifests, schema, and test.js

- [ ] **Step 2: Inspect generated output**

```bash
ls -la k6_output/
ls -la k6_output/data/yellow_trips/ | head -20
cat k6_output/schema/server_a_sales_taxi_trips.sql
cat k6_output/scenarios/heavy_mixed_server_a.json | python -m json.tool | head -30
head -50 k6_output/test.js
```

Verify: SQL types are correct, JSON chunks are well-formed, test.js has proper K6 structure.

- [ ] **Step 3: Validate K6 script syntax (if K6 binary available)**

```bash
./k6 inspect k6_output/test.js
```

Expected: K6 parses the script without errors (or skip if custom binary not built yet).

- [ ] **Step 4: Commit any fixes needed**

If the smoke test reveals issues, fix them and commit.
