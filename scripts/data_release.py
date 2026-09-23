"""
Move the real build inputs between a working checkout and a GitHub release.

An `aiqclib` NRT QC run writes more than git should ever hold: the `test_nrt`
batch this site is built from is 2.2 GB across 8 files. They live instead as
assets on a release of the data repository, which the deploy workflow
downloads before it builds. Nothing here is derived: these are the run's own
outputs, and `site/data/` is rebuilt from them on every deploy.

One asset per dataset, named after the dataset id in the build configuration
rather than after the file on disk, because an `aiqclib` batch calls every one
of them `nrt_qc_output.parquet`. The configuration is what maps an asset back
to the path the build expects, so publishing and fetching read the same file
and a dataset cannot be renamed on one side only.

Usage:

    uv run python scripts/data_release.py publish data-2026-09-23
    uv run python scripts/data_release.py fetch data-2026-09-23

Both need the `gh` CLI, authenticated for the data repository. `fetch` refuses
to write over inputs that are already there, because in a working checkout the
path it would write to is the real `aiqclib` output tree.
"""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Dict, List

from nrtqc_diff.config import BuildConfig, read_config

#: The repository whose releases carry the inputs. It is separate from the
#: code repository so that a data refresh and a code release do not share a
#: tag list and do not have to happen together.
DEFAULT_REPO = "AIQC-Hub/nrtqc-diff-data"

#: The build configuration naming the datasets a release holds.
DEFAULT_CONFIG = "config/test_nrt.yaml"

#: Uploaded beside the parquet files. It records what the release was built
#: from and how big each asset should be, which is what lets `fetch` tell a
#: truncated download from a complete one and say in the log which vintage of
#: the data a deploy is running on.
MANIFEST_NAME = "manifest.json"


def asset_name(dataset_id: str) -> str:
    """
    The release asset holding one dataset's input.

    :param dataset_id: The `id` of a dataset in the build configuration.
    :return: The asset file name.
    """
    return f"{dataset_id}.parquet"


def human(size: int) -> str:
    """
    Format a byte count for a progress line.

    :param size: A number of bytes.
    :return: The size in the largest unit that leaves it above 1.
    """
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """
    Run a `gh` command, letting its output through to the terminal.

    :param args: The arguments after `gh`.
    :param check: Whether a non-zero exit should raise.
    :return: The finished process.
    :raises SystemExit: If `gh` is not installed.
    """
    try:
        return subprocess.run(["gh", *args], check=check)
    except FileNotFoundError:
        raise SystemExit(
            "The GitHub CLI (gh) is required. See https://cli.github.com/."
        ) from None


def load(config_file: str) -> BuildConfig:
    """
    Read the build configuration, reporting a missing file plainly.

    :param config_file: The YAML build configuration.
    :return: The parsed configuration.
    :raises SystemExit: If the file does not exist.
    """
    if not os.path.isfile(config_file):
        raise SystemExit(f"No such configuration: {config_file}")
    return read_config(config_file)


