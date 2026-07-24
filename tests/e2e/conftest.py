import os
from contextlib import contextmanager
from uuid import uuid4

import pytest
from taxi_loader.connection import ATTACH_NAME, ConnConfig, attach_target, connect_duckdb


@pytest.fixture
def cfg():
    return ConnConfig(
        host=os.environ.get("MSSQL_HOST", "localhost"),
        port=int(os.environ.get("MSSQL_PORT", "1433")),
        database="taxi",
        schema="t" + uuid4().hex[:8],
        user=os.environ.get("MSSQL_USER", "sa"),
        password=os.environ["MSSQL_PASSWORD"],
    )


@contextmanager
def attached(conn_cfg):
    """Short-lived read connection; respects the process-global mssql attach."""
    conn = connect_duckdb()
    try:
        attach_target(conn, conn_cfg, create_schema=False)
        yield conn
    finally:
        try:
            conn.execute(f"DETACH {ATTACH_NAME}")
        except Exception:
            pass
        conn.close()
