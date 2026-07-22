"""Tests for SQL building and parquet writing."""
import duckdb

from taxi_normalize.executor import build_transform_sql, execute_transform
from taxi_normalize.planner import ColumnAction, Plan


def test_build_transform_sql_covers_all_action_types(tmp_path):
    plan = Plan(
        actions=[
            ColumnAction(action="passthrough", source_column="vendorid", target_column="vendorid"),
            ColumnAction(action="rename", source_column="pu_datetime", target_column="tpep_pickup_datetime"),
            ColumnAction(action="cast", source_column="passenger_count", target_column="passenger_count", cast_to="BIGINT"),
            ColumnAction(action="null_fill", target_column="airport_fee", target_type="DOUBLE"),
        ],
        unresolved=[],
    )
    sql = build_transform_sql(plan, tmp_path / "in.parquet", tmp_path / "out.parquet")
    assert '"vendorid"' in sql
    assert '"pu_datetime" AS "tpep_pickup_datetime"' in sql
    assert 'CAST("passenger_count" AS BIGINT)' in sql
    assert 'NULL::DOUBLE AS "airport_fee"' in sql
    assert "COPY (" in sql
    assert "FORMAT PARQUET" in sql


def test_build_transform_sql_uses_tmp_path_not_final(tmp_path):
    plan = Plan(actions=[ColumnAction(action="passthrough", source_column="a", target_column="a")], unresolved=[])
    sql = build_transform_sql(plan, tmp_path / "in.parquet", tmp_path / "out.parquet")
    # SQL writes to .tmp.parquet; the caller does the rename.
    assert ".tmp.parquet" in sql


def test_execute_transform_produces_valid_parquet(yellow_family, tmp_path):
    # Take the era-1 file and passthrough it to a new location
    conn = duckdb.connect(":memory:")
    era1 = yellow_family / "2009" / "yellow_tripdata_2009-01.parquet"
    # Build a passthrough plan by reading the raw schema
    desc = conn.execute(f"DESCRIBE SELECT * FROM '{era1}'").fetchall()
    plan = Plan(
        actions=[ColumnAction(action="passthrough", source_column=r[0], target_column=r[0]) for r in desc],
        unresolved=[],
    )
    out = tmp_path / "passthrough.parquet"
    execute_transform(conn, plan, era1, out)

    assert out.exists()
    assert not out.with_suffix(".tmp.parquet").exists()  # tmp cleaned up
    # Verify identical row count
    orig_count = conn.execute(f"SELECT count(*) FROM '{era1}'").fetchone()[0]
    new_count = conn.execute(f"SELECT count(*) FROM '{out}'").fetchone()[0]
    assert orig_count == new_count


def test_execute_transform_applies_rename_and_cast(yellow_family, tmp_path):
    conn = duckdb.connect(":memory:")
    era1 = yellow_family / "2009" / "yellow_tripdata_2009-01.parquet"
    out = tmp_path / "normalized.parquet"

    # Rename pu_datetime → tpep_pickup_datetime; cast passenger_count DOUBLE→BIGINT (era1 is safe: 1.0, 2.0)
    plan = Plan(
        actions=[
            ColumnAction(action="passthrough", source_column="vendorid", target_column="vendorid"),
            ColumnAction(action="rename", source_column="pu_datetime", target_column="tpep_pickup_datetime"),
            ColumnAction(action="cast", source_column="passenger_count", target_column="passenger_count", cast_to="BIGINT"),
        ],
        unresolved=[],
    )
    execute_transform(conn, plan, era1, out)

    cols = [r[0] for r in conn.execute(f"DESCRIBE SELECT * FROM '{out}'").fetchall()]
    assert "tpep_pickup_datetime" in cols
    assert "pu_datetime" not in cols
    pc_type = next(r[1] for r in conn.execute(f"DESCRIBE SELECT * FROM '{out}'").fetchall() if r[0] == "passenger_count")
    assert pc_type == "BIGINT"
