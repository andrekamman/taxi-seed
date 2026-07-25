# dev/test/prod promotion + release pipeline — design spec (2026-07-25)

**Sub-project:** the first of the "last two parts" the user requested. Part 1 (**this spec**) = branch
separation + a CI-gated PR flow + tag-driven release promotion to TestPyPI (test) and PyPI (prod).
Part 2 (separate, later) = a full documentation sweep reflecting the whole expansion.

**Goal:** Establish dev/test/prod as a *code-artifact promotion* pipeline for the `taxi-seed`
package: changes land on `dev` via CI-gated pull requests, and the same merged code is promoted to
TEST (TestPyPI) and PROD (PyPI) by pushing git tags, using GitHub-native mechanisms standard for
small open-source Python repos.

**Repo identity (context):** this repo is a **practice/seed data installer** — it loads realistic NYC
TLC taxi data into a database (SQL Server today; more loaders/transformers later) so people can learn
data engineering and database migrations. The migration *recipes* live in a separate `taxi-lab` hub
repo that depends on this package. That is why the PyPI distribution is named for its job (`taxi-seed`),
not the dataset.

---

## Decisions locked (from brainstorming)

- **Promotion model:** trunk `dev` + tag-driven releases. **TestPyPI = TEST, PyPI = PROD.**
- **Branch topology:** `dev` (new default branch / integration trunk) + `main` (stable, released;
  mkdocs `gh-deploy` runs from `main`). Not three long-lived env branches.
- **PR gate:** the `integration` job (SQL Server e2e) is a **required status check** on `dev`.
- **Publishing:** real **Trusted Publishing (OIDC)** to TestPyPI and PyPI — no stored API tokens.
- **Versioning:** tag-driven via **`hatch-vcs`** (the git tag is the single source of truth; drop the
  static `version = "0.1.0"`).
- **Distribution name:** `taxi-seed` (verified available on PyPI 2026-07-25). Import packages
  (`taxi_loader`, `taxi_normalize`, `taxi_orchestrate`, `taxi_shared`, `schema_drift`, `k6_loadtest`)
  and console scripts (`taxi-load`, `taxi-run`, `normalize`, …) are **unchanged** — only
  `[project].name` moves.

---

## Background / current state (facts)

- **Package:** `pyproject.toml` `[project] name = "taxi"`, static `version = "0.1.0"` (the only version
  string in the repo), build backend `hatchling`. Not published; **no git tags**, no release
  automation, no `Makefile`. Deps `duckdb>=1.4.4`, `pyyaml>=6.0`; extras `test`, `docs`.
- **Connection config:** argparse flags with hard-coded defaults (`localhost`/`1433`/`taxi`/`dbo`/`sa`)
  + `MSSQL_PASSWORD` env for the password. **No environment concept, no config file, no settings
  module.** (This spec does **not** add one — promotion is about code artifacts, not runtime DB
  targets. See Out of scope.)
- **CI** (`.github/workflows/ci.yml`): `on: push: branches:[main]` + `on: pull_request` (no branch
  filter → any PR base). Jobs: `test` (py3.12/3.13 unit), `integration` (SQL Server service container
  + loader/e2e tests) — both run on every PR and on push-to-main; `docs`
  (`if: push && ref==refs/heads/main`, `needs: test`) deploys mkdocs to `gh-pages`.
- **Branches:** default `main`; `gh-pages` (docs artifact). **No `dev` branch.** Remote
  `github.com/andrekamman/taxi`, a **personal public repo** → branch protection + GitHub Environments
  (with required reviewers) are available on the Free plan.
- **Docs:** mkdocs-material; no Operations/Deployment section today. `docs/architecture.md`
  "What's not built yet" (line ~194) names this exact work and is stale.

---

## Design

### A. Branch topology (`dev` + `main`)

- Create a **`dev`** branch from current `main`; make `dev` the **default branch** on GitHub (so PRs
  target it by default).
