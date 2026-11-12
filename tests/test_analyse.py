"""The analyses: how profiles are grouped, and what the tables count."""

import json
import os
from datetime import datetime

import polars as pl
import pytest
import yaml

from nrtqc_diff.analyse import (
    COVERAGE_CLASSES,
    check_conversion_steps,
    classify,
    dropped_tables,
    input_flag_table,
    land_causes,
    match_tables,
    overlap_table,
    probable_flag_table,
    response_table,
    run_analyses,
    shape_table,
)
from nrtqc_diff.config import read_config
from nrtqc_diff.reports import dropped_platform_table

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
    checkpres = reports / "convert" / "checkpres"
    checkpres.mkdir()
    (checkpres / "src.summary.tsv").write_text(
        "n_files\tn_platforms\tn_profiles\tn_no_pres\tn_bad_profiles\tpct_bad"
        "\tn_reversed\tn_non_monotonic\tn_bad_platforms\tn_bad_files\n"
        "3\t2\t9\t1\t3\t33.3\t1\t2\t1\t2\n",
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
    dups = tmp_path / "ctddump" / "dups"
    dups.mkdir(parents=True)
    columns = "dup_group\tplatform_code\tprofile_no\tn_obs\n"
    (dups / "src.dups.tsv").write_text(
        columns
        + "1\tA\t1\t5\n1\tA\t2\t5\n1\tA\t3\t4\n2\tA\t4\t3\n2\tB\t1\t3\n"
        + "3\tA\t5\t3\n3\tA\t6\t3\n3\tC\t1\t3\n",
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
                # The profiles of CASES are short, so the minimum is lowered.
                "data_mode": {
                    "columns": ["temp_dm", "pres_dm"],
                    "estimated": {"min_observations": 5},
                },
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
                    "ctddump_dups": "ctddump/dups",
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


def test_input_flags_count_every_observation(analysed):
    flags = table(analysed, "input_flags")
    # Land, delayed and estimated profiles too: the whole input is counted.
    expected = {1: 0, 4: 0}
    for _, observations in CASES.values():
        for flag, *_ in observations:
            expected[flag] += 1
    assert dict(zip(flags["original_flag"], flags["n_obs"])) == expected
    assert flags["has_position_qc"].to_list() == [True, True]


def test_input_flags_keep_profiles_without_a_position_flag(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "site": {"data_dir": "out"},
                "variables": [
                    {"name": "temp", "flag": "temp_qc", "nrt_flag": "temp_nrt_flag"}
                ],
                "data_mode": {"columns": ["temp_dm"]},
                "datasets": [
                    {"id": "one", "region": "R", "product": "P", "path": "x.parquet"}
                ],
            }
        ),
        encoding="utf-8",
    )
    spec = read_config(str(config))
    scan = pl.LazyFrame(
        {
            "temp_qc": ["1", "4", "", "1"],
            "position_qc": ["1", "1", "1", ""],
        }
    )
    rows = input_flag_table(scan, spec.datasets[0], spec).select(
        "original_flag", "has_position_qc", "n_obs"
    )
    assert rows.rows() == [(1, False, 1), (1, True, 1), (4, True, 1), (None, True, 1)]


def test_probable_flags_are_counted_by_mode_group_and_check(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "site": {"data_dir": "out"},
                "variables": [
                    {"name": "temp", "flag": "temp_qc", "nrt_flag": "temp_nrt_flag"}
                ],
                "data_mode": {"columns": ["temp_dm"]},
                "datasets": [
                    {"id": "one", "region": "R", "product": "P", "path": "x.parquet"}
                ],
            }
        ),
        encoding="utf-8",
    )
    spec = read_config(str(config))
    scan = pl.LazyFrame(
        {
            "platform_code": ["A", "A", "A", "B", "B", "C"],
            "profile_no": [1, 1, 1, 1, 1, 1],
            "temp_qc": ["3", "3", "1", "2", "3", "3"],
            "temp_dm": ["R", "R", "R", "D", None, "R"],
            "temp_nrt_flag": [4, 1, 1, 1, 4, 4],
            "temp_qc_spike": [4, 1, 1, 1, 1, 4],
        }
    )
    # C is on land, so its 3 is left out.
    profiles = pl.DataFrame(
        {
            "platform_code": ["A", "B", "C"],
            "profile_no": [1, 1, 1],
            "land": [False, False, True],
            "temp_group": ["nrt", "delayed", "land"],
        }
    )
    rows = {
        (r["flag"], r["obs_data_mode"], r["group"], r["nrtqclib_bad"]): r
        for r in probable_flag_table(scan, profiles, spec.datasets[0], spec).iter_rows(
            named=True
        )
    }
    assert set(rows) == {
        (3, "R", "nrt", True),
        (3, "R", "nrt", False),
        (2, "D", "delayed", False),
        (3, "", "delayed", True),
    }
    assert rows[(3, "R", "nrt", True)]["n_obs"] == 1
    assert rows[(3, "R", "nrt", True)]["fail_spike"] == 1
    assert rows[(3, "", "delayed", True)]["fail_spike"] == 0


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


