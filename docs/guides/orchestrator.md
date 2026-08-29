# Orchestrator

`taxi-run` runs the pipeline as one command: download → normalize → (opt-in) load, per trip type, honoring each stage's own exit-code contract rather than reinventing one. It exists so a cron job or CI step can invoke a single tool instead of chaining three, while still stopping cleanly the moment a type needs a human (normalize found unresolved mapping items) or a stage genuinely fails. A companion command, `taxi-curate-mappings`, auto-accepts detected schema drift into complete mapping YAMLs for cases where you want the pipeline to run unattended and are comfortable with heuristic acknowledgments.

## Prerequisites

- `uv sync` in the repo root — this also pulls in the [downloader](downloader.md), [normalize](normalize.md), and [loader](loader.md) packages that `taxi-run` shells out to.
- Everything the stages you enable need: for `--load`, a reachable SQL Server and `MSSQL_PASSWORD` set (see the [loader guide](loader.md#prerequisites)).

## Install

```bash
uv sync
```

Exposes two console scripts from the root `pyproject.toml`: `taxi-run` (`taxi_orchestrate.cli:main`) and `taxi-curate-mappings` (`taxi_orchestrate.curate:main`).

## Basic usage

```bash
# Download + normalize all four types (no load)
uv run taxi-run

# Download + normalize one type
uv run taxi-run yellow

# Recent-mode download, then normalize
uv run taxi-run yellow --recent 3

# Also load into SQL Server
export MSSQL_PASSWORD=your-password
uv run taxi-run yellow --load

# See the planned stages without running anything
uv run taxi-run yellow --recent 3 --load --dry-run
```

`data_type` is a positional argument, one of `yellow`, `green`, `fhv`, `fhvhv`; omit it to run all four in turn.

## How stages chain

For each requested type, `taxi-run` invokes each planned stage as a subprocess (`python -m taxi_download.cli`, `python -m taxi_normalize.cli`, `python -m taxi_loader.cli`) from the detected repo root, and classifies the stage's exit code (`pipeline.classify`) into one of: `ok`, `needs_review`, `failed`, `partial`, or `conn_error`. That classification drives two independent decisions:

- **`halt_type`** — stop processing *this type's* remaining stages. Set for any non-zero download exit, a normalize exit of `1` or `3` (needs review) or anything else non-zero (failed), but *not* for a load exit of `0` or `1` (load partial doesn't block anything — load is always the last stage anyway).
- **`abort_run`** — skip the load stage for **all remaining types** in this invocation, not just the current one. Only a loader exit `2` triggers this: it's ambiguous between a connection/config problem (which would fail every subsequent type's load identically) and a per-type `TypeMappingError`, and the deliberate conservative choice is to over-abort — the exit `2` still surfaces on that type's own record either way.

Repo root is auto-detected by walking up from the current directory looking for a `pyproject.toml` alongside a `normalize/mappings/` directory (`find_repo_root`); if none is found, it falls back to the current directory. `--data-dir` overrides where stage subprocesses read/write (`DIR/raw`, `DIR/raw-normalized`) but does not change where the repo root itself is located.

At the end of the run, a summary table is printed (`type`, `download`, `normalize`, `load`, `outcome` columns) via `report.render_summary`, with a per-type label like `LOADED`, `NEEDS REVIEW`, `LOAD PARTIAL`, `LOAD ERROR`, `DOWNLOAD FAILED`, `NORMALIZE ERROR`, or `OK`.

## Flags

**`--recent [N]`** — pass recent-mode through to the downloader. Bare `--recent` (no value) requests the downloader's own default (3 months); `--recent 5` requests 5. Omit `--recent` entirely to walk full history instead.

```bash
uv run taxi-run yellow --recent        # downloader's default recent window
uv run taxi-run yellow --recent 5      # 5 most recent months
uv run taxi-run                        # full history, all four types
```

**`--skip-download`** — skip the download stage; normalize (and optionally load) run against whatever is already in the local `raw/` mirror.

