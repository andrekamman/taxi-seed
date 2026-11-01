# seeder

A Docker Compose stack (`taxi-mssql`) that advances one host data directory toward a
complete SQL Server data set: raw parquet, normalized parquet, a presized mdf and ldf,
page-compressed year tables, and a compressed, verified backup. Re-running continues from
wherever the last run stopped — every stage reads the directory to decide whether it has
work to do.

Two settings are required, in `mssql/.env` (gitignored):

```bash
cat > .env <<'ENV'
TAXI_DATA_DIR=/srv/taxi
MSSQL_PASSWORD=change-me-Str0ng!
ENV
docker compose up --build --abort-on-container-exit --exit-code-from seeder
```

Keep both flags: they stop the whole stack when the seeder exits and make `$?` the
seeder's own exit code (`0` clean, `1` needs review, `2` operational failure).

The Compose **project** is `taxi-mssql`; the service that does the work is `seeder`,
because the stack already has an `mssql` service — the SQL Server itself.

→ **[Full guide](https://andrekamman.github.io/taxi-seed/guides/mssql/)**
