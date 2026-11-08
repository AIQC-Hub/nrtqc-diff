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

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

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
    is_one_of,
    item_columns,
    original_flag,
    status_expression,
    status_predicates,
    usable_flag,
)
from nrtqc_diff.reports import report_tables

#: The version of the tables' shape, recorded in ``meta.json``. Raise it with
#: any change to a table's name or columns, so that a page or an R script
#: reading an older set can say so instead of drawing the wrong thing.
ANALYSES_FORMAT: int = 9

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
    item_tables: Dict[str, List[pl.DataFrame]] = {}
    for dataset in config.datasets:
        scan = _scan(dataset, spec)
        mine = profiles.filter(pl.col("dataset_id") == dataset.id)
        input_flags.append(input_flag_table(scan, dataset, config))

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

    if land_flags:
        tables["depth_land_flags"] = pl.concat(land_flags, how="diagonal_relaxed")
    tables["input_flags"] = pl.concat(input_flags, how="diagonal_relaxed")
    for name, frames in item_tables.items():
        tables[name] = pl.concat(frames, how="diagonal_relaxed")

    reports = report_tables(config)
    if "conversion_steps" in reports:
        for message in check_conversion_steps(reports["conversion_steps"], profiles):
            print(f"[nrtqc-diff] warning: {message}", file=sys.stderr)
    tables.update(reports)
    overlap = overlap_table(profiles, config)
    if overlap.height:
        tables["conversion_overlap"] = overlap
    tables.update(match_tables(profiles, config))

    meta = _write(tables, config, spec)
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
    low = pl.col("elevation") <= SHORE_HEIGHT_M
    cause = (
        pl.when(_whole_degree("longitude") & _whole_degree("latitude"))
        .then(pl.lit("whole_degrees"))
        .when(low & (pl.col("dist_to_coast") <= SHORE_DISTANCE_KM))
        .then(pl.lit("shore"))
        .when(low)
        .then(pl.lit("low_far"))
        .otherwise(pl.lit("inland"))
    )
    order = {name: i for i, name in enumerate(LAND_CAUSES)}
    return (
        on_land.with_columns(cause.alias("cause"))
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
             ``dm_estimated_platforms`` and ``dm_estimated_profiles``.
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
    return {
        "dm_groups": groups,
        "dm_share_hist": hist,
        "dm_threshold": threshold,
        "dm_estimated_platforms": platforms,
        "dm_estimated_profiles": estimated,
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


def match_tables(
    profiles: pl.DataFrame, config: BuildConfig
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
    :return: ``conversion_coverage`` (one row per pair of products, with the
             profiles in each coverage class), ``conversion_coverage_years``
             (the same by year) and ``conversion_match`` (the pairs'
             numbers of observations, by coverage class).
    """
    needed = {"profile_timestamp", "longitude", "latitude", "n_obs"}
    if not needed <= set(profiles.columns):
        return {}
    present = set(profiles["dataset_id"].unique().to_list())
    coverage, years, summary = [], [], []
    for reference, other in _region_pairs(config, present):
        if reference.data_mode is not None or other.data_mode != DELAYED:
            continue
        theirs = _match_frame(profiles, other.id)
        if theirs.height == 0:
            continue
        covered = cover_profiles(_match_frame(profiles, reference.id), theirs)
        labels = [
            pl.lit(reference.id).alias("dataset_id"),
            pl.lit(reference.region).alias("region"),
            pl.lit(reference.product).alias("product"),
            pl.lit(reference.label).alias("label"),
            pl.lit(other.id).alias("compared_id"),
            pl.lit(other.label).alias("compared_label"),
        ]
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


def _write(
    tables: Dict[str, pl.DataFrame], config: BuildConfig, spec: AnalysesSpec
) -> Dict[str, Any]:
    """
    Write the tables as CSV and ``meta.json`` beside them.

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
        },
    }
    with open(os.path.join(spec.out_dir, "meta.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2)
        handle.write("\n")
    return meta
