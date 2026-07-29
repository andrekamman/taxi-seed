# Documentation Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring every user-facing doc in line with the current codebase (Python downloader, loader/orchestrator tools, k6 removed, repo renamed `taxi`→`taxi-seed`).

**Architecture:** Surgical corrections to existing docs driven by a code-verified audit; two brand-new guides for `loader` and `orchestrator`; k6 purged from the live docs and stashed in a `taxi-lab-handoff/` dir. No code changes. Verification is a strict mkdocs build plus a grep sweep for stale terms.

**Tech Stack:** Markdown, MkDocs Material (`mkdocs build --strict`), `uv`.

## Global Constraints

- Repo name is **`taxi-seed`** (never `taxi`). Clone dir is `taxi-seed`.
- Downloader is a **Python** package `taxi_download`; invoke as `uv run taxi-download …` or `python -m taxi_download.cli …`. The bash script `download_taxi_data.sh` **does not exist**.
- Install is **`uv sync` at the repo root** (single root package). No per-subdirectory installs.
- Console scripts (exact): `normalize`, `schema-drift`, `taxi-curate-mappings`, `taxi-download`, `taxi-load`, `taxi-run`.
- Downloader flags: positional `data_type` (yellow/green/fhv/fhvhv; omit = all), `--recent [N]` (default N=3), `--data-dir DIR` (writes `DIR/raw`, default `.`). **No** `--from`/`--to`, **no** `OUTPUT_DIR` env var. Uses `httpx` (not curl).
- Downloader backoff: per-month retry 30→90→270s capped 3600s, `MAX_RETRIES=4`; full-history walk aborts a type after `MAX_CONSECUTIVE_GIVEUPS=3`. Exit `2` only when a type finished `downloaded==0 and gaveup>0`, else `0`.
- Start dates: yellow `2009-01`, green `2013-08`, fhv `2015-01`, fhvhv `2019-02`.
- `MSSQL_PASSWORD` is read by `taxi-load` and `taxi-run` (required for the load stage).
- k6 is **removed** from this repo (moved to `taxi-lab`). No k6 in any live doc.
- Test-count claims must be **durable** (distinguish fast unit suite from SQL-Server integration/e2e), never a hardcoded number like "83".
- Each task ends by verifying with grep/`mkdocs build --strict` where applicable, then a commit.

---

### Task 1: Fix entry docs (README, index, getting-started)

**Files:**
- Modify: `README.md`
- Modify: `docs/index.md`
- Modify: `docs/getting-started.md`

**Corrections (README.md):**
- L1 title `# taxi` → `# taxi-seed`.
- L7 intro + L19 "Four tools": reframe to the real set — downloader (Python), schema-drift, normalize, loader, orchestrator (+ `shared` lib). Drop k6.
- L9–L11 feature bullets: delete the K6 bullet; keep downloader + normalizer bullets; optionally add a loader/orchestrator bullet.
- L21 downloader "bash CLI" → "Python CLI (`taxi-download`)". L24 remove `k6-loadtest/`; add `loader/` and `orchestrator/` (and `shared/`).
- L32 `cd taxi` → `cd taxi-seed`. L33 `./downloader/download_taxi_data.sh --recent 3 yellow` → `uv sync` then `uv run taxi-download yellow --recent 3`.
- L36 drop "bash + curl — no Python needed"; state all tools need `uv sync`; "other three tools" → schema-drift/normalize/loader/orchestrator.
- L42–L45 Requirements: remove bash/curl and Go/K6; downloader needs Python 3.12+ and uv like everything else.
- L54 guides list: drop "K6 Load Test"; the shipped guides are Downloader/Schema Drift/Normalize/Loader/Orchestrator. L55–L56 cookbook/architecture blurbs: drop load-testing/"four tools".
- L70 Acknowledgments: "downloader shell script" → "downloader" (attribution otherwise valid).

**Corrections (docs/index.md):** same class of fixes — L1 title, L3–L17 intro (drop k6, add loader/orchestrator, "shell script"→Python), L42–L50 delete K6 bullet, L52–L62 "How it compares" table (drop Load-testing row; install-effort cell → just `uv sync`), L68 `cd taxi-seed`, L69 quick-start command, L73/L77 install claim, L78/L80 requirements, L88–L95 guides/cookbook pointers, L99–L105 acknowledgments.

