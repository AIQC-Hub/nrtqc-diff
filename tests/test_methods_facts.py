"""The facts the Methods need: records merged, the member kept, the dates."""

from datetime import datetime

import polars as pl
import yaml

from nrtqc_diff.config import read_config
from nrtqc_diff.reports import (
    DUP_KEPT,
    date_table,
    duplicate_kept,
    duplicate_kind_tables,
    record_table,
)

#: Two NRT files with ``TIME`` records and one CORA file with ``N_PROF``.
#: Numbers outside ``dimensions`` must not count.
HEADER = """\
GL_PR_CT_A:
  global_attributes:
    TIME: 99
  dimensions:
    TIME: 5
    DEPTH: 20
  variables:
    TIME:
      data_type: Double(F64)
      dimensions:
      - TIME
GL_PR_CT_B:
  dimensions:
    DEPTH: 3
    TIME: 4
CO_DMQCGL01_19870324_PR_CT:
  dimensions:
    N_PROF: 3
    N_LEVELS: 80
  variables:
    TIME:
      dimensions:
      - N_PROF
"""


def _reports(tmp_path, header=HEADER):
    reports = tmp_path / "report"
    (reports / "convert" / "checkpres").mkdir(parents=True)
    (reports / "convert" / "checkpres" / "src.summary.tsv").write_text(
        "n_files\tn_platforms\tn_profiles\tn_no_pres\n3\t2\t10\t1\n", encoding="utf-8"
    )
    (reports / "convert" / "src.parquet.tsv").write_text(
        "platform_code\tn_profiles\tn_obs\nA\t4\t40\nB\t3\t30\n", encoding="utf-8"
    )
    headers = tmp_path / "header"
    headers.mkdir()
    (headers / "src.yaml").write_text(header, encoding="utf-8")
    return str(reports), str(headers)


def _config(tmp_path):
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
                        "path": "output.parquet",
                        "source": "src",
                    }
                ],
                "analyses": {"out_dir": "out"},
            }
        ),
        encoding="utf-8",
    )
    return read_config(str(path))


def test_the_records_merged_are_checked_less_converted(tmp_path):
    reports, headers = _reports(tmp_path)
    row = record_table(_config(tmp_path), reports, headers).row(0, named=True)
    assert row["n_files"] == 3
    assert row["n_records"] == 12
    assert (row["n_checked"], row["n_no_pres"], row["n_converted"]) == (10, 1, 7)
    assert row["n_merged_records"] == 3
    assert row["n_unchecked"] == 1


def test_a_header_without_record_dimensions_leaves_the_records_empty(tmp_path):
    reports, headers = _reports(tmp_path, "FILE_1:\n  global_attributes:\n    x: 1\n")
    row = record_table(_config(tmp_path), reports, headers).row(0, named=True)
    assert row["n_records"] is None and row["n_unchecked"] is None
    assert row["n_merged_records"] == 3


#: ``group: [(platform, profile_no, rows, in the output)]``, in markdup's
#: row order, and the class the kept member gets.
GROUPS = {
    1: ([("A", 1, 3, True), ("A", 2, 3, False)], "first_of_tied"),
    2: ([("B", 1, 2, False), ("C", 1, 3, True)], "most_obs"),
    3: ([("D", 1, 3, False), ("D", 2, 3, True)], "other"),
    4: ([("E", 1, 3, True), ("E", 2, 2, False)], "most_obs"),
    5: ([("F", 1, 2, True), ("F", 2, 3, False)], "other"),
    6: ([("G", 1, 3, False), ("G", 2, 3, False)], "none"),
    7: ([("H", 1, 3, True), ("H", 2, 3, True)], "several"),
    # Tied on the most, the first of the tied wins over an earlier shorter one.
    8: ([("I", 1, 1, False), ("I", 2, 3, True), ("I", 3, 3, False)], "first_of_tied"),
}


def _write_groups(folder):
    listed, rows, output = [], [], []
    for group, (members, _) in GROUPS.items():
        for platform, number, n_rows, kept in members:
            listed.append(f"{group}\t{platform}\t{number}\t{n_rows}\n")
            for level in range(1, n_rows + 1):
                row = {
                    "platform_code": platform,
                    "profile_no": number,
                    "observation_no": level,
                }
                rows.append({**row, "is_dup": True})
                if kept:
                    output.append(row)
    # Not a duplicate: in both files, never in a group.
    rows.append(
        {"platform_code": "A", "profile_no": 9, "observation_no": 1, "is_dup": False}
    )
    output.append({"platform_code": "A", "profile_no": 9, "observation_no": 1})
    (folder / "src.dups.tsv").write_text(
        "dup_group\tplatform_code\tprofile_no\tn_obs\n" + "".join(listed),
        encoding="utf-8",
    )
    pl.DataFrame(rows).with_columns(
        pl.col("profile_no", "observation_no").cast(pl.UInt32)
    ).write_parquet(folder / "src.parquet")
    pl.DataFrame(output).write_parquet(folder / "output.parquet")


