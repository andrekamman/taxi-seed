"""Mapping YAML scaffold generator with amend semantics.

Analyzes raw/<type>/ files (schema + metadata + schema-drift rename detection)
and produces a YAML with:
- a machine-generated timeline header of detected drift transitions
- existing semantic content (renames/lossy_casts/acknowledged_data_loss) preserved
- new SUGGESTED entries (commented) for likely renames
- new TODO entries (commented) for lossy casts and potential data loss

On first run, generates the file from scratch. On subsequent runs, preserves
the human's committed content and appends only NEW SUGGESTED/TODO items.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional, Union

import duckdb

from schema_drift.analyze import analyze_data_type
from taxi_normalize.data_check import (
    aggregate_across_files,
    fits_in_target_type,
    get_file_metadata,
)
from taxi_normalize.mapping import Mapping, load_mapping


RENAME_CONFIDENCE_THRESHOLD = 0.6  # matches schema-drift's default


@dataclass
class BootstrapResult:
    """Report returned by bootstrap_type so callers can print appropriate messages."""
    was_new: bool                     # True if the YAML didn't exist before
    new_items: int                    # count of new SUGGESTED/TODO items added
    timeline: list[str] = field(default_factory=list)


@dataclass
class DriftReport:
    """Structured drift detection for a data type, relative to an existing mapping."""
    target_name: str
    timeline: list[str]
    rename_suggestions: list[tuple[str, str, float]]   # (old, new, confidence)
    lossy: list[dict]         # {column, from, to, reason, files_present}
    data_loss: list[dict]     # {column, files_present}


def _parse_sample(sample: str) -> Union[int, str]:
    """Convert CLI --sample value into what schema-drift's get_column_stats expects."""
    s = sample.strip()
    if s.endswith("%"):
        pct = int(s.rstrip("%"))
        if pct <= 0:
            return 0     # 0 -> no sampling
        if pct >= 100:
            return 0     # 100% -> no sampling
        return f"{pct}%"
    if s.isdigit():
        return int(s)
    raise ValueError(f"Invalid --sample value: {sample!r}. Use N or N%.")


def _summarize_change(change) -> str:
    """One-line human-readable summary of a schema-drift SchemaChange transition."""
    parts = []
    if change.columns_renamed:
        r = ", ".join(f"{c.old_col.name}->{c.new_col.name}" for c in change.columns_renamed)
        parts.append(f"renamed {r}")
    if change.columns_added:
        a = ", ".join(c.name for c in change.columns_added)
        parts.append(f"added {a}")
    if change.columns_removed:
        d = ", ".join(c.name for c in change.columns_removed)
        parts.append(f"dropped {d}")
    if change.columns_type_changed:
        t = ", ".join(f"{old.name}({old.dtype}->{new.dtype})" for old, new in change.columns_type_changed)
        parts.append(f"type-changed {t}")
    detail = "; ".join(parts) if parts else "schema signature changed"
    return f"{change.period_from} -> {change.period_to}: {detail}"


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


def bootstrap_type(
    data_type: str,
    raw_dir: Path,
    output_yaml: Path,
    sample: str = "100%",
) -> BootstrapResult:
    """Generate or amend the mapping YAML at output_yaml.

    - If output_yaml does not exist: writes it fresh with all detected
      SUGGESTED/TODO scaffolding.
    - If output_yaml exists: preserves existing renames/lossy_casts/
      acknowledged_data_loss entries verbatim, and appends new SUGGESTED/TODO
      items for anything the existing mapping does not already handle.

    Never rewrites human comments in the file body — the timeline header is
    regenerated on every run based on the current data.
    """
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


