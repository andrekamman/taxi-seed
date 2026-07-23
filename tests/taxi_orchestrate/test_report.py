from taxi_orchestrate.pipeline import DOWNLOAD, LOAD, NORMALIZE, classify
from taxi_orchestrate.report import TypeRun, render_summary, type_label


def test_label_ok_download_normalize():
    r = TypeRun("yellow", [classify(DOWNLOAD, 0), classify(NORMALIZE, 0)])
    assert type_label(r) == "OK"


def test_label_loaded():
    r = TypeRun("yellow", [classify(DOWNLOAD, 0), classify(NORMALIZE, 0), classify(LOAD, 0)])
    assert type_label(r) == "LOADED"


def test_label_needs_review():
    r = TypeRun("green", [classify(DOWNLOAD, 0), classify(NORMALIZE, 1)])
    assert type_label(r) == "NEEDS REVIEW"


def test_label_download_failed():
    r = TypeRun("fhv", [classify(DOWNLOAD, 1)])
    assert type_label(r) == "DOWNLOAD FAILED"


def test_label_load_partial():
    r = TypeRun("yellow", [classify(DOWNLOAD, 0), classify(NORMALIZE, 0), classify(LOAD, 1)])
    assert type_label(r) == "LOAD PARTIAL"


def test_render_summary_lists_all_types():
    runs = [
        TypeRun("yellow", [classify(DOWNLOAD, 0), classify(NORMALIZE, 0)]),
        TypeRun("green", [classify(DOWNLOAD, 0), classify(NORMALIZE, 1)]),
    ]
    out = render_summary(runs)
    assert "yellow" in out and "green" in out
    assert "OK" in out and "NEEDS REVIEW" in out
