"""
Move the published site data between a working checkout and a GitHub release.

The trimming happens where the inputs are. An `aiqclib` batch is 2.2 GB of
parquet that the build reads twice to write the 429 MB the site serves, and
the deploy runs on every push to `main`, so rebuilding it there would spend
two minutes and a 2.2 GB download re-deriving the same files on a commit that
only touched a docstring. The build runs once, on the machine that has the
data, and the deploy renders what it produced.

A release therefore holds the contents of `site/data`, one asset per file:
`catalog.json`, `profiles.parquet`, and one observation file per dataset. The
catalog is what maps an asset back to the path it belongs at, so fetching
needs no build configuration and cannot disagree with what was published.

What a release cannot carry is the code that made it, so `catalog.json`
records `data_format` and both halves of this script check it. Data published
before a change to the published columns or file names is refused rather than
rendered against a site that has moved on. See `DATA_FORMAT` in
`src/nrtqc_diff/build.py`.

Usage:

    uv run nrtqc-diff build -c config/test_nrt.yaml
    uv run python scripts/data_release.py publish data-2026-09-23
    uv run python scripts/data_release.py fetch data-2026-09-23

Both need the `gh` CLI, authenticated for the data repository.
"""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from nrtqc_diff.build import DATA_FORMAT

#: The repository whose releases carry the data. It is separate from the code
#: repository so that a data refresh and a code release do not share a tag
#: list, and because release assets live outside the git object store: a new
#: vintage of a 429 MB build adds a tag ref and nothing to any history.
DEFAULT_REPO = "AIQC-Hub/nrtqc-diff-data"

#: Where a build writes and a deploy reads.
DEFAULT_DATA_DIR = "site/data"

#: The two files every build writes beside the observation files.
CATALOG_NAME = "catalog.json"
PROFILES_NAME = "profiles.parquet"

#: Uploaded beside them. It records how big each asset should be, which is
#: what lets `fetch` tell a truncated download from a complete one, and what
#: the data was built from and when.
MANIFEST_NAME = "manifest.json"


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


def check_format(catalog: Dict[str, Any], where: str) -> None:
    """
    Refuse data whose shape is not the one this checkout writes and reads.

    :param catalog: A parsed ``catalog.json``.
    :param where: What is being checked, for the message.
    :raises SystemExit: If the catalog was written by a different format.
    """
    found = catalog.get("data_format")
    if found == DATA_FORMAT:
        return
    raise SystemExit(
        f"{where} is data format {found if found is not None else 'unrecorded'}, "
        f"and this checkout writes and reads {DATA_FORMAT}.\n"
        "The published columns, file names or catalog fields have changed "
        "since it was built.\nRebuild it with `nrtqc-diff build` and publish "
        "it again."
    )


def data_files(data_dir: str) -> Tuple[Dict[str, Any], List[Tuple[str, str]]]:
    """
    List what a build wrote, as the catalog describes it.

    :param data_dir: A directory a build wrote to.
    :return: The parsed catalog, and the files as ``(asset name, path)``
             pairs. An asset is named after the file it holds, so an
             observation file at ``obs/ar_ar.parquet`` is ``ar_ar.parquet``.
    :raises SystemExit: If the directory is not a build, a file it names is
                        missing, or two files would claim one asset name.
    """
    catalog_path = os.path.join(data_dir, CATALOG_NAME)
    if not os.path.isfile(catalog_path):
        raise SystemExit(
            f"No {CATALOG_NAME} in {data_dir}. Run `nrtqc-diff build` first."
        )
    with open(catalog_path, "r", encoding="utf-8") as handle:
        catalog = json.load(handle)
    check_format(catalog, data_dir)

    relative = [CATALOG_NAME, PROFILES_NAME]
    relative += [
        dataset["observations_file"]
        for region in catalog["regions"]
        for product in region["products"]
        for dataset in product["datasets"]
    ]

    files: List[Tuple[str, str]] = []
    claimed: Dict[str, str] = {}
    for name in relative:
        asset = os.path.basename(name)
        if asset in claimed:
            raise SystemExit(
                f"{name} and {claimed[asset]} would both be published as "
                f"'{asset}'. A dataset id may not collide with "
                f"{PROFILES_NAME} or {CATALOG_NAME}."
            )
        claimed[asset] = name
        path = os.path.join(data_dir, name)
        if not os.path.isfile(path):
            raise SystemExit(f"{CATALOG_NAME} names {name}, which is not there.")
        files.append((asset, path))
    return catalog, files