**Corrections (docs/getting-started.md):**
- L11 prereqs: remove `curl`; keep Python 3.12+ and uv.
- L16 & L90 WSL: replace `OUTPUT_DIR=/mnt/c/...` with `uv run taxi-download --data-dir /mnt/c/Users/$USER/taxi-data yellow --recent 3`.
- L22 `cd taxi-seed`. L52 command → `uv run taxi-download yellow --recent 3`.
- L57–L81 expected-output block: replace old bash output with the real Python CLI output: an optional leading `cleaned N corrupt parquet file(s)` line (only if any removed), then `yellow: downloaded 3, gave up on 0`, then `total downloaded: 3`. No "Generating URL list", "Files saved to", or per-file lines.
- L85 remove the `--from`/`--to` sentence; describe the real options (`--recent N` vs default full-history; `--data-dir` for location).
- L249 "four-tool DAG" → correct tool set (align with architecture.md wording from Task 5).
- Leave correct sections intact: DuckDB peek, `schema-drift --types yellow`, `normalize yellow`, and the `raw/…` / `raw-normalized/…` paths.

- [ ] **Step 1:** Apply the README.md corrections above.
- [ ] **Step 2:** Apply the docs/index.md corrections.
- [ ] **Step 3:** Apply the docs/getting-started.md corrections.
- [ ] **Step 4: Verify** no stale terms remain in these three files:
  `grep -nE 'download_taxi_data\.sh|OUTPUT_DIR|k6|K6|cd taxi\b|four tools|bash \+ curl|--from|--to' README.md docs/index.md docs/getting-started.md` → expect no live/current-usage hits (a historical mention is acceptable only if clearly framed as such; none expected here).
- [ ] **Step 5: Commit** `docs: sync entry docs (README, index, getting-started) to current CLI`.

---

### Task 2: Rewrite docs/guides/downloader.md for the Python CLI

**Files:**
- Modify (near-total rewrite): `docs/guides/downloader.md`
- Reference (read-only, ground truth): `downloader/src/taxi_download/cli.py`, `download.py`, `dates.py`

**Corrections:**
- Intro/throughout: Python CLI `taxi-download` (also `python -m taxi_download.cli`), not a bash script.
- Prerequisites: Python ≥3.12 + uv (`uv sync` at repo root). Remove bash/curl/Git-Bash.
- Install section: `uv sync`; exposes console script `taxi-download`.
- Basic usage: replace all `./downloader/download_taxi_data.sh …` with `taxi-download` forms. Document the arg-ordering gotcha: `--recent` is `nargs="?"`, so `taxi-download --recent yellow` FAILS (`int("yellow")`); use `taxi-download --recent 3 yellow` or `taxi-download yellow --recent`.
- Windows/WSL: replace `OUTPUT_DIR` + interactive `Continue? [y/N]` prompt (neither exists) with `--data-dir /mnt/c/Users/$USER/taxi-data`. Delete the prompt paragraph.
- WAF classifier: full streaming `GET` (no Range/206). `_classify_status`: 403 with body containing `accessdenied`/`nosuchkey` → NOTFOUND; **any other 403 → RATELIMIT**; `httpx.HTTPError` → NETERROR (retried like ratelimit).
- Backoff: per-month capped exponential 30→90→270s (cap 3600s), `MAX_RETRIES=4`; full-history walk aborts a type after `MAX_CONSECUTIVE_GIVEUPS=3` (per type, not process-wide). Exit `2` only when a type finished `downloaded==0 and gaveup>0`. Delete the "5→15→60 minute" ladder.
- Boundary auto-termination: start dates yellow 2009-01, **green 2013-08**, fhv 2015-01, **fhvhv 2019-02** (green/fhvhv start mid-year).
- Recent-mode: `MAX_LOOKBACK=18` months.
- Configuration section: replace all `OUTPUT_DIR=…` with `--data-dir DIR` (output → `DIR/raw`); "uses curl … HTTPS_PROXY" → httpx honors standard proxy env via `trust_env`.
- Cron example: `taxi-download --recent 3`.
- Troubleshooting: remove non-existent log strings ("Rate limit / WAF block detected (backoff #1)", "Pausing for 5 minute(s)"); real output is `"{type}: downloaded X, gave up on Y"` and `"total downloaded: N"`. Writes to `<name>.part` then atomic `os.replace` (not `curl -o`).

