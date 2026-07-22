"""Per-column action planning.

Given a raw file's metadata, a target schema, and a mapping, decides one of:
  - passthrough  (column present unchanged in target)
  - rename       (raw column name differs from target, per mapping)
  - cast         (raw type differs from target — auto if safe, per mapping if lossy)
  - null_fill    (column in target but not in raw)
  - (drop, implicit) — column in raw but not in target, either all-null or acked_data_loss
  - unresolved   — anything the planner can't decide without human input
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from taxi_normalize.data_check import fits_in_target_type
from taxi_normalize.mapping import Mapping


@dataclass
class ColumnAction:
    action: str  # "passthrough" | "rename" | "cast" | "null_fill"
    source_column: Optional[str] = None
    target_column: Optional[str] = None
    cast_to: Optional[str] = None
    target_type: Optional[str] = None


@dataclass
class Unresolved:
    column: str
    kind: str  # "unmapped_drop" | "unacked_lossy_cast"
    details: str = ""


@dataclass
class Plan:
    actions: list[ColumnAction]
    unresolved: list[Unresolved]


_FLOAT_TYPE_PREFIXES = ("DOUBLE", "FLOAT", "REAL", "DECIMAL")
_INT_TYPE_PREFIXES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT")


def _column_has_data(stats: dict) -> bool:
    """A column has data if any non-null value exists across all row groups."""
    total = stats.get("num_rows", 0)
    nulls = stats.get("null_count", 0)
    return (total - nulls) > 0


def _cast_is_safe(raw_stats: dict, target_type: str) -> bool:
    """Metadata-only safety check for the raw->target type transition.

    Float -> int always requires an explicit ack (precision loss), even if the
    numeric range fits. Metadata cannot rule out fractional values without a
    full scan, and requiring an ack is the safe default.
    """
    src_type = str(raw_stats.get("type", "")).upper()
    tgt = target_type.upper()
    if any(src_type.startswith(p) for p in _FLOAT_TYPE_PREFIXES) and any(tgt.startswith(p) for p in _INT_TYPE_PREFIXES):
        return False
    fits, _reason = fits_in_target_type(raw_stats, target_type)
    return fits


def plan_file(
    raw_metadata: dict[str, dict],
    target_metadata: dict[str, dict],
    mapping: Mapping,
) -> Plan:
    """Produce a Plan for one raw file.

    raw_metadata:    {col: stats} from data_check.get_file_metadata(raw_path)
    target_metadata: {col: stats} from data_check.get_file_metadata(target_path)
    """
    actions: list[ColumnAction] = []
    unresolved: list[Unresolved] = []

    rename_of = mapping.renames                          # raw -> target
    inv_renames = {v: k for k, v in rename_of.items()}   # target -> raw

    raw_cols = set(raw_metadata.keys())
    target_cols = set(target_metadata.keys())

    # Emit actions in target-schema order so the resulting parquet has a
    # canonical column layout.
    for tgt_col in target_metadata.keys():
        tgt_type = target_metadata[tgt_col]["type"]
        # Case A: target column exists in raw as the same name → passthrough or cast
        if tgt_col in raw_cols:
            raw_stats = raw_metadata[tgt_col]
            raw_type = raw_stats["type"]
            if raw_type == tgt_type:
                actions.append(ColumnAction(action="passthrough", source_column=tgt_col, target_column=tgt_col))
            elif _cast_is_safe(raw_stats, tgt_type):
                actions.append(ColumnAction(action="cast", source_column=tgt_col, target_column=tgt_col, cast_to=tgt_type))
            else:
                if tgt_col in mapping.lossy_casts:
                    actions.append(ColumnAction(action="cast", source_column=tgt_col, target_column=tgt_col, cast_to=tgt_type))
                else:
                    unresolved.append(Unresolved(
                        column=tgt_col,
                        kind="unacked_lossy_cast",
                        details=f"{raw_type} -> {tgt_type} would lose data (range/precision)",
                    ))
            continue
        # Case B: target column absent from raw, but mapping renames some raw col INTO it
        if tgt_col in inv_renames:
            src = inv_renames[tgt_col]
            if src in raw_cols:
                raw_stats = raw_metadata[src]
                raw_type = raw_stats["type"]
                if raw_type == tgt_type:
                    actions.append(ColumnAction(action="rename", source_column=src, target_column=tgt_col))
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
        # Case C: target column has no source in raw at all → null_fill
        actions.append(ColumnAction(action="null_fill", target_column=tgt_col, target_type=tgt_type))

    # Now check for raw columns that don't map to any target column: potential drops
    for raw_col in raw_cols:
        if raw_col in target_cols:
            continue  # handled as passthrough above
        if raw_col in rename_of:
            continue  # handled as rename above
        # Not in target, not renamed. Either all-null (safe auto-drop) or unresolved.
        raw_stats = raw_metadata[raw_col]
        if not _column_has_data(raw_stats):
            continue  # safe auto-drop, no action emitted
        if raw_col in mapping.acknowledged_data_loss:
            continue  # acknowledged, no action emitted
        unresolved.append(Unresolved(
            column=raw_col,
            kind="unmapped_drop",
            details="column has data; add to renames: or acknowledged_data_loss:",
        ))

    return Plan(actions=actions, unresolved=unresolved)
