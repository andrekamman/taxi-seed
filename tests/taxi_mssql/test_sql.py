import pytest

from taxi_mssql import sql


def test_create_database_places_files_and_sizes_them():
    stmt = sql.create_database(
        "taxi", "/data/mssql/data/taxi.mdf", "/data/mssql/log/taxi_log.ldf",
        data_mb=20480, log_mb=16384, growth_mb=1024,
    )
    assert "CREATE DATABASE [taxi]" in stmt
    assert "FILENAME = N'/data/mssql/data/taxi.mdf'" in stmt
    assert "FILENAME = N'/data/mssql/log/taxi_log.ldf'" in stmt
    assert "SIZE = 20480MB" in stmt
    assert "SIZE = 16384MB" in stmt
    assert "FILEGROWTH = 1024MB" in stmt


def test_attach_database_reattaches_both_files_without_sizing_them():
    stmt = sql.attach_database(
        "taxi", "/data/mssql/data/taxi.mdf", "/data/mssql/log/taxi_log.ldf")
    assert "CREATE DATABASE [taxi]" in stmt
    assert "FILENAME = N'/data/mssql/data/taxi.mdf'" in stmt
    assert "FILENAME = N'/data/mssql/log/taxi_log.ldf'" in stmt
    assert stmt.rstrip().endswith("FOR ATTACH")
    # Sizing an existing file is meaningless and would be rejected.
    assert "SIZE =" not in stmt


def test_attach_database_rejects_a_bad_identifier():
    with pytest.raises(sql.SqlIdentifierError):
        sql.attach_database("bad name", "/a.mdf", "/b.ldf")


def test_attach_database_escapes_quotes_in_paths():
    stmt = sql.attach_database("taxi", "/data/o'brien.mdf", "/b.ldf")
    assert "N'/data/o''brien.mdf'" in stmt


def test_set_recovery_simple():
    assert sql.set_recovery_simple("taxi") == "ALTER DATABASE [taxi] SET RECOVERY SIMPLE"


def test_database_exists_query():
    assert "DB_ID('taxi')" in sql.database_exists("taxi")


def test_data_file_size_query_targets_the_rows_file():
    q = sql.data_file_size_mb("taxi")
    assert "sys.database_files" in q
    assert "type_desc = 'ROWS'" in q


def test_max_loaded_at_query_uses_the_schema():
    q = sql.max_loaded_at("dbo")
    assert "dbo._load_manifest" in q
    assert "MAX(loaded_at)" in q
    assert "OBJECT_ID" in q


def test_ola_installed_query():
    assert "dbo.DatabaseBackup" in sql.ola_installed()


def test_rows_per_type_query_groups_by_type():
    q = sql.rows_per_type("dbo")
    assert "dbo._load_manifest" in q
    assert "SUM(row_count)" in q
    assert "GROUP BY data_type" in q


def test_database_backup_uses_the_agreed_parameters():
    stmt = sql.database_backup("taxi", "/data/mssql/backup")
    for fragment in [
        "@Databases = N'taxi'",
        "@Directory = N'/data/mssql/backup'",
        "@BackupType = 'FULL'",
        "@Compress = 'Y'",
        "@CheckSum = 'Y'",
        "@Verify = 'Y'",
        "@CleanupTime = 1",
        "@CleanupMode = 'AFTER_BACKUP'",
    ]:
        assert fragment in stmt


def test_identifier_validation_rejects_injection():
    with pytest.raises(sql.SqlIdentifierError):
        sql.validate_identifier("taxi]; DROP DATABASE [x", "database")


def test_identifier_validation_accepts_normal_names():
    assert sql.validate_identifier("taxi", "database") == "taxi"


def test_create_database_rejects_a_bad_identifier():
    with pytest.raises(sql.SqlIdentifierError):
        sql.create_database("bad name", "/a.mdf", "/b.ldf", 1, 1, 1)


def test_paths_with_quotes_are_escaped():
    stmt = sql.create_database("taxi", "/data/o'brien.mdf", "/b.ldf", 1, 1, 1)
    assert "N'/data/o''brien.mdf'" in stmt


def test_prepare_maintenance_solution_disables_jobs_and_sets_directory():
    original = (
        "DECLARE @CreateJobs nvarchar(max)          = 'Y'   -- create jobs\n"
        "DECLARE @BackupDirectory nvarchar(max)     = NULL  -- backup root\n"
        "DECLARE @OutputFileDirectory nvarchar(max) = NULL\n"
    )
    out = sql.prepare_maintenance_solution(original, "/data/mssql/backup")
    assert "= 'N'" in out
    assert "= 'Y'" not in out
    assert "= N'/data/mssql/backup'" in out
    # alignment and trailing comments survive
    assert "-- create jobs" in out
    assert "-- backup root" in out


def test_prepare_maintenance_solution_is_idempotent():
    original = ("DECLARE @CreateJobs nvarchar(max) = 'Y'\n"
                "DECLARE @BackupDirectory nvarchar(max) = NULL\n")
    once = sql.prepare_maintenance_solution(original, "/b")
    assert sql.prepare_maintenance_solution(once, "/b") == once


