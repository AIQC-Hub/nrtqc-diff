"""
Splitting profiles by data mode: automated NRT, or touched in delayed mode.

The input carries a data mode per observation and parameter (``temp_dm``,
``psal_dm``, ``pres_dm``): ``R`` for real time, ``A`` for adjusted in real
time, ``D`` for delayed mode. A delayed mode flag is likely to be an
operator's decision rather than the automated tests', so comparing it with the
flags ``nrtqclib`` computes asks a different question from comparing two sets
of NRT flags: not "do the two automated systems agree" but "do the automated
tests anticipate what an expert decided". The two answers are kept apart by
publishing every profile under exactly one of :data:`DATA_MODES`, which the
site filters on.

The split is by profile, like the trimming rule, because a profile is what a
reader opens. In the real batch that loses almost nothing: a profile that
mixes delayed mode with real time observations is rare, and nearly always one
whose pressure alone was revised.
"""

from typing import Dict, List

import polars as pl

from nrtqc_diff.config import DataModeSpec
from nrtqc_diff.flags import is_one_of

#: No observation of the profile is in delayed mode, in any configured column.
NRT: str = "nrt"
#: At least one observation of the profile is in delayed mode.
DELAYED: str = "delayed"

#: The two groups, in the order the site offers them, with the words it uses.
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
            "Profiles with at least one delayed mode observation: the input "
            "flags there are likely to be an operator's decisions."
        ),
    },
]

#: The published per-observation column saying whether any configured data
#: mode column holds a delayed value. The plots mark those observations.
DELAYED_COLUMN: str = "delayed"


def is_delayed(spec: DataModeSpec) -> pl.Expr:
    """
    Whether an observation is in delayed mode in any configured column.

    Cast to text first, because the data mode arrives as a string from
    ctddump but could as easily be categorical. A null or empty cell is not
    delayed: it says nothing about the observation.

    :param spec: The configured data mode columns and values.
    :return: A boolean expression, never null.
    """
    tests = [
        is_one_of(pl.col(column).cast(pl.Utf8), spec.delayed_values)
        for column in spec.columns
    ]
    return pl.any_horizontal(tests)


def profile_data_mode(n_delayed: pl.Expr) -> pl.Expr:
    """
    The data mode group of a profile, from its count of delayed observations.

    :param n_delayed: The number of delayed mode observations in the profile.
    :return: A ``Utf8`` expression yielding :data:`NRT` or :data:`DELAYED`.
    """
    return pl.when(n_delayed > 0).then(pl.lit(DELAYED)).otherwise(pl.lit(NRT))
