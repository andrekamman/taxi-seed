"""T-SQL string builders. Pure: these return SQL, they never execute it."""
from __future__ import annotations

import re

_IDENT_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")


class SqlIdentifierError(Exception):
    """An identifier or vendored script failed validation. Exit code 2."""


def validate_identifier(name: str, what: str) -> str:
    if not _IDENT_RE.match(name or ""):
        raise SqlIdentifierError(
            f"invalid {what} {name!r}: must match [A-Za-z_][A-Za-z0-9_]*"
        )
    return name


def _lit(value: str) -> str:
    """Escape a value for a T-SQL single-quoted string literal."""
    return value.replace("'", "''")


def create_database(database: str, mdf_path: str, ldf_path: str,
                    data_mb: int, log_mb: int, growth_mb: int) -> str:
    db = validate_identifier(database, "database")
    return (
        f"CREATE DATABASE [{db}]\n"
        f"  ON PRIMARY (NAME = N'{db}', FILENAME = N'{_lit(str(mdf_path))}',\n"
        f"    SIZE = {int(data_mb)}MB, FILEGROWTH = {int(growth_mb)}MB)\n"
        f"  LOG ON (NAME = N'{db}_log', FILENAME = N'{_lit(str(ldf_path))}',\n"
        f"    SIZE = {int(log_mb)}MB, FILEGROWTH = {int(growth_mb)}MB)"
    )


def attach_database(database: str, mdf_path: str, ldf_path: str) -> str:
    """Re-register an existing mdf/ldf pair with a server that has forgotten it.

    The user database's files live on the bind mount and survive
    `docker compose down`; the system databases that record them do not
    necessarily. Without this, the next run sees DB_ID('taxi') IS NULL, tries to
    CREATE, and SQL Server refuses with Msg 5170 ("cannot create file ...
    because it already exists") forever after.
    """
    db = validate_identifier(database, "database")
    return (
        f"CREATE DATABASE [{db}]\n"
        f"  ON (FILENAME = N'{_lit(str(mdf_path))}'),\n"
        f"     (FILENAME = N'{_lit(str(ldf_path))}')\n"
        f"  FOR ATTACH"
    )


def set_recovery_simple(database: str) -> str:
    return f"ALTER DATABASE [{validate_identifier(database, 'database')}] SET RECOVERY SIMPLE"


def database_exists(database: str) -> str:
    return f"SELECT CASE WHEN DB_ID('{_lit(database)}') IS NULL THEN 0 ELSE 1 END"


def data_file_size_mb(database: str) -> str:
    db = validate_identifier(database, "database")
    return (
        f"SELECT CAST(SUM(size) * 8 / 1024 AS BIGINT) "
        f"FROM [{db}].sys.database_files WHERE type_desc = 'ROWS'"
    )


def ola_installed() -> str:
    return "SELECT CASE WHEN OBJECT_ID('dbo.DatabaseBackup') IS NULL THEN 0 ELSE 1 END"


def max_loaded_at(schema: str) -> str:
    sch = validate_identifier(schema, "schema")
    return (
        f"IF OBJECT_ID('{sch}._load_manifest', 'U') IS NULL\n"
        f"    SELECT CONVERT(VARCHAR(27), NULL, 126)\n"
        f"ELSE\n"
        f"    SELECT CONVERT(VARCHAR(27), MAX(loaded_at), 126) FROM {sch}._load_manifest"
    )


def rows_per_type(schema: str) -> str:
    sch = validate_identifier(schema, "schema")
    return (
        f"SELECT data_type, SUM(row_count) FROM {sch}._load_manifest "
        f"GROUP BY data_type ORDER BY data_type"
    )


def loaded_table_rows(schema: str, data_type: str) -> str:
    """Committed rows across every year table of one trip type.

    Deliberately not _load_manifest, which rows_per_type above already reads:
    execute_year_plan's RELOAD branch copies a whole year in one statement and
    writes its manifest rows only afterwards, so the manifest reports zero for
    the entire two hours a large year takes. sys.partitions moves the whole
    time, which is the point of a heartbeat.
    """
    sch = validate_identifier(schema, "schema")
    dt = validate_identifier(data_type, "trip type")
    return (
        "SELECT ISNULL(SUM(p.rows), 0) FROM sys.tables t "
        "JOIN sys.schemas s ON s.schema_id = t.schema_id "
        "JOIN sys.partitions p ON p.object_id = t.object_id "
        "AND p.index_id IN (0, 1) "
        f"WHERE s.name = '{_lit(sch)}' "
        f"AND t.name LIKE '{_lit(dt)}\\_%' ESCAPE '\\'"
    )


