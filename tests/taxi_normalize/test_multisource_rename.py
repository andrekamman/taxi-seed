from taxi_normalize.mapping import Mapping
from taxi_normalize.planner import plan_file


def _md(type_map):
    return {c: {"type": t, "min": 1, "max": 9, "null_count": 0, "num_rows": 9} for c, t in type_map.items()}


def test_first_era_source_renamed():
    raw = _md({"Trip_Pickup_DateTime": "TIMESTAMP", "vendorid": "BIGINT"})
    target = _md({"tpep_pickup_datetime": "TIMESTAMP", "vendorid": "BIGINT"})
    mapping = Mapping(target="t.parquet",
                      renames={"Trip_Pickup_DateTime": "tpep_pickup_datetime",
                               "pickup_datetime": "tpep_pickup_datetime"})
    plan = plan_file(raw, target, mapping)
    assert plan.unresolved == []
    r = next(a for a in plan.actions if a.target_column == "tpep_pickup_datetime")
    assert r.action == "rename" and r.source_column == "Trip_Pickup_DateTime"


def test_second_era_source_renamed():
    raw = _md({"pickup_datetime": "TIMESTAMP", "vendorid": "BIGINT"})
    target = _md({"tpep_pickup_datetime": "TIMESTAMP", "vendorid": "BIGINT"})
    mapping = Mapping(target="t.parquet",
                      renames={"Trip_Pickup_DateTime": "tpep_pickup_datetime",
                               "pickup_datetime": "tpep_pickup_datetime"})
    r = next(a for a in plan_file(raw, target, mapping).actions if a.target_column == "tpep_pickup_datetime")
    assert r.action == "rename" and r.source_column == "pickup_datetime"


def test_both_sources_present_is_unresolved():
    raw = _md({"Trip_Pickup_DateTime": "TIMESTAMP", "pickup_datetime": "TIMESTAMP", "vendorid": "BIGINT"})
    target = _md({"tpep_pickup_datetime": "TIMESTAMP", "vendorid": "BIGINT"})
    mapping = Mapping(target="t.parquet",
                      renames={"Trip_Pickup_DateTime": "tpep_pickup_datetime",
                               "pickup_datetime": "tpep_pickup_datetime"})
    plan = plan_file(raw, target, mapping)
    assert any(u.kind == "ambiguous_multisource_rename" for u in plan.unresolved)


def test_target_present_directly_wins():
    raw = _md({"tpep_pickup_datetime": "TIMESTAMP", "vendorid": "BIGINT"})
    target = _md({"tpep_pickup_datetime": "TIMESTAMP", "vendorid": "BIGINT"})
    mapping = Mapping(target="t.parquet",
                      renames={"Trip_Pickup_DateTime": "tpep_pickup_datetime"})
    plan = plan_file(raw, target, mapping)
    assert plan.unresolved == []
    a = next(a for a in plan.actions if a.target_column == "tpep_pickup_datetime")
    assert a.action == "passthrough"


def test_single_source_rename_unchanged():
    raw = _md({"old": "BIGINT"})
    target = _md({"new": "BIGINT"})
    mapping = Mapping(target="t.parquet", renames={"old": "new"})
    a = next(a for a in plan_file(raw, target, mapping).actions if a.target_column == "new")
    assert a.action == "rename" and a.source_column == "old"
