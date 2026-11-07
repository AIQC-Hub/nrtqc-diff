"""
Reading the reports ctddump and seastamp wrote, for the analyses.

The inputs of an nrtqclib batch come out of two earlier tools: ctddump, which
converts the NetCDF files to parquet and cleans and de-duplicates them, and
seastamp, which adds the distance to the coast and the sea floor depth. Both
write small TSV reports as they go. This module turns those into tidy tables,
one row per dataset and whatever the table is about, keyed by the dataset id
of the build configuration rather than by the tools' own file stems.

Nothing here reads observations. The reports are a few hundred kilobytes, and
the one large file family, the ctddump header YAML (41 MB for a CORA
product), is scanned line by line for the two things the analyses need.
"""

import os
import re
from collections import Counter
from typing import Dict, List, Optional

import polars as pl

from nrtqc_diff.config import BuildConfig, DatasetSpec

#: The ctddump pipeline steps whose per-platform reports are read, in order:
#: the report directory under ``report/``, the key written to the tables, and
#: what the step did, in words. ``markdup`` is left out: it only marks
#: duplicates, so its counts are those of ``filter`` plus a column, which
#: :func:`duplicate_table` reads.
CTDDUMP_STEPS: List[Dict[str, str]] = [
    {"dir": "convert", "key": "convert", "label": "Converted"},
    {
        "dir": "clean/dropqc",
        "key": "dropqc",
        "label": "Bad time or position QC dropped",
    },
    {"dir": "clean/dropna", "key": "dropna", "label": "All-missing profiles dropped"},
    {"dir": "clean/filter", "key": "filter", "label": "Outside the region dropped"},
    {"dir": "dedup/dedup", "key": "dedup", "label": "Duplicates removed"},
]

#: The data mode values ctddump counts per variable, as the column suffix and
#: the value written to the tables.
DATA_MODE_SUFFIXES: Dict[str, str] = {"r": "R", "a": "A", "d": "D", "unknown": ""}

#: The variables whose data mode ctddump counts.
DATA_MODE_VARIABLES: List[str] = ["temp", "psal", "pres"]

#: The presence columns of the header report, as written there.
HEADER_ITEMS: List[str] = [
    "temp",
    "psal",
    "pres",
    "deph",
    "time_qc",
    "position_qc",
]

#: A file entry of the header YAML starts at the left margin.
_FILE_LINE = re.compile(r"^(\S[^:]*):\s*$")

#: The file level data mode, four spaces in, under ``global_attributes``.
_DATA_MODE_LINE = re.compile(r"^    data_mode:\s*(\S*)\s*$")


def _sourced(config: BuildConfig) -> List[DatasetSpec]:
    """The datasets that name a report stem, in configuration order."""
    return [spec for spec in config.datasets if spec.source]


def _read_tsv(path: str) -> pl.DataFrame:
    """
    Read one ctddump or seastamp report.

    Every column is read as text first and cast where it is used, because
    an empty cell in a numeric column (``nobs_agree_pct`` of a comparison
    with nothing in common) would otherwise decide the column's type.

    :param path: The TSV file.
    :return: The table.
    """
    return pl.read_csv(path, separator="\t", infer_schema=False)


def _number(column: str) -> pl.Expr:
    """A report column as a float, empty cells as null."""
    return pl.col(column).cast(pl.Float64, strict=False)


def _label_columns(spec: DatasetSpec) -> List[pl.Expr]:
    """The columns that say which dataset a row is about."""
    return [
        pl.lit(spec.id).alias("dataset_id"),
        pl.lit(spec.region).alias("region"),
        pl.lit(spec.product).alias("product"),
        pl.lit(spec.label).alias("label"),
    ]


def step_table(config: BuildConfig, reports: str) -> pl.DataFrame:
    """
    Platforms, profiles and observations left after each ctddump step.

    :param config: The build configuration.
    :param reports: The ctddump ``report`` directory.
    :return: One row per dataset and step.
    """
    frames = []
    for spec in _sourced(config):
        for order, step in enumerate(CTDDUMP_STEPS):
            path = os.path.join(reports, step["dir"], f"{spec.source}.parquet.tsv")
            if not os.path.isfile(path):
                continue
            table = _read_tsv(path).with_columns(
                _number("n_profiles"), _number("n_obs")
            )
            frames.append(
                pl.DataFrame(
                    {
                        "step": [step["key"]],
                        "step_order": [order],
                        "step_label": [step["label"]],
                        "n_platforms": [
                            int(table.filter(pl.col("n_profiles") > 0).height)
                        ],
                        "n_profiles": [int(table["n_profiles"].sum())],
                        "n_obs": [int(table["n_obs"].sum())],
                    }
                ).select(_label_columns(spec) + [pl.all()])
            )
    return _concat(frames)


