import shutil
from pathlib import Path

import duckdb
import pytest
from fakedata import DATA_TYPES, TARGET_COLUMNS, generate
from taxi_normalize.cli import main as normalize_main

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_MAPPINGS = REPO_ROOT / "normalize" / "mappings"


def _describe(path):
    con = duckdb.connect()
    rows = con.execute(
        "SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM read_parquet(?))",
        [str(path)],
    ).fetchall()
    con.close()
    return rows


@pytest.mark.parametrize("data_type", DATA_TYPES)
def test_generated_data_normalizes_cleanly(tmp_path, monkeypatch, data_type):
    shutil.copytree(REPO_MAPPINGS, tmp_path / "normalize" / "mappings")
    generate(tmp_path, data_type, rows_target=2, rows_drift=3)

    monkeypatch.chdir(tmp_path)
    rc = normalize_main([data_type])
    assert rc == 0, f"normalize exited {rc} for {data_type} (expected 0 clean)"

    out_dir = tmp_path / "raw-normalized" / data_type
    produced = sorted(out_dir.rglob("*.parquet"))
    assert len(produced) == 2, f"expected 2 normalized files, got {produced}"

    # Every normalized file conforms to the canonical target schema.
    for f in produced:
        assert _describe(f) == TARGET_COLUMNS[data_type]


def test_yellow_value_map_and_cast_applied(tmp_path, monkeypatch):
    """payment_type CRD/CASH/Dispute -> 1/2/4 (BIGINT); passenger_count DOUBLE -> BIGINT."""
    shutil.copytree(REPO_MAPPINGS, tmp_path / "normalize" / "mappings")
    info = generate(tmp_path, "yellow", rows_target=2, rows_drift=6)
    monkeypatch.chdir(tmp_path)
    assert normalize_main(["yellow"]) == 0

    normalized_drift = (
        tmp_path / "raw-normalized" / "yellow" / "2015" / info["drift_path"].name
    )
    con = duckdb.connect()
    pay = {r[0] for r in con.execute(
        "SELECT DISTINCT payment_type FROM read_parquet(?)", [str(normalized_drift)]
    ).fetchall()}
    coltypes = dict(con.execute(
        "SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM read_parquet(?))",
        [str(normalized_drift)],
    ).fetchall())
    con.close()
    assert pay <= {1, 2, 4}
    assert coltypes["passenger_count"] == "BIGINT"
    assert coltypes["payment_type"] == "BIGINT"
    assert coltypes["VendorID"] == "INTEGER"  # renamed from vendor_id + value-mapped
