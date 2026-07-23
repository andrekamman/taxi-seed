from taxi_normalize.bootstrap import DriftReport, detect_drift


def test_detect_drift_on_yellow_family(yellow_family):
    report = detect_drift("yellow", yellow_family, existing=None, sample="100%")
    assert isinstance(report, DriftReport)
    assert report.target_name.endswith(".parquet")
    # renames detected for the datetime columns
    rename_targets = {new for _old, new, _c in report.rename_suggestions}
    assert "tpep_pickup_datetime" in rename_targets
    # lossy is bootstrap's range-based detection (structured list; may be empty here)
    assert isinstance(report.lossy, list)
    for d in report.lossy:
        assert set(d) >= {"column", "from", "to", "reason", "files_present"}
    # era-1-only columns with data and no rename candidate -> data loss
    dataloss_cols = {d["column"] for d in report.data_loss}
    assert "pickup_latitude" in dataloss_cols or "pickup_longitude" in dataloss_cols
    for d in report.data_loss:
        assert set(d) >= {"column", "files_present"}


def test_detect_drift_respects_existing(yellow_family):
    from taxi_normalize.mapping import Mapping
    existing = Mapping(target="yellow_tripdata_2024-01.parquet",
                       renames={"pu_datetime": "tpep_pickup_datetime"})
    report = detect_drift("yellow", yellow_family, existing=existing, sample="100%")
    # already-handled rename is not re-suggested
    assert all(old != "pu_datetime" for old, _new, _c in report.rename_suggestions)
