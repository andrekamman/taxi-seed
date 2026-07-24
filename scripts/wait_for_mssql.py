"""Block until the SQL Server used by the integration/e2e tests accepts a
connection, then ensure the target database exists. Reuses the loader's real
connection path (so it also validates the pinned mssql extension version).

Usage: MSSQL_PASSWORD=... uv run python scripts/wait_for_mssql.py
Env: MSSQL_HOST (default 127.0.0.1), MSSQL_PORT (1433), MSSQL_USER (sa).
"""
import os
import sys
import time

from taxi_loader.connection import ConnConfig, connect_duckdb, ensure_database

ATTEMPTS = 40
DELAY_S = 3


def main() -> int:
    password = os.environ.get("MSSQL_PASSWORD")
    if not password:
        print("MSSQL_PASSWORD not set", file=sys.stderr)
        return 2
    cfg = ConnConfig(
        host=os.environ.get("MSSQL_HOST", "127.0.0.1"),
        port=int(os.environ.get("MSSQL_PORT", "1433")),
        database="taxi",
        schema="dbo",
        user=os.environ.get("MSSQL_USER", "sa"),
        password=password,
    )
    for attempt in range(1, ATTEMPTS + 1):
        try:
            conn = connect_duckdb()
            ensure_database(conn, cfg)
            conn.close()
            print(f"SQL Server ready (attempt {attempt}); database 'taxi' ensured")
            return 0
        except Exception as exc:  # noqa: BLE001 - report and retry
            print(f"waiting for SQL Server ({attempt}/{ATTEMPTS}): {exc}")
            time.sleep(DELAY_S)
    print("SQL Server did not become ready", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
