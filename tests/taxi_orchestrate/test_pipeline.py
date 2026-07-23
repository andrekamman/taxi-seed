import pytest

from taxi_orchestrate.pipeline import (
    CONN_ERROR, DOWNLOAD, FAILED, LOAD, NEEDS_REVIEW, NORMALIZE, OK, PARTIAL,
    StageOutcome, classify, overall_exit_code,
)


def test_download_ok_does_not_halt():
    o = classify(DOWNLOAD, 0)
    assert (o.status, o.halt_type, o.abort_run) == (OK, False, False)


def test_download_failure_halts_type():
    o = classify(DOWNLOAD, 1)
    assert (o.status, o.halt_type, o.abort_run) == (FAILED, True, False)


def test_normalize_ok():
    assert classify(NORMALIZE, 0).status == OK


@pytest.mark.parametrize("code", [1, 3])
def test_normalize_needs_review_halts(code):
    o = classify(NORMALIZE, code)
    assert (o.status, o.halt_type) == (NEEDS_REVIEW, True)


def test_normalize_error_halts():
    o = classify(NORMALIZE, 2)
    assert (o.status, o.halt_type) == (FAILED, True)


def test_load_ok():
    assert classify(LOAD, 0).status == OK


def test_load_partial_does_not_halt_or_abort():
    o = classify(LOAD, 1)
    assert (o.status, o.halt_type, o.abort_run) == (PARTIAL, False, False)


def test_load_conn_error_aborts_run():
    o = classify(LOAD, 2)
    assert (o.status, o.halt_type, o.abort_run) == (CONN_ERROR, True, True)


def test_unknown_stage_raises():
    with pytest.raises(ValueError):
        classify("bogus", 0)


def test_overall_clean():
    assert overall_exit_code([classify(DOWNLOAD, 0), classify(NORMALIZE, 0)]) == 0


def test_overall_needs_review_is_1():
    assert overall_exit_code([classify(NORMALIZE, 3)]) == 1


def test_overall_failure_is_2():
    assert overall_exit_code([classify(DOWNLOAD, 1)]) == 2


def test_overall_failure_beats_needs_review():
    outs = [classify(NORMALIZE, 3), classify(DOWNLOAD, 1)]
    assert overall_exit_code(outs) == 2


def test_overall_load_partial_is_2():
    assert overall_exit_code([classify(NORMALIZE, 0), classify(LOAD, 1)]) == 2
