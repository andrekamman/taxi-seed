# Exit codes

Every CLI in this repo follows the same convention: `0` on success, non-zero on specific failure classes documented per-tool. Scripts consuming these tools — a CI job, a cron wrapper, a Makefile, a shell one-liner — can branch on the exit code without parsing stderr.

The tables below list the *emitted* codes. Codes are stable and part of the tools' contract: changing one is a breaking change and requires a version bump.

## Downloader (`taxi-download`)

`taxi-download` is the Python console script (`downloader/src/taxi_download/cli.py`); it classifies every CloudFront response before choosing to retry, skip, or back off, and reports one summary line per requested type (`<type>: downloaded N, gave up on M`).

| Code | Meaning | Suggested action |
|---|---|---|
| 0 | Every requested type either downloaded successfully or was already present locally (`downloaded == 0` with `gaveup == 0` also counts as success — nothing to do). | None. Idempotent success — safe to re-run. |
| 2 | At least one requested type finished with `downloaded == 0` and `gaveup > 0` (every candidate file for that type hit a persistent rate-limit / WAF block or was otherwise unobtainable), or an argument error (unknown flag, invalid data type). | Re-run later — CloudFront WAF cooldowns can outlast a single retry ladder — or retry from a different network. For argument errors, run with `--help`. |

There is no exit `1` and no `130`-on-Ctrl-C special case documented for this tool beyond the standard shell conventions below.

### Rate-limit / WAF handling

CloudFront responds to requests in several distinguishable ways:

- **Existing file** — HTTP 200 with a body that begins with the parquet magic bytes `PAR1`.
- **Not yet published** — HTTP 403 with an S3-style `AccessDenied` XML body (or a 404).
- **WAF / rate-limit block** — HTTP 403 with an HTML "The request could not be satisfied" page, or a direct HTTP 429 / 503.

On a rate-limit classification, `taxi-download` backs off and retries; if every attempt for a type is exhausted with zero successful downloads, that type reports `gaveup > 0` and the overall exit code becomes 2.

## Normalize (`uv run normalize`)

| Code | Meaning | Suggested action |
|---|---|---|
| 0 | Success — all specified data types normalized (or all outputs were already present and skipped). | None. |
| 1 | Mapping incomplete — one or more unresolved items reported. The mapping YAML has been amended in place with new SUGGESTED/TODO entries for every unresolved column. | Review the amended `normalize/mappings/<type>.yaml`, uncomment the SUGGESTED lines you accept, fill in `ack_date:` on TODO blocks, re-run. |
| 2 | Configuration error — mapping failed to load (malformed YAML), `target:` file not found under `raw/<type>/`, or a first-run bootstrap analysis error. | Fix the reported issue: validate the YAML, bump `target:` to a file that actually exists, or check the raw-data path. |
| 3 | First run — no mapping YAML existed for this type. A scaffold has been generated at `normalize/mappings/<type>.yaml` from the raw data. | Review the scaffold, uncomment SUGGESTED renames you accept, fill in `ack_date:` for TODOs, re-run. |

A missing `raw/<type>/` directory is **not** a configuration error — it's treated as "nothing to normalize yet," prints `<type>: no raw files at <raw_dir>, skipping`, and exits **0** for that type.

### Multi-type aggregation

When invoked with no argument (`uv run normalize`), the tool runs all four data types in turn and returns the **highest** exit code observed. A mixed run — e.g. `yellow` succeeds (0), `fhvhv` needs edits (1) — exits `1`. This keeps CI logic simple: any non-zero means "at least one type needs attention", and the code tells you what kind of attention.

The ordering used by exit-code precedence, from lowest to highest: `0 → 1 → 2 → 3`. A first-run-plus-mapping-error scenario therefore exits `3` (the more actionable state), not `2`.

### CI recipes

```bash
# Fail the CI job on any non-zero — the strictest posture.
uv run normalize || exit $?

# Treat exit 3 (first run) as expected on a fresh clone; anything else fails.
uv run normalize
code=$?
if [ "$code" != "0" ] && [ "$code" != "3" ]; then exit "$code"; fi
```

## Schema-drift (`uv run schema-drift`)

| Code | Meaning | Suggested action |
|---|---|---|
| 0 | Report generated to stdout (or to `--output <file>`). | None. |
| 1 | `--data-dir <path>` does not exist. Message: `Error: Data directory '<path>' does not exist.` | Check the path. Default is `raw/` relative to CWD; pass `--data-dir` explicitly if your mirror lives elsewhere. |
| 2 | Argument error (argparse default). Unknown flag, missing required value, invalid `--types` entry, etc. | Run with `--help` to see the flag list. |

