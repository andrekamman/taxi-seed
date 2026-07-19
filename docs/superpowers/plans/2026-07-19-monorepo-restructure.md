# Monorepo Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the repo so the downloader, schema-drift analyzer, and K6 load tester are three clearly-separated components under a single monorepo, with the DuckDB→SQL Server bits (type mapping + CREATE TABLE generation) extracted to a `shared/` package for future reuse by a planned SQL Server loader.

**Architecture:** One `pyproject.toml` at root packages three Python components (`k6_loadtest`, `schema_drift`, `taxi_shared`) using the `src/` layout. Downloader is bash-only. Each component lives in its own kebab-case top-level directory with its own README. Tests mirror the package layout under a top-level `tests/` tree. Restructure happens as 6 atomic commits, each ending green.

**Tech Stack:** Python 3.12+, uv, hatchling, DuckDB, pyyaml, pytest. Bash 4+ / curl for the downloader.

**Reference spec:** `docs/superpowers/specs/2026-07-19-monorepo-restructure-design.md`

---

## Preconditions

- [ ] **Working tree is clean.** If you have uncommitted work sitting alongside this plan (the WAF-aware `download_taxi_data.sh` rewrite, the LICENSE swap to MIT, the `THIRD_PARTY_NOTICES` file, the README acknowledgment addition, this plan file, or the design doc), commit those first as separate commits so the restructure PR is clean. This plan assumes `git status` is clean at start.

- [ ] **Baseline test run.** Confirm the current pytest suite passes so any breakage during the restructure can be attributed to a specific step.

  ```bash
  uv run pytest -q
  ```

  Expected: all tests pass (currently 6 test files under `tests/loadtest/`).

---

## Task 1: Extract shared code into `taxi_shared` package

**Motivation:** `loadtest/type_mapping.py` and `loadtest/sql_generator.py` will be needed by the future SQL Server loader. Extracting first makes every subsequent task's import updates simpler.

**Files:**
- Create: `shared/src/taxi_shared/__init__.py` (empty)
- Move: `loadtest/type_mapping.py` → `shared/src/taxi_shared/type_mapping.py`
- Move: `loadtest/sql_generator.py` → `shared/src/taxi_shared/sql_generator.py`
- Move: `tests/loadtest/test_type_mapping.py` → `tests/taxi_shared/test_type_mapping.py`
- Move: `tests/loadtest/test_sql_generator.py` → `tests/taxi_shared/test_sql_generator.py`
- Modify: `loadtest/preprocess.py:10-16` (update imports)
- Modify: `pyproject.toml` (add package)

### Steps

- [ ] **1.1 Create `taxi_shared` package skeleton**

  ```bash
  mkdir -p shared/src/taxi_shared
  touch shared/src/taxi_shared/__init__.py
  ```

- [ ] **1.2 Move source files (preserve git history)**

  ```bash
  git mv loadtest/type_mapping.py shared/src/taxi_shared/type_mapping.py
  git mv loadtest/sql_generator.py shared/src/taxi_shared/sql_generator.py
  ```

- [ ] **1.3 Move test files (preserve git history)**

  ```bash
  mkdir -p tests/taxi_shared
  git mv tests/loadtest/test_type_mapping.py tests/taxi_shared/test_type_mapping.py
  git mv tests/loadtest/test_sql_generator.py tests/taxi_shared/test_sql_generator.py
  ```

- [ ] **1.4 Update imports in `loadtest/preprocess.py`**

  Change only the two `loadtest.` imports that reference the moved modules. Everything else in the file (including the other `loadtest.` imports for `config`, `data_export`, `k6_generator` — those get handled in Task 2) stays unchanged.

  Line 10 — replace the exact string:
  ```
  from loadtest.type_mapping import map_duckdb_to_mssql
  ```
  with:
  ```
  from taxi_shared.type_mapping import map_duckdb_to_mssql
  ```

  Line 11 — replace the exact string:
  ```
  from loadtest.sql_generator import (
  ```
  with:
  ```
  from taxi_shared.sql_generator import (
  ```

  Do not touch the closing `)` or any of the imported names between the parens (those are on lines 12–16 and are correct as-is; only the module path on the opening line changes).

  Verify with:
  ```bash
  grep -n 'loadtest\.\(type_mapping\|sql_generator\)' loadtest/preprocess.py
  ```
  Expected: no output.

