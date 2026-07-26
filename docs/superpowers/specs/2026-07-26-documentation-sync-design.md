# Documentation Sync — Design

**Date:** 2026-07-26
**Status:** Approved
**Topic:** Bring all user-facing documentation back in line with the current codebase.

## Problem

The documentation describes a repo that no longer exists. Since the docs were
last touched, the codebase changed substantially:

- The repo was renamed `taxi` → `taxi-seed`.
- The downloader was rewritten from a bash script (`downloader/download_taxi_data.sh`)
  into a Python package (`taxi_download`, console script `taxi-download`). The bash
  script and its test were deleted.
- `k6-loadtest` was removed from this repo (it is moving to a separate `taxi-lab` repo).
- Two new tools shipped: `loader` (`taxi-load`) and `orchestrator`
  (`taxi-run`, `taxi-curate-mappings`), plus a `shared` library (`taxi_shared`).
- A configurable `--data-dir` flag was threaded across
  downloader / normalize / loader / orchestrator.

As a result: quick-start commands are broken, install instructions are wrong,
flags / env vars / exit codes documented don't match the code, k6 is presented as a
live component, and the two newest tools are largely undocumented (their READMEs even
link to guide pages that 404).

## Ground truth (verified against code)

- **Console scripts** (`pyproject.toml [project.scripts]`): `normalize`,
  `schema-drift`, `taxi-curate-mappings`, `taxi-download`, `taxi-load`, `taxi-run`.
- **Wheel packages** (6): `taxi_download`, `taxi_loader`, `taxi_normalize`,
  `taxi_orchestrate`, `schema_drift`, `taxi_shared`.
- **Deps:** `duckdb`, `httpx`, `pyyaml`. `requires-python >= 3.12`. Installed via
  `uv sync` at the **repo root** (single root package, not per-subdirectory).
- **Downloader** (`taxi_download`): positional `data_type` (yellow/green/fhv/fhvhv;
  omit = all), `--recent [N]` (default N=3), `--data-dir DIR` (files land under
  `DIR/raw`, default `.`). No `--from`/`--to`, no `OUTPUT_DIR`. Uses `httpx` (not curl).
  Per-month retry backoff 30 → 90 → 270s capped at 3600s, `MAX_RETRIES=4`;
  full-history walk aborts a type after `MAX_CONSECUTIVE_GIVEUPS=3`. Exit `2` only when
  a type finished with `downloaded==0 and gaveup>0`, else `0`. Start dates:
  yellow 2009-01, green 2013-08, fhv 2015-01, fhvhv 2019-02.
- **`MSSQL_PASSWORD`** is read by `taxi-load` and `taxi-run` (required for the load stage).
- **CI** (`ci.yml`): `test` (matrix 3.12/3.13), `docs` (push→main only, `mkdocs gh-deploy`),
  `integration` (SQL Server service, Python 3.13).

The full per-file discrepancy list lives in the audit conducted during brainstorming;
this spec captures the decisions and the shape of the work, not every line.

## Scope

Three categories of change.

### 1. Surgical corrections to existing docs

Driven by the code-verified audit. Files: `README.md`, `docs/index.md`,
`docs/getting-started.md`, `mkdocs.yml`, `docs/guides/downloader.md`,
`docs/guides/normalize.md`, `docs/guides/schema-drift.md`, `docs/architecture.md`,
`docs/cookbook.md`, `docs/contributing.md`, `docs/reference/configuration.md`,
`docs/reference/exit-codes.md`, `docs/operations/releasing.md`.

Key corrections:

- Rename `taxi` → `taxi-seed` (titles, `cd taxi`, mkdocs `site_name`/`site_description`).
- Downloader: bash → Python everywhere; real flags, backoff, exit codes, start dates;
  drop `OUTPUT_DIR` and curl. `downloader.md` is a near-total rewrite.
- Framing: "four tools" → the actual set (downloader, schema-drift, normalize, loader,
  orchestrator + shared lib). Remove k6 as a live component.
- Reference: rewrite exit-codes for `taxi-download`; add `taxi-load`, `taxi-run`,
  `taxi-curate-mappings`; drop k6-preprocess. In configuration, drop the k6 YAML section,
  fix the `OUTPUT_DIR`/`MSSQL_PASSWORD`/proxy rows, and document `--data-dir`.
- normalize guide: document `--data-dir`; fix exit-2 wording, "Amended by" header on
  amend, scaffold comment text, SUGGESTED punctuation.
- schema-drift guide: install at repo root; confidence 0.7 (default/taxi mode) vs
  0.6 (generic mode).
- architecture: 5 tools + shared; Python downloader; loader & orchestrator are **built**,
  not "not yet built"; updated DAG and repo layout.
- cookbook: replace bash-downloader and k6 recipes with `taxi-download` / `taxi-load` /
  `taxi-run`.
- Test-count claims: replace brittle hardcoded numbers ("83 tests", "under a second")
  with durable phrasing that distinguishes the fast unit suite from the SQL-Server
  integration/e2e tests.

### 2. Two new guides

Author `docs/guides/loader.md` and `docs/guides/orchestrator.md` matching the depth and
house style of the existing guides, verified against source. Add both to the mkdocs nav.
Fix the broken `guides/loader/` and `guides/orchestrator/` links in the respective
component READMEs.

### 3. k6 purge with stash

Remove all k6 references from the live docs. Move the k6 design spec
(`docs/superpowers/specs/2026-03-25-k6-sql-load-testing-design.md`) and plan
(`docs/superpowers/plans/2026-03-25-k6-sql-load-testing.md`) into a new git-tracked,
non-published top-level directory `taxi-lab-handoff/`, with a short README noting the
files are staged for migration to the `taxi-lab` repo. Remove the k6 spec from the
mkdocs nav.

Out of scope: the leftover untracked `k6-loadtest/` `.pyc` directory (code, not docs) —
flag it to the user but do not touch it as part of this work.

## Verification

- `mkdocs build --strict` succeeds (no broken links, no nav/orphan warnings).
- The doc-relevant test suite passes.
- Spot-check: every documented command, flag, env var, and exit code matches the code.

## Non-goals

- No code changes (only documentation and doc-adjacent file moves).
- No new features; no refactoring beyond the doc content itself.
- No rewrite of the historical `superpowers/plans` and `superpowers/specs` archive
  (point-in-time records), except moving the k6 spec/plan to the handoff dir.
