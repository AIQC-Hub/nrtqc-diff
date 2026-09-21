"""
The ``nrtqc-diff`` command line entry point.

One subcommand so far, ``build``, which is the whole Python side of the
project: everything else in the repository is the static site that reads what
this writes.
"""

import argparse
import sys
from typing import List, Optional

from nrtqc_diff.build import build_site_data
from nrtqc_diff.config import read_config


def build_parser() -> argparse.ArgumentParser:
    """
    Assemble the argument parser.

    :return: The parser, with its subcommands attached.
    """
    parser = argparse.ArgumentParser(
        prog="nrtqc-diff",
        description=(
            "Turn aiqclib NRT QC output into the files the static site queries."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Write the site's data files.")
    build.add_argument(
        "-c",
        "--config",
        default="config/datasets.yaml",
        help="The build configuration (default: config/datasets.yaml).",
    )
    build.add_argument(
        "-o",
        "--data-dir",
        default=None,
        help="Override the output directory named in the configuration.",
    )
    build.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Print nothing but errors.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """
    Run the command line interface.

    :param argv: The arguments to parse, defaulting to ``sys.argv[1:]``.
    :return: The process exit code.
    """
    args = build_parser().parse_args(argv)

    try:
        config = read_config(args.config)
        if args.data_dir:
            config = type(config)(
                title=config.title,
                data_dir=args.data_dir,
                variables=config.variables,
                datasets=config.datasets,
            )
        build_site_data(config, verbose=not args.quiet)
    except (FileNotFoundError, ValueError) as error:
        print(f"nrtqc-diff: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
