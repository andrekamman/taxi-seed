"""value_maps: remap discrete historical string values to the target's coded form
(e.g. yellow payment_type 'CASH' -> 2), losslessly, with numeric-era files passing
through unchanged."""
from pathlib import Path

import duckdb

from taxi_normalize.data_check import get_file_metadata
from taxi_normalize.executor import execute_transform
from taxi_normalize.mapping import Mapping
from taxi_normalize.planner import plan_file


def _write(conn, path, select):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn.execute(f"COPY ({select}) TO '{path}' (FORMAT PARQUET)")


def test_mapping_parses_value_maps(tmp_path):
    from taxi_normalize.mapping import load_mapping
    p = tmp_path / "m.yaml"
    p.write_text(
        "target: t.parquet\n"
        "value_maps:\n"
        "  payment_type:\n"
        "    'CASH': 2\n"
        "    'CREDIT': 1\n"
    )
    m = load_mapping(p)
    assert m.value_maps["payment_type"] == {"CASH": 2, "CREDIT": 1}


def test_planner_emits_value_map_for_string_source():
    raw = {"payment_type": {"type": "VARCHAR", "min": "CASH", "max": "No Charge",
                            "null_count": 0, "num_rows": 4}}
    target = {"payment_type": {"type": "BIGINT", "min": 1, "max": 6,
                               "null_count": 0, "num_rows": 4}}
    mapping = Mapping(target="t.parquet", value_maps={"payment_type": {"CASH": 2, "CREDIT": 1}})
    plan = plan_file(raw, target, mapping)
    assert plan.unresolved == []
    act = next(a for a in plan.actions if a.target_column == "payment_type")
    assert act.action == "value_map"


def test_planner_passthrough_when_already_target_type():
    raw = {"payment_type": {"type": "BIGINT", "min": 1, "max": 6, "null_count": 0, "num_rows": 4}}
    target = {"payment_type": {"type": "BIGINT", "min": 1, "max": 6, "null_count": 0, "num_rows": 4}}
    mapping = Mapping(target="t.parquet", value_maps={"payment_type": {"CASH": 2}})
    plan = plan_file(raw, target, mapping)
    act = next(a for a in plan.actions if a.target_column == "payment_type")
    assert act.action == "passthrough"


def test_end_to_end_value_map_and_passthrough(tmp_path):
    conn = duckdb.connect(":memory:")
    # string era
    _write(conn, tmp_path / "raw/yellow/2009/yellow_tripdata_2009-01.parquet",
           "SELECT * FROM (VALUES (1,'CASH'),(2,'Credit'),(3,'No Charge'),(4,'DIS')) AS t(vendorid, payment_type)")
    # numeric era (target shape)
    tgt = tmp_path / "raw/yellow/2024/yellow_tripdata_2024-01.parquet"
    _write(conn, tgt,
           "SELECT * FROM (VALUES (1, CAST(2 AS BIGINT)),(2, CAST(1 AS BIGINT))) AS t(vendorid, payment_type)")

    mapping = Mapping(target="yellow_tripdata_2024-01.parquet",
                      value_maps={"payment_type": {"CASH": 2, "Cash": 2, "Credit": 1,
                                                   "No Charge": 3, "DIS": 4}})
    target_md = get_file_metadata(conn, tgt)

    src = tmp_path / "raw/yellow/2009/yellow_tripdata_2009-01.parquet"
    out = tmp_path / "out/yellow/2009/yellow_tripdata_2009-01.parquet"
    plan = plan_file(get_file_metadata(conn, src), target_md, mapping)
    execute_transform(conn, plan, src, out)

    rows = conn.execute(f"SELECT vendorid, payment_type FROM '{out}' ORDER BY vendorid").fetchall()
    assert rows == [(1, 2), (2, 1), (3, 3), (4, 4)]   # CASH->2, Credit->1, No Charge->3, DIS->4
    # column is the target integer type
    typ = conn.execute(f"SELECT typeof(payment_type) FROM '{out}' LIMIT 1").fetchone()[0]
    assert typ.upper() == "BIGINT"


def test_null_source_passes_through_value_map(tmp_path):
    conn = duckdb.connect(":memory:")
    _write(conn, tmp_path / "raw/yellow/2009/yellow_tripdata_2009-01.parquet",
           "SELECT * FROM (VALUES (1,'CASH'),(2, CAST(NULL AS VARCHAR))) AS t(vendorid, payment_type)")
    tgt = tmp_path / "raw/yellow/2024/yellow_tripdata_2024-01.parquet"
    _write(conn, tgt, "SELECT 1 AS vendorid, CAST(2 AS BIGINT) AS payment_type")
    mapping = Mapping(target="yellow_tripdata_2024-01.parquet",
                      value_maps={"payment_type": {"CASH": 2}})
    target_md = get_file_metadata(conn, tgt)
    src = tmp_path / "raw/yellow/2009/yellow_tripdata_2009-01.parquet"
    out = tmp_path / "out/p.parquet"
    execute_transform(conn, plan_file(get_file_metadata(conn, src), target_md, mapping), src, out)
    assert conn.execute(f"SELECT vendorid, payment_type FROM '{out}' ORDER BY vendorid").fetchall() == [(1, 2), (2, None)]


def test_unmapped_value_raises_not_silent_null(tmp_path):
    import pytest
    conn = duckdb.connect(":memory:")
    _write(conn, tmp_path / "raw/yellow/2009/yellow_tripdata_2009-01.parquet",
           "SELECT * FROM (VALUES (1,'CASH'),(2,'MYSTERY')) AS t(vendorid, payment_type)")
    tgt = tmp_path / "raw/yellow/2024/yellow_tripdata_2024-01.parquet"
    _write(conn, tgt, "SELECT 1 AS vendorid, CAST(2 AS BIGINT) AS payment_type")
    mapping = Mapping(target="yellow_tripdata_2024-01.parquet",
                      value_maps={"payment_type": {"CASH": 2}})  # 'MYSTERY' unmapped
    target_md = get_file_metadata(conn, tgt)
    src = tmp_path / "raw/yellow/2009/yellow_tripdata_2009-01.parquet"
    out = tmp_path / "out/p.parquet"
    with pytest.raises(duckdb.Error, match="unmapped value"):
        execute_transform(conn, plan_file(get_file_metadata(conn, src), target_md, mapping), src, out)
