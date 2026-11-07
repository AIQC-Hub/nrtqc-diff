"""
The build step: NRT QC output in, web-ready files out.

One run reads each configured dataset, keeps the profiles where the two flag
sources say something worth looking at, and writes three kinds of file into
``site/data``: a catalog describing the tree, one row per kept profile with
its counts, and one observation file per dataset. ``docs/DATA_MODEL.md``
documents the schemas; this module is the only thing that writes them.

Nothing here ever holds a whole dataset in memory. An input can carry a
hundred million observations, so each dataset is read twice under the
streaming engine: once to count every profile and decide which ones qualify,
and once to copy the qualifying observations straight out to parquet. What is
collected in between is one row per profile, which is small.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import polars as pl

from nrtqc_diff.config import REQUIRED_COLUMNS, BuildConfig, DatasetSpec, VariableSpec
from nrtqc_diff.data_mode import (
    DELAYED,
    DELAYED_COLUMN,
    LAND,
    PUBLISHED_GROUPS,
    input_only_share,
    is_delayed,
    is_estimated,
    is_unknown,
    land_rule,
    profile_data_mode,
    sea_floor_elevation,
)
from nrtqc_diff.flags import (
    STATUSES,
    computed_flag,
    is_anomaly,
    item_columns,
    original_flag,
    status_expression,
    status_predicates,
    usable_flag,
)

#: The shape of what a build writes, recorded in the catalog.
#:
#: The published data is built where the inputs are and uploaded to a release,
#: rather than rebuilt on every deploy, so a release can outlive the code that
#: made it. Raise this in the same commit as any change to the published
#: columns, file names or catalog fields, and a deploy carrying data from
#: before that change stops instead of rendering a site against files that no
#: longer match. `scripts/data_release.py` is what compares the two.
DATA_FORMAT: int = 4

#: The published profile column naming the variables whose flags put the
#: profile in delayed mode by estimate, comma separated, or null.
ESTIMATED_COLUMN: str = "estimated_from"

#: The key identifying a profile everywhere in the site.
PROFILE_KEYS: List[str] = ["platform_code", "profile_no"]

#: Profile columns copied from the input when present. A dataset without them
#: still builds; the site simply shows less about the profile.
OPTIONAL_PROFILE_COLUMNS: List[str] = [
    "profile_timestamp",
    "longitude",
    "latitude",
    "filename",
]

#: The per-profile column saying whether the trimming rule keeps it. Internal
#: to the build: it is dropped before ``profiles.parquet`` is written.
KEEP_COLUMN: str = "_keep"


def build_site_data(config: BuildConfig, verbose: bool = False) -> Dict[str, Any]:
    """
    Build every published file for the configured datasets.

    :param config: The build configuration.
    :param verbose: Whether to print progress per dataset.
    :return: The catalog that was written, as a dictionary.
    :raises FileNotFoundError: If a dataset's input parquet is missing.
    :raises ValueError: If an input is missing a required or configured
                        column, or is not ordered by ``platform_code``.
    """
    os.makedirs(os.path.join(config.data_dir, "obs"), exist_ok=True)

    profile_frames: List[pl.DataFrame] = []
    entries: List[Dict[str, Any]] = []

    for spec in config.datasets:
        if verbose:
            print(f"[nrtqc-diff] {spec.id}: reading {spec.path}")

        scan = _scan_dataset(spec, config)
        _check_input_order(spec, scan)

        summary = _profile_summary(scan, spec, config)
        kept = summary.filter(pl.col(KEEP_COLUMN))

        profiles = _profile_frame(kept, spec, config)
        profile_frames.append(profiles)

        _write_observations(scan, kept, spec, config)

        entries.append(
            {
                "id": spec.id,
                "region": spec.region,
                "product": spec.product,
                "label": spec.label,
                "description": spec.description,
                "observations_file": f"obs/{spec.id}.parquet",
                "n_profiles": profiles.height,
                "n_profiles_total": summary.height,
                "n_observations": int(kept["n_obs"].sum()),
                "n_observations_total": int(summary["n_obs"].sum()),
                "data_mode": spec.data_mode,
                "modes": _mode_counts(summary, kept),
            }
        )
        if verbose:
            entry = entries[-1]
            groups = ", ".join(
                f"{mode['key']} {entry['modes'][mode['key']]['n_profiles']}"
                for mode in PUBLISHED_GROUPS
            )
            filed = (
                f", all filed as {spec.data_mode} by configuration"
                if spec.data_mode
                else ""
            )
            print(
                f"[nrtqc-diff] {spec.id}: kept {entry['n_profiles']} of "
                f"{entry['n_profiles_total']} profiles, "
                f"{entry['n_observations']} observations ({groups}{filed})"
            )

    profile_path = os.path.join(config.data_dir, "profiles.parquet")
    _concat_profiles(profile_frames).write_parquet(profile_path, compression="zstd")

    catalog = _catalog(config, entries)
    with open(
        os.path.join(config.data_dir, "catalog.json"), "w", encoding="utf-8"
    ) as handle:
        json.dump(catalog, handle, indent=2)
        handle.write("\n")

    if verbose:
        print(f"[nrtqc-diff] wrote {config.data_dir}")
    return catalog


def _scan_dataset(spec: DatasetSpec, config: BuildConfig) -> pl.LazyFrame:
    """
    Open one NRT QC output, checking it carries what the build needs.

    The file is scanned rather than read: the column check costs a footer read
    and nothing is loaded until a later step asks for it. The agreement
    category is not attached here. Only the observation pass publishes it, and
    building those strings for every row of an input this size is exactly what
    the streaming passes are trying to avoid.

    :param spec: The dataset to read.
    :param config: The build configuration.
    :return: The validated scan.
    :raises FileNotFoundError: If the parquet is missing.
    :raises ValueError: If a required or configured column is absent.
    """
    if not os.path.exists(spec.path):
        raise FileNotFoundError(
            f"Dataset '{spec.id}' points at '{spec.path}', which does not exist."
        )

    scan = pl.scan_parquet(spec.path)
    _check_columns(spec, config, scan.collect_schema().names())
    return scan


def _check_columns(spec: DatasetSpec, config: BuildConfig, columns: List[str]) -> None:
    """
    Fail with one message naming every column the input is missing.

    Reporting them together matters: a mistyped variable name usually breaks
    three columns at once, and finding them one run at a time is tedious.

    :raises ValueError: If any required or configured column is absent.
    """
    needed = list(REQUIRED_COLUMNS)
    for variable in config.variables:
        needed += [variable.name, variable.flag, variable.nrt_flag]
    needed += config.data_mode.columns

    missing = [column for column in needed if column not in columns]
    if missing:
        raise ValueError(
            f"Dataset '{spec.id}' ({spec.path}) is missing "
            f"{len(missing)} column(s): {', '.join(missing)}."
        )


def _check_input_order(spec: DatasetSpec, scan: pl.LazyFrame) -> None:
    """
    Require the input to be grouped by platform, in non-decreasing order.

    The build copies observations out in input order rather than sorting them,
    because sorting a hundred million rows is the one step that would need
    unbounded memory. That is safe only if the input is already ordered, and
    the ordering is what the site depends on: it reads the observation files
    over HTTP range requests, and a profile can be found by reading one row
    group only because a row group covers a narrow range of profile ids.

    :param spec: The dataset being checked.
    :param scan: The dataset's scan.
    :raises ValueError: If ``platform_code`` ever decreases.
    """
    column = pl.col("platform_code")
    ordered = (
        scan.select((column >= column.shift(1)).fill_null(True).all())
        .collect(engine="streaming")
        .item()
    )
    if not ordered:
        raise ValueError(
            f"Dataset '{spec.id}' ({spec.path}) is not ordered by "
            "'platform_code'. The build publishes observations in input "
            "order, so sort the NRT QC output by platform_code, profile_no "
            "and observation_no before building."
        )


def _profile_summary(
    scan: pl.LazyFrame, spec: DatasetSpec, config: BuildConfig
) -> pl.DataFrame:
    """
    One row per profile of the whole input, kept or not.

    This is the pass that reads every observation. It answers four questions
    at once: how many profiles and observations the input holds, which
    profiles the trimming rule keeps, what the summary table's counts are, and
    which data mode group each profile belongs to.

    A profile with a position on land (:func:`~nrtqc_diff.data_mode.land_rule`)
    is filed under ``land`` first, whatever its data mode, as in the
    analyses. Otherwise the group is the data mode columns'
    (:func:`profile_data_mode`), unless the flags of temperature or salinity
    are estimated to be delayed mode ones
    (:func:`~nrtqc_diff.data_mode.is_estimated`): then the profile is
    delayed mode, and :data:`ESTIMATED_COLUMN` names the variables. A
    dataset filed whole by its configuration stays as configured, land
    apart.
    The result is one row per profile, so collecting it is safe however large
    the input was.

    :param scan: The scan produced by :func:`_scan_dataset`.
    :param spec: The dataset being built, for its data mode override.
    :param config: The build configuration.
    :return: The grouped frame, with a :data:`KEEP_COLUMN` flag, the
             profile's ``data_mode`` and :data:`ESTIMATED_COLUMN`.
    """
    names = scan.collect_schema().names()
    present = [name for name in OPTIONAL_PROFILE_COLUMNS if name in names]

    aggregations: List[pl.Expr] = [pl.len().alias("n_obs")]
    aggregations += [pl.col(name).first() for name in present]
    for variable in config.variables:
        predicates = status_predicates(variable)
        for status in STATUSES:
            aggregations.append(
                predicates[status["key"]]
                .sum()
                .cast(pl.Int32)
                .alias(f"{variable.name}_n_{status['key']}")
            )
        aggregations.append(
            usable_flag(variable)
            .sum()
            .cast(pl.Int32)
            .alias(f"{variable.name}_n_usable")
        )
    aggregations.append(
        is_delayed(config.data_mode).sum().cast(pl.Int32).alias("n_obs_delayed")
    )
    aggregations.append(
        is_unknown(config.data_mode, names).sum().cast(pl.Int32).alias("n_obs_unknown")
    )
    aggregations.append(
        pl.any_horizontal([is_anomaly(v) for v in config.variables])
        .any()
        .alias(KEEP_COLUMN)
    )
    land_column, positive_depth = land_rule(config)
    if land_column in names:
        aggregations.append(
            sea_floor_elevation(land_column, positive_depth).max().alias("_elevation")
        )
        on_land = (pl.col("_elevation") >= 0).fill_null(False)
    else:
        on_land = pl.lit(False)

    declared = profile_data_mode(
        pl.col("n_obs_delayed"), pl.col("n_obs_unknown"), spec.data_mode
    )
    if spec.data_mode is not None:
        estimated = pl.lit(None, dtype=pl.Utf8)
    else:
        estimated = pl.concat_str(
            [
                pl.when(
                    ~on_land
                    & is_estimated(
                        input_only_share(v.name, config.data_mode),
                        declared,
                        config.data_mode,
                    )
                ).then(pl.lit(v.name))
                for v in config.variables
            ],
            separator=",",
            ignore_nulls=True,
        )
        estimated = pl.when(estimated != "").then(estimated)
    return (
        scan.group_by(PROFILE_KEYS)
        .agg(aggregations)
        .collect(engine="streaming")
        .with_columns(estimated.alias(ESTIMATED_COLUMN))
        .with_columns(
            pl.when(on_land)
            .then(pl.lit(LAND))
            .when(pl.col(ESTIMATED_COLUMN).is_not_null())
            .then(pl.lit(DELAYED))
            .otherwise(declared)
            .alias("data_mode")
        )
    )


def _mode_counts(summary: pl.DataFrame, kept: pl.DataFrame) -> Dict[str, Any]:
    """
    The published and source totals of one dataset, per data mode group.

    The same four numbers the dataset entry carries for the whole dataset, so
    a page filtered to one group can still say how much of that group the
    trimming left out.

    :param summary: Every profile of the dataset, from :func:`_profile_summary`.
    :param kept: The profiles the trimming rule keeps.
    :return: ``{mode: {n_profiles, n_profiles_total, n_observations,
             n_observations_total}}`` for every key of ``PUBLISHED_GROUPS``.
    """
    counts: Dict[str, Any] = {}
    for mode in PUBLISHED_GROUPS:
        total = summary.filter(pl.col("data_mode") == mode["key"])
        published = kept.filter(pl.col("data_mode") == mode["key"])
        counts[mode["key"]] = {
            "n_profiles": published.height,
            "n_profiles_total": total.height,
            "n_observations": int(published["n_obs"].sum()),
            "n_observations_total": int(total["n_obs"].sum()),
        }
        if mode["key"] == DELAYED:
            # How much of the group is there by estimate rather than declared.
            estimated = pl.col(ESTIMATED_COLUMN).is_not_null()
            counts[DELAYED]["n_profiles_estimated"] = published.filter(estimated).height
            counts[DELAYED]["n_profiles_estimated_total"] = total.filter(
                estimated
            ).height
    return counts


def _write_observations(
    scan: pl.LazyFrame,
    kept: pl.DataFrame,
    spec: DatasetSpec,
    config: BuildConfig,
) -> None:
    """
    Copy the qualifying observations out to ``obs/{dataset_id}.parquet``.

    This is the second and last pass over the input. It streams: the rows go
    from the scan through the filter to the file without ever being collected,
    and the only thing held in memory is the list of profiles to keep, which
    has one row per profile.

    :param scan: The scan produced by :func:`_scan_dataset`.
    :param kept: The profiles the trimming rule keeps.
    :param spec: The dataset being built.
    :param config: The build configuration.
    """
    qualifying = kept.select(PROFILE_KEYS).lazy()
    observations = _observation_frame(
        scan.join(qualifying, on=PROFILE_KEYS, how="semi"), spec, config
    )
    observations.sink_parquet(
        os.path.join(config.data_dir, "obs", f"{spec.id}.parquet"),
        compression="zstd",
        row_group_size=config.row_group_size,
        engine="streaming",
    )


def _profile_id() -> pl.Expr:
    """
    The one-column profile key the site selects on.

    :return: A ``Utf8`` expression of the form ``platform:profile_no``.
    """
    return (
        pl.col("platform_code").cast(pl.Utf8)
        + pl.lit(":")
        + pl.col("profile_no").cast(pl.Utf8)
    ).alias("profile_id")


def _observation_frame(
    scan: pl.LazyFrame, spec: DatasetSpec, config: BuildConfig
) -> pl.LazyFrame:
    """
    One row per published observation, with both flags, the category and the
    data mode.

    The rows keep the order they had in the input, which
    :func:`_check_input_order` has already required to be by platform. The
    site orders by ``observation_no`` in SQL when it draws a profile, so
    nothing here depends on the order for display; the row groups do, for
    range requests.

    :param scan: The trimmed scan.
    :param spec: The dataset being built.
    :param config: The build configuration.
    :return: The frame written to ``obs/{dataset_id}.parquet``.
    """
    columns: List[pl.Expr] = [
        pl.lit(spec.id).alias("dataset_id"),
        _profile_id(),
        pl.col("platform_code").cast(pl.Utf8),
        pl.col("profile_no").cast(pl.Int32),
        pl.col("observation_no").cast(pl.Int32),
        pl.col("pres").cast(pl.Float64),
    ]
    for variable in config.variables:
        columns += [
            pl.col(variable.name).cast(pl.Float64),
            original_flag(variable).alias(variable.flag),
            computed_flag(variable).alias(variable.nrt_flag),
            status_expression(variable),
        ]
    # The data mode as the input had it, for the hover box, and whether it
    # makes the observation a delayed mode one, for the marker. The second is
    # derivable from the first, but only with the configured values in hand.
    columns += [pl.col(name).cast(pl.Utf8) for name in config.data_mode.columns]
    columns.append(is_delayed(config.data_mode).alias(DELAYED_COLUMN))

    items = item_columns(
        scan.collect_schema().names(), [v.name for v in config.variables]
    )
    columns += [pl.col(name).cast(pl.Int32, strict=False) for name in items]

    return scan.select(columns)


def _profile_frame(
    kept: pl.DataFrame, spec: DatasetSpec, config: BuildConfig
) -> pl.DataFrame:
    """
    One row per published profile, with the counts the summary table shows.

    Per variable the row carries a count for every agreement category plus
    ``{variable}_n_disagree``, and the frame as a whole carries ``n_disagree``
    summed across variables, which is the table's default sort. ``data_mode``
    says which group of ``PUBLISHED_GROUPS`` the profile is published under,
    :data:`ESTIMATED_COLUMN` which variables put it in delayed mode by
    estimate, if any, and
    ``n_obs_delayed`` and ``n_obs_unknown`` how many of its observations are
    delayed and have no data mode. The two counts are what the data mode
    columns say even for a dataset filed whole under one group.

    :param kept: The rows of :func:`_profile_summary` the trimming rule keeps.
    :param spec: The dataset being built.
    :param config: The build configuration.
    :return: The dataset's slice of ``profiles.parquet``.
    """
    present = [name for name in OPTIONAL_PROFILE_COLUMNS if name in kept.columns]

    disagreement: List[pl.Expr] = []
    for variable in config.variables:
        per_variable = pl.col(f"{variable.name}_n_original_only") + pl.col(
            f"{variable.name}_n_nrtqclib_only"
        )
        disagreement.append(
            per_variable.cast(pl.Int32).alias(f"{variable.name}_n_disagree")
        )

    grouped = kept.with_columns(disagreement).with_columns(
        pl.sum_horizontal([pl.col(f"{v.name}_n_disagree") for v in config.variables])
        .cast(pl.Int32)
        .alias("n_disagree")
    )

    return (
        grouped.with_columns(
            pl.lit(spec.id).alias("dataset_id"),
            pl.lit(spec.region).alias("region"),
            pl.lit(spec.product).alias("product"),
            _profile_id(),
        )
        .select(
            ["dataset_id", "region", "product", "profile_id"]
            + PROFILE_KEYS
            + present
            + ["data_mode", ESTIMATED_COLUMN]
            + ["n_obs", "n_obs_delayed", "n_obs_unknown", "n_disagree"]
            + [
                column
                for variable in config.variables
                for column in _variable_count_columns(variable)
            ]
        )
        .sort(["n_disagree", "profile_id"], descending=[True, False])
    )


def _variable_count_columns(variable: VariableSpec) -> List[str]:
    """
    The per-variable count columns of ``profiles.parquet``, in display order.

    :param variable: The variable being compared.
    :return: The column names.
    """
    return [f"{variable.name}_n_disagree"] + [
        f"{variable.name}_n_{status['key']}" for status in STATUSES
    ]


def _concat_profiles(frames: List[pl.DataFrame]) -> pl.DataFrame:
    """
    Stack the per-dataset profile frames into the single published file.

    :param frames: One frame per dataset, possibly with differing optional
                   columns.
    :return: The concatenated frame.
    """
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed")


def _catalog(config: BuildConfig, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Assemble ``catalog.json``: the tree, the variables and the categories.

    The regions and products are derived from the dataset entries rather than
    configured separately, so the tree cannot disagree with what was built.

    :param config: The build configuration.
    :param entries: One entry per built dataset.
    :return: The catalog.
    """
    regions: List[Dict[str, Any]] = []
    for entry in entries:
        region = _find_or_append(regions, entry["region"], "products")
        product = _find_or_append(region["products"], entry["product"], "datasets")
        product["datasets"].append(entry)

    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_format": DATA_FORMAT,
        "title": config.title,
        "variables": [
            {
                "name": variable.name,
                "label": variable.label,
                "unit": variable.unit,
                "flag": variable.flag,
                "nrt_flag": variable.nrt_flag,
                "status_column": variable.status_column,
                "bad_flag_values": variable.bad_flag_values,
                "missing_flag_values": variable.missing_flag_values,
            }
            for variable in config.variables
        ],
        "statuses": STATUSES,
        "data_mode": {
            "columns": config.data_mode.columns,
            "delayed_values": config.data_mode.delayed_values,
            "delayed_column": DELAYED_COLUMN,
            "estimated": {
                "enabled": config.data_mode.estimate,
                "min_share": config.data_mode.estimated_min_share,
                "min_observations": config.data_mode.estimated_min_observations,
                "groups": config.data_mode.estimated_groups,
                "column": ESTIMATED_COLUMN,
            },
            "land_column": land_rule(config)[0],
        },
        "data_modes": PUBLISHED_GROUPS,
        "regions": regions,
    }


def _find_or_append(
    nodes: List[Dict[str, Any]], name: str, child_key: str
) -> Dict[str, Any]:
    """
    Find a named node in a tree level, appending it when it is new.

    :param nodes: The nodes at this level.
    :param name: The node name to find.
    :param child_key: The key holding this node's children.
    :return: The existing or newly appended node.
    """
    for node in nodes:
        if node["name"] == name:
            return node
    node = {"name": name, child_key: []}
    nodes.append(node)
    return node
