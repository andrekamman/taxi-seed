# Orchestrator Design

**Date:** 2026-07-23
**Status:** Approved, ready for implementation planning
**Sub-project of:** the four-part expansion (normalizer ✅, docs ✅, loader ✅, **orchestrator** ← this, CI/fake-data)

## Motivation

The pipeline's four stages — download, (analyze,) normalize, load — each run as their own
tool today, and a human runs them in sequence by hand. The orchestrator is the operational
wrapper that runs them as **one command**, honoring each stage's exit-code contract, and — in
keeping with the project's "data loss is an error / human-in-the-loop" philosophy — **halting a
data type and pulling the human in exactly when normalize flags new drift for review**, rather
than silently loading partially-normalized data.

It is deliberately the narrowest useful thing: a thin coordinator, not a scheduler. Running on a
cadence is left to system `cron` (`cron` + `taxi-run`); "notify a human" is the clear stdout
summary plus a meaningful exit code that `cron` surfaces. No daemon, no state database, no
built-in email/Slack.

This sub-project has **two deliverables**:

1. **The `taxi-run` orchestrator** (code + tests).
2. **Curated, committed mapping YAMLs** for all four types (`normalize/mappings/{yellow,green,fhv,fhvhv}.yaml`),
   without which a fresh clone cannot run the pipeline unattended (normalize would stop on the
   first run to scaffold a mapping for review). See *Mapping curation* below.

## Non-goals

- **No scheduler / daemon.** Cadence is `cron` invoking `taxi-run`. The orchestrator runs once and exits.
- **No state database.** Every stage is already idempotent and disk-backed (downloader skips
  existing files, normalize skips existing outputs, the loader has its `_load_manifest` table).
  The orchestrator derives nothing it doesn't observe from the stage exit codes in a single run.
- **No built-in notifications.** The exit code + printed summary are the notification surface.
- **No new in-memory pipeline.** Stages communicate through the filesystem exactly as they do
  today; the orchestrator shells out to each existing CLI and reads its exit code.
- **No reimplementation of stage logic.** The orchestrator never parses parquet, never touches
  SQL Server directly, never re-derives what a stage already decides. It sequences and reports.
- **The `analyze` (schema-drift) stage is not in the chain.** normalize's bootstrap does its own
  analysis; a standalone drift report is a separate, optional tool. (Possible future `--analyze`.)

## Approach — subprocess seam

The orchestrator **invokes each existing CLI as a subprocess and reads its exit code.** This
preserves the architecture's core seam ("each stage writes its output to disk; the next stage
reads from disk … individually restartable and individually inspectable") and keeps the
orchestrator a thin coordinator with no shared process state.

Rejected alternatives:

- *Import and call each `main()` in-process* — couples the four tools into one process, breaks the
  disk-seam model, shares CWD and global state (the loader's `mssql` attach context is
  process-global), and makes a crash in one stage able to corrupt another.
- *A bash wrapper* — cannot cleanly express the per-type exit-code halting logic, and is not
  unit-testable.

## Component layout

New component `orchestrator/`, matching the monorepo pattern:

```
orchestrator/
├── README.md                        # one-paragraph pointer to the docs guide
└── src/taxi_orchestrate/
    ├── __init__.py
    ├── cli.py          # entry point `taxi-run`: arg parsing, the per-type loop
    ├── stages.py       # pure build_*_cmd() argv builders + a thin injected subprocess runner
    ├── pipeline.py     # PURE exit-code -> outcome + halt/continue logic (the heart)
    └── report.py       # per-type / per-stage summary rendering
```

**Tests:** `tests/taxi_orchestrate/` (mirrors the other components).

**`pyproject.toml` additions:**
- Add `orchestrator/src/taxi_orchestrate` to `[tool.hatch.build.targets.wheel] packages`.
- Add `taxi-run = "taxi_orchestrate.cli:main"` to `[project.scripts]`.
- **No new runtime dependency** (stdlib `subprocess`, `argparse`, `pathlib`).

## CLI

```
taxi-run [TYPE]                 # TYPE optional; omit = all four types, in order
  --recent [N]                  # downloader recent-mode passthrough (default N per downloader)
  --skip-download               # normalize (and load) using the existing raw/ mirror
  --download-only               # only mirror; skip normalize and load
  --load                        # opt-in: also load normalized parquet into SQL Server
  --sample N|N%                 # passed through to normalize
  --data-dir DIR                # working root holding raw/ + raw-normalized/ (default: repo root)
  --dry-run                     # print the per-type plan of stages and exit 0, running nothing
  # forwarded to taxi-load, only meaningful with --load:
  --host HOST  --port 1433  --database taxi  --schema dbo  --user sa
  --flush-rows 100000  --full-refresh
```

