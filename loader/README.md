# loader

Bulk-loads normalized TLC parquet (`raw-normalized/<type>/<year>/`) into SQL Server —
one table per year per type — entirely through DuckDB and the `mssql` community
extension. Idempotent: a month not loaded is appended; a complete year is skipped;
a changed or incomplete year is truncated and reloaded.

→ **[Full guide](https://andrekamman.github.io/taxi/guides/loader/)**