def test_the_reports_are_read(analysed):
    steps = table(analysed, "conversion_steps")
    assert steps["step"].to_list() == ["convert", "dedup"]
    assert steps["n_platforms"].to_list() == [2, 2]
    assert steps["n_profiles"].to_list() == [9, 6]
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
    order = table(analysed, "conversion_pressure_order").row(0, named=True)
    assert order["dataset_id"] == "one"
    assert (order["n_profiles"], order["n_reversed"], order["n_non_monotonic"]) == (
        9,
        1,
        2,
    )
    assert "pct_bad" not in order


def test_duplicates_are_split_within_and_across_platforms(analysed):
    kinds = table(analysed, "conversion_dup_kinds")
    by_kind = {row["kind"]: row for row in kinds.iter_rows(named=True)}
    assert by_kind["within"]["n_groups"] == 1
    assert by_kind["within"]["n_profiles"] == 3
    assert by_kind["within"]["n_removed"] == 2
    assert by_kind["across"]["n_removed"] == 1
    assert by_kind["both"]["n_removed"] == 2
    assert kinds["kind"].to_list() == ["within", "across", "both"]
    sets = table(analysed, "conversion_dup_platforms")
    assert sorted(sets["platforms"].to_list()) == ["A + B", "A + C"]
    sizes = table(analysed, "conversion_dup_sizes")
    assert dict(zip(sizes["kind"] + sizes["size_label"], sizes["n_groups"])) == {
        "within3": 1,
        "across2": 1,
        "both3": 1,
    }


def test_the_step_check_reports_a_difference():
    steps = pl.DataFrame(
        {
            "dataset_id": ["a", "a"],
            "step_order": [0, 1],
            "n_profiles": [5, 3],
            "n_obs": [9, 6],
        }
    )
    same = pl.DataFrame({"dataset_id": ["a"] * 3, "n_obs": [2, 2, 2]})
    assert check_conversion_steps(steps, same) == []
    fewer = same.head(2)
    assert "holds 2" in check_conversion_steps(steps, fewer)[0]


def test_overlap_matches_with_and_without_the_platform(tmp_path):
    when = [datetime(2020, 1, 1, 6), datetime(2020, 1, 1, 18), datetime(2020, 1, 2)]
    profiles = pl.DataFrame(
        {
            "dataset_id": ["a", "a", "a", "b", "b"],
            "platform_code": ["P", "Q", "R", "P", "X"],
            "profile_no": [1, 1, 1, 1, 1],
            "profile_timestamp": when + [when[0], when[1]],
            "longitude": [1.0, 2.0, 3.0, 1.0004, 2.0],
            "latitude": [50.0, 50.0, 50.0, 50.0, 50.0],
        }
    )
    config = _two_dataset_config(tmp_path)
    overlap = overlap_table(profiles, config)
    row = overlap.filter(pl.col("dataset_id") == "a").row(0, named=True)
    assert row["compared_id"] == "b"
    assert row["n_profiles"] == 3
    assert row["n_matched_same_platform"] == 1
    assert row["n_matched_any_platform"] == 2


