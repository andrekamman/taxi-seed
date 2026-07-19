# Monorepo Restructure Design

**Date:** 2026-07-19
**Status:** Approved, ready for implementation planning

## Motivation

The repo has grown to contain three distinct tools (a bash downloader, a Python schema-drift analyzer, a K6 SQL-Server load tester) with more planned (a DuckDB-based SQL Server bulk loader). Today they share one flat directory layout, one `pyproject.toml`, one config file at root, and the Python code is a single package called `loadtest`. Before making the repo public, we want the three tools to live as clearly separated components under one repo, sharing the code that genuinely belongs shared, and giving each tool its own documentation entry point.

## Non-goals

- Splitting into multiple independently-installable packages. One `pip install taxi` gets everything (decision recorded during brainstorming).
- Publishing to PyPI. That happens later; the placeholder name `taxi` stays in the design and is renamed at publish time.
- Rewriting any tool in a different language. The Rust/Go conversation from the brainstorming session was explicitly deferred.
- Building the future SQL Server loader. This restructure only prepares the ground for it.

## Current state

```
.
├── LICENSE  THIRD_PARTY_NOTICES  README.md
├── pyproject.toml  uv.lock  .python-version  .gitignore
├── config.yaml                     (K6 sample; duplicates loadtest/config.sample.yaml)
├── raw_data_urls.txt               (leftover from pre-rewrite downloader)
├── download_taxi_data.sh           (399 lines, WAF-aware retry, PAR1 validation)
├── schema_drift.py                 (1073 lines, single-file script)
├── build_k6.sh                     (compiles custom K6 binary)
├── k6                              (compiled binary, gitignored, not tracked)
├── config.yaml
├── loadtest/                       (Python package)
│   ├── __init__.py  config.py  data_export.py  k6_generator.py
│   ├── preprocess.py               (entry point: preprocess)
│   ├── sql_generator.py            (DuckDB → SQL Server CREATE TABLE)
│   ├── type_mapping.py             (DuckDB → SQL Server type map)
│   └── config.sample.yaml
├── tests/loadtest/                 (6 test modules)
├── raw/                            (gitignored data mirror)
├── k6_output/                      (gitignored K6 output)
└── docs/superpowers/{specs,plans}/ (prior K6 load-testing spec + plan)
```

The DuckDB→SQL Server bits inside `loadtest/type_mapping.py` and `loadtest/sql_generator.py` are the primary shared-code candidates: the future SQL Server loader will need them, and `schema-drift` may benefit from the type-map too.

## Target layout

```
taxi/
├── README.md                        # Index pointing into per-tool READMEs
├── LICENSE  THIRD_PARTY_NOTICES
├── pyproject.toml  uv.lock  .python-version  .gitignore
│
├── downloader/
│   ├── README.md
│   └── download_taxi_data.sh
│
├── schema-drift/
│   ├── README.md
│   └── src/schema_drift/
│       ├── __init__.py
│       ├── cli.py                   # argparse + main()
│       ├── scanner.py               # walk parquet, extract schemas via DuckDB
│       ├── compare.py               # diff schemas across months/years/types
│       └── report.py                # output formatting
│
├── k6-loadtest/
│   ├── README.md
│   ├── build_k6.sh
│   ├── config.sample.yaml
│   └── src/k6_loadtest/
│       ├── __init__.py
│       ├── config.py
│       ├── data_export.py
│       ├── k6_generator.py
│       └── preprocess.py            # entry point: k6-preprocess
│
├── shared/
│   └── src/taxi_shared/
│       ├── __init__.py
│       ├── type_mapping.py          # moved from loadtest/
│       └── sql_generator.py         # moved from loadtest/
│
├── tests/
│   ├── schema_drift/
│   ├── k6_loadtest/                 # existing tests, imports updated
│   └── taxi_shared/                 # extracted type_mapping + sql_generator tests
│
├── docs/superpowers/{specs,plans}/  # existing; new spec + plan land here
│
├── raw/                             # gitignored, shared TLC data mirror
└── k6-loadtest/output/              # gitignored, was root k6_output/
```

Naming: directories use kebab-case, Python packages use snake_case. The `src/` layout is used consistently for all three Python packages — not strictly required, but it's standard for public packages and prevents accidental imports of top-level directories that happen to share a name with a package.

## Per-tool details

### downloader/

- No Python, no pyproject entry, no tests package. Pure bash.
- README covers: what the tool does, WAF-aware retry / boundary detection differentiators, install requirements (bash 4+, curl), Windows note (install Git for Windows), the two usage modes (`--recent N` and default full catch-up).
- The script's working-directory contract changes: it currently writes to `./raw/` (relative to CWD, which is the repo root today). Because the script now lives in `downloader/`, either:
  - the script always resolves `raw/` relative to the repo root (via `dirname "$0"/..`), or
  - the README documents that users invoke it from the repo root as `./downloader/download_taxi_data.sh`.

  Design decision: resolve `raw/` relative to the script's own directory (`$(dirname "$0")/../raw`) so the script works from any CWD. This is a small edit to the script; verified during implementation.

### schema-drift/

- Python package `schema_drift`, script entry `schema-drift = "schema_drift.cli:main"`.
- The 1073-line source file is split into modules. Final boundaries confirmed during writing-plans by reading the current file; the working target is `cli.py / scanner.py / compare.py / report.py`. If reading reveals a natural fifth module or shows a proposed module is tiny, adjust at that point.
- Basic import smoke tests added if none exist today. Existing functional tests (if any — the repo currently ships zero tests for schema-drift) are moved into `tests/schema_drift/`; if none exist, a minimal smoke test is added so pytest exits clean.
- README covers: purpose (schema drift analysis of TLC parquet), install, `schema-drift --help` usage, output format.

