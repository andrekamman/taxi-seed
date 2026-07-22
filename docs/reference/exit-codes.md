# Exit codes

Every CLI in this repo follows the same convention: `0` on success, non-zero on specific failure classes documented per-tool. Scripts consuming these tools — a CI job, a cron wrapper, a Makefile, a shell one-liner — can branch on the exit code without parsing stderr.

The tables below list the *emitted* codes. Codes are stable and part of the tools' contract: changing one is a breaking change and requires a version bump.

## Downloader (`download_taxi_data.sh`)

The downloader is a bash script; codes are set with `exit N`. It never uses `set -e`, so intermediate curl failures are handled inline and do not surface as a raw curl exit code.

| Code | Meaning | Suggested action |
|---|---|---|
| 0 | All requested downloads succeeded or were already present locally. `--help` also exits 0. | None. Idempotent success — safe to re-run. |
| 1 | Unknown option, unrecognized data type, WSL confirmation prompt declined, or persistent rate-limit / WAF block after 3 backoff attempts (the 5 min → 15 min → 60 min ladder was exhausted). | If unknown-option: run with `--help`. If WSL prompt: re-run with `OUTPUT_DIR` set to a `/mnt/c/...` path (or accept the prompt). If backoff-exhaustion: wait several hours — WAF cooldowns often exceed the 60 min ladder cap — and retry from a different network or via a VPN. |
| 130 | User interrupted with Ctrl-C (standard shell convention: 128 + SIGINT). | None. |

### Rate-limit / WAF handling

CloudFront responds to requests in several distinguishable ways:

- **Existing file** — HTTP 200/206 with a body that begins with the parquet magic bytes `PAR1`.
- **Not yet published** — HTTP 403 with an S3-style `AccessDenied` XML body (or a 404).
- **WAF / rate-limit block** — HTTP 403 with an HTML "The request could not be satisfied" page, or a direct HTTP 429 / 503.

The downloader classifies every response before choosing to retry, skip, or back off. On a rate-limit classification, an exponential ladder kicks in: sleep 5 min, 15 min, 60 min, then abort with exit `1`. A single successful download resets the ladder to 0.

### Interactive vs non-interactive

The WSL confirmation prompt is only issued when stdin is a TTY (`[ -t 0 ]`). In non-interactive contexts (cron, CI, `nohup`, subshell redirections, `docker run` without `-it`) the script proceeds without prompting and writes to the resolved `output_dir` unchanged. This lets you script the downloader without worrying about the prompt blocking your job.

## Normalize (`uv run normalize`)

| Code | Meaning | Suggested action |
|---|---|---|
| 0 | Success — all specified data types normalized (or all outputs were already present and skipped). | None. |
| 1 | Mapping incomplete — one or more unresolved items reported. The mapping YAML has been amended in place with new SUGGESTED/TODO entries for every unresolved column. | Review the amended `normalize/mappings/<type>.yaml`, uncomment the SUGGESTED lines you accept, fill in `ack_date:` on TODO blocks, re-run. |
| 2 | Configuration error — missing raw data directory, malformed mapping YAML, or `target:` file not found under `raw/<type>/`. | Fix the reported issue: check the raw-data path, validate the YAML, or bump `target:` to a file that actually exists. |
| 3 | First run — no mapping YAML existed for this type. A scaffold has been generated at `normalize/mappings/<type>.yaml` from the raw data. | Review the scaffold, uncomment SUGGESTED renames you accept, fill in `ack_date:` for TODOs, re-run. |

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

## K6-preprocess (`uv run k6-preprocess`)

| Code | Meaning | Suggested action |
|---|---|---|
| 0 | Success — DDL, chunks, `test.js`, and `manifest.json` written to `--output` (default `k6-loadtest/output/`). | Apply the DDL and run K6. |
| 1 | Any exception raised during preprocessing. Common causes: missing `--config` file, malformed YAML, config validation failure (missing required section/field, unknown `mode:`, `workload` percentages that don't sum to 100, `path:` glob that matches no files, invalid `think_time` duration string). The failing message is printed to stderr as `Error: {message}`. | Read the stderr message; it names the failing field or the missing file. Fix and re-run. |
| 2 | Argument error (argparse default). Missing the required `--config` flag, unknown option. | Run with `--help`. |

### Common exit-1 messages

The messages below all correspond to exit `1` and come from `ConfigError` in `k6_loadtest/config.py`:

- `Missing required section: {data_sources|targets|scenarios}` — the top-level YAML is missing one of the three required blocks.
- `Data source {name!r}: mode must be 'parquet' or 'synthetic', got {mode!r}` — unknown `mode:` value.
- `Data source {name!r}: missing required field {field!r}` — a required per-mode field (`columns`, `key_columns`, `path`) is absent.
- `Data source {name!r}: synthetic column {col!r} must have a 'type' field` — a synthetic column entry has no `type:`.
- `Data source {name!r}: key_column {kc!r} not found in columns` — `key_columns:` references a name that is not declared under `columns:`.
- `Data source {name!r}: path {…} matches no files` — the parquet glob resolved to zero files at preprocess time.
- `Target {name!r}: missing required field {field!r}` — a target is missing one of `host`, `port`, `database`, `username`, `password`, `table`.
- `Scenario {name!r}: target {target!r} not found in targets` / `data_source {…} not found in data_sources` — a scenario references a name that does not exist at the top level.
- `Scenario {name!r}: ordering must be 'parallel' or 'sequential', got {ordering!r}` — unknown `ordering:` value.
- `Scenario {name!r}: workload percentages must sum to 100, got {total}` — the `insert`/`update`/`delete` mix doesn't add up.
- `Scenario {name!r}: missing required field 'k6'` — no `k6:` block, so there's no executor to generate.
- `Invalid duration format: {value!r} (expected e.g. '200ms' or '1s')` — a `think_time.min` or `think_time.max` value doesn't match the duration grammar.

Every message is prefixed with `Error: ` when printed to stderr. Grepping for `Error: ` in your CI log surfaces preprocess failures quickly.

## Standard shell conventions

The tools in this repo do not invent exit codes beyond what's documented above; standard shell conventions still apply on top:

- `130` — SIGINT (Ctrl-C). Bash reports this for any tool killed by SIGINT — not just the downloader.
- `137` — SIGKILL (128 + 9). Often means an OOM kill; the Linux OOM killer sends SIGKILL. Rerun with a smaller dataset (`--sample` for normalize, `max_rows` for k6-preprocess parquet mode) or on a machine with more RAM.
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