Schema-drift itself does not raise on missing columns, mismatched types, or ambiguous renames — those are reported *in* the output, not in the exit code. If you want a CI job that fails when new drift appears, diff the report against a checked-in baseline; the exit code alone will not signal it.

## Loader (`taxi-load`)

| Code | Meaning | Suggested action |
|---|---|---|
| 0 | Every requested type either loaded successfully, was a no-op (`--dry-run`), or had no parquet under the resolved input dir (skipped as "nothing to load yet"). | None. |
| 1 | At least one type failed mid-load (a `duckdb.Error` or a loader-internal error during `COPY`/DDL execution) — a partial load; other types may have still succeeded. | Re-run — the reconcile logic picks up from the manifest's actual state. |
| 2 | An identifier/config error (invalid `--schema` or `--database`), a missing `MSSQL_PASSWORD`, a connection/provisioning failure (installing the `mssql` extension, an unexpected extension version, attaching the database, creating the database/schema), or a per-type `TypeMappingError` (a DuckDB column type with no SQL Server equivalent). | Check the stderr message; set `MSSQL_PASSWORD`, fix the identifier, or add the missing type mapping. |

When multiple types are processed in one invocation (no positional `data_type`), the overall exit code is the **maximum** across all per-type outcomes — a `TypeMappingError` on one type (2) outranks a mid-load failure on another (1), which outranks types that loaded cleanly (0). See [Loader → Exit codes](../guides/loader.md#exit-codes) for the full writeup.

## Orchestrator (`taxi-run`)

| Code | Meaning | Suggested action |
|---|---|---|
| 0 | Every planned stage for every type completed cleanly — a genuinely clean run. | None. |
| 1 | No stage failed outright, but at least one type stopped at `needs_review` (normalize found unresolved mapping items). | Resolve the mapping YAML for the flagged type(s) and re-run. |
| 2 | At least one stage classified as `failed`, `partial`, or `conn_error` (download failed, normalize hit a real config error, or the loader reported a partial load / connection-config error / `TypeMappingError`). Also returned immediately for `--download-only` combined with `--load`, or for a missing `MSSQL_PASSWORD` when `--load` is set — before any stage runs. | Check the per-type summary printed at the end of the run; fix the failing stage and re-run. |

Precedence is **2 > 1 > 0**: `pipeline.overall_exit_code` returns 2 if any stage outcome is in the failure set (`failed`, `partial`, `conn_error`), else 1 if any is `needs_review`, else 0. Note that a loader exit of `1` (partial load) classifies as `partial`, which **is** counted among the exit-2 statuses at the orchestrator level — so a partial load on any type escalates the whole run's exit code to 2, even though that type's own stage exit was 1. See [Orchestrator → Exit codes](../guides/orchestrator.md#exit-codes) for the full writeup.

## Mapping curator (`taxi-curate-mappings`)

| Code | Meaning | Suggested action |
|---|---|---|
| 0 | Every requested type curated cleanly, or was skipped because `<raw-dir>/<type>` doesn't exist (not an error). | None. |
| 2 | At least one type raised (no file matching the target name, a non-executable cast needing manual mapping, or unresolved items remaining after 8 rounds); argument errors also exit 2. | Read the stderr message for the blocking column/type and resolve it by hand in the mapping YAML. |

Multiple types aggregate via `max()`, same as the other multi-type tools in this repo. See [Orchestrator → `taxi-curate-mappings`](../guides/orchestrator.md#taxi-curate-mappings) for the full writeup.

## Standard shell conventions

The tools in this repo do not invent exit codes beyond what's documented above; standard shell conventions still apply on top:

- `130` — SIGINT (Ctrl-C). Bash reports this for any tool killed by SIGINT — not just the downloader.
- `137` — SIGKILL (128 + 9). Often means an OOM kill; the Linux OOM killer sends SIGKILL. Rerun with a smaller dataset (`--sample` for normalize) or on a machine with more RAM.
- `143` — SIGTERM (128 + 15). A container orchestrator or `systemd` sent a graceful-shutdown signal. Increase the shutdown timeout or split the work into smaller batches.

None of these are emitted by the tools themselves — they come from the surrounding process manager. If you see one, the tool never got to run its own exit logic.

## Getting the exit code in your shell

For a quick one-liner check:

```bash
uv run normalize yellow; echo "exit=$?"
```

For a scripted decision, capture immediately (a subsequent command overwrites `$?`):

```bash
uv run normalize yellow
code=$?
case "$code" in
    0) echo "normalized" ;;
    1) echo "needs edits — see mapping YAML" ;;
    2) echo "config error — check paths/YAML" ;;
    3) echo "first run — review scaffold" ;;
    *) echo "unexpected exit $code" ;;
esac
```
