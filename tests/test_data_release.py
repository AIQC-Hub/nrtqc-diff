"""
The contract between publishing a built `site/data` and fetching it back.

The deploy runs `fetch` unattended and renders whatever it produces, so what
is tested here is that a release round trips: every file a build wrote comes
back at the path the catalog names, the directory is replaced rather than
merged, and anything short or built to another data format stops before the
existing data is touched. `gh` is stubbed by a script on `PATH` that keeps a
release in a directory, which exercises the whole module without a network.
"""

import importlib.util
import json
import os
import stat
import sys

import pytest

from nrtqc_diff.build import DATA_FORMAT

#: Stands in for the GitHub CLI. A release is a directory under `$GH_STORE`,
#: and only the four subcommands this project uses are implemented.
GH_STUB = """#!/usr/bin/env python3
import os
import shutil
import sys

argv = sys.argv[2:]
subcommand, tag, directory, files = argv[0], "", "", []
index = 1
while index < len(argv):
    word = argv[index]
    if word in ("--repo", "--title", "--notes"):
        index += 2
    elif word == "--dir":
        directory = argv[index + 1]
        index += 2
    elif word == "--clobber":
        index += 1
    else:
        if tag:
            files.append(word)
        else:
            tag = word
        index += 1

release = os.path.join(os.environ["GH_STORE"], tag)
if subcommand == "view":
    sys.exit(0 if os.path.isdir(release) else 1)
if subcommand == "create":
    os.makedirs(release, exist_ok=True)
elif subcommand == "upload":
    for name in files:
        shutil.copyfile(name, os.path.join(release, os.path.basename(name)))
elif subcommand == "download":
    os.makedirs(directory, exist_ok=True)
    for name in sorted(os.listdir(release)):
        shutil.copyfile(os.path.join(release, name), os.path.join(directory, name))
"""


def load_script():
    """
    Import `scripts/data_release.py`, which is not part of the package.

    :return: The imported module.
    """
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts",
        "data_release.py",
    )
    spec = importlib.util.spec_from_file_location("data_release", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_data(data_dir, data_format=DATA_FORMAT):
    """
    Write what a build writes: a catalog, a profile file and two datasets.

    :param data_dir: The directory to write into.
    :param data_format: The format to stamp the catalog with.
    :return: The files written, by path relative to `data_dir`.
    """
    files = {
        "profiles.parquet": b"profiles" * 10,
        "obs/one.parquet": b"one" * 20,
        "obs/two.parquet": b"two" * 30,
    }
    catalog = {
        "generated": "2026-09-23T00:00:00+00:00",
        "data_format": data_format,
        "regions": [
            {
                "name": "Region A",
                "products": [
                    {
                        "name": "Product A",
                        "datasets": [
                            {"id": "one", "observations_file": "obs/one.parquet"},
                            {"id": "two", "observations_file": "obs/two.parquet"},
                        ],
                    }
                ],
            }
        ],
    }
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "obs").mkdir(exist_ok=True)
    for name, content in files.items():
        (data_dir / name).write_bytes(content)
    (data_dir / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    files["catalog.json"] = (data_dir / "catalog.json").read_bytes()
    return files


@pytest.fixture
def release(tmp_path, monkeypatch):
    """
    A stubbed `gh` and a built data directory.

    :return: A tuple of the module under test and the data directory.
    """
    binary = tmp_path / "bin" / "gh"
    binary.parent.mkdir(parents=True)
    binary.write_text(GH_STUB, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(binary.parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("GH_STORE", str(tmp_path / "store"))
    (tmp_path / "store").mkdir()
    return load_script(), tmp_path / "data"


def run(module, data_dir, *args):
    """
    Invoke the script's command line against one data directory.

    :param module: The imported script.
    :param data_dir: The value for `--data-dir`.
    :param args: The arguments after the script name.
    """
    saved = sys.argv
    sys.argv = ["data_release.py", "--data-dir", str(data_dir), *args]
    try:
        module.main()
    finally:
        sys.argv = saved


def contents(data_dir):
    """
    Every file under a directory, by relative path.

    :param data_dir: The directory to walk.
    :return: A mapping of relative path to bytes.
    """
    found = {}
    for root, _, names in os.walk(data_dir):
        for name in names:
            path = os.path.join(root, name)
            found[os.path.relpath(path, data_dir)] = open(path, "rb").read()
    return found


def test_round_trip_restores_every_file(release):
    """What is published comes back at the path the catalog names."""
    module, data_dir = release
    written = write_data(data_dir)
    before = contents(data_dir)

    run(module, data_dir, "publish", "data-test")
    run(module, data_dir, "fetch", "data-test")

    assert contents(data_dir) == before
    assert set(before) == set(written)


def test_fetch_replaces_rather_than_merges(release):
    """A file the catalog no longer names does not survive into the deploy."""
    module, data_dir = release
    write_data(data_dir)
    run(module, data_dir, "publish", "data-test")
    (data_dir / "obs" / "gone.parquet").write_bytes(b"stale")

    run(module, data_dir, "fetch", "data-test")

    assert not (data_dir / "obs" / "gone.parquet").exists()
    assert (data_dir / "obs" / "one.parquet").exists()


def test_a_short_download_keeps_the_data_that_is_there(release):
    """An asset that does not match the manifest stops before anything moves."""
    module, data_dir = release
    write_data(data_dir)
    run(module, data_dir, "publish", "data-test")
    before = contents(data_dir)
    store = os.path.join(os.environ["GH_STORE"], "data-test")
    with open(os.path.join(store, "two.parquet"), "wb") as handle:
        handle.write(b"cut")

    with pytest.raises(SystemExit) as stopped:
        run(module, data_dir, "fetch", "data-test")

    assert "incomplete" in str(stopped.value)
    assert contents(data_dir) == before


def test_another_data_format_is_refused(release):
    """A release built before the published shape changed is not rendered."""
    module, data_dir = release
    write_data(data_dir, data_format=DATA_FORMAT + 1)

    with pytest.raises(SystemExit) as refused:
        run(module, data_dir, "publish", "data-test")

    assert "data format" in str(refused.value)
