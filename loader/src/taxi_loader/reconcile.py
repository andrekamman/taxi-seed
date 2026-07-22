"""Pure per-(type, year) load decision. No database access.

Inputs are gathered by the CLI (disk parquet footers, the manifest table, and
per-year COUNT(*)); the returned plan is executed by load.py.
"""
from __future__ import annotations

from dataclasses import dataclass

SKIP = "skip"
APPEND = "append"
RELOAD = "reload"


@dataclass(frozen=True)
class MonthFile:
    year: int
    month: int
    path: str
    source_row_count: int


@dataclass(frozen=True)
class ManifestRow:
    year: int
    month: int
    row_count: int


@dataclass
class YearPlan:
    year: int
    action: str                # SKIP | APPEND | RELOAD
    months: list[MonthFile]    # [] for SKIP; months to append for APPEND; all disk months for RELOAD


def reconcile(disk_months: list[MonthFile],
              manifest_rows: list[ManifestRow],
              table_row_counts: dict[int, int],
              full_refresh: bool) -> list[YearPlan]:
    years = sorted({m.year for m in disk_months} | {r.year for r in manifest_rows})
    plans: list[YearPlan] = []
    for year in years:
        disk = sorted((m for m in disk_months if m.year == year), key=lambda m: m.month)
        man_by_month = {r.month: r.row_count for r in manifest_rows if r.year == year}
        disk_by_month = {m.month: m for m in disk}
        manifest_sum = sum(man_by_month.values())
        table_count = table_row_counts.get(year, 0)

        if full_refresh:
            plans.append(YearPlan(year, RELOAD, disk))
            continue
        # Integrity gate: committed table rows must equal recorded manifest rows.
        if table_count != manifest_sum:
            plans.append(YearPlan(year, RELOAD, disk))
            continue
        # A manifest month that no longer exists on disk -> rebuild.
        if any(month not in disk_by_month for month in man_by_month):
            plans.append(YearPlan(year, RELOAD, disk))
            continue
        # Per-month decision.
        to_append: list[MonthFile] = []
        changed = False
        for m in disk:
            if m.month not in man_by_month:
                to_append.append(m)
            elif man_by_month[m.month] != m.source_row_count:
                changed = True
                break
        if changed:
            plans.append(YearPlan(year, RELOAD, disk))
        elif to_append:
            plans.append(YearPlan(year, APPEND, to_append))
        else:
            plans.append(YearPlan(year, SKIP, []))
    return plans
