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
   look like an operator's, because most of its observations are flagged by
   the input and not by nrtqclib, is filed as ``estimated_delayed``.
3. **What is left** is ``nrt`` (or ``unknown``), and the per-item tables are
   computed over ``nrt`` alone: two sets of automated flags, on positions at
   sea.

Like the build, nothing here holds a dataset in memory. Each dataset is read
in a streaming pass that yields one row per profile, which decides the
groups; then in passes that keep only the profiles of one group and group the
observations by a handful of small keys.
"""

import json
import os
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
    NRT,
    UNKNOWN,
    is_delayed,
    is_unknown,
    profile_data_mode,
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
)
from nrtqc_diff.reports import report_tables

#: The version of the tables' shape, recorded in ``meta.json``. Raise it with
#: any change to a table's name or columns, so that a page or an R script
#: reading an older set can say so instead of drawing the wrong thing.
ANALYSES_FORMAT: int = 1

#: The key identifying a profile.
PROFILE_KEYS: List[str] = ["platform_code", "profile_no"]

#: A profile whose position is at or above sea level.
LAND: str = "land"
#: An NRT (or unknown) profile whose input flags look like delayed mode ones.
ESTIMATED_DELAYED: str = "estimated_delayed"

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
            "Marked NRT, or with no data mode, but most observations with a "
            "usable input flag are flagged by the input and not by nrtqclib, "
            "which is what an operator's edits look like."
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
    item_tables: Dict[str, List[pl.DataFrame]] = {}
    for dataset in config.datasets:
        scan = _scan(dataset, spec)
        mine = profiles.filter(pl.col("dataset_id") == dataset.id)

        land = mine.filter(pl.col("group") == LAND).select(PROFILE_KEYS)
        if land.height:
            land_flags.append(land_flag_table(scan, land, dataset, config))

        nrt = mine.filter(pl.col("group") == NRT).select(PROFILE_KEYS)
        if nrt.height:
            if verbose:
                print(
                    f"[nrtqc-diff] {dataset.id}: comparing items over "
                    f"{nrt.height} NRT profiles"
                )
            for name, table in item_tables_for(scan, nrt, dataset, config).items():
                item_tables.setdefault(name, []).append(table)

    if land_flags:
        tables["depth_land_flags"] = pl.concat(land_flags, how="diagonal_relaxed")
    for name, frames in item_tables.items():
        tables[name] = pl.concat(frames, how="diagonal_relaxed")

    reports = report_tables(config)
    if "conversion_steps" in reports:
        reports["conversion_steps"] = _with_nrtqc_step(
            reports["conversion_steps"], profiles, config
        )
    tables.update(reports)

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
    column = pl.col(spec.bathymetry_column).cast(pl.Float64, strict=False)
    return -column if spec.positive_depth else column


def usable_flag(variable: VariableSpec) -> pl.Expr:
    """
    Whether the input flag of an observation says anything: present, and not
    one of the variable's missing flag values.
    """
    existing = original_flag(variable)
    return existing.is_not_null() & ~is_one_of(existing, variable.missing_flag_values)


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
        is_unknown(config.data_mode).sum().cast(pl.Int64).alias("n_obs_unknown")
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
    File every profile under one of :data:`ANALYSIS_GROUPS`.

    The share of a variable is the observations only the input flags, over
    those whose input flag says anything; it is computed only for a variable
    with at least ``estimated_min_observations`` such observations. A
    profile's share is the larger of its variables', so a profile whose
    salinity alone was reworked by an operator is caught too.

    :param profiles: The output of :func:`profile_summary`, any number of
                     datasets stacked.
    :param config: The build configuration.
    :param spec: The analyses section.
    :return: The profiles with ``land``, ``{variable}_share``, ``share``,
             ``estimated_delayed`` and ``group`` added.
    """
    shares = []
    for variable in config.variables:
        usable = pl.col(f"{variable.name}_n_usable")
        shares.append(
            pl.when(usable >= spec.estimated_min_observations)
            .then(pl.col(f"{variable.name}_n_{ORIGINAL_ONLY}") / usable)
            .otherwise(None)
            .alias(f"{variable.name}_share")
        )
    land = (pl.col("elevation") >= 0).fill_null(False)
    estimated = (
        pl.col("data_mode").is_in(spec.estimated_groups)
        & ~pl.col("land")
        & (pl.col("share") >= spec.estimated_min_share).fill_null(False)
    )
    group = (
        pl.when(pl.col("land"))
        .then(pl.lit(LAND))
        .when(pl.col("data_mode") == DELAYED)
        .then(pl.lit(DELAYED))
        .when(pl.col("estimated_delayed"))
        .then(pl.lit(ESTIMATED_DELAYED))
        .otherwise(pl.col("data_mode"))
    )
    share_columns = [f"{v.name}_share" for v in config.variables]
    return (
        profiles.with_columns(land.alias("land"), *shares)
        .with_columns(pl.max_horizontal(share_columns).alias("share"))
        .with_columns(estimated.alias("estimated_delayed"))
        .with_columns(group.alias("group"))
    )


def _dataset_columns() -> List[str]:
    return ["dataset_id", "region", "product", "label"]


def _counts_by(
    profiles: pl.DataFrame, keys: List[str], config: BuildConfig
) -> pl.DataFrame:
    """Profiles, observations and status counts summed over ``keys``."""
    sums = [pl.len().cast(pl.Int64).alias("n_profiles"), pl.col("n_obs").sum()]
    for variable in config.variables:
        sums += [pl.col(f"{variable.name}_n_{s['key']}").sum() for s in STATUSES]
    return profiles.group_by(keys, maintain_order=True).agg(sums)