- `TYPE` is one of `yellow`, `green`, `fhv`, `fhvhv`; omitting it processes all four in order.
  Restricted via `choices` (a typo is a usage error, exit 2), matching `taxi-load`.
- **Default run = download → normalize.** `--load` adds the load stage. `--download-only` and
  `--skip-download` select a sub-range; `--download-only` with `--load` is a usage error.
- **Load requires `MSSQL_PASSWORD` (environment only)**, forwarded to `taxi-load` and never
  logged. With `--load` set and the variable missing, the run fails fast with exit 2 before any
  stage runs.
- `--recent [N]` and `--sample` are passthroughs to the downloader and normalize respectively.
- `--dry-run` prints, per type, the stages that would run (and the resolved commands), then exits
  0 without invoking anything.

## Stage invocation

All stages run with `cwd = <data-dir>` (default: the repo root, resolved as the directory
containing `downloader/` and `pyproject.toml`), so normalize finds `raw/` and
`normalize/mappings/` and the loader finds `raw-normalized/`.

| Stage | Command (argv) |
|---|---|
| download | `bash <root>/downloader/download_taxi_data.sh [TYPE]` — or `… --recent [N] [TYPE]` |
| normalize | `<python> -m taxi_normalize.cli [TYPE] [--sample S]` |
| load | `<python> -m taxi_loader.cli [TYPE] --input-dir raw-normalized [conn flags]` |

`<python>` is `sys.executable`. The `-m` form is used because both Python CLIs have a
`__main__` guard, so it works without relying on the console scripts being on `PATH`. The
`build_download_cmd` / `build_normalize_cmd` / `build_load_cmd` functions are **pure** (argv-list
builders) and unit-tested; a single injected `run(cmd, cwd, env) -> int` wrapper does the actual
`subprocess.run` and is stubbed in tests. `MSSQL_PASSWORD` is passed through the child
environment for the load stage only.

## Exit-code & halt semantics (the core)

For each requested type, stages run in order. **A "needs review" or failure outcome halts that
type's remaining stages** — never load data that did not fully normalize — while other types
continue. This logic lives in `pipeline.py` as a pure function of `(stage, child_exit_code)`.

| Stage | Child exit | Type outcome | Effect |
|---|---|---|---|
| download | `0` | — | proceed to normalize |
| download | non-`0` | `DOWNLOAD_FAILED` | halt this type |
| normalize | `0` | — | proceed to load (if `--load`) |
| normalize | `3` (scaffold written) | `NEEDS_REVIEW` | halt this type; name the mapping file |
| normalize | `1` (unresolved / amended) | `NEEDS_REVIEW` | halt this type; name the mapping file |
| normalize | `2` | `NORMALIZE_ERROR` | halt this type |
| load | `0` | `LOADED` (or `SKIPPED` if download+normalize were no-ops) | done |
| load | `1` (partial) | `LOAD_PARTIAL` | record; continue other types |
| load | `2` (conn/config) | `LOAD_ERROR` | **abort remaining loads** (a shared-server conn error will recur) |

**Overall exit code** (mirrors the repo's `2 > 1 > 0` convention):

- `0` — every requested type completed its requested stages cleanly (including all-skip no-ops and a successful `--dry-run`).
- `1` — at least one type halted **needing human mapping review** (normalize exit 3/1), and nothing else failed. This is the cron signal "a mapping needs a human."
- `2` — an **operational failure** occurred (download failed, normalize error, or load partial/error). Cron signal "something broke."

Precedence when several occur: `2` (broke) > `1` (needs review) > `0`. The end-of-run summary
always prints every type's per-stage result regardless of the headline code.

**Known limitation (documented, accepted for v1):** `download_taxi_data.sh` exits `0` even when it
gives up after exhausting WAF backoff — it returns non-zero only on bad arguments. So the
orchestrator cannot distinguish "downloaded nothing because all caught up" from "WAF-blocked and
gave up." v1 treats download exit `0` as success. A future downloader change (exit non-zero on
give-up) would let the orchestrator surface it; that change is out of scope here.

## Reporting

No state store. At the end of a run the orchestrator prints a per-type, per-stage summary table
to stdout (type | download | normalize | load | outcome), a one-line overall verdict, and sets
the exit code above. `cron` captures stdout and the exit code; that is the entire notification
surface. (A machine-readable `--json` summary is a possible future addition, omitted from v1.)

## Mapping curation (second deliverable)

For a fresh clone to run the pipeline unattended, the four per-type mapping YAMLs must be
committed to `normalize/mappings/` (today only `.gitkeep` is there, so the first `normalize` run
of each type stops at exit 3 to scaffold a mapping for review).

The maintainer's full local history already exists at `/Users/andre/git/taxi/raw` (yellow back to
2009, green to 2013, fhv to 2015, fhvhv to 2019 — ~580 monthly parquet files total); **no download
is required.** Curation procedure:

