# Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `taxi-run`, a one-command orchestrator that chains download → normalize → (opt-in) load per data type honoring each stage's exit codes, plus `taxi-curate-mappings`, a tool that auto-accepts all detected drift into complete mapping YAMLs and emits an audit report — then curate and commit the four real mappings.

**Architecture:** A new `orchestrator/` component. The orchestrator invokes each existing CLI as a subprocess (preserving the disk-seam, independently-restartable pipeline) and reads exit codes. The exit-code→outcome + halt/continue logic is a **pure function** (`pipeline.py`), exhaustively unit-tested with no subprocess. A small behavior-preserving refactor to `normalize` exposes structured drift detection (`detect_drift`) that `taxi-curate-mappings` consumes to auto-accept renames and fill lossy-cast/data-loss acknowledgments, verifying against the normalizer's own planner.

**Tech Stack:** Python 3.12, stdlib (`subprocess`, `argparse`, `pathlib`, `dataclasses`), `pyyaml` (already a dep), DuckDB (only transitively, via the reused normalize internals), pytest.

## Global Constraints

- **No new runtime dependency.** stdlib + `pyyaml` (already present). `duckdb`/`pyyaml` reached only through reused `taxi_normalize` code.
- **`src/` layout + tests dir:** package at `orchestrator/src/taxi_orchestrate/`; tests at `tests/taxi_orchestrate/`. Register in `[tool.hatch.build.targets.wheel] packages`.
- **Console scripts:** `taxi-run = "taxi_orchestrate.cli:main"` and `taxi-curate-mappings = "taxi_orchestrate.curate:main"`.
- **`DATA_TYPES = ("yellow", "green", "fhv", "fhvhv")`.** The positional `TYPE` uses `choices=DATA_TYPES` (a typo is a usage error, exit 2). No positional = all four in order.
- **Default `taxi-run` = download → normalize.** `--load` adds load; `--skip-download` drops download; `--download-only` = download only; `--download-only` with `--load` is a usage error.
- **`--load` needs `MSSQL_PASSWORD` (env only), forwarded to `taxi-load`, never logged.** Missing → exit 2 before running anything.
- **Minimum load connection = `--host` + `--user` + `MSSQL_PASSWORD`;** `--database` default `taxi`, `--schema` default `dbo`. Certificate trust is on by default inside the loader (not a `taxi-run` flag).
- **Overall exit codes (mirrors repo `2 > 1 > 0`):** `0` clean (incl. all-skip and successful `--dry-run`); `1` ≥1 type needs human mapping review (normalize exit 3/1) and nothing else failed; `2` an operational failure (download failed / normalize error / load partial or error).
- **Stage invocation:** downloader `bash <root>/downloader/download_taxi_data.sh …`; normalize `<sys.executable> -m taxi_normalize.cli …`; load `<sys.executable> -m taxi_loader.cli … --input-dir raw-normalized`. All run with `cwd = <root>` (repo root: the dir containing `downloader/` and `pyproject.toml`).
- **A "needs review" or failure halts that type's remaining stages** (never load un-normalized data); other types continue. A loader conn/config error (exit 2) aborts remaining types' load stage.
- **`normalize` stays a strict human-in-the-loop gate.** The `detect_drift` extraction is behavior-preserving; auto-acceptance lives only in `taxi-curate-mappings`.
- **Auto-acked entries carry `ack_date` = today, `ack_by: auto-curated`, and a `reason`** with the detected detail. The committed YAMLs + `CURATION-REPORT.md` are the audit trail.
- DRY, YAGNI, TDD, frequent commits.

---

### Task 1: Component skeleton + packaging

**Files:**
- Create: `orchestrator/src/taxi_orchestrate/__init__.py` (empty)
- Create: `orchestrator/src/taxi_orchestrate/cli.py` (stub `main`)
- Create: `orchestrator/src/taxi_orchestrate/curate.py` (stub `main`)
- Create: `orchestrator/README.md`
- Modify: `pyproject.toml` (wheel packages + two scripts)
- Test: `tests/taxi_orchestrate/test_packaging.py`

**Interfaces:**
- Produces: import name `taxi_orchestrate`; console scripts `taxi-run` → `taxi_orchestrate.cli:main`, `taxi-curate-mappings` → `taxi_orchestrate.curate:main`. Every later task imports from `taxi_orchestrate.*`.

- [ ] **Step 1: Write the packaging test**

Create `tests/taxi_orchestrate/test_packaging.py`:
```python
def test_package_importable():
    import taxi_orchestrate  # noqa: F401


def test_entry_points_have_main():
    from taxi_orchestrate import cli, curate
    assert callable(cli.main)
    assert callable(curate.main)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --extra test pytest tests/taxi_orchestrate/test_packaging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_orchestrate'`.

- [ ] **Step 3: Create the package + stubs**

Create `orchestrator/src/taxi_orchestrate/__init__.py` (empty file).

Create `orchestrator/src/taxi_orchestrate/cli.py`:
```python
"""Entry point for `taxi-run` (stub; implemented in a later task)."""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    print("taxi-run: not yet implemented")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Create `orchestrator/src/taxi_orchestrate/curate.py`:
```python
"""Entry point for `taxi-curate-mappings` (stub; implemented in a later task)."""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    print("taxi-curate-mappings: not yet implemented")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Wire packaging**

In `pyproject.toml`, add `"orchestrator/src/taxi_orchestrate",` to `[tool.hatch.build.targets.wheel] packages` (keep alphabetical: after `normalize`, before `schema-drift`):
```toml
packages = [
  "k6-loadtest/src/k6_loadtest",
  "loader/src/taxi_loader",
  "normalize/src/taxi_normalize",
  "orchestrator/src/taxi_orchestrate",
  "schema-drift/src/schema_drift",
  "shared/src/taxi_shared",
]
```
Add to `[project.scripts]`:
```toml
taxi-run = "taxi_orchestrate.cli:main"
taxi-curate-mappings = "taxi_orchestrate.curate:main"
```

- [ ] **Step 5: Sync and run the test**

Run:
```bash
uv sync --extra test
uv run --extra test pytest tests/taxi_orchestrate/test_packaging.py -v
```
Expected: PASS (both). If import fails, re-run `uv sync`.

- [ ] **Step 6: Write the README**

Create `orchestrator/README.md`:
```markdown
# orchestrator

Runs the pipeline as one command: `taxi-run [TYPE]` chains download → normalize
→ (opt-in `--load`) load, honoring each stage's exit codes and halting a type
when normalize needs human review. `taxi-curate-mappings` auto-accepts detected
schema drift into complete mapping YAMLs and writes an audit report.

→ **[Full guide](https://andrekamman.github.io/taxi/guides/orchestrator/)**
```

- [ ] **Step 7: Commit**

```bash
git add orchestrator/ pyproject.toml tests/taxi_orchestrate/test_packaging.py
git commit -m "feat(orchestrator): component skeleton + taxi-run/taxi-curate-mappings wiring"
```

---

### Task 2: `pipeline.py` — pure classification & exit-code logic (the heart)

**Files:**
- Create: `orchestrator/src/taxi_orchestrate/pipeline.py`
- Test: `tests/taxi_orchestrate/test_pipeline.py`

**Interfaces:**
- Produces (relied on by `cli.py`, `report.py`):
  - Stage constants `DOWNLOAD`, `NORMALIZE`, `LOAD`.
  - Status constants `OK`, `NEEDS_REVIEW`, `FAILED`, `PARTIAL`, `CONN_ERROR`.
  - `StageOutcome(stage, exit_code, status, halt_type: bool, abort_run: bool)` — frozen dataclass.
  - `classify(stage: str, exit_code: int) -> StageOutcome`.
  - `overall_exit_code(outcomes: list[StageOutcome]) -> int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/taxi_orchestrate/test_pipeline.py`:
