"""Tests for metadata queries and precision scans."""
import duckdb
import pytest

from taxi_normalize.data_check import (
    get_file_metadata,
    aggregate_across_files,
    fits_in_target_type,
    has_precision_loss,
)


def test_get_file_metadata_returns_per_column_stats(target_file):
    conn = duckdb.connect(":memory:")
    md = get_file_metadata(conn, target_file)
    # Every column present, with type + null_count + row_count
    assert "vendorid" in md
    assert md["vendorid"]["type"] == "INTEGER"
    assert md["vendorid"]["null_count"] == 0
    assert md["vendorid"]["num_rows"] == 2
    assert "airport_fee" in md
    # min/max present for numeric columns
    assert md["vendorid"]["min"] == 1
    assert md["vendorid"]["max"] == 2


def test_aggregate_across_files_unions_columns(yellow_family):
    conn = duckdb.connect(":memory:")
    files = sorted(yellow_family.rglob("*.parquet"))
    mds = [get_file_metadata(conn, f) for f in files]
    agg = aggregate_across_files(mds)
    # Old and new column names both appear in the union
    assert "pu_datetime" in agg
    assert "tpep_pickup_datetime" in agg
    # airport_fee only in era 3, so files_present == 1
    assert agg["airport_fee"]["files_present"] == 1
    assert agg["airport_fee"]["files_with_data"] == 1
    # pu_datetime only in era 1
    assert agg["pu_datetime"]["files_present"] == 1
    assert agg["pu_datetime"]["files_with_data"] == 1
    # vendorid in all three eras
    assert agg["vendorid"]["files_present"] == 3


def test_fits_in_target_type_widening_is_safe():
    # INTEGER data with max=100 fits easily in BIGINT
    stats = {"type": "INTEGER", "min": 1, "max": 100, "num_rows": 100, "null_count": 0}
    fits, reason = fits_in_target_type(stats, "BIGINT")
    assert fits is True
    assert reason == ""


def test_fits_in_target_type_range_overflow_flagged():
    # INT max 999999 does not fit SMALLINT (max 32767)
    stats = {"type": "INTEGER", "min": 0, "max": 999999, "num_rows": 100, "null_count": 0}
    fits, reason = fits_in_target_type(stats, "SMALLINT")
    assert fits is False
    assert "999999" in reason or "SMALLINT" in reason


def test_fits_in_target_type_varchar_length_flagged():
    # String max length 50 does not fit VARCHAR(10)
    stats = {"type": "VARCHAR", "max": "a" * 50, "min": "a", "num_rows": 10, "null_count": 0}
    fits, reason = fits_in_target_type(stats, "VARCHAR(10)")
    assert fits is False


def test_has_precision_loss_double_to_bigint_with_fractional(yellow_family):
    conn = duckdb.connect(":memory:")
    era2_file = yellow_family / "2015" / "yellow_tripdata_2015-06.parquet"
    # passenger_count in era 2 has 1.5 — that would truncate to 1
    loss, count = has_precision_loss(conn, era2_file, "passenger_count", "BIGINT")
    assert loss is True
    assert count == 1  # one row with 1.5


def test_has_precision_loss_no_fractional_is_safe(yellow_family):
    conn = duckdb.connect(":memory:")
    era1_file = yellow_family / "2009" / "yellow_tripdata_2009-01.parquet"
    # Era 1 passenger_count is 1.0 and 2.0 — integer-valued
    loss, count = has_precision_loss(conn, era1_file, "passenger_count", "BIGINT")
    assert loss is False
    assert count == 0
