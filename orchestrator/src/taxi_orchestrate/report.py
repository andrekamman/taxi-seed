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
