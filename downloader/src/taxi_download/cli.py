# downloader/src/taxi_download/cli.py
"""Entry point for the `taxi-download` command (and `python -m taxi_download.cli`)."""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import httpx

from taxi_download.download import (
    DATA_TYPES,
    WalkSummary,
    clean_corrupt,
    download_full,
    download_recent,
)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="taxi-download",
        description="Download NYC TLC trip-data parquet files from CloudFront.",
    )
    p.add_argument("data_type", nargs="?", choices=DATA_TYPES,
                   help="one of the four types; omit to download all four")
    p.add_argument("--recent", nargs="?", type=int, const=3, default=None, metavar="N",
                   help="download the N most recent months (default 3 when N is omitted)")
    p.add_argument("--data-dir", default=".",
                   help="base directory; files land under <DIR>/raw (default: .)")
    return p.parse_args(argv)


def _today() -> date:
    return date.today()


def main(argv=None) -> int:
    args = parse_args(argv)
    raw_dir = Path(args.data_dir) / "raw"

    removed = clean_corrupt(raw_dir)
    if removed:
        print(f"cleaned {removed} corrupt parquet file(s)")

    types = [args.data_type] if args.data_type else list(DATA_TYPES)
    today = _today()
    total = 0
    exit_code = 0

    with httpx.Client(follow_redirects=True,
                      timeout=httpx.Timeout(120.0, connect=30.0)) as client:
        for t in types:
            if args.recent is not None:
                summ = download_recent(client, t, raw_dir, args.recent, today, time.sleep)
            else:
                summ = download_full(client, t, raw_dir, today, time.sleep)
            total += summ.downloaded
            print(f"{t}: downloaded {summ.downloaded}, gave up on {summ.gaveup}")
            if summ.downloaded == 0 and summ.gaveup > 0:
                exit_code = 2

    print(f"total downloaded: {total}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
