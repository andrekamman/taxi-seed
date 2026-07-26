"""Entry point for the `normalize` command.

Single-command flow:
- `normalize <type>` — first run generates the mapping scaffold and exits 3;
  subsequent runs with unresolved items amend the scaffold and exit 1;
  a complete mapping runs the normalization and exits 0.
- `normalize` (no arg) — runs each of the four data types in turn.
- `--sample <N|N%>` — passed to analysis; only takes effect when scaffold
  generation or amendment runs. Default 100% (full scan).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

from taxi_normalize.bootstrap import BootstrapResult, bootstrap_type
from taxi_normalize.data_check import get_file_metadata
from taxi_normalize.executor import execute_transform
from taxi_normalize.mapping import MappingError, load_mapping
from taxi_normalize.planner import plan_file


DATA_TYPES = ("yellow", "green", "fhv", "fhvhv")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="normalize",
        description="Rewrite historical TLC parquet files to conform to the latest schema.",
    )
    parser.add_argument(
        "data_type", nargs="?",
        help="Data type to normalize (yellow/green/fhv/fhvhv). Omit to run all four.",
    )
    parser.add_argument(
        "--sample", default="100%",
        help="Rows to sample for rename verification during first-run analysis "
             "and amendment: N (absolute) or N%% (percent). Default: 100%% (full scan). "
             "Ignored when the mapping is already complete.",
    )
    parser.add_argument(
        "--data-dir", default=".",
        help="Base dir for data: reads <data-dir>/raw/<type>, writes "
             "<data-dir>/raw-normalized/<type>. Default: current directory.",
    )
    args = parser.parse_args(argv)

    types = [args.data_type] if args.data_type else list(DATA_TYPES)
    overall_rc = 0
    for data_type in types:
        rc = _normalize_one(data_type, args.sample, args.data_dir)
        if rc != 0 and rc > overall_rc:
            overall_rc = rc
    return overall_rc


def _normalize_one(data_type: str, sample: str, data_dir: str) -> int:
    raw_dir = Path(data_dir) / "raw" / data_type
    mapping_path = Path("normalize") / "mappings" / f"{data_type}.yaml"
    out_dir = Path(data_dir) / "raw-normalized" / data_type

    if not raw_dir.exists():
        print(f"{data_type}: no raw files at {raw_dir}, skipping")
        return 0

    # First run: no mapping exists — bootstrap the scaffold and stop for review.
    if not mapping_path.exists():
        try:
            result = bootstrap_type(data_type, raw_dir, mapping_path, sample=sample)
        except (FileNotFoundError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        _print_first_run(data_type, mapping_path, result)
        return 3

    # Mapping exists — load and validate it.
    try:
        mapping = load_mapping(mapping_path)
    except MappingError as e:
        print(f"error: mapping {mapping_path}: {e}", file=sys.stderr)
        return 2

    # Resolve the target file
    target_matches = list(raw_dir.rglob(mapping.target))
    if not target_matches:
        print(
            f"error: target file {mapping.target} (pinned in {mapping_path}) "
            f"not found under {raw_dir}",
            file=sys.stderr,
        )
        return 2
    target_path = target_matches[0]

    conn = duckdb.connect(":memory:")
    target_md = get_file_metadata(conn, target_path)

    # Plan all files to collect unresolved items in one report.
    plans = []
    unresolved_by_col: dict[str, str] = {}
    for f in sorted(raw_dir.rglob("*.parquet")):
        raw_md = get_file_metadata(conn, f)
        plan = plan_file(raw_md, target_md, mapping)
        plans.append((f, plan))
        for u in plan.unresolved:
            unresolved_by_col.setdefault(u.column, u.kind + ": " + u.details)

    if unresolved_by_col:
        # Amend the mapping with SUGGESTED/TODO entries for anything the human
        # hasn't already addressed, then print the consolidated error report.
        try:
            amend_result = bootstrap_type(data_type, raw_dir, mapping_path, sample=sample)
        except (FileNotFoundError, ValueError) as e:
            # Analysis failure is separate from the unresolved-mapping issue;
            # surface it but still report the unresolved items.
            print(f"warning: could not amend mapping: {e}", file=sys.stderr)
            amend_result = None
        _print_unresolved(data_type, mapping_path, unresolved_by_col, amend_result)
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


def _print_first_run(data_type: str, mapping_path: Path, result: BootstrapResult) -> None:
    print(f"{data_type}: no mapping found — analyzed raw data and wrote {mapping_path}.")
    if result.timeline:
        print(f"  Detected {len(result.timeline)} drift transition(s) — see file header.")
    print(f"  {result.new_items} SUGGESTED/TODO item(s) need your review.")
    print("")
    print("Next steps:")
    print(f"  1. Review {mapping_path}")
    print("  2. Uncomment SUGGESTED entries you accept, delete the rest")
    print("  3. Fill in `ack_date:` for each TODO to acknowledge lossy casts or data loss")
    print(f"  4. Re-run: uv run normalize {data_type}")


def _print_unresolved(
    data_type: str,
    mapping_path: Path,
    unresolved_by_col: dict[str, str],
    amend_result,
) -> None:
    print(
        f"\nERROR: {data_type} - {len(unresolved_by_col)} unresolved item(s) in {mapping_path}",
        file=sys.stderr,
    )
    print("  Cannot normalize this data type until these are handled.\n", file=sys.stderr)
    for col, details in sorted(unresolved_by_col.items()):
        print(f"  - {col}: {details}", file=sys.stderr)
    if amend_result is not None and amend_result.new_items > 0:
        print(
            f"\n  I've amended {mapping_path} with {amend_result.new_items} new "
            f"SUGGESTED/TODO item(s) for the unresolved columns.",
            file=sys.stderr,
        )
        print(
            "  Review the additions, then edit the mapping and re-run.",
            file=sys.stderr,
        )
    else:
        print(
            "\n  Options: add to `renames:`, `lossy_casts:` (with ack_date), "
            "or `acknowledged_data_loss:` (with ack_date).",
            file=sys.stderr,
        )
    print(f"  Nothing was written for {data_type}.\n", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
