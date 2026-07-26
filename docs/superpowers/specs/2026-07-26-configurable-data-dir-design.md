# Configurable data directory (`--data-dir`) — design spec (2026-07-26)

**Goal:** Let every pipeline stage write/read its data under a caller-chosen base directory instead of
the hardcoded repo-relative `raw` / `raw-normalized`. A single `--data-dir <BASE>` flag on each tool
derives the fixed subfolders (`BASE/raw`, `BASE/raw-normalized`); the orchestrator threads one
`--data-dir` through all stages so `taxi-run --data-dir /taxi/data` downloads to `/taxi/data/raw`,
normalizes to `/taxi/data/raw-normalized`, and loads from there.

---

## Decisions locked (from brainstorming)

- **One base `--data-dir`, derived subfolders** — not per-part input/output flags. Keep it simple.
- **Keep the subfolder names** `raw` and `raw-normalized` (do not rename to `normalized`).
- **`--data-dir` on all four tools** (downloader, normalizer, loader, orchestrator) for a uniform
  interface; the loader keeps `--input-dir` as an explicit lower-level override.
- **Fully backward-compatible:** every new flag defaults to today's behavior.
- **Mappings are code, not data:** `normalize/mappings/<type>.yaml` stays resolved from the repo/CWD,
  NOT under `--data-dir`.

---

## Current state (facts, from code)

- **Downloader** `downloader/download_taxi_data.sh`: output dir = `$OUTPUT_DIR` if set, else
  `<script_dir>/../raw` (lines 70-77). **No CLI flag** for location. Layout
  `<output_dir>/<type>/<year>/<file>`. Args today: positional `TYPE`, `--recent [N] [TYPE]`.
- **Normalizer** `normalize/src/taxi_normalize/cli.py`: `main(argv=None)`; argparse has only
  `data_type` + `--sample`. `_normalize_one` hardcodes (relative to CWD):
  `raw_dir = Path("raw")/data_type`, `mapping_path = Path("normalize")/"mappings"/f"{data_type}.yaml"`,
  `out_dir = Path("raw-normalized")/data_type` (lines 55-58).
- **Loader** `loader/src/taxi_loader/cli.py`: `--input-dir` (default `"raw-normalized"`, lines 41-42);
  `discover_month_files` globs `Path(input_dir)/data_type` rglob `*.parquet` (lines 52-65).
- **Orchestrator** `orchestrator/src/taxi_orchestrate/{cli,stages}.py`: `--data-dir` (default None) →
  `root = data_dir or find_repo_root(cwd)` (cli.py:85); every stage runs via `stages.run(cmd, cwd=root)`
  (stages.py:58-62). `build_download_cmd(root, …)` locates the script at `root/downloader/…` but passes
  **no** output location — the script writes to `<script_dir>/../raw`, so **`--data-dir` does NOT
  redirect downloads today** (the gap this feature fixes). `build_normalize_cmd` passes only
  `data_type`/`--sample` (relies on cwd). `build_load_cmd(t, INPUT_DIR="raw-normalized", conn)` passes
  `--input-dir raw-normalized`.
- **No shared path constant** — `"raw"`/`"raw-normalized"` literals are repeated per component. (The
  separate `curate.py` and `schema-drift` tools already have their own `--raw-dir` flags — precedent,
  but out of scope here.)

---

## Design

### Common rule
`--data-dir DIR` names a **base** directory. Derived, fixed subfolders under it:
`DIR/raw/<type>/<year>/…` (downloaded + raw input) and `DIR/raw-normalized/<type>/…` (normalized
output/loader input). Default `DIR` reproduces today's behavior per tool (see each).

### A. Downloader (`download_taxi_data.sh`)
- Add a `--data-dir DIR` option to the arg parser (a flag taking a value).
- Output-dir precedence: `$OUTPUT_DIR` (explicit full path, if set) → `$DATA_DIR/raw` (if `--data-dir`
  given) → `<script_dir>/../raw` (default, unchanged). Update the "Files saved to" echoes to reflect
  the resolved dir.
- Layout under the resolved output dir is unchanged.

### B. Normalizer (`taxi_normalize/cli.py`)
- Add `--data-dir` (default `"."`).
- `raw_dir = Path(data_dir)/"raw"/data_type`; `out_dir = Path(data_dir)/"raw-normalized"/data_type`.
- `mapping_path` **unchanged** (`Path("normalize")/"mappings"/f"{data_type}.yaml"` relative to CWD).
- Default `"."` = today's behavior (`raw`/`raw-normalized` in CWD).

### C. Loader (`taxi_loader/cli.py`)
- Add `--data-dir` (default `None`); change `--input-dir` default to `None`.
- Resolve the effective input dir (precedence: explicit `--input-dir` > `--data-dir`-derived > default):
  ```
  if args.input_dir is not None:      input_dir = args.input_dir
  elif args.data_dir is not None:     input_dir = str(Path(args.data_dir) / "raw-normalized")
  else:                               input_dir = "raw-normalized"
  ```
  Put this in a small helper (e.g. `resolve_input_dir(args)`) so it is unit-testable. Discovery logic
  unchanged. Default (neither flag) = `"raw-normalized"`, today's behavior.

