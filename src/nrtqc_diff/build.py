"""
The build step: NRT QC output in, web-ready files out.

One run reads each configured dataset, keeps the profiles where the two flag
sources say something worth looking at, and writes three kinds of file into
``site/data``: a catalog describing the tree, one row per kept profile with
its counts, and one observation file per dataset. ``docs/DATA_MODEL.md``
documents the schemas; this module is the only thing that writes them.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import polars as pl

from nrtqc_diff.config import REQUIRED_COLUMNS, BuildConfig, DatasetSpec, VariableSpec
from nrtqc_diff.flags import (
    STATUSES,
    computed_flag,
    is_anomaly,
    item_columns,
    original_flag,
    status_expression,
)

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


def build_site_data(config: BuildConfig, verbose: bool = False) -> Dict[str, Any]:
    """
    Build every published file for the configured datasets.

    :param config: The build configuration.
    :param verbose: Whether to print progress per dataset.
    :return: The catalog that was written, as a dictionary.
    :raises FileNotFoundError: If a dataset's input parquet is missing.
    :raises ValueError: If an input is missing a required or configured column.
    """
    os.makedirs(os.path.join(config.data_dir, "obs"), exist_ok=True)

    profile_frames: List[pl.DataFrame] = []
    entries: List[Dict[str, Any]] = []

    for spec in config.datasets:
        if verbose:
            print(f"[nrtqc-diff] {spec.id}: reading {spec.path}")

        frame = _read_dataset(spec, config)
        total_profiles = frame.select(PROFILE_KEYS).unique().height

        kept = _trim_to_anomalous_profiles(frame, config)
        observations = _observation_frame(kept, spec, config)
        profiles = _profile_frame(kept, spec, config)

        observation_path = os.path.join(config.data_dir, "obs", f"{spec.id}.parquet")
        observations.write_parquet(observation_path, compression="zstd")
        profile_frames.append(profiles)

        entries.append(
            {
                "id": spec.id,
                "region": spec.region,
                "product": spec.product,
                "label": spec.label,
                "description": spec.description,
                "observations_file": f"obs/{spec.id}.parquet",
                "n_profiles": profiles.height,
                "n_profiles_total": total_profiles,
                "n_observations": observations.height,
                "n_observations_total": frame.height,
            }
        )
        if verbose:
            print(
                f"[nrtqc-diff] {spec.id}: kept {profiles.height} of "
                f"{total_profiles} profiles, {observations.height} observations"
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


def _read_dataset(spec: DatasetSpec, config: BuildConfig) -> pl.DataFrame:
    """
    Read one NRT QC output and attach the agreement category per variable.

    :param spec: The dataset to read.
    :param config: The build configuration.
    :return: The input frame plus one ``{variable}_status`` column per variable.
    :raises FileNotFoundError: If the parquet is missing.
    :raises ValueError: If a required or configured column is absent.
    """
    if not os.path.exists(spec.path):
        raise FileNotFoundError(
            f"Dataset '{spec.id}' points at '{spec.path}', which does not exist."
        )

    frame = pl.read_parquet(spec.path)
    _check_columns(spec, config, frame.columns)
    return frame.with_columns(
        [status_expression(variable) for variable in config.variables]
    )


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

    missing = [column for column in needed if column not in columns]
    if missing:
        raise ValueError(
            f"Dataset '{spec.id}' ({spec.path}) is missing "
            f"{len(missing)} column(s): {', '.join(missing)}."
        )


def _trim_to_anomalous_profiles(
    frame: pl.DataFrame, config: BuildConfig
) -> pl.DataFrame:
    """
    Keep whole profiles in which either source flagged anything.

    Trimming is by profile, not by observation: a flagged point is only
    readable next to the unflagged ones above and below it, so the profile is
    published entire or not at all.

    :param frame: The frame produced by :func:`_read_dataset`.
    :param config: The build configuration.
    :return: The rows of the qualifying profiles.
    """
    anomaly = pl.any_horizontal([is_anomaly(v) for v in config.variables])
    qualifying = frame.filter(anomaly).select(PROFILE_KEYS).unique(subset=PROFILE_KEYS)
    return frame.join(qualifying, on=PROFILE_KEYS, how="semi")


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
    frame: pl.DataFrame, spec: DatasetSpec, config: BuildConfig
) -> pl.DataFrame:
    """
    One row per published observation, with both flags and the category.

    :param frame: The trimmed frame.
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
            pl.col(variable.status_column),
        ]

    items = item_columns(frame.columns, [v.name for v in config.variables])
    columns += [pl.col(name).cast(pl.Int32, strict=False) for name in items]

    return frame.select(columns).sort(["profile_id", "observation_no"])


def _profile_frame(
    frame: pl.DataFrame, spec: DatasetSpec, config: BuildConfig
) -> pl.DataFrame:
    """
    One row per published profile, with the counts the summary table shows.

    Per variable the row carries a count for every agreement category plus
    ``{variable}_n_disagree``, and the frame as a whole carries ``n_disagree``
    summed across variables, which is the table's default sort.

    :param frame: The trimmed frame.
    :param spec: The dataset being built.
    :param config: The build configuration.
    :return: The dataset's slice of ``profiles.parquet``.
    """
    present = [name for name in OPTIONAL_PROFILE_COLUMNS if name in frame.columns]

    aggregations: List[pl.Expr] = [pl.len().alias("n_obs")]
    aggregations += [pl.col(name).first() for name in present]
    for variable in config.variables:
        for status in STATUSES:
            aggregations.append(
                (pl.col(variable.status_column) == status["key"])
                .sum()
                .cast(pl.Int32)
                .alias(f"{variable.name}_n_{status['key']}")
            )

    grouped = frame.group_by(PROFILE_KEYS).agg(aggregations)

    disagreement: List[pl.Expr] = []
    for variable in config.variables:
        per_variable = pl.col(f"{variable.name}_n_original_only") + pl.col(
            f"{variable.name}_n_aiqclib_only"
        )
        disagreement.append(
            per_variable.cast(pl.Int32).alias(f"{variable.name}_n_disagree")
        )

    grouped = grouped.with_columns(disagreement).with_columns(
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
            + ["n_obs", "n_disagree"]
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
            }
            for variable in config.variables
        ],
        "statuses": STATUSES,
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
