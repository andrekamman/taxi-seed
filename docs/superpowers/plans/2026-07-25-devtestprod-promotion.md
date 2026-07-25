# dev/test/prod promotion + release pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the `taxi` package into a tag-driven, Trusted-Publishing release pipeline named `taxi-seed`, with a CI-gated PR flow into `dev` and promotion to TestPyPI (test) and PyPI (prod) via git tags.

**Architecture:** `hatch-vcs` makes the git tag the version source. A `release.yml` workflow triggers on `v*` tags: it builds sdist+wheel, then a prerelease tag publishes to TestPyPI and a final `vX.Y.Z` tag publishes to PyPI (behind a required-reviewer GitHub Environment) plus a GitHub Release — all via OIDC Trusted Publishing, no stored tokens. `ci.yml` runs the existing test/integration jobs on PRs into `dev`; making `integration` a required check is a one-time GitHub repo setting documented in a new ops page.

**Tech Stack:** hatchling + hatch-vcs, `uv build`, GitHub Actions, `pypa/gh-action-pypi-publish@release/v1` (OIDC), `gh` CLI, mkdocs-material.

## Global Constraints

_Every task's requirements implicitly include this section._

- **No new runtime dependencies.** `hatch-vcs` is added to `[build-system].requires` only (a build-time dep). Runtime deps stay `duckdb>=1.4.4`, `pyyaml>=6.0`; extras `test`/`docs` unchanged.
- **Import packages and console scripts are unchanged.** Only `[project].name` becomes `taxi-seed`. Do NOT touch `[tool.hatch.build.targets.wheel] packages` or `[project.scripts]` (`taxi-load`, `taxi-run`, `normalize`, `taxi-curate-mappings`, `schema-drift`, `k6-preprocess`).
- **No runtime DB-target env/config selector.** This work is code-artifact (PyPI) promotion only; do not add dev/test/prod database connection profiles or a `--env` flag.
- **No stored publish secrets/tokens anywhere.** Publishing uses OIDC Trusted Publishing: the only permission is `id-token: write`. No `PYPI_API_TOKEN`, `TWINE_*`, `username`/`password`.
- **Tag conventions:** tags are `vX.Y.Z` (hatch-vcs strips the leading `v`). **Prerelease/dev** tags (`vX.Y.ZrcN`, `vX.Y.ZaN`, `vX.Y.ZbN`, `vX.Y.Z.devN`) → **TestPyPI**; **final** `vX.Y.Z` → **PyPI** + GitHub Release.
- **The branch-protection required check must be named `integration`** — exactly the `ci.yml` job name. Renaming that job disarms the gate.
- **Publishing to real indexes is validated out-of-band** (first real prerelease tag). In-repo validation is: YAML parses, local `uv build` produces a correctly-named/versioned artifact, and the tag-classification logic is correct.
- **Distribution name `taxi-seed`** was verified available on PyPI (2026-07-25). `README.md` and a **MIT** `LICENSE` exist at repo root.

---

## Task 1: `pyproject.toml` — tag-driven versioning, `taxi-seed` rename, PyPI metadata

**Files:**
- Modify: `pyproject.toml`
- Regenerate: `uv.lock`

**Interfaces:**
- Produces: a package whose distribution name is `taxi-seed`, version is derived from git tags via `hatch-vcs`, and which still contains all six import packages + all console-script entry points.

- [ ] **Step 1: Rewrite the `[project]` and build/version config**

Replace the current `[project]` block (lines 1-9) and `[build-system]` block (lines 11-13) so the file begins like this (leave `[tool.hatch.build.targets.wheel]`, `[project.optional-dependencies]`, and `[project.scripts]` exactly as they are):

```toml
[project]
name = "taxi-seed"
dynamic = ["version"]
description = "NYC TLC Taxi data analysis tools"
readme = "README.md"
license = "MIT"
license-files = ["LICENSE"]
requires-python = ">=3.12"
dependencies = [
    "duckdb>=1.4.4",
    "pyyaml>=6.0",
]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Operating System :: OS Independent",
    "Topic :: Database",
]

[project.urls]
Homepage = "https://github.com/andrekamman/taxi"
Documentation = "https://andrekamman.github.io/taxi/"
Repository = "https://github.com/andrekamman/taxi"

[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[tool.hatch.version]
source = "vcs"
fallback-version = "0.0.0"

[tool.hatch.version.raw-options]
version_scheme = "no-guess-dev"
local_scheme = "no-local-version"
```

