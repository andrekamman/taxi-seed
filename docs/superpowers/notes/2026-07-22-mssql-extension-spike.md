# mssql extension spike — findings

**Date:** 2026-07-22
**Task:** Task 1 of the SQL loader plan (`docs/superpowers/plans/2026-07-22-sql-loader.md`)
**Environment:** macOS ARM64, DuckDB 1.4.4 (repo pin), `INSTALL mssql FROM community`.

## Confirmed WITHOUT a SQL Server (community repo + local DuckDB)

- `INSTALL mssql FROM community; LOAD mssql;` succeeds on DuckDB 1.4.4 — no external system dependency. ✅
- **Installed extension version reports as `7e57d24`** — a git short-SHA, **not** a semver.
  The DuckDB community-extension registry versions this extension by commit hash. The spec's
  nominated pin `v0.2.1` is therefore not a value `INSTALL … FROM community` can return.
  → `EXPECTED_MSSQL_EXT_VERSION = "7e57d24"` (see `loader/src/taxi_loader/connection.py`).
  Read via `SELECT extension_version FROM duckdb_extensions() WHERE extension_name='mssql'`.
- Functions present (`SELECT function_name FROM duckdb_functions() WHERE function_name LIKE 'mssql%'`):
  `mssql_azure_auth_test, mssql_close, mssql_exec, mssql_open, mssql_ping, mssql_pool_stats,
  mssql_preload_catalog, mssql_refresh_cache, mssql_scan, mssql_version`.
  The plan uses `mssql_exec` (scalar; DDL/DML, returns affected rows) and `mssql_scan(catalog, query)`
  (table; reads). Both present. ✅ No `mssql_query` — the plan correctly uses `mssql_scan`.

### Version-pin caveat (surface to maintainer)

A commit-hash pin is brittle: whenever the community CI rebuilds the extension for DuckDB 1.4.x,
the hash changes and the startup assertion (exit 2) will fire until `EXPECTED_MSSQL_EXT_VERSION`
is bumped. This is exactly the spec's intent ("a version bump is a deliberate, tested change"),
but the practical cadence of hash churn is higher than a semver would be. Options if it proves
noisy: (a) relax the assertion to a warning, or (b) pin the DuckDB extension repo/version. Left as
spec'd (hard assert) for v1.

## Pending — requires a live SQL Server (Docker daemon was not running at spike time)

Validated instead by the env-gated integration suite (`tests/taxi_loader/test_load_integration.py`,
Tasks 6 & 8), which is the real gate. SQL spellings below are grounded in the extension's official
README (fetched during planning) and are exercised end-to-end once a server is available:

- `ATTACH 'Server=host,1433;Database=db;User Id=sa;Password=…;Encrypt=yes;TrustServerCertificate=yes' AS mssql (TYPE mssql)`
- Provisioning via `SELECT mssql_exec('<master-attach>', 'IF DB_ID(''taxi'') IS NULL EXEC(''CREATE DATABASE [taxi]'')')`
- `SELECT mssql_exec('mssql', '<CREATE TABLE …>')` (confirm whether DDL must be `EXEC('…')`-wrapped)
- Append: `COPY (SELECT * FROM read_parquet([...])) TO 'mssql://mssql/dbo/<table>' (FORMAT 'bcp', CREATE_TABLE false, TABLOCK true, FLUSH_ROWS 100000)`
- Reload: `DROP TABLE IF EXISTS <fq>` → explicit `CREATE TABLE` → append-style `COPY` (no `REPLACE`)
- Reads: `SELECT … FROM mssql_scan('mssql', '<query>')`

**To finish the live spike / run integration:** start Docker, then
`docker run -d --name mssql-it -e ACCEPT_EULA=Y -e MSSQL_SA_PASSWORD='Str0ng_Passw0rd!' -p 1433:1433 mcr.microsoft.com/mssql/server:2022-latest`
and `MSSQL_PASSWORD='Str0ng_Passw0rd!' uv run --extra test pytest tests/taxi_loader/test_load_integration.py -v`.
