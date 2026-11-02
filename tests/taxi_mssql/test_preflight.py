import subprocess
from pathlib import Path

import pytest

from taxi_mssql import preflight
from taxi_mssql.config import from_env


def settings_for(tmp_path):
    return from_env({"MSSQL_PASSWORD": "pw", "TAXI_DATA_DIR": str(tmp_path)})


def baked(tmp_path):
    d = tmp_path / "baked"
    d.mkdir()
    for t in ("yellow", "green", "fhv", "fhvhv"):
        (d / f"{t}.yaml").write_text(f"# pristine {t}\n")
    return d


def test_ensure_directories_creates_the_full_layout(tmp_path):
    s = settings_for(tmp_path)
    preflight.ensure_directories(s)
    assert (tmp_path / "raw").is_dir()
    assert (tmp_path / "raw-normalized").is_dir()
    assert (tmp_path / "mssql" / "data").is_dir()
    assert (tmp_path / "mssql" / "log").is_dir()
    assert (tmp_path / "mssql" / "backup").is_dir()
    assert (tmp_path / "normalize" / "mappings").is_dir()


def test_ensure_directories_is_idempotent(tmp_path):
    s = settings_for(tmp_path)
    preflight.ensure_directories(s)
    preflight.ensure_directories(s)
    assert (tmp_path / "mssql" / "data").is_dir()


def test_seed_mappings_copies_when_absent(tmp_path):
    s = settings_for(tmp_path)
    preflight.ensure_directories(s)
    copied = preflight.seed_mappings(s, baked_dir=baked(tmp_path))
    assert sorted(copied) == ["fhv", "fhvhv", "green", "yellow"]
    assert (tmp_path / "normalize" / "mappings" / "yellow.yaml").read_text() == (
        "# pristine yellow\n"
    )


def test_seed_mappings_never_overwrites_an_amended_mapping(tmp_path):
    s = settings_for(tmp_path)
    preflight.ensure_directories(s)
    target = tmp_path / "normalize" / "mappings" / "yellow.yaml"
    target.write_text("# amended after schema drift\n")

    copied = preflight.seed_mappings(s, baked_dir=baked(tmp_path))

    assert "yellow" not in copied
    assert target.read_text() == "# amended after schema drift\n"


def test_seed_mappings_tolerates_a_missing_baked_dir(tmp_path):
    s = settings_for(tmp_path)
    preflight.ensure_directories(s)
    assert preflight.seed_mappings(s, baked_dir=tmp_path / "nope") == []


# --- taxi-load month capability --------------------------------------------

def _help(text):
    def helper(cmd):
        return subprocess.CompletedProcess(cmd, 0, text, "")
    return helper