**`--download-only`** — only run the download stage; normalize and load are skipped for every type. **Conflicts with `--load`**: passing both exits immediately with code 2 and an error message, before any stage runs.

```bash
uv run taxi-run --download-only --load   # error: --download-only cannot be combined with --load (exit 2)
```

**`--load`** — after a successful normalize, also load that type's normalized parquet into SQL Server via `taxi-load`. Requires `MSSQL_PASSWORD` to be set in the environment; if it isn't, `taxi-run` exits 2 immediately, before running any stage for any type.

```bash
uv run taxi-run yellow --load   # error: MSSQL_PASSWORD environment variable is required for --load (exit 2, if unset)
```

**`--data-dir DIR`** — base data directory forwarded to every stage: download writes to `DIR/raw`, normalize reads `DIR/raw` and writes `DIR/raw-normalized`, load reads `DIR/raw-normalized`. Default: the detected repo root.

**`--sample VALUE`** — forwarded verbatim to normalize's `--sample` flag (see the [normalize guide](normalize.md#the-sample-flag)).

**`--dry-run`** — print the planned stage list per type (e.g. `download -> normalize -> load`) and exit 0 without invoking any subprocess.

**Load-only pass-through flags** — meaningful only when `--load` is set; forwarded to `taxi-load` unchanged: `--host` (default `localhost`), `--port` (default `1433`), `--database` (default `taxi`), `--schema` (default `dbo`), `--user` (default `sa`), `--flush-rows` (default `100000`), `--full-refresh`. See the [loader guide](loader.md#configuration) for what each controls.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every planned stage for every type completed cleanly (`ok`) — a genuinely clean run — or `--dry-run` printed the per-type plan and exited without running anything. |
| 1 | No stage failed outright, but at least one type stopped at `needs_review` — normalize returned 1 (unresolved mapping items) or 3 (first-run scaffold awaiting review) for that type. Nothing operationally broke; a human has follow-up work. |
| 2 | At least one stage classified as `failed`, `partial`, or `conn_error` — download failed outright, normalize hit a real configuration error, the loader reported a partial load, or the loader hit a connection/config error or `TypeMappingError`. Also returned immediately for the `--download-only` + `--load` conflict or a missing `MSSQL_PASSWORD` when `--load` is set, before any stage runs. |

Precedence is **2 > 1 > 0**: `pipeline.overall_exit_code` scans every stage outcome across every type and returns 2 if any outcome is in the failure set (`failed`, `partial`, `conn_error`), else 1 if any is `needs_review`, else 0.

Note the one subtlety: a loader exit of `1` (partial load) classifies as `partial`, which **is** counted among the exit-2 statuses in `overall_exit_code` — so a partial load on any type escalates the whole run's exit code to 2, even though that type's own stage exit was 1. This is intentional: a partial load is a real operational anomaly (some rows may be missing from SQL Server), not merely "needs human review of a mapping file," so it's treated with the same urgency as a hard failure at the orchestrator level.

## `taxi-curate-mappings`

`taxi-curate-mappings [TYPE]` is a separate, deliberately-invoked bulk-accept utility for schema drift — it does **not** run as part of `taxi-run`. It drives off `normalize`'s own planner (`plan_file`) so the mapping it produces is exactly what `normalize` will accept with zero unresolved items, then writes every acknowledgment with `ack_by: auto-curated` and a `reason:` explaining what was detected.

```bash
uv run taxi-curate-mappings yellow
uv run taxi-curate-mappings                       # all four types
uv run taxi-curate-mappings yellow --raw-dir raw --mappings-dir normalize/mappings
```

For each type, it:

1. Runs `detect_drift` against every raw parquet file to get rename candidates and the target-file pin (reusing an existing mapping's `target:` if one exists).
2. Iteratively re-plans against the normalizer's own logic (up to 8 rounds), and for every unresolved item:
   - **Renames** — accepted if the candidate cast is "executable" (target is a string type, same type family, or numeric→numeric); otherwise the column is dropped as acknowledged data loss instead of producing a mapping that would crash at cast time.
   - **Lossy casts** — auto-acknowledged (`ack_date` = today) if the cast is executable; a non-executable same-name cast (e.g. a string column TLC later ships as numeric) is **not** auto-acked — it raises and lists the blocking column(s), since auto-accepting it would produce a mapping that crashes `normalize` at runtime.
   - **Unmapped drops** — acknowledged as data loss if no viable rename exists.
3. Writes the mapping YAML (`normalize/mappings/<type>.yaml`) with a header noting it was auto-curated, and re-validates until nothing is unresolved (or raises after 8 rounds if the drift is ambiguous/cyclic).

After all requested types are processed, it writes an audit report to `<mappings-dir>/CURATION-REPORT.md` listing, per type, every acknowledgment-required decision (lossy casts, data-loss drops) to verify and every auto-accepted rename with its confidence — renames are heuristic and worth a second look even though they weren't blocking.

**Flags:** `data_type` (positional, optional, defaults to all four), `--raw-dir` (default `raw`), `--mappings-dir` (default `normalize/mappings`).

**Exit codes:** `0` if every requested type curated cleanly (or was skipped because `<raw-dir>/<type>` doesn't exist — printed as `<type>: no raw files at <raw-dir>/<type>, skipping`, not an error); `2` if any type raised (no raw files found for an existing type's directory check, no file matching the target name, a non-executable cast needing manual mapping, or unresolved items remaining after 8 rounds) — the loop continues to the next type and the overall exit code is the max observed.

Because it writes machine-accepted mapping YAMLs directly, treat its output the way you'd treat any other auto-generated config: review the diff (especially the `CURATION-REPORT.md`) before merging, the same way you'd review a `normalize`-amended mapping by hand.

## Troubleshooting

**Q: `error: --download-only cannot be combined with --load`.**
A: The two flags are mutually exclusive by design — `--download-only` explicitly means "stop after mirroring," so pairing it with `--load` is a contradiction the CLI rejects up front (exit 2) rather than silently picking one.

**Q: `error: MSSQL_PASSWORD environment variable is required for --load`.**
A: Set it before invoking, same as the [loader](loader.md#prerequisites): `export MSSQL_PASSWORD=...`. Checked once up front for the whole run, before any type's stages execute.

**Q: A type shows `NEEDS REVIEW` in the summary and load never ran for it.**
A: Normalize exited `1` or `3` (unresolved mapping items or first-run scaffold) for that type, which halts that type's remaining stages — load included. Resolve it the normal way: edit `normalize/mappings/<type>.yaml` and re-run, or use `taxi-curate-mappings` if you want it auto-resolved.

**Q: One type's `LOAD ERROR` seems to have skipped load for every type after it.**
A: That's `abort_run` firing — a loader exit `2` (connection/config error, or a `TypeMappingError`) is treated as likely to recur identically for every subsequent type's load, so the orchestrator stops attempting the load stage for the rest of the run rather than repeating a doomed connection attempt N times. Download and normalize still run normally for the remaining types.

**Q: `taxi-curate-mappings` raised `non-executable cast(s) need manual mapping`.**
A: A column changed from a string type to a numeric/temporal type (or similarly incompatible family) between historical files — casting it automatically could crash at runtime on real data (e.g. a `payment_type` column that was `'CASH'`/`'CREDIT'` text becoming a numeric code). This needs a human decision (a `value_maps` entry or an explicit drop), so the tool refuses to auto-accept it. Add the mapping by hand in `normalize/mappings/<type>.yaml` and re-run `normalize` directly.

**Q: Where do I find what `taxi-curate-mappings` actually changed?**
A: `<mappings-dir>/CURATION-REPORT.md`, regenerated on every run — it lists every lossy cast and data-loss drop that needs verification, plus every auto-accepted rename with its detection confidence.
