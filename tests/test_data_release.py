"""
The contract between publishing the build inputs and fetching them back.

The deploy workflow runs `fetch` unattended, so what is tested here is the
round trip: an asset named on the way out must land on the way back in at the
path `config/test_nrt.yaml` names, byte for byte. `gh` is stubbed by a script
on `PATH` that keeps a release in a directory, which exercises the whole of
this module without a network or a token.
"""

import importlib.util
import os
import shutil
import stat
import sys

import pytest
import yaml

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
    if word in ("--repo", "--title", "--notes", "--pattern"):
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


@pytest.fixture
def release(tmp_path, monkeypatch):
    """
    A stubbed `gh`, two input files and the config that names them.

    :return: A tuple of the module under test, the config path and the input
             paths by dataset id.
    """
    binary = tmp_path / "bin" / "gh"
    binary.parent.mkdir(parents=True)
    binary.write_text(GH_STUB, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(binary.parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("GH_STORE", str(tmp_path / "store"))
    (tmp_path / "store").mkdir()

    # An aiqclib batch names every dataset's output the same, and the
    # directory above it is what tells them apart. That is the layout the
    # asset naming has to survive.
    inputs = {}
    for index, name in enumerate(("one", "two")):
        path = tmp_path / "in" / f"nrt_qc_{name}_0001" / "nrt_qc"
        path.mkdir(parents=True)
        target = path / "nrt_qc_output.parquet"
        target.write_bytes(b"parquet" + bytes([index]) * (100 + index))
        inputs[name] = target

    config = tmp_path / "test_nrt.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "site": {"data_dir": str(tmp_path / "out")},
                "variables": [
                    {"name": "temp", "flag": "temp_qc", "nrt_flag": "temp_nrt_flag"}
                ],
                "datasets": [
                    {
                        "id": name,
                        "region": "Region A",
                        "product": "Product A",
                        "path": str(path),
                    }
                    for name, path in inputs.items()
                ],
            }
        ),
        encoding="utf-8",
    )
    return load_script(), config, inputs


def run(module, *args):
    """
    Invoke the script's command line.

    :param module: The imported script.
    :param args: The arguments after the script name.
    """
    saved = sys.argv
    sys.argv = ["data_release.py", *args]
    try:
        module.main()
    finally:
        sys.argv = saved


def test_round_trip_restores_every_input(release):
    """What is published comes back at the path the configuration names."""
    module, config, inputs = release
    before = {name: path.read_bytes() for name, path in inputs.items()}

    run(module, "-c", str(config), "publish", "data-test")
    for path in inputs.values():
        shutil.rmtree(path.parent.parent)
    run(module, "-c", str(config), "fetch", "data-test")

    assert {name: path.read_bytes() for name, path in inputs.items()} == before


def test_fetch_leaves_inputs_that_are_already_there(release):
    """A checkout's `data/` is the real output tree, so it is not written over."""
    module, config, inputs = release
    run(module, "-c", str(config), "publish", "data-test")
    inputs["one"].write_bytes(b"local")

    with pytest.raises(SystemExit) as refused:
        run(module, "-c", str(config), "fetch", "data-test")

    assert "--force" in str(refused.value)
    assert inputs["one"].read_bytes() == b"local"


def test_a_short_download_moves_nothing(release):
    """An asset that does not match the manifest stops the whole fetch."""
    module, config, inputs = release
    run(module, "-c", str(config), "publish", "data-test")
    for path in inputs.values():
        shutil.rmtree(path.parent.parent)
    store = os.path.join(os.environ["GH_STORE"], "data-test")
    with open(os.path.join(store, "two.parquet"), "wb") as handle:
        handle.write(b"cut")

    with pytest.raises(SystemExit) as stopped:
        run(module, "-c", str(config), "fetch", "data-test")

    assert "incomplete" in str(stopped.value)
    assert not inputs["one"].exists()
