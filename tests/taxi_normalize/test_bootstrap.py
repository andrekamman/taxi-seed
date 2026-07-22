"""Tests for bootstrap YAML scaffold generation."""
from pathlib import Path

import pytest
import yaml

from taxi_normalize.bootstrap import bootstrap_type
from taxi_normalize.mapping import MappingError, load_mapping


def test_bootstrap_writes_yaml_with_target_pinned(yellow_family, tmp_path):
    out_yaml = tmp_path / "yellow.yaml"
    bootstrap_type("yellow", yellow_family, out_yaml)
    assert out_yaml.exists()
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


def test_bootstrap_refuses_to_overwrite(yellow_family, tmp_path):
    out_yaml = tmp_path / "yellow.yaml"
    out_yaml.write_text("target: existing.parquet\n")
    with pytest.raises(FileExistsError):
        bootstrap_type("yellow", yellow_family, out_yaml)


def test_bootstrap_sample_flag_accepts_percent(yellow_family, tmp_path):
    # Sanity: the call succeeds when sample is a percent string.
    out_yaml = tmp_path / "yellow.yaml"
    bootstrap_type("yellow", yellow_family, out_yaml, sample="10%")
    assert out_yaml.exists()


def test_bootstrap_sample_flag_accepts_absolute_rows(yellow_family, tmp_path):
    out_yaml = tmp_path / "yellow.yaml"
    bootstrap_type("yellow", yellow_family, out_yaml, sample="100")
    assert out_yaml.exists()


def test_bootstrap_no_drift_family_produces_minimal_yaml(no_drift_family, tmp_path):
    out_yaml = tmp_path / "green.yaml"
    bootstrap_type("green", no_drift_family, out_yaml)
    raw = yaml.safe_load(out_yaml.read_text())
    # Nothing should need suggestion or ack
    assert raw.get("renames", {}) in ({}, None)
    # Loading should succeed with just target set
    m = load_mapping(out_yaml)
    assert m.target.startswith("green_tripdata_")
