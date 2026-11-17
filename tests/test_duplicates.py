"""The duplicate groups and shared casts, checked against time and values."""

import json
from datetime import datetime, timedelta

import polars as pl
import yaml

from nrtqc_diff.analyse import overlap_checks
from nrtqc_diff.config import read_config
from nrtqc_diff.reports import (
    DUP_MATCHES,
    duplicate_checks,
    duplicate_kind_tables,
    duplicate_rules,
)

#: ``group: [(platform, profile_no, seconds after 06:00, longitude,
#: latitude, [(pres, temp, psal) per level])]``.
LEVELS = [(5.0, 10.0, 35.0), (20.0, 9.0, 35.1), (60.0, 8.0, 35.2)]
GROUPS = {
    # One platform twice, the same second, the same values and position.
    1: [("A", 1, 0, 1.5, 50.5, LEVELS), ("A", 2, 0, 1.5, 50.5, LEVELS)],
    # Two platforms 30 s apart, temperature 0.0005 apart, positions within.
    2: [
        ("B", 1, 0, 2.5, 50.5, LEVELS),
        ("C", 1, 30, 2.5004, 50.5, [(p, t + 0.0005, s) for p, t, s in LEVELS]),
    ],
    # Two platforms, one twice, the same second; one salinity is 0.1 off.
    3: [
        ("D", 1, 0, 3.5, 50.5, LEVELS),
        ("D", 2, 0, 3.5, 50.5, LEVELS),
        ("E", 1, 0, 3.5, 50.5, LEVELS[:2] + [(60.0, 8.0, 35.3)]),
    ],
    # Two hours apart, three levels against two, at whole degrees.
    4: [("F", 1, 0, 4.0, 51.0, LEVELS), ("F", 2, 7200, 4.0, 51.0, LEVELS[:2])],
    # The same second, but one temperature missing in one member only.
    5: [
        ("G", 1, 0, 5.5, 50.5, LEVELS),
        ("G", 2, 0, 5.5, 50.5, [LEVELS[0], (20.0, None, 35.1), LEVELS[2]]),
    ],
    # Identical but 10 minutes apart: still a candidate.
    6: [("H", 1, 0, 6.5, 50.5, LEVELS), ("H", 2, 600, 6.5, 50.5, LEVELS)],
}

#: ``group: (kind, time, levels, values, position, whole_degree, match)``.
EXPECTED = {
    1: ("within", "same_second", "same", "identical", "equal", False, "confirmed"),
    2: ("across", "within_minute", "same", "close", "within", False, "confirmed"),
    3: ("both", "same_second", "same", "different", "equal", False, "same_time"),
    4: ("within", "same_date", "differ", "not_compared", "equal", True, "candidate"),
    5: ("within", "same_second", "same", "different", "equal", False, "same_time"),
    6: ("within", "within_hour", "same", "identical", "equal", False, "candidate"),
}


def _write_markdup(folder, stem="src"):
    """Write a ``.dups.tsv`` and the parquet beside it, as markdup does."""
    start = datetime(2020, 1, 1, 6)
    listed, rows = [], []
    for group, members in GROUPS.items():
        for platform, number, seconds, lon, lat, levels in members:
            when = start + timedelta(seconds=seconds)
            listed.append(
                f"{group}\t{platform}\t{number}\t0\t{when:%Y-%m-%d %H:%M:%S}"
                f"\t{lon}\t{lat}\t{len(levels)}\n"
            )
            for level, (pres, temp, psal) in enumerate(levels, 1):
                rows.append(
                    {
                        "platform_code": platform,
                        "profile_no": number,
                        "observation_no": level,
                        "pres": pres,
                        "temp": temp,
                        "psal": psal,
                        "is_dup": True,
                    }
                )
    # Not a duplicate, and different from A's members: must not be read.
    rows.append(
        {
            "platform_code": "A",
            "profile_no": 3,
            "observation_no": 1,
            "pres": 99.0,
            "temp": 0.0,
            "psal": 0.0,
            "is_dup": False,
        }
    )
    (folder / f"{stem}.dups.tsv").write_text(
        "dup_group\tplatform_code\tprofile_no\tprofile_time\tprofile_timestamp"
        "\tlongitude\tlatitude\tn_obs\n" + "".join(listed),
        encoding="utf-8",
    )
    pl.DataFrame(rows, schema_overrides={"temp": pl.Float32}).with_columns(
        pl.col("pres", "psal").cast(pl.Float32),
        pl.col("profile_no", "observation_no").cast(pl.UInt32),
    ).write_parquet(folder / f"{stem}.parquet")


def test_each_group_is_classed_by_time_levels_values_and_position(tmp_path):
    _write_markdup(tmp_path)
    checked = duplicate_checks(
        str(tmp_path / "src.dups.tsv"), str(tmp_path / "src.parquet")
    )
    columns = ["kind", "time", "levels", "values", "position", "whole_degree"]
    found = {
        int(row["dup_group"]): tuple(row[c] for c in columns + ["match"])
        for row in checked.iter_rows(named=True)
    }
    assert found == EXPECTED


def test_a_list_without_times_or_without_its_parquet_is_not_checked(tmp_path):
    _write_markdup(tmp_path)
    assert duplicate_checks(str(tmp_path / "src.dups.tsv"), str(tmp_path / "x")) is None
    (tmp_path / "old.dups.tsv").write_text(
        "dup_group\tplatform_code\tprofile_no\tn_obs\n1\tA\t1\t3\n1\tA\t2\t3\n",
        encoding="utf-8",
    )
    assert (
        duplicate_checks(str(tmp_path / "old.dups.tsv"), str(tmp_path / "src.parquet"))
        is None
    )


