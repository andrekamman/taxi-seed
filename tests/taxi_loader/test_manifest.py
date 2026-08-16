import duckdb
import pytest

from taxi_loader import manifest
from taxi_loader.connection import ConnConfig
from taxi_loader.manifest import (
    MANIFEST_COLUMNS, build_manifest_ddl, manifest_fq,
)


def test_manifest_fq_uses_schema():
    assert manifest_fq("dbo") == "dbo._load_manifest"
    assert manifest_fq("stage") == "stage._load_manifest"


def test_manifest_columns_are_pk_compatible():
    # data_type must be bounded (not NVARCHAR(MAX)) to sit in the PK.
    assert MANIFEST_COLUMNS["data_type"].startswith("NVARCHAR(")
    assert "MAX" not in MANIFEST_COLUMNS["data_type"]
    assert MANIFEST_COLUMNS["year"] == "INT"
    assert MANIFEST_COLUMNS["row_count"] == "BIGINT"


def test_build_manifest_ddl_has_create_and_pk():
    stmts = build_manifest_ddl("dbo")
    assert len(stmts) == 1
    ddl = stmts[0]
    assert ddl.startswith("CREATE TABLE dbo._load_manifest (")
    assert "data_type NVARCHAR(16)" in ddl
    assert "PRIMARY KEY (data_type, year, month)" in ddl
    assert not ddl.rstrip().endswith(";")


def test_build_manifest_ddl_is_page_compressed_and_well_formed():
    ddl = build_manifest_ddl("dbo")[0]
    # The PK constraint must be spliced into the column list, not into the
    # WITH(...) options clause -- and the WITH clause must be the very last
    # thing in the statement.
    assert ddl.endswith(
        "    CONSTRAINT PK_dbo__load_manifest PRIMARY KEY (data_type, year, month)\n"
        ") WITH (DATA_COMPRESSION = PAGE)"
    )
    assert "PAGE,\n    CONSTRAINT" not in ddl
    assert ddl.count("(") == ddl.count(")")


class _FakeConn:
    def __init__(self, on_execute):
        self._on_execute = on_execute

    def execute(self, sql, params=None):
        return self._on_execute(sql, params)


def _cfg():
    return ConnConfig(host="h", port=1433, database="taxi", schema="dbo",
                      user="sa", password="pw")


def _duplicate_object(sql, params=None):
    raise duckdb.Error("There is already an object named '_load_manifest'")


def test_ensure_manifest_table_tolerates_a_concurrent_creator(monkeypatch):
    """Every worker calls this on start-up; only one can win the CREATE."""
    calls = {"exists": 0}

    def exists(conn, cfg):
        calls["exists"] += 1
        return calls["exists"] > 1

    monkeypatch.setattr(manifest, "manifest_table_exists", exists)
    manifest.ensure_manifest_table(_FakeConn(_duplicate_object), _cfg())
    assert calls["exists"] == 2


def test_ensure_manifest_table_reraises_when_still_absent(monkeypatch):
    monkeypatch.setattr(manifest, "manifest_table_exists", lambda conn, cfg: False)
    with pytest.raises(duckdb.Error):
        manifest.ensure_manifest_table(_FakeConn(_duplicate_object), _cfg())
