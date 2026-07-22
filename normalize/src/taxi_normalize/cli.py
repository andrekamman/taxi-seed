"""Entry point for the `normalize` command."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

from taxi_normalize.bootstrap import bootstrap_type
from taxi_normalize.data_check import get_file_metadata
from taxi_normalize.executor import execute_transform
from taxi_normalize.mapping import MappingError, load_mapping
from taxi_normalize.planner import plan_file


DATA_TYPES = ("yellow", "green", "fhv", "fhvhv")


def main() -> int:
    # argv-prefix routing: `bootstrap` is a subcommand; everything else is normalize mode.
    argv = sys.argv[1:]
    if argv and argv[0] == "bootstrap":
        parser = argparse.ArgumentParser(prog="normalize bootstrap")
        parser.add_argument("data_type", help=f"One of: {', '.join(DATA_TYPES)}")
        parser.add_argument(
            "--sample", default="100%",
            help="Rows to sample for rename verification: N (absolute) or N%% (percent). Default: 100%%.",
        )
        args = parser.parse_args(argv[1:])
        return _cmd_bootstrap(args.data_type, args.sample)

    parser = argparse.ArgumentParser(
        prog="normalize",
        description="Rewrite historical TLC parquet files to conform to the latest schema.",
    )
    parser.add_argument(
        "data_type", nargs="?",
        help="Data type to normalize (yellow/green/fhv/fhvhv). Omit to run all four.",
    )
    args = parser.parse_args(argv)
    types = [args.data_type] if args.data_type else list(DATA_TYPES)
    return _cmd_normalize(types)


def _cmd_bootstrap(data_type: str, sample: str) -> int:
    raw_dir = Path("raw") / data_type
    if not raw_dir.exists():
        print(f"error: {raw_dir} does not exist. Run the downloader first.", file=sys.stderr)
        return 2
    output_yaml = Path("normalize") / "mappings" / f"{data_type}.yaml"
    try:
        bootstrap_type(data_type, raw_dir, output_yaml, sample=sample)
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"Wrote {output_yaml}. Review the SUGGESTED entries and fill in TODOs before running normalize.")
    return 0


def _cmd_normalize(types: list[str]) -> int:
    overall_rc = 0
    for data_type in types:
        rc = _normalize_one(data_type)
        if rc != 0:
            overall_rc = rc
    return overall_rc


def _normalize_one(data_type: str) -> int:
    raw_dir = Path("raw") / data_type
    mapping_path = Path("normalize") / "mappings" / f"{data_type}.yaml"
    out_dir = Path("raw-normalized") / data_type

    if not mapping_path.exists():
        print(
            f"error: mapping file {mapping_path} not found. "
            f"Run `normalize bootstrap {data_type}` first.",
            file=sys.stderr,
        )
        return 2

    try:
        mapping = load_mapping(mapping_path)
    except MappingError as e:
        print(f"error: mapping {mapping_path}: {e}", file=sys.stderr)
        return 2

    if not raw_dir.exists():
        print(f"{data_type}: no raw files at {raw_dir}, skipping", file=sys.stdout)
        return 0

    raw_files = sorted(raw_dir.rglob("*.parquet"))
    target_file = raw_dir.rglob(mapping.target)
    target_path = next(iter(target_file), None)
    if target_path is None:
        print(
            f"error: target file {mapping.target} not found under {raw_dir}",
            file=sys.stderr,
        )
        return 2

    conn = duckdb.connect(":memory:")
    target_md = get_file_metadata(conn, target_path)

    # Plan all files to collect unresolved items in one report.
    plans = []
    unresolved_by_col: dict[str, str] = {}
    for f in raw_files:
        raw_md = get_file_metadata(conn, f)
        plan = plan_file(raw_md, target_md, mapping)
        plans.append((f, plan))
        for u in plan.unresolved:
            unresolved_by_col.setdefault(u.column, u.kind + ": " + u.details)

    if unresolved_by_col:
        print(f"\nERROR: {data_type} - {len(unresolved_by_col)} unresolved item(s) in {mapping_path}", file=sys.stderr)
        print(f"  Cannot normalize this data type until these are handled.\n", file=sys.stderr)
        for col, details in sorted(unresolved_by_col.items()):
            print(f"  - {col}: {details}", file=sys.stderr)
        print("\n  Options: add to `renames:`, `lossy_casts:` (with ack_date), or `acknowledged_data_loss:` (with ack_date).", file=sys.stderr)
        print(f"  Nothing was written for {data_type}.\n", file=sys.stderr)
        return 1

    written = 0
    skipped = 0
    for f, plan in plans:
        rel = f.relative_to(raw_dir)
        out_path = out_dir / rel
        if out_path.exists():
            skipped += 1
            continue
        execute_transform(conn, plan, f, out_path)
        written += 1

    print(f"{data_type}: {written} file(s) normalized, {skipped} skipped (already present).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
