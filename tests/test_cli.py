"""The command line: what it passes on to the build."""

import pyarrow.parquet as pq
import yaml

from nrtqc_diff.cli import main


def test_an_output_override_keeps_every_other_setting(tmp_path, nrt_qc_frame):
    """`-o` replaces the data directory and nothing else."""
    input_path = tmp_path / "input.parquet"
    nrt_qc_frame.write_parquet(input_path)
    config_path = tmp_path / "datasets.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "site": {"data_dir": "unused", "row_group_size": 4},
                "variables": [
                    {"name": "temp", "flag": "temp_qc", "nrt_flag": "temp_nrt_flag"}
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
        ),
        encoding="utf-8",
    )

    out = tmp_path / "elsewhere"
    assert main(["build", "-c", str(config_path), "-o", str(out), "-q"]) == 0
    written = pq.ParquetFile(out / "obs" / "one.parquet").metadata
    assert written.num_row_groups == 4
    assert not (tmp_path / "unused").exists()
