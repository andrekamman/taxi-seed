import pytest

from taxi_loader.connection import (
    ConnConfig, LoaderConfigError, secret_name, validate_identifier,
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


def test_secret_names_are_distinct_per_attach():
    # master and the target are attached simultaneously, so their secrets must not
    # collide -- CREATE OR REPLACE on a shared name would reconfigure the live one.
    from taxi_loader.connection import ATTACH_NAME, BOOT_ATTACH_NAME
    assert secret_name(ATTACH_NAME) != secret_name(BOOT_ATTACH_NAME)


def test_secret_statement_binds_every_field():
    """The password must never reach a SQL string.

    ATTACH takes no bind parameters, which is why the old connection-string form had to
    interpolate it as an escaped literal. The secret form binds all five fields, so no
    credential appears in any statement text.
    """
    from taxi_loader.connection import create_secret

    captured = []

    class FakeConn:
        def execute(self, sql, params=None):
            captured.append((sql, params))
            return self

    create_secret(FakeConn(), cfg(password="p@ss'; DROP--"), "taxi", "mssql")
    sql, params = captured[0]
    assert "p@ss" not in sql
    assert sql.count("?") == 5
    assert params == ["h", 1433, "taxi", "sa", "p@ss'; DROP--"]


# --- session settings and the require-existing gate --------------------------

def test_session_settings_cover_the_three_the_loader_depends_on():
    from taxi_loader.connection import SESSION_SETTINGS

    # Each of these has a default that is wrong for this loader: tablock='auto'
    # resolves to off for a clustered target, the catalog cache serves stale DDL,
    # and the query timeout is 30 seconds.
    assert SESSION_SETTINGS["mssql_copy_tablock"] == "true"
    assert SESSION_SETTINGS["mssql_exec_invalidate_cache"] is True
    assert SESSION_SETTINGS["mssql_query_timeout"] == 0
    # Parallel writers must NOT be pinned here: the default derives from the host's
    # thread count, which is the measured-fastest configuration.
    assert "mssql_copy_parallel_writers" not in SESSION_SETTINGS


def test_require_existing_refuses_an_absent_database():
    from taxi_loader.connection import (
        LoaderConnectionError, ensure_database,
    )

    class FakeConn:
        """Answers DB_ID(...) with NULL, i.e. the database does not exist."""

        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append(sql)
            return self

        def fetchone(self):
            return (None,)

    conn = FakeConn()
    with pytest.raises(LoaderConnectionError) as e:
        ensure_database(conn, cfg(database="absent"), require_existing=True)
    assert "does not exist" in str(e.value)
    # The point of the flag: it must not have tried to create anything.
    assert not any("CREATE DATABASE" in s for s in conn.executed)


def test_default_ensure_database_still_creates():
    from taxi_loader.connection import ensure_database

    class FakeConn:
        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append((sql, params))
            return self

        def fetchone(self):
            return (None,)

    conn = FakeConn()
    ensure_database(conn, cfg(database="taxi"))
    flat = " ".join(str(p) for _, p in conn.executed if p) + " ".join(s for s, _ in conn.executed)
    assert "CREATE DATABASE" in flat
