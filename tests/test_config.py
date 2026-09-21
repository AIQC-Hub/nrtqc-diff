"""The configuration reader: resolution, defaults and the errors it raises."""

import pytest
import yaml

from nrtqc_diff.config import read_config


def write_config(tmp_path, **overrides):
    """
    Write a minimal valid configuration, with the given keys replaced.

    :param tmp_path: The pytest temporary directory.
    :return: The path to the written file.
    """
    document = {
        "site": {"title": "Test", "data_dir": "out"},
        "variables": [
            {
                "name": "temp",
                "flag": "temp_qc",
                "nrt_flag": "temp_nrt_flag",
            }
        ],
        "datasets": [
            {
                "id": "one",
                "region": "R",
                "product": "P",
                "path": "input.parquet",
            }
        ],
    }
    document.update(overrides)
    path = tmp_path / "datasets.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def test_paths_resolve_against_the_config_file(tmp_path):
    config = read_config(str(write_config(tmp_path)))
    assert config.datasets[0].path == str(tmp_path / "input.parquet")
    assert config.data_dir == str(tmp_path / "out")


def test_absolute_paths_are_left_alone(tmp_path):
    config = read_config(
        str(
            write_config(
                tmp_path,
                datasets=[
                    {
                        "id": "one",
                        "region": "R",
                        "product": "P",
                        "path": "/data/x.parquet",
                    }
                ],
            )
        )
    )
    assert config.datasets[0].path == "/data/x.parquet"


def test_bad_flag_values_default_to_the_argo_scheme(tmp_path):
    config = read_config(str(write_config(tmp_path)))
    assert config.variables[0].bad_flag_values == [3, 4, 6, 7]


def test_label_falls_back_to_the_name(tmp_path):
    config = read_config(str(write_config(tmp_path)))
    assert config.variables[0].label == "temp"
    assert config.datasets[0].label == "one"


def test_missing_key_names_the_entry(tmp_path):
    path = write_config(tmp_path, variables=[{"name": "temp", "flag": "temp_qc"}])
    with pytest.raises(ValueError, match="nrt_flag"):
        read_config(str(path))


def test_empty_sections_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="no datasets"):
        read_config(str(write_config(tmp_path, datasets=[])))


def test_duplicate_dataset_ids_are_rejected(tmp_path):
    entry = {"id": "one", "region": "R", "product": "P", "path": "a.parquet"}
    with pytest.raises(ValueError, match="unique"):
        read_config(str(write_config(tmp_path, datasets=[entry, dict(entry)])))


def test_dataset_ids_are_restricted_to_sql_safe_characters(tmp_path):
    path = write_config(
        tmp_path,
        datasets=[
            {"id": "one-two", "region": "R", "product": "P", "path": "a.parquet"}
        ],
    )
    with pytest.raises(ValueError, match="underscores"):
        read_config(str(path))


def test_variable_lookup(tmp_path):
    config = read_config(str(write_config(tmp_path)))
    assert config.variable("temp").flag == "temp_qc"
    with pytest.raises(KeyError):
        config.variable("psal")
