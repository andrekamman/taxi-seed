# Configurable `--data-dir` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `--data-dir <BASE>` flag to the downloader, normalizer, loader, and orchestrator that derives the fixed `BASE/raw` and `BASE/raw-normalized` subfolders, so the whole pipeline can read/write under a caller-chosen base directory. Fully backward-compatible.

**Architecture:** Each tool gains a `--data-dir` flag defaulting to today's behavior. The orchestrator computes `repo_root` (for the downloader script + `normalize/mappings/`) and `data_dir` separately, runs every stage with `cwd = repo_root`, and passes `--data-dir <data_dir>` explicitly to each stage — fixing the current gap where `--data-dir` did not redirect downloads.

**Tech Stack:** Python (argparse), Bash, pytest, `uv`.

## Global Constraints

_Every task's requirements implicitly include this section._

- **Backward-compatible:** every new flag defaults to today's behavior. Default runs and paths are unchanged (`raw` / `raw-normalized` relative to CWD / repo root).
- **Keep the subfolder names** `raw` and `raw-normalized`. Derivation is always `<base>/raw` and `<base>/raw-normalized`.
- **Mappings stay repo-relative:** `normalize`'s `Path("normalize")/"mappings"/f"{type}.yaml"` resolution is NOT changed and NOT moved under `--data-dir`.
- **`--data-dir` on all four tools**; the loader keeps `--input-dir` as an explicit override (precedence: `--input-dir` > `--data-dir` > default `"raw-normalized"`). Downloader precedence: `OUTPUT_DIR` env > `--data-dir` > script-relative `../raw`.
- **No new dependencies.**
- No real network in tests; no committed `.parquet`.

---

## Task 1: Downloader `--data-dir` (bash)

**Files:**
- Modify: `downloader/download_taxi_data.sh`
- Test: `tests/downloader/test_output_dir.py` (new)

**Interfaces:**
- Produces: the script accepts `--data-dir DIR` and writes to `DIR/raw/<type>/<year>/…`.

- [ ] **Step 1: Write the failing test**

```python
# tests/downloader/test_output_dir.py
"""The downloader resolves its output dir from --data-dir / OUTPUT_DIR without downloading."""
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "downloader" / "download_taxi_data.sh"


def _net_shim(tmp_path):
    """A PATH prefix whose wget/curl/aria2c are instant no-ops, so even if the
    walker reaches a download call, nothing hits the network."""
    binp = tmp_path / "shimbin"
    binp.mkdir()
    for tool in ("wget", "curl", "aria2c"):
        p = binp / tool
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)
    return f"{binp}{os.pathsep}{os.environ['PATH']}"


def _run(tmp_path, *args, **env):
    e = {**os.environ, "PATH": _net_shim(tmp_path), **env}
    # --recent 0 => zero download iterations; the shim covers any stray call.
    return subprocess.run(
        ["bash", str(SCRIPT), "--recent", "0", "yellow", *args],
        env=e, capture_output=True, text=True, timeout=60,
    )


def test_data_dir_creates_base_raw(tmp_path):
    base = tmp_path / "elsewhere"
    r = _run(tmp_path, "--data-dir", str(base))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (base / "raw").is_dir()


def test_output_dir_env_overrides_data_dir(tmp_path):
    base = tmp_path / "base"
    custom = tmp_path / "custom"
    r = _run(tmp_path, "--data-dir", str(base), OUTPUT_DIR=str(custom))
    assert r.returncode == 0, r.stdout + r.stderr
    assert custom.is_dir()                 # OUTPUT_DIR wins
    assert not (base / "raw").exists()     # --data-dir ignored when OUTPUT_DIR set
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/downloader/test_output_dir.py -q`
Expected: FAIL — the script rejects `--data-dir` today (`Unknown option: --data-dir`, exit 1).

- [ ] **Step 3: Add `--data-dir` parsing and precedence to the script**

In `downloader/download_taxi_data.sh`:

(a) After the flag-init block (near line 11), add an initializer:
```bash
data_dir_opt=""         # base dir; when set, output_dir = $data_dir_opt/raw
```

(b) In the `while [[ $# -gt 0 ]]` arg loop, add a `--data-dir` case (e.g. right before `-h|--help`):
```bash
        --data-dir)
            shift
            if [[ $# -gt 0 ]]; then
                data_dir_opt="$1"
                shift
            else
                echo "--data-dir requires a directory argument" >&2
                exit 1
            fi
            ;;
```

