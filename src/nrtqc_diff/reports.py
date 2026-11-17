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


#: The totals of ctddump's ``checkpres`` summary, as written there:
#: profiles, platforms and files checked, profiles without any pressure (not
#: checked), and the profiles whose pressure steps down somewhere, split
#: into ``reversed`` (stored bottom up) and ``non_monotonic`` (down and up
#: again), with the platforms and files that hold them.
PRESSURE_ORDER_COLUMNS: List[str] = [
    "n_files",
    "n_platforms",
    "n_profiles",
    "n_no_pres",
    "n_bad_profiles",
    "n_reversed",
    "n_non_monotonic",
    "n_bad_platforms",
    "n_bad_files",
]


def pressure_order_table(config: BuildConfig, reports: str) -> pl.DataFrame:
    """
    The pressure order ctddump found in the source files, before its merge.

    ``checkpres`` runs on the per-file parquet, before ``concat convert``
    sorts each profile by increasing pressure, and writes
    ``convert/checkpres/<stem>.summary.tsv``. ctddump versions before 0.37
    did not, and a dataset without the file is left out.

    :param config: The build configuration.
    :param reports: The ctddump ``report`` directory.
    :return: One row per dataset.
    """
    frames = []
    for spec in _sourced(config):
        name = f"{spec.source}.summary.tsv"
        path = os.path.join(reports, "convert", "checkpres", name)
        if not os.path.isfile(path):
            continue
        table = _read_tsv(path)
        frames.append(
            table.head(1)
            .select(
                [
                    _number(c).cast(pl.Int64).alias(c)
                    if c in table.columns
                    else pl.lit(None, dtype=pl.Int64).alias(c)
                    for c in PRESSURE_ORDER_COLUMNS
                ]
            )
            .select(_label_columns(spec) + [pl.all()])
        )
    return _concat(frames)


def pressure_order_profiles(spec: DatasetSpec, reports: str) -> Optional[pl.DataFrame]:
    """
    The profiles ctddump's ``checkpres`` found stepping down in pressure.

    ``convert/checkpres/<stem>.profiles.tsv`` lists each profile whose
    pressure decreases somewhere in the order the source file stores it,
    before ``concat convert`` sorts the levels. Its ``profile_no`` counts
    within one source file, so the profile is told by its file, platform,
    time and position instead.

    :param spec: The dataset.
    :param reports: The ctddump ``report`` directory.
    :return: ``filename``, ``platform_code``, ``profile_time`` (days, as
             nrtqclib's output has it), ``longitude``, ``latitude`` and
             ``kind`` (``reversed`` or ``non_monotonic``) and ``n_obs``, one
             row per listed profile; None when the dataset has no list, as before
             ctddump 0.37.
    """
    if not spec.source:
        return None
    path = os.path.join(reports, "convert", "checkpres", f"{spec.source}.profiles.tsv")
    if not os.path.isfile(path):
        return None
    return _read_tsv(path).select(
        pl.col("filename"),
        pl.col("platform_code"),
        _number("profile_time").alias("profile_time"),
        _number("longitude").alias("longitude"),
        _number("latitude").alias("latitude"),
        pl.col("kind"),
        _number("n_obs").cast(pl.Int64).alias("n_obs"),
    )


