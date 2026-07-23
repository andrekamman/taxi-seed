"""Regression: columns whose type drifts between string and numeric across files
(real TLC history has these) must not crash range aggregation or the fit check."""
from taxi_normalize.data_check import aggregate_across_files, fits_in_target_type


def _stats(type_, min_v, max_v, nulls=0, rows=10):
    return {"type": type_, "min": min_v, "max": max_v, "null_count": nulls, "num_rows": rows}


def test_aggregate_across_mixed_string_and_int_min_does_not_crash():
    # Same column: string values in one file, integer values in another.
    file_a = {"c": _stats("VARCHAR", "N", "Y")}
    file_b = {"c": _stats("INTEGER", 1, 9)}
    agg = aggregate_across_files([file_a, file_b])  # must not raise TypeError
    assert agg["c"]["files_present"] == 2
    assert set(agg["c"]["types_seen"]) == {"VARCHAR", "INTEGER"}
    # first-seen bound is kept; the incomparable value is ignored, not fatal
    assert agg["c"]["min_range"] == "N"


def test_aggregate_int_then_string_keeps_first_and_survives():
    agg = aggregate_across_files([{"c": _stats("INTEGER", 1, 9)}, {"c": _stats("VARCHAR", "a", "z")}])
    assert agg["c"]["min_range"] == 1  # int first-seen kept; str ignored


def test_fits_in_target_type_string_min_vs_int_target_defers():
    # A string min/max against an INTEGER target can't be range-judged; assume fit.
    fits, _ = fits_in_target_type({"min": "N", "max": "Y"}, "INTEGER")
    assert fits is True


def test_fits_in_target_type_still_flags_real_overflow():
    fits, reason = fits_in_target_type({"min": 0, "max": 10**12}, "INTEGER")
    assert fits is False and "exceeds" in reason
