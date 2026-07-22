"""Tests for YAML mapping loading and validation."""
import pytest

from taxi_normalize.mapping import (
    Mapping,
    LossyCastEntry,
    DataLossEntry,
    MappingError,
    load_mapping,
)


def _write(path, content):
    path.write_text(content)
    return path


def test_load_minimal_valid_mapping(tmp_path):
    f = _write(tmp_path / "yellow.yaml", "target: yellow_tripdata_2024-01.parquet\n")
    m = load_mapping(f)
    assert m.target == "yellow_tripdata_2024-01.parquet"
    assert m.renames == {}
    assert m.lossy_casts == {}
    assert m.acknowledged_data_loss == {}


def test_load_full_mapping(tmp_path):
    f = _write(tmp_path / "yellow.yaml", """
target: yellow_tripdata_2024-01.parquet
renames:
  pu_datetime: tpep_pickup_datetime
  do_datetime: tpep_dropoff_datetime
lossy_casts:
  passenger_count:
    from: DOUBLE
    to: BIGINT
    ack_date: 2026-07-21
    ack_by: andrekamman
    reason: "Integer semantically"
acknowledged_data_loss:
  pickup_latitude:
    ack_date: 2026-07-21
""")
    m = load_mapping(f)
    assert m.renames == {"pu_datetime": "tpep_pickup_datetime", "do_datetime": "tpep_dropoff_datetime"}
    assert m.lossy_casts["passenger_count"].ack_date == "2026-07-21"
    assert m.lossy_casts["passenger_count"].ack_by == "andrekamman"
    assert m.lossy_casts["passenger_count"].reason == "Integer semantically"
    assert m.lossy_casts["passenger_count"].from_type == "DOUBLE"
    assert m.lossy_casts["passenger_count"].to_type == "BIGINT"
    assert m.acknowledged_data_loss["pickup_latitude"].ack_date == "2026-07-21"
    assert m.acknowledged_data_loss["pickup_latitude"].ack_by is None
    assert m.acknowledged_data_loss["pickup_latitude"].reason is None


def test_missing_ack_date_on_lossy_cast_raises(tmp_path):
    f = _write(tmp_path / "yellow.yaml", """
target: yellow_tripdata_2024-01.parquet
lossy_casts:
  passenger_count:
    from: DOUBLE
    to: BIGINT
    ack_by: andrekamman
""")
    with pytest.raises(MappingError, match="ack_date"):
        load_mapping(f)


def test_missing_ack_date_on_data_loss_raises(tmp_path):
    f = _write(tmp_path / "yellow.yaml", """
target: yellow_tripdata_2024-01.parquet
acknowledged_data_loss:
  pickup_latitude:
    reason: "removed"
""")
    with pytest.raises(MappingError, match="ack_date"):
        load_mapping(f)


def test_missing_target_raises(tmp_path):
    f = _write(tmp_path / "yellow.yaml", "renames: {}\n")
    with pytest.raises(MappingError, match="target"):
        load_mapping(f)


def test_unknown_top_level_key_raises(tmp_path):
    f = _write(tmp_path / "yellow.yaml", """
target: yellow_tripdata_2024-01.parquet
something_weird: {}
""")
    with pytest.raises(MappingError, match="something_weird"):
        load_mapping(f)


def test_missing_file_raises(tmp_path):
    with pytest.raises(MappingError, match="not found"):
        load_mapping(tmp_path / "doesnotexist.yaml")


def test_malformed_yaml_raises(tmp_path):
    f = _write(tmp_path / "yellow.yaml", "target: yellow\n  invalid: yaml:\n")
    with pytest.raises(MappingError):
        load_mapping(f)