```python
import pytest

from taxi_orchestrate.pipeline import (
    CONN_ERROR, DOWNLOAD, FAILED, LOAD, NEEDS_REVIEW, NORMALIZE, OK, PARTIAL,
    StageOutcome, classify, overall_exit_code,
)


def test_download_ok_does_not_halt():
    o = classify(DOWNLOAD, 0)
    assert (o.status, o.halt_type, o.abort_run) == (OK, False, False)


def test_download_failure_halts_type():
    o = classify(DOWNLOAD, 1)
    assert (o.status, o.halt_type, o.abort_run) == (FAILED, True, False)


def test_normalize_ok():
    assert classify(NORMALIZE, 0).status == OK


@pytest.mark.parametrize("code", [1, 3])
def test_normalize_needs_review_halts(code):
    o = classify(NORMALIZE, code)
    assert (o.status, o.halt_type) == (NEEDS_REVIEW, True)


def test_normalize_error_halts():
    o = classify(NORMALIZE, 2)
    assert (o.status, o.halt_type) == (FAILED, True)


def test_load_ok():
    assert classify(LOAD, 0).status == OK


def test_load_partial_does_not_halt_or_abort():
    o = classify(LOAD, 1)
    assert (o.status, o.halt_type, o.abort_run) == (PARTIAL, False, False)


def test_load_conn_error_aborts_run():
    o = classify(LOAD, 2)
    assert (o.status, o.halt_type, o.abort_run) == (CONN_ERROR, True, True)


def test_unknown_stage_raises():
    with pytest.raises(ValueError):
        classify("bogus", 0)


def test_overall_clean():
    assert overall_exit_code([classify(DOWNLOAD, 0), classify(NORMALIZE, 0)]) == 0


def test_overall_needs_review_is_1():
    assert overall_exit_code([classify(NORMALIZE, 3)]) == 1


def test_overall_failure_is_2():
    assert overall_exit_code([classify(DOWNLOAD, 1)]) == 2


def test_overall_failure_beats_needs_review():
    outs = [classify(NORMALIZE, 3), classify(DOWNLOAD, 1)]
    assert overall_exit_code(outs) == 2


def test_overall_load_partial_is_2():
    assert overall_exit_code([classify(NORMALIZE, 0), classify(LOAD, 1)]) == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/taxi_orchestrate/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_orchestrate.pipeline'`.

- [ ] **Step 3: Implement `pipeline.py`**

Create `orchestrator/src/taxi_orchestrate/pipeline.py`:
```python
"""Pure per-stage classification and overall exit-code logic. No I/O, no subprocess."""
from __future__ import annotations

from dataclasses import dataclass

# Stages
DOWNLOAD = "download"
NORMALIZE = "normalize"
LOAD = "load"

# Stage statuses
OK = "ok"
NEEDS_REVIEW = "needs_review"
FAILED = "failed"
PARTIAL = "partial"
CONN_ERROR = "conn_error"

_FAILURE_STATUSES = frozenset({FAILED, PARTIAL, CONN_ERROR})


@dataclass(frozen=True)
class StageOutcome:
    stage: str
    exit_code: int
    status: str
    halt_type: bool   # stop this type's remaining stages
    abort_run: bool   # skip the load stage for all remaining types


def classify(stage: str, exit_code: int) -> StageOutcome:
    if stage == DOWNLOAD:
        if exit_code == 0:
            return StageOutcome(stage, exit_code, OK, False, False)
        return StageOutcome(stage, exit_code, FAILED, True, False)
    if stage == NORMALIZE:
        if exit_code == 0:
            return StageOutcome(stage, exit_code, OK, False, False)
        if exit_code in (1, 3):
            return StageOutcome(stage, exit_code, NEEDS_REVIEW, True, False)
        return StageOutcome(stage, exit_code, FAILED, True, False)
    if stage == LOAD:
        if exit_code == 0:
            return StageOutcome(stage, exit_code, OK, False, False)
        if exit_code == 1:
            return StageOutcome(stage, exit_code, PARTIAL, False, False)
        return StageOutcome(stage, exit_code, CONN_ERROR, True, True)
    raise ValueError(f"unknown stage: {stage!r}")


def overall_exit_code(outcomes: list[StageOutcome]) -> int:
    if any(o.status in _FAILURE_STATUSES for o in outcomes):
        return 2
    if any(o.status == NEEDS_REVIEW for o in outcomes):
        return 1
    return 0
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --extra test pytest tests/taxi_orchestrate/test_pipeline.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add orchestrator/src/taxi_orchestrate/pipeline.py tests/taxi_orchestrate/test_pipeline.py
git commit -m "feat(orchestrator): pure stage classification + overall exit-code logic"
```

---

### Task 3: `stages.py` — argv builders + subprocess runner

**Files:**
- Create: `orchestrator/src/taxi_orchestrate/stages.py`
- Test: `tests/taxi_orchestrate/test_stages.py`

**Interfaces:**
- Produces (relied on by `cli.py`):
  - `LoadConn(host, port, database, schema, user, flush_rows, full_refresh)` — dataclass.
  - `build_download_cmd(root: Path, data_type: str | None, recent: int | None) -> list[str]`
  - `build_normalize_cmd(data_type: str, sample: str | None) -> list[str]`
  - `build_load_cmd(data_type: str, input_dir: str, conn: LoadConn) -> list[str]`
  - `run(cmd: list[str], cwd: Path, extra_env: dict | None = None) -> int`
- `recent` convention: `None` = full mode (no `--recent`); `0` = recent mode with the downloader's default N (`--recent` alone); `>0` = `--recent N`.

- [ ] **Step 1: Write the failing tests**

Create `tests/taxi_orchestrate/test_stages.py`:
```python
import sys
from pathlib import Path

from taxi_orchestrate.stages import (
    LoadConn, build_download_cmd, build_load_cmd, build_normalize_cmd, run,
)


def _conn(**kw):
    base = dict(host="h", port=1433, database="taxi", schema="dbo", user="sa",
                flush_rows=100000, full_refresh=False)
    base.update(kw)
    return LoadConn(**base)


def test_download_full_all_types():
    cmd = build_download_cmd(Path("/repo"), None, None)
    assert cmd == ["bash", "/repo/downloader/download_taxi_data.sh"]


def test_download_full_one_type():
    cmd = build_download_cmd(Path("/repo"), "yellow", None)
    assert cmd[-1] == "yellow" and "--recent" not in cmd


def test_download_recent_default_n():
    cmd = build_download_cmd(Path("/repo"), "green", 0)
    assert "--recent" in cmd and "green" == cmd[-1]
    assert cmd[cmd.index("--recent") + 1] == "green"  # no numeric N inserted


def test_download_recent_explicit_n():
    cmd = build_download_cmd(Path("/repo"), "green", 3)
    i = cmd.index("--recent")
    assert cmd[i + 1] == "3" and cmd[-1] == "green"


def test_normalize_cmd():
    cmd = build_normalize_cmd("yellow", "50%")
    assert cmd == [sys.executable, "-m", "taxi_normalize.cli", "yellow", "--sample", "50%"]


def test_normalize_cmd_no_sample():
    cmd = build_normalize_cmd("fhv", None)
    assert cmd == [sys.executable, "-m", "taxi_normalize.cli", "fhv"]


def test_load_cmd_forwards_flags():
    cmd = build_load_cmd("yellow", "raw-normalized", _conn(host="db1", port=1444, full_refresh=True))
    assert cmd[:4] == [sys.executable, "-m", "taxi_loader.cli", "yellow"]
    assert "--input-dir" in cmd and cmd[cmd.index("--input-dir") + 1] == "raw-normalized"
    assert cmd[cmd.index("--host") + 1] == "db1"
    assert cmd[cmd.index("--port") + 1] == "1444"
    assert "--full-refresh" in cmd
    # password is NEVER on the command line
    assert not any("password" in c.lower() for c in cmd)


def test_run_returns_child_exit_code(tmp_path):
    rc = run([sys.executable, "-c", "import sys; sys.exit(7)"], cwd=tmp_path)
    assert rc == 7


def test_run_passes_extra_env(tmp_path):
    code = "import os,sys; sys.exit(0 if os.environ.get('X_TEST')=='v' else 5)"
    rc = run([sys.executable, "-c", code], cwd=tmp_path, extra_env={"X_TEST": "v"})
    assert rc == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/taxi_orchestrate/test_stages.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_orchestrate.stages'`.

