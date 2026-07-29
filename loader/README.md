# loader

Bulk-loads normalized TLC parquet (`raw-normalized/<type>/<year>/`) into SQL Server —
one table per year per type — entirely through DuckDB and the `mssql` community
extension. Idempotent: a month not loaded is appended; a complete year is skipped;
a changed or incomplete year is truncated and reloaded.

Run it as a console script or a module:

```bash
taxi-load                      # all four types, MSSQL_PASSWORD must be set
taxi-load yellow --dry-run     # print the reconciliation plan, write nothing
python -m taxi_loader.cli yellow --full-refresh
```

→ **[Full guide](https://andrekamman.github.io/taxi-seed/guides/loader/)**
