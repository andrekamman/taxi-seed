# Releasing

This page is the operator's runbook for the branch model, the CI gate that protects it, and the tag-driven release flow that publishes `taxi-seed` to PyPI. If you are looking for how to build and test locally as a contributor, see [Contributing](../contributing.md) instead — this page is about promoting code from a merged PR to a published package.

## Branch model

- **`dev`** is the default branch and the integration branch. Feature branches are cut from `dev`, and pull requests target `dev` — not `main`.
- **`main`** is the stable, released branch. Nothing lands on `main` directly; it only moves forward by merging `dev` into it once `dev` is in a state worth shipping.
- The docs site deploys from `main` (see the `docs` job in `.github/workflows/ci.yml`, gated on `push` to `refs/heads/main`), so `main` is also "what's live on the docs site" at any given moment.
- Releases are tagged **on `main`**, after the promotion merge — never on `dev` and never on a feature branch. The version published to PyPI is whatever `main` looked like when the tag was pushed.

The promotion path in order: feature branch → PR into `dev` → `dev` passes CI → PR/merge `dev` into `main` → tag `main` → release workflow runs.

## The CI gate

Every pull request runs two jobs defined in `.github/workflows/ci.yml`:

- **`test`** — unit tests across the Python version matrix (3.12, 3.13).
- **`integration`** — end-to-end tests against a real SQL Server container (loader integration + pipeline e2e), on Python 3.13.

Merging into `dev` or `main` is blocked until `integration` passes — it is configured as a **required status check** (see the one-time setup below for the exact command that wires this up).

!!! warning "The required check is named `integration`"
    Branch protection matches on the job name, not on what the job does. If the `integration` job in `.github/workflows/ci.yml` is ever renamed, the branch-protection rule silently stops matching anything — GitHub will show the merge button as available even though no equivalent check ran. Renaming that job and updating branch protection have to happen in the same PR.

## Releasing (test → prod)

The published version is derived entirely from the git tag via `hatch-vcs` — there is no version string to bump in `pyproject.toml`. Pushing a tag is the release action.

`.github/workflows/release.yml` triggers on any tag matching `v*` and classifies it as prerelease or final by shape:

| Tag shape | Example | Classified as | Destination |
|---|---|---|---|
| `vX.Y.Z` | `v0.2.0` | final | PyPI + GitHub Release |
| anything else matching `v*` | `v0.2.0rc1`, `v0.2.0a1`, `v0.2.0b1`, `v0.2.0.dev1` | prerelease | TestPyPI + GitHub prerelease |

Both paths run the same `build` job first (checkout with full history/tags for `hatch-vcs`, `uv sync --extra test`, `pytest -q` as a guard, `uv build`), then fan out to `testpypi` or `pypi` based on the classification, then create the GitHub release/prerelease from the built artifacts. Publishing uses OIDC Trusted Publishing (`id-token: write`) — there are no PyPI tokens stored anywhere in the repo or its secrets.

### Cut a prerelease (TestPyPI)

From an up-to-date `main`:

```bash
git tag v0.2.0rc1
git push origin v0.2.0rc1
```

This runs the `build` job, then the `testpypi` job (environment `testpypi`, no approval required), then creates a GitHub prerelease. Watch it in the Actions tab or with:

```bash
gh run watch
```

Verify the published artifact installs before promoting further:

```bash
pip install -i https://test.pypi.org/simple/ taxi-seed==0.2.0rc1
```

### Cut a final release (PyPI)

Once the prerelease has been verified, tag the same commit (or a later one on `main`) with the final version:

```bash
git tag v0.2.0
git push origin v0.2.0
```

This runs `build`, then the `pypi` job — which waits for approval in the `pypi` GitHub Environment (the required reviewer configured during one-time setup) before it publishes — then creates the GitHub Release. Approve the deployment from the Actions run page (or `gh run watch`, which surfaces the pending-approval state) when you're ready for it to go live.

## One-time setup checklist

These are manual, out-of-band steps — they only need to happen once per repo, not per release.

1. **Create and default `dev`.**

    ```bash
    git checkout -b dev main
    git push -u origin dev
    ```

    Then in GitHub: **Settings → General → Default branch** → switch it to `dev`. New PRs and `git clone` will target `dev` from then on.

2. **Register PyPI + TestPyPI Trusted Publishers.** Both PyPI and TestPyPI support registering a *pending* publisher before the project has ever been uploaded — this is what lets the very first `pypi`/`testpypi` job authenticate via OIDC with nothing to configure on the GitHub side beyond the environment name. On each of [pypi.org](https://pypi.org/manage/account/publishing/) and [test.pypi.org](https://test.pypi.org/manage/account/publishing/), add a pending publisher with:

    | Field | Value |
    |---|---|
    | PyPI project name | `taxi-seed` |
    | Owner | `andrekamman` |
    | Repository name | `taxi` |
    | Workflow name | `release.yml` |
    | Environment name | `pypi` (on pypi.org) / `testpypi` (on test.pypi.org) |

3. **Create the GitHub Environments.** **Settings → Environments**:
    - `testpypi` — no protection rules. The prerelease flow should be fast and unattended.
    - `pypi` — add yourself (or the release approver) as a **required reviewer**. This is the actual production approval gate: the `pypi` job in `release.yml` will not run until someone approves the deployment.

4. **Turn on branch protection for `dev`**, requiring the `integration` check:

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

    Note: GitHub only lists a check name as selectable in the Settings UI after it has run at least once on the repo (e.g. via one open PR) — but the API above accepts the `integration` name regardless of whether the UI has seen it yet, so this can be run before or after that first PR.

## Optional — custom docs domain

The docs site can be pointed at `taxi-seed.com` instead of the default `andrekamman.github.io/taxi/` URL. This is not required for anything above and can be done at any time:

1. Add a `docs/CNAME` file to the repo containing exactly:

    ```
    taxi-seed.com
    ```

    `mkdocs gh-deploy` copies everything under `docs/` into the published site, so this file will land at the root of `gh-pages` automatically on the next docs deploy.
2. In GitHub: **Settings → Pages**, set the custom domain to `taxi-seed.com` and enable "Enforce HTTPS" once the certificate has provisioned.
3. Add the DNS records at your registrar: either an `ALIAS`/`ANAME` (or the four GitHub Pages `A` records) for the apex domain, or a `CNAME` record if serving from a subdomain — pointing at `andrekamman.github.io`.
