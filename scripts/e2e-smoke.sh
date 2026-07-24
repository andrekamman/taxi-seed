#!/usr/bin/env bash
# Run the SQL Server integration + pipeline e2e tests locally, mirroring CI.
# Brings up a disposable SQL Server container, waits for readiness, runs the
# tests, and tears the container down.
set -euo pipefail

PASSWORD="${MSSQL_PASSWORD:-Str0ng_Passw0rd!}"
CONTAINER="${MSSQL_CONTAINER:-taxi-mssql-e2e}"

cleanup() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=${PASSWORD}" \
  -p 1433:1433 -d --name "$CONTAINER" \
  mcr.microsoft.com/mssql/server:2022-latest >/dev/null

MSSQL_PASSWORD="$PASSWORD" MSSQL_HOST=127.0.0.1 MSSQL_PORT=1433 MSSQL_USER=sa \
  uv run python scripts/wait_for_mssql.py

MSSQL_PASSWORD="$PASSWORD" MSSQL_HOST=127.0.0.1 MSSQL_PORT=1433 MSSQL_USER=sa \
  uv run --extra test pytest \
    tests/taxi_loader/test_load_integration.py \
    tests/e2e/test_pipeline_e2e.py -v