def test_a_pair_either_side_of_a_rounding_boundary_still_matches(tmp_path):
    """Rounded to 3 decimals these would be 2.001 and 2.000, and miss."""
    when = datetime(2020, 1, 1, 6)
    profiles = pl.DataFrame(
        {
            "dataset_id": ["a", "b"],
            "platform_code": ["P", "P"],
            "profile_no": [1, 7],
            "profile_timestamp": [when, when],
            "longitude": [2.0005, 2.00049],
            "latitude": [50.0, 50.0],
        }
    )
    overlap = overlap_table(profiles, _two_dataset_config(tmp_path))
    assert overlap["n_matched_same_platform"].to_list() == [1, 1]


def test_matched_profiles_are_compared_with_the_delayed_product(tmp_path):
    when = datetime(2020, 1, 1, 6)
    profiles = pl.DataFrame(
        {
            "dataset_id": ["a", "a", "a", "b", "b", "b"],
            "platform_code": ["P", "P", "Q", "P", "P", "Q"],
            "profile_no": [1, 2, 1, 1, 2, 1],
            "profile_timestamp": [when, when.replace(day=2), when] * 2,
            "longitude": [1.0, 1.0, 5.0, 1.0, 1.0, 5.0],
            "latitude": [50.0] * 6,
            "n_obs": [10, 11, 20, 10, 10, 25],
        }
    )
    tables = match_tables(profiles, _two_dataset_config(tmp_path, delayed="b"))
    summary = tables["conversion_match"].row(0, named=True)
    assert (summary["dataset_id"], summary["compared_id"]) == ("a", "b")
    assert summary["n_matched"] == 3
    assert summary["n_same_obs"] == 1
    assert summary["n_more_in_dataset"] == 1
    assert summary["n_more_in_compared"] == 1
    assert summary["coverage"] == "same_platform"


def test_each_nrt_profile_is_filed_by_whether_the_delayed_product_has_it(tmp_path):
    when = datetime(2020, 1, 1, 6)
    later = datetime(2021, 6, 1, 6)
    rows = [
        # dataset, platform, profile, time, longitude
        ("a", "P", 1, when, 1.0),  # same platform, date and place
        ("a", "P", 2, when, 2.0),  # under another code in b
        ("a", "P", 3, when, 3.0),  # 0.005 degrees off: nearby
        ("a", "P", 4, when, 4.0),  # P is in b, this cast is not
        ("a", "Z", 1, when, 5.0),  # Z is not in b at all
        ("a", "P", 5, later, 1.0),  # after b's last profile
        ("b", "P", 1, when, 1.0),
        ("b", "Q", 1, when, 2.0),
        ("b", "P", 3, when, 3.005),
        ("b", "P", 9, datetime(2019, 1, 1), 9.0),
    ]
    profiles = pl.DataFrame(
        rows,
        schema=[
            "dataset_id",
            "platform_code",
            "profile_no",
            "profile_timestamp",
            "longitude",
        ],
        orient="row",
    ).with_columns(pl.lit(50.0).alias("latitude"), pl.lit(10).alias("n_obs"))
    tables = match_tables(profiles, _two_dataset_config(tmp_path, delayed="b"))
    row = tables["conversion_coverage"].row(0, named=True)
    assert row["n_profiles"] == 6
    assert {name: row[f"n_{name}"] for name in COVERAGE_CLASSES} == {
        "same_platform": 1,
        "other_platform": 1,
        "nearby": 1,
        "platform_only": 1,
        "no_platform": 1,
        "outside_period": 1,
    }
    assert str(row["period_end"]) == "2020-01-01"
    summary = tables["conversion_match"]
    assert dict(zip(summary["coverage"], summary["n_matched"])) == {
        "same_platform": 1,
        "other_platform": 1,
    }
    years = tables["conversion_coverage_years"]
    assert years.filter(pl.col("year") == 2021)["coverage"].to_list() == [
        "outside_period"
    ]