### k6-loadtest/

- Python package `k6_loadtest`, script entry renamed `preprocess` → `k6-preprocess = "k6_loadtest.preprocess:main"`.
- Directory rename: `loadtest/` → `k6-loadtest/src/k6_loadtest/`.
- Two files are extracted to `shared/`: `type_mapping.py`, `sql_generator.py`. Imports inside `k6_loadtest` change from `from .type_mapping import X` to `from taxi_shared.type_mapping import X` (and analogous for `sql_generator`).
- `build_k6.sh` and `config.sample.yaml` move inside the tool's directory.
- Existing `tests/loadtest/` moves to `tests/k6_loadtest/` with import updates. The two tests that cover the extracted modules (`test_type_mapping.py`, `test_sql_generator.py`) move to `tests/taxi_shared/`.
- README covers: what it does, K6 install prerequisites, `build_k6.sh` step, `config.sample.yaml → config.yaml` workflow, the two modes (parquet vs synthetic), running the generated test.

### shared/ (taxi_shared)

- Python package `taxi_shared`. No CLI entry point — it's a library.
- Just the two extracted modules + minimal `__init__.py`. No new abstractions added; this is a mechanical move.
- Reason for extracting now (versus waiting for the SQL loader): the second consumer is already named, extraction is a ~100-line mechanical move, and doing it now avoids drift if the k6-loadtest copies get edited before extraction happens.

## pyproject.toml

```toml
[project]
name = "taxi"                       # placeholder — decide at publish time
version = "0.1.0"
description = "NYC TLC taxi data tools"
requires-python = ">=3.12"
dependencies = ["duckdb>=1.4.4", "pyyaml>=6.0"]

[project.optional-dependencies]
test = ["pytest>=8.0"]

[project.scripts]
k6-preprocess = "k6_loadtest.preprocess:main"
schema-drift  = "schema_drift.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = [
  "k6-loadtest/src/k6_loadtest",
  "schema-drift/src/schema_drift",
  "shared/src/taxi_shared",
]
```

The downloader has no entry (bash-only). Dependencies stay shared across all three Python packages via a single dependency list — sufficient for `duckdb` and `pyyaml` today, which are used by both `schema_drift` and `k6_loadtest`.

## Migration order

One PR, structured as a sequence of atomic commits. Each commit ends with `uv run pytest` green before the next starts.

1. **Create `shared/` package.** Move `type_mapping.py` + `sql_generator.py` from `loadtest/` into `shared/src/taxi_shared/`. Update the two Python files inside `loadtest/` that import them (`k6_generator.py`, `preprocess.py` — verified during implementation). Update `pyproject.toml` packages list to add `taxi_shared`. Move `test_type_mapping.py` and `test_sql_generator.py` from `tests/loadtest/` to `tests/taxi_shared/` with updated imports. Run tests → green.

2. **Rename `loadtest/` → `k6-loadtest/src/k6_loadtest/`.** Update `pyproject.toml`: rename package path, rename script entry `preprocess` → `k6-preprocess`. Move `build_k6.sh` and `config.sample.yaml` inside `k6-loadtest/`. Move `tests/loadtest/` → `tests/k6_loadtest/`. Update all `from loadtest.X` imports to `from k6_loadtest.X`. Run tests → green.

3. **Move `download_taxi_data.sh` into `downloader/`.** Adjust the script's `raw/` path resolution to be script-relative (`$(dirname "$0")/../raw`) so it works from any CWD. Manual smoke-run with `--recent 1` to confirm no regression. Add `downloader/README.md`.

4. **Split `schema_drift.py` into `schema-drift/src/schema_drift/` modules.** Largest step; own commit. Reader-driven — module boundaries confirmed by first reading the script during writing-plans. Add `pyproject.toml` script entry. Add at least an import smoke test in `tests/schema_drift/` if no functional tests exist to carry over. Run tests → green.

5. **Cleanup commit.** Delete root `config.yaml` (duplicate of `k6-loadtest/config.sample.yaml`). Delete `raw_data_urls.txt` (unused since downloader rewrite). Update `.gitignore`: change `k6_output/` → `k6-loadtest/output/`; keep `raw/`.

6. **Documentation commit.** Rewrite top-level `README.md` as an index. Write per-tool READMEs: `downloader/README.md`, `schema-drift/README.md`, `k6-loadtest/README.md`. Top-level README links to each and keeps the existing acknowledgment section pointing at `THIRD_PARTY_NOTICES`.

Why in this order: shared extraction goes first because it's the most invasive edit and every subsequent step benefits from clean imports. Downloader move is trivial and can slot anywhere. Schema-drift split is largest and goes late so it doesn't block other work if it turns up unexpected complexity. Documentation goes last so it describes the final state, not intermediate.

## Out of scope for this restructure

- Any behavioral change to the downloader, schema-drift, or k6-loadtest beyond what's needed to make imports resolve and the script's `raw/` path work.
- Renaming the umbrella project from `taxi` to anything else.
- Splitting the umbrella into multiple installable packages (uv workspace).
- Building the SQL Server loader tool.
- CI configuration (there is none today; add later if/when needed).
- Reviewing schema-drift for correctness or improvement during the split — the split preserves behavior; any improvements are a separate task.

## Success criteria

- Repo tree matches the target layout above.
- `uv sync` succeeds with no changes to declared dependencies.
- `uv run pytest` passes.
- `uv run k6-preprocess --help` and `uv run schema-drift --help` both work.
- `./downloader/download_taxi_data.sh --recent 1` runs successfully from the repo root and writes to `raw/`.
- Every per-tool README loads and describes its tool without needing content from other READMEs.
- No file left orphaned at the repo root that belongs inside a tool directory.
