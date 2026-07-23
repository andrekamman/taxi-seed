"""Entry point for the `taxi-load` command.

taxi-load [TYPE]  — bulk-load raw-normalized/<type>/<year>/*.parquet into SQL
Server, one table per year per type, idempotently. TYPE omitted = all four.
Password comes from MSSQL_PASSWORD only.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import duckdb

from taxi_loader import load, manifest
from taxi_loader.connection import (
    ATTACH_NAME, ConnConfig, LoaderConfigError, LoaderConnectionError, LoaderError,
    attach_target, connect_duckdb, ensure_database, validate_identifier,
)
from taxi_loader.reconcile import APPEND, RELOAD, SKIP, MonthFile, reconcile
from taxi_shared.type_mapping import TypeMappingError

DATA_TYPES = ("yellow", "green", "fhv", "fhvhv")
_MONTH_RE = re.compile(r"(\d{4})-(\d{2})")


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="taxi-load",
        description="Bulk-load normalized TLC parquet into SQL Server.",
    )
    p.add_argument("data_type", nargs="?", choices=DATA_TYPES,
                   help="yellow/green/fhv/fhvhv. Omit to load all four.")
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=1433)
    p.add_argument("--database", default="taxi")
    p.add_argument("--schema", default="dbo")
    p.add_argument("--user", default="sa")
    p.add_argument("--input-dir", default="raw-normalized",
                   help="reads <input-dir>/<type>/<year>/*.parquet")
    p.add_argument("--flush-rows", type=int, default=100000,
                   help="BCP commit batch size")
    p.add_argument("--full-refresh", action="store_true",
                   help="force truncate+reload of every year")
    p.add_argument("--dry-run", action="store_true",
                   help="print the reconciliation plan and exit without writing")
    return p.parse_args(argv)


def discover_month_files(conn: duckdb.DuckDBPyConnection, input_dir,
                         data_type: str) -> list[MonthFile]:
    base = Path(input_dir) / data_type
    months: list[MonthFile] = []
    if not base.exists():
        return months
    for f in sorted(base.rglob("*.parquet")):
        m = _MONTH_RE.search(f.name)
        if not m:
            continue
        year, month = int(m.group(1)), int(m.group(2))
        months.append(MonthFile(year, month, str(f),
                                load.parquet_row_count(conn, f)))
    return months


def _describe_plan(data_type: str, plans) -> None:
    for plan in plans:
        if plan.action == SKIP:
            print(f"  {data_type} {plan.year}: skip")
        elif plan.action == APPEND:
            mm = ", ".join(f"{m.month:02d}" for m in plan.months)
            print(f"  {data_type} {plan.year}: append month(s) {mm}")
        else:
            print(f"  {data_type} {plan.year}: truncate + reload "
                  f"({len(plan.months)} month file(s))")


def _process_type(conn, cfg, data_type: str, input_dir: str,
                  flush_rows: int, full_refresh: bool, dry_run: bool,
                  attached: bool) -> int:
    disk = discover_month_files(conn, input_dir, data_type)
    if not disk:
        print(f"{data_type}: no parquet under {input_dir}/{data_type}, skipping")
        return 0

    if dry_run and not attached:
        # Target database doesn't exist yet -> nothing on the server, so
        # every year is fresh; no manifest, no table counts.
        manifest_rows = []
    elif dry_run:
        manifest_rows = (
            manifest.read_manifest(conn, cfg, data_type)
            if manifest.manifest_table_exists(conn, cfg) else []
        )
    else:
        manifest_rows = manifest.read_manifest(conn, cfg, data_type)

    if dry_run and not attached:
        table_counts = {}
    else:
        years = sorted({m.year for m in disk} | {r.year for r in manifest_rows})
        table_counts = {
            y: load.count_year_table(conn, cfg, load.year_table(data_type, y))
            for y in years
        }
    plans = reconcile(disk, manifest_rows, table_counts, full_refresh)

    if dry_run:
        print(f"{data_type}: plan")
        _describe_plan(data_type, plans)
        return 0

    total = 0
    for plan in plans:
        total += load.execute_year_plan(conn, cfg, data_type, plan,
                                        flush_rows=flush_rows)
    n_reload = sum(1 for p in plans if p.action == RELOAD)
    n_append = sum(1 for p in plans if p.action == APPEND)
    print(f"{data_type}: {total} row(s) loaded "
          f"({n_append} append, {n_reload} reload year(s)).")
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)

    try:
        schema = validate_identifier(args.schema, "schema")
        database = validate_identifier(args.database, "database")
    except LoaderConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    password = os.environ.get("MSSQL_PASSWORD")
    if not password:
        print("error: MSSQL_PASSWORD environment variable is required",
              file=sys.stderr)
        return 2

    types = [args.data_type] if args.data_type else list(DATA_TYPES)

    cfg = ConnConfig(host=args.host, port=args.port, database=database,
                     schema=schema, user=args.user, password=password)

    # Connection / provisioning failures are exit 2 (nothing loaded).
    try:
        conn = connect_duckdb()
        if args.dry_run:
            # Read-only: attach without provisioning; tolerate an absent database.
            try:
                attach_target(conn, cfg, create_schema=False)
                attached = True
            except LoaderConnectionError:
                attached = False   # DB doesn't exist yet -> every year is fresh
        else:
            ensure_database(conn, cfg)
            attach_target(conn, cfg)
            manifest.ensure_manifest_table(conn, cfg)
            attached = True
    except LoaderError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    overall = 0
    try:
        for data_type in types:
            try:
                _process_type(conn, cfg, data_type, args.input_dir,
                              args.flush_rows, args.full_refresh, args.dry_run,
                              attached)
            except TypeMappingError as e:
                print(f"error: {data_type}: {e}", file=sys.stderr)
                overall = max(overall, 2)
            except (duckdb.Error, LoaderError) as e:
                print(f"error: {data_type} failed mid-load: {e}", file=sys.stderr)
                overall = max(overall, 1)
    finally:
        # Release the process-global mssql attach context; the extension keeps
        # it alive across DuckDB connections, so a leaked attach would collide
        # with the next run in the same process.
        if attached:
            try:
                conn.execute(f"DETACH {ATTACH_NAME}")
            except duckdb.Error:
                pass
        conn.close()
    return overall


if __name__ == "__main__":
    sys.exit(main())