def database_backup(database: str, directory: str) -> str:
    return (
        "EXECUTE dbo.DatabaseBackup "
        f"@Databases = N'{_lit(database)}', "
        f"@Directory = N'{_lit(str(directory))}', "
        "@BackupType = 'FULL', "
        "@Compress = 'Y', "
        "@CheckSum = 'Y', "
        "@Verify = 'Y', "
        "@CleanupTime = 1, "
        "@CleanupMode = 'AFTER_BACKUP'"
    )


def prepare_maintenance_solution(text: str, backup_directory: str) -> str:
    """Patch the vendored Ola script: no SQL Agent jobs, fixed backup directory.

    The file is vendored pristine so it stays diffable against upstream; the two
    edits happen here, where they are testable. Raises if either marker is
    absent, which is how an upstream reformat surfaces instead of silently
    creating SQL Agent jobs that cannot run in the container.
    """
    # The real MaintenanceSolution.sql declares these as
    #   DECLARE @CreateJobs nvarchar(max)      = 'Y'   -- trailing comment
    # not "SET @X = ...". The prefix is captured so the substitution replaces
    # only the value and leaves the alignment and trailing comment intact.
    # The quote-aware N'(?:[^']|'')*' keeps re-application idempotent, and
    # requiring the "= <value>" suffix skips the second, valueless
    # `DECLARE @BackupDirectory nvarchar(max)` inside a procedure body.
    jobs = re.compile(r"(DECLARE @CreateJobs nvarchar\(max\)\s*=\s*)'[YN]'")
    directory = re.compile(
        r"(DECLARE @BackupDirectory nvarchar\(max\)\s*=\s*)(?:NULL|N'(?:[^']|'')*')"
    )

    if not jobs.search(text):
        raise SqlIdentifierError(
            "vendored MaintenanceSolution.sql has no "
            "'DECLARE @CreateJobs nvarchar(max) = ...' line; "
            "re-vendor and re-check the patch markers"
        )
    if not directory.search(text):
        raise SqlIdentifierError(
            "vendored MaintenanceSolution.sql has no "
            "'DECLARE @BackupDirectory nvarchar(max) = ...' line; "
            "re-vendor and re-check the patch markers"
        )

    # Lambdas rather than template strings: a replacement containing a
    # backslash would otherwise be interpreted as a group reference.
    text = jobs.sub(lambda m: m.group(1) + "'N'", text, count=1)
    return directory.sub(
        lambda m: m.group(1) + f"N'{_lit(str(backup_directory))}'", text, count=1
    )


def set_max_server_memory(mb: int) -> str:
    """Cap the buffer pool. Dynamic -- no restart, unlike memory.memorylimitmb."""
    if isinstance(mb, bool) or not isinstance(mb, int):
        raise ValueError(f"max server memory must be an int, got {mb!r}")
    if mb < 1:
        raise ValueError(f"max server memory must be >= 1, got {mb}")
    return (
        "EXEC sp_configure 'show advanced options', 1;\n"
        "RECONFIGURE;\n"
        f"EXEC sp_configure 'max server memory (MB)', {mb};\n"
        "RECONFIGURE;"
    )


def reset_year(schema: str, data_type: str, year: int) -> str:
    """Empty one year table and forget its manifest rows, so every month of it
    reloads as a clean append."""
    sch = validate_identifier(schema, "schema")
    dt = validate_identifier(data_type, "trip type")
    if isinstance(year, bool) or not isinstance(year, int):
        raise ValueError(f"year must be an int, got {year!r}")
    table = f"{sch}.{dt}_{year}"
    return (
        f"IF OBJECT_ID('{_lit(table)}', 'U') IS NOT NULL\n"
        f"    TRUNCATE TABLE {table};\n"
        f"IF OBJECT_ID('{_lit(sch)}._load_manifest', 'U') IS NOT NULL\n"
        f"    DELETE FROM {sch}._load_manifest\n"
        f"    WHERE data_type = '{_lit(dt)}' AND year = {year};"
    )
