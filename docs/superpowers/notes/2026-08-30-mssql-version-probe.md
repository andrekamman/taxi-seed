# `mssql_version()` across every reachable extension build — probe results

**Date:** 2026-08-30 (all figures measured on this date; see the drift warning below)
**Ticket:** [#15](https://github.com/andrekamman/taxi-seed/issues/15), under map [#14](https://github.com/andrekamman/taxi-seed/issues/14)
**Environment:** Linux x86_64 (`PRAGMA platform` → `linux_amd64`), one throwaway `uv venv` per DuckDB
version with an isolated `extension_directory`, so every build was fetched fresh from
`community-extensions.duckdb.org` on the probe date. The repo's `.venv`, `pyproject.toml` and
`uv.lock` were not touched.

!!! warning "Read this first — the conclusions below are superseded"

    The **measurements** in this note are a valid record of what the registry served on
    2026-08-30. Its **recommendations** are not: the decisions they fed into went a
    different way, and the registry has moved again since. Specifically, as of 2026-10-31:

    - **The hash assertion this note set out to replace no longer exists.**
      `EXPECTED_MSSQL_EXT_VERSION` and `loader/src/taxi_loader/connection.py:20` are gone.
    - **A floor on `mssql_version()` was not adopted.** This note recommends "a floor/range,
      not equality". [#18](https://github.com/andrekamman/taxi-seed/issues/18) concluded
      something stronger: **vendor the extension**. taxi-seed mirrors the binary as a release
      asset and the loader loads it from a vendored `extension_directory`, asserting the
      build hash *and* the semver by equality — which is safe precisely because the bytes
      can no longer change underneath the pin. See `loader/src/taxi_loader/extension.py`.
    - **DuckDB 1.5.5 drifted again.** Upstream now serves **`d35b617` / `0.2.5`**
      (`Last-Modified: Wed, 09 Sep 2026 08:38:11 GMT`), the fourth distinct build recorded
      for a single DuckDB release:

        | build | `mssql_version()` | status |
        |---|---|---|
        | `39e547c` | unknown | recorded in an old pin comment; never verified, gone |
        | `37596c2` | `0.2.3` (claimed) | unverified — gone before it could be checked |
        | `e2b8c01` | `0.2.4` | probed here, and **what taxi-seed pins and mirrors** |
        | `d35b617` | `0.2.5` | current upstream, 2026-09-09 onward |

        This is the empirical case for vendoring rather than for any assertion design. It
        also means upstream is no longer a usable *fallback* for our pin: a fetch from it
        fails the recorded SHA-256, by design.

    - **`mssql_copy_flush_rows` was acted on.** This note leaves it open;
      [#21](https://github.com/andrekamman/taxi-seed/issues/21) decided to **keep the
      loader's own `FLUSH_ROWS 100000`** rather than adopt the extension's `102400`. It is
      the commit batch size and therefore the crash granularity an interrupted load leaves
      behind, which the reconcile is sized around; our own value cannot shift when the
      extension is rebuilt.
    - **"Not verified against a live SQL Server on 1.5.x" is now verified.** The loader runs
      on 1.5.5 / `e2b8c01`: `ATTACH` (via `CREATE SECRET`), `mssql_exec`, `mssql_scan` and
      `COPY … (FORMAT 'bcp')` all confirmed against a real server by
      [#21](https://github.com/andrekamman/taxi-seed/issues/21).

    What remains uniquely useful here: the **version ladder** across every reachable build,
    the **1.4.5 dead end**, and the **40-setting reference capture**, which is where the
    wrong-by-default `mssql_copy_tablock`, `mssql_exec_invalidate_cache` and
    `mssql_query_timeout` values were found.

## Why

`loader/src/taxi_loader/connection.py:20` asserts the **build hash**
(`EXPECTED_MSSQL_EXT_VERSION = "7e57d24"`). Hashes churn whenever the community CI rebuilds the
extension, so the assertion is being moved to `mssql_version()`, a semver the extension reports
itself. This note is the measurement that assertion design will rest on.

## Method

For each DuckDB version, in its own venv and its own extension directory:

```bash
uv venv --python 3.12 venv-$V
uv pip install --python venv-$V/bin/python "duckdb==$V"
```

```python
con = duckdb.connect(config={"extension_directory": "<fresh dir>"})
con.execute("INSTALL mssql FROM community")
con.execute("LOAD mssql")
con.execute("SELECT extension_version FROM duckdb_extensions() WHERE extension_name='mssql'")
con.execute("SELECT mssql_version()")
```

## Results

| DuckDB | INSTALL | build hash served 2026-08-30 | hash expected going in | `mssql_version()` |
|---|---|---|---|---|
| 1.4.4 | OK | `7e57d24` | `7e57d24` ✅ | **`0.1.17`** |
| 1.4.5 | **HTTP 404** | — (no build published) | — | — |
| 1.5.0 | OK | `15bcb19` | `15bcb19` ✅ | **`0.1.18`** |
| 1.5.1 | OK | `15bcb19` | (not previously recorded) | **`0.1.18`** |
| 1.5.2 | OK | `15bcb19` | `15bcb19` ✅ | **`0.1.18`** |
| 1.5.3 | OK | `51f4781` | `51f4781` ✅ | **`0.2.1`** |
| 1.5.4 | OK | `51f4781` | `51f4781` ✅ | **`0.2.1`** |
| 1.5.5 | OK | **`e2b8c01`** | `37596c2` ❌ **drift** | **`0.2.4`** |

`installed_from` was `community` for every successful row.

### Hash drift on 1.5.5 — the headline finding

The 1.5.5 build served today is **`e2b8c01`, not `37596c2`**, and it reports
**`0.2.4`, not the `0.2.3` the findings document claimed**. The registry object's
`Last-Modified` is `Tue, 18 Aug 2026 11:27:23 GMT` — a rebuild twelve days before this probe.
`0.2.3` is therefore **not confirmed**; it was presumably true when it was written and has since
been superseded. This is the third distinct hash recorded for DuckDB 1.5.5 (`39e547c`, `37596c2`,
now `e2b8c01`), which is the empirical case for abandoning the hash assertion.

Note that the semver moved too (`0.2.3` → `0.2.4`), so `mssql_version()` is *not* a stable
constant across rebuilds of a single DuckDB version either — it churns more slowly than the hash,
not never. Any assertion on it should be a floor/range, not equality. That design is ticket #18.

### `Last-Modified` of each registry object (probe date 2026-08-30)

| DuckDB | HTTP | `Last-Modified` |
|---|---|---|
| 1.4.4 | 200 | Sun, 15 Feb 2026 20:09:09 GMT |
| 1.4.5 | **404** | — |
| 1.5.0 | 200 | Tue, 10 Mar 2026 10:04:26 GMT |
| 1.5.1 | 200 | Mon, 23 Mar 2026 03:46:29 GMT |
| 1.5.2 | 200 | Wed, 15 Apr 2026 07:10:42 GMT |
| 1.5.3 | 200 | Tue, 09 Jun 2026 10:46:54 GMT |
| 1.5.4 | 200 | Tue, 16 Jun 2026 21:16:58 GMT |
| 1.5.5 | 200 | Tue, 18 Aug 2026 11:27:23 GMT |

Checked with `curl -sI http://community-extensions.duckdb.org/v$V/linux_amd64/mssql.duckdb_extension.gz`.

### DuckDB 1.4.5 has no `mssql` build — verbatim error

The Python package `duckdb==1.4.5` installs fine; the extension is simply not published for it:

```
duckdb.duckdb.HTTPException: HTTP Error: Failed to download extension "mssql" at URL
"http://community-extensions.duckdb.org/v1.4.5/linux_amd64/mssql.duckdb_extension.gz" (HTTP 404)

Candidate extensions: "mysql", "mysql_scanner", "sqlite", "vss", "sqlite3"
For more info, visit https://duckdb.org/docs/stable/extensions/troubleshooting?version=v1.4.5&platform=linux_amd64&extension=mssql
```

**1.4.5 is a dead end for this loader.** Any move off the 1.4.4 pin has to jump to 1.5.x.

### Version ladder, read off the table

Three distinct extension generations are reachable:

- `0.1.17` — DuckDB 1.4.x only (1.4.4 is the only 1.4 release with a build).
- `0.1.18` — DuckDB 1.5.0 / 1.5.1 / 1.5.2, one build (`15bcb19`) shared across all three.
- `0.2.1` — DuckDB 1.5.3 / 1.5.4, one build (`51f4781`) shared across both.
- `0.2.4` — DuckDB 1.5.5, build `e2b8c01` **as of 2026-08-30**.

The 0.1 → 0.2 boundary falls between DuckDB 1.5.2 and 1.5.3.

## Reference capture — DuckDB 1.5.5 / `e2b8c01` / `mssql_version()` = `0.2.4`

`SELECT DISTINCT function_name FROM duckdb_functions() WHERE function_name LIKE '%mssql%'` — 16 functions:

`mssql_azure_auth_test`, `mssql_close`, `mssql_close_all`, `mssql_exec`, `mssql_invalidate_cache`,
`mssql_kerberos_auth_test`, `mssql_kerberos_auth_test_secret`, `mssql_open`, `mssql_ping`,
`mssql_pool_stats`, `mssql_preload_catalog`, `mssql_refresh_cache`, `mssql_scan`, `mssql_version`,
`mssql_winsspi_auth_test`, `mssql_winsspi_auth_test_spn`

New since the 1.4.4 / `0.1.17` spike (2026-07-22): `mssql_close_all`, `mssql_invalidate_cache`,
`mssql_kerberos_auth_test`, `mssql_kerberos_auth_test_secret`, `mssql_winsspi_auth_test`,
`mssql_winsspi_auth_test_spn`. Nothing the loader uses was removed — `mssql_exec`, `mssql_scan`
and `mssql_version` are all still present.

`SELECT name, value, description FROM duckdb_settings() WHERE name LIKE 'mssql%' ORDER BY 1` — 40 settings:

| name | default | description |
|---|---|---|
| `mssql_acquire_timeout` | `30` | Connection acquire timeout in seconds (0 = fail immediately) |
| `mssql_attach_validation_timeout` | `0` | Timeout in seconds for the ATTACH-time credential round trip (0 = inherit mssql_connection_timeout). Spec 047 / US2. |
| `mssql_browser_timeout_seconds` | `3` | SQL Server Browser UDP query timeout in seconds for named-instance resolution (default: 3) |
| `mssql_catalog_cache_ttl` | `0` | Metadata cache TTL in seconds (0 = manual refresh only) |
| `mssql_catalog_native_types` | `true` | Report MSSQL_VARCHAR(n)/MSSQL_NVARCHAR(n) for attached string columns instead of VARCHAR (default: true) |
| `mssql_connection_cache` | `true` | Enable connection pooling and reuse |
| `mssql_connection_limit` | `64` | Maximum connections per attached mssql database |
| `mssql_connection_timeout` | `30` | TCP connection timeout in seconds |
| `mssql_convert_varchar_max` | `true` | Convert VARCHAR(MAX) to NVARCHAR(MAX) in table scans for UTF-8 compatibility (default: true) |
| `mssql_copy_flush_rows` | `102400` | Rows per bulk-load batch sent to SQL Server (default: 102400 — SQL Server's threshold for writing compressed columnstore rowgroups directly; 0 = one batch at the end, high memory) |
| `mssql_copy_parallel_writers` | `0` | Concurrent bulk-load connections a single COPY/CTAS may open (default: 0 = derive from DuckDB's thread count, capped at 8). 1 disables parallel loading. Ignored inside an explicit transaction, where the connection is pinned |
| `mssql_copy_tablock` | `auto` | TABLOCK hint for COPY/BCP: 'auto' (by target shape — heap on, anything clustered off), 'true', or 'false' |
| `mssql_ctas_drop_on_failure` | `false` | Drop table if CTAS insert phase fails (default: false, table remains for debugging) |
| `mssql_ctas_text_type` | `NVARCHAR` | Type given to an unannotated VARCHAR column by CTAS and COPY: NVARCHAR (Unicode, default) or VARCHAR (single-byte, needs a UTF-8 collation) |
| `mssql_ctas_use_bcp` | `true` | Use BCP protocol for CTAS data transfer (default: true, 2-10x faster than INSERT) |
| `mssql_default_string_length` | `0` | Length for an unannotated VARCHAR column created by CTAS/COPY (0 = MAX, the default) |
| `mssql_default_table_kind` | `HEAP` | Shape of a table created by CTAS/COPY: HEAP (default) or COLUMNSTORE |
| `mssql_dml_batch_size` | `500` | Maximum rows per UPDATE/DELETE batch (default: 500, affects parameter count) |
| `mssql_dml_max_parameters` | `2000` | Maximum parameters per UPDATE/DELETE statement (SQL Server limit ~2100) |
| `mssql_dml_use_prepared` | `true` | Use prepared statements for UPDATE/DELETE operations |
| `mssql_enable_statistics` | `true` | Enable statistics collection from SQL Server for query optimizer |
| `mssql_exec_invalidate_cache` | `false` | Invalidate the catalog cache after DDL executed via mssql_exec() (CREATE/DROP/ALTER/TRUNCATE/RENAME/EXEC) |
| `mssql_idle_timeout` | `300` | Idle connection timeout in seconds (0 = no timeout) |
| `mssql_insert_batch_size` | `1000` | Maximum rows per INSERT statement (SQL Server limit: 1000) |
| `mssql_insert_max_rows_per_statement` | `1000` | Hard cap on rows per INSERT statement (SQL Server limit: 1000) |
| `mssql_insert_max_sql_bytes` | `8388608` | Maximum SQL statement size in bytes |
| `mssql_insert_use_returning_output` | `true` | Use OUTPUT INSERTED for RETURNING clause |
| `mssql_login7_max_packet` | `0` | TEST-ONLY: max LOGIN7 TDS packet size in bytes for integrated auth (0 = default 4096) |
| `mssql_metadata_timeout` | `300` | Metadata query timeout in seconds (default: 300, 0 = no timeout). Increase for very large catalogs |
| `mssql_min_connections` | `0` | Minimum connections to maintain per context |
| `mssql_named_instance_resolution` | `true` | Enable SQL Server Browser (UDP 1434) resolution of host\instance connection strings |
| `mssql_order_pushdown` | `false` | Enable ORDER BY pushdown to SQL Server (default: false) |
| `mssql_query_timeout` | `30` | Query execution timeout in seconds (0 = no timeout, default: 30) |
| `mssql_reset_connection` | `true` | Reset session state (temp tables, SET options, open transactions) when a connection returns to the pool; false hands that state to the user |
| `mssql_statistics_cache_ttl_seconds` | `300` | Statistics cache TTL in seconds |
| `mssql_statistics_level` | `0` | Statistics detail level: 0=row count, 1=+histogram min/max, 2=+NDV |
| `mssql_statistics_use_dbcc` | `false` | Allow DBCC SHOW_STATISTICS for column statistics (requires permissions) |
| `mssql_tds_packet_size` | `16384` | TDS frame size in bytes requested at login, clamped to [512, 32767] (default: 16384) |
| `mssql_utf8_collation` | `Latin1_General_100_BIN2_UTF8` | Collation given to VARCHAR columns created by CTAS when the server supports UTF-8 (default: Latin1_General_100_BIN2_UTF8, matching Fabric; empty inherits the database default) |
| `mssql_utf8_support` | `true` | Advertise the UTF8SUPPORT feature in LOGIN7 so UTF-8 collation columns arrive as UTF-8 instead of UTF-16 (default: true) |

`mssql_copy_flush_rows` defaults to `102400`; the loader's `COPY … (FLUSH_ROWS 100000)` from the
2026-07-22 spike is now slightly below the extension's own default. Not acted on here.

## Scope and limits

- **Not verified:** behaviour against a live SQL Server on any 1.5.x build. This probe only
  installs, loads, and reads version metadata. Nothing here says the loader's SQL still works on
  `0.2.x` — the ATTACH/`mssql_exec`/`COPY … (FORMAT 'bcp')` spellings from the 2026-07-22 spike
  were confirmed only against 1.4.4 / `0.1.17`.
- **Not verified:** the earlier-recorded 1.5.5 hashes `39e547c` and `37596c2` and their reported
  `mssql_version()`. Those builds are gone from the registry; only what it serves today is
  observable. `0.2.3` is recorded as **unverified**.
- **Platform-specific:** `linux_amd64` only. The 2026-07-22 spike was macOS ARM64. Build hashes are
  per-platform artifacts; the semver should not be, but that was not checked.
- Every figure above expires the moment the community CI rebuilds. Re-run the probe before relying
  on any hash.
