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
        "data_mode": {"columns": ["temp_dm", "pres_dm"]},
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


def test_missing_flag_values_default_to_the_unjudged_values(tmp_path):
    """0 is "no QC performed" and 9 is "missing value"; neither means good."""
    config = read_config(str(write_config(tmp_path)))
    assert config.variables[0].missing_flag_values == [0, 9]


def test_missing_flag_values_can_be_set(tmp_path):
    path = write_config(
        tmp_path,
        variables=[
            {
                "name": "temp",
                "flag": "temp_qc",
                "nrt_flag": "temp_nrt_flag",
                "missing_flag_values": [9],
            }
        ],
    )
    assert read_config(str(path)).variables[0].missing_flag_values == [9]


def test_a_flag_value_cannot_be_both_bad_and_missing(tmp_path):
    path = write_config(
        tmp_path,
        variables=[
            {
                "name": "temp",
                "flag": "temp_qc",
                "nrt_flag": "temp_nrt_flag",
                "bad_flag_values": [4, 9],
                "missing_flag_values": [0, 9],
            }
        ],
    )
    with pytest.raises(ValueError, match="cannot be both"):
        read_config(str(path))


def test_row_group_size_defaults_and_is_configurable(tmp_path):
    assert read_config(str(write_config(tmp_path))).row_group_size == 100_000
    path = write_config(tmp_path, site={"data_dir": "out", "row_group_size": 25000})
    assert read_config(str(path)).row_group_size == 25000


def test_a_row_group_size_of_zero_is_rejected(tmp_path):
    path = write_config(tmp_path, site={"data_dir": "out", "row_group_size": 0})
    with pytest.raises(ValueError, match="row_group_size"):
        read_config(str(path))


def test_data_mode_values_default_to_delayed_mode(tmp_path):
    config = read_config(str(write_config(tmp_path)))
    assert config.data_mode.columns == ["temp_dm", "pres_dm"]
    assert config.data_mode.delayed_values == ["D"]


def test_data_mode_values_can_be_set(tmp_path):
    path = write_config(
        tmp_path, data_mode={"columns": ["temp_dm"], "delayed_values": ["D", "M"]}
    )
    assert read_config(str(path)).data_mode.delayed_values == ["D", "M"]


def test_the_data_mode_section_is_required(tmp_path):
    """Calling every profile NRT by omission would defeat the split."""
    path = write_config(tmp_path)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    del document["data_mode"]
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValueError, match="data_mode"):
        read_config(str(path))


def test_data_mode_needs_a_column(tmp_path):
    path = write_config(tmp_path, data_mode={"columns": []})
    with pytest.raises(ValueError, match="data_mode.columns"):
        read_config(str(path))


def test_a_dataset_can_be_filed_under_one_data_mode(tmp_path):
    entry = {
        "id": "cora",
        "region": "R",
        "product": "P",
        "path": "a.parquet",
        "data_mode": "delayed",
    }
    config = read_config(str(write_config(tmp_path, datasets=[entry])))
    assert config.datasets[0].data_mode == "delayed"


def test_a_dataset_data_mode_must_be_a_group(tmp_path):
    entry = {
        "id": "cora",
        "region": "R",
        "product": "P",
        "path": "a.parquet",
        "data_mode": "D",
    }
    with pytest.raises(ValueError, match="cora"):
        read_config(str(write_config(tmp_path, datasets=[entry])))