- [ ] **1.5 Update imports in the moved test files**

  In `tests/taxi_shared/test_type_mapping.py:3`:
  Replace: `from loadtest.type_mapping import map_duckdb_to_mssql, TypeMappingError`
  With: `from taxi_shared.type_mapping import map_duckdb_to_mssql, TypeMappingError`

  In `tests/taxi_shared/test_sql_generator.py:3`:
  Replace: `from loadtest.sql_generator import (`
  With: `from taxi_shared.sql_generator import (`

  (Rest of the multiline import stays the same.)

- [ ] **1.6 Update `pyproject.toml` packages list**

  Change:
  ```toml
  [tool.hatch.build.targets.wheel]
  packages = ["loadtest"]
  ```
  To:
  ```toml
  [tool.hatch.build.targets.wheel]
  packages = ["loadtest", "shared/src/taxi_shared"]
  ```

- [ ] **1.7 Re-sync the environment**

  ```bash
  uv sync
  ```

  Expected: success, no dependency changes reported.

- [ ] **1.8 Run tests to verify green**

  ```bash
  uv run pytest -q
  ```

  Expected: all tests pass (same count as baseline; two of them are now under `tests/taxi_shared/` importing from `taxi_shared`).

- [ ] **1.9 Commit**

  `git mv` in steps 1.2–1.3 already staged the moves; only the modified `preprocess.py` and `pyproject.toml` need to be added.

  ```bash
  git add -A
  git commit -m "refactor: extract type_mapping and sql_generator into taxi_shared package"
  ```

---

## Task 2: Rename `loadtest` to `k6_loadtest` under `k6-loadtest/src/`

**Motivation:** `loadtest` is a generic name; `k6_loadtest` is discoverable and matches the new directory. Also moves the K6-related helpers (`build_k6.sh`, `config.sample.yaml`) inside the component's directory.

**Files:**
- Move: `loadtest/` → `k6-loadtest/src/k6_loadtest/` (all remaining files)
- Move: `tests/loadtest/` → `tests/k6_loadtest/`
- Move: `build_k6.sh` → `k6-loadtest/build_k6.sh`
- Move: `loadtest/config.sample.yaml` → `k6-loadtest/config.sample.yaml` (moves with the loadtest dir; explicit line for clarity)
- Modify: `k6-loadtest/src/k6_loadtest/preprocess.py:8-17` (update imports)
- Modify: every `tests/k6_loadtest/test_*.py` (update imports)
- Modify: `pyproject.toml` (packages list + script entry)

### Steps

- [ ] **2.1 Create the new directory structure and move the package**

  ```bash
  mkdir -p k6-loadtest/src
  git mv loadtest k6-loadtest/src/k6_loadtest
  git mv tests/loadtest tests/k6_loadtest
  git mv build_k6.sh k6-loadtest/build_k6.sh
  ```

  After this, `k6-loadtest/src/k6_loadtest/config.sample.yaml` exists (moved with the loadtest dir). Move it up one level to sit alongside `build_k6.sh`:

  ```bash
  git mv k6-loadtest/src/k6_loadtest/config.sample.yaml k6-loadtest/config.sample.yaml
  ```

- [ ] **2.2 Update source imports in `k6-loadtest/src/k6_loadtest/preprocess.py`**

  Every remaining `from loadtest.` import needs the prefix changed to `from k6_loadtest.`. There are exactly three such lines (the `taxi_shared` imports from Task 1 are unaffected). Do each individually:

  Line 8 — replace:
  ```
  from loadtest.config import load_config, validate_config
  ```
  with:
  ```
  from k6_loadtest.config import load_config, validate_config
  ```

  Line 9 — replace:
  ```
  from loadtest.data_export import export_chunks, get_schema
  ```
  with:
  ```
  from k6_loadtest.data_export import export_chunks, get_schema
  ```

  Line 17 — replace:
  ```
  from loadtest.k6_generator import generate_manifest, generate_test_js
  ```
  with:
  ```
  from k6_loadtest.k6_generator import generate_manifest, generate_test_js
  ```

  Verify with:
  ```bash
  grep -n 'from loadtest' k6-loadtest/src/k6_loadtest/preprocess.py
  ```
  Expected: no output.