- [ ] **Step 3: Implement `stages.py`**

Create `orchestrator/src/taxi_orchestrate/stages.py`:
```python
"""Argv builders for each pipeline stage + a thin subprocess runner."""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class LoadConn:
    host: str
    port: int
    database: str
    schema: str
    user: str
    flush_rows: int
    full_refresh: bool


def build_download_cmd(root: Path, data_type: Optional[str],
                       recent: Optional[int]) -> list[str]:
    cmd = ["bash", str(root / "downloader" / "download_taxi_data.sh")]
    if recent is not None:
        cmd.append("--recent")
        if recent > 0:
            cmd.append(str(recent))
    if data_type:
        cmd.append(data_type)
    return cmd


def build_normalize_cmd(data_type: str, sample: Optional[str]) -> list[str]:
    cmd = [sys.executable, "-m", "taxi_normalize.cli", data_type]
    if sample:
        cmd += ["--sample", sample]
    return cmd


def build_load_cmd(data_type: str, input_dir: str, conn: LoadConn) -> list[str]:
    cmd = [
        sys.executable, "-m", "taxi_loader.cli", data_type,
        "--input-dir", input_dir,
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


def run(cmd: list[str], cwd: Path, extra_env: Optional[dict] = None) -> int:
    env = None
    if extra_env:
        env = {**os.environ, **extra_env}
    return subprocess.run(cmd, cwd=str(cwd), env=env).returncode
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --extra test pytest tests/taxi_orchestrate/test_stages.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add orchestrator/src/taxi_orchestrate/stages.py tests/taxi_orchestrate/test_stages.py
git commit -m "feat(orchestrator): stage argv builders + subprocess runner"
```

---

### Task 4: `report.py` — per-type/per-stage summary

**Files:**
- Create: `orchestrator/src/taxi_orchestrate/report.py`
- Test: `tests/taxi_orchestrate/test_report.py`

**Interfaces:**
- Consumes: `pipeline.StageOutcome` and its status constants.
- Produces (relied on by `cli.py`):
  - `TypeRun(data_type: str, outcomes: list[StageOutcome])` — dataclass.
  - `type_label(run: TypeRun) -> str` — headline for one type (`OK` / `LOADED` / `NEEDS REVIEW` / `DOWNLOAD FAILED` / `NORMALIZE ERROR` / `LOAD PARTIAL` / `LOAD ERROR` / `SKIPPED`).
  - `render_summary(runs: list[TypeRun]) -> str` — a text table + one-line verdict.

- [ ] **Step 1: Write the failing tests**

Create `tests/taxi_orchestrate/test_report.py`:
```python
from taxi_orchestrate.pipeline import DOWNLOAD, LOAD, NORMALIZE, classify
from taxi_orchestrate.report import TypeRun, render_summary, type_label


def test_label_ok_download_normalize():
    r = TypeRun("yellow", [classify(DOWNLOAD, 0), classify(NORMALIZE, 0)])
    assert type_label(r) == "OK"


def test_label_loaded():
    r = TypeRun("yellow", [classify(DOWNLOAD, 0), classify(NORMALIZE, 0), classify(LOAD, 0)])
    assert type_label(r) == "LOADED"


def test_label_needs_review():
    r = TypeRun("green", [classify(DOWNLOAD, 0), classify(NORMALIZE, 1)])
    assert type_label(r) == "NEEDS REVIEW"


def test_label_download_failed():
    r = TypeRun("fhv", [classify(DOWNLOAD, 1)])
    assert type_label(r) == "DOWNLOAD FAILED"


def test_label_load_partial():
    r = TypeRun("yellow", [classify(DOWNLOAD, 0), classify(NORMALIZE, 0), classify(LOAD, 1)])
    assert type_label(r) == "LOAD PARTIAL"


def test_render_summary_lists_all_types():
    runs = [
        TypeRun("yellow", [classify(DOWNLOAD, 0), classify(NORMALIZE, 0)]),
        TypeRun("green", [classify(DOWNLOAD, 0), classify(NORMALIZE, 1)]),
    ]
    out = render_summary(runs)
    assert "yellow" in out and "green" in out
    assert "OK" in out and "NEEDS REVIEW" in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/taxi_orchestrate/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxi_orchestrate.report'`.

- [ ] **Step 3: Implement `report.py`**

Create `orchestrator/src/taxi_orchestrate/report.py`:
```python
"""Human-readable run summary."""
from __future__ import annotations

from dataclasses import dataclass

from taxi_orchestrate.pipeline import (
    CONN_ERROR, DOWNLOAD, FAILED, LOAD, NEEDS_REVIEW, NORMALIZE, OK, PARTIAL,
    StageOutcome,
)


@dataclass
class TypeRun:
    data_type: str
    outcomes: list[StageOutcome]


def _outcome_for(run: "TypeRun", stage: str):
    for o in run.outcomes:
        if o.stage == stage:
            return o
    return None


def type_label(run: "TypeRun") -> str:
    dl = _outcome_for(run, DOWNLOAD)
    nz = _outcome_for(run, NORMALIZE)
    ld = _outcome_for(run, LOAD)
    if dl is not None and dl.status == FAILED:
        return "DOWNLOAD FAILED"
    if nz is not None and nz.status == NEEDS_REVIEW:
        return "NEEDS REVIEW"
    if nz is not None and nz.status == FAILED:
        return "NORMALIZE ERROR"
    if ld is not None:
        if ld.status == OK:
            return "LOADED"
        if ld.status == PARTIAL:
            return "LOAD PARTIAL"
        if ld.status == CONN_ERROR:
            return "LOAD ERROR"
    if not run.outcomes:
        return "SKIPPED"
    return "OK"


def _cell(run: "TypeRun", stage: str) -> str:
    o = _outcome_for(run, stage)
    return o.status if o is not None else "-"


def render_summary(runs: list["TypeRun"]) -> str:
    header = f"{'type':<8} {'download':<12} {'normalize':<14} {'load':<12} outcome"
    lines = [header, "-" * len(header)]
    for r in runs:
        lines.append(
            f"{r.data_type:<8} {_cell(r, DOWNLOAD):<12} {_cell(r, NORMALIZE):<14} "
            f"{_cell(r, LOAD):<12} {type_label(r)}"
        )
    return "\n".join(lines)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --extra test pytest tests/taxi_orchestrate/test_report.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add orchestrator/src/taxi_orchestrate/report.py tests/taxi_orchestrate/test_report.py
git commit -m "feat(orchestrator): per-type run summary rendering"
```

---

### Task 5: `cli.py` — `taxi-run` orchestration