### D. Orchestrator (`cli.py` + `stages.py`)
- Compute both roots explicitly:
  - `repo_root = find_repo_root(Path.cwd())` — where the downloader script + `normalize/mappings/` live.
  - `data_dir = Path(args.data_dir).resolve() if args.data_dir else repo_root`.
- Run **every** stage with `cwd = repo_root` (so the downloader script path and mappings resolve),
  and **pass `--data-dir <data_dir>` explicitly to every stage**:
  - `build_download_cmd`: still `bash <repo_root>/downloader/download_taxi_data.sh …`, now appended
    with `--data-dir <data_dir>`.
  - `build_normalize_cmd`: append `--data-dir <data_dir>`.
  - `build_load_cmd`: pass `--data-dir <data_dir>` (loader derives `<data_dir>/raw-normalized`);
    the `INPUT_DIR="raw-normalized"` constant + explicit `--input-dir` are dropped from the load cmd.
- Update the `--data-dir` help text to describe the derived-subfolder behavior.
- **Default (no `--data-dir`):** `data_dir == repo_root`, `cwd == repo_root` → download/normalize/load
  all operate on `repo_root/raw` and `repo_root/raw-normalized`, identical to today.

---

## Backward compatibility & test impact

- All tool defaults reproduce current behavior; standalone runs and most tests are unaffected.
- **Tests that change** (expected, part of this feature):
  - `tests/taxi_loader/test_cli.py` — the `--input-dir` default is now `None`; assert the **resolved**
    input dir (via `resolve_input_dir`) instead of `ns.input_dir == "raw-normalized"`. Add cases for
    `--data-dir` deriving `<dir>/raw-normalized` and `--input-dir` overriding it.
  - `tests/taxi_orchestrate/test_stages.py` — `build_load_cmd` now emits `--data-dir <dir>` instead of
    `--input-dir raw-normalized`; `build_normalize_cmd`/`build_download_cmd` now include `--data-dir`.
    Update assertions accordingly.
  - `tests/e2e/test_pipeline_e2e.py` — still passes (mappings now read from the repo via `cwd=repo_root`
    rather than the workroot copy; the generated `workroot/raw` is read via `--data-dir`). The
    `shutil.copytree(REPO_MAPPINGS, …)` becomes redundant; **verify the e2e test still passes** against
    SQL Server, and optionally drop the now-unneeded copy.
- Normalizer tests (which `chdir` and use CWD-relative `raw`) are unaffected — `--data-dir` default `"."`
  keeps CWD-relative behavior.

## Testing strategy

- **Downloader:** it has no tests today; add a focused test (bash invoked via subprocess, or assert the
  resolved `output_dir` logic) proving `--data-dir DIR` writes under `DIR/raw` and that the default and
  `OUTPUT_DIR` paths are unchanged. Keep it small (do not actually download — e.g. `--recent 0`/dry
  path, or test only the directory-resolution branch).
- **Normalizer:** a test that generates raw under a temp `--data-dir` and asserts output lands in
  `<data-dir>/raw-normalized` while mappings are still read from the repo. Reuse existing fixtures.
- **Loader:** unit tests for `resolve_input_dir` covering all three precedence branches.
- **Orchestrator:** `build_*_cmd` unit tests asserting each stage receives `--data-dir <data_dir>` and
  `cwd == repo_root`; a `--dry-run` end-to-end that prints the planned commands with a custom
  `--data-dir`.
- **Integration:** the SQL-Server e2e (`taxi-run --skip-download --load --data-dir <workroot>`) still
  loads the expected row counts.
- Full suite stays green (203 passed / 11 skipped baseline, adjusted for the updated assertions).

## Out of scope

- Renaming `raw` / `raw-normalized`.
- Per-part / per-stage separate input+output overrides (explicitly rejected — one base dir only).
- Adding `--data-dir` to the separate `curate.py` / `schema-drift` tools (they already have `--raw-dir`;
  unifying them is a possible later cleanup, not this feature).
- A shared path-constant module / centralizing the `raw`/`raw-normalized` literals (nice-to-have; the
  derivation lives in each tool for now).
- Making the mappings directory configurable (mappings stay repo-relative).

## Notes for the plan

- The downloader bash arg loop must handle `--data-dir <value>` alongside the existing positional
  `TYPE` and `--recent [N] [TYPE]` parsing — get the ordering right so `--data-dir` can appear in any
  position the orchestrator emits it.
- Keep `resolve_input_dir` pure (takes the parsed args / the two values) for easy unit testing.
- Confirm the orchestrator `--dry-run` path prints the new `--data-dir` in each command.