def data_mode_table(config: BuildConfig, reports: str) -> pl.DataFrame:
    """
    Observations per data mode and variable, as converted and as kept.

    :param config: The build configuration.
    :param reports: The ctddump ``report`` directory.
    :return: One row per dataset, step (``convert`` or ``dedup``), variable
             and data mode, with the mode ``""`` for an unknown one.
    """
    frames = []
    for spec in _sourced(config):
        for step in (CTDDUMP_STEPS[0], CTDDUMP_STEPS[-1]):
            path = os.path.join(reports, step["dir"], f"{spec.source}.parquet.tsv")
            if not os.path.isfile(path):
                continue
            table = _read_tsv(path)
            rows = []
            for variable in DATA_MODE_VARIABLES:
                for suffix, mode in DATA_MODE_SUFFIXES.items():
                    column = f"{variable}_dm_{suffix}"
                    if column not in table.columns:
                        continue
                    rows.append(
                        {
                            "step": step["key"],
                            "variable": variable,
                            "data_mode": mode,
                            "n_obs": int(
                                table.select(_number(column).sum()).item() or 0
                            ),
                        }
                    )
            if rows:
                frames.append(
                    pl.DataFrame(rows).select(_label_columns(spec) + [pl.all()])
                )
    return _concat(frames)


def duplicate_table(config: BuildConfig, reports: str) -> pl.DataFrame:
    """
    Duplicate profiles ctddump marked, before it removed them.

    :param config: The build configuration.
    :param reports: The ctddump ``report`` directory.
    :return: One row per dataset.
    """
    frames = []
    for spec in _sourced(config):
        path = os.path.join(reports, "dedup", "markdup", f"{spec.source}.parquet.tsv")
        if not os.path.isfile(path):
            continue
        table = _read_tsv(path).with_columns(
            _number("n_profiles"), _number("n_obs"), _number("dup_profiles")
        )
        frames.append(
            pl.DataFrame(
                {
                    "n_platforms": [int(table.filter(pl.col("n_profiles") > 0).height)],
                    "n_platforms_with_duplicates": [
                        int(table.filter(pl.col("dup_profiles") > 0).height)
                    ],
                    "n_profiles": [int(table["n_profiles"].sum())],
                    "n_duplicate_profiles": [int(table["dup_profiles"].sum())],
                }
            ).select(_label_columns(spec) + [pl.all()])
        )
    return _concat(frames)


#: Duplicate groups larger than this are pooled into one size class.
DUP_SIZE_CAP: int = 10

#: How many across-platform platform sets to keep per dataset, commonest
#: first. The rest are summed into a row of their own.
DUP_PLATFORM_SETS: int = 15


def _duplicate_groups(path: str) -> pl.DataFrame:
    """
    The duplicate groups of one ctddump ``.dups.tsv``, one row per group.

    A group is ``within`` one platform when all its profiles carry the same
    platform code, and ``across`` platforms otherwise. ``dedup`` keeps one
    profile of each group, so a group of ``n`` loses ``n - 1``.

    :param path: The duplicates list ``markdup`` wrote.
    :return: ``dup_group``, ``n_profiles``, ``n_platforms``, ``platforms``
             (the sorted codes joined by `` + ``) and ``kind``.
    """
    return (
        _read_tsv(path)
        .group_by("dup_group")
        .agg(
            pl.len().cast(pl.Int64).alias("n_profiles"),
            pl.col("platform_code").n_unique().cast(pl.Int64).alias("n_platforms"),
            pl.col("platform_code").unique().sort().str.join(" + ").alias("platforms"),
        )
        .with_columns(
            pl.when(pl.col("n_platforms") == 1)
            .then(pl.lit("within"))
            .otherwise(pl.lit("across"))
            .alias("kind")
        )
    )


