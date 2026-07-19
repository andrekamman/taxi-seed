import argparse
import sys
from pathlib import Path

import duckdb

from schema_drift.analyze import analyze_data_type
from schema_drift.report import generate_report


def main():
    parser = argparse.ArgumentParser(
        description="Analyze schema drift in NYC TLC taxi Parquet files"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("raw"),
        help="Directory containing the taxi data (default: raw)",
    )
    parser.add_argument(
        "--types",
        nargs="+",
        default=["yellow", "green", "fhv", "fhvhv"],
        help="Data types to analyze (default: all)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Output file for the report (default: stdout)",
    )
    parser.add_argument(
        "--verify-data",
        action="store_true",
        help="Verify rename candidates by sampling actual data (slower but more accurate)",
    )
    parser.add_argument(
        "--generic",
        action="store_true",
        help="Use generic mode: detect renames by data similarity only, without domain knowledge. "
             "Suggestions require human review.",
    )

    args = parser.parse_args()

    if args.generic and args.verify_data:
        print("Note: --generic mode already uses data verification, --verify-data is ignored.", file=sys.stderr)

    if not args.data_dir.exists():
        print(f"Error: Data directory '{args.data_dir}' does not exist.", file=sys.stderr)
        sys.exit(1)

    # Create a DuckDB connection (in-memory)
    conn = duckdb.connect(":memory:")

    results = []
    for data_type in args.types:
        print(f"Analyzing {data_type} data...")
        result = analyze_data_type(
            conn,
            args.data_dir,
            data_type,
            verify_data=args.verify_data,
            generic_mode=args.generic,
        )
        results.append(result)

    print("")

    # Generate report
    report = generate_report(results)

    if args.output:
        args.output.write_text(report)
        print(f"Report written to: {args.output}")
    else:
        print(report)

    conn.close()


if __name__ == "__main__":
    main()
