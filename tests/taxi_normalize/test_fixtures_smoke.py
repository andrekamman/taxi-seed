"""Verify the fixture infrastructure produces valid parquet files with the expected schemas."""
import duckdb


def test_yellow_family_creates_three_files(yellow_family):
    files = sorted(yellow_family.rglob("*.parquet"))
    assert len(files) == 3
    assert files[0].name == "yellow_tripdata_2009-01.parquet"
    assert files[1].name == "yellow_tripdata_2015-06.parquet"
    assert files[2].name == "yellow_tripdata_2024-01.parquet"


def test_yellow_family_eras_have_expected_columns(yellow_family, target_file):
    conn = duckdb.connect(":memory:")
    era1 = yellow_family / "2009" / "yellow_tripdata_2009-01.parquet"
    era1_cols = {r[0] for r in conn.execute(f"DESCRIBE SELECT * FROM '{era1}'").fetchall()}
    assert "pu_datetime" in era1_cols
    assert "pickup_latitude" in era1_cols
    assert "airport_fee" not in era1_cols

    target_cols = {r[0] for r in conn.execute(f"DESCRIBE SELECT * FROM '{target_file}'").fetchall()}
    assert "tpep_pickup_datetime" in target_cols
    assert "airport_fee" in target_cols
    assert "pu_datetime" not in target_cols


def test_no_drift_family_has_uniform_schema(no_drift_family):
    files = sorted(no_drift_family.rglob("*.parquet"))
    assert len(files) == 2
    conn = duckdb.connect(":memory:")
    s0 = conn.execute(f"DESCRIBE SELECT * FROM '{files[0]}'").fetchall()
    s1 = conn.execute(f"DESCRIBE SELECT * FROM '{files[1]}'").fetchall()
    assert s0 == s1