**Files:**
- Modify: `orchestrator/src/taxi_orchestrate/cli.py` (replace the stub)
- Test: `tests/taxi_orchestrate/test_cli.py`
- Test: `tests/taxi_orchestrate/test_run_stub.py`

**Interfaces:**
- Consumes: `pipeline`, `stages`, `report`.
- Produces: `parse_args(argv) -> argparse.Namespace`; `find_repo_root(start: Path) -> Path`; `main(argv=None) -> int`.
- Tests drive the run loop by monkeypatching `taxi_orchestrate.stages.run`.

- [ ] **Step 1: Write the failing tests**

Create `tests/taxi_orchestrate/test_cli.py`:
```python
import taxi_orchestrate.cli as cli
from taxi_orchestrate.cli import DATA_TYPES, main, parse_args


def test_parse_defaults():
    ns = parse_args([])
    assert ns.data_type is None
    assert ns.skip_download is False and ns.download_only is False and ns.load is False
    assert ns.host == "localhost" and ns.port == 1433
    assert ns.database == "taxi" and ns.schema == "dbo" and ns.user == "sa"
    assert ns.flush_rows == 100000 and ns.full_refresh is False
    assert ns.sample is None and ns.recent is None and ns.dry_run is False


def test_bad_type_is_usage_error():
    import pytest
    with pytest.raises(SystemExit) as e:
        parse_args(["yello"])
    assert e.value.code == 2


def test_download_only_with_load_is_error(capsys):
    rc = main(["--download-only", "--load"])
    assert rc == 2


def test_load_without_password_is_exit_2(monkeypatch):
    monkeypatch.delenv("MSSQL_PASSWORD", raising=False)
    rc = main(["yellow", "--skip-download", "--load"])
    assert rc == 2


def test_dry_run_invokes_nothing(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli.stages, "run", lambda *a, **k: calls.append(a) or 0)
    rc = main(["yellow", "--dry-run"])
    assert rc == 0
    assert calls == []
    assert "yellow" in capsys.readouterr().out
```

Create `tests/taxi_orchestrate/test_run_stub.py`:
```python
import taxi_orchestrate.cli as cli


def _fake_run(codes):
    """Return a run() stub that yields exit codes keyed by the stage token in cmd."""
    def _run(cmd, cwd, extra_env=None):
        joined = " ".join(cmd)
        if "download_taxi_data.sh" in joined:
            stage = "download"
        elif "taxi_normalize.cli" in joined:
            stage = "normalize"
        else:
            stage = "load"
        # data type is the last non-flag token for our stub purposes
        dtype = cmd[-1] if not cmd[-1].startswith("-") else "?"
        return codes.get((stage, dtype), codes.get(stage, 0))
    return _run


def test_default_runs_download_then_normalize(monkeypatch, capsys):
    seen = []
    def _run(cmd, cwd, extra_env=None):
        seen.append("download" if "download_taxi_data.sh" in " ".join(cmd)
                    else "normalize" if "taxi_normalize.cli" in " ".join(cmd) else "load")
        return 0
    monkeypatch.setattr(cli.stages, "run", _run)
    rc = main_ok = cli.main(["yellow"])
    assert rc == 0
    assert seen == ["download", "normalize"]   # no load by default


def test_needs_review_halts_before_load(monkeypatch):
    monkeypatch.setenv("MSSQL_PASSWORD", "pw")
    def _run(cmd, cwd, extra_env=None):
        j = " ".join(cmd)
        if "taxi_normalize.cli" in j:
            return 1          # needs review
        if "taxi_loader.cli" in j:
            raise AssertionError("load must not run after needs-review")
        return 0
    monkeypatch.setattr(cli.stages, "run", _run)
    rc = cli.main(["yellow", "--load"])
    assert rc == 1            # needs review, nothing failed


def test_load_conn_error_aborts_remaining_loads(monkeypatch):
    monkeypatch.setenv("MSSQL_PASSWORD", "pw")
    loads = []
    def _run(cmd, cwd, extra_env=None):
        j = " ".join(cmd)
        if "taxi_loader.cli" in j:
            loads.append(cmd[cmd.index("taxi_loader.cli") + 1] if False else cmd[3])
            return 2          # conn error on first load
        return 0
    monkeypatch.setattr(cli.stages, "run", _run)
    rc = cli.main(["--skip-download", "--load"])   # all four types
    assert rc == 2
    assert len(loads) == 1    # aborted after the first conn error


def test_skip_download_runs_normalize_only(monkeypatch):
    seen = []
    def _run(cmd, cwd, extra_env=None):
        seen.append("download" if "download_taxi_data.sh" in " ".join(cmd)
                    else "normalize" if "taxi_normalize.cli" in " ".join(cmd) else "load")
        return 0
    monkeypatch.setattr(cli.stages, "run", _run)
    rc = cli.main(["green", "--skip-download"])
    assert rc == 0 and seen == ["normalize"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/taxi_orchestrate/test_cli.py tests/taxi_orchestrate/test_run_stub.py -v`
Expected: FAIL — `parse_args`/behavior not implemented (stub prints and returns 0).

- [ ] **Step 3: Implement `cli.py`**

Replace `orchestrator/src/taxi_orchestrate/cli.py`:
```python
"""taxi-run: chain download -> normalize -> (opt-in) load per data type.

Honors each stage's exit codes: a needs-review or failure halts that type's
remaining stages; a loader conn/config error aborts the load stage for all
remaining types. Overall exit code: 0 clean, 1 needs-review, 2 operational
failure (2 > 1 > 0).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from taxi_orchestrate import pipeline, report, stages

DATA_TYPES = ("yellow", "green", "fhv", "fhvhv")
INPUT_DIR = "raw-normalized"


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="taxi-run",
        description="Run the pipeline: download -> normalize -> (opt-in) load.",
    )
    p.add_argument("data_type", nargs="?", choices=DATA_TYPES,
                   help="yellow/green/fhv/fhvhv. Omit to run all four.")
    p.add_argument("--recent", nargs="?", type=int, const=0, default=None,
                   help="downloader recent-mode: --recent [N] (N optional)")
    p.add_argument("--skip-download", action="store_true",
                   help="skip the download stage; use the existing raw/ mirror")
    p.add_argument("--download-only", action="store_true",
                   help="only mirror; skip normalize and load")
    p.add_argument("--load", action="store_true",
                   help="also load normalized parquet into SQL Server")
    p.add_argument("--sample", default=None, help="passed through to normalize")
    p.add_argument("--data-dir", default=None,
                   help="working root holding raw/ + raw-normalized/ (default: repo root)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the per-type plan and exit without running anything")
    # forwarded to taxi-load (only meaningful with --load)
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=1433)
    p.add_argument("--database", default="taxi")
    p.add_argument("--schema", default="dbo")
    p.add_argument("--user", default="sa")
    p.add_argument("--flush-rows", type=int, default=100000)
    p.add_argument("--full-refresh", action="store_true")
    return p.parse_args(argv)


def find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for d in [cur, *cur.parents]:
        if (d / "downloader" / "download_taxi_data.sh").exists() and (d / "pyproject.toml").exists():
            return d
    return cur


def _planned_stages(args) -> list[str]:
    if args.download_only:
        return [pipeline.DOWNLOAD]
    st = [] if args.skip_download else [pipeline.DOWNLOAD]
    st.append(pipeline.NORMALIZE)
    if args.load:
        st.append(pipeline.LOAD)
    return st


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.download_only and args.load:
        print("error: --download-only cannot be combined with --load", file=sys.stderr)
        return 2

    password = None
    if args.load:
        password = os.environ.get("MSSQL_PASSWORD")
        if not password:
            print("error: MSSQL_PASSWORD environment variable is required for --load",
                  file=sys.stderr)
            return 2

    root = Path(args.data_dir).resolve() if args.data_dir else find_repo_root(Path.cwd())
    types = [args.data_type] if args.data_type else list(DATA_TYPES)
    planned = _planned_stages(args)
    conn = stages.LoadConn(
        host=args.host, port=args.port, database=args.database, schema=args.schema,
        user=args.user, flush_rows=args.flush_rows, full_refresh=args.full_refresh,
    )

    if args.dry_run:
        print(f"taxi-run plan (root={root}); stages: {' -> '.join(planned)}")
        for t in types:
            print(f"  {t}: {', '.join(planned)}")
        return 0

    runs: list[report.TypeRun] = []
    all_outcomes: list[pipeline.StageOutcome] = []
    abort_load = False

    for t in types:
        outcomes: list[pipeline.StageOutcome] = []
        halted = False
        for stage in planned:
            if stage == pipeline.LOAD and abort_load:
                break
            if stage == pipeline.DOWNLOAD:
                rc = stages.run(stages.build_download_cmd(root, t, args.recent), root)
            elif stage == pipeline.NORMALIZE:
                rc = stages.run(stages.build_normalize_cmd(t, args.sample), root)
            else:  # LOAD
                rc = stages.run(stages.build_load_cmd(t, INPUT_DIR, conn), root,
                                extra_env={"MSSQL_PASSWORD": password})
            o = pipeline.classify(stage, rc)
            outcomes.append(o)
            if o.abort_run:
                abort_load = True
            if o.halt_type:
                halted = True
                break
        runs.append(report.TypeRun(t, outcomes))
        all_outcomes.extend(outcomes)
        _ = halted  # (kept for readability; halting already broke the stage loop)

    print(report.render_summary(runs))
    return pipeline.overall_exit_code(all_outcomes)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --extra test pytest tests/taxi_orchestrate/test_cli.py tests/taxi_orchestrate/test_run_stub.py -v`
