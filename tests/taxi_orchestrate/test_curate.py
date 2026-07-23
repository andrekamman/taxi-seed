import duckdb

from taxi_normalize.data_check import get_file_metadata
from taxi_normalize.mapping import load_mapping
from taxi_normalize.planner import plan_file
from taxi_orchestrate.curate import curate_type, render_report


def _unresolved_count(raw_dir, mapping_path):
    mapping = load_mapping(mapping_path)
    conn = duckdb.connect(":memory:")
    files = sorted(raw_dir.rglob("*.parquet"))
    target = [f for f in files if f.name == mapping.target][0]
    target_md = get_file_metadata(conn, target)
    return sum(len(plan_file(get_file_metadata(conn, f), target_md, mapping).unresolved)
               for f in files)


def test_curate_produces_planner_clean_mapping(drift_family, tmp_path):
    mapping_path = tmp_path / "mappings" / "yellow.yaml"
    curate_type("yellow", drift_family, mapping_path, today="2026-07-23")
    assert mapping_path.exists()
    # The normalizer's OWN planner accepts the mapping with zero unresolved.
    assert _unresolved_count(drift_family, mapping_path) == 0


def test_curate_acks_float_to_int_lossy(drift_family, tmp_path):
    mapping_path = tmp_path / "mappings" / "yellow.yaml"
    result = curate_type("yellow", drift_family, mapping_path, today="2026-07-23")
    mapping = load_mapping(mapping_path)
    assert "passenger_count" in mapping.lossy_casts
    e = mapping.lossy_casts["passenger_count"]
    assert e.ack_date == "2026-07-23" and e.ack_by == "auto-curated"
    assert e.from_type.upper().startswith("DOUBLE") and e.to_type.upper() == "BIGINT"
    assert any(d.column == "passenger_count" for d in result.lossy)


def test_curate_renames_then_drops(drift_family, tmp_path):
    mapping_path = tmp_path / "mappings" / "yellow.yaml"
    result = curate_type("yellow", drift_family, mapping_path, today="2026-07-23")
    mapping = load_mapping(mapping_path)
    # pu_datetime is a detected rename target, not a dropped column
    assert mapping.renames.get("pu_datetime") == "tpep_pickup_datetime"
    # pickup_latitude has no rename candidate -> data-loss ack
    assert "pickup_latitude" in mapping.acknowledged_data_loss
    assert mapping.acknowledged_data_loss["pickup_latitude"].ack_by == "auto-curated"


def test_render_report_lists_ack_required(drift_family, tmp_path):
    mapping_path = tmp_path / "mappings" / "yellow.yaml"
    result = curate_type("yellow", drift_family, mapping_path, today="2026-07-23")
    text = render_report([result], today="2026-07-23")
    assert "passenger_count" in text and "pickup_latitude" in text and "yellow" in text
    assert "Acknowledgments required" in text
