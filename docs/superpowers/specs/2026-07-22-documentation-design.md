# Documentation Design

**Date:** 2026-07-22
**Status:** Approved, ready for implementation planning
**Sub-project of:** the four-part expansion (this doubles as sub-project #4 "CI testing pipeline" — the docs-build CI workflow uses the same GitHub Actions setup that tests will).

## Motivation

The current documentation reads as a summary for readers who already know what they're looking at. Total footprint is 222 lines across 5 READMEs. The top-level README is 23 lines and its installation section says only `uv sync`, which is not enough to actually get a working setup (Python 3.12+, uv itself, curl for the downloader, Go 1.22+ for the K6 build, a SQL Server target for the load-tester, sensible disk provisioning for the raw parquet mirror, and OS-specific gotchas like the WSL VHDX warning). For a repo being evaluated by data engineers considering adoption, this is inadequate.

This spec turns the docs into something a data engineer can use to decide "is this worth trying?" in five minutes, "how do I get it working end-to-end?" in fifteen, and "how do I extend it or integrate it?" as a reference. The output is a Material for MkDocs site published to GitHub Pages.

## Non-goals

- Sweeping code refactoring or renaming. **One targeted downloader change is in scope** (below); nothing else.
- API-reference documentation auto-generated from docstrings. The audience wants task-oriented guides, not autodoc.
- Multi-language localization.
- A custom domain.
- Documenting sub-projects that haven't been built yet (SQL Server loader, orchestrator, dev/test/prod promotion). Placeholder pages linking to their spec docs are acceptable; full content is not required.
- Writing a "history of the project" narrative. The commit log and design specs already tell that story for readers who want it.

## In-scope code change: `--recent` semantics + per-type support

The current `--recent N` flag has two shortcomings that the doc-writing work surfaced:

1. **Fixed to all four types.** No way to say "just yellow" — a common request since many users only care about one dataset. Full-catch-up mode has the same limitation.
2. **Ambiguous meaning of "N".** Today the counter increments both when an existing file is found on disk and when a new file downloads successfully. It also uses a fixed `max_lookback_months` cap. A user running `--recent 3` on a fresh checkout could end up with two files (if only two are actually published yet) or three files (including up to two months of unpublished-but-eventually-there gaps that the walker skipped past). The behavior isn't consistent, especially for the "keep me caught up on a schedule" use case.

**New semantics:**

Usage: `./downloader/download_taxi_data.sh --recent [N] [TYPE]`

- Both `N` and `TYPE` are optional, order-sensitive (N first, then TYPE). Defaults: N=3, TYPE=all four types.
- `TYPE` is one of `yellow`, `green`, `fhv`, `fhvhv`.
- Argument parsing: after `--recent`, peek at the next arg: if it's numeric, treat as N; then peek again: if it's a known type, treat as TYPE; else pass through.
- **Walker behavior** (per data type):
  - Start at previous month (current − 1). Walk backward one month at a time.
  - **Remote file successfully downloaded** → increment counter.
  - **Remote responds "not published yet"** (403 AccessDenied) → skip, don't count, continue walking back. This handles the common case of "current month not published yet, walk back one".
  - **Local file already exists at that path** → STOP walking. The assumption is that the user has previously downloaded up to some earlier point, so anything older is already local. This gives clean incremental catch-up semantics: run it on a cron and each execution fetches only the new months since last run.
  - Stop conditions: counter reaches N, OR local file encountered, OR `max_lookback_months` reached (safety cap for genuinely-empty-series edge case).

**Example scenarios** (using yellow, which publishes with ~2 month lag; suppose today is 2026-07-22):

- Fresh checkout, `--recent 3 yellow`: tries 2026-06 (not published), 2026-05 (downloads), 2026-04 (downloads), 2026-03 (downloads). Result: 3 files.
- Same setup a month later, `--recent 3 yellow`: tries 2026-07 (not published), 2026-06 (downloads new), 2026-05 (already local → stop). Result: 1 new file. This is the keep-up-to-date incremental case.
- User already caught up, `--recent 3 yellow`: tries 2026-06 (not published), 2026-05 (already local → stop). Result: 0 new files, exits cleanly.

**Sequence in the rollout:** this code change is commit #0 in the plan (before the MkDocs scaffolding), so that Getting Started and the Downloader guide can document the per-type flag as an actually-working feature. The change is small (~30 lines in `downloader/download_taxi_data.sh`) and self-contained.

## Audience

**Primary reader:** data engineers evaluating this repo for their organization. They want:

- Solid technical depth (they know parquet, DuckDB, and basic SQL Server).
- Concrete install/ops details, not "run `uv sync`".
- Why-not-alternatives (comparison to `toddwschneider/nyc-taxi-data`, DuckDB `httpfs` direct queries, HuggingFace mirrors).
- Integration surface — "can I pipe this into my stack?".

Secondary readers are analysts wanting quick data access and contributors wanting to understand the architecture. The site is layered so each finds their track: the top of the home page hooks the analyst quickly, the guides go deeper for the engineer, and the Design Specs section satisfies the contributor.

## Site structure

Published at `https://andrekamman.github.io/taxi/`. Navigation:

```
Home                              docs/index.md
Getting Started                   docs/getting-started.md
Guides
  Downloader                      docs/guides/downloader.md
  Schema Drift                    docs/guides/schema-drift.md
  Normalize                       docs/guides/normalize.md
  K6 Load Test                    docs/guides/k6-loadtest.md
Cookbook                          docs/cookbook.md
Architecture                      docs/architecture.md
Reference
  Configuration                   docs/reference/configuration.md
  Exit codes                      docs/reference/exit-codes.md
Design Specs                      docs/superpowers/specs/… (existing files surfaced in nav)
Contributing                      docs/contributing.md
```

**Top-level `README.md`** shrinks to a concise GitHub landing page (~80 lines): what this is, three-bullet differentiators, badges (CI status, license, docs link), a big "→ Full documentation at …" pointer, the acknowledgments block. Not a duplicate of the site.

**Per-component READMEs** (`downloader/`, `schema-drift/`, `k6-loadtest/`, `normalize/`) collapse to one-paragraph pointers: purpose + "See [full guide](https://andrekamman.github.io/taxi/guides/<component>)". This avoids doc drift where the same content lives in two places.

## Per-page content plan

### `docs/index.md` (Home) — ~150 lines

Hooks the evaluator in 2 minutes.

- Hero paragraph: what this is, one sentence.
- Three-bullet differentiators (WAF-aware downloader, human-in-the-loop normalizer, K6 load-test bundle).
- Comparison snapshot table: rows for `toddwschneider/nyc-taxi-data`, DuckDB `httpfs` direct query, and this repo. Columns: primary use case, resumable, WAF-aware, schema handling, target audience.
- Quick Start block: 3 shell commands to first parquet (`git clone`, `uv sync`, `./downloader/download_taxi_data.sh --recent 3`).
- Requirements at a glance.
- "Where to next" grid: Getting Started (recommended path), or skip directly to a specific tool's guide.
- Acknowledgments (Todd Schneider link).

### `docs/getting-started.md` — ~300 lines, ~10 minute tutorial

End-to-end path from a clean laptop to normalized parquet.

- Prerequisites: Python 3.12+, uv (link to install), curl, ~300 MB free disk for the tutorial. Platform notes (macOS, Linux, Windows via Git Bash / WSL with the VHDX warning explained).
- Clone + install (`uv sync --extra test` to also get pytest, since we recommend running tests to verify install).
- Verify install: `uv run --extra test pytest -q` shows a green suite.
- Download the three most recent months of yellow: `./downloader/download_taxi_data.sh --recent 3 yellow`. Estimated download: ≈200 MB; time: 1–2 minutes on a residential connection. Explain that `TYPE` is optional (omitting it downloads all four types, which is ≈1.8 GB — mostly FHVHV — and takes longer). Cross-link to the Downloader guide for full-history sizing.
- Look at what you got: `find raw/ -name '*.parquet'`, `du -sh raw/`, `uv run python -c "import duckdb; print(duckdb.execute(\"DESCRIBE SELECT * FROM 'raw/yellow/2025/yellow_tripdata_2025-11.parquet'\").fetchall())"`.
- Optionally bootstrap a normalize mapping: `uv run normalize yellow` (first run — the tool auto-bootstraps). Show what the emitted YAML looks like. Explain the SUGGESTED / TODO lines. Uncomment / fill in a minimal mapping. Re-run: it produces `raw-normalized/yellow/…`.
- Optionally run schema-drift: `uv run schema-drift --types yellow`. Show a snippet of the report.
- "Where to next" links to per-tool guides.

### `docs/guides/downloader.md` — ~250 lines

- What it does + why it exists (2 paragraphs).
- Prerequisites: bash 4+, curl, disk sized to intended footprint (see table below).

**Disk sizing table** (approximate, based on TLC file sizes as of mid-2026):

| Scope                       | Disk needed | Time (residential connection) |
|-----------------------------|-------------|-------------------------------|
| `--recent 3 yellow`         | ≈200 MB     | 1–2 min                       |
| `--recent 3 green`          | ≈15 MB      | seconds                       |
| `--recent 3 fhv`            | ≈120 MB     | 1 min                         |
| `--recent 3 fhvhv`          | ≈1.5 GB     | 5–10 min                      |
| `--recent 3` (all four)     | ≈1.8 GB     | 8–15 min                      |
| Full history yellow only    | ≈30 GB      | 2–4 hours                     |
| Full history green only     | ≈1.2 GB     | 5–10 min                      |
| Full history fhv only       | ≈6 GB       | 30–60 min                     |
| Full history fhvhv only     | ≈37 GB      | 2–4 hours                     |
| **Full history (all four)** | **≈75 GB**  | **6–10 hours**                |

TLC adds roughly 2 GB/month across all types (mostly FHVHV). Plan capacity accordingly if you're building a long-lived mirror.
- Install: nothing (bash script).
- Windows note: Git for Windows; the script uses only curl and standard POSIX tools so no extra install is needed once Git Bash is present.
- **What makes it different** section: WAF classifier explained with a mermaid decision-tree diagram (200/206→ok; 403+XML AccessDenied→notfound; 403+CloudFront HTML→ratelimit; 429/503→ratelimit); exponential backoff ladder (5 → 15 → 60 min, resets on success); boundary auto-termination; PAR1 head+tail validation.
- Basic usage: `--recent N`, no-arg (full catch-up).
- Config: `OUTPUT_DIR` env var — when to use it, WSL example.
- WSL disk-usage warning explained (VHDX growth on C: drive; interactive `[y/N]` prompt; non-interactive proceed).
- Output layout: `raw/<type>/<year>/<type>_tripdata_YYYY-MM.parquet`.
- **Alternatives table**: `toddwschneider/nyc-taxi-data`, DuckDB `httpfs`, HuggingFace mirrors. Columns: what it is, when to use it, when NOT to use it.
- Troubleshooting: "I'm being rate-limited constantly"; "Downloads keep failing with 403"; "The script says my WSL VHDX will grow — what does that mean?"; "I need to point downloads at S3 instead of local disk" (short answer: not supported, PR welcome).

### `docs/guides/schema-drift.md` — ~200 lines

- What it does + why.
- Prerequisites: uv-installed `schema-drift` command; a parquet family (either the downloader's `raw/` mirror or any directory of TLC-shaped parquet).
- Install: `uv sync`.
- Basic usage: `uv run schema-drift` (default: analyzes all four types in `raw/`).
- The three modes: default taxi mode (semantic-category-aware, uses NYC-TLC abbreviation dictionary), `--generic` (data-driven only, no domain knowledge, results require human review), `--verify-data` (sample data to verify low-confidence renames). Which to use when.
- **Reading the report**: annotated example of an actual `schema-drift yellow` output with each block explained (INITIAL SCHEMA, CHANGES, RENAMES with confidence + verification status, CROSS-TYPE SUMMARY).
- **How rename detection works**: semantic categories (pickup vs dropoff vs coordinate vs location_id), longest-common-subsequence similarity, token-based similarity with abbreviation expansion, data verification via column-stats comparison. Reasonable pseudocode / mermaid.
- Programmatic API: importing `schema_drift.analyze.analyze_data_type()` from a notebook, with a small worked example.
- Known limits: heuristic, not proof; needs human review; particularly weak when two columns are statistically indistinguishable (e.g., two timestamp columns with identical ranges).

### `docs/guides/normalize.md` — ~350 lines (the deepest guide)

- What it does + why. Explains the "data loss is a first-class error, not a warning" philosophy up front.
- Prerequisites: `raw/` mirror; `uv sync`.
- Install: `uv sync`.
- **The three-state model** with a mermaid state diagram: first run (no mapping → auto-bootstrap → exit 3) → user edits YAML → subsequent run (unresolved → auto-amend → exit 1, OR complete → normalize → exit 0).
- Mapping YAML structure: brief inline, link to `docs/reference/configuration.md` for the exhaustive field-by-field reference.
- **Bootstrap-and-amend semantics** explained: what "bootstrap" means (SUGGESTED renames from schema-drift, TODO ack items for lossy casts and data loss), what "amend" means (preserves existing entries, appends new SUGGESTED/TODO), the operational scenario (TLC ships new drift → orchestrator's scheduled run detects unresolved → amend adds new items → human reviews).
- The `ack_date`-is-the-only-required-field convention. `ack_by` and `reason` are optional but recommended.
- Exit codes table: 0 (success), 1 (unresolved), 2 (config error), 3 (first-run bootstrap needs review).
- The `--sample` flag: what it does, when to reduce below 100%.
- **Full worked example 1**: first-time yellow. Show the actual bootstrap output for real yellow parquet, the SUGGESTED lines schema-drift produces, what a human's edits look like, the resulting normalize run.
- **Full worked example 2**: TLC ships new drift months later; scheduled orchestrator run (foreshadowed) detects unresolved; amend adds new items; human reviews the diff in the mapping file.

### `docs/guides/k6-loadtest.md` — ~250 lines

- What it does + why.
- Prerequisites: Go 1.22+ for the custom K6 build (`xk6-sql` + SQL Server driver), a SQL Server instance to test against (Docker snippet inline; adjustments for cloud SQL Server).
- Full setup: build custom K6, copy `config.sample.yaml → config.yaml`, edit, run preprocess, apply CREATE TABLE scripts, run the K6 test.
- Both source modes with a decision guide (parquet: real data, real ratios, real distribution / synthetic: instant startup, unbounded scale, no data files needed).
- Config reference (brief; full YAML schema in `docs/reference/configuration.md`).
- Sample docker-compose for the SQL Server test target.
- Interpreting K6 output: what the built-in K6 metrics mean; SQL-specific metrics from `xk6-sql`; how to establish a baseline and detect regression.

### `docs/cookbook.md` — 5–8 recipes, ~50 lines each

- **Nightly refresh via cron**: full command, systemd timer / launchd plist snippets, logging.
- **DuckDB `httpfs` — no local mirror**: query TLC parquet directly from CloudFront; when this is enough and you don't need the downloader at all.
- **Side-by-side comparison of multiple TLC years**: DuckDB query examples that join yellow and green, or slice across schema versions post-normalization.
- **Load-testing the normalizer's output**: chaining `normalize` → `k6-preprocess` on `raw-normalized/`.
- **Running behind a corporate proxy**: env vars for curl, uv, gh; how to configure the downloader.
- **Populating a fresh dev SQL Server**: docker-compose, K6 preprocess in parquet mode, running the load test as a bulk-load one-shot.

### `docs/architecture.md` — ~200 lines

- Repo layout with tree diagram.
- The four-tool DAG: mermaid flowchart from `download → analyze → normalize → load`.
- **Core design principles**:
  - WAF-aware retry (why classifying 403 correctly matters).
  - Data loss is an error (why the ack_date convention forces the human to think once).
  - Per-file atomicity (`.tmp.parquet` → atomic rename).
  - Metadata-only checks vs full-scan checks (parquet footer everywhere possible; only precision checks full-scan).
  - Monorepo rationale (shared `taxi_shared` for DuckDB→SQL Server type mapping; simpler ops than three separate repos).
- The `taxi_shared` package's role.
- Testing philosophy: synthetic parquet fixtures built by DuckDB in `conftest.py`; no network or shared filesystem dependencies in tests.

### `docs/reference/configuration.md` — reference-grade, no editorializing

- Mapping YAML (`normalize/mappings/<type>.yaml`): every field, its type, whether required, valid values, default.
- K6 `config.yaml` (`k6-loadtest/config.sample.yaml`): every field, per-mode (parquet vs synthetic).
- Env vars: `OUTPUT_DIR`, `MSSQL_PASSWORD`, `TZ` (if relevant), any others.

### `docs/reference/exit-codes.md` — one table per tool

Each row: exit code → meaning → suggested action.

### `docs/contributing.md` — ~150 lines

- Dev setup (`uv sync --extra test`, `--extra docs`).
- Running the test suite; adding a new test.
- PR checklist (tests green, docs updated if user-facing, ack in commit message).
- Code style (existing informal conventions: single-quote SQL literals via f-strings for now; explicit exit-code semantics; error-first paths in CLI).
- Where design decisions live (link to `docs/superpowers/specs/`).

### `docs/superpowers/specs/`

Existing files, surfaced in the sidebar under "Design Specs" for the contributor audience. No content changes required; the `mkdocs.yml` navigation lists each spec explicitly by filename (rather than auto-discovering) so new specs get an explicit add-to-nav step in their commit — this avoids the sidebar changing shape silently. Same policy for `docs/superpowers/plans/` if ever surfaced.

## Examples strategy

- **Every code block is copy-pasteable and produces the shown output.** No pseudocode. No `<placeholder>` values. If the reader can't verify a snippet worked, the doc has failed. Where an install command doesn't return output, the doc says so ("this command has no output on success — check `echo $?` for exit 0").
- **Real data over synthetic** where feasible. Getting Started uses `--recent 3` (≈500 MB across all types, downloads in a minute or two). Guides use scaled real examples. Only the k6-loadtest guide leans on synthetic mode as an on-ramp.
- **Show real output**, not sanitized ideal output. If `normalize yellow` prints `Detected 4 drift transition(s) — see file header.`, that's what the doc shows.
- **Full-history sections are clearly labeled** with realistic disk + time expectations ("If you want to catch up on all history: `./downloader/download_taxi_data.sh` — this will download ≈75 GB across all four types and take 6–10 hours; or scope to one type with `./downloader/download_taxi_data.sh yellow` for ≈30 GB / 2–4 hours."). No surprise disk usage.

## MkDocs setup

- **Theme**: [Material for MkDocs](https://squidfunk.github.io/mkdocs-material).
- **Features** enabled: `content.code.copy`, `content.tabs.link`, `navigation.tracking`, `navigation.top`, `search.highlight`, `search.share`.
- **Extensions**:
  - `admonition` (callouts)
  - `pymdownx.superfences` (mermaid diagram support via `custom_fences`)
  - `pymdownx.tabbed` with `alternate_style: true` (per-OS install tabs)
  - `pymdownx.highlight` with `linenums: true` (numbered code blocks)
  - `attr_list` + `md_in_html` (badges, custom classes)
  - `footnotes`
- **Diagrams**: mermaid rendered inline via `pymdownx.superfences` + `custom_fences` config; used for the tool DAG, WAF-classifier decision tree, and normalize state model.

## Dependencies

Add a `docs` optional-dependencies group to `pyproject.toml`:

```toml
[project.optional-dependencies]
test = ["pytest>=8.0"]
docs = [
    "mkdocs>=1.6",
    "mkdocs-material>=9.5",
    "pymdown-extensions>=10.7",
]
```

Local dev: `uv run --extra docs mkdocs serve` (auto-reload on port 8000).

## CI

Two workflows:

**`.github/workflows/test.yml`** — runs on every push and PR:
- Ubuntu-latest.
- Installs uv, syncs with `--extra test`.
- Runs `uv run --extra test pytest -q`.
- Matrix over Python 3.12 and 3.13.

**`.github/workflows/docs.yml`** — runs on push to `main` only:
- Waits for `test.yml` to succeed (via `needs:` / job dependency).
- Installs uv, syncs with `--extra docs`.
- Runs `uv run --extra docs mkdocs gh-deploy --force`.
- Deploys the built site to the `gh-pages` branch, which GitHub Pages serves.

## GitHub Pages

- Enable Pages via `gh api -X POST repos/andrekamman/taxi/pages -f source[branch]=gh-pages -f source[path]=/` after the first successful `mkdocs gh-deploy` creates the `gh-pages` branch. Alternatively, enable via the GitHub UI: Settings → Pages → Source: Deploy from a branch → Branch: `gh-pages` → `/ (root)`.
- Site URL: `https://andrekamman.github.io/taxi/`.
- Add the site URL to the repo's `About` sidebar (via `gh repo edit andrekamman/taxi --homepage https://andrekamman.github.io/taxi/`) so it's prominent on the GitHub landing page.
- These are one-time manual steps; not part of any commit.

## Rollout plan

Split into eleven commits, each independently reviewable and each landing a coherent slice. The site improves incrementally as commits land after #1.

0. **Downloader semantic change**: `--recent [N] [TYPE]` argument parsing, walker stops on local-file-encountered instead of counting it. Update `download_recent_type` accordingly. Adjust `download_full_type` invocation to also accept an optional TYPE argument (`./downloader/download_taxi_data.sh yellow` becomes valid; without argument, still walks all four). Update the top-level help output. Verify with a quick smoke run.
1. MkDocs scaffolding: `pyproject.toml` `docs` extra, `mkdocs.yml`, `docs/index.md`, `.github/workflows/{test,docs}.yml`. Site is live and shows the home page.
2. `docs/getting-started.md`.
3. `docs/guides/downloader.md` + shrink `downloader/README.md` to pointer.
4. `docs/guides/schema-drift.md` + shrink `schema-drift/README.md`.
5. `docs/guides/normalize.md` + shrink `normalize/README.md`.
6. `docs/guides/k6-loadtest.md` + shrink `k6-loadtest/README.md`.
7. `docs/cookbook.md`.
8. `docs/architecture.md`.
9. `docs/reference/configuration.md` + `docs/reference/exit-codes.md`.
10. `docs/contributing.md` + top-level `README.md` rewrite.

## Success criteria

- The MkDocs site is live at `https://andrekamman.github.io/taxi/` and updates automatically on push to `main`.
- The GitHub landing page's `About` sidebar links to the site.
- A data engineer visiting the site can go from "I've never heard of this" to "I have a normalized parquet dataset locally" in under 30 minutes by following Getting Started.
- Every code block in every page is copy-pasteable and produces the shown output (spot-check during implementation).
- Every per-component README is ≤ 20 lines and points at its guide.
- `docs/superpowers/specs/` files appear in the site nav under "Design Specs" (no content changes).
- CI test workflow runs on every PR; docs workflow runs on push to `main` and deploys automatically.

## Operational notes

- The local checkout at `/Users/andre/git/taxi/` points at the private `taxi-dev` remote. Docs work must land in the **public** `andrekamman/taxi` repo. Implementation begins with a fresh clone of the public repo into a separate directory (e.g., `/Users/andre/git/taxi-public/`) so we don't accidentally push docs commits into the private repo. The spec + plan themselves can live in either repo; keeping them in `taxi-dev` matches the existing pattern where `docs/superpowers/specs/` accumulates design records.
- Since the public and private repos have divergent commit SHAs (filter-repo rewrite), cherry-picking between them uses `git cherry-pick <sha>` with the source specified explicitly.

## Out of scope

- Auto-generated API reference (docstring → HTML). If needed later, add `mkdocstrings`.
- Search over commit history or issue links.
- CI running on multiple OSes (matrix on Ubuntu + macOS + Windows). Nice-to-have; adds cost and flakiness. Ubuntu-only for now.
- Rewriting the design specs themselves. They're kept as-is and surfaced in the nav.
- Building the SQL Server loader, orchestrator, or dev/test/prod promotion sub-projects. Placeholder mentions in the cookbook / architecture pages are fine; full guides await those sub-projects being built.
- Per-type support in the schema-drift, normalize, and k6-preprocess tools. Only the downloader gets the code change in this sub-project. (Normalize already supports per-type via `normalize <type>`; the other tools have `--types` flags already.)