def test_each_group_is_classed_by_the_member_kept(tmp_path):
    _write_groups(tmp_path)
    kept = duplicate_kept(
        str(tmp_path / "src.dups.tsv"),
        str(tmp_path / "src.parquet"),
        str(tmp_path / "output.parquet"),
    )
    found = dict(kept.select(pl.col("dup_group").cast(pl.Int64), "kept_by").rows())
    assert found == {group: expected for group, (_, expected) in GROUPS.items()}
    assert set(found.values()) <= set(DUP_KEPT)


def test_without_the_output_the_member_kept_is_not_read(tmp_path):
    _write_groups(tmp_path)
    assert (
        duplicate_kept(
            str(tmp_path / "src.dups.tsv"),
            str(tmp_path / "src.parquet"),
            str(tmp_path / "x"),
        )
        is None
    )
    (tmp_path / "output.parquet").unlink()
    tables = duplicate_kind_tables(_config(tmp_path), str(tmp_path))
    assert tables["conversion_dup_kept"].height == 0


def test_the_kept_table_counts_groups_per_class(tmp_path):
    _write_groups(tmp_path)
    kept = duplicate_kind_tables(_config(tmp_path), str(tmp_path))[
        "conversion_dup_kept"
    ]
    assert kept.select("kept_by", "n_groups").rows() == [
        ("most_obs", 2),
        ("first_of_tied", 2),
        ("other", 2),
        ("none", 1),
        ("several", 1),
    ]
    assert kept["n_profiles"].sum() == sum(len(m) for m, _ in GROUPS.values())


#: An NRT file dated by its attribute (a variable's attribute and a name
#: that looks like a date must not count), and a CORA daily file by name.
DATED_HEADER = """\
GL_PR_CT_SHIP_20200101:
  global_attributes:
    title: x
    time_coverage_start: '1919-04-10T01:06:00Z'
  variables:
    TEMP:
      time_coverage_start: 1800-01-01T00:00:00Z
GL_PR_CT_NODATE:
  global_attributes:
    title: y
CO_DMQCGL01_19870324_PR_CT:
  dimensions:
    N_PROF: 3
"""


def _dated(tmp_path, header):
    headers = tmp_path / "header"
    headers.mkdir()
    (headers / "src.yaml").write_text(header, encoding="utf-8")
    dups = tmp_path / "dups"
    dups.mkdir()
    pl.DataFrame(
        {
            "profile_timestamp": [
                datetime(1953, 4, 9, 10),
                datetime(2024, 12, 31, 23, 59),
                datetime(1990, 1, 1),
            ]
        }
    ).write_parquet(dups / "src.parquet")
    return str(headers), str(dups)


def test_dates_are_declared_by_attribute_or_name_and_found_in_the_region(tmp_path):
    headers, dups = _dated(tmp_path, DATED_HEADER)
    row = date_table(_config(tmp_path), headers, dups).row(0, named=True)
    assert row["date_source"] == "time_coverage_start+file_name"
    assert (row["n_files"], row["n_files_dated"]) == (3, 2)
    assert row["declared_first"] == "1919-04-10"
    # An attribute says where a file starts, not where it ends.
    assert row["declared_last"] is None
    assert (row["region_first"], row["region_last"]) == ("1953-04-09", "2024-12-31")


def test_daily_files_declare_both_ends(tmp_path):
    names = "".join(
        f"CO_DMQCGL01_{d}_PR_CT:\n  dimensions:\n    N_PROF: 1\n"
        for d in ("19870324", "19530409", "20241231")
    )
    headers, dups = _dated(tmp_path, names)
    row = date_table(_config(tmp_path), headers, dups).row(0, named=True)
    assert row["date_source"] == "file_name"
    assert (row["declared_first"], row["declared_last"]) == (
        "1953-04-09",
        "2024-12-31",
    )


def test_either_source_alone_fills_its_own_columns(tmp_path):
    headers, dups = _dated(tmp_path, DATED_HEADER)
    config = _config(tmp_path)
    alone = date_table(config, None, dups).row(0, named=True)
    assert alone["n_files"] is None and alone["declared_first"] is None
    assert alone["region_first"] == "1953-04-09"
    alone = date_table(config, headers, None).row(0, named=True)
    assert alone["region_first"] is None and alone["declared_first"] == "1919-04-10"
    assert date_table(config, None, None).height == 0
