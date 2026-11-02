from taxi_load.reconcile import (
    APPEND, RELOAD, SKIP, ManifestRow, MonthFile, YearPlan, reconcile,
)


def mf(year, month, rows, path=None):
    return MonthFile(year, month, path or f"/x/{year}-{month:02d}.parquet", rows)


def mr(year, month, rows):
    return ManifestRow(year, month, rows)


def only(plans, year):
    return next(p for p in plans if p.year == year)


def test_fresh_year_appends_all_months():
    disk = [mf(2024, 1, 5), mf(2024, 2, 6)]
    plans = reconcile(disk, [], {}, full_refresh=False)
    p = only(plans, 2024)
    assert p.action == APPEND
    assert {m.month for m in p.months} == {1, 2}


def test_all_present_and_matching_skips():
    disk = [mf(2024, 1, 5), mf(2024, 2, 6)]
    man = [mr(2024, 1, 5), mr(2024, 2, 6)]
    counts = {2024: 11}
    plans = reconcile(disk, man, counts, full_refresh=False)
    assert only(plans, 2024).action == SKIP


def test_one_new_month_appends_only_it():
    disk = [mf(2024, 1, 5), mf(2024, 2, 6), mf(2024, 3, 7)]
    man = [mr(2024, 1, 5), mr(2024, 2, 6)]
    counts = {2024: 11}
    p = only(reconcile(disk, man, counts, full_refresh=False), 2024)
    assert p.action == APPEND
    assert [m.month for m in p.months] == [3]


def test_source_count_changed_reloads():
    disk = [mf(2024, 1, 5), mf(2024, 2, 99)]   # month 2 grew on disk
    man = [mr(2024, 1, 5), mr(2024, 2, 6)]
    counts = {2024: 11}
    p = only(reconcile(disk, man, counts, full_refresh=False), 2024)
    assert p.action == RELOAD
    assert {m.month for m in p.months} == {1, 2}


def test_manifest_month_vanished_reloads():
    disk = [mf(2024, 1, 5)]                     # month 2 removed from disk
    man = [mr(2024, 1, 5), mr(2024, 2, 6)]
    counts = {2024: 11}
    assert only(reconcile(disk, man, counts, full_refresh=False), 2024).action == RELOAD


def test_integrity_mismatch_reloads():
    disk = [mf(2024, 1, 5), mf(2024, 2, 6)]
    man = [mr(2024, 1, 5), mr(2024, 2, 6)]      # manifest sum 11
    counts = {2024: 14}                          # table has 3 extra (partial prior load)
    assert only(reconcile(disk, man, counts, full_refresh=False), 2024).action == RELOAD


def test_table_missing_but_manifest_has_rows_reloads():
    disk = [mf(2024, 1, 5)]
    man = [mr(2024, 1, 5)]
    counts = {}                                  # table absent -> 0 != 5
    assert only(reconcile(disk, man, counts, full_refresh=False), 2024).action == RELOAD


def test_full_refresh_reloads_even_when_matching():
    disk = [mf(2024, 1, 5)]
    man = [mr(2024, 1, 5)]
    counts = {2024: 5}
    p = only(reconcile(disk, man, counts, full_refresh=True), 2024)
    assert p.action == RELOAD
    assert {m.month for m in p.months} == {1}


def test_multiple_years_decided_independently():
    disk = [mf(2023, 1, 5), mf(2024, 1, 9)]
    man = [mr(2023, 1, 5)]                        # 2023 complete, 2024 fresh
    counts = {2023: 5}
    plans = reconcile(disk, man, counts, full_refresh=False)
    assert only(plans, 2023).action == SKIP
    assert only(plans, 2024).action == APPEND
    assert [p.year for p in plans] == [2023, 2024]   # sorted


def test_returns_year_plan_type():
    plans = reconcile([mf(2024, 1, 5)], [], {}, full_refresh=False)
    assert isinstance(plans[0], YearPlan)


# --- the column-set guard ---------------------------------------------------

def _append_plan(year=2026, months=1):
    from taxi_load.reconcile import APPEND, MonthFile, YearPlan
    return YearPlan(year=year, action=APPEND,
                    months=[MonthFile(year, m, f"/p/{year}-{m}.parquet", 10)
                            for m in range(1, months + 1)])


def test_guard_refuses_an_append_whose_parquet_gained_a_column():
    """The #37 shape: a re-pinned `target:` widens the parquet, not the table.

    Reconcile sees matching row counts and picks APPEND, so without this the COPY
    fails deep in the bulk-load path on a column mismatch.
    """
    import pytest
    from taxi_load.cli import guard_column_drift
    from taxi_load.connection import LoaderRefusedError

    cols = (["a", "b", "request_source"], ["a", "b"])
    with pytest.raises(LoaderRefusedError) as e:
        guard_column_drift([_append_plan()], {2026: cols})
    msg = str(e.value)
    assert "request_source" in msg
    assert "--full-refresh" in msg          # must name the remedy
    assert "2026" in msg


def test_guard_reports_a_column_the_table_has_and_the_parquet_lost():
    import pytest
    from taxi_load.cli import guard_column_drift
    from taxi_load.connection import LoaderRefusedError

    with pytest.raises(LoaderRefusedError, match="gone_away"):
        guard_column_drift([_append_plan()], {2026: (["a"], ["a", "gone_away"])})


def test_guard_catches_a_pure_reordering():
    import pytest
    from taxi_load.cli import guard_column_drift
    from taxi_load.connection import LoaderRefusedError

    with pytest.raises(LoaderRefusedError, match="different order"):
        guard_column_drift([_append_plan()], {2026: (["b", "a"], ["a", "b"])})


def test_guard_passes_when_columns_agree():
    from taxi_load.cli import guard_column_drift
    guard_column_drift([_append_plan()], {2026: (["a", "b"], ["a", "b"])})


def test_guard_ignores_a_table_that_does_not_exist_yet():
    """An APPEND into a fresh year creates the table, so there is nothing to disagree."""
    from taxi_load.cli import guard_column_drift
    guard_column_drift([_append_plan()], {2026: (["a", "b"], [])})


def test_guard_ignores_reload_and_skip():
    """RELOAD rebuilds the table from the parquet; SKIP loads nothing.

    Checking either would make the guard refuse the very command (--full-refresh)
    that its own error message tells the operator to run.
    """
    from taxi_load.cli import guard_column_drift
    from taxi_load.reconcile import RELOAD, SKIP, YearPlan

    mismatch = {2026: (["a", "b", "c"], ["a"])}
    guard_column_drift([YearPlan(year=2026, action=RELOAD, months=[])], mismatch)
    guard_column_drift([YearPlan(year=2026, action=SKIP, months=[])], mismatch)
