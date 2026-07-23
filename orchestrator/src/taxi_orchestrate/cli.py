"""taxi-run: chain download -> normalize -> (opt-in) load per data type.

Honors each stage's exit codes: a needs-review or failure halts that type's
remaining stages; a loader conn/config error aborts the load stage for all
remaining types. Overall exit code: 0 clean, 1 needs-review, 2 operational
failure (2 > 1 > 0).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from taxi_orchestrate import pipeline, report, stages

DATA_TYPES = ("yellow", "green", "fhv", "fhvhv")
INPUT_DIR = "raw-normalized"


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="taxi-run",
        description="Run the pipeline: download -> normalize -> (opt-in) load.",
    )
    p.add_argument("data_type", nargs="?", choices=DATA_TYPES,
                   help="yellow/green/fhv/fhvhv. Omit to run all four.")
    p.add_argument("--recent", nargs="?", type=int, const=0, default=None,
                   help="downloader recent-mode: --recent [N] (N optional)")
    p.add_argument("--skip-download", action="store_true",
                   help="skip the download stage; use the existing raw/ mirror")
    p.add_argument("--download-only", action="store_true",
                   help="only mirror; skip normalize and load")
    p.add_argument("--load", action="store_true",
                   help="also load normalized parquet into SQL Server")
    p.add_argument("--sample", default=None, help="passed through to normalize")
    p.add_argument("--data-dir", default=None,
                   help="working root holding raw/ + raw-normalized/ (default: repo root)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the per-type plan and exit without running anything")
    # forwarded to taxi-load (only meaningful with --load)
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=1433)
    p.add_argument("--database", default="taxi")
    p.add_argument("--schema", default="dbo")
    p.add_argument("--user", default="sa")
    p.add_argument("--flush-rows", type=int, default=100000)
    p.add_argument("--full-refresh", action="store_true")
    return p.parse_args(argv)


def find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for d in [cur, *cur.parents]:
        if (d / "downloader" / "download_taxi_data.sh").exists() and (d / "pyproject.toml").exists():
            return d
    return cur


def _planned_stages(args) -> list[str]:
    if args.download_only:
        return [pipeline.DOWNLOAD]
    st = [] if args.skip_download else [pipeline.DOWNLOAD]
    st.append(pipeline.NORMALIZE)
    if args.load:
        st.append(pipeline.LOAD)
    return st


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.download_only and args.load:
        print("error: --download-only cannot be combined with --load", file=sys.stderr)
        return 2

    password = None
    if args.load:
        password = os.environ.get("MSSQL_PASSWORD")
        if not password:
            print("error: MSSQL_PASSWORD environment variable is required for --load",
                  file=sys.stderr)
            return 2

    root = Path(args.data_dir).resolve() if args.data_dir else find_repo_root(Path.cwd())
    types = [args.data_type] if args.data_type else list(DATA_TYPES)
    planned = _planned_stages(args)
    conn = stages.LoadConn(
        host=args.host, port=args.port, database=args.database, schema=args.schema,
        user=args.user, flush_rows=args.flush_rows, full_refresh=args.full_refresh,
    )

    if args.dry_run:
        print(f"taxi-run plan (root={root}); stages: {' -> '.join(planned)}")
        for t in types:
            print(f"  {t}: {', '.join(planned)}")
        return 0

    runs: list[report.TypeRun] = []
    all_outcomes: list[pipeline.StageOutcome] = []
    abort_load = False

    for t in types:
        outcomes: list[pipeline.StageOutcome] = []
        for stage in planned:
            if stage == pipeline.LOAD and abort_load:
                break
            if stage == pipeline.DOWNLOAD:
                rc = stages.run(stages.build_download_cmd(root, t, args.recent), root)
            elif stage == pipeline.NORMALIZE:
                rc = stages.run(stages.build_normalize_cmd(t, args.sample), root)
            else:  # LOAD
                rc = stages.run(stages.build_load_cmd(t, INPUT_DIR, conn), root,
                                extra_env={"MSSQL_PASSWORD": password})
            o = pipeline.classify(stage, rc)
            outcomes.append(o)
            if o.abort_run:
                abort_load = True
            if o.halt_type:  # needs-review or failure: skip this type's remaining stages
                break
        runs.append(report.TypeRun(t, outcomes))
        all_outcomes.extend(outcomes)

    print(report.render_summary(runs))
    return pipeline.overall_exit_code(all_outcomes)


if __name__ == "__main__":
    sys.exit(main())