def duplicate_kind_tables(config: BuildConfig, dups: str) -> Dict[str, pl.DataFrame]:
    """
    Duplicates within one platform and across platforms, from ctddump's lists.

    :param config: The build configuration.
    :param dups: The directory holding ``<stem>.dups.tsv``.
    :return: ``conversion_dup_kinds`` (one row per dataset and kind),
             ``conversion_dup_sizes`` (per dataset, kind and group size, the
             sizes above :data:`DUP_SIZE_CAP` pooled) and
             ``conversion_dup_platforms`` (the commonest platform sets behind
             the across-platform groups).
    """
    kinds, sizes, sets = [], [], []
    for spec in _sourced(config):
        path = os.path.join(dups, f"{spec.source}.dups.tsv")
        if not os.path.isfile(path):
            continue
        groups = _duplicate_groups(path)
        labels = _label_columns(spec)
        kinds.append(
            groups.group_by("kind")
            .agg(
                pl.len().cast(pl.Int64).alias("n_groups"),
                pl.col("n_profiles").sum().alias("n_profiles"),
                (pl.col("n_profiles") - 1).sum().alias("n_removed"),
            )
            .sort("kind", descending=True)
            .select(labels + [pl.all()])
        )
        sizes.append(
            groups.with_columns(
                pl.col("n_profiles").clip(upper_bound=DUP_SIZE_CAP + 1).alias("size")
            )
            .group_by("kind", "size")
            .agg(
                pl.len().cast(pl.Int64).alias("n_groups"),
                pl.col("n_profiles").sum().alias("n_profiles"),
            )
            .with_columns(
                pl.when(pl.col("size") > DUP_SIZE_CAP)
                .then(pl.lit(f"{DUP_SIZE_CAP + 1}+"))
                .otherwise(pl.col("size").cast(pl.Utf8))
                .alias("size_label")
            )
            .sort("kind", "size", descending=[True, False])
            .select(labels + [pl.all()])
        )
        across = (
            groups.filter(pl.col("kind") == "across")
            .group_by("platforms")
            .agg(
                pl.col("n_platforms").first(),
                pl.len().cast(pl.Int64).alias("n_groups"),
                pl.col("n_profiles").sum().alias("n_profiles"),
                (pl.col("n_profiles") - 1).sum().alias("n_removed"),
            )
            .sort(["n_groups", "platforms"], descending=[True, False])
            .with_row_index("rank", offset=1)
        )
        top = across.filter(pl.col("rank") <= DUP_PLATFORM_SETS)
        rest = across.filter(pl.col("rank") > DUP_PLATFORM_SETS)
        if rest.height:
            top = pl.concat(
                [
                    top,
                    rest.select(
                        pl.lit(None, pl.UInt32).alias("rank"),
                        pl.lit(f"{rest.height} other sets").alias("platforms"),
                        pl.lit(None, pl.Int64).alias("n_platforms"),
                        pl.col("n_groups").sum(),
                        pl.col("n_profiles").sum(),
                        pl.col("n_removed").sum(),
                    ),
                ],
                how="vertical_relaxed",
            )
        sets.append(top.select(labels + [pl.all()]))
    return {
        "conversion_dup_kinds": _concat(kinds),
        "conversion_dup_sizes": _concat(sizes),
        "conversion_dup_platforms": _concat(sets),
    }


def file_table(config: BuildConfig, reports: str) -> pl.DataFrame:
    """
    How many source files carry each variable ctddump reads.

    :param config: The build configuration.
    :param reports: The ctddump ``report`` directory.
    :return: One row per dataset and item, with the dataset's file count.
    """
    frames = []
    for spec in _sourced(config):
        path = os.path.join(reports, "header", f"{spec.source}.yaml.tsv")
        if not os.path.isfile(path):
            continue
        table = _read_tsv(path)
        rows = [
            {
                "item": item,
                "n_files": table.height,
                "n_with": int((table[f"has_{item}"] == "true").sum()),
            }
            for item in HEADER_ITEMS
            if f"has_{item}" in table.columns
        ]
        frames.append(pl.DataFrame(rows).select(_label_columns(spec) + [pl.all()]))
    return _concat(frames)


def extra_parameter_table(config: BuildConfig, reports: str) -> pl.DataFrame:
    """
    The other parameters the source files carry, which ctddump does not read.

    :param config: The build configuration.
    :param reports: The ctddump ``report`` directory.
    :return: One row per dataset and parameter.
    """
    frames = []
    for spec in _sourced(config):
        path = os.path.join(reports, "header", f"{spec.source}.yaml.tsv")
        if not os.path.isfile(path):
            continue
        table = _read_tsv(path)
        if "extra_params" not in table.columns:
            continue
        # ctddump separates them with semicolons; commas are accepted too.
        # A file lists each parameter once, so a count is a count of files.
        counts: Counter = Counter()
        for cell in table["extra_params"].fill_null(""):
            names = {name.strip() for name in re.split(r"[;,]", cell)}
            counts.update(name for name in names if name)
        if counts:
            frames.append(
                pl.DataFrame(
                    {
                        "parameter": list(counts),
                        "n_files": [int(n) for n in counts.values()],
                    }
                )
                .sort(["n_files", "parameter"], descending=[True, False])
                .select(_label_columns(spec) + [pl.all()])
            )
    return _concat(frames)