def _emit_yaml(
    *,
    output_yaml: Path,
    data_type: str,
    target_name: str,
    existing: Optional[Mapping],
    timeline: list[str],
    new_rename_suggestions: list[tuple[str, str, float]],
    new_lossy_todos: list[dict],
    new_data_loss_todos: list[dict],
) -> None:
    """Emit the mapping YAML as text (comments + existing + new commented items)."""
    lines: list[str] = []

    today = date.today().isoformat()
    verb = "Generated" if existing is None else "Amended"
    lines.append(f"# {verb} by `normalize {data_type}` on {today}.")
    if existing is None:
        lines.append("# Review each SUGGESTED entry: uncomment to accept, delete to reject.")
        lines.append("# Fill in `ack_date:` for each TODO to acknowledge lossy casts or data loss.")
    else:
        lines.append("# Existing entries preserved; new SUGGESTED/TODO items appended.")
        lines.append("# Review each new SUGGESTED entry: uncomment to accept, delete to reject.")
    if timeline:
        lines.append("# Detected drift transitions:")
        for tl in timeline:
            lines.append(f"#   {tl}")
    lines.append("")
    lines.append(f"target: {target_name}")
    lines.append("")

    # renames section
    lines.append("renames:")
    existing_renames = existing.renames if existing else {}
    for old, new in sorted(existing_renames.items()):
        lines.append(f"  {old}: {new}")
    for old, new, conf in new_rename_suggestions:
        verified = "data-verified" if conf >= 0.8 else "NOT data-verified - review carefully"
        lines.append(f"  # SUGGESTED (confidence {int(conf*100)}%, {verified}) - uncomment to accept:")
        lines.append(f"  # {old}: {new}")
    if not existing_renames and not new_rename_suggestions:
        lines.append("  {}")
    lines.append("")

    # lossy_casts section
    lines.append("lossy_casts:")
    existing_lossy = existing.lossy_casts if existing else {}
    for col, entry in sorted(existing_lossy.items()):
        lines.append(f"  {col}:")
        lines.append(f"    from: {entry.from_type}")
        lines.append(f"    to: {entry.to_type}")
        lines.append(f"    ack_date: {entry.ack_date}")
        if entry.ack_by:
            lines.append(f"    ack_by: {entry.ack_by}")
        if entry.reason:
            lines.append(f"    reason: {_yaml_scalar(entry.reason)}")
    for entry in new_lossy_todos:
        lines.append(f"  # DETECTED: {entry['column']} changed {entry['from']} -> {entry['to']}. {entry['reason']}")
        lines.append("  # Set ack_date to accept (ack_by and reason are optional):")
        lines.append(f"  # {entry['column']}:")
        lines.append(f"  #   from: {entry['from']}")
        lines.append(f"  #   to: {entry['to']}")
        lines.append("  #   ack_date: TODO")
    if not existing_lossy and not new_lossy_todos:
        lines.append("  {}")
    lines.append("")

    # acknowledged_data_loss section
    lines.append("acknowledged_data_loss:")
    existing_data_loss = existing.acknowledged_data_loss if existing else {}
    for col, entry in sorted(existing_data_loss.items()):
        lines.append(f"  {col}:")
        lines.append(f"    ack_date: {entry.ack_date}")
        if entry.ack_by:
            lines.append(f"    ack_by: {entry.ack_by}")
        if entry.reason:
            lines.append(f"    reason: {_yaml_scalar(entry.reason)}")
    for entry in new_data_loss_todos:
        lines.append(f"  # DETECTED: {entry['column']} has non-null data in {entry['files_present']} file(s),")
        lines.append("  # no rename candidate above the confidence threshold.")
        lines.append("  # Set ack_date to accept the loss (ack_by and reason are optional):")
        lines.append(f"  # {entry['column']}:")
        lines.append("  #   ack_date: TODO")
    if not existing_data_loss and not new_data_loss_todos:
        lines.append("  {}")

    output_yaml.parent.mkdir(parents=True, exist_ok=True)
    output_yaml.write_text("\n".join(lines) + "\n")


def _yaml_scalar(s: str) -> str:
    """Quote a string for safe YAML emission if it contains special characters."""
    if any(c in s for c in ':#"\'\n') or s != s.strip():
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return s
