"""Tests for the planner logic."""
import pytest

from taxi_normalize.mapping import LossyCastEntry, DataLossEntry, Mapping
from taxi_normalize.planner import ColumnAction, Plan, Unresolved, plan_file


def _stats(type_, min_v=1, max_v=10, nulls=0, rows=10):
    return {"type": type_, "min": min_v, "max": max_v, "null_count": nulls, "num_rows": rows}


def _make_metadata(type_map):
    return {col: _stats(t) for col, t in type_map.items()}


def test_pure_passthrough_identical_schemas():
    raw = _make_metadata({"a": "INTEGER", "b": "VARCHAR"})
    target = _make_metadata({"a": "INTEGER", "b": "VARCHAR"})
    mapping = Mapping(target="t.parquet")
    plan = plan_file(raw, target, mapping)
    assert plan.unresolved == []
    assert [a.action for a in plan.actions] == ["passthrough", "passthrough"]


def test_rename_applied():
    raw = _make_metadata({"pu_datetime": "TIMESTAMP", "vendorid": "INTEGER"})
    target = _make_metadata({"tpep_pickup_datetime": "TIMESTAMP", "vendorid": "INTEGER"})
    mapping = Mapping(target="t.parquet", renames={"pu_datetime": "tpep_pickup_datetime"})
    plan = plan_file(raw, target, mapping)
    assert plan.unresolved == []
    # Find the rename action
    rename = next(a for a in plan.actions if a.action == "rename")
    assert rename.source_column == "pu_datetime"
    assert rename.target_column == "tpep_pickup_datetime"


def test_safe_widening_auto_cast():
    raw = _make_metadata({"a": "INTEGER"})
    target = _make_metadata({"a": "BIGINT"})
    mapping = Mapping(target="t.parquet")
    plan = plan_file(raw, target, mapping)
    assert plan.unresolved == []
    cast = next(a for a in plan.actions if a.action == "cast")
    assert cast.cast_to == "BIGINT"


def test_safe_auto_drop_of_all_null_column():
    raw = {"a": _stats("INTEGER"), "b": _stats("INTEGER", min_v=None, max_v=None, nulls=10, rows=10)}
    target = _make_metadata({"a": "INTEGER"})
    mapping = Mapping(target="t.parquet")
    plan = plan_file(raw, target, mapping)
    assert plan.unresolved == []
    # 'b' is all null and not in target → auto-drop; not represented in actions
    col_names = [a.source_column or a.target_column for a in plan.actions]
    assert "b" not in col_names


def test_unmapped_drop_with_data_is_unresolved():
    raw = _make_metadata({"a": "INTEGER", "gone_col": "INTEGER"})  # gone_col has data (nulls=0)
    target = _make_metadata({"a": "INTEGER"})
    mapping = Mapping(target="t.parquet")
    plan = plan_file(raw, target, mapping)
    assert any(u.column == "gone_col" and u.kind == "unmapped_drop" for u in plan.unresolved)


def test_acknowledged_data_loss_removes_from_unresolved():
    raw = _make_metadata({"a": "INTEGER", "gone_col": "INTEGER"})
    target = _make_metadata({"a": "INTEGER"})
    mapping = Mapping(
        target="t.parquet",
        acknowledged_data_loss={"gone_col": DataLossEntry(column="gone_col", ack_date="2026-07-21")},
    )
    plan = plan_file(raw, target, mapping)
    assert plan.unresolved == []


def test_column_added_since_gets_null_fill():
    raw = _make_metadata({"a": "INTEGER"})
    target = _make_metadata({"a": "INTEGER", "new_col": "VARCHAR"})
    mapping = Mapping(target="t.parquet")
    plan = plan_file(raw, target, mapping)
    assert plan.unresolved == []
    nullfill = next(a for a in plan.actions if a.action == "null_fill")
    assert nullfill.target_column == "new_col"
    assert nullfill.target_type == "VARCHAR"


def test_lossy_cast_without_ack_is_unresolved():
    # DOUBLE with fractional values → BIGINT is lossy
    raw = {"passenger_count": _stats("DOUBLE", min_v=0.5, max_v=6.5)}
    target = _make_metadata({"passenger_count": "BIGINT"})
    mapping = Mapping(target="t.parquet")
    plan = plan_file(raw, target, mapping)
    assert any(u.column == "passenger_count" and u.kind == "unacked_lossy_cast" for u in plan.unresolved)


def test_lossy_cast_with_ack_date_only_is_applied():
    raw = {"passenger_count": _stats("DOUBLE", min_v=0.5, max_v=6.5)}
    target = _make_metadata({"passenger_count": "BIGINT"})
    mapping = Mapping(
        target="t.parquet",
        lossy_casts={
            "passenger_count": LossyCastEntry(
                column="passenger_count",
                from_type="DOUBLE",
                to_type="BIGINT",
                ack_date="2026-07-21",
            )
        },
    )
    plan = plan_file(raw, target, mapping)
    assert plan.unresolved == []
    cast = next(a for a in plan.actions if a.action == "cast")
    assert cast.cast_to == "BIGINT"
