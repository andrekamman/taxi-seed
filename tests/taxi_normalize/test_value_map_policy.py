from pathlib import Path

import duckdb
import pytest

from taxi_normalize.data_check import get_file_metadata
from taxi_normalize.executor import _sql_lit, execute_transform
from taxi_normalize.mapping import Mapping, load_mapping
from taxi_normalize.planner import plan_file


def test_sql_lit_none_is_null():
    assert _sql_lit(None) == "NULL"


def test_parse_strict_and_policy_forms(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(
        "target: t.parquet\n"
        "value_maps:\n"
        "  VendorID:\n"
        "    CMT: 1\n"
        "    DDS: null\n"
        "  RatecodeID:\n"
        "    map:\n"
        "      '1': 1\n"
        "      '6': 6\n"
        "    on_unmapped: 'null'\n"
    )
    m = load_mapping(p)
    assert m.value_maps["VendorID"] == {"CMT": 1, "DDS": None}
    assert m.value_map_unmapped.get("VendorID", "error") == "error"
    assert m.value_maps["RatecodeID"] == {"1": 1, "6": 6}
    assert m.value_map_unmapped["RatecodeID"] == "null"


def test_bad_policy_rejected(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("target: t.parquet\nvalue_maps:\n  c:\n    map: {'1': 1}\n    on_unmapped: bogus\n")
    from taxi_normalize.mapping import MappingError
    with pytest.raises(MappingError):
        load_mapping(p)


def test_on_unmapped_error_raises(tmp_path):
    conn = duckdb.connect(":memory:")
    src = tmp_path / "raw/yellow/2010/f.parquet"
    src.parent.mkdir(parents=True, exist_ok=True)
    conn.execute(f"COPY (SELECT * FROM (VALUES (1,'X')) AS t(vendorid, RatecodeID)) TO '{src}' (FORMAT PARQUET)")
    tgt = tmp_path / "raw/yellow/2024/f.parquet"
    tgt.parent.mkdir(parents=True, exist_ok=True)
    conn.execute(f"COPY (SELECT 1 AS vendorid, CAST(1 AS BIGINT) AS RatecodeID) TO '{tgt}' (FORMAT PARQUET)")
    mapping = Mapping(target="f.parquet", value_maps={"RatecodeID": {"1": 1}})  # strict; 'X' unmapped
    out = tmp_path / "o.parquet"
    with pytest.raises(duckdb.Error, match="unmapped value"):
        execute_transform(conn, plan_file(get_file_metadata(conn, src),
                          get_file_metadata(conn, tgt), mapping), src, out)


def test_value_map_null_target(tmp_path):
    # NOTE: the id column is named "row_id" (not "vendorid") to avoid colliding with
    # "VendorID" case-insensitively — DuckDB folds duplicate column names that differ
    # only by case and silently renames the second to "VendorID_1" at COPY time, which
    # would defeat the value_map lookup on "VendorID" for reasons unrelated to this test.
    conn = duckdb.connect(":memory:")
    src = tmp_path / "raw/yellow/2009/f.parquet"
    src.parent.mkdir(parents=True, exist_ok=True)
    conn.execute(f"COPY (SELECT * FROM (VALUES (1,'CMT'),(2,'DDS')) AS t(row_id, VendorID)) TO '{src}' (FORMAT PARQUET)")
    tgt = tmp_path / "raw/yellow/2024/f.parquet"
    tgt.parent.mkdir(parents=True, exist_ok=True)
    conn.execute(f"COPY (SELECT 1 AS row_id, CAST(1 AS BIGINT) AS VendorID) TO '{tgt}' (FORMAT PARQUET)")
    mapping = Mapping(target="f.parquet", value_maps={"VendorID": {"CMT": 1, "DDS": None}})
    out = tmp_path / "o.parquet"
    execute_transform(conn, plan_file(get_file_metadata(conn, src),
                      get_file_metadata(conn, tgt), mapping), src, out)
    assert conn.execute(f"SELECT row_id, VendorID FROM '{out}' ORDER BY row_id").fetchall() == [(1, 1), (2, None)]
