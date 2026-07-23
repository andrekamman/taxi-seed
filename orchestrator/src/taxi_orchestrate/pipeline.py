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
        # Loader exit 2 is conn/config OR a per-type TypeMappingError; both abort
        # the remaining types' load. Conflating them is the deliberate conservative
        # choice (over-aborting is safe: exit 2 still surfaces the failure).
        return StageOutcome(stage, exit_code, CONN_ERROR, True, True)
    raise ValueError(f"unknown stage: {stage!r}")


def overall_exit_code(outcomes: list[StageOutcome]) -> int:
    if any(o.status in _FAILURE_STATUSES for o in outcomes):
        return 2
    if any(o.status == NEEDS_REVIEW for o in outcomes):
        return 1
    return 0