def dropped_platform_table(config: BuildConfig, reports: str) -> pl.DataFrame:
    """
    The profiles ctddump's ``dropqc`` step dropped, per platform.

    ``dropqc`` keeps a profile only if its time and position flags are both
    good or missing, and drops it before seastamp runs, so nothing stamps
    where it was. The per-platform reports before and after the step give
    how many each platform lost and the range of its positions before. A
    platform whose positions all fall on one point is ``fixed``: that point
    is where every one of its dropped profiles was, and it is kept as
    ``longitude`` and ``latitude``. Any other platform is left without one.

    :param config: The build configuration.
    :param reports: The ctddump ``report`` directory.
    :return: One row per dataset and platform present before the step,
             dropped profiles or not.
    """
    frames = []
    for spec in _sourced(config):
        before_path = os.path.join(reports, "convert", f"{spec.source}.parquet.tsv")
        after_path = os.path.join(
            reports, "clean", "dropqc", f"{spec.source}.parquet.tsv"
        )
        if not (os.path.isfile(before_path) and os.path.isfile(after_path)):
            continue
        before = _read_tsv(before_path)
        bounds = ["longitude_min", "longitude_max", "latitude_min", "latitude_max"]
        if all(c in before.columns for c in bounds):
            fixed = (_number("longitude_min") == _number("longitude_max")) & (
                _number("latitude_min") == _number("latitude_max")
            )
        else:
            fixed = pl.lit(False)
        after = _read_tsv(after_path).select(
            "platform_code", _number("n_profiles").cast(pl.Int64).alias("n_after")
        )
        table = (
            before.select(
                "platform_code",
                _number("n_profiles").cast(pl.Int64).alias("n_before"),
                fixed.fill_null(False).alias("fixed"),
                *[
                    _number(c) if c in before.columns else pl.lit(None, pl.Float64)
                    for c in ("longitude_min", "latitude_min")
                ],
            )
            .join(after, on="platform_code", how="left")
            .with_columns(
                (pl.col("n_before") - pl.col("n_after").fill_null(0)).alias(
                    "n_dropped"
                ),
                pl.when(pl.col("fixed"))
                .then(pl.col("longitude_min"))
                .alias("longitude"),
                pl.when(pl.col("fixed")).then(pl.col("latitude_min")).alias("latitude"),
            )
            .select(
                "platform_code",
                "n_before",
                "n_dropped",
                "fixed",
                "longitude",
                "latitude",
            )
        )
        frames.append(table.select(_label_columns(spec) + [pl.all()]))
    return _concat(frames)


#: Duplicate groups larger than this are pooled into one size class.
DUP_SIZE_CAP: int = 10

#: The kinds of duplicate group, in the order the tables list them.
DUP_KINDS: List[str] = ["within", "across", "both"]

#: How many multi-platform platform sets to keep per dataset, commonest
#: first. The rest are summed into a row of their own.
DUP_PLATFORM_SETS: int = 15

#: How far apart in time the members of a group (or the two profiles of a
#: pair) are, in order: the same second; under a minute; under an hour; and
#: more, which still means the same date, since ctddump groups by date.
DUP_TIME_CLASSES: List[str] = [
    "same_second",
    "within_minute",
    "within_hour",
    "same_date",
]

#: The upper ends of the time classes but the last, in seconds, exclusive.
DUP_TIME_LIMITS_S: List[float] = [1.0, 60.0, 3600.0]

#: The time classes that count as the same time for :data:`DUP_MATCHES`.
DUP_SAME_TIME: List[str] = ["same_second", "within_minute"]

#: Whether every member has the same number of levels.
DUP_LEVEL_CLASSES: List[str] = ["same", "differ"]

#: How the values of members with the same number of levels compare, level
#: by level in stored order: all equal (missing equal to missing); all
#: within :data:`DUP_VALUE_TOLERANCES`; or not. ``not_compared`` is a group
#: whose members differ in their number of levels.
DUP_VALUE_CLASSES: List[str] = ["identical", "close", "different", "not_compared"]

#: The largest spread across members at one level that still counts as
#: ``close``, per variable, in the variable's unit. A variable missing from
#: the data is left out of the comparison.
DUP_VALUE_TOLERANCES: Dict[str, float] = {"pres": 0.1, "temp": 0.001, "psal": 0.001}

#: The values are stored in single precision, so a spread of exactly the
#: tolerance can come out a little above it. A spread up to this factor of
#: the tolerance is still ``close``.
DUP_TOLERANCE_SLACK: float = 1.01

#: How many observations of duplicate groups :func:`duplicate_checks` reads
#: at a time. All of CORA Arctic's at once peaked at 3.4 GB; each chunk scans
#: the file again, which takes a few seconds.
DUP_CHUNK_LEVELS: int = 5_000_000

#: Whether the members sit at exactly one position, or only within the
#: 0.001 degree that ctddump groups by.
DUP_POSITION_CLASSES: List[str] = ["equal", "within"]

#: A position counts as whole degrees when both coordinates are this close
#: to an integer.
DUP_WHOLE_DEGREE_TOLERANCE: float = 1e-6

#: What a group or pair is taken to be: ``confirmed``, the same time and
#: values ``identical`` or ``close``; ``same_time``, the same time but not
#: those values; ``candidate``, everything else.
DUP_MATCHES: List[str] = ["confirmed", "same_time", "candidate"]