- [ ] **Step 1:** Re-read `cli.py`, `download.py`, `dates.py` to confirm current constants before writing.
- [ ] **Step 2:** Rewrite `docs/guides/downloader.md` per the corrections.
- [ ] **Step 3: Verify** `grep -nE 'download_taxi_data\.sh|OUTPUT_DIR|curl|bash 4|5 ?→ ?15|206|--from|--to' docs/guides/downloader.md` → no hits.
- [ ] **Step 4: Commit** `docs(guides): rewrite downloader guide for Python taxi_download`.

---

### Task 3: Fix normalize.md and schema-drift.md guides

**Files:**
- Modify: `docs/guides/normalize.md`
- Modify: `docs/guides/schema-drift.md`
- Reference: `normalize/src/taxi_normalize/{cli.py,bootstrap.py}`, `schema-drift/src/schema_drift/{cli.py,renames.py}`

**Corrections (normalize.md):**
- Add a short `--data-dir` section: reads `<data-dir>/raw/<type>`, writes `<data-dir>/raw-normalized/<type>`, default `.`. Note the mapping path is always `normalize/mappings/<type>.yaml` relative to CWD (not affected by `--data-dir`).
- Exit-code table (code 2): a **missing raw directory is a skip → exit 0** ("no raw files … skipping"), not "missing raw data". Exit 2 = mapping load / target-not-found / bootstrap analysis errors.
- Worked example 2 amend header: `# Generated by …` → `# Amended by …` (only first run says "Generated").
- Scaffold comment text: match the real emitted text — lossy cast block starts `# DETECTED: <col> changed X -> Y.` / `# Set ack_date to accept (ack_by and reason are optional):` and ends `#   ack_date: TODO` (not empty). Data-loss block: `# DETECTED: <col> has non-null data in N file(s),` / `# no rename candidate above the confidence threshold.` / `# Set ack_date to accept the loss …` / `#   ack_date: TODO`.
- SUGGESTED rename comment: ASCII hyphen `- uncomment to accept:` (not em-dash); label `data-verified` only when confidence ≥0.8, else `NOT data-verified - review carefully`.

**Corrections (schema-drift.md):**
- Prerequisites: `uv sync` in the **repo root** (not the `schema-drift/` dir).
- Confidence threshold: default/taxi mode uses **0.7** (name similarity, ×0.7 type-mismatch penalty); the **0.6** cutoff applies to `--generic` data-driven detection. The doc currently conflates them.

- [ ] **Step 1:** Apply normalize.md corrections.
- [ ] **Step 2:** Apply schema-drift.md corrections.
- [ ] **Step 3: Verify** the scaffold examples match code: re-read `bootstrap.py` emit strings and confirm the guide text is identical.
- [ ] **Step 4: Commit** `docs(guides): correct normalize (--data-dir, scaffold text) and schema-drift (install, threshold)`.

---

### Task 4: Write loader and orchestrator guides + wire nav + fix README links

**Files:**
- Create: `docs/guides/loader.md`
- Create: `docs/guides/orchestrator.md`
- Modify: `mkdocs.yml` (nav: add Loader + Orchestrator under Guides)
- Modify: `loader/README.md` (fix broken `guides/loader/` link — now valid once guide exists)
- Modify: `orchestrator/README.md` (fix broken `guides/orchestrator/` link)
- Reference: `loader/src/taxi_loader/{cli.py,load.py,connection.py,manifest.py,reconcile.py}`, `orchestrator/src/taxi_orchestrate/{cli.py,pipeline.py,stages.py,curate.py,report.py}`

**House style (match existing guides):** intro sentence, Prerequisites, Install, Basic usage, then behavior deep-dives, Configuration, Exit codes, Troubleshooting. Verify every command/flag against source.