def test_land_profiles_are_filed_by_their_likely_cause():
    rows = [
        # longitude, latitude, elevation (m), distance to the coast (km)
        (10.0, 57.0, 40.0, 12.0),  # whole degrees, whatever else holds
        (10.2, 57.3, 3.0, 0.4),  # low and close: the shore
        (10.2, 57.4, 0.0, 6.0),  # low but far from the coast
        (10.2, 57.5, 6.0, 0.2),  # higher than the shore allows
        (10.2, 57.5, 6.0, 0.2),  # the same position again
    ]
    on_land = pl.DataFrame(
        rows,
        schema=["longitude", "latitude", "elevation", "dist_to_coast"],
        orient="row",
    ).with_columns(
        pl.lit("a").alias("dataset_id"),
        pl.lit("R").alias("region"),
        pl.lit("P").alias("product"),
        pl.lit("A").alias("label"),
    )
    causes = land_causes(on_land)
    assert causes["cause"].to_list() == ["whole_degrees", "shore", "low_far", "inland"]
    assert causes["n_profiles"].to_list() == [1, 1, 1, 2]
    assert causes["n_positions"].to_list() == [1, 1, 1, 1]


def test_each_variable_is_estimated_on_its_own(tmp_path):
    """Salinity reworked, temperature not: out of the salinity comparison only."""
    path = tmp_path / "c.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "variables": [
                    {"name": n, "flag": f"{n}_qc", "nrt_flag": f"{n}_nrt"}
                    for n in ("temp", "psal")
                ],
                "data_mode": {"columns": ["temp_dm"]},
                "datasets": [{"id": "a", "region": "R", "product": "P", "path": "a"}],
                "analyses": {"out_dir": "out"},
            }
        ),
        encoding="utf-8",
    )
    config = read_config(str(path))
    profiles = pl.DataFrame(
        {
            "dataset_id": ["a", "a", "a"],
            "platform_code": ["SAL", "BOTH", "DM"],
            "profile_no": [1, 1, 1],
            "elevation": [-50.0] * 3,
            "data_mode": ["nrt", "nrt", "delayed"],
            "temp_n_usable": [10, 10, 10],
            "temp_n_original_only": [0, 9, 9],
            "psal_n_usable": [10, 10, 10],
            "psal_n_original_only": [9, 9, 9],
        }
    )
    groups = {
        row["platform_code"]: (row["temp_group"], row["psal_group"])
        for row in classify(profiles, config, config.analyses).iter_rows(named=True)
    }
    assert groups == {
        "SAL": ("nrt", "estimated_delayed"),
        "BOTH": ("estimated_delayed", "estimated_delayed"),
        "DM": ("delayed", "delayed"),
    }


def _two_dataset_config(tmp_path, delayed=None):
    path = tmp_path / "c.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "variables": [{"name": "t", "flag": "t_qc", "nrt_flag": "t_nrt"}],
                "data_mode": {"columns": ["t_dm"]},
                "datasets": [
                    {
                        "id": name,
                        "region": "R",
                        "product": f"P{number}",
                        "path": name,
                        **({"data_mode": "delayed"} if name == delayed else {}),
                    }
                    for number, name in enumerate(["a", "b"], 1)
                ],
                "analyses": {"out_dir": "out"},
            }
        ),
        encoding="utf-8",
    )
    return read_config(str(path))


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
                "analyses": {"out_dir": "o"},
            }
        ),
        encoding="utf-8",
    )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["data_mode"]["estimated"] = {"min_share": 60}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="at most 1"):
        read_config(str(path))