Notes:
- `dynamic = ["version"]` replaces the static `version = "0.1.0"` (which is removed).
- `local_scheme = "no-local-version"` guarantees no `+g<sha>` local segment (PyPI rejects those); `no-guess-dev` avoids version-guessing on untagged commits.
- If `uv build` (Step 3) errors on `license = "MIT"` / `license-files` (an older hatchling that predates PEP 639), fall back to `license = { file = "LICENSE" }` and drop `license-files`; keep the `License :: OSI Approved :: MIT License` classifier either way. Note which form you used in your report.

- [ ] **Step 2: Regenerate the lockfile**

Run: `uv sync --extra test`
Expected: succeeds; `uv.lock` updates. Verify the project entry renamed:
Run: `grep -n 'name = "taxi-seed"' uv.lock`
Expected: one match (the workspace/root project). (The old `name = "taxi"` entry is gone.)

- [ ] **Step 3: Validate a tag-derived build produces the right artifact (no publish)**

```bash
git tag v0.0.1                     # throwaway tag so hatch-vcs yields a clean version
uv build
ls dist/
```
Expected: `dist/taxi_seed-0.0.1-py3-none-any.whl` and `dist/taxi_seed-0.0.1.tar.gz` (the `taxi_seed-` prefix proves the rename; `0.0.1` proves tag-driven versioning).

Verify the wheel still ships everything:
```bash
unzip -l dist/taxi_seed-0.0.1-py3-none-any.whl | grep -E 'taxi_loader/|taxi_normalize/|taxi_orchestrate/|taxi_shared/|schema_drift/|k6_loadtest/|entry_points.txt'
unzip -p dist/taxi_seed-0.0.1-py3-none-any.whl 'taxi_seed-0.0.1.dist-info/entry_points.txt'
unzip -p dist/taxi_seed-0.0.1-py3-none-any.whl 'taxi_seed-0.0.1.dist-info/METADATA' | grep -E '^(Name|Version|License):'
```
Expected: all six package dirs present; `entry_points.txt` lists `taxi-load`, `taxi-run`, `normalize`, `taxi-curate-mappings`, `schema-drift`, `k6-preprocess`; METADATA shows `Name: taxi-seed`, `Version: 0.0.1`.

Clean up the throwaway tag and build output (do NOT leave the tag — a pushed tag would trigger the release workflow):
```bash
git tag -d v0.0.1
rm -rf dist
```

- [ ] **Step 4: Confirm the suite still passes**

Run: `uv run --extra test pytest -q`
Expected: same as before the change (203 passed, 11 skipped) — the rename/versioning must not affect tests.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: tag-driven versioning via hatch-vcs + rename dist to taxi-seed"
```

---

## Task 2: GitHub Actions — PR-gate trigger + tag-driven release workflow

**Files:**
- Modify: `.github/workflows/ci.yml` (trigger only)
- Create: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: the `taxi-seed` / `hatch-vcs` build from Task 1.
- Produces: `release.yml` publishing to TestPyPI (prerelease tags) / PyPI (final tags, gated) + a GitHub Release; `ci.yml` running `test`+`integration` on `dev` pushes and all PRs.

- [ ] **Step 1: Add `dev` to the CI push trigger**

In `.github/workflows/ci.yml`, change the trigger block from:

```yaml
on:
  push:
    branches: [main]
  pull_request:
```

to:

```yaml
on:
  push:
    branches: [main, dev]
  pull_request:
```

Change NOTHING else in `ci.yml` (the `test`, `integration`, and `docs` job bodies and the `docs` `if:` gate stay exactly as they are). `pull_request` remains unfiltered, so it already covers PRs into `dev` and `main`.

- [ ] **Step 2: Create the release workflow**

Create `.github/workflows/release.yml`:

```yaml
name: Release

on:
  push:
    tags:
      - "v*"

permissions:
  contents: read