Expected: PASS (all). (`test_run_stub`'s conn-error case relies on `abort_load` skipping the remaining three types' load; `--skip-download` means only the load stage runs per type, so exactly one load executes.)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/src/taxi_orchestrate/cli.py tests/taxi_orchestrate/test_cli.py tests/taxi_orchestrate/test_run_stub.py
git commit -m "feat(orchestrator): taxi-run CLI (stage selection, dry-run, exit codes)"
```

---

### Task 6: `normalize` refactor — extract `detect_drift`

**Files:**
- Modify: `normalize/src/taxi_normalize/bootstrap.py`
- Test: `tests/taxi_normalize/test_detect_drift.py`

**Interfaces:**
- Produces (relied on by `curate.py`):
  - `DriftReport(target_name: str, timeline: list[str], rename_suggestions: list[tuple[str, str, float]], lossy: list[dict], data_loss: list[dict])` — dataclass. `lossy` items are `{"column", "from", "to", "reason", "files_present"}`; `data_loss` items are `{"column", "files_present"}`.
  - `detect_drift(data_type: str, raw_dir: Path, existing: Mapping | None, sample: str = "100%") -> DriftReport`.
- `bootstrap_type` keeps its exact current behavior (now implemented as `detect_drift` + `_emit_yaml`).

- [ ] **Step 1: Write the failing test**

Create `tests/taxi_normalize/test_detect_drift.py`:
```python
from taxi_normalize.bootstrap import DriftReport, detect_drift


def test_detect_drift_on_yellow_family(yellow_family):
    # yellow_family (from conftest) has: pu_datetime->tpep_pickup_datetime rename,
    # passenger_count DOUBLE with fractional values vs BIGINT target (lossy),
    # and old lat/long columns dropped with data (data loss).
    report = detect_drift("yellow", yellow_family, existing=None, sample="100%")
    assert isinstance(report, DriftReport)
    assert report.target_name.endswith(".parquet")
    rename_targets = {new for _old, new, _c in report.rename_suggestions}
    assert "tpep_pickup_datetime" in rename_targets
    lossy_cols = {d["column"] for d in report.lossy}
    assert "passenger_count" in lossy_cols
    for d in report.lossy:
        assert set(d) >= {"column", "from", "to", "reason", "files_present"}
    dataloss_cols = {d["column"] for d in report.data_loss}
    assert "pickup_latitude" in dataloss_cols or "pickup_longitude" in dataloss_cols


def test_detect_drift_respects_existing(yellow_family):
    from taxi_normalize.mapping import Mapping
    existing = Mapping(target="yellow_tripdata_2024-01.parquet",
                       renames={"pu_datetime": "tpep_pickup_datetime"})
    report = detect_drift("yellow", yellow_family, existing=existing, sample="100%")
    # already-handled rename is not re-suggested
    assert all(old != "pu_datetime" for old, _new, _c in report.rename_suggestions)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/taxi_normalize/test_detect_drift.py -v`
Expected: FAIL — `ImportError: cannot import name 'DriftReport'`.

- [ ] **Step 3: Refactor `bootstrap.py`**

Add the `DriftReport` dataclass (near `BootstrapResult`):
```python
@dataclass
class DriftReport:
    """Structured drift detection for a data type, relative to an existing mapping."""
    target_name: str
    timeline: list[str]
    rename_suggestions: list[tuple[str, str, float]]   # (old, new, confidence)
    lossy: list[dict]         # {column, from, to, reason, files_present}
    data_loss: list[dict]     # {column, files_present}
