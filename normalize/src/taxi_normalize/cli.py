"""Entry point for the `normalize` command."""
import argparse
import sys


def _build_bootstrap_parser() -> argparse.ArgumentParser:
    """Parser used only when the user invoked `normalize bootstrap ...`."""
    parser = argparse.ArgumentParser(
        prog="normalize bootstrap",
        description="Generate normalize/mappings/<type>.yaml from raw/<type>/ + schema-drift analysis.",
    )
    parser.add_argument("data_type", help="One of: yellow, green, fhv, fhvhv")
    parser.add_argument(
        "--sample",
        default="100%",
        help="Rows to sample for rename verification: N (absolute) or N%% (percent). Default: 100%% (full scan).",
    )
    return parser


def _build_normalize_parser() -> argparse.ArgumentParser:
    """Default parser: `normalize [data_type]`."""
    parser = argparse.ArgumentParser(
        prog="normalize",
        description="Rewrite historical TLC parquet files to conform to the latest schema.",
        epilog="Subcommand: `normalize bootstrap <type> [--sample N|N%%]` regenerates a mapping YAML.",
    )
    parser.add_argument(
        "data_type",
        nargs="?",
        help="Data type to normalize (yellow/green/fhv/fhvhv). Omit to run all four.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    # Route on the first positional token. `bootstrap` is the only real subcommand;
    # everything else (including no args) is the default normalize mode.
    if argv and argv[0] == "bootstrap":
        args = _build_bootstrap_parser().parse_args(argv[1:])
        print(
            f"bootstrap {args.data_type} --sample {args.sample}: not implemented yet",
            file=sys.stderr,
        )
        return 2

    args = _build_normalize_parser().parse_args(argv)
    if args.data_type:
        print(f"normalize {args.data_type}: not implemented yet", file=sys.stderr)
    else:
        print("normalize (all types): not implemented yet", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