**loader.md must cover:** purpose (loads `raw-normalized/<type>/<year>/` parquet into SQL Server via DuckDB + `mssql` community extension); prereqs (SQL Server reachable, `MSSQL_PASSWORD` env var); flags (`--data-dir`, `--input-dir` override, `--dry-run`, `--full-refresh`, type positional); one-table-per-year-per-type; idempotent append/skip/truncate-reload (reconcile) behavior; exit codes 0 (success/dry-run/no-input skip) / 2 (identifier/config error, missing `MSSQL_PASSWORD`, connection/provision failure, `TypeMappingError`) / 1 (a type failed mid-load, partial).

**orchestrator.md must cover:** `taxi-run [TYPE]` chains download → normalize → opt-in `--load`; `--recent [N]`, `--data-dir`, `--download-only` (conflicts with `--load` → exit 2), `--load` (requires `MSSQL_PASSWORD` → exit 2 if unset); exit codes 0 clean / 1 needs-review / 2 operational failure (precedence 2>1>0). Also document `taxi-curate-mappings` (auto-accepts detected drift into complete mapping YAMLs + writes an audit report).

- [ ] **Step 1:** Read the loader source; draft `docs/guides/loader.md`.
- [ ] **Step 2:** Read the orchestrator source; draft `docs/guides/orchestrator.md`.
- [ ] **Step 3:** Add `- Loader: guides/loader.md` and `- Orchestrator: guides/orchestrator.md` to `mkdocs.yml` nav under Guides.
- [ ] **Step 4:** Confirm `loader/README.md` and `orchestrator/README.md` guide links now resolve (page slugs `guides/loader/`, `guides/orchestrator/`); adjust link text if needed.
- [ ] **Step 5: Verify** `uv run --extra docs mkdocs build --strict` builds with the two new pages in nav and no "not in nav" / broken-link warnings.
- [ ] **Step 6: Commit** `docs(guides): add loader and orchestrator guides + nav`.

---

