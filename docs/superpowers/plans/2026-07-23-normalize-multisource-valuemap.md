# Multi-source Renames + value_map Policies — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let normalize map multiple historical column names to one target (multi-source rename) and let `value_maps` carry `null` targets + an `on_unmapped: null|error` policy — then complete yellow's mapping so its 2009-2010 pickup/dropoff datetimes, vendor, store-and-fwd flag, and valid rate codes are preserved instead of dropped.

**Architecture:** Two small changes to the `normalize` planner/mapping/executor, then hand-authored yellow mapping additions (which `taxi-curate-mappings` preserves). No new dependencies.

**Tech Stack:** Python 3.12, DuckDB, existing `taxi_normalize` (mapping/planner/executor/data_check), pytest.

## Global Constraints

- **Behavior unchanged for existing single-source renames and strict value_maps.** The whole `tests/taxi_normalize/` + `tests/taxi_orchestrate/` suites must stay green.
- **Multi-source rename:** multiple `old: new` entries may share a `new`. The planner picks the source **present in the current file**. If **>1 source is present with the target absent**, emit an `Unresolved(kind="ambiguous_multisource_rename")` (fail-loud via normalize's existing exit-1/report path — do NOT silently pick one). If the target column is present directly, Case A wins.
- **value_map forms:** `{value: target}` (strict, `on_unmapped=error`, default) OR `{map: {value: target}, on_unmapped: null|error}`. Detect the policy form by `set(entry) <= {"map","on_unmapped"}` with `entry["map"]` a dict. Target values may be `null` (→ SQL NULL). `_sql_lit(None)` renders `NULL`.
- **value_map executor semantics:** source NULL → NULL; mapped value → its target (incl. explicit NULL); unmapped non-null → `error(...)` (strict) or `NULL` (`on_unmapped: null`).
- **DDS is mapped explicitly to null** in `VendorID` (strict), so a genuinely-new vendor still errors.
- `taxi-curate-mappings` must **preserve** hand-authored value_maps *including* their `on_unmapped` policy (round-trip the policy form in `_mapping_to_dict`).
- DRY, YAGNI, TDD, frequent commits.

---

### Task 1: Multi-source rename in the planner

**Files:**
- Modify: `normalize/src/taxi_normalize/planner.py`
- Test: `tests/taxi_normalize/test_multisource_rename.py`

**Interfaces:**
- `ColumnAction` gains `value_map_unmapped: str = "error"` (used by the value_map action; set here, consumed by Task 2's executor).
- Planner builds `inv_renames: dict[target, list[source]]` and selects the present source per file.

- [ ] **Step 1: Write the failing tests**

Create `tests/taxi_normalize/test_multisource_rename.py`:
```python
from taxi_normalize.mapping import Mapping
from taxi_normalize.planner import plan_file


def _md(type_map):
    return {c: {"type": t, "min": 1, "max": 9, "null_count": 0, "num_rows": 9} for c, t in type_map.items()}


def test_first_era_source_renamed():
    raw = _md({"Trip_Pickup_DateTime": "TIMESTAMP", "vendorid": "BIGINT"})
    target = _md({"tpep_pickup_datetime": "TIMESTAMP", "vendorid": "BIGINT"})
    mapping = Mapping(target="t.parquet",
                      renames={"Trip_Pickup_DateTime": "tpep_pickup_datetime",
                               "pickup_datetime": "tpep_pickup_datetime"})
    plan = plan_file(raw, target, mapping)
    assert plan.unresolved == []
    r = next(a for a in plan.actions if a.target_column == "tpep_pickup_datetime")
    assert r.action == "rename" and r.source_column == "Trip_Pickup_DateTime"


def test_second_era_source_renamed():
    raw = _md({"pickup_datetime": "TIMESTAMP", "vendorid": "BIGINT"})
    target = _md({"tpep_pickup_datetime": "TIMESTAMP", "vendorid": "BIGINT"})
    mapping = Mapping(target="t.parquet",
                      renames={"Trip_Pickup_DateTime": "tpep_pickup_datetime",
                               "pickup_datetime": "tpep_pickup_datetime"})
    r = next(a for a in plan_file(raw, target, mapping).actions if a.target_column == "tpep_pickup_datetime")
    assert r.action == "rename" and r.source_column == "pickup_datetime"


def test_both_sources_present_is_unresolved():
    raw = _md({"Trip_Pickup_DateTime": "TIMESTAMP", "pickup_datetime": "TIMESTAMP", "vendorid": "BIGINT"})
    target = _md({"tpep_pickup_datetime": "TIMESTAMP", "vendorid": "BIGINT"})
    mapping = Mapping(target="t.parquet",
                      renames={"Trip_Pickup_DateTime": "tpep_pickup_datetime",
                               "pickup_datetime": "tpep_pickup_datetime"})
    plan = plan_file(raw, target, mapping)
    assert any(u.kind == "ambiguous_multisource_rename" for u in plan.unresolved)


def test_target_present_directly_wins():
    raw = _md({"tpep_pickup_datetime": "TIMESTAMP", "vendorid": "BIGINT"})
    target = _md({"tpep_pickup_datetime": "TIMESTAMP", "vendorid": "BIGINT"})
    mapping = Mapping(target="t.parquet",
                      renames={"Trip_Pickup_DateTime": "tpep_pickup_datetime"})
    plan = plan_file(raw, target, mapping)
    assert plan.unresolved == []
    a = next(a for a in plan.actions if a.target_column == "tpep_pickup_datetime")
    assert a.action == "passthrough"


def test_single_source_rename_unchanged():
    raw = _md({"old": "BIGINT"})
    target = _md({"new": "BIGINT"})
    mapping = Mapping(target="t.parquet", renames={"old": "new"})
    a = next(a for a in plan_file(raw, target, mapping).actions if a.target_column == "new")
    assert a.action == "rename" and a.source_column == "old"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/taxi_normalize/test_multisource_rename.py -v`
Expected: `test_both_sources_present_is_unresolved` fails (current single-inverse planner silently picks one; no ambiguous unresolved) — and possibly the era tests depending on dict order.

- [ ] **Step 3: Implement the planner change**

In `normalize/src/taxi_normalize/planner.py`, add the field to `ColumnAction`:
```python
@dataclass
class ColumnAction:
    action: str  # "passthrough" | "rename" | "cast" | "null_fill" | "value_map"
    source_column: Optional[str] = None
    target_column: Optional[str] = None
    cast_to: Optional[str] = None
    target_type: Optional[str] = None
    value_map: Optional[dict] = None
    value_map_unmapped: str = "error"  # for value_map actions: "error" | "null"
```

Replace the single inverse map:
```python
    rename_of = mapping.renames                          # raw -> target
    inv_renames = {v: k for k, v in rename_of.items()}   # target -> raw
```
with a multi-source inverse:
```python
    rename_of = mapping.renames                          # raw -> target
    inv_renames: dict[str, list[str]] = {}               # target -> [sources]
    for _old, _new in rename_of.items():
        inv_renames.setdefault(_new, []).append(_old)
```

Replace the Case A value_map branch to thread the policy:
```python
            elif tgt_col in mapping.value_maps:
                actions.append(ColumnAction(action="value_map", source_column=tgt_col, target_column=tgt_col,
                                            target_type=tgt_type, value_map=mapping.value_maps[tgt_col],
                                            value_map_unmapped=mapping.value_map_unmapped.get(tgt_col, "error")))
```

Replace the whole Case B block with the multi-source version:
```python
        # Case B: target column absent from raw, but mapping renames some raw col INTO it
        if tgt_col in inv_renames:
            present = [s for s in inv_renames[tgt_col] if s in raw_cols]
            if len(present) > 1:
                unresolved.append(Unresolved(
                    column=tgt_col, kind="ambiguous_multisource_rename",
                    details=f"multiple sources {present} present for target {tgt_col} in one file; "
                            f"only one historical name should appear per file",
                ))
                continue
            if present:
                src = present[0]
                raw_stats = raw_metadata[src]
                raw_type = raw_stats["type"]
                if raw_type == tgt_type:
                    actions.append(ColumnAction(action="rename", source_column=src, target_column=tgt_col))
                elif tgt_col in mapping.value_maps:
                    actions.append(ColumnAction(action="value_map", source_column=src, target_column=tgt_col,
                                                target_type=tgt_type, value_map=mapping.value_maps[tgt_col],
                                                value_map_unmapped=mapping.value_map_unmapped.get(tgt_col, "error")))
                elif _cast_is_safe(raw_stats, tgt_type):
                    actions.append(ColumnAction(action="rename", source_column=src, target_column=tgt_col, cast_to=tgt_type))
                else:
                    if tgt_col in mapping.lossy_casts or src in mapping.lossy_casts:
                        actions.append(ColumnAction(action="rename", source_column=src, target_column=tgt_col, cast_to=tgt_type))
                    else:
                        unresolved.append(Unresolved(
                            column=src,
                            kind="unacked_lossy_cast",
                            details=f"rename {src}->{tgt_col} with type {raw_type} -> {tgt_type} would lose data",
                        ))
                continue
```
(Note: `Mapping.value_map_unmapped` is added in Task 2. Until then this references an attribute that
doesn't exist — Task 2 lands the mapping field. If implementing Task 1 alone, add a temporary
`value_map_unmapped: dict = field(default_factory=dict)` to `Mapping`; Task 2 finalizes its parsing.
Recommended: implement Tasks 1 and 2 together, or land Task 2's `Mapping` field first.)

- [ ] **Step 4: Run to verify it passes**

Run:
```bash
uv run --extra test pytest tests/taxi_normalize/test_multisource_rename.py tests/taxi_normalize/ -v
```
Expected: the 5 new tests PASS and the whole `tests/taxi_normalize/` suite stays green.

- [ ] **Step 5: Commit**

```bash
git add normalize/src/taxi_normalize/planner.py tests/taxi_normalize/test_multisource_rename.py
git commit -m "feat(normalize): multi-source renames (target <- several historical names)"
```

---

### Task 2: value_map NULL values + on_unmapped policy

**Files:**
- Modify: `normalize/src/taxi_normalize/mapping.py`, `normalize/src/taxi_normalize/executor.py`, `orchestrator/src/taxi_orchestrate/curate.py`
- Test: `tests/taxi_normalize/test_value_map_policy.py`

**Interfaces:**
- `Mapping` gains `value_map_unmapped: dict[str, str]` (`{column: "error" | "null"}`, default `error`).
- `load_mapping` parses both value_map forms.
- `executor._sql_lit(None) -> "NULL"`; the `value_map` action's `ELSE` is `error(...)` or `NULL` per policy.
- `curate._mapping_to_dict` round-trips the policy form.

- [ ] **Step 1: Write the failing tests**

Create `tests/taxi_normalize/test_value_map_policy.py`:
```python
from pathlib import Path

import duckdb
import pytest

from taxi_normalize.data_check import get_file_metadata
from taxi_normalize.executor import _sql_lit, execute_transform
from taxi_normalize.mapping import Mapping, load_mapping
from taxi_normalize.planner import plan_file


def test_sql_lit_none_is_null():
    assert _sql_lit(None) == "NULL"


def test_parse_strict_and_policy_forms(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(
        "target: t.parquet\n"
        "value_maps:\n"
        "  VendorID:\n"
        "    CMT: 1\n"
        "    DDS: null\n"
        "  RatecodeID:\n"
        "    map:\n"
        "      '1': 1\n"
        "      '6': 6\n"
        "    on_unmapped: 'null'\n"
    )
    m = load_mapping(p)
    assert m.value_maps["VendorID"] == {"CMT": 1, "DDS": None}
    assert m.value_map_unmapped.get("VendorID", "error") == "error"
    assert m.value_maps["RatecodeID"] == {"1": 1, "6": 6}
    assert m.value_map_unmapped["RatecodeID"] == "null"


def test_bad_policy_rejected(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("target: t.parquet\nvalue_maps:\n  c:\n    map: {'1': 1}\n    on_unmapped: bogus\n")
    from taxi_normalize.mapping import MappingError
    with pytest.raises(MappingError):
        load_mapping(p)


def test_on_unmapped_null_discards_invalid(tmp_path):
    # same-name RatecodeID that is VARCHAR in the source: valid codes map, invalid -> NULL.
    conn = duckdb.connect(":memory:")
    src = tmp_path / "raw/yellow/2010/f.parquet"
    src.parent.mkdir(parents=True, exist_ok=True)
    conn.execute(f"COPY (SELECT * FROM (VALUES (1,'1'),(2,'6'),(3,'128')) AS t(vendorid, RatecodeID)) TO '{src}' (FORMAT PARQUET)")
    tgt = tmp_path / "raw/yellow/2024/f.parquet"
    tgt.parent.mkdir(parents=True, exist_ok=True)
    conn.execute(f"COPY (SELECT 1 AS vendorid, CAST(1 AS BIGINT) AS RatecodeID) TO '{tgt}' (FORMAT PARQUET)")
    mapping = Mapping(target="f.parquet", value_maps={"RatecodeID": {"1": 1, "6": 6}},
                      value_map_unmapped={"RatecodeID": "null"})
    out = tmp_path / "o.parquet"
    execute_transform(conn, plan_file(get_file_metadata(conn, src),
                      get_file_metadata(conn, tgt), mapping), src, out)
    got = conn.execute(f"SELECT vendorid, RatecodeID FROM '{out}' ORDER BY vendorid").fetchall()
    assert got == [(1, 1), (2, 6), (3, None)]   # '128' unmapped -> NULL


def test_on_unmapped_error_raises(tmp_path):
    conn = duckdb.connect(":memory:")
    src = tmp_path / "raw/yellow/2010/f.parquet"
    src.parent.mkdir(parents=True, exist_ok=True)
    conn.execute(f"COPY (SELECT * FROM (VALUES (1,'X')) AS t(vendorid, RatecodeID)) TO '{src}' (FORMAT PARQUET)")
    tgt = tmp_path / "raw/yellow/2024/f.parquet"
    tgt.parent.mkdir(parents=True, exist_ok=True)
    conn.execute(f"COPY (SELECT 1 AS vendorid, CAST(1 AS BIGINT) AS RatecodeID) TO '{tgt}' (FORMAT PARQUET)")
    mapping = Mapping(target="f.parquet", value_maps={"RatecodeID": {"1": 1}})  # strict; 'X' unmapped
    out = tmp_path / "o.parquet"
    with pytest.raises(duckdb.Error, match="unmapped value"):
        execute_transform(conn, plan_file(get_file_metadata(conn, src),
                          get_file_metadata(conn, tgt), mapping), src, out)


def test_value_map_null_target(tmp_path):
    conn = duckdb.connect(":memory:")
    src = tmp_path / "raw/yellow/2009/f.parquet"
    src.parent.mkdir(parents=True, exist_ok=True)
    conn.execute(f"COPY (SELECT * FROM (VALUES (1,'CMT'),(2,'DDS')) AS t(vendorid, VendorID)) TO '{src}' (FORMAT PARQUET)")
    tgt = tmp_path / "raw/yellow/2024/f.parquet"
    tgt.parent.mkdir(parents=True, exist_ok=True)
    conn.execute(f"COPY (SELECT 1 AS vendorid, CAST(1 AS BIGINT) AS VendorID) TO '{tgt}' (FORMAT PARQUET)")
    mapping = Mapping(target="f.parquet", value_maps={"VendorID": {"CMT": 1, "DDS": None}})
    out = tmp_path / "o.parquet"
    execute_transform(conn, plan_file(get_file_metadata(conn, src),
                      get_file_metadata(conn, tgt), mapping), src, out)
    assert conn.execute(f"SELECT vendorid, VendorID FROM '{out}' ORDER BY vendorid").fetchall() == [(1, 1), (2, None)]
```
(Delete the incomplete `test_on_unmapped_null_discards_invalid` stub above and rely on the
rename-based end-to-end validation in Task 3 for the on_unmapped:null path over a real rename; the
`error` and `null-target` executor paths are covered here.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/taxi_normalize/test_value_map_policy.py -v`
Expected: FAIL — `Mapping` has no `value_map_unmapped`; `_sql_lit(None)` returns `'None'`.

- [ ] **Step 3: Implement mapping parsing**

In `normalize/src/taxi_normalize/mapping.py`, add the field to `Mapping`:
```python
    value_maps: dict[str, dict] = field(default_factory=dict)
    value_map_unmapped: dict[str, str] = field(default_factory=dict)  # column -> "error" | "null"
```
Replace the value_maps parsing block:
```python
    value_maps: dict[str, dict] = {}
    value_map_unmapped: dict[str, str] = {}
    for col, entry in (raw.get("value_maps") or {}).items():
        if not isinstance(entry, dict) or not entry:
            raise MappingError(
                f"value_maps.{col} must be a non-empty dict of {{source_value: target_value}}"
            )
        if set(entry.keys()) <= {"map", "on_unmapped"} and isinstance(entry.get("map"), dict):
            vm = entry["map"]
            if not vm:
                raise MappingError(f"value_maps.{col}.map must be non-empty")
            policy = entry.get("on_unmapped", "error")
            if policy not in ("error", "null"):
                raise MappingError(
                    f"value_maps.{col}.on_unmapped must be 'error' or 'null', got {policy!r}"
                )
        else:
            vm = entry
            policy = "error"
        value_maps[col] = {str(k): v for k, v in vm.items()}
        value_map_unmapped[col] = policy
```
And pass it into the `Mapping(...)` construction:
```python
    return Mapping(
        target=target,
        renames=renames,
        lossy_casts=lossy_casts,
        acknowledged_data_loss=data_loss,
        value_maps=value_maps,
        value_map_unmapped=value_map_unmapped,
    )
```

- [ ] **Step 4: Implement the executor change**

In `normalize/src/taxi_normalize/executor.py`, make `_sql_lit` handle `None`:
```python
def _sql_lit(value) -> str:
    """Render a Python scalar as a SQL literal (None->NULL, numbers bare, strings quoted)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"
```
And update the `value_map` action to honor the policy:
```python
    if action.action == "value_map":
        src = _quote(action.source_column)
        tgt = _quote(action.target_column)
        whens = " ".join(
            f"WHEN CAST({src} AS VARCHAR) = {_sql_lit(str(k))} THEN {_sql_lit(v)}"
            for k, v in action.value_map.items()
        )
        if action.value_map_unmapped == "null":
            els = "NULL"
        else:
            els = (
                "error('value_map: unmapped value in " + action.source_column.replace("'", "''")
                + ": ' || CAST(" + src + " AS VARCHAR))"
            )
        return (
            f"CAST(CASE WHEN {src} IS NULL THEN NULL {whens} ELSE {els} END "
            f"AS {action.target_type}) AS {tgt}"
        )
```

- [ ] **Step 5: Preserve the policy in curate**

In `orchestrator/src/taxi_orchestrate/curate.py`, update `_mapping_to_dict` to round-trip the policy form:
```python
        # Preserve hand-authored value_maps (+ their on_unmapped policy).
        if existing.value_maps:
            vm_out = {}
            for c, m in existing.value_maps.items():
                if existing.value_map_unmapped.get(c, "error") == "null":
                    vm_out[c] = {"map": dict(m), "on_unmapped": "null"}
                else:
                    vm_out[c] = dict(m)
            d["value_maps"] = vm_out
```

- [ ] **Step 6: Run to verify it passes**

Run:
```bash
uv run --extra test pytest tests/taxi_normalize/test_value_map_policy.py tests/taxi_normalize/ tests/taxi_orchestrate/ -v
```
Expected: new tests PASS; both suites stay green (existing value_map + curate tests unaffected).

- [ ] **Step 7: Commit**

```bash
git add normalize/src/taxi_normalize/mapping.py normalize/src/taxi_normalize/executor.py orchestrator/src/taxi_orchestrate/curate.py tests/taxi_normalize/test_value_map_policy.py
git commit -m "feat(normalize): value_map null targets + on_unmapped policy; curate preserves it"
```

---

### Task 3: Complete yellow's mapping + validate end-to-end

**Files:**
- Modify: `normalize/mappings/yellow.yaml` (hand-authored additions)
- Modify (regenerated): `normalize/mappings/CURATION-REPORT.md`

**Interfaces:** none (uses `taxi-curate-mappings yellow` + `taxi-run yellow --skip-download`).

- [ ] **Step 1: Confirm the DOUBLE→string forms for the store_and_fwd value_map**

Run:
```bash
uv run python -c "import duckdb; c=duckdb.connect(); print(c.execute(\"SELECT CAST(CAST(0.0 AS DOUBLE) AS VARCHAR), CAST(CAST(1.0 AS DOUBLE) AS VARCHAR)\").fetchone())"
```
Record the exact strings (expected `('0.0','1.0')`); use them as the `store_and_fwd_flag` value_map keys. If DuckDB renders them differently (e.g. `'0'`/`'1'`), use whatever it prints.

- [ ] **Step 2: Author the yellow mapping additions**

Edit `normalize/mappings/yellow.yaml`. Keep the existing `target`, the auto-curated `renames`/`lossy_casts`/`acknowledged_data_loss`, and the `payment_type` value_map. Then:

1. **Move these columns OUT of `acknowledged_data_loss`** (delete their entries): `Trip_Pickup_DateTime`, `Trip_Dropoff_DateTime`, `pickup_datetime`, `dropoff_datetime`, `vendor_name`, `vendor_id`, `store_and_forward`, `rate_code`.
2. **Add to `renames:`** (multi-source; several share a target):
```yaml
  Trip_Pickup_DateTime: tpep_pickup_datetime
  pickup_datetime: tpep_pickup_datetime
  Trip_Dropoff_DateTime: tpep_dropoff_datetime
  dropoff_datetime: tpep_dropoff_datetime
  vendor_name: VendorID
  vendor_id: VendorID
  store_and_forward: store_and_fwd_flag
  rate_code: RatecodeID
```
3. **Add to `value_maps:`** (use the confirmed `store_and_fwd_flag` key strings from Step 1):
```yaml
  VendorID:
    CMT: 1
    VTS: 2
    DDS: null
  store_and_fwd_flag:
    '0.0': N
    '1.0': Y
  RatecodeID:
    map:
      '1': 1
      '2': 2
      '3': 3
      '4': 4
      '5': 5
      '6': 6
    on_unmapped: 'null'
```

- [ ] **Step 3: Re-run curation (must preserve the additions) and confirm it stays clean**

Run:
```bash
uv run taxi-curate-mappings yellow
```
Expected: exit 0, `Audit report written…`. Confirm the additions survived:
```bash
grep -E "tpep_pickup_datetime|vendor_name: VendorID|on_unmapped" normalize/mappings/yellow.yaml
```
Expected: the multi-source renames and the `RatecodeID` policy form are still present (curate preserved them and did not re-drop the moved columns). If any moved column reappears under `acknowledged_data_loss`, STOP — the rename/value_map isn't resolving it; investigate before proceeding.

- [ ] **Step 4: Re-normalize yellow and spot-check the 2009-2010 columns**

Run:
```bash
rm -rf raw-normalized/yellow
uv run taxi-run yellow --skip-download
```
Expected: `yellow … OK` (exit 0). Then spot-check that the recovered columns are populated (not all-NULL) in the early years:
```bash
uv run python - <<'PY'
import duckdb, glob
c = duckdb.connect()
for year in ("2009", "2010"):
    f = sorted(glob.glob(f"raw-normalized/yellow/{year}/*.parquet"))[0]
    row = c.execute(f"""
        SELECT count(tpep_pickup_datetime) AS pu, count(tpep_dropoff_datetime) AS do,
               count(VendorID) AS vend, count(store_and_fwd_flag) AS saf,
               count(RatecodeID) AS rc, count(*) AS n
        FROM '{f}'""").fetchone()
    print(year, dict(zip(["pu","do","vend","saf","rc","n"], row)))
    # VendorID codes present, store_and_fwd_flag is Y/N
    print("  VendorID distinct:", [r[0] for r in c.execute(f"SELECT DISTINCT VendorID FROM '{f}'").fetchall()])
    print("  store_and_fwd distinct:", [r[0] for r in c.execute(f"SELECT DISTINCT store_and_fwd_flag FROM '{f}'").fetchall()])
PY
```
Expected: `pu`/`do` counts > 0 for both years (datetimes preserved), `vend` mostly populated (DDS rows NULL), `saf` is `Y`/`N`, `rc` populated. If pickup/dropoff counts are 0, the datetime VARCHAR→TIMESTAMP cast or the rename isn't firing — STOP and investigate (the 2009/2010 datetime string format may need a different cast).

- [ ] **Step 5: Commit the completed mapping + report**

```bash
git add normalize/mappings/yellow.yaml normalize/mappings/CURATION-REPORT.md
git commit -m "feat(normalize): complete yellow mapping — recover 2009-2010 datetime/vendor/rate columns

Multi-source renames (Trip_Pickup_DateTime/pickup_datetime -> tpep_pickup_datetime, etc.),
VendorID value_map (CMT->1, VTS->2, DDS->null), store_and_fwd_flag (0->N,1->Y), and
RatecodeID (valid 1-6, else null). Only genuinely-removed TLC columns (old lat/long,
__index_level_0__) remain data-loss."
```

---

### Task 4: Full-suite verification

**Files:** none.

- [ ] **Step 1: Full repo suite**

Run: `uv run --extra test pytest -q`
Expected: PASS (loader integration tests skip without a server), no regressions.

- [ ] **Step 2: Confirm the other three types are unaffected**

Run: `uv run taxi-run green fhv fhvhv --skip-download` is not a valid multi-arg form; instead confirm via the already-normalized outputs are still valid, or spot-run one:
```bash
uv run taxi-run green --skip-download
```
Expected: `green … OK`.

- [ ] **Step 3: Commit any tidy-ups**

```bash
git add -A && git commit -m "test: full-suite verification for multi-source renames + value_map policies" || echo "nothing to commit"
```

---

## Self-review against the spec

- **Feature 1 (multi-source rename, present-source selection, ambiguity fail-loud):** Task 1 + tests. ✅
- **Feature 2 (null value, on_unmapped policy, both YAML forms, `_sql_lit(None)`):** Task 2 + tests. ✅
- **curate preserves value_maps incl. policy:** Task 2 Step 5. ✅
- **Yellow additions (datetimes, VendorID w/ DDS→null, store_and_fwd_flag, RatecodeID valid-else-null):** Task 3. ✅
- **Behavior unchanged for single-source/strict:** `test_single_source_rename_unchanged` + full-suite gate (Tasks 1, 4). ✅
- **End-to-end yellow exit 0 + populated 2009-2010 columns:** Task 3 Step 4. ✅
