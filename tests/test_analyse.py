"""The analyses: how profiles are grouped, and what the tables count."""

import json
import os

import polars as pl
import pytest
import yaml

from nrtqc_diff.analyse import run_analyses
from nrtqc_diff.config import read_config

#: ``platform: (temp_dm, [(temp_qc, temp_nrt_flag, spike, date, elevation)])``.
#: Each platform is one profile built to land in one group.
CASES = {
    # One position the bathymetry puts 3 m above sea level: on land, so it is
    # left out whatever its flags say.
    "LAND": ("R", [(4, 1, 1, 1, -10.0), (4, 1, 1, 1, 3.0)] + [(1, 1, 1, 1, -10.0)] * 4),
    # NRT, but five of six observations flagged by the input alone: what an
    # operator's edits look like, so estimated delayed mode.
    "OPER": ("R", [(4, 1, 1, 1, -50.0)] * 5 + [(1, 1, 1, 1, -50.0)]),
    # The same pattern on three observations is too few to call.
    "FEW": ("R", [(4, 1, 1, 1, -50.0)] * 3),
    # Delayed mode already: filed as delayed, never as estimated.
    "DELAY": ("D", [(4, 1, 1, 1, -50.0)] * 6),
    # No data mode, and the operator pattern: estimated as well.
    "UNK": ("", [(4, 1, 1, 1, -50.0)] * 6),
    # Plain NRT: one observation the spike test alone flags, one the spike
    # and date tests both flag, one both sources flag, the rest good.
    "NRT": (
        "R",
        [
            (1, 4, 4, 1, -50.0),
            (1, 4, 4, 4, -50.0),
            (4, 4, 4, 1, -50.0),
        ]
        + [(1, 1, 1, 1, -50.0)] * 3,
    ),
}


@pytest.fixture
def analysed(tmp_path):
    """
    Run the analyses over :data:`CASES`, with small ctddump and seastamp
    reports beside them.

    :return: The output directory.
    """
    rows = []
    for platform, (temp_dm, observations) in sorted(CASES.items()):
        for number, (flag, nrt, spike, date, height) in enumerate(observations, 1):
            rows.append(
                {
                    "platform_code": platform,
                    "profile_no": 1,
                    "observation_no": number,
                    "pres": 30.0 * number,
                    "temp": 5.0,
                    "temp_qc": flag,
                    "temp_nrt_flag": nrt,
                    "temp_dm": temp_dm,
                    "pres_dm": "R" if temp_dm != "D" else "D",
                    "temp_qc_spike": spike,
                    "qc_impossible_date": date,
                    "bathymetry": height,
                    "dist_to_coast": 0.5,
                    "position_qc": 1,
                    "longitude": 20.0,
                    "latitude": 58.0,
                }
            )
    pl.DataFrame(rows).write_parquet(tmp_path / "input.parquet")

    reports = tmp_path / "ctddump" / "report"
    header_report = reports / "header"
    header_report.mkdir(parents=True)
    (header_report / "src.yaml.tsv").write_text(
        "filename\thas_temp\textra_params\nA\ttrue\tDOX1;CNDC\nB\tfalse\tDOX1\n",
        encoding="utf-8",
    )
    header = "platform_code\tn_profiles\tn_obs\ttemp_dm_r\ttemp_dm_d\n"
    for step, profiles in (("convert", 9), ("dedup/dedup", 6)):
        folder = reports / step
        folder.mkdir(parents=True)
        (folder / "src.parquet.tsv").write_text(
            header + f"A\t{profiles - 1}\t30\t20\t10\nB\t1\t2\t2\t0\nC\t0\t0\t0\t0\n",
            encoding="utf-8",
        )
    headers = tmp_path / "ctddump" / "header"
    headers.mkdir(parents=True)
    (headers / "src.yaml").write_text(
        "FILE_1:\n  global_attributes:\n    data_mode: M\n    title: x\n"
        "  variables:\n    TEMP:\n      data_mode: R\n"
        "FILE_2:\n  global_attributes:\n    title: y\n",
        encoding="utf-8",
    )
    stamp = tmp_path / "seastamp"
    stamp.mkdir()
    (stamp / "src.tsv").write_text(
        "column\tkind\titem\tunit\trows\tlocations\tmin\tmax\tmean\n"
        "bathymetry\tdepth\tland\tm\t1\t1\t\t\t\n",
        encoding="utf-8",
    )

    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "site": {"data_dir": "out"},
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
                        "source": "src",
                    }
                ],
                "analyses": {
                    "out_dir": "analyses",
                    "ctddump_reports": "ctddump/report",
                    "ctddump_headers": "ctddump/header",
                    "seastamp_reports": "seastamp",
                },
            }
        ),
        encoding="utf-8",
    )
    run_analyses(read_config(str(config)))
    return tmp_path / "analyses"


def table(out, name):
    """Read one analysis table."""
    return pl.read_csv(out / "tables" / f"{name}.csv", infer_schema_length=None)


def test_every_profile_lands_in_its_group(analysed):
    groups = table(analysed, "dm_groups")
    found = {
        (row["data_mode"], row["group"]): row["n_profiles"]
        for row in groups.iter_rows(named=True)
    }
    assert found == {
        ("nrt", "land"): 1,
        ("nrt", "estimated_delayed"): 1,
        ("nrt", "nrt"): 2,
        ("delayed", "delayed"): 1,
        ("unknown", "estimated_delayed"): 1,
    }
    listed = table(analysed, "dm_estimated_profiles")["platform_code"].to_list()
    assert listed == ["OPER", "UNK"]