### Task 5: Rewrite architecture.md and cookbook.md

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/cookbook.md`
- Reference: component CLIs, `pyproject.toml`, `.github/workflows/ci.yml`, `scripts/e2e-smoke.sh`

**Corrections (architecture.md):**
- L3 "four tools" → 5 tools + shared lib; downloader is Python; add loader (DuckDB→SQL Server) + orchestrator (chains stages via `taxi-run`); drop k6.
- Table: downloader "bash + curl" → "Python + httpx"; delete k6 row; add loader + orchestrator rows.
- Repo root `taxi/` → `taxi-seed/`; `downloader/` tree → `downloader/src/taxi_download/`; remove `k6-loadtest/` tree; add `loader/src/taxi_loader/`, `orchestrator/src/taxi_orchestrate/` (+ `curate.py`); add `scripts/`.
- "manages all four Python packages" → six packages.
- Mermaid DAG: downloader=Python; replace k6 nodes with `loader (taxi-load) → SQL Server`; show `taxi-run` driving download→normalize→load.
- `taxi_shared` prose: used by loader now (not "k6 today / planned loader tomorrow"); `sql_generator.py` consumer = loader.
- Test layout: replace `tests/k6_loadtest/` with `tests/taxi_loader/`, `tests/taxi_orchestrate/`, `tests/e2e/`, `tests/downloader/`.
- Test-count claim: durable phrasing (fast unit suite in seconds; integration/e2e need a SQL Server container) — no hardcoded number.
- **"What's not built yet":** remove SQL Server loader and Orchestrator (both built now); keep only genuinely-future items (e.g. dev/test/prod promotion if a spec still lists it). Note CI now has `test`, `docs`, `integration` jobs.

**Corrections (cookbook.md):**
- L3 "four tools … K6 load test" → downloader/schema-drift/normalize/loader orchestrated by `taxi-run`.
- All `./downloader/download_taxi_data.sh …` → `uv run taxi-download …`; nightly script can be `uv run taxi-run --recent 3`.
- "Load-testing the normalizer's output" recipe: replace the entire `k6-loadtest/config.yaml` + `k6-preprocess` + `./k6-loadtest/k6 run` flow with a `uv run taxi-load` recipe (reads `raw-normalized/<type>`, needs `MSSQL_PASSWORD`, supports `--dry-run`, `--full-refresh`, `--data-dir`, `--input-dir`).
- Proxy recipe: `taxi-download` (httpx, honors `HTTP_PROXY`/`HTTPS_PROXY` via `trust_env`) not curl; drop `CURL_CA_BUNDLE` emphasis (keep `SSL_CERT_FILE`); reword the "K6 hitting the proxy for localhost:1433" line for `taxi-load` (DuckDB mssql extension).
- "Populating a fresh dev SQL Server": `uv run taxi-download --recent 3 yellow` → `uv run normalize yellow` → `uv run taxi-load yellow` (or one `uv run taxi-run --recent 3 --load yellow`); delete the k6 config block.

- [ ] **Step 1:** Rewrite architecture.md per corrections.
- [ ] **Step 2:** Rewrite cookbook.md per corrections.
- [ ] **Step 3: Verify** `grep -nE 'download_taxi_data\.sh|k6|K6|four tools|bash \+ curl|OUTPUT_DIR' docs/architecture.md docs/cookbook.md` → no hits.
- [ ] **Step 4: Commit** `docs: rewrite architecture and cookbook for current tool set`.

---

### Task 6: Fix reference docs + contributing + releasing

**Files:**
- Modify: `docs/reference/configuration.md`
- Modify: `docs/reference/exit-codes.md`
- Modify: `docs/contributing.md`
- Modify: `docs/operations/releasing.md`
- Reference: component CLIs, `.github/workflows/{ci.yml,release.yml}`

**Corrections (configuration.md):**
- Delete the entire "K6 load-test config YAML" section; replace with a short note that loader/orchestrator config is CLI flags + `MSSQL_PASSWORD` (no YAML config file).
- Env-var table: delete `OUTPUT_DIR` row (not read by any code); document `--data-dir` (downloader/normalize/loader/orchestrator; loader also `--input-dir`). Fix `MSSQL_PASSWORD` row → read by `taxi-load` / `taxi-run` (required for `--load`), not "K6 test.js". Fix `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY` rows → "httpx (downloader)" not "curl".
- Leave the Normalize mapping YAML section (accurate).

**Corrections (exit-codes.md):**
- Downloader section: retitle `download_taxi_data.sh` → `taxi-download`; rewrite table to {0 success/all-present, 2 nothing-downloaded-but-gave-up / argparse error}. Delete exit 1, exit 130, WSL-prompt subsection, `OUTPUT_DIR`.
- Delete the K6-preprocess section entirely.
- Keep Normalize (0/1/2/3, highest-exit-code) and Schema-drift (0/1/2) sections (accurate).
- **Add** sections: `taxi-load` (0 success/dry-run/skip; 2 config/identifier/`MSSQL_PASSWORD`/connection/`TypeMappingError`; 1 mid-load failure, `max()` aggregation); `taxi-run` (0 clean / 1 needs-review / 2 operational, precedence 2>1>0; plus 2 for `--download-only`+`--load` conflict and missing `MSSQL_PASSWORD` with `--load`); `taxi-curate-mappings` (at least a stub).

**Corrections (contributing.md):**
- L17 `cd taxi` → `cd taxi-seed`.
- L24 tool list: `taxi-download`, `schema-drift`, `normalize`, `taxi-load`, `taxi-run`, `taxi-curate-mappings` (drop `k6-preprocess`).
- L37 `83 passed` and L30 "under a second": durable phrasing; note full suite includes SQL-Server integration/e2e (needs a container, not sub-second).
- L111 commit example `feat(downloader): OUTPUT_DIR env var` → `feat(downloader): --data-dir flag`.
- L120 Bash code-style bullet: downscope to `scripts/` (only `scripts/e2e-smoke.sh` is bash now).

**Corrections (releasing.md):**
- L16 "two jobs defined in ci.yml" → `ci.yml` defines three jobs (`test`, `docs`, `integration`); a PR runs `test` and `integration` (`docs` deploys only on push to `main`). Everything else (tag classification, TestPyPI/PyPI, OIDC, `taxi-seed`) verified accurate — leave as is.

- [ ] **Step 1:** Apply configuration.md corrections.
- [ ] **Step 2:** Apply exit-codes.md corrections (including new taxi-load/taxi-run/taxi-curate-mappings sections).
- [ ] **Step 3:** Apply contributing.md and releasing.md corrections.
- [ ] **Step 4: Verify** `grep -nE 'OUTPUT_DIR|k6|K6|download_taxi_data\.sh|83 passed|cd taxi\b' docs/reference/*.md docs/contributing.md docs/operations/releasing.md` → no hits.
- [ ] **Step 5: Commit** `docs(reference): fix config/exit-codes for current tools; update contributing/releasing`.

---

### Task 7: Purge k6 from nav + stash for taxi-lab; finalize mkdocs

**Files:**
- Create: `taxi-lab-handoff/README.md`
- Move: `docs/superpowers/specs/2026-03-25-k6-sql-load-testing-design.md` → `taxi-lab-handoff/2026-03-25-k6-sql-load-testing-design.md`
- Move: `docs/superpowers/plans/2026-03-25-k6-sql-load-testing.md` → `taxi-lab-handoff/2026-03-25-k6-sql-load-testing.md`
- Modify: `mkdocs.yml`

**Corrections:**
- `git mv` both k6 files into `taxi-lab-handoff/`.
- `taxi-lab-handoff/README.md`: short note that these files are staged for migration to the `taxi-lab` repo and are intentionally excluded from the published docs site.
- `mkdocs.yml`: `site_name: taxi` → `taxi-seed`; `site_description` → drop "K6 SQL Server load tester", mention downloader/schema-drift/normalizer/loader/orchestrator. Remove the "K6 SQL load testing" Design Specs nav entry. Add relevant newer specs to Design Specs nav (python-downloader, sql-loader, orchestrator, configurable-data-dir, this documentation-sync spec). Confirm the two new guide entries from Task 4 are present.

- [ ] **Step 1:** `mkdir -p taxi-lab-handoff` and `git mv` the two k6 files.
- [ ] **Step 2:** Write `taxi-lab-handoff/README.md`.
- [ ] **Step 3:** Apply mkdocs.yml corrections (site_name, description, nav).
- [ ] **Step 4: Verify** `grep -rniE 'k6' docs/ mkdocs.yml` → no hits (k6 lives only in `taxi-lab-handoff/` now).
- [ ] **Step 5: Commit** `docs: purge k6 from live docs, stash for taxi-lab; finalize mkdocs nav/title`.

---

### Task 8: Full verification sweep

**Files:** none (verification + any fix-up commits)

- [ ] **Step 1: Strict docs build** — `uv sync --extra docs && uv run mkdocs build --strict`. Expected: builds clean, no warnings.
- [ ] **Step 2: Global stale-term sweep** — `grep -rniE 'download_taxi_data\.sh|OUTPUT_DIR|k6-preprocess|cd taxi\b|bash \+ curl' README.md docs/ | grep -v taxi-lab-handoff` and `grep -rni 'k6' README.md docs/ mkdocs.yml`. Expected: no hits.
- [ ] **Step 3: Doc-relevant tests** — `uv run pytest tests/downloader tests/e2e -q` (fast, no SQL Server) to confirm documented CLI behavior still matches. Note in the summary which suites need a SQL Server container and were not run.
- [ ] **Step 4: Spot-check** each documented command/flag/exit code against the source one final time.
- [ ] **Step 5:** If any fix-ups were needed, commit `docs: verification fix-ups`.

---

## Self-Review

- **Spec coverage:** entry docs (T1), downloader guide (T2), normalize/schema-drift (T3), new guides + nav + README links (T4), architecture/cookbook (T5), reference/contributing/releasing (T6), k6 purge+stash+mkdocs (T7), verification (T8) — all spec sections mapped.
- **Placeholder scan:** every correction lists old→new concretely; no "update the docs" vagueness.
- **Consistency:** console-script names, flags, exit codes, and start dates match the Global Constraints throughout; k6 handling is centralized in T7 to avoid nav conflicts.
