import pytest

from taxi_loader.connection import (
    ConnConfig, LoaderConfigError, build_conn_string, validate_identifier,
)


def cfg(**kw):
    base = dict(host="h", port=1433, database="taxi", schema="dbo",
               user="sa", password="secret-pw")
    base.update(kw)
    return ConnConfig(**base)


def test_validate_identifier_accepts_plain():
    assert validate_identifier("dbo", "schema") == "dbo"
    assert validate_identifier("taxi_2", "database") == "taxi_2"


@pytest.mark.parametrize("bad", ["a-b", "1x", "a b", "a;drop", "", "a'b"])
def test_validate_identifier_rejects(bad):
    with pytest.raises(LoaderConfigError):
        validate_identifier(bad, "schema")


def test_conn_string_has_fields_and_password():
    s = build_conn_string(cfg(host="db1", port=1444, password="p@ss"), "taxi")
    assert "Server=db1,1444" in s
    assert "Database=taxi" in s
    assert "User Id=sa" in s
    assert "Password=p@ss" in s
    assert "Encrypt=yes" in s
    assert "TrustServerCertificate=yes" in s


def test_conn_string_targets_requested_database():
    s = build_conn_string(cfg(), "master")
    assert "Database=master" in s
