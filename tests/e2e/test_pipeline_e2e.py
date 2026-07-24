import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import attached
from fakedata import DATA_TYPES, generate
from taxi_loader import load, manifest

pytestmark = pytest.mark.skipif(
    not os.environ.get("MSSQL_PASSWORD"),
    reason="requires SQL Server (set MSSQL_PASSWORD)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_MAPPINGS = REPO_ROOT / "normalize" / "mappings"


def _run_pipeline(workroot: Path, data_type: str, cfg) -> None:
    argv = [
        "taxi-run", data_type, "--skip-download", "--load",
        "--data-dir", str(workroot),
        "--host", cfg.host, "--port", str(cfg.port),
        "--database", cfg.database, "--schema", cfg.schema, "--user", cfg.user,
    ]
    env = {**os.environ, "MSSQL_PASSWORD": cfg.password}
    result = subprocess.run(argv, env=env, capture_output=True, text=True)
    assert result.returncode == 0, (
        f"taxi-run exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def _count(cfg, table: str) -> int:
    with attached(cfg) as conn:
        return load.count_year_table(conn, cfg, table)


@pytest.mark.parametrize("data_type", DATA_TYPES)
def test_pipeline_loads_expected_row_counts(tmp_path, cfg, data_type):
    shutil.copytree(REPO_MAPPINGS, tmp_path / "normalize" / "mappings")
    info = generate(tmp_path, data_type, rows_target=2, rows_drift=3)

    _run_pipeline(tmp_path, data_type, cfg)

    assert _count(cfg, f"{data_type}_{info['target_year']}") == info["target_rows"]
    assert _count(cfg, f"{data_type}_{info['drift_year']}") == info["drift_rows"]

    with attached(cfg) as conn:
        rows = manifest.read_manifest(conn, cfg, data_type)
    # manifest.read_manifest returns list[ManifestRow] (a frozen dataclass with
    # .year/.month/.row_count fields, not a plain tuple) -- use attribute access
    # rather than tuple-unpacking to build the comparison set.
    triples = {(r.year, r.month, r.row_count) for r in rows}
    assert (info["target_year"], info["target_month"], info["target_rows"]) in triples
    assert (info["drift_year"], info["drift_month"], info["drift_rows"]) in triples