def _order(column: str, values: List[str]) -> pl.Expr:
    """A text column as its position in ``values``, for sorting."""
    return pl.col(column).replace_strict(values, range(len(values)))


def time_class(seconds: pl.Expr) -> pl.Expr:
    """The :data:`DUP_TIME_CLASSES` entry for a time apart in seconds."""
    expr = pl.when(seconds < DUP_TIME_LIMITS_S[0]).then(pl.lit(DUP_TIME_CLASSES[0]))
    for limit, name in zip(DUP_TIME_LIMITS_S[1:], DUP_TIME_CLASSES[1:]):
        expr = expr.when(seconds < limit).then(pl.lit(name))
    return expr.otherwise(pl.lit(DUP_TIME_CLASSES[-1]))


def match_class() -> pl.Expr:
    """The :data:`DUP_MATCHES` entry from the ``time`` and ``values`` columns."""
    same_time = pl.col("time").is_in(DUP_SAME_TIME)
    return (
        pl.when(same_time & pl.col("values").is_in(["identical", "close"]))
        .then(pl.lit("confirmed"))
        .when(same_time)
        .then(pl.lit("same_time"))
        .otherwise(pl.lit("candidate"))
    )


def value_classes(levels: pl.LazyFrame, variables: List[str]) -> pl.DataFrame:
    """
    Compare the members of each group level by level.

    A level is ``identical`` when every member has the same value of every
    variable, or every member lacks it; ``close`` when the spread (largest
    minus smallest) stays within :data:`DUP_VALUE_TOLERANCES`. A value
    missing in one member and present in another, or a level some member
    lacks, makes the level ``different``. A group takes the worst class of
    its levels.

    Two aggregations, one per level and one per group, so that no member is
    joined against another: the observations are read once.

    :param levels: ``_gid`` (the group), ``_size`` (its number of members),
                   ``_level`` (the level number, the same in every member)
                   and the variables, as floats.
    :param variables: The variables to compare, each in
                      :data:`DUP_VALUE_TOLERANCES`.
    :return: ``_gid`` and ``values``, one row per group.
    """
    per_level = levels.group_by("_gid", "_level").agg(
        pl.len().alias("_n"),
        pl.col("_size").first(),
        *[pl.col(v).max().alias(f"_max_{v}") for v in variables],
        *[pl.col(v).min().alias(f"_min_{v}") for v in variables],
        *[pl.col(v).null_count().alias(f"_nulls_{v}") for v in variables],
    )
    missing = pl.col("_n") != pl.col("_size")
    for v in variables:
        nulls = pl.col(f"_nulls_{v}")
        missing = missing | ((nulls > 0) & (nulls < pl.col("_n")))
    per_group = per_level.group_by("_gid").agg(
        missing.any().alias("_missing"),
        *[
            (pl.col(f"_max_{v}").cast(pl.Float64) - pl.col(f"_min_{v}"))
            .max()
            .alias(f"_spread_{v}")
            for v in variables
        ],
    )
    spreads = [pl.col(f"_spread_{v}").fill_null(0.0) for v in variables]
    identical = (
        pl.all_horizontal([s == 0 for s in spreads]) if spreads else pl.lit(True)
    )
    close = (
        pl.all_horizontal(
            [
                s <= DUP_VALUE_TOLERANCES[v] * DUP_TOLERANCE_SLACK
                for s, v in zip(spreads, variables)
            ]
        )
        if spreads
        else pl.lit(True)
    )
    return (
        per_group.select(
            "_gid",
            pl.when(pl.col("_missing"))
            .then(pl.lit("different"))
            .when(identical)
            .then(pl.lit("identical"))
            .when(close)
            .then(pl.lit("close"))
            .otherwise(pl.lit("different"))
            .alias("values"),
        )
    ).collect(engine="streaming")


def value_columns(scan: pl.LazyFrame) -> List[str]:
    """The variables of :data:`DUP_VALUE_TOLERANCES` that ``scan`` holds."""
    present = scan.collect_schema().names()
    return [v for v in DUP_VALUE_TOLERANCES if v in present]


def value_floats(variables: List[str]) -> List[pl.Expr]:
    """
    The variables as ``Float32``, NaN as missing. Single precision is how
    they are stored, and halves what the per-level aggregation holds.
    """
    return [pl.col(v).cast(pl.Float32).fill_nan(None).alias(v) for v in variables]