def _one_dataset_config(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "variables": [{"name": "temp", "flag": "temp_qc", "nrt_flag": "t"}],
                "data_mode": {"columns": ["temp_dm"]},
                "datasets": [
                    {
                        "id": "one",
                        "region": "R",
                        "product": "P",
                        "path": "x",
                        "source": "src",
                    }
                ],
                "analyses": {"out_dir": "out"},
            }
        ),
        encoding="utf-8",
    )
    return read_config(str(path))


def test_the_kind_tables_gain_match_and_sum_to_the_old_counts(tmp_path):
    _write_markdup(tmp_path)
    tables = duplicate_kind_tables(_one_dataset_config(tmp_path), str(tmp_path))
    kinds = tables["conversion_dup_kinds"]
    assert kinds.select("kind", "match").rows() == [
        ("within", "confirmed"),
        ("within", "same_time"),
        ("within", "candidate"),
        ("across", "confirmed"),
        ("both", "same_time"),
    ]
    totals = kinds.group_by("kind").agg(pl.col("n_groups", "n_removed").sum())
    assert dict(totals.select("kind", "n_groups").rows()) == {
        "within": 4,
        "across": 1,
        "both": 1,
    }
    assert kinds["n_removed"].sum() == 7

    checks = tables["conversion_dup_checks"]
    assert checks["n_groups"].sum() == len(GROUPS)
    assert checks["match"].is_in(DUP_MATCHES).all()
    whole = checks.filter(pl.col("whole_degree"))
    assert whole.select("time", "levels", "n_groups").rows() == [
        ("same_date", "differ", 1)
    ]
    sizes = tables["conversion_dup_sizes"]
    assert sizes["n_groups"].sum() == len(GROUPS)
    assert sizes.filter(pl.col("kind") == "both")["size_label"].to_list() == ["3"]

    # Without the parquet the tables are as before: no match, no checks.
    (tmp_path / "src.parquet").unlink()
    plain = duplicate_kind_tables(_one_dataset_config(tmp_path), str(tmp_path))
    assert "match" not in plain["conversion_dup_kinds"].columns
    assert plain["conversion_dup_checks"].height == 0
    assert plain["conversion_dup_kinds"]["n_groups"].to_list() == [4, 1, 1]


def test_the_rules_name_every_class_and_cut():
    rules = duplicate_rules()
    assert rules["same_time"] == ["same_second", "within_minute"]
    assert rules["time_limits_s"] == [1.0, 60.0, 3600.0]
    assert rules["value_tolerances"] == {"pres": 0.1, "temp": 0.001, "psal": 0.001}
    json.dumps(rules)


def _two_nrt_products(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "variables": [{"name": "temp", "flag": "temp_qc", "nrt_flag": "t"}],
                "data_mode": {"columns": ["temp_dm"]},
                "datasets": [
                    {"id": name, "region": "R", "product": name.upper(), "path": name}
                    for name in ("reg", "glo", "dm")
                ],
                "analyses": {"out_dir": "out"},
            }
        ),
        encoding="utf-8",
    )
    text = yaml.safe_load(path.read_text(encoding="utf-8"))
    text["datasets"][2]["data_mode"] = "delayed"
    path.write_text(yaml.safe_dump(text), encoding="utf-8")
    return read_config(str(path))


def test_shared_casts_of_two_nrt_products_are_checked_once(tmp_path):
    """
    The regional product's casts against the global one's: the same cast
    under another code, the same values; one 2 s apart, 0.01 degrees warmer
    at one level; one with a level fewer. The delayed mode product is left
    out, and each pair of products is checked once.
    """
    when = datetime(2020, 1, 1, 6)
    # platform, global platform, longitude, seconds apart, global levels
    casts = [
        ("P", "GLO1", 1.0, 0, LEVELS),
        ("Q", "Q", 2.0, 2, [LEVELS[0], (20.0, 9.01, 35.1), LEVELS[2]]),
        ("R", "R", 3.0, 0, LEVELS[:2]),
    ]
    rows = {"reg": [], "glo": [], "dm": []}
    profiles = []
    for number, (code, code_c, lon, seconds, levels_c) in enumerate(casts, 1):
        for name, platform, levels, offset in (
            ("reg", code, LEVELS, 0),
            ("glo", code_c, levels_c, seconds),
            ("dm", code, LEVELS, 0),
        ):
            for level, (pres, temp, psal) in enumerate(levels, 1):
                rows[name].append(
                    {
                        "platform_code": platform,
                        "profile_no": number,
                        "observation_no": level,
                        "pres": pres,
                        "temp": temp,
                        "psal": psal,
                        "bathymetry": -100.0,
                    }
                )
            profiles.append(
                {
                    "dataset_id": name,
                    "platform_code": platform,
                    "profile_no": number,
                    "profile_timestamp": when + timedelta(seconds=offset),
                    "longitude": lon,
                    "latitude": 50.0,
                    "n_obs": len(levels),
                }
            )
    for name, frame in rows.items():
        pl.DataFrame(frame).write_parquet(tmp_path / name)
    config = _two_nrt_products(tmp_path)
    table = overlap_checks(pl.DataFrame(profiles), config, config.analyses)

    assert table.select("dataset_id", "compared_id").unique().rows() == [("reg", "glo")]
    columns = ["platform", "time", "levels", "values", "position", "match"]
    found = {
        tuple(r[c] for c in columns): r["n_pairs"] for r in table.iter_rows(named=True)
    }
    assert found == {
        ("other", "same_second", "same", "identical", "equal", "confirmed"): 1,
        ("same", "same_second", "differ", "not_compared", "equal", "same_time"): 1,
        ("same", "within_minute", "same", "different", "equal", "same_time"): 1,
    }
