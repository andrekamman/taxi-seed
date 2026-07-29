# Contributing

Contributions are welcome — the standard flow is fork the repo, cut a branch off `dev`, and open a pull request back into `dev`. Before you dive into the code, the [design specs](superpowers/specs/2026-07-19-monorepo-restructure-design.md) under `docs/superpowers/specs/` are the best starting point for the "why is this shaped this way?" reader; they capture the design decisions each sub-project was built against, and reading them first is usually faster than reverse-engineering intent from the diffs.

This page covers the four things you need to be productive in a PR: how to get a working dev environment, how to run and add tests, how to build the docs, and the informal conventions the tree already follows. If something on this page is out of date or contradicts what CI actually does, that itself is a bug — please file it or fix it in your PR.

## Branching & releases

`dev` is the default and integration branch — branch from it, and open your PR **into `dev`**, not `main`. The `integration` check (SQL Server e2e tests) has to pass before a PR can merge; it's a required status check, so the merge button stays disabled until it's green. `main` only moves forward via a promotion merge from `dev`, and releases happen by tagging `main` — see [Operations → Releasing](operations/releasing.md) for the full tag-to-PyPI flow.

## Dev setup

Clone the repo and sync the environment with both optional extras:

```bash
git clone https://github.com/andrekamman/taxi-seed.git
cd taxi-seed
uv sync --extra test --extra docs
```

- `--extra test` installs `pytest` (and any test-only dependencies) so you can run the test suite.
- `--extra docs` installs `mkdocs`, `mkdocs-material`, and `pymdown-extensions` so you can build the documentation site locally.

Both extras are optional at runtime; the tools themselves (`taxi-download`, `schema-drift`, `normalize`, `taxi-load`, `taxi-run`, `taxi-curate-mappings`) work with a bare `uv sync`. Contributors will almost always want both, so the command above is the recommended default.

`uv sync` creates a project-local `.venv/`. You do not need to activate it — every command in this guide uses `uv run` as the entry point, which resolves the interpreter and the extras on each invocation. If you prefer an activated shell, `source .venv/bin/activate` works, but the CI and every example below run without activation.

## Running the test suite

The unit-test portion of the suite (everything except `tests/taxi_loader/test_load_integration.py` and `tests/e2e/`, both of which need a real SQL Server container) runs in about a second on a modern laptop — there is no reason to skip it:

```bash
# Unit suite (hermetic, no external services)
uv run --extra test pytest -q
```

Expected output on a clean tree: `N passed in <n>.<nn>s` (the count grows as tests are added; treat any failure, not a specific number, as the signal). If you see a failure that does not reproduce for you locally in isolation, run the offending test with `-vv` and check whether a fixture is leaking state — every fixture in this repo is required to be `tmp_path`-scoped, and any cross-test leak is a bug worth reporting.

The full suite also includes SQL-Server integration and end-to-end tests (loader integration + pipeline e2e) that need a real SQL Server container — these are what CI's `integration` job runs, and they are not sub-second. See `.github/workflows/ci.yml` for how CI brings up the container.

To narrow the run to one component while iterating:

```bash
# One component (all tests in one directory)
uv run --extra test pytest tests/taxi_normalize -v
```

To narrow further to a single test — useful when you are red-green-refactor-ing a single behaviour:

```bash
# One specific test
uv run --extra test pytest tests/taxi_normalize/test_planner.py::test_pure_passthrough_identical_schemas -v
```

Add `-x` to stop at the first failure, or `--lf` to re-run only the tests that failed on the previous run.

## Adding a new test

