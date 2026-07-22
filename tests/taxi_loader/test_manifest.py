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
    create, pk = build_manifest_ddl("dbo")
    assert create.startswith("CREATE TABLE dbo._load_manifest (")
    assert "data_type NVARCHAR(16)" in create
    assert not create.rstrip().endswith(";")
    assert "ALTER TABLE dbo._load_manifest ADD CONSTRAINT" in pk
    assert "PRIMARY KEY (data_type, year, month)" in pk
