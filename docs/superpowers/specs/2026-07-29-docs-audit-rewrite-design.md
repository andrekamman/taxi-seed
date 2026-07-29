# Documentation Audit & Rewrite — Design

**Date:** 2026-07-29
**Status:** Approved (design)
**Scope owner:** Andre Kamman

## Problem

The published documentation site (<https://andrekamman.github.io/taxi-seed/>) is
materially wrong. Its homepage still describes the repo as `taxi` with **four
tools including a "K6-based SQL Server load tester"**, calls the downloader a
"shell script", and quotes stale figures. None of this reflects the current
codebase: five tools plus a shared library, a Python downloader, no K6 (that
tool and its design were moved to the separate `taxi-lab` repo), and the repo is
named `taxi-seed`.

### Root cause (two distinct problems)

1. **Deploy gap.** The docs deploy job in `.github/workflows/ci.yml` triggers
   **only on push to `main`** (`if: github.ref == 'refs/heads/main'`). All doc
   cleanup work lands on `dev` (the repo's default branch, `origin/HEAD → dev`).
   GitHub Pages serves from `gh-pages`, last built **2026-07-26 from `main`'s
   tip `fe33231`** — which predates the K6 purge. `dev` is 3 commits ahead of
   `main` with zero divergence (clean fast-forward available). So the public
   site is a pre-cleanup snapshot even though much of the source is already
   fixed.

2. **Untrusted source.** The user has twice caught prior "comprehensive review"
   claims that were incomplete. Even where source markdown looks clean, it must
   be independently re-verified against the code rather than trusted.

## Goal

An **independent, evidence-backed audit and rewrite** of all *living*
documentation, treating the current source as untrusted, verifying every
checkable claim against the actual code/CLI, rewriting anything inaccurate or
stale — and then fixing the deploy so the corrected docs actually reach the
public site.

"Done" is defined by evidence (a strict build, a link check, a re-read of
rendered output, and a findings report), not by assertion.

## Scope

### In scope — 18 living-doc files

- **Root:** `README.md`
- **Component READMEs (5):** `downloader/README.md`, `loader/README.md`,
  `normalize/README.md`, `orchestrator/README.md`, `schema-drift/README.md`
- **Site pages (13):** `docs/index.md`, `docs/getting-started.md`,
  `docs/cookbook.md`, `docs/architecture.md`, `docs/contributing.md`,
  `docs/operations/releasing.md`, `docs/reference/configuration.md`,
  `docs/reference/exit-codes.md`, `docs/guides/{downloader, loader, normalize,
  schema-drift, orchestrator}.md`

### Out of scope

- `docs/superpowers/plans/*` — historical planning archive (also excluded from
  the site build via `exclude_docs`).
- `docs/superpowers/specs/*` — historical point-in-time design records. **Not
  rewritten.** See "Published historical specs" below for the one light-touch
  exception.
- `taxi-lab-handoff/*` — intentionally staged for migration to the `taxi-lab`
  repo; K6 content there is expected.

## Approach (A: ground-truth-first + independent parallel audit)

### Phase 1 — Establish ground truth

Build one reference sheet, captured directly from the code, as the *only*
trusted source for the audit. Commit it as a scratch artifact so verdicts are
reproducible.

Contents:

- **CLI surface:** `--help` output for all six commands — `taxi-download`,
  `schema-drift`, `normalize`, `taxi-load`, `taxi-run`, `taxi-curate-mappings`
  — capturing real subcommands, flags, and defaults.
- **Exit codes:** the actual exit-code constants / `sys.exit(...)` sites per
  tool.
- **Config keys:** the real config schema each tool reads.
- **Corrected core facts:** tool count (5 tools + shared lib), downloader
  language (Python), repo name (`taxi-seed`), disk-size figures, WAF backoff
  ladder, number of TLC series.
- **Path/naming conventions:** `raw/` layout, parquet organization, etc.

### Phase 2 — Independent parallel audit

Fan out **one subagent per file (18 total)** using Superpowers'
dispatching-parallel-agents. Each agent receives **only** the ground-truth sheet
plus its single assigned doc — deliberately *not* any prior conclusions — and
returns a structured findings ledger:

| Field | Meaning |
|---|---|
| `claim` | A checkable statement (command, flag, number, path, exit code, cross-link) |
| `verdict` | CONFIRMED / WRONG / STALE / UNVERIFIABLE |
| `evidence` | The ground-truth line or code reference deciding it |
| `fix` | Concrete suggested correction |

Agents are instructed to be exhaustive and to mark anything they cannot verify
as UNVERIFIABLE rather than assume. All 18 ledgers are aggregated into one
findings table.

### Phase 3 — Surgical rewrite + cross-doc consistency

- Fix every WRONG/STALE finding while preserving good existing prose (this is a
  surgical rewrite, not a from-scratch regeneration).
- Run a whole-corpus consistency pass: repo name (`taxi-seed`), tool count,
  terminology ("downloader", not "shell script"), identical use of the six
  command names everywhere, and resolution of every internal cross-link/anchor.

### Phase 4 — Verification & definition of done

- `uv run --extra docs mkdocs build --strict` passes (strict fails on broken
  links / nav).
- Link/anchor check across all rewritten pages.
- Re-read the **rendered** output (built HTML), confirming the homepage and each
  guide against ground truth.
- Produce a final findings report: every WRONG/STALE item and its resolution,
  for user spot-check.

### Phase 5 — Publish

- Work lands on `dev` as one reviewable commit (or a short, well-scoped series).
- Publishing = **fast-forward `dev → main`**, which triggers `gh-deploy` and
  rebuilds Pages. (Decision: keep `main` as the docs-publish gate; the workflow
  is unchanged.)
- After deploy, confirm the live site reflects the corrected copy.
- **No push to `main` without explicit user go-ahead.**

## Published historical specs (decided: historical banner)

A few `superpowers/specs/*` files are in the site nav under "Design Specs" and
still describe "four tools / K6". Their bodies stay untouched (they are
historical records), but each spec that describes a since-removed or changed
feature gets a single italic header line:

> *Historical design record (as of YYYY-MM-DD); the K6 load tester was later
> moved to the separate `taxi-lab` repo.*

This is a targeted addition to affected specs only — not a rewrite, and not a
nav change.

## Non-goals

- No rewrite of historical plans/specs bodies.
- No change to the `taxi-lab-handoff/` content.
- No change to the deploy-branch strategy (main remains the gate).
- No code changes — documentation only. (If the audit surfaces a genuine
  code/doc mismatch that is a *code* bug, it is reported, not silently fixed
  here.)

## Success criteria

1. All 18 living docs contain zero WRONG/STALE claims per the aggregated
   findings table.
2. `mkdocs build --strict` is green; all internal links resolve.
3. Repo name, tool count, and command names are consistent across every living
   doc.
4. Affected published specs carry the historical banner.
5. After `dev → main` promotion, the live site homepage describes `taxi-seed`
   with five tools + shared lib, a Python downloader, and no K6.
6. A findings report documents what was wrong and how each item was resolved.

## Risks / watch-items

- **Naming asymmetry:** entry points mix `taxi-`-prefixed (`taxi-download`,
  `taxi-load`, `taxi-run`, `taxi-curate-mappings`) and unprefixed
  (`schema-drift`, `normalize`) commands. Docs must match the real names exactly
  — a likely source of existing errors.
- **UNVERIFIABLE claims:** narrative/perf figures with no code source (e.g. disk
  sizes, timings) must be either sourced, hedged, or removed — not asserted.
- **Overclaiming (again):** the independent fan-out and the evidence-based
  definition of done exist specifically to prevent a repeat of premature
  "comprehensive" claims.
