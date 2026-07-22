"""Smoke tests for the CLI. Uses subprocess for genuine end-to-end coverage."""
import subprocess
import sys
from pathlib import Path


def _run(*args, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "taxi_normalize.cli", *args],
        capture_output=True, text=True, cwd=cwd,
    )


def test_help_exits_zero():
    r = _run("--help")
    assert r.returncode == 0
    assert "normalize" in r.stdout.lower()


def test_bootstrap_help_exits_zero():
    r = _run("bootstrap", "--help")
    assert r.returncode == 0
    assert "--sample" in r.stdout


def test_normalize_missing_mapping_errors(tmp_path):
    # `normalize yellow` in a dir with no mappings/ -> error
    (tmp_path / "raw" / "yellow" / "2024").mkdir(parents=True)
    r = _run("yellow", cwd=tmp_path)
    assert r.returncode != 0
    assert "mapping" in (r.stdout + r.stderr).lower()


def test_end_to_end_bootstrap_then_normalize(yellow_family, tmp_path):
    # yellow_family fixture is at tmp_path/raw/yellow with three era files
    workdir = tmp_path
    (workdir / "normalize" / "mappings").mkdir(parents=True)

    # 1. bootstrap emits the scaffold
    r_boot = _run("bootstrap", "yellow", cwd=workdir)
    assert r_boot.returncode == 0, r_boot.stderr
    mapping_file = workdir / "normalize" / "mappings" / "yellow.yaml"
    assert mapping_file.exists()

    # 2. Hand-edit: write a complete valid mapping.
    mapping_file.write_text("""
target: yellow_tripdata_2024-01.parquet
renames:
  pu_datetime: tpep_pickup_datetime
  do_datetime: tpep_dropoff_datetime
lossy_casts:
  passenger_count:
    from: DOUBLE
    to: BIGINT
    ack_date: 2026-07-21
acknowledged_data_loss:
  pickup_latitude:
    ack_date: 2026-07-21
  pickup_longitude:
    ack_date: 2026-07-21
""")

    # 3. Run normalize
    r_norm = _run("yellow", cwd=workdir)
    assert r_norm.returncode == 0, r_norm.stderr

    # 4. Verify outputs exist and have target schema
    out_dir = workdir / "raw-normalized" / "yellow"
    out_files = sorted(out_dir.rglob("*.parquet"))
    assert len(out_files) == 3
    # Check the era-1 output has the renamed columns
    import duckdb
    conn = duckdb.connect(":memory:")
    era1_out = next(f for f in out_files if "2009-01" in f.name)
    cols = {r[0] for r in conn.execute(f"DESCRIBE SELECT * FROM '{era1_out}'").fetchall()}
    assert "tpep_pickup_datetime" in cols
    assert "pu_datetime" not in cols
    assert "pickup_latitude" not in cols


def test_normalize_with_unresolved_mapping_errors_consolidated(yellow_family, tmp_path):
    workdir = tmp_path
    (workdir / "normalize" / "mappings").mkdir(parents=True)
    # Empty mapping - will produce unresolved items
    (workdir / "normalize" / "mappings" / "yellow.yaml").write_text(
        "target: yellow_tripdata_2024-01.parquet\n"
    )
    r = _run("yellow", cwd=workdir)
    assert r.returncode == 1
    out = r.stdout + r.stderr
    assert "unresolved" in out.lower()
    assert "pu_datetime" in out