- **`main`** remains the stable/released branch. mkdocs continues to deploy from `main`.
- Flow: `feature/*` → PR → **`dev`** (CI-gated) → when a release is desired, PR/merge **`dev` → `main`**
  → tag the release on `main`.
- The `dev`→`main` promotion is an ordinary PR (also CI-gated), keeping `main` always releasable.

### B. CI as the PR gate (`.github/workflows/ci.yml`)

- Keep `test` and `integration` running on `pull_request` (already unfiltered → covers PRs into `dev`
  and `main`). Add `dev` to the push trigger so direct pushes to `dev` also run them:
  `on: push: branches: [main, dev]`. `docs` stays `main`-push-only.
- **Required status check:** make the `integration` job a required check for merging into `dev` (and
  `main`) via a **branch-protection rule / ruleset**. This is a GitHub *repo setting*, not in-repo
  YAML. The spec provides the exact `gh api` command; the user applies it once (§E). This is the
  GitHub equivalent of Azure DevOps build-validation branch policy.
- **Job-name stability:** the required check is referenced by the job's name (`integration`); renaming
  the job later silently disarms the gate — call this out in the ops docs.

### C. Release & promotion workflow (`.github/workflows/release.yml`)

Triggered `on: push: tags: ['v*']`. One workflow, tag-pattern-driven:

1. **Build** (always): checkout with full history/tags (`fetch-depth: 0`, needed by `hatch-vcs`),
   build sdist+wheel (`uv build`), run the fast unit suite (`uv run --extra test pytest -q`, no DB) as
   a publish guard, upload `dist/` as a workflow artifact.
2. **Classify the tag** (PEP 440): a **prerelease/dev** tag (`v1.2.3rc1`, `v1.2.3a1`, `v1.2.3b1`,
   `v1.2.3.dev1`) → TEST path; a **final** tag (`v1.2.3`) → PROD path.
3. **TEST — publish to TestPyPI:** job `environment: testpypi`, `permissions: id-token: write`, publish
   `dist/` with `pypa/gh-action-pypi-publish` pointed at `https://test.pypi.org/legacy/`. Runs for
   prerelease tags (and may also run for final tags as a pre-PROD smoke — decided at plan time; default:
   prerelease tags only).
4. **PROD — publish to PyPI:** job `environment: pypi` (which carries a **required reviewer** →
   the human approval gate), `permissions: id-token: write`, publish `dist/` with
   `pypa/gh-action-pypi-publish` (default PyPI). Runs for **final** tags only. On success, create a
   **GitHub Release** for the tag with the built artifacts attached (prerelease tags → GitHub
   *prerelease*; final tags → full release).
5. **No secrets:** publishing uses **Trusted Publishing (OIDC)**; the only permission needed is
   `id-token: write`. No `PYPI_API_TOKEN`/`TWINE_*` anywhere.

> The exact `hatch-vcs` config, `uv build` invocation, and `pypa/gh-action-pypi-publish` inputs
> (including how to select TestPyPI vs PyPI and how prerelease detection is wired) will be pinned in
> the implementation plan against current upstream docs — these are easy to get subtly wrong.

### D. Versioning & distribution name (`pyproject.toml`)

- Add `hatch-vcs` to `[build-system].requires`; set `[tool.hatch.version] source = "vcs"`; **remove**
  the static `version` and add `dynamic = ["version"]`. The git tag `vX.Y.Z` → version `X.Y.Z`
  (hatch-vcs strips the leading `v`).
- Configure `hatch-vcs` so untagged/dev builds produce **PEP 440-valid** versions acceptable to
  TestPyPI/PyPI (no local `+g<sha>` segment on published artifacts) — pinned at plan time.
- Rename `[project].name` `taxi` → **`taxi-seed`**. Add minimal PyPI-facing metadata that costs
  nothing and improves the listing: `description` (already present), `readme = "README.md"` (README.md
  exists at repo root — reference it), `license`, `[project.urls]` (Homepage → repo, Docs → the
  mkdocs site), and a few `classifiers`. **No** change to `[tool.hatch.build.targets.wheel] packages`
  or `[project.scripts]`.

