"""Mapping YAML scaffold generator.

Analyzes raw/<type>/ files (schema + metadata + schema-drift rename detection)
and writes a YAML scaffold with SUGGESTED entries for likely renames and TODO
entries for lossy casts and potential data loss.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

import duckdb

from schema_drift.analyze import (
    analyze_data_type,
    find_parquet_files,
    get_parquet_schema,
)
from schema_drift.renames import detect_renames_by_data
from taxi_normalize.data_check import (
    aggregate_across_files,
    fits_in_target_type,
    get_file_metadata,
)


RENAME_CONFIDENCE_THRESHOLD = 0.6  # matches schema-drift's default


def _parse_sample(sample: str) -> Union[int, str]:
    """Convert CLI --sample value into what schema-drift's get_column_stats expects."""
    s = sample.strip()
    if s.endswith("%"):
        pct = int(s.rstrip("%"))
        if pct <= 0:
            return 0     # 0 → no sampling
        if pct >= 100:
            return 0     # 100% → no sampling
        return f"{pct}%"
    if s.isdigit():
        return int(s)
    raise ValueError(f"Invalid --sample value: {sample!r}. Use N or N%.")


def bootstrap_type(
    data_type: str,
    raw_dir: Path,
    output_yaml: Path,
    sample: str = "100%",
) -> None:
    """Analyze raw_dir and write output_yaml with scaffolding.

    raw_dir: the type-scoped raw directory (e.g., raw/yellow/). May contain
             year subdirectories with parquet files (matches downloader layout).
    output_yaml: destination for the mapping file. Refuses to overwrite.
    sample: passed through to schema-drift's rename detector; default 100%.
    """
    if output_yaml.exists():
        raise FileExistsError(
            f"{output_yaml} already exists. Delete it or edit manually. "
            f"Re-run bootstrap after deletion to regenerate scaffolding."
        )

    # Collect files. raw_dir may be the top-level "raw/" or a per-type dir.
    files = sorted(raw_dir.rglob(f"{data_type}_tripdata_*.parquet"))
    if not files:
        files = sorted(raw_dir.rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet files found under {raw_dir} for {data_type}")

    target_file = files[-1]  # newest by filename sort

    conn = duckdb.connect(":memory:")
    files_md = [get_file_metadata(conn, f) for f in files]
    agg = aggregate_across_files(files_md)
    target_md = get_file_metadata(conn, target_file)
    target_cols = set(target_md.keys())

    # Ask schema-drift for rename candidates via its Python API.
    # Use the "parent of raw_dir" as data_dir if raw_dir is per-type, otherwise use raw_dir.
    if raw_dir.name == data_type:
        data_dir = raw_dir.parent
    else:
        data_dir = raw_dir
    sample_arg = _parse_sample(sample)
    analysis = analyze_data_type(
        conn, data_dir, data_type, verify_data=False, generic_mode=True,
        sample_size=sample_arg,
    )
    # analysis['changes'] is a list of SchemaChange objects at transition points.
    # Collect all rename candidates across transitions, keyed by (old_col, new_col).
    rename_candidates: dict[tuple[str, str], float] = {}
    for change in analysis["changes"]:
        for rename in change.columns_renamed:
            old = rename.old_col.name
            new = rename.new_col.name
            conf = rename.confidence
            existing = rename_candidates.get((old, new), 0)
            if conf > existing:
                rename_candidates[(old, new)] = conf

    # Determine per-column disposition
    lossy_cast_todos: list[dict] = []
    data_loss_todos: list[dict] = []

    for col, stats in agg.items():
        if col in target_cols:
            # Check whether the type change is lossy
            raw_type_seen = stats["types_seen"][0] if stats["types_seen"] else None
            tgt_type = target_md[col]["type"]
            if raw_type_seen and raw_type_seen != tgt_type:
                fits, reason = fits_in_target_type({"type": raw_type_seen, "min": stats["min_range"], "max": stats["max_range"]}, tgt_type)
                if not fits:
                    lossy_cast_todos.append({
                        "column": col, "from": raw_type_seen, "to": tgt_type, "reason": reason,
                    })
            continue
        # Column not in target
        if stats["files_with_data"] == 0:
            continue  # safe auto-drop
        # Is there a rename suggestion targeting an existing target column for this old col?
        renamed_to = [new for (old, new), conf in rename_candidates.items() if old == col and new in target_cols and conf >= RENAME_CONFIDENCE_THRESHOLD]
        if renamed_to:
            continue  # will be emitted as SUGGESTED rename below
        data_loss_todos.append({"column": col, "files_present": stats["files_present"]})

    # Emit YAML text with commented SUGGESTED lines and TODO placeholders.
    # We write text directly (not via yaml.dump) so we can include comments.
    lines: list[str] = []
    lines.append(f"# Generated by `normalize bootstrap {data_type}`. Review each SUGGESTED entry:")
    lines.append("# uncomment to accept, delete to reject. Fill in each TODO before running.")
    lines.append(f"target: {target_file.name}")
    lines.append("")
    lines.append("renames:")
    any_rename = False
    for (old, new), conf in sorted(rename_candidates.items(), key=lambda x: -x[1]):
        if new not in target_cols:
            continue
        verified = "data-verified" if conf >= 0.8 else "NOT data-verified — review carefully"
        lines.append(f"  # SUGGESTED (confidence {int(conf*100)}%, {verified}) — uncomment to accept:")
        lines.append(f"  # {old}: {new}")
        any_rename = True
    if not any_rename:
        lines.append("  {}")
    lines.append("")
    lines.append("lossy_casts:")
    if not lossy_cast_todos:
        lines.append("  {}")
    else:
        for entry in lossy_cast_todos:
            lines.append(f"  # DETECTED: {entry['column']} changed {entry['from']} -> {entry['to']}. {entry['reason']}")
            lines.append("  # Set ack_date to accept (ack_by and reason are optional):")
            lines.append(f"  # {entry['column']}:")
            lines.append(f"  #   from: {entry['from']}")
            lines.append(f"  #   to: {entry['to']}")
            lines.append("  #   ack_date: TODO")
    lines.append("")
    lines.append("acknowledged_data_loss:")
    if not data_loss_todos:
        lines.append("  {}")
    else:
        for entry in data_loss_todos:
            lines.append(f"  # DETECTED: {entry['column']} has non-null data in {entry['files_present']} file(s),")
            lines.append("  # no rename candidate above the confidence threshold.")
            lines.append("  # Set ack_date to accept the loss (ack_by and reason are optional):")
            lines.append(f"  # {entry['column']}:")
            lines.append("  #   ack_date: TODO")

    output_yaml.parent.mkdir(parents=True, exist_ok=True)
    output_yaml.write_text("\n".join(lines) + "\n")
