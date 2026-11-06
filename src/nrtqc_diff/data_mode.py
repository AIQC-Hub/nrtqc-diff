"""
Splitting profiles by data mode: automated NRT, delayed mode, or unknown.

The input carries a data mode per observation and parameter (``temp_dm``,
``psal_dm``, ``pres_dm``): ``R`` for real time, ``A`` for adjusted in real
time, ``D`` for delayed mode, and ``""`` when the file does not say. A delayed
mode flag is likely to be an operator's decision rather than the automated
tests', so comparing it with the flags ``nrtqclib`` computes asks a different
question from comparing two sets of NRT flags: not "do the two automated
systems agree" but "do the automated tests anticipate what an expert
decided". The answers are kept apart by publishing every profile under
exactly one of :data:`DATA_MODES`, which the site filters on.

An empty data mode is its own group rather than a quiet NRT. ctddump writes
``""`` both for a fill cell and for a parameter whose file gives no usable
mode, such as one marked ``M`` (mixed) with no per-level variable, and that
can hide delayed mode data. "NRT only" therefore takes positive evidence:
every observation ``R`` or ``A`` in every configured column.

The split is by profile, like the trimming rule, because a profile is what a
reader opens. A dataset can also be filed whole under one group by its
configuration, for a product whose input flags come from its own delayed
mode QC whatever its data mode columns say.
"""

from typing import Dict, List, Optional

import polars as pl

from nrtqc_diff.config import DATA_MODE_KEYS, DataModeSpec
from nrtqc_diff.flags import is_one_of

#: Every observation of the profile is real time or adjusted in real time.
NRT: str = "nrt"
#: At least one observation of the profile is in delayed mode, or the dataset
#: is configured as delayed mode throughout.
DELAYED: str = "delayed"
#: No observation is in delayed mode, but at least one has no data mode.
UNKNOWN: str = "unknown"

#: The groups, in the order the site offers them, with the words it uses.
#: Kept here for the same reason as ``STATUSES``: the build copies it into
#: ``catalog.json`` and every page reads it from there.
DATA_MODES: List[Dict[str, str]] = [
    {
        "key": NRT,
        "label": "NRT only",
        "description": (
            "Profiles whose observations are all in real time or adjusted in "
            "real time: both flags come from automated tests."
        ),
    },
    {
        "key": DELAYED,
        "label": "Contains delayed mode",
        "description": (
            "Profiles with at least one delayed mode observation, or from a "
            "product whose flags come from its own delayed mode QC: the input "
            "flags there are likely to be an operator's decisions."
        ),
    },
    {
        "key": UNKNOWN,
        "label": "Data mode unknown",
        "description": (
            "Profiles with no delayed mode observation but at least one whose "
            "file gives no data mode, so they cannot be called NRT."
        ),
    },
]

assert [mode["key"] for mode in DATA_MODES] == DATA_MODE_KEYS

#: The published per-observation column saying whether any configured data
#: mode column holds a delayed value. The plots mark those observations.
DELAYED_COLUMN: str = "delayed"


def is_delayed(spec: DataModeSpec) -> pl.Expr:
    """
    Whether an observation is in delayed mode in any configured column.

    Cast to text first, because the data mode arrives as a string from
    ctddump but could as easily be categorical.

    :param spec: The configured data mode columns and values.
    :return: A boolean expression, never null.
    """
    tests = [
        is_one_of(pl.col(column).cast(pl.Utf8), spec.delayed_values)
        for column in spec.columns
    ]
    return pl.any_horizontal(tests)


def is_unknown(spec: DataModeSpec) -> pl.Expr:
    """
    Whether an observation has no data mode in any configured column.

    Empty or null, which ctddump writes when the file gives no usable mode.
    An observation can be both this and :func:`is_delayed`, delayed in one
    column and empty in another; the delayed value decides its profile.

    :param spec: The configured data mode columns.
    :return: A boolean expression, never null.
    """
    tests = [
        is_one_of(pl.col(column).cast(pl.Utf8), [""]) | pl.col(column).is_null()
        for column in spec.columns
    ]
    return pl.any_horizontal(tests)


def profile_data_mode(
    n_delayed: pl.Expr, n_unknown: pl.Expr, override: Optional[str] = None
) -> pl.Expr:
    """
    The data mode group of a profile, from its counts of telling observations.

    A delayed observation decides the profile, then an unknown one; only a
    profile with neither is NRT.

    :param n_delayed: The number of delayed mode observations in the profile.
    :param n_unknown: The number of observations with no data mode.
    :param override: A group to file the profile under regardless, from the
                     dataset's configuration, or None.
    :return: A ``Utf8`` expression yielding one key of :data:`DATA_MODES`.
    """
    if override is not None:
        return pl.lit(override)
    return (
        pl.when(n_delayed > 0)
        .then(pl.lit(DELAYED))
        .when(n_unknown > 0)
        .then(pl.lit(UNKNOWN))
        .otherwise(pl.lit(NRT))
    )