def header_data_mode_table(config: BuildConfig, headers: str) -> pl.DataFrame:
    """
    The file level ``data_mode`` attribute of the source files.

    ``M`` (mixed) is the interesting value: such a file says its levels are
    in different modes, and ctddump only knows which if the file carries a
    per-level data mode variable as well. A file without the attribute is
    counted under ``""``.

    :param config: The build configuration.
    :param headers: The ctddump ``header`` directory of per-dataset YAML.
    :return: One row per dataset and attribute value.
    """
    frames = []
    for spec in _sourced(config):
        path = os.path.join(headers, f"{spec.source}.yaml")
        if not os.path.isfile(path):
            continue
        modes: List[str] = []
        current: Optional[str] = None
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if _FILE_LINE.match(line):
                    if current is not None:
                        modes.append(current)
                    current = ""
                    continue
                found = _DATA_MODE_LINE.match(line)
                if found and current == "":
                    current = found.group(1).strip("'\"")
        if current is not None:
            modes.append(current)
        if not modes:
            continue
        frames.append(
            pl.DataFrame({"data_mode": modes})
            .group_by("data_mode")
            .len("n_files")
            .with_columns(pl.col("n_files").cast(pl.Int64))
            .sort("data_mode")
            .select(_label_columns(spec) + [pl.all()])
        )
    return _concat(frames)


def compare_table(config: BuildConfig, reports: str) -> pl.DataFrame:
    """
    The profile matching ctddump did between products of one region.

    Only comparisons between two configured datasets are kept, each in both
    directions as ctddump writes them: the reference's coverage differs from
    the compared product's.

    :param config: The build configuration.
    :param reports: The ctddump ``report`` directory.
    :return: One row per reference and compared dataset.
    """
    by_stem = {spec.source: spec for spec in _sourced(config)}
    directory = os.path.join(reports, "compare")
    if not os.path.isdir(directory):
        return pl.DataFrame()
    frames = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".tsv"):
            continue
        table = _read_tsv(os.path.join(directory, name))
        for row in table.iter_rows(named=True):
            reference = by_stem.get(row["reference"])
            compared = by_stem.get(row["compared"])
            if reference is None or compared is None:
                continue
            frames.append(
                pl.DataFrame(
                    {
                        "region": [reference.region],
                        "reference_id": [reference.id],
                        "reference_label": [reference.label],
                        "compared_id": [compared.id],
                        "compared_label": [compared.label],
                        **{
                            key: [_as_number(row[key])]
                            for key in row
                            if key not in ("reference", "compared")
                        },
                    }
                )
            )
    return _concat(frames)


def stamp_table(config: BuildConfig, reports: str) -> pl.DataFrame:
    """
    The seastamp summary of each dataset: totals and bands of both columns.

    :param config: The build configuration.
    :param reports: The seastamp ``report/stamp`` directory.
    :return: One row per dataset and report line, with the band order kept.
    """
    frames = []
    for spec in _sourced(config):
        path = os.path.join(reports, f"{spec.source}.tsv")
        if not os.path.isfile(path):
            continue
        table = (
            _read_tsv(path)
            .with_row_index("line")
            .with_columns(
                pl.col("line").cast(pl.Int64),
                *[_number(c) for c in ("rows", "locations", "min", "max", "mean")],
            )
        )
        frames.append(table.select(_label_columns(spec) + [pl.all()]))
    return _concat(frames)


def report_tables(config: BuildConfig) -> Dict[str, pl.DataFrame]:
    """
    Every table this module makes, by the name it is written under.

    A report directory the configuration does not name, or a dataset
    without a ``source``, contributes nothing rather than failing: the
    analyses of the NRT QC output do not need the reports.

    :param config: The build configuration, with an ``analyses`` section.
    :return: The tables, empty ones left out.
    """
    spec = config.analyses
    tables: Dict[str, pl.DataFrame] = {}
    if spec is not None and spec.ctddump_reports:
        tables["conversion_steps"] = step_table(config, spec.ctddump_reports)
        tables["conversion_data_mode"] = data_mode_table(config, spec.ctddump_reports)
        tables["conversion_duplicates"] = duplicate_table(config, spec.ctddump_reports)
        tables["conversion_files"] = file_table(config, spec.ctddump_reports)
        tables["conversion_extra_parameters"] = extra_parameter_table(
            config, spec.ctddump_reports
        )
        tables["conversion_compare"] = compare_table(config, spec.ctddump_reports)
    if spec is not None and spec.ctddump_headers:
        tables["conversion_file_data_mode"] = header_data_mode_table(
            config, spec.ctddump_headers
        )
    if spec is not None and spec.ctddump_dups:
        tables.update(duplicate_kind_tables(config, spec.ctddump_dups))
    if spec is not None and spec.seastamp_reports:
        tables["depth_stamp"] = stamp_table(config, spec.seastamp_reports)
    return {name: table for name, table in tables.items() if table.height}


def _as_number(value: Optional[str]) -> Optional[float]:
    """A report cell as a number, or None when it is empty or not one."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _concat(frames: List[pl.DataFrame]) -> pl.DataFrame:
    """Stack per-dataset frames, or return an empty frame for none."""
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed")
