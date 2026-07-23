"""taxi-curate-mappings: auto-accept detected drift into complete mapping YAMLs and write an
audit report of the acknowledgment-required decisions.

Drives off the normalizer's OWN planner (plan_file) — the authoritative gate — so the produced
mapping is exactly what `normalize` will accept with zero unresolved. detect_drift supplies
rename-candidate names and the target-file pin. The normalizer stays a strict human-in-the-loop
gate; this is a separate, deliberately-invoked bulk-accept utility. Every acknowledgment carries
ack_date/ack_by/reason; the report + committed YAMLs are the audit trail.
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
_MAX_ROUNDS = 8


@dataclass
class AckDecision:
    kind: str            # "lossy" | "data_loss"
    column: str
    detail: str
    files_present: int


@dataclass
class CurationResult:
    data_type: str
    renames: list = field(default_factory=list)      # (old, new, confidence)
    lossy: list = field(default_factory=list)        # AckDecision
    data_loss: list = field(default_factory=list)    # AckDecision


def _mapping_to_dict(existing: Optional[Mapping], target_name: str) -> dict:
    d = {"target": target_name, "renames": {}, "lossy_casts": {}, "acknowledged_data_loss": {}}
    if existing is not None:
        d["renames"] = dict(existing.renames)
        # Preserve hand-authored value_maps (+ their on_unmapped policy).
        if existing.value_maps:
            vm_out = {}
            for c, m in existing.value_maps.items():
                if existing.value_map_unmapped.get(c, "error") == "null":
                    vm_out[c] = {"map": dict(m), "on_unmapped": "null"}
                else:
                    vm_out[c] = dict(m)
            d["value_maps"] = vm_out
        for c, e in existing.lossy_casts.items():
            d["lossy_casts"][c] = {"from": e.from_type, "to": e.to_type,
                                   "ack_date": e.ack_date, "ack_by": e.ack_by or ACK_BY,
                                   **({"reason": e.reason} if e.reason else {})}
        for c, e in existing.acknowledged_data_loss.items():
            d["acknowledged_data_loss"][c] = {"ack_date": e.ack_date, "ack_by": e.ack_by or ACK_BY,
                                              **({"reason": e.reason} if e.reason else {})}
    return d


def _write_mapping(mapping_path: Path, mapping_dict: dict, data_type: str, today: str) -> None:
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    header = (f"# Auto-curated by `taxi-curate-mappings {data_type}` on {today}.\n"
              f"# Every acknowledgment is machine-accepted; see CURATION-REPORT.md to verify.\n\n")
    body = yaml.safe_dump(mapping_dict, sort_keys=False, default_flow_style=False)
    mapping_path.write_text(header + body)


def _list_files(raw_dir: Path, data_type: str) -> list[Path]:
    files = sorted(raw_dir.rglob(f"{data_type}_tripdata_*.parquet"))
    return files or sorted(raw_dir.rglob("*.parquet"))


def _unresolved_total(conn, files, target_md, mapping: Mapping) -> int:
    return sum(len(plan_file(get_file_metadata(conn, f), target_md, mapping).unresolved)
               for f in files)


_STRING_TYPES = ("VARCHAR", "CHAR", "TEXT", "STRING", "BPCHAR")
_NUMERIC_TYPES = ("TINYINT", "SMALLINT", "INTEGER", "INT", "BIGINT", "HUGEINT",
                  "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT",
                  "DOUBLE", "FLOAT", "REAL", "DECIMAL")
_TEMPORAL_TYPES = ("DATE", "TIME", "TIMESTAMP")


def _family(sql_type: str) -> str:
    t = (sql_type or "").upper()
    if t.startswith(_STRING_TYPES):
        return "string"
    if t.startswith(_NUMERIC_TYPES):
        return "numeric"
    if t.startswith(_TEMPORAL_TYPES):
        return "temporal"
    if t.startswith("BOOL"):
        return "bool"
    return "other"


def _cast_executable(src_type: str, tgt_type: str) -> bool:
    """Whether CAST(src -> tgt) will run for arbitrary values (metadata-level, conservative).

    Executable when: target is string (anything casts to string), same family, or
    numeric -> numeric. A string -> numeric/temporal cast can fail at runtime
    (e.g. early yellow Payment_Type 'CASH' -> BIGINT), so it is NOT executable —
    such a rename is dropped (data-loss) instead of producing a crashing mapping.
    """
    sf, tf = _family(src_type), _family(tgt_type)
    if tf == "string":
        return True
    if sf == tf:
        return True
    if sf == "numeric" and tf == "numeric":
        return True
    return False


def curate_type(data_type: str, raw_dir: Path, mapping_path: Path,
                today: Optional[str] = None) -> CurationResult:
    today = today or date.today().isoformat()
    result = CurationResult(data_type=data_type)
    conn = duckdb.connect(":memory:")

    files = _list_files(raw_dir, data_type)
    if not files:
        raise FileNotFoundError(f"No parquet files under {raw_dir} for {data_type}")

    existing = load_mapping(mapping_path) if mapping_path.exists() else None

    # detect_drift (existing=None) => ALL rename candidates + target pin.
    report = detect_drift(data_type, raw_dir, None, sample="100%")
    target_name = existing.target if existing else report.target_name
    rename_new: dict[str, str] = {}
    rename_conf: dict[str, float] = {}
    for old, new, conf in report.rename_suggestions:
        if old not in rename_conf or conf > rename_conf[old]:
            rename_new[old] = new
            rename_conf[old] = conf

    target_file = next((f for f in files if f.name == target_name), None)
    if target_file is None:
        raise FileNotFoundError(f"target {target_name} not found under {raw_dir}")
    target_md = get_file_metadata(conn, target_file)

    mapping_dict = _mapping_to_dict(existing, target_name)

    for _round in range(_MAX_ROUNDS):
        _write_mapping(mapping_path, mapping_dict, data_type, today)
        mapping = load_mapping(mapping_path)

        lossy_needed: dict[str, dict] = {}     # col -> {from, to, count, details}
        drop_needed: dict[str, dict] = {}      # col -> {count, src}
        for f in files:
            md = get_file_metadata(conn, f)
            for u in plan_file(md, target_md, mapping).unresolved:
                if u.kind == "unacked_lossy_cast":
                    from_type = md.get(u.column, {}).get("type", "UNKNOWN")
                    if u.column in target_md:
                        to_type = target_md[u.column]["type"]
                    elif u.column in mapping.renames and mapping.renames[u.column] in target_md:
                        to_type = target_md[mapping.renames[u.column]]["type"]
                    else:
                        to_type = "UNKNOWN"
                    e = lossy_needed.setdefault(
                        u.column, {"from": from_type, "to": to_type, "count": 0, "details": u.details})
                    e["count"] += 1
                elif u.kind == "unmapped_drop":
                    d = drop_needed.setdefault(u.column, {"count": 0, "src": md.get(u.column, {}).get("type", "UNKNOWN")})
                    d["count"] += 1

        if not lossy_needed and not drop_needed:
            break

        lc = mapping_dict["lossy_casts"]
        blocked: list[str] = []
        for col, e in lossy_needed.items():
            if col in lc:
                continue
            if not _cast_executable(e["from"], e["to"]):
                # A non-executable same-name cast (e.g. string -> numeric) cannot be
                # auto-acked without producing a crashing mapping; surface it.
                blocked.append(f"{col} ({e['from']} -> {e['to']})")
                continue
            reason = f"{e['from']} -> {e['to']}: {e['details']}"
            lc[col] = {"from": e["from"], "to": e["to"], "ack_date": today,
                       "ack_by": ACK_BY, "reason": reason}
            result.lossy.append(AckDecision("lossy", col, f"{e['from']} -> {e['to']}", e["count"]))
        if blocked:
            raise RuntimeError(
                f"{data_type}: non-executable cast(s) need manual mapping: {', '.join(blocked)}"
            )

        renames = mapping_dict["renames"]
        dl = mapping_dict["acknowledged_data_loss"]
        for col, d in drop_needed.items():
            if col in renames or col in dl:
                continue
            new = rename_new.get(col)
            value_mapped = new is not None and new in mapping_dict.get("value_maps", {})
            # Accept a rename when the cast will run (numeric/same-family/->string)
            # OR when the target column has a hand-authored value_map to convert it;
            # otherwise drop the column (data-loss) rather than emit a crashing cast.
            if new is not None and (value_mapped or _cast_executable(d["src"], target_md.get(new, {}).get("type", ""))):
                renames[col] = new
                result.renames.append((col, new, rename_conf.get(col, 0.0)))
            else:
                nfiles = d["count"]
                dl[col] = {"ack_date": today, "ack_by": ACK_BY,
                           "reason": f"column dropped; had data in {nfiles} file(s)"}
                result.data_loss.append(AckDecision("data_loss", col, f"had data in {nfiles} file(s)", nfiles))

    _write_mapping(mapping_path, mapping_dict, data_type, today)
    remaining = _unresolved_total(conn, files, target_md, load_mapping(mapping_path))
    if remaining:
        raise RuntimeError(
            f"{data_type}: {remaining} unresolved item(s) remain after {_MAX_ROUNDS} round(s) "
            f"of auto-curation (ambiguous/cyclic drift needing manual review)"
        )
    return result


def render_report(results: list[CurationResult], today: Optional[str] = None) -> str:
    today = today or date.today().isoformat()
    lines = [f"# Mapping curation report ({today})", "",
             "Machine-accepted by `taxi-curate-mappings`. **Verify the acknowledgment-required",
             "decisions** below (lossy casts and data-loss drops); renames are heuristic.", ""]
    for r in results:
        lines += [f"## {r.data_type}", "", "### Acknowledgments required (verify these)"]
        if not r.lossy and not r.data_loss:
            lines.append("- none")
        for d in r.lossy:
            lines.append(f"- **lossy cast** `{d.column}`: {d.detail} ({d.files_present} file(s))")
        for d in r.data_loss:
            lines.append(f"- **data loss** `{d.column}`: {d.detail}")
        lines += ["", "### Auto-accepted renames (heuristic)"]
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