- [ ] **2.3 Update test imports**

  For each file in `tests/k6_loadtest/`, replace `from loadtest.` with `from k6_loadtest.`. Exact locations to change:

  - `tests/k6_loadtest/test_preprocess_integration.py:7` — `from loadtest.preprocess import run_preprocess` → `from k6_loadtest.preprocess import run_preprocess`
  - `tests/k6_loadtest/test_k6_generator.py:5` — `from loadtest.k6_generator import ...` → `from k6_loadtest.k6_generator import ...`
  - `tests/k6_loadtest/test_config.py:4` — `from loadtest.config import ...` → `from k6_loadtest.config import ...`
  - `tests/k6_loadtest/test_config.py:76` — same rewrite (there's a second import inside a test)
  - `tests/k6_loadtest/test_data_export.py:7` — `from loadtest.data_export import ...` → `from k6_loadtest.data_export import ...`

  Sanity check afterwards:

  ```bash
  grep -rn 'from loadtest\|import loadtest' k6-loadtest/ tests/
  ```

  Expected: no output (all imports converted).

- [ ] **2.4 Update `pyproject.toml`: packages list and script entry**

  Change packages list from:
  ```toml
  [tool.hatch.build.targets.wheel]
  packages = ["loadtest", "shared/src/taxi_shared"]
  ```
  To:
  ```toml
  [tool.hatch.build.targets.wheel]
  packages = ["k6-loadtest/src/k6_loadtest", "shared/src/taxi_shared"]
  ```

  Change script entry from:
  ```toml
  [project.scripts]
  preprocess = "loadtest.preprocess:main"
  ```
  To:
  ```toml
  [project.scripts]
  k6-preprocess = "k6_loadtest.preprocess:main"
  ```

- [ ] **2.5 Re-sync and run tests**

  ```bash
  uv sync
  uv run pytest -q
  ```

  Expected: all tests pass.

- [ ] **2.6 Smoke-test the entry point**

  ```bash
  uv run k6-preprocess --help
  ```

  Expected: argparse help output. Confirms the new script name resolves to the moved code.

- [ ] **2.7 Commit**

  ```bash
  git add -A
  git commit -m "refactor: rename loadtest to k6_loadtest, move under k6-loadtest/"
  ```

---

## Task 3: Move downloader into `downloader/`

**Motivation:** Isolate the downloader in its own directory. Also fix the script's working-directory assumption so it works from any CWD.

**Files:**
- Move: `download_taxi_data.sh` → `downloader/download_taxi_data.sh`
- Modify: `downloader/download_taxi_data.sh` (make `raw/` path script-relative)

### Steps

- [ ] **3.1 Move the script**

  ```bash
  mkdir downloader
  git mv download_taxi_data.sh downloader/download_taxi_data.sh
  ```

- [ ] **3.2 Make the `raw/` path script-relative**

  In `downloader/download_taxi_data.sh`, find the line:
  ```bash
  output_dir="raw"
  ```

  Replace with:
  ```bash
  # Resolve raw/ relative to this script's location, so the script works from any CWD.
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  output_dir="$script_dir/../raw"
  ```

  This resolves to the repo-root `raw/` directory regardless of where the user invokes the script from.

- [ ] **3.3 Smoke-test the script**

  From the repo root:

  ```bash
  ./downloader/download_taxi_data.sh --recent 1
  ```

  Expected: script runs, finds existing `raw/` mirror (from prior runs), reports "Already have" for at least one month per type. No new download errors. Files remain at `raw/<type>/<year>/...` (not `raw/downloader/../raw/...`).

  Also try from a different CWD to confirm the fix:

  ```bash
  (cd /tmp && /Users/andre/git/taxi/downloader/download_taxi_data.sh --recent 1)
  ```

  Expected: same behavior — files still land in the repo's `raw/` directory, not in `/tmp/raw/`.

- [ ] **3.4 Commit**

  ```bash
  git add downloader/
  git commit -m "refactor: move downloader into downloader/ subdirectory"
  ```

---

## Task 4: Split `schema_drift.py` into a package

**Motivation:** 1073 lines in one file is hard to navigate; splitting into cohesive modules makes future extension easier. Reading the file during plan-writing revealed 7 natural module boundaries (more granular than the spec's placeholder 4 — the actual code has distinct sections for models, name similarity, statistics, rename detection, analysis, reporting, and CLI).

**Target modules:**
| Module | Contents | Source lines |
|---|---|---|
| `models.py` | `ColumnInfo`, `ColumnRename`, `SchemaChange` dataclasses | 18–54 |
| `similarity.py` | `ABBREVIATIONS`, `SEMANTIC_OPPOSITES`, `get_semantic_categories`, `normalize_column_name`, `column_name_similarity`, `longest_common_subsequence_length`, `types_compatible` | 57–275 |
| `stats.py` | `get_column_stats`, `compare_column_stats`, `compute_data_similarity_score` | 323–558 |
| `renames.py` | `detect_renames`, `detect_renames_by_data`, `verify_renames_with_data` | 278–320, 561–662 |
| `analyze.py` | `get_parquet_schema`, `extract_period`, `find_parquet_files`, `compare_schemas`, `schema_signature`, `analyze_data_type` | 665–842 |
| `report.py` | `format_schema_table`, `generate_report` | 845–997 |
| `cli.py` | `main` + argparse setup | 1000–1073 |

**Dependency DAG (each module imports only from those below it):**
```
cli      → analyze, report
report   → models (+ stdlib defaultdict)
analyze  → models, similarity, renames, stats
renames  → models, similarity, stats
stats    → duckdb (external only)
similarity → (stdlib only)
models   → (stdlib only)
```

**Files:**
- Create: `schema-drift/src/schema_drift/__init__.py`
- Create: `schema-drift/src/schema_drift/models.py`
- Create: `schema-drift/src/schema_drift/similarity.py`
- Create: `schema-drift/src/schema_drift/stats.py`
- Create: `schema-drift/src/schema_drift/renames.py`
- Create: `schema-drift/src/schema_drift/analyze.py`
- Create: `schema-drift/src/schema_drift/report.py`
- Create: `schema-drift/src/schema_drift/cli.py`
- Create: `tests/schema_drift/__init__.py`
- Create: `tests/schema_drift/test_smoke.py`
- Delete: `schema_drift.py` (root)
- Modify: `pyproject.toml` (add package + script entry)

### Steps

- [ ] **4.1 Write the smoke test BEFORE any code movement**

  This test characterizes current behavior (the CLI is invocable and returns nonzero when the data dir doesn't exist) so the split can be verified against it.

  Create `tests/schema_drift/__init__.py` (empty).

  Create `tests/schema_drift/test_smoke.py`:

  ```python
  """Smoke tests that verify schema_drift package structure and CLI wiring.

  These tests do not exercise the analyzer against real parquet data — they
  verify that the split package still exposes the expected public surface and
  that the CLI entry point is invocable.
  """
  import subprocess
  import sys


  def test_public_imports_available():
      """Every module in the split package must import cleanly."""
      from schema_drift import models, similarity, stats, renames, analyze, report, cli
      # Concrete names that MUST remain importable from their target modules.
      assert models.ColumnInfo is not None
      assert models.ColumnRename is not None
      assert models.SchemaChange is not None
      assert callable(similarity.column_name_similarity)
      assert callable(similarity.types_compatible)
      assert callable(stats.get_column_stats)
      assert callable(renames.detect_renames)
      assert callable(renames.detect_renames_by_data)
      assert callable(analyze.analyze_data_type)
      assert callable(report.generate_report)
      assert callable(cli.main)


  def test_cli_help_runs():
      """schema-drift --help must exit 0 with usage output."""
      result = subprocess.run(
          [sys.executable, "-m", "schema_drift.cli", "--help"],
          capture_output=True,
          text=True,
      )
      assert result.returncode == 0
      assert "schema drift" in result.stdout.lower()


  def test_cli_missing_data_dir_exits_nonzero():
      """CLI must exit nonzero when given a data dir that doesn't exist."""
      result = subprocess.run(
          [sys.executable, "-m", "schema_drift.cli",
           "--data-dir", "/tmp/definitely_does_not_exist_xyz"],
          capture_output=True,
          text=True,
      )
      assert result.returncode != 0
      assert "does not exist" in result.stderr.lower()
  ```

- [ ] **4.2 Verify the smoke test fails (package doesn't exist yet)**

  ```bash
  uv run pytest tests/schema_drift/test_smoke.py -v
  ```

  Expected: all three tests fail with `ModuleNotFoundError: No module named 'schema_drift'`.

- [ ] **4.3 Create the package skeleton**

  ```bash
  mkdir -p schema-drift/src/schema_drift
  touch schema-drift/src/schema_drift/__init__.py
  ```

- [ ] **4.4 Extract `models.py`**

  Create `schema-drift/src/schema_drift/models.py` containing lines 18–54 of the original `schema_drift.py` (the three `@dataclass`es: `ColumnInfo`, `ColumnRename`, `SchemaChange`) preceded by these imports:

  ```python
  from dataclasses import dataclass
  from pathlib import Path
  ```

  Do not include any other code from the original file in this module. Do not add new abstractions — this is a mechanical move.

- [ ] **4.5 Extract `similarity.py`**

  Create `schema-drift/src/schema_drift/similarity.py` containing:
  - `ABBREVIATIONS` dict (lines 58–85)
  - `SEMANTIC_OPPOSITES` list (lines 88–94)
  - `get_semantic_categories` function (lines 97–141)
  - `normalize_column_name` function (lines 144–158)
  - `column_name_similarity` function (lines 161–231)
  - `longest_common_subsequence_length` function (lines 234–249)
  - `types_compatible` function (lines 252–275)

  Preceded by (no external deps needed):
  ```python
  # Pure stdlib module — no imports required beyond what's used inline.
  ```

- [ ] **4.6 Extract `stats.py`**

  Create `schema-drift/src/schema_drift/stats.py` containing:
  - `get_column_stats` (lines 323–390)
  - `compare_column_stats` (lines 393–481)
  - `compute_data_similarity_score` (lines 484–558)

  Preceded by:
  ```python
  from pathlib import Path

  import duckdb
  ```

  Note: the original file has `import math` inline inside two functions (lines 420, 507). Leave those inline; moving them to top-level is a semantic-preserving optimization that's out of scope here.

- [ ] **4.7 Extract `renames.py`**

  Create `schema-drift/src/schema_drift/renames.py` containing:
  - `detect_renames` (lines 278–320)
  - `detect_renames_by_data` (lines 561–623)
  - `verify_renames_with_data` (lines 626–662)

  Preceded by:
  ```python
  from pathlib import Path

  import duckdb

  from schema_drift.models import ColumnInfo, ColumnRename
  from schema_drift.similarity import column_name_similarity, types_compatible
  from schema_drift.stats import compare_column_stats, compute_data_similarity_score, get_column_stats
  ```

- [ ] **4.8 Extract `analyze.py`**

  Create `schema-drift/src/schema_drift/analyze.py` containing:
  - `get_parquet_schema` (lines 665–672)
  - `extract_period` (lines 675–680)
  - `find_parquet_files` (lines 683–692)
  - `compare_schemas` (lines 695–723)
  - `schema_signature` (lines 726–728)
  - `analyze_data_type` (lines 731–842)

  Preceded by:
  ```python
  import sys
  from pathlib import Path

  import duckdb

  from schema_drift.models import ColumnInfo, SchemaChange
  from schema_drift.renames import detect_renames, detect_renames_by_data, verify_renames_with_data
  ```

- [ ] **4.9 Extract `report.py`**

  Create `schema-drift/src/schema_drift/report.py` containing:
  - `format_schema_table` (lines 845–854)
  - `generate_report` (lines 857–997)

  Preceded by:
  ```python
  from collections import defaultdict

  from schema_drift.models import ColumnInfo
  ```

- [ ] **4.10 Extract `cli.py`**

  Create `schema-drift/src/schema_drift/cli.py` containing the `main()` function (lines 1000–1069) and the `if __name__ == "__main__"` guard (lines 1072–1073).

  Preceded by:
  ```python
  import argparse
  import sys
  from pathlib import Path

  import duckdb

  from schema_drift.analyze import analyze_data_type
  from schema_drift.report import generate_report
  ```

- [ ] **4.11 Update `pyproject.toml`: add package + script entry**

  Add to packages list:
  ```toml
  [tool.hatch.build.targets.wheel]
  packages = [
    "k6-loadtest/src/k6_loadtest",
    "schema-drift/src/schema_drift",
    "shared/src/taxi_shared",
  ]
  ```

  Add to scripts:
  ```toml
  [project.scripts]
  k6-preprocess = "k6_loadtest.preprocess:main"
  schema-drift = "schema_drift.cli:main"
  ```

- [ ] **4.12 Delete the original `schema_drift.py`**

  ```bash
  git rm schema_drift.py
  ```

- [ ] **4.13 Re-sync and run the smoke tests**

  ```bash
  uv sync
  uv run pytest tests/schema_drift/test_smoke.py -v
  ```

  Expected: all three smoke tests pass. If any fail, the split has an import loop or missing symbol — fix before continuing. Common causes: forgot to include a function in the target module; imported from wrong module; missed a top-level import.

- [ ] **4.14 Run the full suite**

  ```bash
  uv run pytest -q
  ```

  Expected: full suite passes.

- [ ] **4.15 Smoke-test the CLI entry point**

  ```bash
  uv run schema-drift --help
  ```

  Expected: argparse help output identical to the original script's help.

- [ ] **4.16 Commit**

  ```bash
  git add -A
  git commit -m "refactor: split schema_drift.py into schema-drift/src/schema_drift/ package"
  ```

---

## Task 5: Cleanup

**Motivation:** Delete files that are duplicates or leftovers from earlier iterations. Update `.gitignore` for the new K6 output path.

**Files:**
- Delete: `config.yaml` (root — duplicate of `k6-loadtest/config.sample.yaml`)
- Delete: `raw_data_urls.txt` (leftover from pre-rewrite downloader)
- Modify: `.gitignore` (adjust `k6_output/` → `k6-loadtest/output/`)

### Steps

- [ ] **5.1 Delete duplicate config**

  ```bash
  git rm config.yaml
  ```

- [ ] **5.2 Delete leftover URL list**

  ```bash
  git rm raw_data_urls.txt
  ```

- [ ] **5.3 Update `.gitignore`**

  Open `.gitignore` and find the line:
  ```
  k6_output/
  ```
  Replace with:
  ```
  k6-loadtest/output/
  ```

  Keep every other line in `.gitignore` unchanged (in particular `raw/` stays).

- [ ] **5.4 If `k6_output/` exists at root, move it**

  ```bash
  if [ -d k6_output ]; then
      mkdir -p k6-loadtest
      mv k6_output k6-loadtest/output
  fi
  ```

  (This is user-local uncommitted data; the `mv` is safe because `k6_output/` was gitignored.)

- [ ] **5.5 Verify no orphaned files at root**

  ```bash
  ls
  ```

  Expected top-level entries (approximately): `LICENSE`, `README.md`, `THIRD_PARTY_NOTICES`, `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, `downloader/`, `schema-drift/`, `k6-loadtest/`, `shared/`, `tests/`, `docs/`, `raw/` (gitignored), and no leftover `.sh`/`.py`/`.yaml` files sitting alongside them.

- [ ] **5.6 Run tests one more time**

  ```bash
  uv run pytest -q
  ```

  Expected: all tests pass. Cleanup should not have broken anything.

- [ ] **5.7 Commit**

  ```bash
  git add .gitignore
  git commit -m "chore: remove duplicate config.yaml and leftover raw_data_urls.txt, update gitignore"
  ```

---

## Task 6: Documentation

**Motivation:** Every subdirectory needs a README a user can start from. Top-level README becomes an index. This is the last commit so it describes the final state.

**Files:**
- Modify: `README.md` (rewrite as index)
- Create: `downloader/README.md`
- Create: `schema-drift/README.md`
- Create: `k6-loadtest/README.md`

### Steps

- [ ] **6.1 Rewrite top-level `README.md`**

  Overwrite `README.md` with:

  ```markdown
  # taxi

  Tools for downloading, analyzing, and load-testing with NYC TLC taxi trip data.

  ## Components

  - **[downloader/](downloader/)** — WAF-aware bulk downloader for TLC parquet files, with exponential backoff and boundary auto-termination per data type.
  - **[schema-drift/](schema-drift/)** — Analyzer that detects and reports schema changes across TLC parquet files over time, with name-based and data-driven rename detection.
  - **[k6-loadtest/](k6-loadtest/)** — K6-based SQL Server load tester. Preprocesses parquet (or generates synthetic data) into K6-compatible input.

  See each component's README for install and usage.

  ## Install

  ```bash
  uv sync
  ```

  This installs `k6-preprocess` and `schema-drift` as commands. The downloader is a standalone bash script — no install step required.

  ## Acknowledgments

  Originally inspired by [toddwschneider/nyc-taxi-data](https://github.com/toddwschneider/nyc-taxi-data) (MIT). See [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES).
  ```

- [ ] **6.2 Create `downloader/README.md`**

  ```markdown
  # downloader

  Bash script that mirrors NYC TLC parquet trip data from CloudFront to a local `raw/` directory.

  ## What makes it different

  - **WAF-aware retry classifier** — distinguishes CloudFront WAF blocks (HTML error page, or 429/503) from "file not published yet" (403 with S3-style AccessDenied XML). Naive downloaders treat both as generic failure and either false-positive on rate limits or false-negative on missing files.
  - **Exponential backoff** on real rate-limit hits: 5 min → 15 min → 60 min, resets on any successful download.
  - **Boundary auto-termination** — walks each series chronologically forward, stops cleanly when it reaches the end of published data for that type, then moves on to the next type.
  - **Parquet validation** — verifies PAR1 magic bytes at both head and tail of every downloaded file; truncated / intercepted downloads are automatically retried.

  ## Requirements

  - `bash` 4+
  - `curl`
  - `find`, `grep`, `head`, `tail`, `date`, `printf`, `mktemp`, `sleep` (all standard on Linux/macOS)

  **Windows:** install [Git for Windows](https://gitforwindows.org/) and run the script in Git Bash. No additional setup needed.

  ## Usage

  From the repo root:

  ```bash
  # Catch up on all history for every data type (yellow, green, fhv, fhvhv)
  ./downloader/download_taxi_data.sh

  # Just the newest N months per type (useful for incremental updates)
  ./downloader/download_taxi_data.sh --recent 3
  ```

  Files land in `raw/<type>/<year>/<type>_tripdata_YYYY-MM.parquet`. Already-downloaded files are skipped. Corrupt files (missing PAR1 magic bytes) are cleaned up automatically at the start of each run.

  ## Alternative: query in place

  If you don't need a local mirror, DuckDB's `httpfs` extension can query TLC parquet directly from CloudFront:

  ```sql
  INSTALL httpfs; LOAD httpfs;
  SELECT count(*) FROM read_parquet('https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-01.parquet');
  ```

  This downloader is for cases where you want a resumable local mirror — bulk analytics, offline work, or feeding a database.
  ```

- [ ] **6.3 Create `schema-drift/README.md`**

  ```markdown
  # schema-drift

  Detects and reports schema changes across NYC TLC parquet files over time. Uses DuckDB to introspect parquet files, compares schemas at every transition point, and identifies added / removed / type-changed / renamed columns.

  ## Usage

  ```bash
  # Analyze the default `raw/` directory across all four data types
  uv run schema-drift

  # Focus on specific types
  uv run schema-drift --types yellow green

  # Write the report to a file
  uv run schema-drift --output drift-report.txt

  # Use data-driven rename detection (no domain knowledge)
  uv run schema-drift --generic

  # Verify name-based renames with actual data (slower but more accurate)
  uv run schema-drift --verify-data
  ```

  Requires the parquet mirror produced by the [downloader](../downloader/README.md) at `raw/<type>/<year>/*.parquet`.

  ## Modes

  - **Default (taxi mode)** — Uses NYC-TLC-specific abbreviations and semantic categories (pickup/dropoff, coordinates, location IDs, amounts, datetimes) to detect renames by name similarity.
  - **`--generic`** — Ignores domain knowledge; detects renames purely by comparing column data (null ratios, cardinality, value ranges, top values). Slower; results require human review.
  - **`--verify-data`** — In taxi mode, samples actual data to verify low-confidence rename candidates.
  ```

- [ ] **6.4 Create `k6-loadtest/README.md`**

  ```markdown
  # k6-loadtest

  K6-based SQL Server load tester with a Python preprocessor that turns parquet (or synthetic data specs) into a K6 test bundle: CREATE TABLE DDL, chunked JSON payloads, a K6 `test.js`, and a manifest.

  ## Prerequisites

  - **Go 1.22+** (used by `build_k6.sh` to compile a K6 binary with the SQL Server extension)
  - **A SQL Server instance** to test against (local Docker works; a sample compose file is not included but see `config.sample.yaml`)

  ## Setup

  ```bash
  # 1. Build the custom K6 binary (produces `./k6-loadtest/k6`)
  ./k6-loadtest/build_k6.sh

  # 2. Copy the sample config and adjust for your environment
  cp k6-loadtest/config.sample.yaml k6-loadtest/config.yaml
  $EDITOR k6-loadtest/config.yaml

  # 3. Preprocess data into K6 inputs
  uv run k6-preprocess --config k6-loadtest/config.yaml --output k6-loadtest/output/

  # 4. Create tables (apply files under k6-loadtest/output/schema/ to your SQL Servers)

  # 5. Run the load test
  MSSQL_PASSWORD=yourpass ./k6-loadtest/k6 run k6-loadtest/output/test.js
  ```

  ## Data source modes

  Set per-source in `config.yaml`:

  - **`mode: parquet`** — reads real data from parquet files (typically the `raw/` mirror from the downloader).
  - **`mode: synthetic`** — K6 generates random rows at runtime from column value ranges. Instant startup, unlimited scale, no parquet files needed.

  See `config.sample.yaml` for full option documentation.
  ```

- [ ] **6.5 Commit**

  ```bash
  git add README.md downloader/README.md schema-drift/README.md k6-loadtest/README.md
  git commit -m "docs: rewrite README as index and add per-component READMEs"
  ```

---

## Post-restructure verification

- [ ] **Full test suite passes**

  ```bash
  uv run pytest -q
  ```

- [ ] **Both entry points work**

  ```bash
  uv run k6-preprocess --help
  uv run schema-drift --help
  ```

- [ ] **Downloader runs from repo root**

  ```bash
  ./downloader/download_taxi_data.sh --recent 1
  ```

- [ ] **No leftover files at root**

  ```bash
  ls
  ```

  Expected: only the entries listed in Step 5.5.

- [ ] **Import discipline**

  ```bash
  grep -rn 'from loadtest\|import loadtest' . 2>/dev/null | grep -v .git
  ```

  Expected: no output (all imports have been updated).

- [ ] **Git log shows six clean commits** (in order): shared extraction → k6_loadtest rename → downloader move → schema_drift split → cleanup → docs.

  ```bash
  git log --oneline -6
  ```
