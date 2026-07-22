"""YAML mapping loading and validation."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


class MappingError(Exception):
    """Raised for any mapping YAML validation failure."""


@dataclass
class LossyCastEntry:
    column: str
    from_type: str
    to_type: str
    ack_date: str            # required — anything truthy counts as acknowledgment
    ack_by: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class DataLossEntry:
    column: str
    ack_date: str            # required
    ack_by: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class Mapping:
    target: str
    renames: dict[str, str] = field(default_factory=dict)
    lossy_casts: dict[str, LossyCastEntry] = field(default_factory=dict)
    acknowledged_data_loss: dict[str, DataLossEntry] = field(default_factory=dict)


_ALLOWED_KEYS = {"target", "renames", "lossy_casts", "acknowledged_data_loss"}


def load_mapping(path: Path) -> Mapping:
    """Load and validate a mapping YAML file. Raises MappingError on any problem."""
    if not path.exists():
        raise MappingError(f"Mapping file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise MappingError(f"Invalid YAML in {path}: {e}") from e

    if raw is None:
        raise MappingError(f"Empty mapping file: {path}")
    if not isinstance(raw, dict):
        raise MappingError(f"Mapping root must be a dict, got {type(raw).__name__}")

    unknown = set(raw.keys()) - _ALLOWED_KEYS
    if unknown:
        raise MappingError(f"Unknown top-level key(s): {sorted(unknown)}. "
                           f"Allowed: {sorted(_ALLOWED_KEYS)}")

    target = raw.get("target")
    if not target or not isinstance(target, str):
        raise MappingError("Mapping must have a 'target:' key with a filename value.")

    renames = raw.get("renames") or {}
    if not isinstance(renames, dict):
        raise MappingError("'renames:' must be a dict of {old_name: new_name}.")
    for k, v in renames.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise MappingError(f"Rename entry must be string->string, got {k!r}: {v!r}")

    lossy_casts: dict[str, LossyCastEntry] = {}
    for col, entry in (raw.get("lossy_casts") or {}).items():
        if not isinstance(entry, dict):
            raise MappingError(f"lossy_casts.{col} must be a dict")
        if not entry.get("ack_date"):
            raise MappingError(f"lossy_casts.{col} requires 'ack_date'")
        from_type = entry.get("from")
        to_type = entry.get("to")
        if not from_type or not to_type:
            raise MappingError(f"lossy_casts.{col} requires 'from' and 'to' types")
        lossy_casts[col] = LossyCastEntry(
            column=col,
            from_type=str(from_type),
            to_type=str(to_type),
            ack_date=str(entry["ack_date"]),
            ack_by=entry.get("ack_by"),
            reason=entry.get("reason"),
        )

    data_loss: dict[str, DataLossEntry] = {}
    for col, entry in (raw.get("acknowledged_data_loss") or {}).items():
        if not isinstance(entry, dict):
            raise MappingError(f"acknowledged_data_loss.{col} must be a dict")
        if not entry.get("ack_date"):
            raise MappingError(f"acknowledged_data_loss.{col} requires 'ack_date'")
        data_loss[col] = DataLossEntry(
            column=col,
            ack_date=str(entry["ack_date"]),
            ack_by=entry.get("ack_by"),
            reason=entry.get("reason"),
        )

    return Mapping(
        target=target,
        renames=renames,
        lossy_casts=lossy_casts,
        acknowledged_data_loss=data_loss,
    )