### E. Manual, out-of-band setup (the user's steps — documented, not automated in-repo)

The workflows assume these one-time actions; the ops docs page (§F) lists them with exact values:
1. **Create the `dev` branch**, push it, set it as the **default branch** on GitHub.
2. **PyPI + TestPyPI:** create accounts; register a **Trusted Publisher** on each for repo
   `andrekamman/taxi`, workflow filename `release.yml`, and environment name `pypi` (PyPI) /
   `testpypi` (TestPyPI).
3. **GitHub Environments:** create `testpypi` and `pypi`; add a **required reviewer** to `pypi` (the
   PROD approval gate).
4. **Branch protection on `dev`** (and `main`): require the `integration` status check (and, if
   desired, a PR review). Provide the `gh api` / ruleset command in the docs.

### F. Documentation (this sub-project only)

- Add one page: **`docs/operations/releasing.md`** (new "Operations" nav group in `mkdocs.yml`) — the
  branch model, the CI PR gate, the tag → TestPyPI → PyPI promotion flow, and the §E manual setup
  checklist (accounts, trusted publishers, environments, branch protection).
- Update `CONTRIBUTING`/`docs/contributing.md` to state the new flow (branch from `dev`, PR into
  `dev`, integration check must pass).
- The **broader docs sweep** (fixing stale `architecture.md`, reflecting loader/orchestrator/CI/e2e)
  is **sub-project 2** — not in this spec.

---

## Testing / validation strategy

- **Workflow validity:** `release.yml` and the edited `ci.yml` parse (`yaml.safe_load`); job/trigger
  structure asserted (e.g. release job classification for a sample prerelease vs final tag).
- **Build correctness (local, no publish):** `uv build` produces an sdist+wheel whose **version derives
  from a git tag** — validated by creating a throwaway local tag (e.g. `v0.0.0test`) and asserting the
  built artifact's version, then deleting the tag. Confirms `hatch-vcs` + dynamic version + the
  `taxi-seed` dist name (built filename is `taxi_seed-<v>-*.whl`) without touching PyPI.
- **Metadata:** assert `[project].name == "taxi-seed"`, `dynamic` includes `version`, no static
  `version`, and the wheel still contains all six import packages + console scripts (`uv build` +
  inspect, or `pip show`/entry-points after a local install into a temp venv).
- **Publishing itself is validated out-of-band** on the first real prerelease tag (TestPyPI) — CI can't
  publish to real indexes from a test run, and Trusted Publishing needs the registered publisher. The
  ops docs give the exact first-run procedure.
- **Non-regression:** the existing `test`/`integration`/`docs` jobs and the full pytest suite remain
  green; the `ci.yml` edits don't alter existing job bodies.

## Out of scope (explicit)

- **Runtime DB-target environments / a config-file or `--env` selector for the loader.** Promotion here
  is code-artifact (PyPI) promotion; it does **not** add dev/test/prod *database* connection profiles.
  (A future piece could add a `taxi-seed --env` config surface; not now.)
- **Publishing `taxi-lab` (the recipes hub) or splitting this repo.** `taxi-lab` is a separate repo the
  user owns; this spec only makes *this* package cleanly installable/depend-able.
- **The full documentation sweep** (sub-project 2).
- **k6-in-CI (piece C)** and **schema-diff promotion gates** — still deferred.
- **Automating the PyPI/GitHub account & environment setup** — inherently manual/out-of-band;
  documented, not scripted (beyond the branch-protection `gh` command).

## Notes for the eventual plan

- Pin exact `hatch-vcs`/`uv build`/`gh-action-pypi-publish`/OIDC config against current docs.
- Decide final-tag behavior: TestPyPI-then-PyPI vs PyPI-only (default: prerelease→TestPyPI only,
  final→PyPI only + GitHub Release).
- Ensure `fetch-depth: 0` wherever `hatch-vcs` reads tags (build job).
- `README.md` exists at repo root — reference it via `readme = "README.md"`.
- The branch-protection required-check name must match the job name `integration` exactly.
