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
    p.add_argument("--input-dir", default=None,
                   help="reads <input-dir>/<type>/<year>/*.parquet "
                        "(overrides --data-dir; default: raw-normalized)")
    p.add_argument("--data-dir", default=None,
                   help="base dir; reads <data-dir>/raw-normalized (unless --input-dir given)")
    p.add_argument("--flush-rows", type=int, default=100000,
                   help="BCP commit batch size")
    p.add_argument("--full-refresh", action="store_true",
                   help="force truncate+reload of every year")
    p.add_argument("--dry-run", action="store_true",
                   help="print the reconciliation plan and exit without writing")
    p.add_argument("--year", type=int, default=None,
                   help="load only this year; omit for every year on disk")
    p.add_argument("--month", type=int, default=None, choices=range(1, 13),
                   metavar="{1..12}",
                   help="load only this month of --year. Each month is a "
                        "separate parquet file, so separate processes may load "
                        "different months of one year concurrently -- TABLOCK "
                        "takes a BU lock, and BU locks are compatible.")
    args = p.parse_args(argv)
    if args.month is not None and args.year is None:
        # A bare --month would mean "this month of every year", which is not a
        # unit anything schedules, and would silently widen a worker's scope.
        p.error("--month requires --year")
    return args


def resolve_input_dir(input_dir, data_dir) -> str:
    if input_dir is not None:
        return input_dir
    if data_dir is not None:
        return str(Path(data_dir) / "raw-normalized")
    return "raw-normalized"


def discover_month_files(conn: duckdb.DuckDBPyConnection, input_dir,
                         data_type: str, year: int | None = None,
                         month: int | None = None) -> list[MonthFile]:
    base = Path(input_dir) / data_type
    months: list[MonthFile] = []
    if not base.exists():
        return months
    for f in sorted(base.rglob("*.parquet")):
        m = _MONTH_RE.search(f.name)
        if not m:
            continue
        file_year, file_month = int(m.group(1)), int(m.group(2))
        # Filter here, not after reconcile: reconcile derives its year set and
        # its per-year count_year_table calls from this list, and a worker that
        # owns one month must not count or plan the other 583.
        if year is not None and file_year != year:
            continue
        if month is not None and file_month != month:
            continue
        months.append(MonthFile(file_year, file_month, str(f),
                                load.parquet_row_count(conn, f)))
    return months


def guard_month_mode(plans, month: int | None) -> None:
    """In --month mode a RELOAD plan is fatal, not something to execute.

    RELOAD drops and recreates the year table. With several workers loading
    different months of that same year, one of them doing that would destroy the
    others' work mid-flight. A year that needs rebuilding must be reset by the
    caller before any month of it is dispatched.
    """
    if month is None:
        return None
    bad = [p.year for p in plans if p.action == RELOAD]
    if bad:
        raise LoaderError(
            f"year(s) {bad} fail the integrity check (committed rows disagree "
            f"with the manifest) and need a truncate+reload, which --month mode "
            f"will not do while sibling months may be loading. Reset the year "
            f"first, then re-dispatch its months."
        )
    return None


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
                  attached: bool, year: int | None = None,
                  month: int | None = None) -> int:
    disk = discover_month_files(conn, input_dir, data_type, year=year, month=month)
    if not disk:
        scope = f" {year}" if year is not None else ""
        scope += f"-{month:02d}" if month is not None else ""
        print(f"{data_type}{scope}: no parquet under {input_dir}/{data_type}, "
              f"skipping")
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

    # Scope the manifest the same way as the disk walk, so reconcile compares
    # like with like. Unscoped, a worker owning one month would see every other
    # month's manifest rows as "on the manifest but missing from disk" and plan
    # a whole-year RELOAD.
    if year is not None:
        manifest_rows = [r for r in manifest_rows if r.year == year]
    if month is not None:
        manifest_rows = [r for r in manifest_rows if r.month == month]

    if dry_run and not attached:
        table_counts = {}
    else:
        years = sorted({m.year for m in disk} | {r.year for r in manifest_rows})
        table_counts = {
            y: load.count_year_table(conn, cfg, load.year_table(data_type, y))
            for y in years
        }
    plans = reconcile(disk, manifest_rows, table_counts, full_refresh)
    guard_month_mode(plans, month)

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
    input_dir = resolve_input_dir(args.input_dir, args.data_dir)

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
                _process_type(conn, cfg, data_type, input_dir,
                              args.flush_rows, args.full_refresh, args.dry_run,
                              attached, year=args.year, month=args.month)
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
