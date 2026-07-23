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


def test_value_map_skips_numeric_same_name_column():
    # A DOUBLE same-name column with a value_map defined must CAST, not value_map
    # (fixes: DOUBLE RatecodeID 1.0..6.0 would be NULLed by string-keyed value_map).
    from taxi_normalize.mapping import Mapping
    raw = _md({"RatecodeID": "DOUBLE"})
    target = _md({"RatecodeID": "BIGINT"})
    mapping = Mapping(target="t.parquet",
                      value_maps={"RatecodeID": {"1": 1, "6": 6}},
                      value_map_unmapped={"RatecodeID": "null"})
    plan = plan_file(raw, target, mapping)
    # numeric same-name is NOT value-mapped (it casts, or is unresolved needing an ack)
    assert not any(a.action == "value_map" for a in plan.actions if a.target_column == "RatecodeID")


def test_value_map_applies_to_string_same_name_column():
    from taxi_normalize.mapping import Mapping
    raw = _md({"payment_type": "VARCHAR"})
    target = _md({"payment_type": "BIGINT"})
    mapping = Mapping(target="t.parquet", value_maps={"payment_type": {"CASH": 2}})
    a = next(a for a in plan_file(raw, target, mapping).actions if a.target_column == "payment_type")
    assert a.action == "value_map"   # string same-name still value-maps
