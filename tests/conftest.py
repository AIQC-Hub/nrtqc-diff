"""Shared fixtures: a tiny NRT QC output and the config that publishes it."""

import json
from typing import Dict, List

import polars as pl
import pytest
import yaml

#: One profile per agreement case, so every branch of the build has a row.
#: ``(platform, temp_qc, temp_nrt_flag)`` per observation. The keys are in
#: alphabetical order because the build requires its input to be ordered by
#: platform, the way an ``nrtqclib`` NRT QC output is.
PROFILE_CASES: Dict[str, List[tuple]] = {
    # Both sources call observation 2 bad.
    "AGREE": [(1, 1, 1), (2, 4, 4), (3, 1, 1)],
    # Only nrtqclib flags anything.
    "CALC": [(1, 1, 1), (2, 1, 3), (3, 1, 1)],
    # Nothing is flagged, so this profile must not reach the site.
    "CLEAN": [(1, 1, 1), (2, 1, 1), (3, 1, 1)],
    # Only the input flags anything.
    "INPUT": [(1, 4, 1), (2, 1, 1), (3, 1, 1)],
    # Flag 9 means the value is missing, which is not the same as good. On
    # its own it cannot qualify a profile, but here nrtqclib flags one too.
    "MISSING": [(1, 9, 1), (2, 9, 4), (3, 1, 1)],
    # The input flag is missing, which is its own category.
    "NOFLAG": [(1, None, 1), (2, None, 4), (3, 1, 1)],
    # Flag 0 means no QC was performed. Nothing else is flagged, so this
    # profile must not reach the site either.
    "NOQC": [(1, 0, 1), (2, 0, 1), (3, 1, 1)],
}

#: The data mode of each profile that is not plain real time, as
#: ``{platform: {observation_no: (temp_dm, pres_dm)}}``. Everything not
#: listed is ``("R", "R")``.
DATA_MODE_CASES: Dict[str, Dict[int, tuple]] = {
    # Mixed: only the pressure of the deepest observation was revised in
    # delayed mode, which is still enough to make the profile delayed.
    "CALC": {3: ("R", "D")},
    # Delayed, but nothing is flagged, so it counts towards the source total
    # of the delayed group without being published.
    "CLEAN": {1: ("D", "D"), 2: ("D", "D"), 3: ("D", "D")},
    # Delayed mode throughout.
    "INPUT": {1: ("D", "D"), 2: ("D", "D"), 3: ("D", "D")},
    # An empty data mode says nothing, so this profile cannot be called NRT
    # and is filed as unknown.
    "MISSING": {1: ("", "R"), 2: (None, "R")},
}

#: The published profiles that contain delayed mode data.
DELAYED_PUBLISHED: List[str] = ["CALC:1", "INPUT:1"]


@pytest.fixture
def nrt_qc_frame() -> pl.DataFrame:
    """One NRT QC output covering every agreement category."""
    rows = []
    for platform, observations in PROFILE_CASES.items():
        for observation_no, temp_qc, temp_nrt in observations:
            temp_dm, pres_dm = DATA_MODE_CASES.get(platform, {}).get(
                observation_no, ("R", "R")
            )
            rows.append(
                {
                    "platform_code": platform,
                    "profile_no": 1,
                    "observation_no": observation_no,
                    "pres": 5.0 * observation_no,
                    "temp": 10.0 - observation_no,
                    "temp_qc": temp_qc,
                    "temp_nrt_flag": temp_nrt,
                    "temp_dm": temp_dm,
                    "pres_dm": pres_dm,
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
                "data_mode": {"columns": ["temp_dm", "pres_dm"]},
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