def publish(args: argparse.Namespace) -> None:
    """
    Upload one input file per configured dataset to a release.

    The files are staged as symlinks rather than copies, so a 2.2 GB batch
    costs nothing on disk before it is uploaded.

    :param args: The parsed command line.
    :raises SystemExit: If an input named by the configuration is missing.
    """
    config = load(args.config)
    missing = [
        f"  {dataset.id}: {dataset.path}"
        for dataset in config.datasets
        if not os.path.isfile(dataset.path)
    ]
    if missing:
        raise SystemExit(
            "These inputs are not where the configuration says they are:\n"
            + "\n".join(missing)
        )

    sizes = {dataset.id: os.path.getsize(dataset.path) for dataset in config.datasets}
    total = sum(sizes.values())
    manifest = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": os.path.basename(args.config),
        "total_bytes": total,
        "datasets": [
            {
                "id": dataset.id,
                "asset": asset_name(dataset.id),
                "bytes": sizes[dataset.id],
            }
            for dataset in config.datasets
        ],
    }

    for dataset in config.datasets:
        print(f"  {asset_name(dataset.id):<28} {human(sizes[dataset.id]):>9}")
    print(f"  {'total':<28} {human(total):>9}")
    if args.dry_run:
        print(f"Would upload {len(config.datasets)} assets to {args.repo} {args.tag}.")
        return

    stage = tempfile.mkdtemp(prefix="nrtqc-inputs-")
    try:
        uploads: List[str] = []
        for dataset in config.datasets:
            link = os.path.join(stage, asset_name(dataset.id))
            os.symlink(os.path.abspath(dataset.path), link)
            uploads.append(link)
        manifest_file = os.path.join(stage, MANIFEST_NAME)
        with open(manifest_file, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
            handle.write("\n")
        uploads.append(manifest_file)

        exists = (
            gh("release", "view", args.tag, "--repo", args.repo, check=False).returncode
            == 0
        )
        if not exists:
            notes = (
                f"Build inputs for the nrtqc-diff site: {len(config.datasets)} "
                f"datasets, {human(total)}, as named by `{manifest['config']}`.\n\n"
                "Downloaded by the deploy workflow of "
                "https://github.com/AIQC-Hub/nrtqc-diff; see its "
                "`docs/RELEASING.md`."
            )
            print(f"Creating release {args.tag} in {args.repo}")
            gh(
                "release",
                "create",
                args.tag,
                "--repo",
                args.repo,
                "--title",
                args.tag,
                "--notes",
                notes,
            )
        print(f"Uploading {len(uploads)} assets to {args.repo} {args.tag}")
        gh("release", "upload", args.tag, "--repo", args.repo, "--clobber", *uploads)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    print("Done.")


def fetch(args: argparse.Namespace) -> None:
    """
    Download a release into the paths the build configuration expects.

    :param args: The parsed command line.
    :raises SystemExit: If an input is already present, or the release does
                        not hold an asset for every configured dataset.
    """
    config = load(args.config)
    present = [
        dataset.path for dataset in config.datasets if os.path.exists(dataset.path)
    ]
    if present and not args.force:
        raise SystemExit(
            f"{len(present)} of these inputs are already here, the first being\n"
            f"  {present[0]}\n"
            "Nothing was downloaded. In a working checkout that path is the "
            "real aiqclib\noutput tree; pass --force only if you are sure it "
            "should be written over."
        )

    stage = tempfile.mkdtemp(prefix="nrtqc-inputs-")
    try:
        print(f"Downloading {args.repo} {args.tag}")
        gh(
            "release",
            "download",
            args.tag,
            "--repo",
            args.repo,
            "--dir",
            stage,
            "--pattern",
            "*.parquet",
            "--pattern",
            MANIFEST_NAME,
        )

        expected: Dict[str, int] = {}
        manifest_file = os.path.join(stage, MANIFEST_NAME)
        if os.path.isfile(manifest_file):
            with open(manifest_file, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
            expected = {
                entry["asset"]: int(entry["bytes"])
                for entry in manifest.get("datasets", [])
            }
            print(f"  built {manifest.get('generated', 'at an unrecorded time')}")

        absent = [
            asset_name(dataset.id)
            for dataset in config.datasets
            if not os.path.isfile(os.path.join(stage, asset_name(dataset.id)))
        ]
        if absent:
            held = sorted(os.listdir(stage))
            raise SystemExit(
                f"{args.repo} {args.tag} holds no asset for "
                f"{', '.join(absent)}.\nIt holds: {', '.join(held) or 'nothing'}."
            )

        # Every asset is checked before any of them is moved, so a download
        # that came up short leaves the destination as it found it rather
        # than half filled with a tree the next run would refuse to write to.
        sizes = {
            dataset.id: os.path.getsize(os.path.join(stage, asset_name(dataset.id)))
            for dataset in config.datasets
        }
        short = [
            f"  {asset_name(dataset_id)}: {size} bytes, manifest says "
            f"{expected[asset_name(dataset_id)]}"
            for dataset_id, size in sizes.items()
            if asset_name(dataset_id) in expected
            and expected[asset_name(dataset_id)] != size
        ]
        if short:
            raise SystemExit(
                "The download is incomplete; nothing was moved into place.\n"
                + "\n".join(short)
            )

        for dataset in config.datasets:
            source = os.path.join(stage, asset_name(dataset.id))
            os.makedirs(os.path.dirname(dataset.path), exist_ok=True)
            shutil.move(source, dataset.path)
            print(f"  {dataset.id:<12} {human(sizes[dataset.id]):>9}  {dataset.path}")
        total = sum(sizes.values())
        print(f"Fetched {len(config.datasets)} inputs, {human(total)}.")
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main() -> None:
    """Parse the command line and run the requested half."""
    # Which release and which configuration are accepted on either side of the
    # subcommand, because both orders are the natural one to type. Suppressing
    # the defaults is what makes that safe: an option left out of one parser
    # then leaves what the other parsed alone, rather than writing a default
    # over it, and the defaults are applied once at the end instead.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--repo",
        default=argparse.SUPPRESS,
        help=f"The repository holding the release. Default: {DEFAULT_REPO}",
    )
    common.add_argument(
        "-c",
        "--config",
        default=argparse.SUPPRESS,
        help=f"The build configuration. Default: {DEFAULT_CONFIG}",
    )

    parser = argparse.ArgumentParser(description=__doc__, parents=[common])
    commands = parser.add_subparsers(dest="command", required=True)

    upload = commands.add_parser(
        "publish", parents=[common], help="Upload the inputs to a release."
    )
    upload.add_argument("tag", help="The release tag, e.g. data-2026-09-23.")
    upload.add_argument(
        "--dry-run", action="store_true", help="List what would be uploaded."
    )
    upload.set_defaults(handler=publish)

    download = commands.add_parser(
        "fetch", parents=[common], help="Download the inputs of a release."
    )
    download.add_argument("tag", help="The release tag to download.")
    download.add_argument(
        "--force", action="store_true", help="Write over inputs already present."
    )
    download.set_defaults(handler=fetch)

    args = parser.parse_args()
    args.repo = getattr(args, "repo", DEFAULT_REPO)
    args.config = getattr(args, "config", DEFAULT_CONFIG)
    args.handler(args)


if __name__ == "__main__":
    main()