def test_a_short_profile_has_no_share(analysed):
    """FEW is in the NRT group but absent from the share histogram."""
    hist = table(analysed, "dm_share_hist")
    assert hist.filter(pl.col("data_mode") == "nrt")["n_profiles"].sum() == 2


def test_the_threshold_table_counts_at_or_above(analysed):
    thresholds = table(analysed, "dm_threshold").filter(pl.col("data_mode") == "nrt")
    by = dict(zip(thresholds["threshold"], thresholds["n_profiles"]))
    # OPER's share is 5/6, NRT's 0; neither reaches 1.
    assert by[0.6] == 1 and by[0.8] == 1 and by[0.9] == 0


def test_land_profiles_are_counted_with_their_flags(analysed):
    summary = table(analysed, "depth_summary").row(0, named=True)
    assert summary["n_land"] == 1
    assert summary["n_land_position_bad"] == 0
    assert summary["n_land_temp_any_bad"] == 1
    margin = table(analysed, "depth_land_margin")
    assert dict(zip(margin["margin_m"], margin["n_profiles"]))[5.0] == 0
    flags = table(analysed, "depth_land_flags")
    assert dict(zip(flags["original_flag"], flags["n_obs"])) == {1: 4, 4: 2}


def test_items_are_attributed_over_nrt_profiles_only(analysed):
    status = table(analysed, "items_status")
    counts = dict(zip(status["status"], status["n_obs"]))
    # NRT's six observations plus FEW's three, which only the input flags.
    assert counts == {
        "nrtqclib_only": 2,
        "agree_bad": 1,
        "agree_good": 3,
        "original_only": 3,
    }
    items = {
        row["item"]: row
        for row in table(analysed, "items_by_item").iter_rows(named=True)
    }
    assert items["spike"]["n_fail"] == 3
    assert items["spike"]["n_fail_nrtqclib_only"] == 2
    assert items["spike"]["n_alone_nrtqclib_only"] == 1
    assert items["impossible_date"]["n_fail_nrtqclib_only"] == 1
    combos = table(analysed, "items_combinations")
    pairs = {
        (row["status"], row["combination"]): row["n_obs"]
        for row in combos.iter_rows(named=True)
    }
    assert pairs[("nrtqclib_only", "spike+impossible_date")] == 1
    assert pairs[("nrtqclib_only", "spike")] == 1


def test_the_reports_are_read_and_the_output_step_added(analysed):
    steps = table(analysed, "conversion_steps")
    assert steps["step"].to_list() == ["convert", "dedup", "nrtqc"]
    assert steps["n_platforms"].to_list() == [2, 2, 6]
    assert steps["n_profiles"].to_list() == [9, 6, 6]
    modes = table(analysed, "conversion_file_data_mode")
    assert dict(zip(modes["data_mode"].fill_null(""), modes["n_files"])) == {
        "M": 1,
        "": 1,
    }
    assert table(analysed, "depth_stamp")["item"].to_list() == ["land"]
    extra = table(analysed, "conversion_extra_parameters")
    assert dict(zip(extra["parameter"], extra["n_files"])) == {"DOX1": 2, "CNDC": 1}
    files = table(analysed, "conversion_files")
    assert dict(zip(files["item"], files["n_with"])) == {"temp": 1}


def test_meta_lists_the_tables_and_rules(analysed):
    meta = json.loads((analysed / "meta.json").read_text(encoding="utf-8"))
    written = sorted(name[:-4] for name in os.listdir(analysed / "tables"))
    assert meta["tables"] == written
    assert meta["rules"]["estimated_delayed"]["min_share"] == 0.6
    assert [g["key"] for g in meta["groups"]][0] == "land"


def test_report_folders_follow_symbolic_links(tmp_path):
    """``link/../reports`` means beside where the link points."""
    target = tmp_path / "elsewhere" / "batch"
    target.mkdir(parents=True)
    (tmp_path / "elsewhere" / "reports").mkdir()
    (tmp_path / "repo" / "config").mkdir(parents=True)
    os.symlink(target, tmp_path / "repo" / "data")
    path = tmp_path / "repo" / "config" / "c.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "variables": [{"name": "t", "flag": "t_qc", "nrt_flag": "t_nrt"}],
                "data_mode": {"columns": ["t_dm"]},
                "datasets": [{"id": "a", "region": "R", "product": "P", "path": "x"}],
                "analyses": {
                    "out_dir": "out",
                    "ctddump_reports": "../data/../reports",
                },
            }
        ),
        encoding="utf-8",
    )
    spec = read_config(str(path)).analyses
    assert spec.ctddump_reports == os.path.realpath(tmp_path / "elsewhere" / "reports")


def test_a_share_outside_the_unit_interval_is_refused(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "variables": [{"name": "t", "flag": "t_qc", "nrt_flag": "t_nrt"}],
                "data_mode": {"columns": ["t_dm"]},
                "datasets": [{"id": "a", "region": "R", "product": "P", "path": "x"}],
                "analyses": {"out_dir": "o", "estimated_delayed": {"min_share": 60}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="at most 1"):
        read_config(str(path))