```

Add `detect_drift`, moving the detection body currently inside `bootstrap_type` (the block from "Collect files" through building `new_rename_suggestions` / `new_lossy_todos` / `new_data_loss_todos`) into it. Include `files_present` on lossy items:
```python
def detect_drift(data_type: str, raw_dir: Path, existing: Optional[Mapping],
                 sample: str = "100%") -> DriftReport:
    """Detect drift for one data type relative to an existing mapping (or None).

    Pure of any YAML emission — returns the structured suggestions/TODOs that
    bootstrap would otherwise render as commented scaffold.
    """
    files = sorted(raw_dir.rglob(f"{data_type}_tripdata_*.parquet"))
    if not files:
        files = sorted(raw_dir.rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet files found under {raw_dir} for {data_type}")

    if existing is not None:
        target_name = existing.target
        target_candidates = [f for f in files if f.name == target_name]
        if not target_candidates:
            raise FileNotFoundError(
                f"target file {target_name} (pinned mapping) not found under {raw_dir}"
            )
        target_file = target_candidates[0]
    else:
        target_file = files[-1]
        target_name = target_file.name

    conn = duckdb.connect(":memory:")
    files_md = [get_file_metadata(conn, f) for f in files]
    agg = aggregate_across_files(files_md)
    target_md = get_file_metadata(conn, target_file)
    target_cols = set(target_md.keys())

    if raw_dir.name == data_type:
        data_dir = raw_dir.parent
    else:
        data_dir = raw_dir
    analysis = analyze_data_type(
        conn, data_dir, data_type, verify_data=False, generic_mode=True,
        sample_size=_parse_sample(sample),
    )
    timeline = [_summarize_change(c) for c in analysis["changes"]]

    rename_candidates: dict[tuple[str, str], float] = {}
    for change in analysis["changes"]:
        for rename in change.columns_renamed:
            old, new, conf = rename.old_col.name, rename.new_col.name, rename.confidence
            if conf > rename_candidates.get((old, new), 0):
                rename_candidates[(old, new)] = conf

    existing_rename_sources: set[str] = set()
    existing_rename_targets: set[str] = set()
    existing_lossy_cols: set[str] = set()
    existing_dataloss_cols: set[str] = set()
    if existing is not None:
        existing_rename_sources = set(existing.renames.keys())
        existing_rename_targets = set(existing.renames.values())
        existing_lossy_cols = set(existing.lossy_casts.keys())
        existing_dataloss_cols = set(existing.acknowledged_data_loss.keys())

    rename_suggestions: list[tuple[str, str, float]] = []
    for (old, new), conf in sorted(rename_candidates.items(), key=lambda x: -x[1]):
        if new not in target_cols:
            continue
        if old in existing_rename_sources or new in existing_rename_targets:
            continue
        rename_suggestions.append((old, new, conf))

    lossy: list[dict] = []
    data_loss: list[dict] = []
    for col, stats in agg.items():
        if col in target_cols:
            raw_type_seen = stats["types_seen"][0] if stats["types_seen"] else None
            tgt_type = target_md[col]["type"]
            if raw_type_seen and raw_type_seen != tgt_type:
                fits, reason = fits_in_target_type(
                    {"type": raw_type_seen, "min": stats["min_range"], "max": stats["max_range"]},
                    tgt_type,
                )
                if not fits and col not in existing_lossy_cols:
                    lossy.append({
                        "column": col, "from": raw_type_seen, "to": tgt_type,
                        "reason": reason, "files_present": stats["files_present"],
                    })
            continue
        if stats["files_with_data"] == 0:
            continue
        has_candidate = any(
            old == col and new in target_cols and conf >= RENAME_CONFIDENCE_THRESHOLD
            for (old, new), conf in rename_candidates.items()
        )
        if has_candidate:
            continue
        if col in existing_rename_sources or col in existing_dataloss_cols:
            continue
        data_loss.append({"column": col, "files_present": stats["files_present"]})

    return DriftReport(
        target_name=target_name, timeline=timeline,
        rename_suggestions=rename_suggestions, lossy=lossy, data_loss=data_loss,
    )
```

Rewrite `bootstrap_type` to delegate to `detect_drift` (preserving behavior):
```python
def bootstrap_type(data_type: str, raw_dir: Path, output_yaml: Path,
                   sample: str = "100%") -> BootstrapResult:
    if output_yaml.exists():
        existing = load_mapping(output_yaml)
        was_new = False
    else:
        existing = None
        was_new = True

    report = detect_drift(data_type, raw_dir, existing, sample=sample)

    new_lossy_todos = [
        {"column": d["column"], "from": d["from"], "to": d["to"], "reason": d["reason"]}
        for d in report.lossy
    ]
    new_data_loss_todos = [
        {"column": d["column"], "files_present": d["files_present"]}
        for d in report.data_loss
    ]
    new_items = len(report.rename_suggestions) + len(new_lossy_todos) + len(new_data_loss_todos)

    _emit_yaml(
        output_yaml=output_yaml, data_type=data_type, target_name=report.target_name,
        existing=existing, timeline=report.timeline,
        new_rename_suggestions=report.rename_suggestions,
        new_lossy_todos=new_lossy_todos, new_data_loss_todos=new_data_loss_todos,
    )
    return BootstrapResult(was_new=was_new, new_items=new_items, timeline=report.timeline)
```

- [ ] **Step 4: Run to verify (new test + no regression)**

Run:
```bash
uv run --extra test pytest tests/taxi_normalize/test_detect_drift.py tests/taxi_normalize/ -v
```
Expected: the new tests PASS and the entire existing `tests/taxi_normalize/` suite still PASSES (behavior-preserving refactor).

- [ ] **Step 5: Commit**

```bash
git add normalize/src/taxi_normalize/bootstrap.py tests/taxi_normalize/test_detect_drift.py
git commit -m "refactor(normalize): extract detect_drift from bootstrap_type (behavior-preserving)"
```

---

### Task 7: `curate.py` — `taxi-curate-mappings`

**Files:**
- Modify: `orchestrator/src/taxi_orchestrate/curate.py` (replace the stub)
- Test: `tests/taxi_orchestrate/conftest.py` (synthetic drift fixture)
- Test: `tests/taxi_orchestrate/test_curate.py`

**Interfaces:**
- Consumes: `taxi_normalize.bootstrap.detect_drift`, `taxi_normalize.mapping.{Mapping, load_mapping, LossyCastEntry, DataLossEntry}`, `taxi_normalize.data_check.get_file_metadata`, `taxi_normalize.planner.plan_file`.
- Produces:
  - `AckDecision(kind, column, detail, files_present)` and `CurationResult(data_type, renames, lossy, data_loss)` dataclasses.
  - `curate_type(data_type, raw_dir: Path, mapping_path: Path, today: str) -> CurationResult`
  - `render_report(results: list[CurationResult], today: str) -> str`
  - `main(argv=None) -> int` (`taxi-curate-mappings [TYPE]`, defaults raw=`raw`, mappings=`normalize/mappings`, report=`normalize/mappings/CURATION-REPORT.md`).

- [ ] **Step 1: Write the fixture + failing tests**

Create `tests/taxi_orchestrate/conftest.py`:
```python
"""Synthetic drift parquet for curate tests: a rename, a lossy cast, a data-loss drop."""
from pathlib import Path

import duckdb
import pytest


@pytest.fixture
def drift_family(tmp_path: Path) -> Path:
    """raw/yellow with three eras. Returns the raw/ root (top-level)."""
    raw = tmp_path / "raw" / "yellow"
    (raw / "2009").mkdir(parents=True)
    (raw / "2015").mkdir(parents=True)
    (raw / "2024").mkdir(parents=True)
    conn = duckdb.connect(":memory:")
    # Era 1: old rename source (pu_datetime) + a column that later disappears with data.
    conn.execute(f"""
        COPY (SELECT * FROM (VALUES
            (1, TIMESTAMP '2009-01-01 10:00', 40.7),
            (2, TIMESTAMP '2009-01-02 11:00', 40.8)
        ) AS t(vendorid, pu_datetime, pickup_latitude))
        TO '{raw}/2009/yellow_tripdata_2009-01.parquet' (FORMAT PARQUET)
    """)
    # Era 2: renamed column + fractional passenger_count (DOUBLE).
    conn.execute(f"""
        COPY (SELECT * FROM (VALUES
            (1, TIMESTAMP '2015-06-01 10:00', CAST(1.5 AS DOUBLE)),
            (2, TIMESTAMP '2015-06-02 11:00', CAST(2.0 AS DOUBLE))
        ) AS t(vendorid, tpep_pickup_datetime, passenger_count))
        TO '{raw}/2015/yellow_tripdata_2015-06.parquet' (FORMAT PARQUET)
    """)
    # Era 3 (target): passenger_count BIGINT.
    conn.execute(f"""
        COPY (SELECT * FROM (VALUES
            (1, TIMESTAMP '2024-01-01 10:00', CAST(1 AS BIGINT)),
            (2, TIMESTAMP '2024-01-02 11:00', CAST(2 AS BIGINT))
        ) AS t(vendorid, tpep_pickup_datetime, passenger_count))
        TO '{raw}/2024/yellow_tripdata_2024-01.parquet' (FORMAT PARQUET)
    """)
    conn.close()
    return raw
```

Create `tests/taxi_orchestrate/test_curate.py`:
```python
import duckdb

from taxi_normalize.data_check import get_file_metadata
from taxi_normalize.mapping import load_mapping
from taxi_normalize.planner import plan_file
from taxi_orchestrate.curate import curate_type, render_report


def _unresolved_count(raw_dir, mapping_path):
    mapping = load_mapping(mapping_path)
    conn = duckdb.connect(":memory:")
    files = sorted(raw_dir.rglob("*.parquet"))
    target = [f for f in files if f.name == mapping.target][0]
    target_md = get_file_metadata(conn, target)
    total = 0
    for f in files:
        total += len(plan_file(get_file_metadata(conn, f), target_md, mapping).unresolved)
    return total


def test_curate_produces_clean_mapping(drift_family, tmp_path):
    mapping_path = tmp_path / "mappings" / "yellow.yaml"
    result = curate_type("yellow", drift_family, mapping_path, today="2026-07-23")
    assert mapping_path.exists()
    # The normalizer's own planner accepts the mapping with zero unresolved.
    assert _unresolved_count(drift_family, mapping_path) == 0


def test_curate_fills_acks_and_records_decisions(drift_family, tmp_path):
    mapping_path = tmp_path / "mappings" / "yellow.yaml"
    result = curate_type("yellow", drift_family, mapping_path, today="2026-07-23")
    mapping = load_mapping(mapping_path)
    # lossy cast on passenger_count acked
    assert "passenger_count" in mapping.lossy_casts
    assert mapping.lossy_casts["passenger_count"].ack_date == "2026-07-23"
    assert mapping.lossy_casts["passenger_count"].ack_by == "auto-curated"
    # rename accepted
    assert mapping.renames.get("pu_datetime") == "tpep_pickup_datetime"
    # data-loss drop acked for the vanished column
    assert "pickup_latitude" in mapping.acknowledged_data_loss
    # result records the ack-required decisions
    lossy_cols = {d.column for d in result.lossy}
    assert "passenger_count" in lossy_cols


def test_render_report_lists_ack_required(drift_family, tmp_path):
    mapping_path = tmp_path / "mappings" / "yellow.yaml"
    result = curate_type("yellow", drift_family, mapping_path, today="2026-07-23")
    text = render_report([result], today="2026-07-23")
    assert "passenger_count" in text
    assert "pickup_latitude" in text
    assert "yellow" in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/taxi_orchestrate/test_curate.py -v`
Expected: FAIL — `ImportError: cannot import name 'curate_type'`.

- [ ] **Step 3: Implement `curate.py`**

Replace `orchestrator/src/taxi_orchestrate/curate.py`:
```python
"""taxi-curate-mappings: auto-accept detected drift into complete mapping YAMLs
and write an audit report of the acknowledgment-required decisions.

The normalizer stays a strict human-in-the-loop gate; this is a separate,
deliberately-invoked bulk-accept utility. Every acknowledgment is written with
ack_date/ack_by/reason, and the report + committed YAMLs are the audit trail.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import duckdb
import yaml

from taxi_normalize.bootstrap import detect_drift
from taxi_normalize.data_check import get_file_metadata
from taxi_normalize.mapping import Mapping, load_mapping
from taxi_normalize.planner import plan_file

DATA_TYPES = ("yellow", "green", "fhv", "fhvhv")
ACK_BY = "auto-curated"
_MAX_ROUNDS = 6


@dataclass
class AckDecision:
    kind: str            # "lossy" | "data_loss"
    column: str
    detail: str
    files_present: int


@dataclass
class CurationResult:
    data_type: str
    renames: list[tuple[str, str, float]] = field(default_factory=list)
    lossy: list[AckDecision] = field(default_factory=list)
    data_loss: list[AckDecision] = field(default_factory=list)


def _accept_into(mapping_dict: dict, report, today: str, result: CurationResult) -> None:
    """Merge a DriftReport's detections into mapping_dict, recording decisions."""
    renames = mapping_dict.setdefault("renames", {})
    lossy = mapping_dict.setdefault("lossy_casts", {})
    dataloss = mapping_dict.setdefault("acknowledged_data_loss", {})

    for old, new, conf in report.rename_suggestions:
        if old not in renames:
            renames[old] = new
            result.renames.append((old, new, conf))
    for d in report.lossy:
        col = d["column"]
        if col not in lossy:
            lossy[col] = {
                "from": d["from"], "to": d["to"],
                "ack_date": today, "ack_by": ACK_BY,
                "reason": f"{d['from']} -> {d['to']}: {d['reason']}",
            }
            result.lossy.append(AckDecision("lossy", col,
                                            f"{d['from']} -> {d['to']}: {d['reason']}",
                                            d["files_present"]))
    rename_sources = set(renames.keys())
    for d in report.data_loss:
        col = d["column"]
        if col in rename_sources or col in dataloss:
            continue  # rename beats data-loss
        dataloss[col] = {
            "ack_date": today, "ack_by": ACK_BY,
            "reason": f"column dropped; had data in {d['files_present']} file(s)",
        }
        result.data_loss.append(AckDecision("data_loss", col,
                                            f"had data in {d['files_present']} file(s)",
                                            d["files_present"]))


def _write_mapping(mapping_path: Path, mapping_dict: dict, data_type: str, today: str) -> None:
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    header = (f"# Auto-curated by `taxi-curate-mappings {data_type}` on {today}.\n"
              f"# Every acknowledgment is machine-accepted; see CURATION-REPORT.md to verify.\n\n")
    body = yaml.safe_dump(mapping_dict, sort_keys=False, default_flow_style=False)
    mapping_path.write_text(header + body)


def _unresolved_total(raw_dir: Path, mapping: Mapping) -> int:
    conn = duckdb.connect(":memory:")
    all_files = sorted(raw_dir.rglob("*.parquet"))
    target = [f for f in all_files if f.name == mapping.target]
    if not target:
        raise FileNotFoundError(f"target {mapping.target} not found under {raw_dir}")
    target_md = get_file_metadata(conn, target[0])
    total = 0
    for f in all_files:
        total += len(plan_file(get_file_metadata(conn, f), target_md, mapping).unresolved)
    return total


def curate_type(data_type: str, raw_dir: Path, mapping_path: Path,
                today: Optional[str] = None) -> CurationResult:
    """Auto-accept all detected drift for one type into mapping_path; return the
    decisions made. Iterates detect->accept until the normalizer's planner has
    zero unresolved items across every raw file."""
    today = today or date.today().isoformat()
    result = CurationResult(data_type=data_type)

    existing: Optional[Mapping] = load_mapping(mapping_path) if mapping_path.exists() else None
    mapping_dict: dict = {}
    if existing is not None:
        mapping_dict = {
            "target": existing.target,
            "renames": dict(existing.renames),
            "lossy_casts": {c: {"from": e.from_type, "to": e.to_type, "ack_date": e.ack_date,
                                "ack_by": e.ack_by or ACK_BY,
                                **({"reason": e.reason} if e.reason else {})}
                            for c, e in existing.lossy_casts.items()},
            "acknowledged_data_loss": {c: {"ack_date": e.ack_date, "ack_by": e.ack_by or ACK_BY,
                                           **({"reason": e.reason} if e.reason else {})}
                                       for c, e in existing.acknowledged_data_loss.items()},
        }

    for _round in range(_MAX_ROUNDS):
        current = load_mapping(mapping_path) if mapping_path.exists() else existing
        report = detect_drift(data_type, raw_dir, current, sample="100%")
        if "target" not in mapping_dict:
            mapping_dict["target"] = report.target_name
        had = (len(report.rename_suggestions) + len(report.lossy) + len(report.data_loss))
        _accept_into(mapping_dict, report, today, result)
        _write_mapping(mapping_path, mapping_dict, data_type, today)
        if had == 0:
            break

    remaining = _unresolved_total(raw_dir, load_mapping(mapping_path))
    if remaining != 0:
        raise RuntimeError(
            f"{data_type}: {remaining} unresolved item(s) remain after auto-curation "
            f"(possible cyclic/ambiguous drift needing manual review)"
        )
    return result


def render_report(results: list[CurationResult], today: Optional[str] = None) -> str:
    today = today or date.today().isoformat()
    lines = [f"# Mapping curation report ({today})", "",
             "Machine-accepted by `taxi-curate-mappings`. **Verify the acknowledgment-required",
             "decisions below** (lossy casts and data-loss drops); renames are heuristic.", ""]
    for r in results:
        lines.append(f"## {r.data_type}")
        lines.append("")
        lines.append("### Acknowledgments required (verify these)")
        if not r.lossy and not r.data_loss:
            lines.append("- none")
        for d in r.lossy:
            lines.append(f"- **lossy cast** `{d.column}`: {d.detail} ({d.files_present} file(s))")
        for d in r.data_loss:
            lines.append(f"- **data loss** `{d.column}`: {d.detail}")
        lines.append("")
        lines.append("### Auto-accepted renames (heuristic)")
        if not r.renames:
            lines.append("- none")
        for old, new, conf in r.renames:
            lines.append(f"- `{old}` -> `{new}` (confidence {int(conf * 100)}%)")
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="taxi-curate-mappings",
        description="Auto-accept detected drift into mapping YAMLs + write an audit report.",
    )
    p.add_argument("data_type", nargs="?", choices=DATA_TYPES,
                   help="yellow/green/fhv/fhvhv. Omit to curate all four.")
    p.add_argument("--raw-dir", default="raw")
    p.add_argument("--mappings-dir", default="normalize/mappings")
    args = p.parse_args(argv)

    types = [args.data_type] if args.data_type else list(DATA_TYPES)
    today = date.today().isoformat()
    mappings_dir = Path(args.mappings_dir)
    results: list[CurationResult] = []
    rc = 0
    for t in types:
        raw_dir = Path(args.raw_dir) / t
        if not raw_dir.exists():
            print(f"{t}: no raw files at {raw_dir}, skipping")
            continue
        try:
            res = curate_type(t, raw_dir, mappings_dir / f"{t}.yaml", today=today)
        except (FileNotFoundError, RuntimeError) as e:
            print(f"error: {t}: {e}", file=sys.stderr)
            rc = max(rc, 2)
            continue
        results.append(res)
        print(f"{t}: {len(res.renames)} rename(s), {len(res.lossy)} lossy cast(s), "
              f"{len(res.data_loss)} data-loss drop(s) accepted.")
    if results:
        report_path = mappings_dir / "CURATION-REPORT.md"
        report_path.write_text(render_report(results, today=today) + "\n")
        print(f"Audit report written to {report_path}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --extra test pytest tests/taxi_orchestrate/test_curate.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add orchestrator/src/taxi_orchestrate/curate.py tests/taxi_orchestrate/conftest.py tests/taxi_orchestrate/test_curate.py
git commit -m "feat(orchestrator): taxi-curate-mappings (auto-accept drift + audit report)"
```

---

### Task 8: Curate the real mappings + end-to-end validation

**Files:**
- Create (generated + committed): `normalize/mappings/{yellow,green,fhv,fhvhv}.yaml`, `normalize/mappings/CURATION-REPORT.md`

**Interfaces:** none (uses `taxi-curate-mappings` + `taxi-run` against the real `raw/`).

- [ ] **Step 1: Curate all four types against the real data**

The real history is already in `./raw/`. Run:
```bash
uv run taxi-curate-mappings
```
Expected: per-type acceptance summary + `Audit report written to normalize/mappings/CURATION-REPORT.md`, exit 0. If any type reports unresolved items remaining (exit 2), STOP and surface it — that type has ambiguous/cyclic drift the auto-accepter can't resolve and needs a human look.

- [ ] **Step 2: Verify the mappings normalize cleanly (planner gate)**

Run:
```bash
uv run taxi-run --skip-download
```
Expected: per-type summary all `OK` (normalize exit 0 for all four), overall exit 0. This actually writes `raw-normalized/`. If any type shows `NEEDS REVIEW`, inspect its mapping / `CURATION-REPORT.md`.

- [ ] **Step 3: Review the audit report**

Read `normalize/mappings/CURATION-REPORT.md` and confirm the acknowledgment-required decisions (lossy casts + data-loss drops) look correct for each type. This is the human after-the-fact verification the design calls for.

- [ ] **Step 4: (Optional, if a SQL Server is available) end-to-end load**

```bash
docker run -d --name mssql-it -e ACCEPT_EULA=Y -e MSSQL_SA_PASSWORD='Str0ng_Passw0rd!' \
  -p 1433:1433 mcr.microsoft.com/mssql/server:2022-latest
# wait until ready, then:
MSSQL_PASSWORD='Str0ng_Passw0rd!' uv run taxi-run --skip-download --load
docker rm -f mssql-it
```
Expected: per-type summary `LOADED`, overall exit 0.

- [ ] **Step 5: Commit the curated mappings + report**

```bash
git add normalize/mappings/yellow.yaml normalize/mappings/green.yaml \
        normalize/mappings/fhv.yaml normalize/mappings/fhvhv.yaml \
        normalize/mappings/CURATION-REPORT.md
git commit -m "feat(normalize): curated mapping YAMLs for all four types + audit report"
```

---

### Task 9: Full-suite verification

**Files:** none.

- [ ] **Step 1: Run the entire repo test suite**

Run: `uv run --extra test pytest -q`
Expected: PASS, no regressions (loader integration tests skip without a server).

- [ ] **Step 2: Smoke the console scripts**

Run:
```bash
uv run taxi-run --help
uv run taxi-curate-mappings --help
uv run taxi-run --dry-run
```
Expected: help text for both; the dry-run prints a per-type plan and exits 0.

- [ ] **Step 3: Commit any final tidy-ups**

```bash
git add -A
git commit -m "test(orchestrator): full-suite verification pass" || echo "nothing to commit"
```

> **Docs note (out of scope, hand off to docs sub-project):** `orchestrator/README.md` points at `guides/orchestrator/`, which does not exist yet; add that MkDocs guide + nav entry alongside the other component guides.

---

## Self-review against the spec

- **Two deliverables** (`taxi-run` + `taxi-curate-mappings` + committed mappings): Tasks 1–5, 7, 8. ✅
- **Subprocess seam, cwd=root, `-m` invocation:** Task 3 (`stages.py`) + Task 5. ✅
- **Pure exit-code→outcome + halt/continue, `2>1>0`:** Task 2 (`pipeline.py`), exhaustively tested. ✅
- **CLI surface** (default download+normalize, `--load`, `--skip-download`, `--download-only`, `--recent`, `--sample`, `--dry-run`, forwarded loader flags, `choices`): Task 5. ✅
- **Load needs `MSSQL_PASSWORD`, never on argv/logged:** Task 3 (`build_load_cmd` omits password; env via `run`) + Task 5 (fast exit 2). ✅
- **normalize `detect_drift` extraction, behavior-preserving:** Task 6. ✅
- **Auto-accept (renames + lossy/data-loss acks, rename precedence, `ack_date`/`ack_by`/`reason`), zero-unresolved verification via the planner, audit report:** Task 7. ✅
- **Curate the real four mappings + CURATION-REPORT.md + end-to-end validation:** Task 8. ✅
- **Testing** (pure `pipeline`, `stages`, `report`, `cli`, stub run, `curate`; ~25–30): Tasks 2–7. ✅
- **Success criteria** (dry-run, needs-review halts + exit 1, load partial → exit 2, curation clean for four types, fresh-clone `taxi-run --skip-download` exit 0): covered by Tasks 5, 7, 8, 9. ✅