def test_the_rule_in_the_analyses_section_points_to_its_new_place(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "variables": [{"name": "t", "flag": "t_qc", "nrt_flag": "t_nrt"}],
                "data_mode": {"columns": ["t_dm"]},
                "datasets": [{"id": "a", "region": "R", "product": "P", "path": "x"}],
                "analyses": {"out_dir": "o", "estimated_delayed": {"min_share": 0.6}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="moved to data_mode.estimated.min_share"):
        read_config(str(path))


def test_a_dataset_filed_whole_is_left_out_of_the_data_mode_tables(analysed):
    """A product like CORA has no NRT profile to estimate, so it is skipped."""
    config = analysed.parent / "config.yaml"
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    whole = dict(raw["datasets"][0], id="whole", data_mode="delayed")
    raw["datasets"].append(whole)
    raw["analyses"]["out_dir"] = "analyses_whole"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    run_analyses(read_config(str(config)))
    out = analysed.parent / "analyses_whole"

    for name in ["dm_groups", "dm_share_hist", "dm_threshold"]:
        assert set(table(out, name)["dataset_id"]) == {"one"}, name
    # The other pages still count it.
    assert set(table(out, "depth_summary")["dataset_id"]) == {"one", "whole"}


def test_one_sided_flags_are_placed_in_their_profiles(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "site": {"data_dir": "out"},
                "variables": [
                    {"name": "temp", "flag": "temp_qc", "nrt_flag": "temp_nrt_flag"}
                ],
                "data_mode": {"columns": ["temp_dm"]},
                "datasets": [
                    {"id": "one", "region": "R", "product": "P", "path": "x.parquet"}
                ],
            }
        ),
        encoding="utf-8",
    )
    spec = read_config(str(config))
    # A: the input flags the top level (at negative pressure), one level
    # inside and the bottom one; nrtqclib flags two levels inside.
    # B: the input flags all of it. C is not kept.
    scan = pl.LazyFrame(
        {
            "platform_code": ["A"] * 6 + ["B"] * 2 + ["C"],
            "profile_no": [1] * 9,
            "pres": [-1.0, 5.0, 10.0, 15.0, 20.0, 25.0, 1.0, 2.0, 1.0],
            "temp_qc": ["4", "1", "3", "1", "1", "4", "4", "4", "4"],
            "temp_nrt_flag": [1, 1, 1, 4, 4, 1, 1, 1, 1],
        }
    )
    keep = {"temp": pl.DataFrame({"platform_code": ["A", "B"], "profile_no": [1, 1]})}
    rows = {
        (r["status"], r["shape"], r["negative_pressure"]): (r["n_obs"], r["n_profiles"])
        for r in shape_table(scan, keep, spec.datasets[0], spec).iter_rows(named=True)
    }
    assert rows == {
        ("original_only", "top", True): (1, 1),
        ("original_only", "inside_alone", False): (1, 1),
        ("original_only", "bottom", False): (1, 1),
        ("original_only", "whole", False): (2, 1),
        ("nrtqclib_only", "inside_several", False): (2, 1),
    }


def test_failures_are_banded_by_how_far_past_the_threshold(tmp_path, monkeypatch):
    # Slices of four rows, so that profile A is cut by a slice edge and has
    # to be carried over whole.
    monkeypatch.setattr("nrtqc_diff.analyse.RESPONSE_CHUNK_ROWS", 4)
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "site": {"data_dir": "out"},
                "variables": [
                    {"name": "temp", "flag": "temp_qc", "nrt_flag": "temp_nrt_flag"},
                    {"name": "psal", "flag": "psal_qc", "nrt_flag": "psal_nrt_flag"},
                ],
                "data_mode": {"columns": ["temp_dm"]},
                "datasets": [
                    {"id": "one", "region": "R", "product": "P", "path": "x.parquet"}
                ],
            }
        ),
        encoding="utf-8",
    )
    spec = read_config(str(config))
    # A: a temperature spike of 2.5 times the threshold at its third level,
    # which the input flags, and a stuck salinity of five values it does
    # not. B: a temperature 10 above the global range at the top, flagged,
    # and a repeated pressure at the bottom, flagged in the pressure flag
    # only, and a NaN temperature, which is missing rather than out of
    # range. C is not compared, and its outlier must not count.
    scan = pl.LazyFrame(
        {
            "platform_code": ["A"] * 5 + ["B"] * 3 + ["C"],
            "profile_no": [1] * 9,
            "observation_no": [1, 2, 3, 4, 5, 1, 2, 3, 1],
            "pres": [1.0, 2.0, 3.0, 4.0, 5.0, 1.0, 2.0, 2.0, 1.0],
            "temp": [10.0, 10.0, 25.0, 10.0, 10.0, 50.0, 10.0, float("nan"), 100.0],
            "psal": [35.0] * 5 + [35.0, 35.1, 35.2, 35.0],
            "temp_qc": ["1", "1", "4", "1", "1", "4", "1", "1", "1"],
            "psal_qc": ["1"] * 9,
            "pres_qc": ["1"] * 7 + ["4", "1"],
        }
    )
    keep = {
        name: pl.DataFrame({"platform_code": ["A", "B"], "profile_no": [1, 1]})
        for name in ["temp", "psal"]
    }
    table = response_table(scan, keep, spec.datasets[0], spec)
    rows = {
        (r["test"], r["variable"], r["band"]): (r["n"], r["n_flagged"])
        for r in table.iter_rows(named=True)
    }
    assert rows[("global_range", "temp", 0)] == (6, 1)
    assert rows[("global_range", "temp", 3)] == (1, 1)
    assert ("global_range", "temp", 4) not in rows
    assert rows[("spike", "temp", 3)] == (1, 1)
    assert rows[("stuck_value", "psal", 2)] == (1, 0)
    assert rows[("stuck_value", "psal", 0)] == (1, 0)
    assert rows[("stuck_value", "temp", 0)] == (1, 0)
    assert rows[("density_inversion", "density", 5)] == (1, 1)
    assert rows[("density_inversion", "density", 0)] == (3, 1)
    assert rows[("pressure_increasing", "pres", 1)] == (1, 1)
    assert rows[("pressure_increasing", "temp", 1)] == (1, 0)


