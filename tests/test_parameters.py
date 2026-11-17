"""nrtqclib's tests and parameters, and ctddump's regions, in meta.json."""

import json

import polars as pl
import pytest
import yaml

from nrtqc_diff.analyse import nrtqclib_parameters, region_parameters
from nrtqc_diff.config import read_config

NRTQCLIB = {
    "path_info_sets": [
        {
            "name": "p",
            "common": {"base_path": "/secret/batch"},
            "input": {"base_path": "/secret/in"},
        }
    ],
    "qc_item_sets": [
        {
            "name": "set_a",
            "items": [
                {"name": "impossible_date"},
                {
                    "name": "regional_range",
                    "params": {"temp": {"min": -1.92, "max": 25.0}},
                },
                {"name": "density_inversion", "params": {"threshold": 0.03}},
            ],
        },
        {
            "name": "set_b",
            "items": [{"name": "spike", "params": {"depth_threshold": 500}}],
        },
        {"name": "unused", "items": [{"name": "stuck_value"}]},
    ],
    "nrt_qc_sets": [
        {
            "name": "x",
            "dataset_folder_name": "run_one",
            "input_file_name": "src_one.parquet",
            "qc_item_set": "set_a",
        },
        {
            "name": "y",
            "dataset_folder_name": "run_two",
            "input_file_name": "other.parquet",
            "qc_item_set": "set_b",
        },
    ],
}

REGIONS = [
    {
        "region": "North",
        "source": "ctddump x.y, scripts/clean_data.sh",
        "include": [{"min_lon": 6, "max_lon": 30, "min_lat": 53, "max_lat": 66}],
        "exclude": [{"min_lon": 6, "max_lon": 15, "min_lat": 60, "max_lat": 66}],
    }
]


def _config(tmp_path, regions=REGIONS):
    (tmp_path / "batch").mkdir(exist_ok=True)
    (tmp_path / "batch" / "nrt_qc.yaml").write_text(
        yaml.safe_dump(NRTQCLIB), encoding="utf-8"
    )
    path = tmp_path / "c.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "variables": [{"name": "temp", "flag": "temp_qc", "nrt_flag": "t"}],
                "data_mode": {"columns": ["temp_dm"]},
                "datasets": [
                    # Matched by its source and the input file name.
                    {
                        "id": "one",
                        "region": "North",
                        "product": "P",
                        "path": "a.parquet",
                        "source": "src_one",
                    },
                    # Matched by the dataset folder in its path.
                    {
                        "id": "two",
                        "region": "North",
                        "product": "Q",
                        "path": "run_two/out.parquet",
                    },
                    # Not run at all.
                    {
                        "id": "three",
                        "region": "South",
                        "product": "P",
                        "path": "c.parquet",
                    },
                ],
                "analyses": {"out_dir": "out"},
                "provenance": {
                    "nrtqclib_config": "batch/nrt_qc.yaml",
                    "regions": regions,
                },
            }
        ),
        encoding="utf-8",
    )
    return read_config(str(path))


def test_the_parameters_of_the_sets_run_are_published_without_paths(tmp_path):
    published = nrtqclib_parameters(_config(tmp_path))
    assert published["datasets"] == {"one": "set_a", "two": "set_b"}
    assert list(published["qc_item_sets"]) == ["set_a", "set_b"]
    assert published["qc_item_sets"]["set_a"] == {
        "impossible_date": {},
        "regional_range": {"temp": {"min": -1.92, "max": 25.0}},
        "density_inversion": {"threshold": 0.03},
    }
    text = json.dumps(published)
    assert "secret" not in text and "parquet" not in text and "run_" not in text


def test_profiles_outside_a_region_are_counted(tmp_path):
    profiles = pl.DataFrame(
        {
            "dataset_id": ["one", "one", "one", "one", "two", "three"],
            # Inside; on the include box's edge; in the excluded box; a
            # missing position; inside; another region, not counted here.
            "longitude": [20.0, 30.0, 10.0, None, 7.0, 100.0],
            "latitude": [55.0, 66.0, 62.0, 55.0, 59.9, 0.0],
        }
    )
    regions = region_parameters(_config(tmp_path), profiles)
    assert len(regions) == 1
    north = regions[0]
    assert north["outside"] == {"one": 2, "two": 0}
    assert north["include"] == [
        {"min_lon": 6.0, "max_lon": 30.0, "min_lat": 53.0, "max_lat": 66.0}
    ]
    assert north["source"].startswith("ctddump")
    # Stored in single precision and widened, a position on the edge is
    # still inside.
    edge = pl.DataFrame(
        {"dataset_id": ["one"], "longitude": [35.567], "latitude": [66.0]},
        schema_overrides={"longitude": pl.Float32, "latitude": pl.Float32},
    ).with_columns(pl.col("longitude", "latitude").cast(pl.Float64))
    box = {**REGIONS[0]["include"][0], "max_lon": 35.567}
    edged = _config(tmp_path, [{**REGIONS[0], "include": [box]}])
    assert region_parameters(edged, edge)[0]["outside"] == {"one": 0}
    nan = profiles.with_columns(pl.lit(float("nan")).alias("latitude"))
    assert region_parameters(_config(tmp_path), nan)[0]["outside"] == {
        "one": 4,
        "two": 1,
    }


@pytest.mark.parametrize(
    "regions, message",
    [
        ([{"region": "Nowhere", "include": REGIONS[0]["include"]}], "no dataset has"),
        ([{"region": "North"}], "no 'include' box"),
        ([{"region": "North", "include": [{"min_lon": 1, "max_lon": 2}]}], "exactly"),
        (
            [
                {
                    "region": "North",
                    "include": [
                        {"min_lon": 3, "max_lon": 2, "min_lat": 0, "max_lat": 1}
                    ],
                }
            ],
            "minimum above",
        ),
        (REGIONS + REGIONS, "repeats"),
        ([{**REGIONS[0], "colour": "red"}], "unknown key"),
    ],
)
def test_malformed_regions_are_rejected(tmp_path, regions, message):
    with pytest.raises(ValueError, match=message):
        _config(tmp_path, regions)


def test_without_regions_or_nrtqclib_config_nothing_is_published(tmp_path):
    config = _config(tmp_path, regions=None)
    assert config.provenance.regions == []
    assert region_parameters(config, pl.DataFrame({"dataset_id": ["one"]})) is None