(c) Replace the output-dir resolution block (currently lines ~70-77) with the three-way precedence:
```bash
# Output directory precedence: OUTPUT_DIR (explicit full path) > --data-dir/raw
# > raw/ resolved relative to this script (so it works from any CWD).
if [ -n "$OUTPUT_DIR" ]; then
    output_dir="$OUTPUT_DIR"
elif [ -n "$data_dir_opt" ]; then
    output_dir="$data_dir_opt/raw"
else
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    output_dir="$script_dir/../raw"
fi
```

(d) Add a `--data-dir` line to the `--help` usage text:
```
  ./download_taxi_data.sh --data-dir DIR            Write to DIR/raw (instead of ./raw)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/downloader/test_output_dir.py -q`
Expected: PASS (both cases). If `--recent 0` still attempts a network call, the shim already no-ops it; the assertions only require the directory to be created.

- [ ] **Step 5: Commit**

```bash
git add downloader/download_taxi_data.sh tests/downloader/test_output_dir.py
git commit -m "feat(downloader): --data-dir base flag (writes DIR/raw)"
```

---

## Task 2: Normalizer `--data-dir`

**Files:**
- Modify: `normalize/src/taxi_normalize/cli.py`
- Test: `tests/taxi_normalize/test_data_dir.py` (new)

**Interfaces:**
- Consumes: nothing new.
- Produces: `normalize --data-dir DIR` reads `DIR/raw/<type>`, writes `DIR/raw-normalized/<type>`; mappings still read from `normalize/mappings/` relative to CWD.

- [ ] **Step 1: Write the failing test**

```python
# tests/taxi_normalize/test_data_dir.py
"""normalize derives raw/raw-normalized under --data-dir (mappings stay repo-relative)."""
from taxi_normalize.cli import main


def test_data_dir_derives_raw_input_path(tmp_path, capsys):
    # No raw files under the given base -> normalize reports the derived path and skips.
    base = tmp_path / "somewhere"
    rc = main(["yellow", "--data-dir", str(base)])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"no raw files at {base / 'raw' / 'yellow'}" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/taxi_normalize/test_data_dir.py -q`
Expected: FAIL — `--data-dir` is not a recognized argument (argparse SystemExit / exit 2).

- [ ] **Step 3: Add `--data-dir` and thread it into path construction**

In `normalize/src/taxi_normalize/cli.py`:

(a) Add the argument after `--sample` (after line 43):
```python
    parser.add_argument(
        "--data-dir", default=".",
        help="Base dir for data: reads <data-dir>/raw/<type>, writes "
             "<data-dir>/raw-normalized/<type>. Default: current directory.",
    )
```

(b) Pass it through in `main` (change line 49):
```python
        rc = _normalize_one(data_type, args.sample, args.data_dir)
```