def test_dropped_profiles_are_placed_where_the_platform_is_fixed(tmp_path):
    """
    A fixed platform's dropped profiles take the batch's stamp of its one
    point; a moving platform's, or a fixed one the batch does not hold at
    that point, stay unplaced.
    """
    columns = (
        "platform_code\tn_profiles\tlongitude_min\tlongitude_max"
        "\tlatitude_min\tlatitude_max\n"
    )
    before = (
        "LAND\t5\t10.5\t10.5\t57.5\t57.5\n"  # fixed, on land, inland
        "SEA\t4\t11.25\t11.25\t57.25\t57.25\n"  # fixed, at sea
        "MOVE\t3\t10\t12\t56\t58\n"  # moving
        "GONE\t2\t9.5\t9.5\t55.5\t55.5\n"  # fixed, every profile dropped
        "AWAY\t2\t9.25\t9.25\t55.25\t55.25\n"  # fixed, batch elsewhere
        "KEPT\t6\t9\t9\t55\t55\n"  # nothing dropped
    )
    after = "LAND\t3\nSEA\t3\nMOVE\t1\nAWAY\t1\nKEPT\t6\n"
    reports = tmp_path / "report"
    (reports / "convert").mkdir(parents=True)
    (reports / "clean" / "dropqc").mkdir(parents=True)
    (reports / "convert" / "src.parquet.tsv").write_text(columns + before)
    (reports / "clean" / "dropqc" / "src.parquet.tsv").write_text(
        "platform_code\tn_profiles\n" + after
    )
    pl.DataFrame(
        {
            "platform_code": ["LAND", "SEA", "MOVE", "AWAY", "KEPT"],
            "longitude": [10.5, 11.25, 11.0, 9.75, 9.0],
            "latitude": [57.5, 57.25, 57.0, 55.25, 55.0],
            "bathymetry": [30.0, -20.0, -50.0, 5.0, -10.0],
            "dist_to_coast": [8.0, 3.0, 10.0, 1.0, 2.0],
        }
    ).write_parquet(tmp_path / "input.parquet")
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "site": {"data_dir": "out"},
                "variables": [
                    {"name": "temp", "flag": "temp_qc", "nrt_flag": "temp_nrt_flag"}
                ],
                "datasets": [
                    {
                        "id": "one",
                        "region": "R",
                        "product": "P",
                        "path": "input.parquet",
                        "source": "src",
                    }
                ],
                "data_mode": {"columns": ["temp_dm"]},
                "analyses": {"out_dir": "analyses", "ctddump_reports": "report"},
            }
        ),
        encoding="utf-8",
    )
    config = read_config(str(config))
    drops = dropped_platform_table(config, config.analyses.ctddump_reports)
    assert drops.height == 6
    tables = dropped_tables(drops, config, config.analyses)

    platforms = tables["conversion_dropqc_platforms"]
    status = dict(zip(platforms["platform_code"], platforms["status"]))
    assert status == {
        "AWAY": "not_stamped",
        "GONE": "not_stamped",
        "LAND": "land",
        "MOVE": "moving",
        "SEA": "sea",
    }
    cause = dict(zip(platforms["platform_code"], platforms["cause"]))
    assert cause["LAND"] == "inland" and cause["SEA"] is None

    summary = tables["conversion_dropqc"].row(0, named=True)
    assert summary["n_profiles"] == 22
    assert summary["n_dropped"] == 2 + 1 + 2 + 2 + 1
    assert (
        summary["n_moving"],
        summary["n_not_stamped"],
        summary["n_sea"],
        summary["n_land"],
    ) == (2, 3, 1, 2)
    assert summary["n_land_inland"] == 2 and summary["n_land_shore"] == 0