jobs:
  build:
    runs-on: ubuntu-latest
    outputs:
      is_prerelease: ${{ steps.classify.outputs.is_prerelease }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0          # hatch-vcs needs full history + tags
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - run: uv python install 3.13
      - run: uv sync --extra test
      - name: Unit-test guard
        run: uv run --extra test pytest -q
      - name: Build sdist + wheel
        run: uv build
      - name: Classify tag (prerelease vs final)
        id: classify
        run: |
          tag="${GITHUB_REF_NAME}"
          if [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo "is_prerelease=false" >> "$GITHUB_OUTPUT"
          else
            echo "is_prerelease=true" >> "$GITHUB_OUTPUT"
          fi
          echo "tag=$tag prerelease=$([[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] && echo false || echo true)"
      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/

  testpypi:
    needs: build
    if: needs.build.outputs.is_prerelease == 'true'
    runs-on: ubuntu-latest
    environment: testpypi
    permissions:
      id-token: write
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/
      - name: Publish to TestPyPI
        uses: pypa/gh-action-pypi-publish@release/v1
        with:
          repository-url: https://test.pypi.org/legacy/

  pypi:
    needs: build
    if: needs.build.outputs.is_prerelease == 'false'
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/
      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1

  github-release:
    needs: [build, testpypi, pypi]
    if: always() && needs.build.result == 'success' && (needs.testpypi.result == 'success' || needs.pypi.result == 'success')
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/
      - name: Create GitHub Release
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          if [[ "${{ needs.build.outputs.is_prerelease }}" == "true" ]]; then
            gh release create "${GITHUB_REF_NAME}" dist/* --prerelease --generate-notes --title "${GITHUB_REF_NAME}"
          else
            gh release create "${GITHUB_REF_NAME}" dist/* --generate-notes --title "${GITHUB_REF_NAME}"
          fi
```

- [ ] **Step 3: Validate both workflows parse and are structured correctly**

```bash
uv run python - <<'PY'
import yaml
ci = yaml.safe_load(open(".github/workflows/ci.yml"))
rel = yaml.safe_load(open(".github/workflows/release.yml"))
# ci.yml — note: YAML parses bare `on:` as the boolean True key
on = ci.get("on", ci.get(True))
assert on["push"]["branches"] == ["main", "dev"], on
assert "pull_request" in on, on
assert set(ci["jobs"]) == {"test", "docs", "integration"}, sorted(ci["jobs"])
# release.yml
ron = rel.get("on", rel.get(True))
assert ron["push"]["tags"] == ["v*"], ron
assert set(rel["jobs"]) == {"build", "testpypi", "pypi", "github-release"}, sorted(rel["jobs"])
assert rel["jobs"]["testpypi"]["environment"] == "testpypi"
assert rel["jobs"]["pypi"]["environment"] == "pypi"
assert rel["jobs"]["testpypi"]["permissions"]["id-token"] == "write"
assert rel["jobs"]["pypi"]["permissions"]["id-token"] == "write"
# no stored publish secrets anywhere
assert "secret" not in open(".github/workflows/release.yml").read().lower()
print("workflows OK")
PY
```
Expected: `workflows OK`.

- [ ] **Step 4: Validate the tag-classification logic directly**

```bash
classify() { [[ "$1" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] && echo "final" || echo "prerelease"; }
for t in v1.2.3 v0.10.0 v1.2.3rc1 v1.2.3a1 v1.2.3b2 v1.2.3.dev4; do echo "$t -> $(classify $t)"; done
```
Expected: `v1.2.3 -> final`, `v0.10.0 -> final`, and all of `v1.2.3rc1 / v1.2.3a1 / v1.2.3b2 / v1.2.3.dev4 -> prerelease`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml .github/workflows/release.yml
git commit -m "ci: PR-gate on dev + tag-driven TestPyPI/PyPI release via trusted publishing"
```

Note: the workflows cannot be exercised end-to-end without pushing tags / registering Trusted Publishers. Real publishing is validated out-of-band on the first prerelease tag, per the ops docs (Task 3). Do not push any tag as part of this task.

---

## Task 3: Operations docs — release runbook, nav, contributing

**Files:**
- Create: `docs/operations/releasing.md`
- Modify: `mkdocs.yml` (add an Operations nav group)
- Modify: `docs/contributing.md`

**Interfaces:**
- Consumes: the branch model, `release.yml`, and required-check name from Tasks 1-2.

- [ ] **Step 1: Write the release runbook**

Create `docs/operations/releasing.md` with these sections (write real, complete content — no placeholders):

1. **Branch model** — `dev` is the default/integration branch; `main` is stable/released (mkdocs deploys from `main`). Feature branches → PR into `dev`; promote by PR/merge `dev` → `main`, then tag on `main`.
2. **CI gate** — every PR runs `test` + `integration` (SQL Server e2e). Merging into `dev`/`main` is blocked until `integration` passes (required status check). Warn: the required check is named `integration`; renaming the CI job disarms the gate.
3. **Releasing (test → prod)** — the version is the git tag (`hatch-vcs`). Tag conventions and what each does:
   - `git tag v0.2.0rc1 && git push origin v0.2.0rc1` → builds and publishes to **TestPyPI** + a GitHub **prerelease**.
   - `git tag v0.2.0 && git push origin v0.2.0` → publishes to **PyPI** (after the `pypi` environment approval) + a GitHub **release**.
   Include an "install to verify" line: `pip install -i https://test.pypi.org/simple/ taxi-seed==0.2.0rc1`.
4. **One-time setup checklist** (the manual, out-of-band steps):
   - **Create/push `dev`, set it as the default branch** (GitHub → Settings → General → Default branch).
   - **PyPI + TestPyPI Trusted Publishers** — register a *pending publisher* on each (works before the first upload): owner `andrekamman`, repo `taxi`, workflow `release.yml`, environment `pypi` (on PyPI) / `testpypi` (on TestPyPI), project name `taxi-seed`.
   - **GitHub Environments** — create `testpypi` (no protection) and `pypi` (add yourself as a **required reviewer** — this is the PROD approval gate). GitHub → Settings → Environments.
   - **Branch protection on `dev`** — require the `integration` check. Provide this exact command:
     ```bash
     gh api -X PUT repos/andrekamman/taxi/branches/dev/protection --input - <<'JSON'
     {
       "required_status_checks": { "strict": true, "contexts": ["integration"] },
       "enforce_admins": true,
       "required_pull_request_reviews": null,
       "restrictions": null
     }
     JSON
     ```
     Note: the `integration` check must have run at least once (e.g. via one PR) before GitHub lists it; the API accepts the name regardless.
5. **Optional — custom docs domain** — the user owns `taxi-seed.com`. To point the docs site at it: add a `docs/CNAME` file containing `taxi-seed.com`, configure the domain in GitHub → Settings → Pages, and add the DNS records. (Not required; noted for later.)

- [ ] **Step 2: Add the Operations nav group**

In `mkdocs.yml`, add an `Operations` section to `nav` (place it after `Reference`), pointing at the new page:

```yaml
  - Operations:
    - Releasing: operations/releasing.md
```
Match the existing nav's indentation/style exactly.

- [ ] **Step 3: Update the contributing guide**

In `docs/contributing.md`, add a short "Branching & releases" note stating: branch from `dev`, open PRs **into `dev`** (not `main`), the `integration` check must pass before merge, and releases happen by tagging (see Operations → Releasing). Keep it brief and consistent with the page's existing tone; do not restructure the file.

- [ ] **Step 4: Validate docs build cleanly**

Run: `uv run --extra docs mkdocs build --strict`
Expected: builds with no warnings/errors (this catches a bad nav entry, a missing page, or a broken internal link). Then remove the build output: `rm -rf site`.

- [ ] **Step 5: Commit**

```bash
git add docs/operations/releasing.md mkdocs.yml docs/contributing.md
git commit -m "docs(ops): release runbook + dev/main branch model + PyPI setup checklist"
```

---

## Self-Review

**1. Spec coverage:**
- Branch topology `dev`+`main` → documented in Task 3 (runbook) + CI trigger in Task 2; the actual branch creation/default-set is a manual step (Task 3 checklist), correctly out of code scope.
- CI PR gate + required `integration` check → Task 2 (trigger) + Task 3 (branch-protection command).
- Release workflow: prerelease→TestPyPI, final→PyPI (gated env) + GitHub Release, OIDC no-tokens → Task 2 (`release.yml`).
- hatch-vcs tag-driven versioning + `taxi-seed` rename + metadata → Task 1.
- Manual out-of-band setup (accounts, trusted publishers, environments, branch protection, default branch) → Task 3 checklist.
- Ops docs page + contributing + nav → Task 3.
- Docs sweep (sub-project 2) correctly excluded.
No spec requirement is unaddressed.

**2. Placeholder scan:** No TBD/TODO. Task 3 Step 1 specifies section-by-section content rather than verbatim prose — acceptable for a docs page (the facts to include are all enumerated with exact values/commands). All code/config steps carry complete, exact content.

**3. Type/name consistency:** `taxi-seed` (dist), `taxi_seed-<v>` (built artifact prefix), job names `test`/`integration`/`docs` (unchanged) and `build`/`testpypi`/`pypi`/`github-release` (release), environment names `testpypi`/`pypi`, required-check name `integration`, and tag conventions (`vX.Y.Z` final; `rc/a/b/.dev` prerelease) are used identically across all three tasks and match the Global Constraints.

**Known verification points for the implementer** (flagged inline, non-blocking): the `license`/`license-files` PEP 639 form vs the older `{file=…}` form depending on hatchling version; and that `uv build` at a tag yields the expected `taxi_seed-<version>` filenames.