(c) Update the `_normalize_one` signature and the two data paths (lines 55-58):
```python
def _normalize_one(data_type: str, sample: str, data_dir: str) -> int:
    raw_dir = Path(data_dir) / "raw" / data_type
    mapping_path = Path("normalize") / "mappings" / f"{data_type}.yaml"
    out_dir = Path(data_dir) / "raw-normalized" / data_type
```
(Leave `mapping_path` exactly as-is — mappings are repo-relative, not under `--data-dir`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/taxi_normalize/test_data_dir.py tests/taxi_normalize -q`
Expected: the new test PASSES and the existing normalize suite stays green (its tests `chdir` and use the default `--data-dir "."`, so `raw`/`raw-normalized` resolve against CWD exactly as before).

- [ ] **Step 5: Commit**

```bash
git add normalize/src/taxi_normalize/cli.py tests/taxi_normalize/test_data_dir.py
git commit -m "feat(normalize): --data-dir base flag for raw/raw-normalized"
```

---

## Task 3: Loader `--data-dir` + `resolve_input_dir`

**Files:**
- Modify: `loader/src/taxi_loader/cli.py`
- Modify: `tests/taxi_loader/test_cli.py`

**Interfaces:**
- Produces: `resolve_input_dir(input_dir, data_dir) -> str` (importable from `taxi_loader.cli`); `taxi-load --data-dir DIR` reads `DIR/raw-normalized`; `--input-dir` overrides.

- [ ] **Step 1: Update the tests (RED)**

In `tests/taxi_loader/test_cli.py`:

(a) Change the import line to also import the resolver:
```python
from taxi_loader.cli import discover_month_files, main, parse_args, resolve_input_dir
```

(b) Replace the `input_dir` assertion in `test_parse_args_defaults` (line 15) with:
```python
    assert ns.input_dir is None
    assert ns.data_dir is None
```

(c) Add a new test for the resolver:
```python
def test_resolve_input_dir_precedence():
    # explicit --input-dir wins
    assert resolve_input_dir("/x/norm", "/base") == "/x/norm"
    # else derive from --data-dir
    assert resolve_input_dir(None, "/base") == "/base/raw-normalized"
    # else default
    assert resolve_input_dir(None, None) == "raw-normalized"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/taxi_loader/test_cli.py -q`
Expected: FAIL — `resolve_input_dir` doesn't exist (ImportError) / `ns.input_dir` is still `"raw-normalized"`.

- [ ] **Step 3: Implement in the loader CLI**

In `loader/src/taxi_loader/cli.py`:

(a) In `parse_args`, change the `--input-dir` default to `None` and add `--data-dir` (replace lines 41-42):
```python
    p.add_argument("--input-dir", default=None,
                   help="reads <input-dir>/<type>/<year>/*.parquet "
                        "(overrides --data-dir; default: raw-normalized)")
    p.add_argument("--data-dir", default=None,
                   help="base dir; reads <data-dir>/raw-normalized (unless --input-dir given)")
```

(b) Add the resolver (module-level, e.g. just after `parse_args`):
```python
def resolve_input_dir(input_dir, data_dir) -> str:
    if input_dir is not None:
        return input_dir
    if data_dir is not None:
        return str(Path(data_dir) / "raw-normalized")
    return "raw-normalized"
```
(`Path` is already imported.)

(c) In `main`, resolve once and pass it into the per-type loop (change the `_process_type` call at line 170 to use the resolved dir). Right after `args = parse_args(argv)` (line 127) add:
```python
    input_dir = resolve_input_dir(args.input_dir, args.data_dir)
```
and change line 170 from `args.input_dir` to `input_dir`:
```python
                _process_type(conn, cfg, data_type, input_dir,
                              args.flush_rows, args.full_refresh, args.dry_run,
                              attached)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/taxi_loader/test_cli.py tests/taxi_loader -q`
Expected: PASS. The existing integration tests pass `--input-dir` explicitly, which still wins.

- [ ] **Step 5: Commit**

```bash
git add loader/src/taxi_loader/cli.py tests/taxi_loader/test_cli.py
git commit -m "feat(loader): --data-dir base flag; --input-dir overrides"
```

---

## Task 4: Orchestrator wiring (thread `--data-dir` through every stage)

**Files:**
- Modify: `orchestrator/src/taxi_orchestrate/stages.py`
- Modify: `orchestrator/src/taxi_orchestrate/cli.py`
- Modify: `tests/taxi_orchestrate/test_stages.py`

**Interfaces:**
- Consumes: the `--data-dir` flags from Tasks 1-3.
- Produces: `build_download_cmd(repo_root, data_type, recent, data_dir)`, `build_normalize_cmd(data_type, sample, data_dir)`, `build_load_cmd(data_type, conn, data_dir)` — each emits `--data-dir <data_dir>`; `taxi-run --data-dir DIR` runs the whole pipeline under `DIR`.

- [ ] **Step 1: Update the stage-builder tests (RED)**

In `tests/taxi_orchestrate/test_stages.py`, update the import + the affected tests to the new signatures:

```python
from taxi_orchestrate.stages import (
    LoadConn, build_download_cmd, build_load_cmd, build_normalize_cmd, run,
)


def test_download_full_all_types():
    cmd = build_download_cmd(Path("/repo"), None, None, Path("/data"))
    assert cmd[:2] == ["bash", "/repo/downloader/download_taxi_data.sh"]
    assert cmd[cmd.index("--data-dir") + 1] == "/data"


def test_download_full_one_type():
    cmd = build_download_cmd(Path("/repo"), "yellow", None, Path("/data"))
    assert "yellow" in cmd and "--recent" not in cmd
    # data_type stays adjacent to the recent group; --data-dir is appended last
    assert cmd[-2:] == ["--data-dir", "/data"]


def test_download_recent_default_n():
    cmd = build_download_cmd(Path("/repo"), "green", 0, Path("/data"))
    assert "--recent" in cmd
    assert cmd[cmd.index("--recent") + 1] == "green"  # no numeric N inserted
    assert cmd[-2:] == ["--data-dir", "/data"]


def test_download_recent_explicit_n():
    cmd = build_download_cmd(Path("/repo"), "green", 3, Path("/data"))
    i = cmd.index("--recent")
    assert cmd[i + 1] == "3" and cmd[i + 2] == "green"
    assert cmd[-2:] == ["--data-dir", "/data"]


def test_normalize_cmd():
    cmd = build_normalize_cmd("yellow", "50%", Path("/data"))
    assert cmd[:4] == [sys.executable, "-m", "taxi_normalize.cli", "yellow"]
    assert cmd[cmd.index("--sample") + 1] == "50%"
    assert cmd[cmd.index("--data-dir") + 1] == "/data"


def test_normalize_cmd_no_sample():
    cmd = build_normalize_cmd("fhv", None, Path("/data"))
    assert cmd == [sys.executable, "-m", "taxi_normalize.cli", "fhv", "--data-dir", "/data"]


def test_load_cmd_forwards_flags():
    cmd = build_load_cmd("yellow", _conn(host="db1", port=1444, full_refresh=True), Path("/data"))
    assert cmd[:4] == [sys.executable, "-m", "taxi_loader.cli", "yellow"]
    assert cmd[cmd.index("--data-dir") + 1] == "/data"
    assert "--input-dir" not in cmd
    assert cmd[cmd.index("--host") + 1] == "db1"
    assert cmd[cmd.index("--port") + 1] == "1444"
    assert "--full-refresh" in cmd
    assert not any("password" in c.lower() for c in cmd)
```

(Leave `test_run_returns_child_exit_code` / `test_run_passes_extra_env` unchanged.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra test pytest tests/taxi_orchestrate/test_stages.py -q`
Expected: FAIL — the builders don't accept a `data_dir` argument yet.

- [ ] **Step 3: Update the stage builders**

In `orchestrator/src/taxi_orchestrate/stages.py`:

```python
def build_download_cmd(repo_root: Path, data_type: Optional[str],
                       recent: Optional[int], data_dir: Path) -> list[str]:
    cmd = ["bash", str(repo_root / "downloader" / "download_taxi_data.sh")]
    if recent is not None:
        cmd.append("--recent")
        if recent > 0:
            cmd.append(str(recent))
    if data_type:
        cmd.append(data_type)          # keep TYPE adjacent to the recent group
    cmd += ["--data-dir", str(data_dir)]
    return cmd


def build_normalize_cmd(data_type: str, sample: Optional[str], data_dir: Path) -> list[str]:
    cmd = [sys.executable, "-m", "taxi_normalize.cli", data_type]
    if sample:
        cmd += ["--sample", sample]
    cmd += ["--data-dir", str(data_dir)]
    return cmd


def build_load_cmd(data_type: str, conn: LoadConn, data_dir: Path) -> list[str]:
    cmd = [
        sys.executable, "-m", "taxi_loader.cli", data_type,
        "--data-dir", str(data_dir),
        "--host", conn.host,
        "--port", str(conn.port),
        "--database", conn.database,
        "--schema", conn.schema,
        "--user", conn.user,
        "--flush-rows", str(conn.flush_rows),
    ]
    if conn.full_refresh:
        cmd.append("--full-refresh")
    return cmd
```

- [ ] **Step 4: Wire `repo_root` + `data_dir` in the orchestrator CLI**

In `orchestrator/src/taxi_orchestrate/cli.py`:

(a) Remove the now-unused `INPUT_DIR` constant (line 18). Verify nothing else references it: `grep -rn "INPUT_DIR" orchestrator tests` should return no other hits.

(b) Update the `--data-dir` help text (line 37-38):
```python
    p.add_argument("--data-dir", default=None,
                   help="base data dir: download->DIR/raw, normalize->DIR/raw-normalized, "
                        "load from DIR/raw-normalized (default: repo root)")
```

(c) Replace the `root = …` line (85) with the split:
```python
    repo_root = find_repo_root(Path.cwd())
    data_dir = Path(args.data_dir).resolve() if args.data_dir else repo_root
```

(d) Update the dry-run print (line 94):
```python
        print(f"taxi-run plan (repo_root={repo_root}, data_dir={data_dir}); "
              f"stages: {' -> '.join(planned)}")
```

(e) Update the three stage call sites (lines 108-114) to run with `cwd=repo_root` and pass `data_dir`:
```python
            if stage == pipeline.DOWNLOAD:
                rc = stages.run(stages.build_download_cmd(repo_root, t, args.recent, data_dir), repo_root)
            elif stage == pipeline.NORMALIZE:
                rc = stages.run(stages.build_normalize_cmd(t, args.sample, data_dir), repo_root)
            else:  # LOAD
                rc = stages.run(stages.build_load_cmd(t, conn, data_dir), repo_root,
                                extra_env={"MSSQL_PASSWORD": password})
```

- [ ] **Step 5: Run the orchestrator + full unit suite**

Run: `uv run --extra test pytest tests/taxi_orchestrate -q && uv run --extra test pytest -q`
Expected: `tests/taxi_orchestrate` green; full suite green (203 passed / 11 skipped, adjusted for the new tests added in Tasks 1-3 — expect ~206 passed). If any test still references the old `build_*_cmd` signatures or `INPUT_DIR`, fix it.

- [ ] **Step 6: Validate the end-to-end pipeline against SQL Server**

The orchestrator now runs stages with `cwd=repo_root` (mappings from the repo) and `--data-dir` for data. Confirm the SQL-Server e2e still loads correctly:

```bash
docker run -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=Str0ng_Passw0rd!" -p 1433:1433 -d --name taxi-mssql-dd mcr.microsoft.com/mssql/server:2022-latest
MSSQL_PASSWORD=Str0ng_Passw0rd! uv run python scripts/wait_for_mssql.py
MSSQL_PASSWORD=Str0ng_Passw0rd! uv run --extra test pytest tests/e2e/test_pipeline_e2e.py -v
docker rm -f taxi-mssql-dd
```
Expected: all 4 types PASS. (The test generates raw under its `--data-dir` workroot and — with the new wiring — normalize reads the committed mappings from the repo via `cwd=repo_root` rather than the workroot copy; the result is identical.) If Docker is unavailable, report BLOCKED for this step rather than skipping the integration check.

- [ ] **Step 7: Commit**

```bash
git add orchestrator/src/taxi_orchestrate/cli.py orchestrator/src/taxi_orchestrate/stages.py tests/taxi_orchestrate/test_stages.py
git commit -m "feat(orchestrator): thread --data-dir through all stages; run stages at repo root"
```

---

## Self-Review

**1. Spec coverage:**
- `--data-dir` on downloader / normalizer / loader / orchestrator → Tasks 1 / 2 / 3 / 4.
- Derived `raw` / `raw-normalized` under base; names kept → all tasks.
- Precedence (loader `--input-dir` > `--data-dir` > default; downloader `OUTPUT_DIR` > `--data-dir` > default) → Tasks 3 / 1.
- Mappings stay repo-relative → Task 2 (mapping_path unchanged) + Task 4 (`cwd=repo_root`).
- Orchestrator fixes the download-redirect gap and decouples data from code → Task 4.
- Backward-compat + the enumerated test updates (loader `test_cli`, orchestrator `test_stages`, e2e verification) → Tasks 3 / 4.
No spec requirement unaddressed.

**2. Placeholder scan:** No TBD/TODO; every code step carries complete content and exact locations (with line numbers).

**3. Type/name consistency:** New signatures are consistent across tasks — `build_download_cmd(repo_root, data_type, recent, data_dir)`, `build_normalize_cmd(data_type, sample, data_dir)`, `build_load_cmd(data_type, conn, data_dir)` (note: `build_load_cmd` drops its old `input_dir` positional in favor of `data_dir`); `resolve_input_dir(input_dir, data_dir)`; `_normalize_one(data_type, sample, data_dir)`. The orchestrator passes `data_dir` (a `Path`) into each; builders `str()` it. `INPUT_DIR` is removed and not referenced anywhere after Task 4.

**Known verification points** (flagged inline): whether the downloader's `--recent 0` no-ops network (the test's PATH shim makes it safe regardless); and the full-suite pass count shifting from 203 to ~206 as Tasks 1-3 add tests.
