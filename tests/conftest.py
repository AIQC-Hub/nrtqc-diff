"""Shared fixtures: a tiny NRT QC output and the config that publishes it."""

import json
from typing import Dict, List

import polars as pl
import pytest
import yaml

#: One profile per agreement case, so every branch of the build has a row.
#: ``(platform, temp_qc, temp_nrt_flag)`` per observation.
PROFILE_CASES: Dict[str, List[tuple]] = {
    # Both sources call observation 2 bad.
    "AGREE": [(1, 1, 1), (2, 4, 4), (3, 1, 1)],
    # Only the input flags anything.
    "INPUT": [(1, 4, 1), (2, 1, 1), (3, 1, 1)],
    # Only aiqclib flags anything.
    "AIQC": [(1, 1, 1), (2, 1, 3), (3, 1, 1)],
    # Nothing is flagged, so this profile must not reach the site.
    "CLEAN": [(1, 1, 1), (2, 1, 1), (3, 1, 1)],
    # The input flag is missing, which is its own category.
    "NOFLAG": [(1, None, 1), (2, None, 4), (3, 1, 1)],
}


@pytest.fixture
def nrt_qc_frame() -> pl.DataFrame:
    """One NRT QC output covering every agreement category."""
    rows = []
    for platform, observations in PROFILE_CASES.items():
        for observation_no, temp_qc, temp_nrt in observations:
            rows.append(
                {
                    "platform_code": platform,
                    "profile_no": 1,
                    "observation_no": observation_no,
                    "pres": 5.0 * observation_no,
                    "temp": 10.0 - observation_no,
                    "temp_qc": temp_qc,
                    "temp_nrt_flag": temp_nrt,
                    "temp_qc_spike": temp_nrt,
                    "qc_impossible_date": 1,
                    "longitude": 20.0,
                    "latitude": 58.0,
                }
            )
    return pl.DataFrame(rows)


@pytest.fixture
def built(tmp_path, nrt_qc_frame):
    """
    Run a complete build over :func:`nrt_qc_frame` and hand back its output.

    :return: A tuple of the catalog, the profile frame and the observation
             frame of the single dataset.
    """
    from nrtqc_diff.build import build_site_data
    from nrtqc_diff.config import read_config

    input_path = tmp_path / "input.parquet"
    nrt_qc_frame.write_parquet(input_path)

    config_path = tmp_path / "datasets.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "site": {"title": "Test", "data_dir": str(tmp_path / "out")},
                "variables": [
                    {
                        "name": "temp",
                        "label": "Temperature",
                        "unit": "degC",
                        "flag": "temp_qc",
                        "nrt_flag": "temp_nrt_flag",
                        "bad_flag_values": [3, 4],
                    }
                ],
                "datasets": [
                    {
                        "id": "one",
                        "region": "Region A",
                        "product": "Product A",
                        "label": "Dataset one",
                        "path": str(input_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    build_site_data(read_config(str(config_path)))
    out = tmp_path / "out"
    profiles = pl.read_parquet(out / "profiles.parquet")
    observations = pl.read_parquet(out / "obs" / "one.parquet")
    written = json.loads((out / "catalog.json").read_text(encoding="utf-8"))
    return written, profiles, observations