1. Symlink `taxi-public/raw → /Users/andre/git/taxi/raw` (both `raw/` and `raw-normalized/` are
   gitignored, so a local symlink is fine).
2. For each type, run `normalize <type>`. First run scaffolds `normalize/mappings/<type>.yaml`
   (exit 3) with SUGGESTED/TODO entries for detected drift; subsequent runs amend it (exit 1) for
   anything still unresolved.
3. **The maintainer makes the acknowledgment decisions** — uncomment accepted renames, and add an
   `ack_date` for each lossy cast and each acknowledged data-loss drop (this is the human-in-the-loop
   gate; it cannot be automated).
4. Re-run until `normalize <type>` exits `0` for all four types.
5. Commit the four reviewed `normalize/mappings/*.yaml`.

This is data-and-review work done during implementation, and it doubles as the orchestrator's real
end-to-end validation: once the mappings are clean, `taxi-run --skip-download` normalizes cleanly,
and `taxi-run --skip-download --load` loads into a SQL Server.

## Testing strategy

```
tests/taxi_orchestrate/
  test_pipeline.py    # pure exit-code -> outcome + halt/continue decision table — NO subprocess
  test_stages.py      # build_*_cmd() argv builders; run() with stubbed commands
  test_cli.py         # arg parsing, stage selection, --dry-run, missing-password exit 2
  test_run_stub.py    # end-to-end chaining against tiny STUB stage scripts (exit codes + markers)
```

- **`test_pipeline.py` (the bulk):** every row of the exit-code table — download fail halts the
  type; normalize 0/1/2/3 → proceed / needs-review / error; load 0/1/2 → loaded / partial /
  abort-remaining; overall `2>1>0` precedence; per-type independence. Pure and fast.
- **`test_stages.py`:** the argv builders produce the exact commands (`-m taxi_normalize.cli …`,
  `--input-dir raw-normalized`, `--recent N`, forwarded loader flags); the injected `run` wrapper
  is exercised with stub commands.
- **`test_run_stub.py`:** the CLI drives a full multi-type run against **stub scripts** that exit
  with configured codes and drop marker files, verifying chaining, halting, stage selection, and
  the summary/exit-code — with no real downloader / normalize / loader / SQL Server.
- **Real end-to-end** (manual, during curation): `taxi-run --skip-download` over the real
  `raw/` symlink produces clean `raw-normalized/`; `taxi-run --skip-download --load` loads into the
  loader's Docker SQL Server. Not part of the fast unit suite.

**Target test count:** ~20–25, weighted toward the pure `pipeline` cases.

## Implementation sequence (for the plan)

1. `pipeline.py` pure outcome/halt logic + exhaustive `test_pipeline.py`.
2. `stages.py` argv builders + injected runner + `test_stages.py`.
3. `report.py` summary rendering (+ unit test).
4. `cli.py` (arg parsing, per-type loop, `--dry-run`, exit codes) + `test_cli.py` + `test_run_stub.py`.
5. `pyproject.toml` wiring + `orchestrator/README.md` pointer.
6. **Mapping curation:** symlink real data, run/normalize/ack per type, commit the four YAMLs;
   validate `taxi-run --skip-download [--load]` end-to-end.

## Success criteria

- `taxi-run` (no arg) runs download → normalize for all four types and prints a per-type summary.
- `taxi-run yellow --skip-download` normalizes yellow from the existing mirror; exit 0 when the
  mapping is complete.
- When normalize scaffolds/amends a mapping for a type, that type halts at `NEEDS_REVIEW`, the
  mapping file is named in the summary, other types still run, and the overall exit code is `1`.
- `taxi-run --skip-download --load` loads normalized parquet into SQL Server via `taxi-load`,
  forwarding connection flags and `MSSQL_PASSWORD`; a load partial/error yields overall exit `2`.
- `--load` without `MSSQL_PASSWORD` fails fast with exit `2`, having run nothing.
- `--dry-run` prints the plan and exits `0`, invoking no stage.
- The four `normalize/mappings/*.yaml` are committed and a fresh clone's `taxi-run --skip-download`
  reaches exit `0` for all four types.
- `uv run --extra test pytest tests/taxi_orchestrate/` passes with no network, no SQL Server, and
  no real TLC data (stubbed stages).

## Out of scope

- Scheduling / daemon / cadence (use system `cron`), state databases, and built-in notifications.
- The `analyze` (schema-drift) stage in the chain; a `--json` machine-readable summary.
- Any change to the downloader's coarse exit codes (WAF give-up still exits 0 in v1).
- The CI fake-data end-to-end pipeline and dev/test/prod promotion — the fourth sub-project.
- Adding path flags (`--input-dir` / `--mappings-dir`) to `normalize`; v1 runs stages at the
  repo-root convention and curates against real data via a local symlink.
