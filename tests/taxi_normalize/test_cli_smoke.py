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
    assert "--sample" in r.stdout


def test_no_bootstrap_subcommand_exposed():
    """`bootstrap` is no longer a public subcommand; the CLI treats it as a data_type
    argument (which will fail because bootstrap isn't a valid type / has no raw dir)."""
    r = _run("bootstrap", "--help", cwd="/tmp")
    # `--help` after a positional is consumed by argparse as unknown args → error.
    # Either way the behavior differs from an actual subcommand.
    assert r.returncode != 0 or "--sample" in r.stdout


def test_normalize_first_run_bootstraps_and_exits_3(yellow_family, tmp_path):
    """First run for a data_type: no mapping exists, so normalize auto-bootstraps
    the scaffold, prints next-step instructions, and exits with code 3."""
    workdir = tmp_path
    # yellow_family already lives at tmp_path/raw/yellow
    r = _run("yellow", cwd=workdir)
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
    mapping_path = workdir / "normalize" / "mappings" / "yellow.yaml"
    assert mapping_path.exists()
    combined = r.stdout + r.stderr
    assert "no mapping found" in combined.lower()
    assert "next steps" in combined.lower()


def test_normalize_second_run_with_completed_mapping_succeeds(yellow_family, tmp_path):
    """After first run + human edit, second run normalizes and exits 0."""
    workdir = tmp_path

    # First run generates the scaffold, exits 3.
    r1 = _run("yellow", cwd=workdir)
    assert r1.returncode == 3
    mapping_path = workdir / "normalize" / "mappings" / "yellow.yaml"
    assert mapping_path.exists()

    # Human edit: replace scaffold with a completed mapping.
    mapping_path.write_text(
        "target: yellow_tripdata_2024-01.parquet\n"
        "renames:\n"
        "  pu_datetime: tpep_pickup_datetime\n"
        "  do_datetime: tpep_dropoff_datetime\n"
        "lossy_casts:\n"
        "  passenger_count:\n"
        "    from: DOUBLE\n"
        "    to: BIGINT\n"
        "    ack_date: 2026-07-21\n"
        "acknowledged_data_loss:\n"
        "  pickup_latitude:\n"
        "    ack_date: 2026-07-21\n"
        "  pickup_longitude:\n"
        "    ack_date: 2026-07-21\n"
    )

    r2 = _run("yellow", cwd=workdir)
    assert r2.returncode == 0, (r2.stdout, r2.stderr)

    out_dir = workdir / "raw-normalized" / "yellow"
    out_files = sorted(out_dir.rglob("*.parquet"))
    assert len(out_files) == 3
    import duckdb
    conn = duckdb.connect(":memory:")
    era1_out = next(f for f in out_files if "2009-01" in f.name)
    cols = {r[0] for r in conn.execute(f"DESCRIBE SELECT * FROM '{era1_out}'").fetchall()}
    assert "tpep_pickup_datetime" in cols
    assert "pu_datetime" not in cols
    assert "pickup_latitude" not in cols


def test_normalize_with_unresolved_mapping_amends_and_exits_1(yellow_family, tmp_path):
    """A partially-filled mapping that leaves items unresolved: the CLI amends
    the mapping with SUGGESTED/TODO items for the unresolved columns and exits 1."""
    workdir = tmp_path
    mapping_dir = workdir / "normalize" / "mappings"
    mapping_dir.mkdir(parents=True)
    mapping_path = mapping_dir / "yellow.yaml"
    # Minimal mapping — every historical column with data is unresolved.
    mapping_path.write_text("target: yellow_tripdata_2024-01.parquet\n")

    r = _run("yellow", cwd=workdir)
    assert r.returncode == 1
    combined = r.stdout + r.stderr
    assert "unresolved" in combined.lower()
    assert "pu_datetime" in combined
    # The amend added new suggestions to the mapping file.
    updated = mapping_path.read_text()
    assert "SUGGESTED" in updated or "TODO" in updated


def test_normalize_missing_raw_data_skips_cleanly(tmp_path):
    """`normalize yellow` when raw/yellow/ doesn't exist should not fail hard."""
    r = _run("yellow", cwd=tmp_path)
    assert r.returncode == 0
    assert "no raw files" in (r.stdout + r.stderr).lower()


def test_normalize_no_args_iterates_all_types(tmp_path):
    """Bare `normalize` iterates over all four types; with no raw data present,
    each one skips cleanly and the overall exit code is 0."""
    r = _run(cwd=tmp_path)
    assert r.returncode == 0
    combined = r.stdout + r.stderr
    for t in ("yellow", "green", "fhv", "fhvhv"):
        assert t in combined
