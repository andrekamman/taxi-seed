# Documentation Audit & Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Independently audit and rewrite all 18 living documentation files against ground-truth code/CLI, then publish the corrected site.

**Architecture:** Capture a code-derived ground-truth reference; fan out one blind subagent per doc to produce a findings ledger; apply surgical fixes; run a cross-doc consistency pass and historical-banner touch-up; verify with a strict build and a rendered re-read; publish via `dev→main` fast-forward (gated on explicit user go-ahead).

**Tech Stack:** MkDocs Material, `uv`, Python 3.12, Superpowers dispatching-parallel-agents, git.

## Global Constraints

- Repo name is `taxi-seed` (never `taxi`). Copy verbatim in all prose.
- Tool count is **five tools plus a shared library**: downloader, schema-drift, normalize, loader, orchestrator (+ `shared/`).
- Downloader is a **Python** package (`taxi_download`), never a "shell script".
- **No K6 / SQL load tester** in living docs (moved to the separate `taxi-lab` repo).
- The six real CLI commands, spelled exactly: `taxi-download`, `schema-drift`, `normalize`, `taxi-load`, `taxi-run`, `taxi-curate-mappings`.
- Documentation only — **no code changes**. A genuine code/doc mismatch that is a *code* bug is reported, not silently patched.
- **Never push to `main` without explicit user go-ahead.**
- Scratch artifacts (ground-truth sheet, findings) live under the session scratchpad: `/private/tmp/claude-501/-Users-andre-git-taxi-seed/776dcffb-40f9-4330-be04-1c89ca46f4ea/scratchpad/`.
- In-scope files (18): `README.md`; `downloader/README.md`, `loader/README.md`, `normalize/README.md`, `orchestrator/README.md`, `schema-drift/README.md`; `docs/index.md`, `docs/getting-started.md`, `docs/cookbook.md`, `docs/architecture.md`, `docs/contributing.md`, `docs/operations/releasing.md`, `docs/reference/configuration.md`, `docs/reference/exit-codes.md`, `docs/guides/{downloader,loader,normalize,schema-drift,orchestrator}.md`.
- Out of scope (do not modify, except Task 5's banner): `docs/superpowers/plans/*`, `docs/superpowers/specs/*`, `taxi-lab-handoff/*`.

---

### Task 1: Establish the ground-truth reference sheet

**Files:**
- Create: `<scratchpad>/ground-truth.md`

**Interfaces:**
- Produces: `ground-truth.md` — the single trusted source consumed by every audit agent in Task 2. Sections: `## CLI`, `## Exit codes`, `## Config keys`, `## Core facts`, `## Paths/conventions`.

- [ ] **Step 1: Capture every CLI `--help`**

Run and paste raw output into `## CLI` (one subsection per command):

```bash
for c in taxi-download schema-drift normalize taxi-load taxi-run taxi-curate-mappings; do
  echo "### $c"; uv run $c --help 2>&1; echo
done
```

Also capture any subcommand help the top-level `--help` reveals (e.g. `uv run taxi-run <subcmd> --help`).

- [ ] **Step 2: Capture exit codes**

Extract real exit points into `## Exit codes` (per tool):

```bash
grep -rn "sys.exit\|raise SystemExit\|EXIT_\|return 1\|return 2" \
  downloader/src loader/src normalize/src schema-drift/src orchestrator/src shared 2>/dev/null
```

Record the meaning of each non-zero code from surrounding context.

- [ ] **Step 3: Capture config keys**

Into `## Config keys` — find the config schema/parse sites:

```bash
grep -rn "config\|\.get(\|os.environ\|argparse\|add_argument\|BaseModel\|dataclass" \
  */src 2>/dev/null | grep -i "config\|env\|key\|default" | head -80
```

List the real keys/flags each tool reads, with defaults.

- [ ] **Step 4: Record corrected core facts + conventions**

Into `## Core facts`: repo name `taxi-seed`; 5 tools + shared lib; Python downloader; no K6; TLC series names/count; WAF backoff ladder (verify the real values in `downloader/src`); disk-size/timing figures — mark each as **sourced** (cite file) or **narrative** (no code source).
Into `## Paths/conventions`: `raw/` layout and parquet organization (verify against `shared/` and downloader code).

- [ ] **Step 5: Sanity-check the sheet**

Re-read `ground-truth.md`. Every "Core fact" is either backed by a captured command/grep above or explicitly tagged `narrative`. No unverified assertions. This file is now frozen for Task 2.

- [ ] **Step 6: Commit (scratch is untracked; commit a pointer note only if useful — otherwise skip)**

No repo commit for scratch. Proceed.

---

### Task 2: Independent parallel audit (18 blind agents)

**Files:**
- Create: `<scratchpad>/findings/<docslug>.md` (one per in-scope file)
- Create: `<scratchpad>/findings-aggregate.md`

**Interfaces:**
- Consumes: `ground-truth.md` from Task 1.
- Produces: `findings-aggregate.md` — a single table `file | claim | verdict | evidence | fix`, consumed by Tasks 3–4.

- [ ] **Step 1: Dispatch the fan-out**

Using superpowers:dispatching-parallel-agents, launch **one subagent per in-scope file (18)**. Each agent prompt contains: (a) the full contents of `ground-truth.md`, (b) the path to its single assigned doc, (c) the instruction to read ONLY that doc + the sheet, and (d) the required output schema below. Agents must NOT read other docs or any prior conversation conclusions.

Required agent output (returned as its final message, and written to `<scratchpad>/findings/<docslug>.md`):

```
For each checkable claim in the doc (command, flag, number, path, exit code, cross-link, tool count, repo name, downloader language):
- claim: <verbatim or paraphrase>
- verdict: CONFIRMED | WRONG | STALE | UNVERIFIABLE
- evidence: <ground-truth line or "no source in sheet">
- fix: <concrete correction, or "none">
Be exhaustive. Mark anything not decidable from the sheet as UNVERIFIABLE (do not assume).
```

- [ ] **Step 2: Aggregate**

Collect all 18 ledgers into `findings-aggregate.md` as one sortable table. Count WRONG + STALE items; list UNVERIFIABLE items separately (these need a human/sourcing decision in Task 3).

- [ ] **Step 3: Gate**

Expected: every in-scope file has a ledger; the known live-site errors (repo name, "four tools", K6, "shell script") appear as WRONG where present. If any file's agent returned nothing usable, re-dispatch that one. No commit (scratch).

---

### Task 3: Apply surgical fixes (WRONG / STALE) per file

**Files:**
- Modify: each of the 18 in-scope files that has ≥1 WRONG/STALE finding.

**Interfaces:**
- Consumes: `findings-aggregate.md` from Task 2.

- [ ] **Step 1: Resolve UNVERIFIABLE items first**

For each UNVERIFIABLE claim (typically perf/disk/timing narrative): either source it (add a code/measurement reference), soften to a clearly-narrative phrasing, or remove it. Do not leave asserted-but-unsourced numbers. Record the decision inline in `findings-aggregate.md`.

- [ ] **Step 2: Edit files, one at a time**

For each file with findings, apply every WRONG/STALE `fix` via Edit, preserving surrounding prose and voice. Keep edits surgical — do not rewrite paragraphs that had no findings. Work through files in this order: `docs/index.md`, `README.md`, component READMEs, guides, reference, cookbook, architecture, getting-started, contributing, operations/releasing.

- [ ] **Step 3: Per-file re-check**

After editing a file, grep it for the banned strings to confirm none survive:

```bash
grep -in "\bfour tools\b\|K6\|load tester\|shell script\|^# taxi$\|\btaxi is one repo\b" <file>
```

Expected: no matches (except legitimate uses, e.g. the `taxi-lab` pointer in contributing.md).

- [ ] **Step 4: Commit the rewrite**

```bash
git add README.md docs/ downloader/README.md loader/README.md normalize/README.md orchestrator/README.md schema-drift/README.md
git commit -m "docs: fix all WRONG/STALE claims from independent audit"
```

---

### Task 4: Cross-doc consistency pass

**Files:**
- Modify: any in-scope file needing consistency fixes.

**Interfaces:**
- Consumes: the corpus after Task 3.

- [ ] **Step 1: Repo name + tool count sweep**

```bash
grep -rin "\btaxi\b\|four tools\|five tools\|K6" README.md docs/*.md docs/guides docs/reference docs/operations */README.md | grep -v superpowers | grep -v taxi-lab-handoff
```

Expected: every bare `taxi` is either `taxi-seed`, a command prefix (`taxi-download`), or a deliberate `taxi-lab` reference. Tool count reads "five" consistently. Fix any stragglers.

- [ ] **Step 2: Command-name consistency**

Grep each of the six commands across the corpus; confirm exact spelling everywhere (no `taxi-normalize`, no `taxi-schema-drift`):

```bash
grep -rin "taxi-normalize\|taxi-schema-drift\|schema_drift \|taxi-orchestrate\b" README.md docs */README.md | grep -v superpowers
```

Expected: no matches. Fix any found.

- [ ] **Step 3: Internal link/anchor integrity (pre-build check)**

```bash
grep -rEn "\]\((?!https?://)[^)]+\)" docs README.md */README.md | grep -v superpowers | head -100
```

Manually confirm each relative link target exists and anchors match headings. Fix broken ones.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/ */README.md
git commit -m "docs: cross-doc consistency (repo name, tool count, command names, links)"
```

---

### Task 5: Historical banner on affected published specs

**Files:**
- Modify: each `docs/superpowers/specs/*.md` in the site nav that describes a since-removed/changed feature (K6, "four tools"). Identify them:

```bash
grep -rln "K6\|four tools\|K6-based\|load tester" docs/superpowers/specs/
```

**Interfaces:**
- Consumes: nav list in `mkdocs.yml` (only banner specs that are actually in the nav).

- [ ] **Step 1: Add the banner**

To the top of each affected, nav-listed spec (immediately under its `# Title`), insert verbatim (using that spec's own date):

```markdown
> *Historical design record (as of <spec-date>); the K6 load tester was later moved to the separate `taxi-lab` repo.*
```

Do NOT edit the spec body.

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/
git commit -m "docs(specs): mark historical design records with a banner note"
```

---

### Task 6: Verify — strict build, links, rendered re-read

**Files:**
- Create: `<scratchpad>/findings-report.md` (final resolution report)

- [ ] **Step 1: Strict build**

```bash
uv run --extra docs mkdocs build --strict 2>&1 | tail -30
```

Expected: exits 0, no WARNING about missing links/nav. If it fails, fix and re-run until green.

- [ ] **Step 2: Rendered spot-check of the homepage**

```bash
grep -o "five tools[^<]*\|four tools[^<]*\|K6[^<]*\|taxi-seed[^<]*" site/index.html | head
```

Expected: "five tools…", "taxi-seed…"; zero "four tools"/"K6".

- [ ] **Step 3: Rendered re-read of each guide**

For each `site/guides/*/index.html` and `site/index.html`, confirm against `ground-truth.md`: command names, exit codes, tool count. Note any residual mismatch and fix its source `.md`, then rebuild.

- [ ] **Step 4: Write the findings report**

`findings-report.md`: every WRONG/STALE item from Task 2 and how it was resolved (file + before→after), plus how each UNVERIFIABLE item was handled. This is the user's spot-check artifact.

- [ ] **Step 5: Present the report to the user**

Show `findings-report.md` and the green `--strict` build result. Do not proceed to publish without acknowledgement.

---

### Task 7: Publish (gated on explicit user go-ahead)

**Files:** none (branch operation).

- [ ] **Step 1: Push dev**

```bash
git push origin dev
```

- [ ] **Step 2: Confirm the fast-forward is still clean**

```bash
git fetch origin -q && git log --oneline origin/dev..origin/main | wc -l
```

Expected: `0` (main has no commits dev lacks → fast-forward safe). If non-zero, STOP and report divergence.

- [ ] **Step 3: Ask the user for explicit go-ahead to promote `dev→main`.**

Do not run Step 4 until the user says yes.

- [ ] **Step 4: Fast-forward main and push (triggers gh-deploy)**

```bash
git push origin origin/dev:main
```

- [ ] **Step 5: Confirm the live site**

After the CI `docs` job completes, fetch `https://andrekamman.github.io/taxi-seed/` and confirm the homepage shows `taxi-seed`, five tools + shared lib, Python downloader, no K6. Report the result.

---

## Self-Review

**Spec coverage:**
- Phase 1 (ground truth) → Task 1. ✓
- Phase 2 (independent audit) → Task 2. ✓
- Phase 3 (surgical rewrite + consistency) → Tasks 3 & 4. ✓
- Phase 4 (verification & definition of done) → Task 6. ✓
- Phase 5 (publish, main gate, no unapproved push) → Task 7. ✓
- Published historical specs (banner) → Task 5. ✓
- Success criteria 1–6 → covered by Tasks 2/3 (no WRONG/STALE), 6 (strict build, rendered check), 4 (consistency), 5 (banner), 7 (live-site confirmation), 6 (findings report). ✓
- Non-goal "no code changes" → Global Constraints + Task 1 Step 2/Task 3 (report, don't patch). ✓

**Placeholder scan:** No TBD/TODO. Every step has concrete commands. The `<spec-date>` / `<scratchpad>` / `<docslug>` / `<file>` tokens are per-item substitutions, not unfilled placeholders.

**Type consistency:** Artifact names are consistent across tasks — `ground-truth.md` (Task 1 → 2), `findings-aggregate.md` (Task 2 → 3, 4), `findings-report.md` (Task 6). Command list is identical everywhere it appears.
