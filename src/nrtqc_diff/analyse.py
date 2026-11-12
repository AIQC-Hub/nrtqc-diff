"""
The analyses: the tables behind the site's Analyses pages and the manuscript.

``nrtqc-diff analyse`` reads the same NRT QC outputs as the build, plus the
ctddump and seastamp reports, and writes small tidy CSV tables to
``analyses.out_dir``. Figures are drawn from those tables by the R scripts
under ``analysis/``, and the pages read both. No table holds observations;
the largest lists a few thousand profiles. ``docs/ANALYSES.md`` documents
every table.

The analyses narrow the comparison in three steps, each one the subject of a
page:

1. **Land.** A profile whose position the bathymetry puts at or above sea
   level is filed under ``land`` and left out of everything after. It is the
   rule of nrtqclib's position on land test (RTQC4), which the batch did not
   run.
2. **Delayed mode.** A profile with a delayed mode observation is filed as
   ``delayed``, as on the rest of the site. An NRT profile whose input flags
   of one variable look like an operator's, because most of its
   observations are flagged by the input and not by nrtqclib, is filed as
   ``estimated_delayed`` for that variable. Temperature and salinity are
   judged apart: an operator often reworks one and not the other.
3. **What is left** is ``nrt`` (or ``unknown``), and the per-item tables of
   each variable are computed over the profiles ``nrt`` for it: two sets of
   automated flags, on positions at sea.

Like the build, nothing here holds a dataset in memory. Each dataset is read
in a streaming pass that yields one row per profile, which decides the
groups; then in passes that keep only the profiles of one group and group the
observations by a handful of small keys.
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import polars as pl

from nrtqc_diff.config import (
    DEFAULT_BAD_FLAG_VALUES,
    AnalysesSpec,
    BuildConfig,
    DatasetSpec,
    VariableSpec,
)
from nrtqc_diff.data_mode import (
    DATA_MODES,
    DELAYED,
    ESTIMATED_DELAYED,
    NRT,
    UNKNOWN,
    input_only_share,
    is_delayed,
    is_estimated,
    is_unknown,
    profile_data_mode,
    sea_floor_elevation,
)
from nrtqc_diff.flags import (
    AGREE_BAD,
    FLAG_GOOD,
    NRTQCLIB_ONLY,
    ORIGINAL_ONLY,
    STATUSES,
    computed_flag,
    is_one_of,
    item_columns,
    original_flag,
    status_expression,
    status_predicates,
    usable_flag,
)
from nrtqc_diff.reports import dropped_platform_table, report_tables

#: The version of the tables' shape, recorded in ``meta.json``. Raise it with
#: any change to a table's name or columns, so that a page or an R script
#: reading an older set can say so instead of drawing the wrong thing.
ANALYSES_FORMAT: int = 12

#: The key identifying a profile.
PROFILE_KEYS: List[str] = ["platform_code", "profile_no"]

#: A profile whose position is at or above sea level.
LAND: str = "land"

#: The groups every profile is filed under by the analyses, in the order
#: they are decided and shown. Land comes first: a profile on land is left
#: out whatever its data mode.
ANALYSIS_GROUPS: List[Dict[str, str]] = [
    {
        "key": LAND,
        "label": "On land",
        "description": (
            "The bathymetry puts the position at or above sea level. Left out "
            "of every analysis after the depth one."
        ),
    },
    {
        "key": DELAYED,
        "label": "Contains delayed mode",
        "description": (
            "At least one delayed mode observation, or a product whose flags "
            "come from its own delayed mode QC."
        ),
    },
    {
        "key": ESTIMATED_DELAYED,
        "label": "Estimated delayed mode",
        "description": (
            "Marked NRT, or with no data mode, but most of the variable's "
            "observations with a usable input flag are flagged by the input "
            "and not by nrtqclib, which is what an operator's edits look like. "
            "Decided per variable."
        ),
    },
    {
        "key": UNKNOWN,
        "label": "Data mode unknown",
        "description": (
            "No delayed mode observation, but at least one with no data mode."
        ),
    },
    {
        "key": NRT,
        "label": "NRT",
        "description": (
            "Every observation in real time or adjusted in real time, at sea, "
            "and not estimated to be delayed mode: the profiles the item "
            "comparison is made on."
        ),
    },
]

#: The input flag values the scheme calls probably good and probably bad.
#: nrtqclib never writes either, so the Delayed mode in NRT products page
#: describes how the input uses them rather than comparing them value for
#: value.
PROBABLE_FLAGS: List[int] = [2, 3]

#: Pressure bands of the per-depth tables, in dbar: the lower edges.
PRES_BREAKS: List[float] = [0.0, 10.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0]

#: Width of a share bin in the share histogram.
SHARE_BIN_WIDTH: float = 0.05

#: Width and reach of the elevation histogram, in metres.
ELEVATION_BIN_M: float = 5.0
ELEVATION_REACH_M: float = 100.0

#: A land profile at most this high (m) and this close to the coast (km) is
#: most likely a cast just off the shore, in a grid cell that is mostly land.
SHORE_HEIGHT_M: float = 5.0
SHORE_DISTANCE_KM: float = 1.0

#: How close (degrees) both coordinates must be to a whole degree for a
#: position to count as given in whole degrees.
WHOLE_DEGREE_TOLERANCE: float = 1e-6

#: The likely causes of a land profile, in the order they are tested.
LAND_CAUSES: List[str] = ["whole_degrees", "shore", "low_far", "inland"]

#: The flag values of ``position_qc`` that mean bad. The column follows the
#: same IOC/Argo scheme as the variables' flags.
POSITION_BAD_VALUES: List[int] = list(DEFAULT_BAD_FLAG_VALUES)

#: Profile columns copied from the input when present.
OPTIONAL_FIRST_COLUMNS: List[str] = ["profile_timestamp", "longitude", "latitude"]


def run_analyses(config: BuildConfig, verbose: bool = False) -> Dict[str, Any]:
    """
    Write every analysis table for the configured datasets.

    :param config: The build configuration, with an ``analyses`` section.
    :param verbose: Whether to print progress.
    :return: The ``meta.json`` that was written, as a dictionary.
    :raises ValueError: If the configuration has no ``analyses`` section or a
                        dataset lacks a column the analyses need.
    :raises FileNotFoundError: If a dataset's input parquet is missing.
    """
    spec = config.analyses
    if spec is None:
        raise ValueError(
            "The configuration has no 'analyses' section, which names where "
            "the analysis tables go and the reports they read."
        )

    tables: Dict[str, pl.DataFrame] = {}
    summaries: List[pl.DataFrame] = []
    for dataset in config.datasets:
        if verbose:
            print(f"[nrtqc-diff] {dataset.id}: summarising profiles")
        scan = _scan(dataset, spec)
        summaries.append(profile_summary(scan, dataset, config, spec))
    profiles = classify(pl.concat(summaries, how="diagonal_relaxed"), config, spec)

    tables.update(depth_tables(profiles, config, spec))
    tables.update(data_mode_tables(profiles, config, spec))

    land_flags: List[pl.DataFrame] = []
    input_flags: List[pl.DataFrame] = []
    probable_obs: List[pl.DataFrame] = []
    item_tables: Dict[str, List[pl.DataFrame]] = {}
    for dataset in config.datasets:
        scan = _scan(dataset, spec)
        mine = profiles.filter(pl.col("dataset_id") == dataset.id)
        input_flags.append(input_flag_table(scan, dataset, config))
        if dataset.data_mode is None:
            probable_obs.append(probable_flag_table(scan, mine, dataset, config))

        land = mine.filter(pl.col("land")).select(PROFILE_KEYS)
        if land.height:
            land_flags.append(land_flag_table(scan, land, dataset, config))

        nrt = {
            v.name: mine.filter(pl.col(f"{v.name}_group") == NRT).select(PROFILE_KEYS)
            for v in config.variables
        }
        if any(keys.height for keys in nrt.values()):
            if verbose:
                counts = ", ".join(f"{n} {k.height}" for n, k in nrt.items())
                print(
                    f"[nrtqc-diff] {dataset.id}: comparing items over the NRT "
                    f"profiles of each variable ({counts})"
                )
            for name, table in item_tables_for(scan, nrt, dataset, config).items():
                item_tables.setdefault(name, []).append(table)
            shapes = shape_table(scan, nrt, dataset, config)
            if shapes.height:
                item_tables.setdefault("items_shape", []).append(shapes)
            for name, table in response_tables(scan, nrt, dataset, config).items():
                if table.height:
                    item_tables.setdefault(name, []).append(table)

    if land_flags:
        tables["depth_land_flags"] = pl.concat(land_flags, how="diagonal_relaxed")
    tables["input_flags"] = pl.concat(input_flags, how="diagonal_relaxed")
    if probable_obs:
        tables["dm_probable_obs"] = pl.concat(probable_obs, how="diagonal_relaxed")
    for name, frames in item_tables.items():
        tables[name] = pl.concat(frames, how="diagonal_relaxed")

    reports = report_tables(config)
    if "conversion_steps" in reports:
        for message in check_conversion_steps(reports["conversion_steps"], profiles):
            print(f"[nrtqc-diff] warning: {message}", file=sys.stderr)
    tables.update(reports)
    if spec.ctddump_reports:
        drops = dropped_platform_table(config, spec.ctddump_reports)
        if drops.height:
            tables.update(dropped_tables(drops, config, spec))
    overlap = overlap_table(profiles, config)
    if overlap.height:
        tables["conversion_overlap"] = overlap
    comparisons = delayed_comparisons(profiles, config)
    tables.update(match_tables(profiles, config, comparisons))
    if comparisons:
        if verbose:
            print("[nrtqc-diff] comparing the flags of the casts paired with CORA")
        tables.update(cora_tables(comparisons, profiles, config, spec))

    meta = _write(tables, config, spec, profile_coverage(profiles))
    if verbose:
        print(f"[nrtqc-diff] wrote {len(tables)} tables to {spec.out_dir}")
    return meta


def _scan(dataset: DatasetSpec, spec: AnalysesSpec) -> pl.LazyFrame:
    """
    Open one NRT QC output, checking it carries the sea floor depth.

    :raises FileNotFoundError: If the parquet is missing.
    :raises ValueError: If the bathymetry column is absent.
    """
    if not os.path.exists(dataset.path):
        raise FileNotFoundError(
            f"Dataset '{dataset.id}' points at '{dataset.path}', which does not exist."
        )
    scan = pl.scan_parquet(dataset.path)
    if spec.bathymetry_column not in scan.collect_schema().names():
        raise ValueError(
            f"Dataset '{dataset.id}' has no '{spec.bathymetry_column}' column, "
            "which the depth analysis reads. Name the sea floor depth column "
            "as analyses.land.column."
        )
    return scan


def elevation(spec: AnalysesSpec) -> pl.Expr:
    """
    The height of the sea floor above sea level: negative under water.

    :param spec: The analyses section, for the column and its sign.
    :return: A ``Float64`` expression.
    """
    return sea_floor_elevation(spec.bathymetry_column, spec.positive_depth)


def profile_summary(
    scan: pl.LazyFrame,
    dataset: DatasetSpec,
    config: BuildConfig,
    spec: AnalysesSpec,
) -> pl.DataFrame:
    """
    One row per profile, with everything the groups are decided from.

    The elevation is the highest of the profile's observations: a profile
    with any position on land is on land, which is how nrtqclib's position on
    land test reads it too, since it judges each observation's position.

    :param scan: The dataset.
    :param dataset: The dataset's configuration.
    :param config: The build configuration.
    :param spec: The analyses section.
    :return: The profiles, with ``data_mode`` decided and the dataset's
             labels attached.
    """
    names = scan.collect_schema().names()
    aggregations: List[pl.Expr] = [
        pl.len().cast(pl.Int64).alias("n_obs"),
        elevation(spec).max().alias("elevation"),
        pl.col("pres").cast(pl.Float64, strict=False).max().alias("pres_max"),
    ]
    aggregations += [pl.col(c).first() for c in OPTIONAL_FIRST_COLUMNS if c in names]
    if "dist_to_coast" in names:
        aggregations.append(
            pl.col("dist_to_coast").cast(pl.Float64, strict=False).min()
        )
    # Taken as stored and cast after collecting. A cast inside the
    # aggregation, ahead of first(), makes polars materialise every group:
    # 1 GB on the Baltic file for this one column, and the Arctic ones are
    # ten times its size.
    flag_firsts = [c for c in ("position_qc", "time_qc") if c in names]
    aggregations += [pl.col(column).first() for column in flag_firsts]
    for variable in config.variables:
        predicates = status_predicates(variable)
        for status in STATUSES:
            aggregations.append(
                predicates[status["key"]]
                .sum()
                .cast(pl.Int64)
                .alias(f"{variable.name}_n_{status['key']}")
            )
        aggregations.append(
            usable_flag(variable)
            .sum()
            .cast(pl.Int64)
            .alias(f"{variable.name}_n_usable")
        )
        aggregations.append(
            is_one_of(original_flag(variable), variable.bad_flag_values)
            .sum()
            .cast(pl.Int64)
            .alias(f"{variable.name}_n_original_bad")
        )
        for flag in PROBABLE_FLAGS:
            aggregations.append(
                (original_flag(variable) == flag)
                .fill_null(False)
                .sum()
                .cast(pl.Int64)
                .alias(f"{variable.name}_n_flag{flag}")
            )
    aggregations.append(
        is_delayed(config.data_mode).sum().cast(pl.Int64).alias("n_obs_delayed")
    )
    aggregations.append(
        is_unknown(config.data_mode, names).sum().cast(pl.Int64).alias("n_obs_unknown")
    )

    return (
        scan.group_by(PROFILE_KEYS)
        .agg(aggregations)
        .collect(engine="streaming")
        .with_columns(
            *[pl.col(c).cast(pl.Int64, strict=False) for c in flag_firsts],
            pl.col("platform_code").cast(pl.Utf8),
            profile_data_mode(
                pl.col("n_obs_delayed"), pl.col("n_obs_unknown"), dataset.data_mode
            ).alias("data_mode"),
            pl.lit(dataset.id).alias("dataset_id"),
            pl.lit(dataset.region).alias("region"),
            pl.lit(dataset.product).alias("product"),
            pl.lit(dataset.label).alias("label"),
        )
    )


def classify(
    profiles: pl.DataFrame, config: BuildConfig, spec: AnalysesSpec
) -> pl.DataFrame:
    """
    File every profile under one of :data:`ANALYSIS_GROUPS`, per variable.

    Land and declared delayed mode are properties of the profile, so they
    are the same for every variable. The estimate is not: the share of a
    variable is the observations only the input flags, over those whose
    input flag says anything, computed only for a variable with at least
    ``estimated_min_observations`` such observations, and a profile is
    estimated delayed mode for each variable whose share reaches
    ``estimated_min_share`` (:func:`~nrtqc_diff.data_mode.is_estimated`, the
    rule the build applies too). An operator who reworked the salinity of a
    cast and left its temperature alone takes it out of the salinity
    comparison only.

    :param profiles: The output of :func:`profile_summary`, any number of
                     datasets stacked.
    :param config: The build configuration.
    :param spec: The analyses section.
    :return: The profiles with ``land`` added, and per variable
             ``{variable}_share``, ``{variable}_estimated`` and
             ``{variable}_group``.
    """
    land = (pl.col("elevation") >= 0).fill_null(False)
    rule = config.data_mode
    per_variable = []
    for variable in config.variables:
        name = variable.name
        share = input_only_share(name, rule)
        estimated = ~pl.col("land") & is_estimated(share, pl.col("data_mode"), rule)
        group = (
            pl.when(pl.col("land"))
            .then(pl.lit(LAND))
            .when(pl.col("data_mode") == DELAYED)
            .then(pl.lit(DELAYED))
            .when(estimated)
            .then(pl.lit(ESTIMATED_DELAYED))
            .otherwise(pl.col("data_mode"))
        )
        per_variable += [
            share.alias(f"{name}_share"),
            estimated.alias(f"{name}_estimated"),
            group.alias(f"{name}_group"),
        ]
    return profiles.with_columns(land.alias("land")).with_columns(per_variable)


def by_variable(profiles: pl.DataFrame, config: BuildConfig) -> pl.DataFrame:
    """
    The profiles once per variable, with that variable's columns under
    generic names: ``variable``, ``group``, ``share``, ``estimated``,
    ``n_usable`` and ``n_{status}``.
    """
    common = [
        c
        for c in _dataset_columns()
        + ["platform_code", "profile_no", "data_mode", "land", "n_obs"]
        if c in profiles.columns
    ]
    frames = []
    for variable in config.variables:
        name = variable.name
        frames.append(
            profiles.select(
                *common,
                pl.lit(name).alias("variable"),
                pl.col(f"{name}_group").alias("group"),
                pl.col(f"{name}_share").alias("share"),
                pl.col(f"{name}_estimated").alias("estimated"),
                pl.col(f"{name}_n_usable").alias("n_usable"),
                *[
                    pl.col(f"{name}_n_{s['key']}").alias(f"n_{s['key']}")
                    for s in STATUSES
                ],
                *[
                    pl.col(f"{name}_n_flag{flag}").alias(f"n_flag{flag}")
                    for flag in PROBABLE_FLAGS
                ],
            )
        )
    return pl.concat(frames)


def _dataset_columns() -> List[str]:
    return ["dataset_id", "region", "product", "label"]


def depth_tables(
    profiles: pl.DataFrame, config: BuildConfig, spec: AnalysesSpec
) -> Dict[str, pl.DataFrame]:
    """
    The tables of the Depth page, from the profile summary alone.

    :return: ``depth_summary``, ``depth_land_margin``,
             ``depth_elevation_hist``, ``depth_land_position_qc``,
             ``depth_land_original``, ``depth_land_profiles`` and
             ``depth_land_cause``.
    """
    keys = _dataset_columns()
    land = pl.col("land")
    position_bad = (
        is_one_of(pl.col("position_qc"), POSITION_BAD_VALUES)
        if "position_qc" in profiles.columns
        else pl.lit(False)
    )
    summary = profiles.group_by(keys, maintain_order=True).agg(
        pl.len().cast(pl.Int64).alias("n_profiles"),
        pl.col("n_obs").sum(),
        pl.col("elevation").is_null().sum().cast(pl.Int64).alias("n_no_bathymetry"),
        land.sum().cast(pl.Int64).alias("n_land"),
        pl.col("n_obs").filter(land).sum().alias("n_land_obs"),
        (land & position_bad).sum().cast(pl.Int64).alias("n_land_position_bad"),
        *[
            (land & (pl.col(f"{v.name}_n_original_bad") > 0))
            .sum()
            .cast(pl.Int64)
            .alias(f"n_land_{v.name}_any_bad")
            for v in config.variables
        ],
        (
            land
            & ~position_bad
            & pl.all_horizontal(
                [pl.col(f"{v.name}_n_original_bad") == 0 for v in config.variables]
            )
        )
        .sum()
        .cast(pl.Int64)
        .alias("n_land_unflagged"),
    )

    margin = pl.concat(
        [
            profiles.group_by(keys, maintain_order=True).agg(
                pl.lit(m).alias("margin_m"),
                (pl.col("elevation") >= m).sum().cast(pl.Int64).alias("n_profiles"),
            )
            for m in spec.land_margins_m
        ]
    )

    reach = ELEVATION_REACH_M
    width = ELEVATION_BIN_M
    hist = (
        profiles.filter(pl.col("elevation").is_not_null())
        .with_columns(
            pl.col("elevation")
            .clip(-reach - width, reach)
            .truediv(width)
            .floor()
            .mul(width)
            .alias("bin_lo")
        )
        .group_by(keys + ["bin_lo"], maintain_order=True)
        .agg(pl.len().cast(pl.Int64).alias("n_profiles"))
        .with_columns((pl.col("bin_lo") + width).alias("bin_hi"))
        .sort(keys + ["bin_lo"])
    )

    tables = {
        "depth_summary": summary,
        "depth_land_margin": margin,
        "depth_elevation_hist": hist,
    }

    on_land = profiles.filter(land)
    if "position_qc" in profiles.columns:
        tables["depth_land_position_qc"] = (
            profiles.with_columns(
                pl.when(land)
                .then(pl.lit("land"))
                .otherwise(pl.lit("sea"))
                .alias("scope")
            )
            .group_by(keys + ["scope", "position_qc"], maintain_order=True)
            .agg(pl.len().cast(pl.Int64).alias("n_profiles"))
            .sort(keys + ["scope", "position_qc"])
        )

    classes = []
    for variable in config.variables:
        bad = pl.col(f"{variable.name}_n_original_bad")
        usable = pl.col(f"{variable.name}_n_usable")
        classes.append(
            on_land.with_columns(
                pl.lit(variable.name).alias("variable"),
                pl.when(usable == 0)
                .then(pl.lit("no_usable_flag"))
                .when(bad >= usable)
                .then(pl.lit("all_bad"))
                .when(bad > 0)
                .then(pl.lit("some_bad"))
                .otherwise(pl.lit("none_bad"))
                .alias("original"),
            )
            .group_by(keys + ["variable", "original"], maintain_order=True)
            .agg(pl.len().cast(pl.Int64).alias("n_profiles"))
        )
    if classes:
        tables["depth_land_original"] = pl.concat(classes)

    listed = [
        c
        for c in (
            ["dataset_id", "platform_code", "profile_no"]
            + OPTIONAL_FIRST_COLUMNS
            + ["elevation", "dist_to_coast", "position_qc", "data_mode", "n_obs"]
            + ["pres_max"]
            + [f"{v.name}_n_usable" for v in config.variables]
            + [f"{v.name}_n_original_bad" for v in config.variables]
            + [f"{v.name}_n_{NRTQCLIB_ONLY}" for v in config.variables]
        )
        if c in on_land.columns
    ]
    tables["depth_land_profiles"] = on_land.select(listed).sort(
        ["dataset_id", "platform_code", "profile_no"]
    )
    if {"longitude", "latitude", "dist_to_coast"} <= set(on_land.columns):
        tables["depth_land_cause"] = land_causes(on_land)
    return tables


def _whole_degree(column: str) -> pl.Expr:
    value = pl.col(column).cast(pl.Float64, strict=False)
    return (value - value.round(0)).abs() <= WHOLE_DEGREE_TOLERANCE


def _land_cause() -> pl.Expr:
    """The first of :data:`LAND_CAUSES` that fits, as :func:`land_causes` says."""
    low = pl.col("elevation") <= SHORE_HEIGHT_M
    return (
        pl.when(_whole_degree("longitude") & _whole_degree("latitude"))
        .then(pl.lit("whole_degrees"))
        .when(low & (pl.col("dist_to_coast") <= SHORE_DISTANCE_KM))
        .then(pl.lit("shore"))
        .when(low)
        .then(pl.lit("low_far"))
        .otherwise(pl.lit("inland"))
    )


def land_causes(on_land: pl.DataFrame) -> pl.DataFrame:
    """
    The land profiles by their likely cause, the first of
    :data:`LAND_CAUSES` that fits:

    - ``whole_degrees``: both coordinates are whole degrees, a station placed
      to the nearest degree, wherever that falls;
    - ``shore``: at most :data:`SHORE_HEIGHT_M` up and
      :data:`SHORE_DISTANCE_KM` from the coast, a cast just off the shore
      in a grid cell that is mostly land;
    - ``low_far``: as low, but further from the coast, such as a lagoon or
      fjord the grid puts at sea level;
    - ``inland``: higher than that, a wrong position.

    :param on_land: The land profiles, with ``longitude``, ``latitude``,
                    ``elevation`` and ``dist_to_coast``.
    :return: One row per dataset and cause, with its profiles and positions.
    """
    keys = _dataset_columns()
    order = {name: i for i, name in enumerate(LAND_CAUSES)}
    return (
        on_land.with_columns(_land_cause().alias("cause"))
        .group_by(keys + ["cause"], maintain_order=True)
        .agg(
            pl.len().cast(pl.Int64).alias("n_profiles"),
            pl.struct("longitude", "latitude")
            .n_unique()
            .cast(pl.Int64)
            .alias("n_positions"),
        )
        .with_columns(
            pl.col("cause").replace_strict(order, return_dtype=pl.Int64).alias("_o")
        )
        .sort(keys[:1] + ["_o"])
        .drop("_o")
    )


#: How far, in degrees, the batch's position of a fixed platform may be from
#: the one ctddump's report gives (written to four decimals) and still be
#: taken as the same point.
DROPPED_POSITION_TOLERANCE: float = 1e-3

#: What is known of where a dropped profile was, in the order the tables
#: list it: on a moving platform, so nowhere; on a fixed platform the batch
#: holds no stamped profile of; at sea; on land.
DROPPED_STATUSES: List[str] = ["moving", "not_stamped", "sea", "land"]


def dropped_tables(
    drops: pl.DataFrame, config: BuildConfig, spec: AnalysesSpec
) -> Dict[str, pl.DataFrame]:
    """
    Where the profiles ctddump's ``dropqc`` dropped were, as far as is known.

    seastamp ran after ``dropqc``, so no dropped profile was stamped. A
    fixed platform's dropped profiles were at its one point, which the
    batch stamped wherever the platform has a profile left: that stamp is
    taken, graded by the land rule and the causes of :func:`land_causes`.
    A moving platform's dropped profiles cannot be placed from the reports.

    :param drops: :func:`~nrtqc_diff.reports.dropped_platform_table`.
    :param config: The build configuration, for the datasets' paths.
    :param spec: The analyses section, for the sea floor column.
    :return: ``conversion_dropqc``, one row per dataset, and
             ``conversion_dropqc_platforms``, one row per platform that lost
             a profile.
    """
    keys = _dataset_columns()
    stamps = []
    for dataset in config.datasets:
        fixed = drops.filter(
            (pl.col("dataset_id") == dataset.id)
            & pl.col("fixed")
            & (pl.col("n_dropped") > 0)
        )
        if not fixed.height or not os.path.exists(dataset.path):
            continue
        stamps.append(
            _scan(dataset, spec)
            .filter(
                pl.col("platform_code")
                .cast(pl.Utf8)
                .is_in(fixed["platform_code"].implode())
            )
            .group_by(pl.col("platform_code").cast(pl.Utf8))
            .agg(
                pl.col("longitude").cast(pl.Float64).first().alias("stamp_longitude"),
                pl.col("latitude").cast(pl.Float64).first().alias("stamp_latitude"),
                elevation(spec).first().alias("elevation"),
                pl.col("dist_to_coast").cast(pl.Float64).first(),
                pl.struct("longitude", "latitude").n_unique().alias("_n_positions"),
            )
            .with_columns(pl.lit(dataset.id).alias("dataset_id"))
            .collect()
        )
    columns = {
        "dataset_id": pl.Utf8,
        "platform_code": pl.Utf8,
        "stamp_longitude": pl.Float64,
        "stamp_latitude": pl.Float64,
        "elevation": pl.Float64,
        "dist_to_coast": pl.Float64,
        "_n_positions": pl.UInt32,
    }
    stamped = (
        pl.concat(
            [s.select([pl.col(c).cast(t) for c, t in columns.items()]) for s in stamps]
        )
        if stamps
        else pl.DataFrame(schema=columns)
    )

    # The stamp counts only if the batch holds the platform at one point,
    # the report's own.
    same_point = (
        (pl.col("_n_positions") == 1)
        & (
            (pl.col("stamp_longitude") - pl.col("longitude")).abs()
            <= DROPPED_POSITION_TOLERANCE
        )
        & (
            (pl.col("stamp_latitude") - pl.col("latitude")).abs()
            <= DROPPED_POSITION_TOLERANCE
        )
    ).fill_null(False)
    platforms = (
        drops.filter(pl.col("n_dropped") > 0)
        .join(stamped, on=["dataset_id", "platform_code"], how="left")
        .with_columns(same_point.alias("_stamped"))
        .with_columns(
            pl.when(~pl.col("fixed"))
            .then(pl.lit("moving"))
            .when(~pl.col("_stamped"))
            .then(pl.lit("not_stamped"))
            .when(pl.col("elevation") >= 0)
            .then(pl.lit("land"))
            .otherwise(pl.lit("sea"))
            .alias("status"),
            pl.when(pl.col("_stamped")).then(pl.col("elevation")).alias("elevation"),
            pl.when(pl.col("_stamped"))
            .then(pl.col("dist_to_coast"))
            .alias("dist_to_coast"),
        )
        .with_columns(
            pl.when(pl.col("status") == "land").then(_land_cause()).alias("cause")
        )
        .select(
            keys
            + [
                "platform_code",
                "n_before",
                "n_dropped",
                "status",
                "cause",
                "longitude",
                "latitude",
                "elevation",
                "dist_to_coast",
            ]
        )
        .sort(["dataset_id", "platform_code"])
    )

    def dropped_where(condition: pl.Expr, name: str) -> pl.Expr:
        return pl.col("n_dropped").filter(condition).sum().cast(pl.Int64).alias(name)

    summary = (
        drops.group_by(keys, maintain_order=True)
        .agg(pl.col("n_before").sum().cast(pl.Int64).alias("n_profiles"))
        .join(
            platforms.group_by(keys).agg(
                pl.col("n_dropped").sum().cast(pl.Int64),
                *[
                    dropped_where(pl.col("status") == s, f"n_{s}")
                    for s in DROPPED_STATUSES
                ],
                *[
                    dropped_where(pl.col("cause") == c, f"n_land_{c}")
                    for c in LAND_CAUSES
                ],
            ),
            on=keys,
            how="left",
        )
        .with_columns(pl.exclude(keys + ["n_profiles"]).fill_null(0))
    )
    return {
        "conversion_dropqc": summary,
        "conversion_dropqc_platforms": platforms,
    }


def data_mode_tables(
    profiles: pl.DataFrame, config: BuildConfig, spec: AnalysesSpec
) -> Dict[str, pl.DataFrame]:
    """
    The tables of the Delayed mode in NRT products page, from the profile
    summary. Every table has a ``variable`` column: the groups are decided
    per variable (:func:`classify`).

    A dataset the configuration files whole under one data mode (CORA, whose
    flags come from its own delayed mode QC) is left out: none of its
    profiles can be estimated, and it would only add rows that are all
    delayed mode.

    :return: ``dm_groups``, ``dm_share_hist``, ``dm_threshold``,
             ``dm_estimated_platforms``, ``dm_estimated_profiles`` and
             ``dm_probable_profiles``.
    """
    whole = [d.id for d in config.datasets if d.data_mode is not None]
    profiles = profiles.filter(~pl.col("dataset_id").is_in(whole))
    keys = _dataset_columns() + ["variable"]
    long = by_variable(profiles, config)
    at_sea = long.filter(~pl.col("land"))
    eligible = at_sea.filter(pl.col("share").is_not_null())

    sums = [pl.len().cast(pl.Int64).alias("n_profiles"), pl.col("n_obs").sum()]
    sums += [pl.col(f"n_{s['key']}").sum() for s in STATUSES]
    groups = (
        long.group_by(keys + ["data_mode", "group"], maintain_order=True)
        .agg(sums)
        .sort(keys + ["data_mode", "group"])
    )

    width = SHARE_BIN_WIDTH
    share = pl.col("share")
    lower = (
        pl.when(share <= 0)
        .then(0.0)
        .when(share >= 1)
        .then(1.0)
        .otherwise((share / width).floor() * width)
    )
    upper = (
        pl.when(share <= 0)
        .then(0.0)
        .when(share >= 1)
        .then(1.0)
        .otherwise((share / width).floor() * width + width)
    )
    hist = (
        eligible.with_columns(
            lower.round(4).alias("bin_lo"), upper.round(4).alias("bin_hi")
        )
        .group_by(keys + ["data_mode", "bin_lo", "bin_hi"], maintain_order=True)
        .agg(pl.len().cast(pl.Int64).alias("n_profiles"), pl.col("n_obs").sum())
        .sort(keys + ["data_mode", "bin_lo"])
    )

    thresholds = sorted(
        set(spec.estimated_sensitivity) | {config.data_mode.estimated_min_share}
    )
    threshold = pl.concat(
        [
            eligible.group_by(keys + ["data_mode"], maintain_order=True).agg(
                pl.lit(t).alias("threshold"),
                pl.len().cast(pl.Int64).alias("n_eligible"),
                (share >= t).sum().cast(pl.Int64).alias("n_profiles"),
                pl.col("n_obs").filter(share >= t).sum().alias("n_obs"),
            )
            for t in thresholds
        ]
    ).sort(keys + ["data_mode", "threshold"])

    candidate = pl.col("data_mode").is_in(config.data_mode.estimated_groups)
    platforms = (
        at_sea.group_by(keys + ["platform_code"], maintain_order=True)
        .agg(
            pl.len().cast(pl.Int64).alias("n_profiles"),
            candidate.sum().cast(pl.Int64).alias("n_candidates"),
            pl.col("estimated").sum().cast(pl.Int64).alias("n_estimated"),
            (pl.col("data_mode") == DELAYED).sum().cast(pl.Int64).alias("n_delayed"),
        )
        .filter(pl.col("n_estimated") > 0)
        .sort(
            ["dataset_id", "variable", "n_estimated"], descending=[False, False, True]
        )
    )

    listed = [
        c
        for c in (
            ["dataset_id", "platform_code", "profile_no"]
            + OPTIONAL_FIRST_COLUMNS
            + ["data_mode", "n_obs"]
            + [f"{v.name}_n_usable" for v in config.variables]
            + [f"{v.name}_n_{ORIGINAL_ONLY}" for v in config.variables]
            + [f"{v.name}_share" for v in config.variables]
            + [f"{v.name}_estimated" for v in config.variables]
        )
        if c in profiles.columns
    ]
    estimated = (
        profiles.filter(
            pl.any_horizontal([pl.col(f"{v.name}_estimated") for v in config.variables])
        )
        .select(listed)
        .sort(["dataset_id", "platform_code", "profile_no"])
    )
    # How flags 2 and 3 sit in their profiles: one observation, or most of
    # the profile by the estimate's own measure (the share of the
    # observations with a usable input flag, counted only with enough of
    # them).
    rule = config.data_mode
    probable = []
    for flag in PROBABLE_FLAGS:
        count = pl.col(f"n_flag{flag}")
        most = (pl.col("n_usable") >= rule.estimated_min_observations) & (
            count >= rule.estimated_min_share * pl.col("n_usable")
        )
        probable.append(
            at_sea.filter(count > 0)
            .group_by(keys + ["group"], maintain_order=True)
            .agg(
                pl.lit(flag).alias("flag"),
                pl.len().cast(pl.Int64).alias("n_profiles"),
                (count == 1).sum().cast(pl.Int64).alias("n_profiles_single"),
                most.sum().cast(pl.Int64).alias("n_profiles_most"),
                count.sum().cast(pl.Int64).alias("n_obs"),
            )
        )
    probable_profiles = pl.concat(probable).sort(keys + ["flag", "group"])

    return {
        "dm_groups": groups,
        "dm_share_hist": hist,
        "dm_threshold": threshold,
        "dm_estimated_platforms": platforms,
        "dm_estimated_profiles": estimated,
        "dm_probable_profiles": probable_profiles,
    }


def land_flag_table(
    scan: pl.LazyFrame,
    land: pl.DataFrame,
    dataset: DatasetSpec,
    config: BuildConfig,
) -> pl.DataFrame:
    """
    The input flag values of the observations of land profiles.

    :param scan: The dataset.
    :param land: The keys of its land profiles.
    :param dataset: The dataset's configuration.
    :param config: The build configuration.
    :return: One row per variable and input flag value.
    """
    rows = (
        scan.join(land.lazy(), on=PROFILE_KEYS, how="semi")
        .select([original_flag(v).alias(v.name) for v in config.variables])
        .collect(engine="streaming")
    )
    frames = [
        rows.group_by(v.name)
        .agg(pl.len().cast(pl.Int64).alias("n_obs"))
        .select(
            pl.lit(dataset.id).alias("dataset_id"),
            pl.lit(v.name).alias("variable"),
            pl.col(v.name).alias("original_flag"),
            "n_obs",
        )
        .sort("original_flag")
        for v in config.variables
    ]
    return pl.concat(frames)


def input_flag_table(
    scan: pl.LazyFrame, dataset: DatasetSpec, config: BuildConfig
) -> pl.DataFrame:
    """
    Every observation of one dataset counted by the value of its input flag.

    All of the dataset is counted, land and delayed mode profiles too: the
    table describes the input as it is, like the regional summary reports,
    rather than the subset an analysis compares.

    The regional reports keep only profiles whose ``position_qc`` is good,
    which drops a profile with no position flag at all. Whether a profile
    has one is kept as a column, so a page can count both ways.

    :param scan: The dataset.
    :param dataset: The dataset's configuration.
    :param config: The build configuration.
    :return: One row per variable, input flag value (null where the input
             has no flag) and ``has_position_qc``, with ``n_obs``.
    """
    names = scan.collect_schema().names()
    has_position = (
        pl.col("position_qc").cast(pl.Int64, strict=False).is_not_null()
        if "position_qc" in names
        else pl.lit(None, dtype=pl.Boolean)
    )
    flags = [original_flag(v).alias(v.name) for v in config.variables]
    counted = (
        scan.select(flags + [has_position.alias("has_position_qc")])
        .group_by(pl.all())
        .agg(pl.len().cast(pl.Int64).alias("n_obs"))
        .collect(engine="streaming")
    )
    label = [
        pl.lit(dataset.id).alias("dataset_id"),
        pl.lit(dataset.region).alias("region"),
        pl.lit(dataset.product).alias("product"),
        pl.lit(dataset.label).alias("label"),
    ]
    per_variable = [
        counted.group_by(pl.col(v.name).alias("original_flag"), "has_position_qc")
        .agg(pl.col("n_obs").sum())
        .select(label + [pl.lit(v.name).alias("variable"), pl.all()])
        for v in config.variables
    ]
    return pl.concat(per_variable).sort(
        ["variable", "original_flag", "has_position_qc"], nulls_last=True
    )


def probable_flag_table(
    scan: pl.LazyFrame,
    profiles: pl.DataFrame,
    dataset: DatasetSpec,
    config: BuildConfig,
) -> pl.DataFrame:
    """
    The observations the input flags 2 or 3, at sea, by what is known of them.

    Each is counted by its flag, the data mode of the observation itself
    (the variable's own data mode column, empty where ctddump found none),
    the group its profile is filed under for the variable, and whether
    nrtqclib flags it bad, with the number of them each nrtqclib check
    failed (``fail_{item}``). The data cannot say who set a flag; these are
    the signs the page reads it by.

    :param scan: The dataset.
    :param profiles: The dataset's classified profiles.
    :param dataset: The dataset's configuration.
    :param config: The build configuration.
    :return: One row per variable, flag, ``obs_data_mode``, ``group`` and
             ``nrtqclib_bad``, with ``n_obs`` and the ``fail_`` counts.
    """
    names = scan.collect_schema().names()
    variable_names = [v.name for v in config.variables]
    sea = profiles.filter(~pl.col("land"))
    label = [
        pl.lit(dataset.id).alias("dataset_id"),
        pl.lit(dataset.region).alias("region"),
        pl.lit(dataset.product).alias("product"),
        pl.lit(dataset.label).alias("label"),
    ]
    frames = []
    for variable in config.variables:
        mode_column = f"{variable.name}_dm"
        mode = (
            pl.col(mode_column).cast(pl.Utf8).fill_null("")
            if mode_column in names and mode_column in config.data_mode.columns
            else pl.lit("")
        )
        columns = [
            c
            for c in item_columns(names, variable_names)
            if c.startswith((f"{variable.name}_qc_", "qc_"))
        ]
        groups = sea.select(
            PROFILE_KEYS + [pl.col(f"{variable.name}_group").alias("group")]
        )
        flag = original_flag(variable)
        counted = (
            scan.filter(flag.is_in(PROBABLE_FLAGS))
            .with_columns(pl.col("platform_code").cast(pl.Utf8))
            .join(groups.lazy(), on=PROFILE_KEYS, how="inner")
            .group_by(
                flag.alias("flag"),
                mode.alias("obs_data_mode"),
                "group",
                (computed_flag(variable) > FLAG_GOOD)
                .fill_null(False)
                .alias("nrtqclib_bad"),
            )
            .agg(
                pl.len().cast(pl.Int64).alias("n_obs"),
                *[
                    (pl.col(c).cast(pl.Int64, strict=False) > FLAG_GOOD)
                    .fill_null(False)
                    .sum()
                    .cast(pl.Int64)
                    .alias(f"fail_{item_name(c, variable)}")
                    for c in columns
                ],
            )
            .collect(engine="streaming")
        )
        frames.append(
            counted.select(label + [pl.lit(variable.name).alias("variable"), pl.all()])
        )
    return pl.concat(frames, how="diagonal_relaxed").sort(
        ["variable", "flag", "obs_data_mode", "group", "nrtqclib_bad"]
    )


def item_name(column: str, variable: VariableSpec) -> str:
    """The item part of a per-item column: ``spike`` of ``temp_qc_spike``."""
    for prefix in (f"{variable.name}_qc_", "qc_"):
        if column.startswith(prefix):
            return column[len(prefix) :]
    return column


def pres_band() -> pl.Expr:
    """The index of the :data:`PRES_BREAKS` band of each observation, -1 for none."""
    pres = pl.col("pres").cast(pl.Float64, strict=False)
    index = pl.sum_horizontal(
        [(pres >= edge).cast(pl.Int8) for edge in PRES_BREAKS[1:]]
    )
    return pl.when(pres.is_null()).then(-1).otherwise(index).cast(pl.Int8)


def pres_band_labels() -> List[Dict[str, Any]]:
    """The bands of :func:`pres_band`, in words, for the tables and meta.json."""
    bands = []
    for index, lower in enumerate(PRES_BREAKS):
        upper = PRES_BREAKS[index + 1] if index + 1 < len(PRES_BREAKS) else None
        if index == 0:
            label = f"< {upper:g}"
        elif upper is None:
            label = f">= {lower:g}"
        else:
            label = f"{lower:g}-{upper:g}"
        bands.append({"band": index, "label": label, "lower": lower, "upper": upper})
    bands.append({"band": -1, "label": "no pressure", "lower": None, "upper": None})
    return bands


def item_tables_for(
    scan: pl.LazyFrame,
    keep: Dict[str, pl.DataFrame],
    dataset: DatasetSpec,
    config: BuildConfig,
) -> Dict[str, pl.DataFrame]:
    """
    The per-item tables of one dataset, each variable over its own profiles.

    Every table is derived from one cube per variable: the observations
    counted by pressure band, input flag, computed flag and which items
    failed. The agreement category is a function of the two flags, so it is
    attached to the cube after collecting it rather than computed per row.

    :param scan: The dataset.
    :param keep: Per variable name, the keys of the profiles to count it
                 over: a profile estimated delayed mode for salinity is left
                 out of the salinity tables only.
    :param dataset: The dataset's configuration.
    :param config: The build configuration.
    :return: ``items_cube``, ``items_status``, ``items_by_item``,
             ``items_combinations``, ``items_depth``, ``items_original_flags``
             and ``items_platforms``.
    """
    names = scan.collect_schema().names()
    variable_names = [v.name for v in config.variables]

    def kept_for(variable: VariableSpec) -> pl.LazyFrame:
        return scan.join(keep[variable.name].lazy(), on=PROFILE_KEYS, how="semi")

    bands = {b["band"]: b["label"] for b in pres_band_labels()}

    queries = []
    item_lists = []
    for variable in config.variables:
        columns = [
            c
            for c in item_columns(names, variable_names)
            if c.startswith((f"{variable.name}_qc_", "qc_"))
        ]
        item_lists.append(columns)
        fails = [
            (pl.col(c).cast(pl.Int64, strict=False) > FLAG_GOOD)
            .fill_null(False)
            .alias(item_name(c, variable))
            for c in columns
        ]
        keys = [
            pres_band().alias("pres_band"),
            original_flag(variable).alias(variable.flag),
            pl.col(variable.nrt_flag)
            .cast(pl.Int64, strict=False)
            .alias(variable.nrt_flag),
        ] + fails
        queries.append(
            kept_for(variable)
            .select(keys)
            .group_by(pl.all())
            .agg(pl.len().cast(pl.Int64).alias("n_obs"))
        )

    # Per platform, each variable over its own profiles.
    for variable in config.variables:
        predicates = status_predicates(variable)
        queries.append(
            kept_for(variable)
            .group_by(pl.col("platform_code").cast(pl.Utf8))
            .agg(
                pl.col("profile_no").n_unique().cast(pl.Int64).alias("n_profiles"),
                pl.len().cast(pl.Int64).alias("n_obs"),
                *[
                    predicates[status["key"]]
                    .sum()
                    .cast(pl.Int64)
                    .alias(f"n_{status['key']}")
                    for status in STATUSES
                ],
            )
            .with_columns(pl.lit(variable.name).alias("variable"))
        )

    # One at a time: collect_all runs the queries side by side, and on the
    # Arctic NRT file that doubled the peak for no gain, since each query
    # reads the file on its own anyway.
    results = [query.collect(engine="streaming") for query in queries]
    label = [
        pl.lit(dataset.id).alias("dataset_id"),
        pl.lit(dataset.region).alias("region"),
        pl.lit(dataset.product).alias("product"),
        pl.lit(dataset.label).alias("label"),
    ]

    cubes, status, by_item, combos, depth, flags = [], [], [], [], [], []
    n_variables = len(config.variables)
    cube_results, platform_results = results[:n_variables], results[n_variables:]
    for variable, columns, cube in zip(config.variables, item_lists, cube_results):
        items = [item_name(c, variable) for c in columns]
        cube = cube.with_columns(
            status_expression(variable).alias("status"),
            pl.col("pres_band")
            .replace_strict(bands, return_dtype=pl.Utf8)
            .alias("pres_label"),
            pl.sum_horizontal([pl.col(i).cast(pl.Int8) for i in items])
            .cast(pl.Int64)
            .alias("n_items_failed")
            if items
            else pl.lit(0, dtype=pl.Int64).alias("n_items_failed"),
        )
        head = label + [pl.lit(variable.name).alias("variable")]

        cubes.append(
            cube.select(
                head
                + [
                    "pres_band",
                    "pres_label",
                    pl.col(variable.flag).alias("original_flag"),
                    pl.col(variable.nrt_flag).alias("nrtqclib_flag"),
                    "status",
                    "n_items_failed",
                ]
                + [pl.col(i).cast(pl.Int8) for i in items]
                + ["n_obs"]
            )
        )
        status.append(
            cube.group_by("status").agg(pl.col("n_obs").sum()).select(head + [pl.all()])
        )
        depth.append(
            cube.group_by("pres_band", "pres_label", "status")
            .agg(pl.col("n_obs").sum())
            .select(head + [pl.all()])
        )
        flags.append(
            cube.group_by(
                pl.col(variable.flag).alias("original_flag"),
                (pl.col(variable.nrt_flag) > FLAG_GOOD)
                .fill_null(False)
                .alias("nrtqclib_bad"),
            )
            .agg(pl.col("n_obs").sum())
            .select(head + [pl.all()])
        )
        for item, column in zip(items, columns):
            fired = cube.filter(pl.col(item))
            only = fired.filter(pl.col("status") == NRTQCLIB_ONLY)
            by_item.append(
                pl.DataFrame(
                    {
                        "item": [item],
                        "column": [column],
                        "n_fail": [int(fired["n_obs"].sum())],
                        "n_fail_agree_bad": [
                            int(
                                fired.filter(pl.col("status") == AGREE_BAD)[
                                    "n_obs"
                                ].sum()
                            )
                        ],
                        "n_fail_nrtqclib_only": [int(only["n_obs"].sum())],
                        "n_alone_nrtqclib_only": [
                            int(
                                only.filter(pl.col("n_items_failed") == 1)[
                                    "n_obs"
                                ].sum()
                            )
                        ],
                    }
                ).select(head + [pl.all()])
            )
        if items:
            combination = pl.concat_str(
                [pl.when(pl.col(i)).then(pl.lit(i)) for i in items],
                separator="+",
                ignore_nulls=True,
            )
            combos.append(
                cube.filter(
                    pl.col("status").is_in([AGREE_BAD, NRTQCLIB_ONLY])
                    & (pl.col("n_items_failed") > 0)
                )
                .group_by("status", combination.alias("combination"), "n_items_failed")
                .agg(pl.col("n_obs").sum())
                .sort("n_obs", descending=True)
                .select(head + [pl.all()])
            )

    tables = {
        "items_cube": pl.concat(cubes, how="diagonal_relaxed"),
        "items_status": pl.concat(status),
        "items_depth": pl.concat(depth).sort(["variable", "pres_band", "status"]),
        "items_original_flags": pl.concat(flags).sort(
            ["variable", "original_flag", "nrtqclib_bad"], nulls_last=True
        ),
        "items_platforms": pl.concat(platform_results)
        .select(label + ["variable", pl.all().exclude("variable")])
        .sort("variable", "platform_code"),
    }
    if by_item:
        tables["items_by_item"] = pl.concat(by_item)
    if combos:
        tables["items_combinations"] = pl.concat(combos)
    return tables


#: Where an observation one source alone flags sits in its profile, judged
#: against the observations that source calls good, in the order the pages
#: list them. ``whole``: the source calls none of the profile's observations
#: good. ``top`` and ``bottom``: shallower than the shallowest, or deeper than
#: the deepest, observation it calls good. ``inside_alone``: between them, the
#: only one of its profile; ``inside_several``: between them, with others.
#: ``no_pressure``: the observation has no pressure to place it by.
SHAPES: List[str] = [
    "whole",
    "top",
    "bottom",
    "inside_alone",
    "inside_several",
    "no_pressure",
]

#: The :data:`SHAPES` in words, for meta.json, so the figures and the pages
#: name them the same way.
SHAPE_LABELS: Dict[str, str] = {
    "whole": "The whole profile",
    "top": "A stretch at the top",
    "bottom": "A stretch at the bottom",
    "inside_alone": "A single level inside",
    "inside_several": "Several levels inside",
    "no_pressure": "No pressure",
}


def shape_table(
    scan: pl.LazyFrame,
    keep: Dict[str, pl.DataFrame],
    dataset: DatasetSpec,
    config: BuildConfig,
) -> pl.DataFrame:
    """
    Where the observations one source alone flags sit in their profiles.

    An automated test that judges a whole profile flags all of it; one that
    judges a level flags a point; a soaking depth left in, or a cast that
    touched the bottom, flags a stretch at one end. Each observation only
    one source flags is placed against the observations that same source
    calls good in its profile, by pressure, as :data:`SHAPES` describes.

    Two streaming passes per variable: the pressure range of the good
    observations per profile, then the one-sided observations placed in it.
    Neither holds more than one row per profile and kind in memory.

    :param scan: The dataset.
    :param keep: Per variable name, the keys of the profiles to count it
                 over, as for :func:`item_tables_for`.
    :param dataset: The dataset's configuration.
    :param config: The build configuration.
    :return: One row per variable, ``status`` (original only or nrtqclib
             only), ``shape``, input flag and whether the pressure is
             negative, with the observations and the profiles they are in.
    """
    pres = pl.col("pres").cast(pl.Float64, strict=False)
    sides = [ORIGINAL_ONLY, NRTQCLIB_ONLY]
    label = [
        pl.lit(dataset.id).alias("dataset_id"),
        pl.lit(dataset.region).alias("region"),
        pl.lit(dataset.product).alias("product"),
        pl.lit(dataset.label).alias("label"),
    ]
    frames = []
    for variable in config.variables:
        if not keep[variable.name].height:
            continue
        predicates = status_predicates(variable)
        bad_existing = is_one_of(original_flag(variable), variable.bad_flag_values)
        computed = computed_flag(variable)
        good = {
            ORIGINAL_ONLY: usable_flag(variable) & ~bad_existing,
            NRTQCLIB_ONLY: computed.is_not_null() & (computed <= FLAG_GOOD),
        }
        rows = scan.join(keep[variable.name].lazy(), on=PROFILE_KEYS, how="semi")

        # The good pressures of each side per profile. when/then rather
        # than a filter inside the aggregation, which keeps it streaming.
        bounds = (
            rows.group_by(PROFILE_KEYS)
            .agg(
                *[
                    agg
                    for side in sides
                    for agg in (
                        pl.when(good[side]).then(pres).min().alias(f"{side}_min"),
                        pl.when(good[side]).then(pres).max().alias(f"{side}_max"),
                        predicates[side].sum().cast(pl.Int64).alias(f"{side}_n"),
                    )
                ]
            )
            .filter(pl.any_horizontal([pl.col(f"{side}_n") > 0 for side in sides]))
            .collect(engine="streaming")
        )
        if not bounds.height:
            continue

        status = (
            pl.when(predicates[ORIGINAL_ONLY])
            .then(pl.lit(ORIGINAL_ONLY))
            .otherwise(pl.lit(NRTQCLIB_ONLY))
        )
        low = (
            pl.when(pl.col("status") == ORIGINAL_ONLY)
            .then(pl.col(f"{ORIGINAL_ONLY}_min"))
            .otherwise(pl.col(f"{NRTQCLIB_ONLY}_min"))
        )
        high = (
            pl.when(pl.col("status") == ORIGINAL_ONLY)
            .then(pl.col(f"{ORIGINAL_ONLY}_max"))
            .otherwise(pl.col(f"{NRTQCLIB_ONLY}_max"))
        )
        shape = (
            pl.when(pl.col("pres").is_null())
            .then(pl.lit("no_pressure"))
            .when(low.is_null())
            .then(pl.lit("whole"))
            .when(pl.col("pres") < low)
            .then(pl.lit("top"))
            .when(pl.col("pres") > high)
            .then(pl.lit("bottom"))
            .otherwise(pl.lit("inside"))
        )
        per_profile = (
            rows.filter(predicates[ORIGINAL_ONLY] | predicates[NRTQCLIB_ONLY])
            .select(
                pl.col("platform_code").cast(pl.Utf8),
                pl.col("profile_no"),
                pres.alias("pres"),
                status.alias("status"),
                original_flag(variable).alias("original_flag"),
            )
            .join(
                bounds.lazy().with_columns(pl.col("platform_code").cast(pl.Utf8)),
                on=PROFILE_KEYS,
                how="inner",
            )
            .with_columns(shape.alias("shape"))
            .group_by(
                *PROFILE_KEYS,
                "status",
                "shape",
                "original_flag",
                (pl.col("pres") < 0).fill_null(False).alias("negative_pressure"),
            )
            .agg(pl.len().cast(pl.Int64).alias("n_obs"))
            .collect(engine="streaming")
        )
        # An observation inside the good range is alone when it is the only
        # one of its side in the profile that sits there.
        inside = (
            per_profile.filter(pl.col("shape") == "inside")
            .group_by(*PROFILE_KEYS, "status")
            .agg(pl.col("n_obs").sum().alias("n_inside"))
        )
        per_profile = per_profile.join(
            inside, on=[*PROFILE_KEYS, "status"], how="left"
        ).with_columns(
            pl.when(pl.col("shape") != "inside")
            .then(pl.col("shape"))
            .when(pl.col("n_inside") == 1)
            .then(pl.lit("inside_alone"))
            .otherwise(pl.lit("inside_several"))
            .alias("shape")
        )
        frames.append(
            per_profile.group_by(
                "status", "shape", "original_flag", "negative_pressure"
            )
            .agg(
                pl.col("n_obs").sum(),
                pl.struct(PROFILE_KEYS).n_unique().cast(pl.Int64).alias("n_profiles"),
            )
            .select(label + [pl.lit(variable.name).alias("variable"), pl.all()])
        )
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames).sort(
        ["variable", "status", "shape", "original_flag", "negative_pressure"],
        nulls_last=True,
    )


#: nrtqclib's thresholds in the ``test_nrt`` batch, which :func:`response_table`
#: measures each failure against: the global range (min, max), and the spike
#: and gradient thresholds above and below :data:`RESPONSE_DEEP_DBAR`
#: (shallow, deep). They follow the batch's ``nrt_qc_config.yaml``; a
#: variable not listed is left out of the tests that need its thresholds.
RESPONSE_GLOBAL_RANGE: Dict[str, tuple] = {"temp": (-2.5, 40.0), "psal": (2.0, 41.0)}
RESPONSE_SPIKE: Dict[str, tuple] = {"temp": (6.0, 2.0), "psal": (0.9, 0.3)}
RESPONSE_GRADIENT: Dict[str, tuple] = {"temp": (9.0, 3.0), "psal": (1.5, 0.5)}
RESPONSE_DEEP_DBAR: float = 500.0

#: Rows read at a time by :func:`response_table`. The tests compare a level
#: with its neighbours, which the streaming engine cannot do, so the data is
#: read in slices of this many rows instead, a profile cut by the edge of a
#: slice carried over whole to the next.
RESPONSE_CHUNK_ROWS: int = 2_000_000

#: The share of a profile's values the input must flag for a stuck profile
#: to count as flagged throughout; a little under all, so that one level
#: left unflagged does not hide it.
RESPONSE_WHOLE_SHARE: float = 0.95

#: The frozen profile test of the Argo real time QC manual, which nrtqclib
#: does not run: a profile and the platform's previous one are averaged in
#: slabs of this many dbar, and the profile is frozen when the absolute
#: differences of the slab means are all under ``max``, at least one under
#: ``min``, and on average under ``mean``.
FROZEN_SLAB_DBAR: float = 50.0
FROZEN_LIMITS: Dict[str, Dict[str, float]] = {
    "temp": {"mean": 0.02, "max": 0.3, "min": 0.001},
    "psal": {"mean": 0.004, "max": 0.3, "min": 0.001},
}

#: The tests :func:`response_table` measures, in the order the pages show
#: them. Each failure is put in a band by how far it is past the test's
#: threshold, ``uppers`` being the upper edge of each band (included) and
#: None the last. ``clear`` names the bands of clear failures: a producer
#: that runs the test flags nearly all of those, one that does not flags
#: them about as often as anything else. ``unit`` is what the counts count.
RESPONSE_TESTS: List[Dict[str, Any]] = [
    {
        "key": "global_range",
        "label": "Global range",
        "unit": "observations",
        "measure": "How far outside nrtqclib's global range, in the variable's unit",
        "bands": ["Inside", "Up to 0.5 outside", "0.5 to 2", "2 to 10", "More than 10"],
        "uppers": [0.0, 0.5, 2.0, 10.0, None],
        "clear": [3, 4],
    },
    {
        "key": "spike",
        "label": "Spike",
        "unit": "observations",
        "measure": "The spike test value, as a multiple of nrtqclib's threshold",
        "bands": ["Under half", "Half to 1", "1 to 2", "2 to 5", "More than 5"],
        "uppers": [0.5, 1.0, 2.0, 5.0, None],
        "clear": [3, 4],
    },
    {
        "key": "gradient",
        "label": "Gradient",
        "unit": "observations",
        "measure": (
            "The gradient test value, as a multiple of nrtqclib's threshold, "
            "over levels that pass the spike test"
        ),
        "bands": ["Under half", "Half to 1", "1 to 2", "2 to 5", "More than 5"],
        "uppers": [0.5, 1.0, 2.0, 5.0, None],
        "clear": [3, 4],
    },
    {
        "key": "stuck_value",
        "label": "Stuck value",
        "unit": "profiles",
        "measure": "Profiles whose values are all the same, by their number of values",
        "bands": ["Not stuck", "2 to 4 values", "5 to 9 values", "10 values or more"],
        "uppers": [0.0, 4.0, 9.0, None],
        "clear": [2, 3],
    },
    {
        "key": "frozen_profile",
        "label": "Frozen profile",
        "unit": "profiles",
        "measure": (
            "How the profile compares with the platform's previous one, at "
            "another time: by the Argo frozen profile test's limits on "
            "50 dbar slabs, or the same pressures and values at every level. "
            "Only profiles inside the range and not one value throughout"
        ),
        "bands": ["Different", "Within Argo's limits", "Exact repeat"],
        "uppers": [0.0, 1.0, None],
        "clear": [2],
    },
    {
        "key": "density_inversion",
        "label": "Density inversion",
        "unit": "pairs of levels",
        "measure": "How much lighter the deeper level is, in kg/m³",
        "bands": [
            "Stable",
            "Up to 0.01",
            "0.01 to 0.03",
            "0.03 to 0.05",
            "0.05 to 0.2",
            "More than 0.2",
        ],
        "uppers": [0.0, 0.01, 0.03, 0.05, 0.2, None],
        "clear": [4, 5],
    },
    {
        "key": "density_inversion_per_dbar",
        "label": "Density inversion per dbar",
        "unit": "pairs of levels",
        "measure": (
            "How much lighter the deeper level is, in kg/m³ per dbar between them"
        ),
        "bands": [
            "Stable",
            "Up to 0.01",
            "0.01 to 0.03",
            "0.03 to 0.05",
            "0.05 to 0.2",
            "More than 0.2",
        ],
        "uppers": [0.0, 0.01, 0.03, 0.05, 0.2, None],
        "clear": [4, 5],
    },
    {
        "key": "pressure_increasing",
        "label": "Pressure increasing",
        "unit": "observations",
        "measure": "How much shallower than the deepest level before it, in dbar",
        "bands": ["Deeper", "Repeated", "Up to 1 shallower", "1 to 10", "More than 10"],
        "uppers": [-1e-6, 1e-6, 1.0, 10.0, None],
        "clear": [1, 2, 3, 4],
    },
]

#: Platforms whose codes start with one of these are ice-tethered profilers
#: (Woods Hole's ITP and their TOP successors); :func:`platform_group` sets
#: them apart from the rest, which in the Arctic are mostly ships.
ICE_TETHERED_PREFIXES: List[str] = ["ITP-", "TOP-"]

#: The edges of the finer bands of ``density_threshold``, in kg/m³ between
#: levels (or per dbar), each band holding the inversions above the edge
#: before it and up to its own; the last band holds those above the last
#: edge. They are close around 0.05, where the regional products' tests
#: start flagging, so that a step there shows.
DENSITY_EDGES: List[float] = [
    0.0, 0.01, 0.02, 0.03, 0.04, 0.045, 0.0495,
    0.05, 0.0505, 0.055, 0.06, 0.08, 0.1, 0.2,
]  # fmt: skip

#: An inversion between levels of more than this many kg/m³ is a clear one
#: in ``density_platforms``, as in the clear bands of ``items_response``.
DENSITY_CLEAR: float = 0.05


def sigma0_eos80(temp: pl.Expr, psal: pl.Expr) -> pl.Expr:
    """
    Density at the surface minus 1000 kg/m³, by the EOS-80 equation of state
    (UNESCO 1981) at zero pressure.

    nrtqclib uses potential density from TEOS-10; the in situ temperature
    and practical salinity here differ from its inputs by a few hundredths
    of a kg/m³ at most in the upper ocean, which is enough to band an
    inversion but not to repeat nrtqclib's test exactly.
    """
    t = temp.cast(pl.Float64)
    s = psal.cast(pl.Float64)
    water = (
        999.842594
        + 6.793952e-2 * t
        - 9.095290e-3 * t**2
        + 1.001685e-4 * t**3
        - 1.120083e-6 * t**4
        + 6.536332e-9 * t**5
    )
    a = (
        8.24493e-1
        - 4.0899e-3 * t
        + 7.6438e-5 * t**2
        - 8.2467e-7 * t**3
        + 5.3875e-9 * t**4
    )
    b = -5.72466e-3 + 1.0227e-4 * t - 1.6546e-6 * t**2
    return water + a * s + b * s.abs().sqrt() * s + 4.8314e-4 * s**2 - 1000.0


def _response_band(test: Dict[str, Any], margin: pl.Expr) -> pl.Expr:
    """The index of the band of ``test`` that ``margin`` falls in."""
    band = pl.when(margin.is_null()).then(pl.lit(None, pl.Int8))
    for index, upper in enumerate(test["uppers"]):
        if upper is None:
            return band.otherwise(pl.lit(index, pl.Int8))
        band = band.when(margin <= upper).then(pl.lit(index, pl.Int8))
    return band.otherwise(pl.lit(None, pl.Int8))


def _with_profile_ids(frame: pl.DataFrame) -> pl.DataFrame:
    """
    ``frame`` with ``_pid``, a whole number per profile counted from 0 in
    the order the profiles come, which the tests compare and group on
    instead of the platform code, a string.

    nrtqclib writes each profile's rows together and in recorded order; a
    slice that is not is sorted first.
    """
    new = (
        (pl.col("platform_code") != pl.col("platform_code").shift(1))
        | (pl.col("profile_no") != pl.col("profile_no").shift(1))
    ).fill_null(True)
    pid = (new.cast(pl.UInt32).cum_sum() - 1).alias("_pid")
    frame = frame.with_columns(pid)
    starts = frame.filter(new)
    backwards = frame.select(
        ((pl.col("_pid").diff() == 0) & (pl.col("observation_no").diff() <= 0)).sum()
    ).item()
    if starts.select(pl.struct(PROFILE_KEYS).n_unique()).item() != starts.height or (
        backwards
    ):
        frame = frame.sort([*PROFILE_KEYS, "observation_no"], maintain_order=True)
        frame = frame.drop("_pid").with_columns(pid)
    return frame


def _response_chunk(
    frame: pl.DataFrame, variables: List[VariableSpec], has_pres_flag: bool
) -> List[pl.DataFrame]:
    """
    The banded failures of one slice of whole profiles, per test.

    :param frame: Whole profiles, in recorded order, numbered by ``_pid``
                  (:func:`_with_profile_ids`), with the values, a
                  ``bad_<name>`` input flag per variable, ``keep_<name>``
                  saying the profile is compared for that variable, and
                  ``bad_pres`` when the input has a pressure flag.
    :return: One frame per test and variable: ``test``, ``variable``,
             ``band``, how many fall in it (``n``) and how many of those
             the input flags (``n_flagged``).
    """
    tests = {t["key"]: t for t in RESPONSE_TESTS}
    frame = frame.lazy().with_columns(
        (pl.col("_pid") == pl.col("_pid").shift(1)).fill_null(False).alias("_sp"),
        (pl.col("_pid") == pl.col("_pid").shift(-1)).fill_null(False).alias("_sn"),
    )
    pres = pl.col("pres")
    deep = pres > RESPONSE_DEEP_DBAR
    out: List[pl.LazyFrame] = []

    def banded(test: str, variable: str, rows: pl.LazyFrame, margin, flagged):
        out.append(
            rows.select(
                _response_band(tests[test], margin).alias("band"),
                flagged.fill_null(False).alias("flagged"),
            )
            .filter(pl.col("band").is_not_null())
            .group_by("band")
            .agg(
                pl.len().cast(pl.Int64).alias("n"),
                pl.col("flagged").sum().cast(pl.Int64).alias("n_flagged"),
            )
            .with_columns(
                pl.lit(test).alias("test"), pl.lit(variable).alias("variable")
            )
        )

    in_range: Dict[str, pl.Expr] = {}
    for variable in variables:
        name = variable.name
        if name not in RESPONSE_GLOBAL_RANGE:
            continue
        low, high = RESPONSE_GLOBAL_RANGE[name]
        x = pl.col(name).cast(pl.Float64)
        in_range[name] = x.is_between(low, high)
        bad = pl.col(f"bad_{name}")
        mine = frame.filter(pl.col(f"keep_{name}"))
        banded("global_range", name, mine, pl.max_horizontal(low - x, x - high), bad)

        # Spike and gradient on values inside the range with both neighbours
        # inside it too, so that a range test is not taken for either.
        prev = pl.when(pl.col("_sp")).then(x.shift(1))
        nxt = pl.when(pl.col("_sn")).then(x.shift(-1))
        inside = in_range[name] & prev.is_between(low, high) & nxt.is_between(low, high)
        neighbours = mine.with_columns(
            inside.alias("_in"),
            ((x - (prev + nxt) / 2).abs() - ((nxt - prev) / 2).abs()).alias("_spike"),
            (x - (prev + nxt) / 2).abs().alias("_gradient"),
        ).filter(pl.col("_in"))
        shallow, deep_spike = RESPONSE_SPIKE[name]
        spike = pl.col("_spike") / pl.when(deep).then(deep_spike).otherwise(shallow)
        banded("spike", name, neighbours, spike, bad)
        shallow, deep_gradient = RESPONSE_GRADIENT[name]
        gradient = pl.col("_gradient") / pl.when(deep).then(deep_gradient).otherwise(
            shallow
        )
        banded("gradient", name, neighbours.filter(spike < 1), gradient, bad)

        # Stuck value: a profile all of whose values, inside the range, are
        # the same, banded by how many values it has.
        stuck = (
            mine.filter(x.is_not_null())
            .group_by("_pid")
            .agg(
                pl.len().alias("_n"),
                x.n_unique().alias("_unique"),
                in_range[name].all().alias("_in"),
                bad.mean().alias("_bad"),
            )
            .filter((pl.col("_n") >= 2) & pl.col("_in"))
        )
        banded(
            "stuck_value",
            name,
            stuck,
            pl.when(pl.col("_unique") == 1)
            .then(pl.col("_n").cast(pl.Float64))
            .otherwise(0.0),
            pl.col("_bad") >= RESPONSE_WHOLE_SHARE,
        )

    # Both variables' tests run over the profiles compared for every variable.
    names = [v.name for v in variables]
    both = frame.filter(pl.all_horizontal([pl.col(f"keep_{n}") for n in names]))
    if "temp" in in_range and "psal" in in_range:
        pairs = _density_pairs(both, names)
        banded(
            "density_inversion",
            "density",
            pairs,
            pl.col("_inversion"),
            pl.col("_pair_bad"),
        )
        banded(
            "density_inversion_per_dbar",
            "density",
            pairs,
            pl.col("_inversion") / pl.col("_dp"),
            pl.col("_pair_bad"),
        )

    # Pressure: how far shallower than the deepest level before it, set
    # against the pressure flag and each variable's flag in turn.
    deepest = pl.when(pl.col("_sp")).then(pres.cum_max().over("_pid").shift(1))
    margin = deepest - pres
    if has_pres_flag:
        banded("pressure_increasing", "pres", both, margin, pl.col("bad_pres"))
    for name in names:
        banded(
            "pressure_increasing",
            name,
            frame.filter(pl.col(f"keep_{name}")),
            margin,
            pl.col(f"bad_{name}"),
        )
    return pl.collect_all(out)


def platform_group(code: pl.Expr) -> pl.Expr:
    """
    ``"ice_tethered"`` for a platform code starting with one of
    :data:`ICE_TETHERED_PREFIXES`, ``"other"`` for any other.
    """
    tethered = pl.any_horizontal(
        [code.str.starts_with(p) for p in ICE_TETHERED_PREFIXES]
    )
    return pl.when(tethered).then(pl.lit("ice_tethered")).otherwise(pl.lit("other"))


def _density_pairs(rows: pl.LazyFrame, names: List[str]) -> pl.LazyFrame:
    """
    ``rows`` with each level set against the one recorded before it.

    ``_inversion`` is how much lighter the level is than the one above it
    (positive for an inversion), in kg/m³ by :func:`sigma0_eos80`, where
    both are of the same profile, have both variables inside the global
    range, and the level is deeper; otherwise it is missing. ``_dp`` is
    the pressure between them, ``_bad`` and ``_bad_upper`` whether the
    input flags either variable at the level and at the one above, and
    ``_pair_bad`` whether it flags either.

    :param rows: Whole profiles in recorded order, with ``_sp`` (the level
                 before is of the same profile), the values and the
                 ``bad_<name>`` flags.
    :param names: The variables, which must include ``temp`` and ``psal``.
    """
    pres = pl.col("pres")
    ok = pl.all_horizontal(
        [
            pl.col(n).cast(pl.Float64).is_between(*RESPONSE_GLOBAL_RANGE[n])
            for n in ["temp", "psal"]
        ]
    )
    bad = pl.any_horizontal([pl.col(f"bad_{n}") for n in names])
    return rows.with_columns(
        sigma0_eos80(pl.col("temp"), pl.col("psal")).alias("_sigma"),
        ok.alias("_ok"),
        bad.alias("_bad"),
    ).with_columns(
        pl.when(
            pl.col("_sp")
            & pl.col("_ok")
            & pl.col("_ok").shift(1)
            & (pres > pres.shift(1))
        )
        .then(pl.col("_sigma").shift(1) - pl.col("_sigma"))
        .alias("_inversion"),
        (pres - pres.shift(1)).alias("_dp"),
        pl.col("_bad").shift(1).alias("_bad_upper"),
        (pl.col("_bad") | pl.col("_bad").shift(1)).alias("_pair_bad"),
    )


def _density_chunk(
    frame: pl.DataFrame, variables: List[VariableSpec]
) -> Dict[str, pl.DataFrame]:
    """
    The density inversions of one slice of whole profiles, counted for
    ``density_threshold`` and ``density_platforms``.

    :param frame: As :func:`_response_chunk` takes it.
    :return: Partial counts, summed over the slices by
             :func:`_density_tables`: ``threshold`` per platform group,
             measure and band; ``platforms`` per platform; ``spacing`` per
             platform and level spacing (rounded to 0.01 dbar), for the
             median. Empty without temperature and salinity.
    """
    names = [v.name for v in variables]
    if "temp" not in names or "psal" not in names:
        return {}
    rows = (
        frame.lazy()
        .filter(pl.all_horizontal([pl.col(f"keep_{n}") for n in names]))
        .with_columns(
            (pl.col("_pid") == pl.col("_pid").shift(1)).fill_null(False).alias("_sp")
        )
    )
    pairs = _density_pairs(rows, names).filter(pl.col("_inversion").is_not_null())
    flags = [
        pl.col("_pair_bad").sum().cast(pl.Int64).alias("n_flagged"),
        pl.col("_bad").sum().cast(pl.Int64).alias("n_flagged_deeper"),
        pl.col("_bad_upper").sum().cast(pl.Int64).alias("n_flagged_upper"),
    ]
    threshold = []
    for measure, value in [
        ("level", pl.col("_inversion")),
        ("dbar", pl.col("_inversion") / pl.col("_dp")),
    ]:
        band = pl.sum_horizontal([(value > edge) for edge in DENSITY_EDGES])
        threshold.append(
            pairs.with_columns(
                platform_group(pl.col("platform_code")).alias("platform_group"),
                band.cast(pl.Int64).alias("band"),
            )
            .filter(pl.col("band") > 0)
            .group_by("platform_group", "band")
            .agg(pl.len().cast(pl.Int64).alias("n"), *flags)
            .with_columns(pl.lit(measure).alias("measure"))
        )
    inversion = pl.col("_inversion")
    small = (inversion > 0) & (inversion <= DENSITY_CLEAR)
    clear = inversion > DENSITY_CLEAR
    platforms = pairs.group_by("platform_code").agg(
        pl.len().cast(pl.Int64).alias("n_pairs"),
        (inversion > 0).sum().cast(pl.Int64).alias("n_inversions"),
        small.sum().cast(pl.Int64).alias("n_small"),
        (small & pl.col("_pair_bad")).sum().cast(pl.Int64).alias("n_small_flagged"),
        clear.sum().cast(pl.Int64).alias("n_clear"),
        (clear & pl.col("_pair_bad")).sum().cast(pl.Int64).alias("n_clear_flagged"),
        (clear & pl.col("_bad")).sum().cast(pl.Int64).alias("n_clear_flagged_deeper"),
    )
    spacing = pairs.group_by("platform_code", pl.col("_dp").round(2)).agg(
        pl.len().cast(pl.Int64).alias("n")
    )
    threshold_counts, platform_counts, spacing_counts = pl.collect_all(
        [pl.concat(threshold), platforms, spacing]
    )
    return {
        "threshold": threshold_counts,
        "platforms": platform_counts,
        "spacing": spacing_counts,
    }


def _density_tables(
    parts: List[Dict[str, pl.DataFrame]], label: List[pl.Expr]
) -> Dict[str, pl.DataFrame]:
    """
    ``density_threshold`` and ``density_platforms`` from the counts of
    :func:`_density_chunk`, labelled with the dataset columns ``label``.
    """
    parts = [p for p in parts if p]
    if not parts:
        return {}
    uppers = DENSITY_EDGES + [None]
    threshold = (
        pl.concat([p["threshold"] for p in parts])
        .group_by("platform_group", "measure", "band")
        .agg(pl.col("n", "n_flagged", "n_flagged_deeper", "n_flagged_upper").sum())
        .with_columns(
            pl.col("band")
            .replace_strict(
                {b: DENSITY_EDGES[b - 1] for b in range(1, len(uppers))},
                return_dtype=pl.Float64,
            )
            .alias("lower"),
            pl.col("band")
            .replace_strict(
                {b: uppers[b] for b in range(1, len(uppers))}, return_dtype=pl.Float64
            )
            .alias("upper"),
        )
        .sort("platform_group", "measure", "band", descending=[False, True, False])
        .select(
            label
            + [
                "platform_group",
                "measure",
                "band",
                "lower",
                "upper",
                "n",
                "n_flagged",
                "n_flagged_deeper",
                "n_flagged_upper",
            ]
        )
    )
    spacing = (
        pl.concat([p["spacing"] for p in parts])
        .group_by("platform_code", "_dp")
        .agg(pl.col("n").sum())
        .sort("platform_code", "_dp")
        .with_columns(
            pl.col("n").cum_sum().over("platform_code").alias("_cum"),
            pl.col("n").sum().over("platform_code").alias("_total"),
        )
        .group_by("platform_code")
        .agg(
            pl.col("_dp")
            .filter(pl.col("_cum") * 2 >= pl.col("_total"))
            .first()
            .alias("spacing_dbar")
        )
    )
    counts = [
        "n_pairs",
        "n_inversions",
        "n_small",
        "n_small_flagged",
        "n_clear",
        "n_clear_flagged",
        "n_clear_flagged_deeper",
    ]
    platforms = (
        pl.concat([p["platforms"] for p in parts])
        .group_by("platform_code")
        .agg(pl.col(counts).sum())
        .filter(pl.col("n_inversions") > 0)
        .join(spacing, on="platform_code", how="left")
        .with_columns(platform_group(pl.col("platform_code")).alias("platform_group"))
        .sort("platform_code")
        .select(label + ["platform_code", "platform_group", "spacing_dbar"] + counts)
    )
    return {"density_threshold": threshold, "density_platforms": platforms}


def _frozen_chunk(
    frame: pl.DataFrame, variables: List[VariableSpec], context: int
) -> List[pl.DataFrame]:
    """
    The frozen profile test over one slice of whole profiles.

    Each profile is set against the profile before it, when that one is of
    the same platform and at another time (one at the same time is a
    duplicate, not a frozen profile), whatever its data mode. It is an
    exact repeat when it has the same pressures and values at every level,
    in order; otherwise it is within Argo's limits when the slab means pass
    :data:`FROZEN_LIMITS`. A profile counts as flagged when the input flags
    :data:`RESPONSE_WHOLE_SHARE` of its values or more. As for stuck value,
    only profiles all of whose values are inside the global range are
    measured, and not those that are one value throughout, so that a range
    or stuck value test is not taken for this one.

    :param frame: Whole profiles, numbered by ``_pid``, with
                  ``profile_time``, the values, ``bad_<name>`` and
                  ``keep_<name>``, every profile of the slice whether
                  compared or not.
    :param variables: The variables to test.
    :param context: How many profiles at the start of ``frame`` are only
                    there to be compared with: the end of the slice before.
    :return: Counts as :func:`_response_chunk` gives them.
    """
    test = next(t for t in RESPONSE_TESTS if t["key"] == "frozen_profile")
    out: List[pl.DataFrame] = []
    pres = pl.col("pres")
    for variable in variables:
        name = variable.name
        if name not in FROZEN_LIMITS:
            continue
        limits = FROZEN_LIMITS[name]
        low, high = RESPONSE_GLOBAL_RANGE[name]
        x = pl.col(name)
        rows = frame.filter(x.is_not_null() & pres.is_not_null())
        profiles = (
            rows.group_by("_pid", maintain_order=True)
            .agg(
                pl.col("platform_code").first(),
                pl.col("profile_time").first(),
                pl.col(f"keep_{name}").first(),
                pl.len().alias("_n"),
                pres.alias("_p"),
                x.alias("_x"),
                x.cast(pl.Float64).is_between(low, high).all().alias("_in"),
                x.n_unique().alias("_unique"),
                pl.col(f"bad_{name}").mean().alias("_bad"),
            )
            .sort("_pid")
            .with_columns(pl.col("_pid").shift(1).alias("_prev"))
            .with_columns(
                (
                    (pl.col("platform_code") == pl.col("platform_code").shift(1))
                    & (pl.col("profile_time") != pl.col("profile_time").shift(1))
                )
                .fill_null(False)
                .alias("_paired"),
                (
                    (pl.col("_n") == pl.col("_n").shift(1))
                    & (pl.col("_p") == pl.col("_p").shift(1))
                    & (pl.col("_x") == pl.col("_x").shift(1))
                )
                .fill_null(False)
                .alias("_exact"),
            )
        )
        slabs = rows.group_by(
            "_pid", (pres / FROZEN_SLAB_DBAR).floor().alias("_slab")
        ).agg(x.mean().alias("_m"))
        differences = (
            profiles.filter(pl.col("_paired"))
            .select("_pid", "_prev")
            .join(slabs, on="_pid")
            .join(
                slabs.rename({"_pid": "_prev", "_m": "_m_prev"}),
                on=["_prev", "_slab"],
            )
            .group_by("_pid")
            .agg(
                (pl.col("_m") - pl.col("_m_prev")).abs().mean().alias("_mean"),
                (pl.col("_m") - pl.col("_m_prev")).abs().max().alias("_max"),
                (pl.col("_m") - pl.col("_m_prev")).abs().min().alias("_min"),
            )
        )
        frozen = (
            (pl.col("_mean") < limits["mean"])
            & (pl.col("_max") < limits["max"])
            & (pl.col("_min") < limits["min"])
        ).fill_null(False)
        measured = (
            profiles.filter(
                pl.col("_paired")
                & pl.col(f"keep_{name}")
                & (pl.col("_n") >= 2)
                & pl.col("_in")
                & (pl.col("_unique") > 1)
                & (pl.col("_pid") >= context)
            )
            .join(differences, on="_pid", how="left")
            .select(
                _response_band(
                    test,
                    pl.when(pl.col("_exact"))
                    .then(2.0)
                    .when(frozen)
                    .then(1.0)
                    .otherwise(0.0),
                ).alias("band"),
                (pl.col("_bad") >= RESPONSE_WHOLE_SHARE).alias("flagged"),
            )
        )
        out.append(
            measured.group_by("band")
            .agg(
                pl.len().cast(pl.Int64).alias("n"),
                pl.col("flagged").sum().cast(pl.Int64).alias("n_flagged"),
            )
            .with_columns(
                pl.lit("frozen_profile").alias("test"), pl.lit(name).alias("variable")
            )
        )
    return out


def response_table(
    scan: pl.LazyFrame,
    keep: Dict[str, pl.DataFrame],
    dataset: DatasetSpec,
    config: BuildConfig,
) -> pl.DataFrame:
    """The ``items_response`` table of :func:`response_tables`, or an empty one."""
    return response_tables(scan, keep, dataset, config).get(
        "items_response", pl.DataFrame()
    )


def response_tables(
    scan: pl.LazyFrame,
    keep: Dict[str, pl.DataFrame],
    dataset: DatasetSpec,
    config: BuildConfig,
) -> Dict[str, pl.DataFrame]:
    """
    How often the input flags the failures of each of nrtqclib's tests, by
    how far past the threshold they are.

    The regional centres say which tests they run; this is how to check it
    against their flags. A producer that runs a test flags nearly all of its
    clear failures, and one with another threshold starts flagging further
    out; one that does not run it flags them about as often as any other
    observation. Each failure is measured again from the values, in the
    order the levels were recorded as nrtqclib does, and banded as
    :data:`RESPONSE_TESTS` describes. Profiles are those of
    :func:`item_tables_for`; a test of both variables or of pressure takes
    the profiles compared for every variable.

    The same pass counts the density inversions finer, around the 0.05
    kg/m³ the regional products' tests start at, and per platform, for
    ``density_threshold`` and ``density_platforms`` (:func:`_density_tables`):
    a product that runs the test on some platforms only shows it there.

    :param scan: The dataset.
    :param keep: Per variable name, the keys of the profiles compared for it.
    :param dataset: The dataset's configuration.
    :param config: The build configuration.
    :return: The tables by name, those with nothing to count left out.
             ``items_response`` has one row per test, variable (``density``
             for the density tests, ``pres`` for the input's pressure flag)
             and band, with how many there are (``n``) and how many the
             input flags (``n_flagged``).
    """
    variables = list(config.variables)
    if not any(keep[v.name].height for v in variables):
        return {}
    schema = scan.collect_schema().names()
    has_pres_flag = "pres_qc" in schema
    columns = [
        pl.col("platform_code").cast(pl.Utf8),
        pl.col("profile_no"),
        pl.col("observation_no"),
        # The frozen profile test leaves out a previous profile at the same
        # time; without a time, it pairs nothing.
        (
            pl.col("profile_time").cast(pl.Float64, strict=False)
            if "profile_time" in schema
            else pl.lit(None, pl.Float64)
        ).alias("profile_time"),
        # A NaN would fail every comparison and land in the last band, so it
        # is read as missing, as the tests treat it.
        pl.col("pres").cast(pl.Float64, strict=False).fill_nan(None),
        *[
            pl.col(v.name).cast(pl.Float64, strict=False).fill_nan(None)
            for v in variables
        ],
        *[
            is_one_of(original_flag(v), v.bad_flag_values).alias(f"bad_{v.name}")
            for v in variables
        ],
    ]
    if has_pres_flag:
        columns.append(
            is_one_of(
                pl.col("pres_qc").cast(pl.Int64, strict=False), DEFAULT_BAD_FLAG_VALUES
            ).alias("bad_pres")
        )
    marks = {
        v.name: keep[v.name]
        .select(pl.col("platform_code").cast(pl.Utf8), pl.col("profile_no"))
        .with_columns(pl.lit(True).alias(f"keep_{v.name}"))
        for v in variables
    }

    total = scan.select(pl.len()).collect().item()
    counts: List[pl.DataFrame] = []
    density: List[Dict[str, pl.DataFrame]] = []
    carry = None
    # The last whole profile of the slice before, which the frozen profile
    # test compares the first profile of the next slice with.
    previous = None
    for start in range(0, total, RESPONSE_CHUNK_ROWS):
        part = scan.slice(start, RESPONSE_CHUNK_ROWS).select(columns).collect()
        if carry is not None:
            part = pl.concat([carry, part])
        if start + RESPONSE_CHUNK_ROWS < total and part.height:
            last = part.select(PROFILE_KEYS).tail(1)
            cut = (pl.col("platform_code") == last["platform_code"][0]) & (
                pl.col("profile_no") == last["profile_no"][0]
            )
            carry = part.filter(cut)
            part = part.filter(~cut)
        else:
            carry = None
        if not part.height:
            continue
        context = 0
        if previous is not None:
            part = pl.concat([previous, part])
            context = 1
        # Whether each profile is compared for each variable, looked up once
        # per profile and spread to its rows by the profile number.
        part = _with_profile_ids(part)
        last_pid = part["_pid"].max()
        previous = part.filter(pl.col("_pid") == last_pid).drop("_pid")
        profiles = part.filter(
            (pl.col("_pid") != pl.col("_pid").shift(1)).fill_null(True)
        ).select("_pid", *PROFILE_KEYS)
        for name, mark in marks.items():
            profiles = profiles.join(
                mark, on=PROFILE_KEYS, how="left", maintain_order="left"
            ).with_columns(pl.col(f"keep_{name}").fill_null(False))
        part = part.with_columns(
            [profiles[f"keep_{v.name}"].gather(part["_pid"]) for v in variables]
        )
        counts.extend(_frozen_chunk(part, variables, context))
        part = part.filter(pl.col("_pid") >= context).filter(
            pl.any_horizontal([pl.col(f"keep_{v.name}") for v in variables])
        )
        if not part.height:
            continue
        counts.extend(_response_chunk(part, variables, has_pres_flag))
        density.append(_density_chunk(part, variables))
    if not counts:
        return {}
    label = [
        pl.lit(dataset.id).alias("dataset_id"),
        pl.lit(dataset.region).alias("region"),
        pl.lit(dataset.product).alias("product"),
        pl.lit(dataset.label).alias("label"),
    ]
    order = {t["key"]: i for i, t in enumerate(RESPONSE_TESTS)}
    response = (
        pl.concat(counts)
        .group_by("test", "variable", "band")
        .agg(pl.col("n").sum(), pl.col("n_flagged").sum())
        .with_columns(pl.col("test").replace_strict(order).alias("_order"))
        .sort("_order", "variable", "band")
        .select(label + ["test", "variable", "band", "n", "n_flagged"])
    )
    return {"items_response": response, **_density_tables(density, label)}


#: Two profiles of different products are the same cast when they share a
#: date and their positions are within this many degrees of each other, in
#: longitude and in latitude. ctddump rounds to 3 decimals instead, which
#: splits a pair whose two positions fall either side of a rounding
#: boundary: the products rarely store a position to the same last digit.
MATCH_TOLERANCE_DEG: float = 0.001

#: A looser tolerance, in degrees, for a profile left unmatched: the same
#: platform on the same date within about 1 km is most likely the same cast
#: with its position stored differently, but it is not counted as a pair.
NEARBY_TOLERANCE_DEG: float = 0.01

#: How :func:`cover_profiles` files each profile of an NRT product against
#: the delayed mode product of its region, in order.
COVERAGE_CLASSES: List[str] = [
    "same_platform",
    "other_platform",
    "nearby",
    "platform_only",
    "no_platform",
    "outside_period",
]

#: How many rounds of pairing :func:`match_profiles` runs at most. A round
#: offers the profiles left unpaired their next best candidate, and pairing
#: stops early once a round finds nothing new.
MATCH_ROUNDS: int = 5

#: The columns each profile is matched and compared on.
_MATCH_COLUMNS: List[str] = [
    "platform_code",
    "profile_no",
    "n_obs",
    "profile_timestamp",
]


def _match_frame(profiles: pl.DataFrame, dataset_id: str) -> pl.DataFrame:
    """
    One dataset's profiles, ready to match: the date and the position as a
    float. A profile with no time or position cannot be matched and is left
    out.
    """
    present = [c for c in _MATCH_COLUMNS if c in profiles.columns]
    return (
        profiles.filter(pl.col("dataset_id") == dataset_id)
        .select(
            *present,
            pl.col("longitude").cast(pl.Float64, strict=False).alias("_lon"),
            pl.col("latitude").cast(pl.Float64, strict=False).alias("_lat"),
            pl.col("profile_timestamp").dt.date().alias("_date"),
        )
        .drop_nulls(["_date", "_lon", "_lat"])
    )


def _with_cells(frame: pl.DataFrame, tolerance: float) -> pl.DataFrame:
    """The cell of ``tolerance`` degrees each position falls in."""
    cells = 1.0 / tolerance
    return frame.with_columns(
        (pl.col("_lon") * cells).floor().cast(pl.Int64).alias("_cx"),
        (pl.col("_lat") * cells).floor().cast(pl.Int64).alias("_cy"),
    )


def match_profiles(
    mine: pl.DataFrame,
    theirs: pl.DataFrame,
    same_platform: bool,
    tolerance: float = MATCH_TOLERANCE_DEG,
) -> pl.DataFrame:
    """
    Pair each profile of one product with the same cast in another.

    Two profiles are a pair when they share a date (and the platform code,
    if asked) and lie within ``tolerance`` of each other in both
    coordinates. Within that tolerance a position can only fall in its
    own cell or a neighbouring one, so the candidates come from joining on
    the nine cells around each profile rather than on every profile of the
    day. Each profile is paired at most once, the closest in time first,
    then in position, and without the platform code a partner on the same
    platform before any other.

    :param mine: The first product, from :func:`_match_frame`.
    :param theirs: The second product, the same way.
    :param same_platform: Whether the platform code must be equal too.
    :param tolerance: The largest difference in either coordinate, degrees.
    :return: One row per pair, the second product's columns suffixed ``_c``
             (``platform_code_c`` included), with ``_dt`` the time between
             them in seconds.
    """
    by = ["_date", "_cx", "_cy"] + (["platform_code"] if same_platform else [])
    mine, theirs = _with_cells(mine, tolerance), _with_cells(theirs, tolerance)
    if same_platform:
        theirs = theirs.with_columns(pl.col("platform_code").alias("platform_code_c"))
    around = pl.concat(
        [
            theirs.with_columns(pl.col("_cx") + dx, pl.col("_cy") + dy)
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
        ]
    )
    reach = tolerance * (1 + 1e-9)
    candidates = (
        mine.join(around, on=by, suffix="_c")
        .filter(
            ((pl.col("_lon") - pl.col("_lon_c")).abs() <= reach)
            & ((pl.col("_lat") - pl.col("_lat_c")).abs() <= reach)
        )
        .with_columns(
            (pl.col("profile_timestamp") - pl.col("profile_timestamp_c"))
            .dt.total_seconds()
            .abs()
            .alias("_dt"),
            (
                (pl.col("_lon") - pl.col("_lon_c")).abs()
                + (pl.col("_lat") - pl.col("_lat_c")).abs()
            ).alias("_dist"),
        )
    )
    # Without the platform in the key, a cast still prefers its own platform:
    # otherwise a closer profile of another platform could take its partner.
    order = ["_dt", "_dist"]
    if not same_platform:
        candidates = candidates.with_columns(
            (pl.col("platform_code") != pl.col("platform_code_c")).alias("_other")
        )
        order = ["_other"] + order
    candidates = candidates.sort(order)

    # Pair greedily, best candidates first. One round keeps each profile's
    # best partner and drops pairs whose partner is taken twice; the next
    # round offers what is left its next best, until nothing changes.
    mine_key = ["platform_code", "profile_no"]
    theirs_key = ["platform_code_c", "profile_no_c"]
    matched = []
    for _ in range(MATCH_ROUNDS):
        if candidates.height == 0:
            break
        chosen = candidates.unique(mine_key, keep="first", maintain_order=True).unique(
            theirs_key, keep="first", maintain_order=True
        )
        if chosen.height == 0:
            break
        matched.append(chosen)
        candidates = candidates.join(
            chosen.select(mine_key), on=mine_key, how="anti"
        ).join(chosen.select(theirs_key), on=theirs_key, how="anti")
    if not matched:
        return candidates.clear()
    return pl.concat(matched)


def _region_pairs(config: BuildConfig, present: set) -> List[tuple]:
    """Every ordered pair of different datasets of one region, in config order."""
    return [
        (reference, other)
        for reference in config.datasets
        if reference.id in present
        for other in config.datasets
        if other.id != reference.id
        and other.region == reference.region
        and other.id in present
    ]


def overlap_table(profiles: pl.DataFrame, config: BuildConfig) -> pl.DataFrame:
    """
    How many of one product's profiles another product of its region also has.

    Two matches are counted, both by :func:`match_profiles`: with the
    platform code, as ctddump's own comparison does; and on date and position
    alone, which also finds a cast that reached the two products under
    different platform codes. A product built from the same casts as another
    but naming its platforms differently shows up only in the second.

    :param profiles: One row per profile of every dataset.
    :param config: The build configuration, for the regions and their order.
    :return: One row per reference and compared dataset of one region.
    """
    needed = {"profile_timestamp", "longitude", "latitude"}
    if not needed <= set(profiles.columns):
        return pl.DataFrame()
    present = set(profiles["dataset_id"].unique().to_list())
    totals = dict(profiles.group_by("dataset_id").len().iter_rows())
    frames = {d: _match_frame(profiles, d) for d in present}
    rows = []
    for reference, other in _region_pairs(config, present):
        mine, theirs = frames[reference.id], frames[other.id]
        rows.append(
            {
                "dataset_id": reference.id,
                "region": reference.region,
                "product": reference.product,
                "label": reference.label,
                "compared_id": other.id,
                "compared_label": other.label,
                "n_profiles": int(totals[reference.id]),
                "n_keyed": int(mine.height),
                "n_matched_same_platform": int(
                    match_profiles(mine, theirs, same_platform=True).height
                ),
                "n_matched_any_platform": int(
                    match_profiles(mine, theirs, same_platform=False).height
                ),
            }
        )
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def _unmatched(frame: pl.DataFrame, pairs: pl.DataFrame, side: str) -> pl.DataFrame:
    """The profiles of ``frame`` that ``pairs`` has not used on ``side``."""
    suffix = "" if side == "mine" else "_c"
    taken = pairs.select(
        pl.col(f"platform_code{'_c' if suffix else ''}").alias("platform_code"),
        pl.col(f"profile_no{suffix}").alias("profile_no"),
    )
    return frame.join(taken, on=["platform_code", "profile_no"], how="anti")


def cover_profiles(mine: pl.DataFrame, theirs: pl.DataFrame) -> Dict[str, pl.DataFrame]:
    """
    Whether each profile of an NRT product is in the delayed mode product.

    The delayed mode product should hold every cast of the NRT product it
    covers, so each NRT profile is looked for in it and filed under the
    first of :data:`COVERAGE_CLASSES` that fits:

    - ``outside_period``: dated before the delayed mode product's first
      profile or after its last, so it cannot be there yet;
    - ``same_platform``: the same platform, date and position
      (:func:`match_profiles`);
    - ``other_platform``: the same date and position under another platform
      code, among the profiles left;
    - ``nearby``: the same platform and date, positions within
      :data:`NEARBY_TOLERANCE_DEG`: probably the same cast, not paired;
    - ``platform_only``: its platform is in the delayed mode product, the
      cast is not;
    - ``no_platform``: its platform is not in the delayed mode product.

    :param mine: The NRT product, from :func:`_match_frame`.
    :param theirs: The delayed mode product, the same way.
    :return: ``profiles``: ``platform_code``, ``profile_no``, ``_date`` and
             ``coverage`` for every profile of ``mine``; and ``pairs``: the
             profiles paired as the same cast (``same_platform`` and
             ``other_platform``), as :func:`match_profiles` gives them, with
             ``coverage``.
    """
    first, last = theirs["_date"].min(), theirs["_date"].max()
    inside = mine.filter(pl.col("_date").is_between(first, last))
    same = match_profiles(inside, theirs, same_platform=True)
    left, left_c = _unmatched(inside, same, "mine"), _unmatched(theirs, same, "theirs")
    other = match_profiles(left, left_c, same_platform=False)
    left, left_c = _unmatched(left, other, "mine"), _unmatched(left_c, other, "theirs")
    nearby = match_profiles(
        left, left_c, same_platform=True, tolerance=NEARBY_TOLERANCE_DEG
    )
    pairs = pl.concat(
        [
            same.with_columns(pl.lit("same_platform").alias("coverage")),
            other.with_columns(
                pl.when(pl.col("platform_code") == pl.col("platform_code_c"))
                .then(pl.lit("same_platform"))
                .otherwise(pl.lit("other_platform"))
                .alias("coverage")
            ),
        ],
        how="diagonal_relaxed",
    )
    key = ["platform_code", "profile_no"]
    filed = pl.concat(
        [
            pairs.select(*key, "coverage"),
            nearby.select(*key, pl.lit("nearby").alias("coverage")),
        ]
    )
    platforms = theirs.select(pl.col("platform_code").unique()).with_columns(
        pl.lit(True).alias("_known")
    )
    profiles = (
        mine.select(*key, "_date")
        .join(filed, on=key, how="left")
        .join(platforms, on="platform_code", how="left")
        .with_columns(
            pl.when(~pl.col("_date").is_between(first, last))
            .then(pl.lit("outside_period"))
            .when(pl.col("coverage").is_not_null())
            .then(pl.col("coverage"))
            .when(pl.col("_known").fill_null(False))
            .then(pl.lit("platform_only"))
            .otherwise(pl.lit("no_platform"))
            .alias("coverage")
        )
        .drop("_known")
    )
    return {"profiles": profiles, "pairs": pairs}


def delayed_comparisons(
    profiles: pl.DataFrame, config: BuildConfig
) -> List[Dict[str, Any]]:
    """
    Each NRT product set against the delayed mode product of its region.

    :param profiles: One row per profile of every dataset, with ``n_obs``
                     and positions.
    :param config: The build configuration.
    :return: One entry per pair of products: ``reference`` and ``other``
             (their :class:`DatasetSpec`), ``theirs`` (the delayed mode
             product's profiles, from :func:`_match_frame`) and ``covered``
             (:func:`cover_profiles`). Empty without the columns to match on.
    """
    needed = {"profile_timestamp", "longitude", "latitude", "n_obs"}
    if not needed <= set(profiles.columns):
        return []
    present = set(profiles["dataset_id"].unique().to_list())
    comparisons = []
    for reference, other in _region_pairs(config, present):
        if reference.data_mode is not None or other.data_mode != DELAYED:
            continue
        theirs = _match_frame(profiles, other.id)
        if theirs.height == 0:
            continue
        covered = cover_profiles(_match_frame(profiles, reference.id), theirs)
        comparisons.append(
            {
                "reference": reference,
                "other": other,
                "theirs": theirs,
                "covered": covered,
            }
        )
    return comparisons


def match_tables(
    profiles: pl.DataFrame,
    config: BuildConfig,
    comparisons: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, pl.DataFrame]:
    """
    How much of an NRT product the reprocessed product of its region holds,
    and whether the casts they share hold the same observations.

    An NRT product carries real time QC and the reprocessed product (CORA)
    delayed mode QC of the same casts, so each NRT product is compared with
    every product of its region configured as delayed mode throughout:
    first which of its profiles the other has (:func:`cover_profiles`),
    then, for the pairs, whether the two versions hold the same number of
    observations.

    :param profiles: One row per profile of every dataset, with ``n_obs``
                     and positions.
    :param config: The build configuration.
    :param comparisons: :func:`delayed_comparisons`, when already made.
    :return: ``conversion_coverage`` (one row per pair of products, with the
             profiles in each coverage class), ``conversion_coverage_years``
             (the same by year) and ``conversion_match`` (the pairs'
             numbers of observations, by coverage class).
    """
    if comparisons is None:
        comparisons = delayed_comparisons(profiles, config)
    coverage, years, summary = [], [], []
    for comparison in comparisons:
        reference, other = comparison["reference"], comparison["other"]
        theirs, covered = comparison["theirs"], comparison["covered"]
        labels = _comparison_labels(reference, other)
        filed = covered["profiles"]
        coverage.append(
            filed.select(
                *labels,
                pl.lit(theirs["_date"].min()).alias("period_start"),
                pl.lit(theirs["_date"].max()).alias("period_end"),
                pl.len().cast(pl.Int64).alias("n_profiles"),
                *[
                    (pl.col("coverage") == name).sum().cast(pl.Int64).alias(f"n_{name}")
                    for name in COVERAGE_CLASSES
                ],
            )
        )
        years.append(
            filed.group_by(
                pl.col("_date").dt.year().cast(pl.Int64).alias("year"), "coverage"
            )
            .agg(pl.len().cast(pl.Int64).alias("n_profiles"))
            .sort("year", "coverage")
            .select(*labels, pl.all())
        )
        difference = pl.col("n_obs").cast(pl.Int64) - pl.col("n_obs_c").cast(pl.Int64)
        summary.append(
            covered["pairs"]
            .group_by("coverage")
            .agg(
                pl.len().cast(pl.Int64).alias("n_matched"),
                (difference == 0).sum().cast(pl.Int64).alias("n_same_obs"),
                (difference > 0).sum().cast(pl.Int64).alias("n_more_in_dataset"),
                (difference < 0).sum().cast(pl.Int64).alias("n_more_in_compared"),
            )
            .sort("coverage", descending=True)
            .select(*labels, pl.all())
        )

    def stack(frames: List[pl.DataFrame]) -> pl.DataFrame:
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    tables = {
        "conversion_coverage": stack(coverage),
        "conversion_coverage_years": stack(years),
        "conversion_match": stack(summary),
    }
    return {name: table for name, table in tables.items() if table.height}


#: Two copies of a level are the same level when their pressures differ by no
#: more than this, in dbar. A pair of profiles is compared level by level only
#: when every level agrees: the same number of observations alone does not
#: make the same levels.
CORA_PRES_TOLERANCE_DBAR: float = 0.1

#: How many levels of paired profiles :func:`cora_tables` reads at a time,
#: on each side. Holding every paired level of the Arctic products at once
#: took 2.5 GB on top of the rest; in chunks of this size it takes 1.3 GB,
#: and no longer, since each chunk reads only the row groups of its
#: platforms.
CORA_CHUNK_LEVELS: int = 2_000_000

#: The coverage classes whose pairs :func:`cora_tables` compares: the pairs
#: of :func:`cover_profiles`.
CORA_COVERAGE: List[str] = ["same_platform", "other_platform"]


def _comparison_labels(reference: DatasetSpec, other: DatasetSpec) -> List[pl.Expr]:
    """The columns naming an NRT product and the product it is compared with."""
    return [
        pl.lit(reference.id).alias("dataset_id"),
        pl.lit(reference.region).alias("region"),
        pl.lit(reference.product).alias("product"),
        pl.lit(reference.label).alias("label"),
        pl.lit(other.id).alias("compared_id"),
        pl.lit(other.label).alias("compared_label"),
    ]


def _paired_levels(
    scan: pl.LazyFrame, keys: pl.DataFrame, variables: List[VariableSpec]
) -> pl.DataFrame:
    """
    The observations of the paired profiles of one product, compactly.

    :param scan: The product.
    :param keys: ``platform_code``, ``profile_no`` and ``_pair``, the pair
                 each profile belongs to. Only the platforms from the first
                 to the last of them, in code order, are read.
    :param variables: The variables whose flags are kept.
    :return: ``_pair``, ``observation_no``, ``pres`` and, per variable, the
             input's flag (``{name}_qc``) and nrtqclib's (``{name}_nrt``).
    """
    flags = []
    for v in variables:
        flags += [
            original_flag(v).cast(pl.Int8, strict=False).alias(f"{v.name}_qc"),
            computed_flag(v).cast(pl.Int8, strict=False).alias(f"{v.name}_nrt"),
        ]
    # The files are written sorted by platform, so a range of platform codes
    # lets the reader skip the row groups outside it.
    codes = keys["platform_code"]
    return (
        scan.filter(
            pl.col("platform_code").is_between(pl.lit(codes.min()), pl.lit(codes.max()))
        )
        .select(
            pl.col("platform_code").cast(pl.Utf8),
            pl.col("profile_no"),
            pl.col("observation_no").cast(pl.Int32, strict=False),
            pl.col("pres")
            .cast(pl.Float64, strict=False)
            .fill_nan(None)
            .cast(pl.Float32),
            *flags,
        )
        .join(keys.lazy(), on=PROFILE_KEYS, how="inner")
        .drop(PROFILE_KEYS)
        .collect(engine="streaming")
    )


def cora_tables(
    comparisons: List[Dict[str, Any]],
    profiles: pl.DataFrame,
    config: BuildConfig,
    spec: AnalysesSpec,
) -> Dict[str, pl.DataFrame]:
    """
    The NRT flags of a cast against its delayed mode flags in CORA.

    Machine learning trains on delayed mode flags, so the question is how
    far the NRT flags are from them, and whether the producer's or
    nrtqclib's come closer. Each pair of :func:`cover_profiles` is the same
    cast in both products. A pair is compared level by level when the two
    copies hold the same number of observations, every observation number
    of one is in the other, and their pressures agree within
    :data:`CORA_PRES_TOLERANCE_DBAR` at every level; and, for each variable,
    only when the NRT profile is ``nrt`` for it, as in the item tables. Each
    level then carries four flags: the producer's NRT flag, nrtqclib's on the
    NRT copy, CORA's, and nrtqclib's on the CORA copy.

    The pairs are taken in chunks of about :data:`CORA_CHUNK_LEVELS` levels:
    for each, both products are read keeping only its profiles' levels, and
    the two are joined.

    :param comparisons: :func:`delayed_comparisons`.
    :param profiles: The classified profiles, for each variable's group.
    :param config: The build configuration.
    :param spec: The analyses section.
    :return: ``cora_flags``, the levels counted by variable, coverage,
             pressure band and the four flags; and ``cora_pairs``, the pairs
             counted through each condition.
    """
    variables = list(config.variables)
    bands = {b["band"]: b["label"] for b in pres_band_labels()}
    flag_tables, pair_tables = [], []
    for comparison in comparisons:
        reference, other = comparison["reference"], comparison["other"]
        labels = _comparison_labels(reference, other)
        groups = profiles.filter(pl.col("dataset_id") == reference.id).select(
            *PROFILE_KEYS, *[f"{v.name}_group" for v in variables]
        )
        pairs = (
            comparison["covered"]["pairs"]
            .filter(pl.col("coverage").is_in(CORA_COVERAGE))
            .join(groups, on=PROFILE_KEYS, how="left")
            .with_columns(
                (pl.col("n_obs") == pl.col("n_obs_c")).alias("_same_obs"),
                *[
                    (pl.col(f"{v.name}_group") == NRT)
                    .fill_null(False)
                    .alias(f"_nrt_{v.name}")
                    for v in variables
                ],
            )
            .with_row_index("_pair")
        )
        compared = pairs.filter(
            pl.col("_same_obs")
            & pl.any_horizontal([pl.col(f"_nrt_{v.name}") for v in variables])
        )
        nrt_scan, cora_scan = _scan(reference, spec), _scan(other, spec)
        aligned, counts = [], []
        # In platform order, each coverage class apart, so that a chunk spans
        # few platforms on either side: the pairs under another code would
        # stretch the range of every chunk they fell in.
        chunk = (pl.col("n_obs").cum_sum() - 1) // CORA_CHUNK_LEVELS
        chunked = compared.sort("coverage", *PROFILE_KEYS).with_columns(
            chunk.over("coverage").alias("_chunk")
        )
        for _, pairs_part in chunked.group_by(
            "coverage", "_chunk", maintain_order=True
        ):
            mine = _paired_levels(
                nrt_scan, pairs_part.select(*PROFILE_KEYS, "_pair"), variables
            )
            theirs = _paired_levels(
                cora_scan,
                pairs_part.select(
                    pl.col("platform_code_c").alias("platform_code"),
                    pl.col("profile_no_c").alias("profile_no"),
                    "_pair",
                ),
                variables,
            )
            joined = mine.join(
                theirs, on=["_pair", "observation_no"], how="inner", suffix="_c"
            )
            del mine, theirs
            difference = (pl.col("pres") - pl.col("pres_c")).abs()
            both_missing = pl.col("pres").is_null() & pl.col("pres_c").is_null()
            joined = joined.with_columns(
                (both_missing | (difference <= CORA_PRES_TOLERANCE_DBAR))
                .fill_null(False)
                .alias("_close"),
                (both_missing | (difference == 0)).fill_null(False).alias("_exact"),
            )
            per_pair = joined.group_by("_pair").agg(
                pl.len().alias("_n_joined"),
                pl.col("_close").all().alias("_close"),
                pl.col("_exact").all().alias("_exact"),
            )
            aligned.append(per_pair)
            good = (
                per_pair.join(pairs_part.select("_pair", "n_obs"), on="_pair")
                .filter((pl.col("_n_joined") == pl.col("n_obs")) & pl.col("_close"))
                .select("_pair")
            )
            levels = joined.join(good, on="_pair", how="semi").join(
                pairs_part.select(
                    "_pair", "coverage", *[f"_nrt_{v.name}" for v in variables]
                ),
                on="_pair",
            )
            del joined
            for v in variables:
                counts.append(
                    levels.filter(pl.col(f"_nrt_{v.name}"))
                    .group_by(
                        "coverage",
                        pres_band().alias("pres_band"),
                        pl.col(f"{v.name}_qc").cast(pl.Int64).alias("original_flag"),
                        pl.col(f"{v.name}_nrt").cast(pl.Int64).alias("nrtqclib_flag"),
                        pl.col(f"{v.name}_qc_c").cast(pl.Int64).alias("cora_flag"),
                        pl.col(f"{v.name}_nrt_c")
                        .cast(pl.Int64)
                        .alias("cora_nrtqclib_flag"),
                    )
                    .agg(pl.len().cast(pl.Int64).alias("n_obs"))
                    .with_columns(pl.lit(v.name).alias("variable"))
                )

        status = pairs.join(
            pl.concat(aligned)
            if aligned
            else pl.DataFrame(
                schema={
                    "_pair": pl.UInt32,
                    "_n_joined": pl.UInt32,
                    "_close": pl.Boolean,
                    "_exact": pl.Boolean,
                }
            ),
            on="_pair",
            how="left",
        ).with_columns(
            (
                pl.col("_same_obs")
                & (pl.col("_n_joined") == pl.col("n_obs")).fill_null(False)
                & pl.col("_close").fill_null(False)
            ).alias("_aligned"),
        )
        observed = (
            pl.concat(counts)
            if counts
            else pl.DataFrame(
                schema={"coverage": pl.Utf8, "n_obs": pl.Int64, "variable": pl.Utf8}
            )
        )
        for v in variables:
            nrt = pl.col("_same_obs") & pl.col(f"_nrt_{v.name}")
            summary = (
                status.group_by("coverage")
                .agg(
                    pl.len().cast(pl.Int64).alias("n_pairs"),
                    pl.col("_same_obs").sum().cast(pl.Int64).alias("n_same_obs"),
                    nrt.sum().cast(pl.Int64).alias("n_nrt"),
                    (nrt & pl.col("_aligned")).sum().cast(pl.Int64).alias("n_aligned"),
                    (nrt & pl.col("_aligned") & pl.col("_exact").fill_null(False))
                    .sum()
                    .cast(pl.Int64)
                    .alias("n_pres_exact"),
                )
                .join(
                    observed.filter(pl.col("variable") == v.name)
                    .group_by("coverage")
                    .agg(pl.col("n_obs").sum()),
                    on="coverage",
                    how="left",
                )
                .with_columns(
                    pl.col("n_obs").fill_null(0),
                    pl.lit(v.name).alias("variable"),
                )
            )
            pair_tables.append(
                summary.select(
                    *labels,
                    "variable",
                    "coverage",
                    "n_pairs",
                    "n_same_obs",
                    "n_nrt",
                    "n_aligned",
                    "n_pres_exact",
                    "n_obs",
                )
            )
        if counts:
            flag_tables.append(
                observed.group_by(
                    "variable",
                    "coverage",
                    "pres_band",
                    "original_flag",
                    "nrtqclib_flag",
                    "cora_flag",
                    "cora_nrtqclib_flag",
                )
                .agg(pl.col("n_obs").sum())
                .with_columns(
                    pl.col("pres_band")
                    .replace_strict(bands, return_dtype=pl.Utf8)
                    .alias("pres_label")
                )
                .select(
                    *labels,
                    "variable",
                    "coverage",
                    "pres_band",
                    "pres_label",
                    "original_flag",
                    "nrtqclib_flag",
                    "cora_flag",
                    "cora_nrtqclib_flag",
                    "n_obs",
                )
            )

    order = {name: i for i, name in enumerate(CORA_COVERAGE)}

    def stack(frames: List[pl.DataFrame], by: List[str]) -> pl.DataFrame:
        if not frames:
            return pl.DataFrame()
        frame = pl.concat(frames, how="diagonal_relaxed")
        return (
            frame.with_columns(pl.col("coverage").replace_strict(order).alias("_c"))
            .sort(["dataset_id", "variable", "_c", *by], nulls_last=True)
            .drop("_c")
        )

    tables = {
        "cora_pairs": stack(pair_tables, []),
        "cora_flags": stack(
            flag_tables,
            [
                "pres_band",
                "original_flag",
                "nrtqclib_flag",
                "cora_flag",
                "cora_nrtqclib_flag",
            ],
        ),
    }
    return {name: table for name, table in tables.items() if table.height}


def check_conversion_steps(steps: pl.DataFrame, profiles: pl.DataFrame) -> List[str]:
    """
    Whether each nrtqclib output holds what ctddump kept, profile for profile.

    seastamp adds columns, not rows, and nrtqclib keeps every observation, so
    the counts should be equal. They are not drawn as a step of their own;
    a difference is reported instead.

    :return: One message per dataset whose counts differ, empty when none do.
    """
    kept = steps.filter(
        pl.col("step_order") == pl.col("step_order").max().over("dataset_id")
    )
    reached = profiles.group_by("dataset_id").agg(
        pl.len().cast(pl.Int64).alias("reached"),
        pl.col("n_obs").sum().cast(pl.Int64).alias("reached_obs"),
    )
    messages = []
    for row in kept.join(reached, on="dataset_id", how="inner").iter_rows(named=True):
        if row["n_profiles"] != row["reached"] or row["n_obs"] != row["reached_obs"]:
            messages.append(
                f"{row['dataset_id']}: ctddump kept {row['n_profiles']} profiles "
                f"and {row['n_obs']} observations, but the nrtqclib output holds "
                f"{row['reached']} and {row['reached_obs']}."
            )
    return messages


def profile_coverage(profiles: pl.DataFrame) -> Dict[str, Dict[str, str]]:
    """
    The dates of each dataset's first and last profile.

    They bound when the source was downloaded, which nothing in the files
    records: the download came after the last profile.

    :param profiles: The classified profiles of every dataset.
    :return: ``{dataset_id: {"first_profile": date, "last_profile": date}}``
             as ISO dates, empty when the profiles have no timestamp.
    """
    if "profile_timestamp" not in profiles.columns:
        return {}
    dates = (
        profiles.group_by("dataset_id")
        .agg(
            pl.col("profile_timestamp").min().dt.date().alias("first"),
            pl.col("profile_timestamp").max().dt.date().alias("last"),
        )
        .drop_nulls()
    )
    return {
        row["dataset_id"]: {
            "first_profile": row["first"].isoformat(),
            "last_profile": row["last"].isoformat(),
        }
        for row in dates.iter_rows(named=True)
    }


def provenance_meta(config: BuildConfig) -> Optional[Dict[str, Any]]:
    """
    The ``provenance`` entry of ``meta.json``.

    It is the configuration's ``provenance`` section as written, with the
    nrtqclib configuration replaced by its file name and SHA-256 checksum,
    so the record says which configuration ran without publishing its paths.

    :param config: The build configuration.
    :return: The entry, or None when the configuration has no section.
    :raises FileNotFoundError: If the named nrtqclib configuration is missing,
                               since a checksum of nothing would mislead.
    """
    spec = config.provenance
    if spec is None:
        return None
    entry: Dict[str, Any] = {}
    if spec.note:
        entry["note"] = spec.note
    entry["tools"] = spec.tools
    if spec.nrtqclib_config:
        if not os.path.isfile(spec.nrtqclib_config):
            raise FileNotFoundError(
                f"provenance.nrtqclib_config: {spec.nrtqclib_config} not found."
            )
        with open(spec.nrtqclib_config, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        entry["nrtqclib_config"] = {
            "file": os.path.basename(spec.nrtqclib_config),
            "sha256": digest,
        }
    entry["reference_data"] = spec.reference_data
    entry["products"] = spec.products
    return entry


def _write(
    tables: Dict[str, pl.DataFrame],
    config: BuildConfig,
    spec: AnalysesSpec,
    coverage: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Write the tables as CSV and ``meta.json`` beside them.

    ``meta.json`` gains a ``provenance`` entry when the configuration has the
    section (see :func:`provenance_meta`), and each dataset its first and
    last profile date when ``coverage`` has them.

    A table directory left over from an earlier run is emptied first, so a
    table that is no longer made cannot linger and be published.

    :return: The metadata written.
    """
    directory = os.path.join(spec.out_dir, "tables")
    os.makedirs(directory, exist_ok=True)
    for name in os.listdir(directory):
        if name.endswith(".csv"):
            os.remove(os.path.join(directory, name))
    for name, table in sorted(tables.items()):
        table.write_csv(os.path.join(directory, f"{name}.csv"))

    meta = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "analyses_format": ANALYSES_FORMAT,
        "tables": sorted(tables),
        "datasets": [
            {
                "id": d.id,
                "region": d.region,
                "product": d.product,
                "label": d.label,
                "source": d.source,
                "data_mode": d.data_mode,
                **(coverage or {}).get(d.id, {}),
            }
            for d in config.datasets
        ],
        "variables": [
            {
                "name": v.name,
                "label": v.label,
                "unit": v.unit,
                "bad_flag_values": v.bad_flag_values,
                "missing_flag_values": v.missing_flag_values,
            }
            for v in config.variables
        ],
        "statuses": STATUSES,
        "groups": ANALYSIS_GROUPS,
        "data_modes": DATA_MODES,
        "pres_bands": pres_band_labels(),
        "shapes": [{"key": k, "label": SHAPE_LABELS[k]} for k in SHAPES],
        "response_tests": [
            {
                "key": t["key"],
                "label": t["label"],
                "unit": t["unit"],
                "measure": t["measure"],
                "bands": [{"band": i, "label": b} for i, b in enumerate(t["bands"])],
                "clear": t["clear"],
            }
            for t in RESPONSE_TESTS
        ],
        "rules": {
            "land": {
                "column": spec.bathymetry_column,
                "positive_depth": spec.positive_depth,
                "margins_m": spec.land_margins_m,
                "shore_height_m": SHORE_HEIGHT_M,
                "shore_distance_km": SHORE_DISTANCE_KM,
            },
            "estimated_delayed": {
                "min_share": config.data_mode.estimated_min_share,
                "min_observations": config.data_mode.estimated_min_observations,
                "groups": config.data_mode.estimated_groups,
                "sensitivity": spec.estimated_sensitivity,
            },
            "position_bad_values": POSITION_BAD_VALUES,
            "cora": {
                "coverage": CORA_COVERAGE,
                "pres_tolerance_dbar": CORA_PRES_TOLERANCE_DBAR,
            },
            "density": {
                "edges": DENSITY_EDGES,
                "clear_above": DENSITY_CLEAR,
                "ice_tethered_prefixes": ICE_TETHERED_PREFIXES,
            },
        },
    }
    provenance = provenance_meta(config)
    if provenance is not None:
        meta["provenance"] = provenance
    with open(os.path.join(spec.out_dir, "meta.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2)
        handle.write("\n")
    return meta
