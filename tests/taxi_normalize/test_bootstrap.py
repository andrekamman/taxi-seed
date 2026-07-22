"""Tests for bootstrap YAML scaffold + amend generation."""
from pathlib import Path

import pytest
import yaml

from taxi_normalize.bootstrap import BootstrapResult, bootstrap_type
from taxi_normalize.mapping import load_mapping


def test_bootstrap_writes_yaml_with_target_pinned(yellow_family, tmp_path):
    out_yaml = tmp_path / "yellow.yaml"
    result = bootstrap_type("yellow", yellow_family, out_yaml)
    assert out_yaml.exists()
    assert result.was_new is True
    raw = yaml.safe_load(out_yaml.read_text())
    # Target is pinned to the newest file
    assert raw["target"] == "yellow_tripdata_2024-01.parquet"


def test_bootstrap_emits_suggested_rename_for_pu_datetime(yellow_family, tmp_path):
    out_yaml = tmp_path / "yellow.yaml"
    bootstrap_type("yellow", yellow_family, out_yaml)
    text = out_yaml.read_text()
    # Should suggest pu_datetime -> tpep_pickup_datetime as a commented SUGGESTED entry
    assert "SUGGESTED" in text
    assert "pu_datetime" in text
    assert "tpep_pickup_datetime" in text


def test_bootstrap_flags_pickup_latitude_as_potential_data_loss(yellow_family, tmp_path):
    out_yaml = tmp_path / "yellow.yaml"
    bootstrap_type("yellow", yellow_family, out_yaml)
    text = out_yaml.read_text()
    # pickup_latitude/longitude have data but no target column and no rename candidate
    # (they should appear in the acknowledged_data_loss TODO block)
    assert "pickup_latitude" in text
    assert "acknowledged_data_loss" in text
    assert "TODO" in text


def test_bootstrap_emits_timeline_header(yellow_family, tmp_path):
    out_yaml = tmp_path / "yellow.yaml"
    result = bootstrap_type("yellow", yellow_family, out_yaml)
    text = out_yaml.read_text()
    assert "Detected drift transitions:" in text
    assert len(result.timeline) >= 1


def test_bootstrap_amends_existing_yaml_preserves_content(yellow_family, tmp_path):
    """Second run against an already-edited YAML must preserve the human's entries."""
    out_yaml = tmp_path / "yellow.yaml"
    # Simulate a human-completed mapping.
    out_yaml.write_text(
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

    result = bootstrap_type("yellow", yellow_family, out_yaml)
    assert result.was_new is False

    # Existing content still parseable and semantically identical.
    m = load_mapping(out_yaml)
    assert m.target == "yellow_tripdata_2024-01.parquet"
    assert m.renames == {
        "pu_datetime": "tpep_pickup_datetime",
        "do_datetime": "tpep_dropoff_datetime",
    }
    assert "passenger_count" in m.lossy_casts
    assert m.lossy_casts["passenger_count"].ack_date == "2026-07-21"
    assert set(m.acknowledged_data_loss.keys()) == {"pickup_latitude", "pickup_longitude"}


def test_bootstrap_amend_reports_zero_new_items_when_fully_resolved(yellow_family, tmp_path):
    """If the existing YAML covers every drift item, amend should add nothing."""
    out_yaml = tmp_path / "yellow.yaml"
    out_yaml.write_text(
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
    result = bootstrap_type("yellow", yellow_family, out_yaml)
    assert result.was_new is False
    assert result.new_items == 0
    text = out_yaml.read_text()
    # No new SUGGESTED-rename lines or TODO ack lines — only the header banner may mention
    # the word "SUGGESTED" in explanation prose. Match the concrete line prefixes instead.
    assert "# SUGGESTED (confidence" not in text
    assert "ack_date: TODO" not in text


def test_bootstrap_amend_flags_only_new_items(yellow_family, tmp_path):
    """If the existing YAML covers renames but not data-loss items, amend
    should append the missing acknowledged_data_loss TODOs but no rename SUGGESTED
    lines for the columns already covered."""
    out_yaml = tmp_path / "yellow.yaml"
    out_yaml.write_text(
        "target: yellow_tripdata_2024-01.parquet\n"
        "renames:\n"
        "  pu_datetime: tpep_pickup_datetime\n"
        "  do_datetime: tpep_dropoff_datetime\n"
    )
    result = bootstrap_type("yellow", yellow_family, out_yaml)
    assert result.was_new is False
    text = out_yaml.read_text()
    # The renames already there — no SUGGESTED for them.
    assert "# pu_datetime: tpep_pickup_datetime" not in text
    assert "# do_datetime: tpep_dropoff_datetime" not in text
    # Data-loss TODOs still appear.
    assert "pickup_latitude" in text
    assert "TODO" in text


def test_bootstrap_sample_flag_accepts_percent(yellow_family, tmp_path):
    out_yaml = tmp_path / "yellow.yaml"
    bootstrap_type("yellow", yellow_family, out_yaml, sample="10%")
    assert out_yaml.exists()


def test_bootstrap_sample_flag_accepts_absolute_rows(yellow_family, tmp_path):
    out_yaml = tmp_path / "yellow.yaml"
    bootstrap_type("yellow", yellow_family, out_yaml, sample="100")
    assert out_yaml.exists()


def test_bootstrap_no_drift_family_produces_minimal_yaml(no_drift_family, tmp_path):
    out_yaml = tmp_path / "green.yaml"
    result = bootstrap_type("green", no_drift_family, out_yaml)
    raw = yaml.safe_load(out_yaml.read_text())
    assert raw.get("renames", {}) in ({}, None)
    m = load_mapping(out_yaml)
    assert m.target.startswith("green_tripdata_")
    assert result.new_items == 0