The fixture pattern is documented by example in [`tests/taxi_normalize/conftest.py`](https://github.com/andrekamman/taxi-seed/blob/dev/tests/taxi_normalize/conftest.py). Fixtures build synthetic parquet families with DuckDB using `COPY (SELECT * FROM VALUES ...) TO '<path>' (FORMAT PARQUET)` — no network, no fixture files checked into the tree, and each test receives its own `tmp_path` so there is no shared state between tests. The upside is that the whole suite is hermetic and finishes in under a second even as the corpus grows; the downside is that new tests have to describe the schema they need, rather than pointing at a fixture file on disk.

Reuse the existing families wherever possible:

- `yellow_family` — three-era yellow-taxi drift (2009 old columns → 2015 mid-drift → 2024 target schema with `airport_fee`).
- `no_drift_family` — two identical-schema green-taxi files, so the normalizer should be a pure passthrough.
- `target_file` — convenience alias for the era-3 file in `yellow_family`, for tests that only need the target schema.

Add a new fixture only when you need a schema shape the existing ones do not cover; when in doubt, extend `yellow_family` with a fourth era rather than forking a new one.

A minimal test that reuses `yellow_family`:

```python
from pathlib import Path

from taxi_normalize.planner import plan_from_family


def test_yellow_family_plan_has_three_eras(yellow_family: Path) -> None:
    """The planner should emit one plan entry per source file."""
    plan = plan_from_family(yellow_family)

    # Three files, one per era
    assert len(plan.files) == 3

    # Era 3 (2024) is the target schema; the plan should mark it as passthrough
    target = next(f for f in plan.files if "2024" in str(f.path))
    assert target.action == "passthrough"
```

## Building the docs locally

The documentation site is [MkDocs](https://www.mkdocs.org/) with the [Material](https://squidfunk.github.io/mkdocs-material/) theme. For local iteration:

```bash
uv run --extra docs mkdocs serve
```

Open <http://127.0.0.1:8000/> in a browser — it live-reloads on any doc edit, so you can iterate on prose without restarting the server. Nav changes require a full restart; markdown, code blocks, and admonition edits reload in-place.

Strict build (fails on any warning — always run this before committing doc changes):

```bash
uv run --extra docs mkdocs build --strict
```

CI runs the same command on every PR that touches `docs/`, `mkdocs.yml`, or any component `README.md`. If the strict build fails locally with a broken-link warning, the fix is almost always either a stale relative link (the target file was moved or renamed) or a nav entry that points at a file that no longer exists.

## PR checklist

Before you open a PR, walk this list top-to-bottom:

- Tests pass locally (`uv run --extra test pytest -q`).
- Docs pass `mkdocs build --strict` if you touched anything under `docs/`, any component `README.md`, or `mkdocs.yml`.
- Commit messages follow the existing convention: `type(scope): imperative subject` (e.g. `feat(downloader): --data-dir flag`, `docs(normalize): fix ack_date example`). Small PRs with a single well-scoped commit are easier to review than one PR with many mixed commits.
- If you changed user-facing behavior, update the relevant guide + reference page in the same PR — a doc-drift PR later is a lot more expensive than a two-line edit now.
- If you added a new sub-project or major feature, write a design spec in `docs/superpowers/specs/` first. The spec does not need to be long, but it should make the tradeoffs you considered explicit so the reviewer can push back on the decision, not the code.

## Code style

There is no enforced formatter today — the conventions below are informal but consistent across the tree.

- **Python** — `from __future__ import annotations` at the top of every module; explicit exit-code semantics on every CLI; error-first paths (validate all inputs → err out on any problem → then do work); `ack_date` and other ISO-8601 dates are always strings, never numeric.
- **Bash** (`scripts/`, e.g. `scripts/e2e-smoke.sh`) — `set -euo pipefail` where feasible; no `#!/bin/sh` shebangs (scripts use bash 4+ features like arrays and `[[ ]]`).
- **SQL** — single-quoted string literals; DuckDB dialect is the default, with occasional SQL Server dialect in `taxi_shared/` (the SQL Server / loader side of the tree).
- **Formatting** — no `ruff`, no `black`, no `isort` in CI today. PRs that add one (with a matching CI check) are welcome; please raise an issue first so we can agree on config.
- **Typing** — Python code uses standard `typing` / `collections.abc` annotations on public function signatures, with `from __future__ import annotations` making everything a string at runtime. There is no `mypy` gate in CI; annotations are treated as documentation, not enforcement.
- **Logging / output** — CLIs write structured, human-readable status lines to stdout and errors to stderr. Exit codes carry the machine-readable signal; do not encode failure state in stdout formatting.
- **Paths** — always `pathlib.Path` in Python for filesystem work; never string concatenation with `/`. In bash, quote every path expansion (`"$FILE"`, `"${FILES[@]}"`) so spaces in the working directory do not break the pipeline.

## Where design decisions live

- [`docs/superpowers/specs/`](superpowers/specs/2026-07-19-monorepo-restructure-design.md) — one design spec per major sub-project. Answers "why is this shaped this way?". Current specs:
    - [Monorepo restructure](superpowers/specs/2026-07-19-monorepo-restructure-design.md) — why the five tools live in one repo with shared tooling instead of five separate repos.
    - [Normalizer](superpowers/specs/2026-07-21-normalizer-design.md) — the "data loss is a first-class error" contract, the mapping YAML shape, and the `ack_date` acknowledgment protocol.
    - [Documentation site](superpowers/specs/2026-07-22-documentation-design.md) — how the site is structured, what belongs in guides vs cookbook vs reference, and the strict-build convention.
    - SQL load testing (legacy) — the design for this has moved with the tool to the separate `taxi-lab` repo.
- `docs/superpowers/plans/` — implementation plans for each spec. Answers "how did we get from design to code?". Kept in the repo but excluded from the published site because they read as working notes rather than reference material.
- Commit history — the "why did this line change?" reader. Commit messages are expected to explain intent, not just restate the diff; if the "why" needs more than a paragraph, it belongs in a spec instead.