def depth_tables(
    profiles: pl.DataFrame, config: BuildConfig, spec: AnalysesSpec
) -> Dict[str, pl.DataFrame]:
    """
    The tables of the Depth page, from the profile summary alone.

    :return: ``depth_summary``, ``depth_land_margin``,
             ``depth_elevation_hist``, ``depth_land_position_qc``,
             ``depth_land_original`` and ``depth_land_profiles``.
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
    return tables


def data_mode_tables(
    profiles: pl.DataFrame, config: BuildConfig, spec: AnalysesSpec
) -> Dict[str, pl.DataFrame]:
    """
    The tables of the NRT and delayed mode page, from the profile summary.

    :return: ``dm_groups``, ``dm_share_hist``, ``dm_threshold``,
             ``dm_estimated_platforms`` and ``dm_estimated_profiles``.
    """
    keys = _dataset_columns()
    at_sea = profiles.filter(~pl.col("land"))
    eligible = at_sea.filter(pl.col("share").is_not_null())

    groups = _counts_by(profiles, keys + ["data_mode", "group"], config).sort(
        keys + ["data_mode", "group"]
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

    thresholds = sorted(set(spec.estimated_sensitivity) | {spec.estimated_min_share})
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

    candidate = pl.col("data_mode").is_in(spec.estimated_groups)
    platforms = (
        at_sea.group_by(keys + ["platform_code"], maintain_order=True)
        .agg(
            pl.len().cast(pl.Int64).alias("n_profiles"),
            candidate.sum().cast(pl.Int64).alias("n_candidates"),
            pl.col("estimated_delayed").sum().cast(pl.Int64).alias("n_estimated"),
            (pl.col("data_mode") == DELAYED).sum().cast(pl.Int64).alias("n_delayed"),
        )
        .filter(pl.col("n_estimated") > 0)
        .sort(["dataset_id", "n_estimated"], descending=[False, True])
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
        )
        if c in profiles.columns
    ]
    estimated = (
        profiles.filter(pl.col("group") == ESTIMATED_DELAYED)
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
    keep: pl.DataFrame,
    dataset: DatasetSpec,
    config: BuildConfig,
) -> Dict[str, pl.DataFrame]:
    """
    The per-item tables of one dataset, over the profiles in ``keep``.

    Every table is derived from one cube per variable: the observations
    counted by pressure band, input flag, computed flag and which items
    failed. The agreement category is a function of the two flags, so it is
    attached to the cube after collecting it rather than computed per row.

    :param scan: The dataset.
    :param keep: The keys of the profiles to count.
    :param dataset: The dataset's configuration.
    :param config: The build configuration.
    :return: ``items_cube``, ``items_status``, ``items_by_item``,
             ``items_combinations``, ``items_depth``, ``items_original_flags``
             and ``items_platforms``.
    """
    names = scan.collect_schema().names()
    variable_names = [v.name for v in config.variables]
    kept = scan.join(keep.lazy(), on=PROFILE_KEYS, how="semi")
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
            kept.select(keys)
            .group_by(pl.all())
            .agg(pl.len().cast(pl.Int64).alias("n_obs"))
        )

    platform_sums: List[pl.Expr] = [
        pl.col("profile_no").n_unique().cast(pl.Int64).alias("n_profiles"),
        pl.len().cast(pl.Int64).alias("n_obs"),
    ]
    for variable in config.variables:
        predicates = status_predicates(variable)
        for status in STATUSES:
            platform_sums.append(
                predicates[status["key"]]
                .sum()
                .cast(pl.Int64)
                .alias(f"{variable.name}_n_{status['key']}")
            )
    queries.append(
        kept.group_by(pl.col("platform_code").cast(pl.Utf8)).agg(platform_sums)
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
    for variable, columns, cube in zip(config.variables, item_lists, results):
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
        "items_platforms": results[-1].select(label + [pl.all()]).sort("platform_code"),
    }
    if by_item:
        tables["items_by_item"] = pl.concat(by_item)
    if combos:
        tables["items_combinations"] = pl.concat(combos)
    return tables


def _with_nrtqc_step(
    steps: pl.DataFrame, profiles: pl.DataFrame, config: BuildConfig
) -> pl.DataFrame:
    """
    Add what reached the nrtqclib output as the last step of the conversion.

    It should equal the last ctddump step: seastamp adds columns, not rows,
    and nrtqclib keeps every observation. Putting it in the same table is
    what lets the page show that it does.
    """
    reached = (
        profiles.group_by(_dataset_columns(), maintain_order=True)
        .agg(
            pl.col("platform_code").n_unique().cast(pl.Int64).alias("n_platforms"),
            pl.len().cast(pl.Int64).alias("n_profiles"),
            pl.col("n_obs").sum().cast(pl.Int64),
        )
        .filter(pl.col("dataset_id").is_in(steps["dataset_id"].unique().implode()))
        .with_columns(
            pl.lit("nrtqc").alias("step"),
            pl.lit(int(steps["step_order"].max()) + 1)
            .cast(steps["step_order"].dtype)
            .alias("step_order"),
            pl.lit("In the nrtqclib output").alias("step_label"),
        )
    )
    return pl.concat(
        [steps, reached.select(steps.columns)], how="vertical_relaxed"
    ).sort(["dataset_id", "step_order"], maintain_order=True)


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
            },
            "estimated_delayed": {
                "min_share": spec.estimated_min_share,
                "min_observations": spec.estimated_min_observations,
                "groups": spec.estimated_groups,
                "sensitivity": spec.estimated_sensitivity,
            },
            "position_bad_values": POSITION_BAD_VALUES,
        },
    }
    with open(os.path.join(spec.out_dir, "meta.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2)
        handle.write("\n")
    return meta
