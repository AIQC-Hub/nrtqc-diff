"""
The ``nrtqc-diff`` command line entry point.

Two subcommands. ``build`` writes the files the site's data pages query.
``analyse`` writes the tables behind the Analyses pages, which the R scripts
under ``analysis/`` turn into figures.
"""

import argparse
import sys
from dataclasses import replace
from typing import List, Optional

from nrtqc_diff.analyse import run_analyses
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
            "Turn nrtqclib NRT QC output into the files the static site queries."
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

    analyse = subparsers.add_parser(
        "analyse", help="Write the tables of the Analyses pages."
    )
    analyse.add_argument(
        "-c",
        "--config",
        default="config/test_nrt.yaml",
        help="The build configuration (default: config/test_nrt.yaml).",
    )
    analyse.add_argument(
        "-o",
        "--out-dir",
        default=None,
        help="Override analyses.out_dir from the configuration.",
    )
    analyse.add_argument(
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
        if args.command == "analyse":
            if args.out_dir and config.analyses is not None:
                config = replace(
                    config, analyses=replace(config.analyses, out_dir=args.out_dir)
                )
            run_analyses(config, verbose=not args.quiet)
            return 0
        if args.data_dir:
            # Every other setting is kept, row_group_size included, which a
            # field by field copy here once dropped.
            config = replace(config, data_dir=args.data_dir)
        build_site_data(config, verbose=not args.quiet)
    except (FileNotFoundError, ValueError) as error:
        print(f"nrtqc-diff: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