def publish(args: argparse.Namespace) -> None:
    """
    Upload a built `site/data` to a release, one asset per file.

    :param args: The parsed command line.
    """
    catalog, files = data_files(args.data_dir)
    sizes = {asset: os.path.getsize(path) for asset, path in files}
    total = sum(sizes.values())
    manifest = {
        "published": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "built": catalog.get("generated"),
        "data_format": DATA_FORMAT,
        "total_bytes": total,
        "assets": [{"asset": asset, "bytes": sizes[asset]} for asset, _ in files],
    }

    for asset, _ in files:
        print(f"  {asset:<28} {human(sizes[asset]):>9}")
    print(f"  {'total':<28} {human(total):>9}")
    if args.dry_run:
        print(f"Would upload {len(files)} assets to {args.repo} {args.tag}.")
        return

    stage = tempfile.mkdtemp(prefix="nrtqc-data-")
    try:
        # Symlinks rather than copies: nothing is duplicated on disk to give
        # an asset the name it is published under.
        uploads: List[str] = []
        for asset, path in files:
            link = os.path.join(stage, asset)
            os.symlink(os.path.abspath(path), link)
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
                f"Published data for the nrtqc-diff site: {len(files) - 2} "
                f"datasets, {human(total)}, data format {DATA_FORMAT}, built "
                f"{catalog.get('generated', 'at an unrecorded time')}.\n\n"
                "Downloaded and rendered by the deploy workflow of "
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
    Download a release into a data directory, replacing what is there.

    The directory is built complete in a staging area and swapped in, so a
    download that came up short leaves the previous one untouched and no run
    can leave a half filled directory behind. Replacing rather than merging is
    what keeps a dataset dropped from the catalog from lingering as a file
    nothing references but the deploy still ships.

    :param args: The parsed command line.
    :raises SystemExit: If the release is incomplete or carries another data
                        format.
    """
    parent = os.path.dirname(os.path.abspath(args.data_dir)) or "."
    os.makedirs(parent, exist_ok=True)
    download = tempfile.mkdtemp(prefix="nrtqc-data-")
    staging = tempfile.mkdtemp(prefix="nrtqc-data-", dir=parent)
    try:
        print(f"Downloading {args.repo} {args.tag}")
        gh(
            "release",
            "download",
            args.tag,
            "--repo",
            args.repo,
            "--dir",
            download,
        )

        catalog_file = os.path.join(download, CATALOG_NAME)
        if not os.path.isfile(catalog_file):
            held = ", ".join(sorted(os.listdir(download))) or "nothing"
            raise SystemExit(
                f"{args.repo} {args.tag} holds no {CATALOG_NAME}, so it is not "
                f"a published build.\nIt holds: {held}."
            )
        with open(catalog_file, "r", encoding="utf-8") as handle:
            catalog = json.load(handle)
        check_format(catalog, f"{args.repo} {args.tag}")
        print(f"  built {catalog.get('generated', 'at an unrecorded time')}")

        expected: Dict[str, int] = {}
        manifest_file = os.path.join(download, MANIFEST_NAME)
        if os.path.isfile(manifest_file):
            with open(manifest_file, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
            expected = {
                entry["asset"]: int(entry["bytes"])
                for entry in manifest.get("assets", [])
            }

        wanted = [CATALOG_NAME, PROFILES_NAME]
        wanted += [
            dataset["observations_file"]
            for region in catalog["regions"]
            for product in region["products"]
            for dataset in product["datasets"]
        ]
        absent = [
            name
            for name in wanted
            if not os.path.isfile(os.path.join(download, os.path.basename(name)))
        ]
        if absent:
            held = ", ".join(sorted(os.listdir(download))) or "nothing"
            raise SystemExit(
                f"{args.repo} {args.tag} holds no asset for "
                f"{', '.join(absent)}.\nIt holds: {held}."
            )

        # Everything is checked before anything is moved, so a short download
        # never becomes a deployed site.
        short = [
            f"  {asset}: {size} bytes, the manifest says {expected[asset]}"
            for asset, size in (
                (
                    os.path.basename(name),
                    os.path.getsize(os.path.join(download, os.path.basename(name))),
                )
                for name in wanted
            )
            if asset in expected and expected[asset] != size
        ]
        if short:
            raise SystemExit(
                "The download is incomplete; nothing was written.\n" + "\n".join(short)
            )

        total = 0
        for name in wanted:
            source = os.path.join(download, os.path.basename(name))
            target = os.path.join(staging, name)
            os.makedirs(os.path.dirname(target) or staging, exist_ok=True)
            shutil.move(source, target)
            total += os.path.getsize(target)

        previous = f"{args.data_dir}.replaced"
        shutil.rmtree(previous, ignore_errors=True)
        replaced = os.path.isdir(args.data_dir)
        if replaced:
            os.rename(args.data_dir, previous)
        os.rename(staging, args.data_dir)
        staging = ""
        if replaced:
            shutil.rmtree(previous, ignore_errors=True)
        print(f"Wrote {len(wanted)} files into {args.data_dir}, {human(total)}.")
    finally:
        shutil.rmtree(download, ignore_errors=True)
        if staging:
            shutil.rmtree(staging, ignore_errors=True)


def main() -> None:
    """Parse the command line and run the requested half."""
    # Which release and which directory are accepted on either side of the
    # subcommand, because both orders are the natural one to type.
    # Suppressing the defaults is what makes that safe: an option left out of
    # one parser then leaves what the other parsed alone, rather than writing
    # a default over it, and the defaults are applied once at the end.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--repo",
        default=argparse.SUPPRESS,
        help=f"The repository holding the release. Default: {DEFAULT_REPO}",
    )
    common.add_argument(
        "-d",
        "--data-dir",
        default=argparse.SUPPRESS,
        help=f"The built site data. Default: {DEFAULT_DATA_DIR}",
    )

    parser = argparse.ArgumentParser(description=__doc__, parents=[common])
    commands = parser.add_subparsers(dest="command", required=True)

    upload = commands.add_parser(
        "publish", parents=[common], help="Upload a built site/data to a release."
    )
    upload.add_argument("tag", help="The release tag, e.g. data-2026-09-23.")
    upload.add_argument(
        "--dry-run", action="store_true", help="List what would be uploaded."
    )
    upload.set_defaults(handler=publish)

    download = commands.add_parser(
        "fetch", parents=[common], help="Download a release into site/data."
    )
    download.add_argument("tag", help="The release tag to download.")
    download.set_defaults(handler=fetch)

    args = parser.parse_args()
    args.repo = getattr(args, "repo", DEFAULT_REPO)
    args.data_dir = getattr(args, "data_dir", DEFAULT_DATA_DIR)
    args.handler(args)


if __name__ == "__main__":
    main()