def test_a_profile_is_set_against_the_platform_s_previous_one(tmp_path, monkeypatch):
    """
    The frozen profile test: an exact repeat, one within Argo's limits and
    one that differs; a repeat at the same time is a duplicate and one of
    another platform is not compared, nor are a stuck profile and one
    outside the range, even repeated. Slices of four rows cut between the
    profiles, so the previous profile has to come from the slice before.
    """
    monkeypatch.setattr("nrtqc_diff.analyse.RESPONSE_CHUNK_ROWS", 4)
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "site": {"data_dir": "out"},
                "variables": [
                    {"name": "temp", "flag": "temp_qc", "nrt_flag": "temp_nrt_flag"},
                    {"name": "psal", "flag": "psal_qc", "nrt_flag": "psal_nrt_flag"},
                ],
                "data_mode": {"columns": ["temp_dm"]},
                "datasets": [
                    {"id": "one", "region": "R", "product": "P", "path": "x.parquet"}
                ],
            }
        ),
        encoding="utf-8",
    )
    spec = read_config(str(config))
    # (platform, profile, time, temperatures, temperature flag)
    profiles = [
        ("A", 1, 0.0, [10.0, 9.0, 8.0], "1"),
        ("A", 2, 1.0, [10.0, 9.0, 8.0], "4"),  # exact repeat, flagged
        ("A", 3, 2.0, [10.005, 9.0005, 8.0], "1"),  # within Argo's limits
        ("A", 4, 3.0, [12.0, 9.0, 8.0], "1"),  # different
        ("A", 5, 3.0, [12.0, 9.0, 8.0], "1"),  # same time: a duplicate
        ("B", 1, 4.0, [12.0, 9.0, 8.0], "1"),  # another platform
        ("B", 2, 5.0, [5.0, 5.0, 5.0], "1"),  # stuck
        ("B", 3, 6.0, [5.0, 5.0, 5.0], "4"),  # stuck, repeated
        ("B", 4, 7.0, [-5.6, -5.6, -4.0], "4"),  # outside the range
        ("B", 5, 8.0, [-5.6, -5.6, -4.0], "4"),  # outside, repeated
    ]
    rows = []
    for i, (platform, number, time, temps, flag) in enumerate(profiles):
        for level, temp in enumerate(temps):
            rows.append(
                {
                    "platform_code": platform,
                    "profile_no": number,
                    "profile_time": time,
                    "observation_no": level + 1,
                    "pres": [10.0, 60.0, 110.0][level],
                    "temp": temp,
                    # Salinity changes from one profile to the next.
                    "psal": 30.0 + i + 0.1 * level,
                    "temp_qc": flag,
                    "psal_qc": "1",
                }
            )
    keep = {
        name: pl.DataFrame(
            {
                "platform_code": [p[0] for p in profiles],
                "profile_no": [p[1] for p in profiles],
            }
        )
        for name in ["temp", "psal"]
    }
    table = response_table(pl.LazyFrame(rows), keep, spec.datasets[0], spec)
    frozen = {
        (r["variable"], r["band"]): (r["n"], r["n_flagged"])
        for r in table.filter(pl.col("test") == "frozen_profile").iter_rows(named=True)
    }
    assert frozen == {
        ("temp", 0): (1, 0),
        ("temp", 1): (1, 0),
        ("temp", 2): (1, 1),
        ("psal", 0): (7, 0),
    }
