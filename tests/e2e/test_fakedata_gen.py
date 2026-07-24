import duckdb
import pytest
from fakedata import DATA_TYPES, TARGET_COLUMNS, TARGET_FILE, generate


def _describe(path):
    con = duckdb.connect()
    rows = con.execute(
        "SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM read_parquet(?))",
        [str(path)],
    ).fetchall()
    con.close()
    return rows


@pytest.mark.parametrize("data_type", DATA_TYPES)
def test_target_file_matches_canonical_schema(tmp_path, data_type):
    info = generate(tmp_path, data_type, rows_target=2, rows_drift=3)
    assert info["target_path"].name == TARGET_FILE[data_type]
    assert info["target_path"].exists()
    described = _describe(info["target_path"])
    assert described == TARGET_COLUMNS[data_type]  # names, types, and order


@pytest.mark.parametrize("data_type", DATA_TYPES)
def test_drift_file_written_with_expected_row_count(tmp_path, data_type):
    info = generate(tmp_path, data_type, rows_target=2, rows_drift=3)
    assert info["drift_path"].exists()
    assert "2015-01" in info["drift_path"].name
    con = duckdb.connect()
    n = con.execute(
        "SELECT count(*) FROM read_parquet(?)", [str(info["drift_path"])]
    ).fetchone()[0]
    con.close()
    assert n == 3


def test_yellow_payment_type_values_are_valid_map_keys(tmp_path):
    info = generate(tmp_path, "yellow", rows_drift=6)
    con = duckdb.connect()
    vals = {r[0] for r in con.execute(
        "SELECT DISTINCT payment_type FROM read_parquet(?)", [str(info["drift_path"])]
    ).fetchall()}
    con.close()
    assert vals <= {"CRD", "CASH", "Dispute"}