def test_prepare_maintenance_solution_ignores_the_valueless_redeclaration():
    """Regression: a second `DECLARE @BackupDirectory nvarchar(max)` (no
    `= value`) appears inside a stored-procedure body further down the real
    script. Requiring the "= <value>" suffix must skip it -- a careless
    pattern without that requirement risks patching the wrong occurrence."""
    original = (
        "DECLARE @CreateJobs nvarchar(max) = 'Y'\n"
        "DECLARE @BackupDirectory nvarchar(max) = NULL\n"
        "...\n"
        "DECLARE @BackupDirectory nvarchar(max)\n"
        "SELECT @BackupDirectory = Value FROM #Config WHERE [Name] = 'BackupDirectory'\n"
    )
    out = sql.prepare_maintenance_solution(original, "/data/mssql/backup")
    assert "DECLARE @BackupDirectory nvarchar(max) = N'/data/mssql/backup'" in out
    # the valueless redeclaration is untouched
    assert "DECLARE @BackupDirectory nvarchar(max)\n" in out


def test_prepare_maintenance_solution_fails_loudly_if_markers_missing():
    with pytest.raises(sql.SqlIdentifierError, match="CreateJobs"):
        sql.prepare_maintenance_solution("-- nothing here\n", "/b")


# Regression tests for bugs found in review

def test_prepare_maintenance_solution_is_idempotent_with_quotes_in_directory():
    """Regression test: idempotency with escaped quotes in directory path."""
    original = (
        "DECLARE @CreateJobs nvarchar(max) = 'Y'\n"
        "DECLARE @BackupDirectory nvarchar(max) = NULL\n"
    )
    once = sql.prepare_maintenance_solution(original, "/data/o'brien/backup")
    twice = sql.prepare_maintenance_solution(once, "/data/o'brien/backup")
    assert once == twice


def test_identifier_validation_rejects_trailing_newline():
    """Regression test: trailing newline must be rejected."""
    with pytest.raises(sql.SqlIdentifierError):
        sql.validate_identifier("taxi\n", "database")


def test_set_recovery_simple_rejects_bad_identifier():
    """Coverage: set_recovery_simple validates its identifier argument."""
    with pytest.raises(sql.SqlIdentifierError):
        sql.set_recovery_simple("bad name")


def test_data_file_size_mb_rejects_bad_identifier():
    """Coverage: data_file_size_mb validates its identifier argument."""
    with pytest.raises(sql.SqlIdentifierError):
        sql.data_file_size_mb("bad name")


def test_max_loaded_at_rejects_bad_identifier():
    """Coverage: max_loaded_at validates its identifier argument."""
    with pytest.raises(sql.SqlIdentifierError):
        sql.max_loaded_at("bad name")


def test_rows_per_type_rejects_bad_identifier():
    """Coverage: rows_per_type validates its identifier argument."""
    with pytest.raises(sql.SqlIdentifierError):
        sql.rows_per_type("bad name")


def test_loaded_table_rows_targets_the_year_tables_of_one_type():
    out = sql.loaded_table_rows("dbo", "fhvhv")
    assert "sys.partitions" in out
    assert "index_id IN (0, 1)" in out
    assert "s.name = 'dbo'" in out
    # The underscore in "fhvhv_2019" is a LIKE wildcard and MUST be escaped,
    # or "fhvhvX2019" would match too.
    assert r"t.name LIKE 'fhvhv\_%' ESCAPE '\'" in out


def test_loaded_table_rows_rejects_a_bad_schema():
    with pytest.raises(sql.SqlIdentifierError):
        sql.loaded_table_rows("dbo; DROP TABLE x --", "fhvhv")


def test_loaded_table_rows_rejects_a_bad_type():
    with pytest.raises(sql.SqlIdentifierError):
        sql.loaded_table_rows("dbo", "fhvhv'; --")


def test_set_max_server_memory_reconfigures():
    out = sql.set_max_server_memory(24057)
    assert "sp_configure 'show advanced options', 1" in out
    assert "sp_configure 'max server memory (MB)', 24057" in out
    assert out.count("RECONFIGURE") == 2


def test_set_max_server_memory_rejects_a_non_integer():
    with pytest.raises(ValueError):
        sql.set_max_server_memory("24057; DROP DATABASE taxi")


def test_set_max_server_memory_rejects_a_bool():
    """bool is an int subclass, and `True` would render as the literal 1."""
    with pytest.raises(ValueError):
        sql.set_max_server_memory(True)


def test_reset_year_truncates_and_clears_the_manifest():
    out = sql.reset_year("dbo", "fhvhv", 2019)
    assert "TRUNCATE TABLE dbo.fhvhv_2019" in out
    assert "OBJECT_ID('dbo.fhvhv_2019', 'U') IS NOT NULL" in out
    assert "DELETE FROM dbo._load_manifest" in out
    assert "data_type = 'fhvhv'" in out
    assert "year = 2019" in out


def test_reset_year_rejects_a_non_integer_year():
    with pytest.raises(ValueError):
        sql.reset_year("dbo", "fhvhv", "2019; DROP DATABASE taxi")