def duplicate_checks(path: str, observations: str) -> Optional[pl.DataFrame]:
    """
    Check each duplicate group of one ctddump list against time and values.

    :param path: The duplicates list ``markdup`` wrote, ``<stem>.dups.tsv``.
    :param observations: ``markdup``'s parquet beside it, whose ``is_dup``
                         marks every member of every group.
    :return: One row per group: ``dup_group``, ``n_profiles``,
             ``n_platforms``, ``platforms``, ``kind`` as
             :func:`_duplicate_groups` gives them, and ``time``, ``levels``,
             ``values``, ``position``, ``whole_degree`` and ``match``. None
             when the list lacks the time or position columns or the parquet
             is missing.
    """
    listed = _read_tsv(path)
    needed = {"profile_timestamp", "longitude", "latitude", "n_obs"}
    if not needed <= set(listed.columns) or not os.path.isfile(observations):
        return None
    lon, lat = _number("longitude"), _number("latitude")

    def whole(column: pl.Expr) -> pl.Expr:
        return (column - column.round(0)).abs() <= DUP_WHOLE_DEGREE_TOLERANCE

    stamp = pl.col("profile_timestamp").str.to_datetime(
        "%Y-%m-%d %H:%M:%S", strict=False
    )
    groups = listed.group_by("dup_group").agg(
        (stamp.max() - stamp.min()).dt.total_seconds().alias("_spread_s"),
        (_number("n_obs").n_unique() == 1).alias("_same_levels"),
        ((lon.n_unique() == 1) & (lat.n_unique() == 1)).alias("_equal"),
        (whole(lon) & whole(lat)).all().alias("whole_degree"),
    )

    # The groups whose members have the same number of levels, numbered
    # into chunks of about DUP_CHUNK_LEVELS observations.
    keys = (
        listed.select(
            pl.col("dup_group").cast(pl.UInt32).alias("_gid"),
            pl.col("platform_code"),
            pl.col("profile_no").cast(pl.UInt32),
            _number("n_obs"),
        )
        .join(
            groups.filter("_same_levels").select(
                pl.col("dup_group").cast(pl.UInt32).alias("_gid")
            ),
            on="_gid",
        )
        .sort("_gid", "platform_code", "profile_no")
        .with_columns((pl.col("n_obs").cum_sum() // DUP_CHUNK_LEVELS).alias("_chunk"))
        .with_columns(
            pl.len().over("_gid").cast(pl.UInt32).alias("_size"),
            pl.col("_chunk").first().over("_gid"),
        )
    )
    scan = pl.scan_parquet(observations)
    variables = value_columns(scan)
    parts = []
    for _, chunk in keys.group_by("_chunk", maintain_order=True):
        levels = (
            scan.filter(pl.col("is_dup"))
            .select(
                pl.col("platform_code").cast(pl.Utf8),
                pl.col("profile_no").cast(pl.UInt32),
                pl.col("observation_no").cast(pl.UInt32).alias("_level"),
                *value_floats(variables),
            )
            .join(
                chunk.select("platform_code", "profile_no", "_gid", "_size").lazy(),
                on=["platform_code", "profile_no"],
            )
            .drop("platform_code", "profile_no")
        )
        parts.append(value_classes(levels, variables))
    values = (
        pl.concat(parts)
        if parts
        else pl.DataFrame(schema={"_gid": pl.UInt32, "values": pl.Utf8})
    ).select(pl.col("_gid").cast(pl.Utf8).alias("dup_group"), "values")

    return (
        _duplicate_groups(path)
        .join(groups, on="dup_group", how="left")
        .join(values, on="dup_group", how="left")
        .select(
            "dup_group",
            "n_profiles",
            "n_platforms",
            "platforms",
            "kind",
            time_class(pl.col("_spread_s")).alias("time"),
            pl.when(pl.col("_same_levels"))
            .then(pl.lit("same"))
            .otherwise(pl.lit("differ"))
            .alias("levels"),
            pl.col("values").fill_null("not_compared"),
            pl.when(pl.col("_equal"))
            .then(pl.lit("equal"))
            .otherwise(pl.lit("within"))
            .alias("position"),
            "whole_degree",
        )
        .with_columns(match_class().alias("match"))
    )


def _duplicate_groups(path: str) -> pl.DataFrame:
    """
    The duplicate groups of one ctddump ``.dups.tsv``, one row per group.

    A group is ``within`` one platform when all its profiles carry the same
    platform code; ``across`` platforms when each carries a different one;
    and ``both`` when it spans two or more platforms and at least one of
    them reports the cast more than once. ``dedup`` keeps one profile of each
    group, so a group of ``n`` loses ``n - 1``.

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
            .when(pl.col("n_profiles") > pl.col("n_platforms"))
            .then(pl.lit("both"))
            .otherwise(pl.lit("across"))
            .alias("kind")
        )
    )


def duplicate_kind_tables(config: BuildConfig, dups: str) -> Dict[str, pl.DataFrame]:
    """
    Duplicates within one platform and across platforms, from ctddump's lists.

    When ``markdup``'s parquet sits beside a list, each group is also
    checked against time and values (:func:`duplicate_checks`): the kinds
    and sizes tables gain ``match``, and summed over it give the same
    counts as without.

    :param config: The build configuration.
    :param dups: The directory holding ``<stem>.dups.tsv`` and, for the
                 checks, ``<stem>.parquet``.
    :return: ``conversion_dup_kinds`` (one row per dataset and kind, and
             ``match`` when checked), ``conversion_dup_sizes`` (per dataset,
             kind, ``match`` when checked, and group size, the sizes above
             :data:`DUP_SIZE_CAP` pooled), ``conversion_dup_platforms`` (the
             commonest platform sets behind the groups spanning two or more
             platforms, ``across`` and ``both``) and, when checked,
             ``conversion_dup_checks`` (per dataset, kind and every check).
    """
    kinds, sizes, sets, checks = [], [], [], []
    for spec in _sourced(config):
        path = os.path.join(dups, f"{spec.source}.dups.tsv")
        if not os.path.isfile(path):
            continue
        checked = duplicate_checks(path, os.path.join(dups, f"{spec.source}.parquet"))
        groups = _duplicate_groups(path) if checked is None else checked
        by = ["kind"] if checked is None else ["kind", "match"]
        order = [_order("kind", DUP_KINDS)]
        if checked is not None:
            order.append(_order("match", DUP_MATCHES))
        labels = _label_columns(spec)
        counts = [
            pl.len().cast(pl.Int64).alias("n_groups"),
            pl.col("n_profiles").sum().alias("n_profiles"),
            (pl.col("n_profiles") - 1).sum().alias("n_removed"),
        ]
        kinds.append(
            groups.group_by(by).agg(counts).sort(order).select(labels + [pl.all()])
        )
        sizes.append(
            groups.with_columns(
                pl.col("n_profiles").clip(upper_bound=DUP_SIZE_CAP + 1).alias("size")
            )
            .group_by(*by, "size")
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
            .sort(*order, "size")
            .select(labels + [pl.all()])
        )
        if checked is not None:
            keys = ["time", "levels", "values", "position", "whole_degree"]
            checks.append(
                checked.group_by("kind", *keys, "match")
                .agg(counts)
                .sort(
                    _order("kind", DUP_KINDS),
                    _order("time", DUP_TIME_CLASSES),
                    _order("levels", DUP_LEVEL_CLASSES),
                    _order("values", DUP_VALUE_CLASSES),
                    _order("position", DUP_POSITION_CLASSES),
                    "whole_degree",
                )
                .select(labels + [pl.all()])
            )
        across = (
            groups.filter(pl.col("n_platforms") > 1)
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
        "conversion_dup_checks": _concat(checks),
    }


def duplicate_rules() -> Dict[str, object]:
    """The classes and cuts of the duplicate checks, for ``meta.json``."""
    return {
        "time_classes": DUP_TIME_CLASSES,
        "time_limits_s": DUP_TIME_LIMITS_S,
        "same_time": DUP_SAME_TIME,
        "level_classes": DUP_LEVEL_CLASSES,
        "value_classes": DUP_VALUE_CLASSES,
        "value_tolerances": DUP_VALUE_TOLERANCES,
        "tolerance_slack": DUP_TOLERANCE_SLACK,
        "position_classes": DUP_POSITION_CLASSES,
        "whole_degree_tolerance": DUP_WHOLE_DEGREE_TOLERANCE,
        "matches": DUP_MATCHES,
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
        tables["conversion_pressure_order"] = pressure_order_table(
            config, spec.ctddump_reports
        )
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
