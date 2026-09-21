"""
Turning two flag columns into one agreement category per observation.

Every comparison in the site rests on the same four-way split of an
observation: both sources call it good, both call it bad, or exactly one of
them flags it. Computing that once here, at build time, keeps the browser
queries to counting and the plots to a colour lookup.
"""

from typing import Dict, List

import polars as pl

from nrtqc_diff.config import VariableSpec

#: The flag value the IOC/Argo scheme uses for "good"; anything worse is an
#: anomaly. The computed ``aiqclib`` flag always follows this scheme, so it
#: needs no per-variable value list the way the input flag does.
FLAG_GOOD: int = 1

#: Neither source flags the observation.
AGREE_GOOD: str = "agree_good"
#: Both sources flag the observation.
AGREE_BAD: str = "agree_bad"
#: Only the flag that came with the input data.
ORIGINAL_ONLY: str = "original_only"
#: Only the flag ``aiqclib`` computed.
AIQCLIB_ONLY: str = "aiqclib_only"
#: The input carries no readable flag and aiqclib flagged nothing either, so
#: there is nothing to compare. An observation whose input flag is missing but
#: which aiqclib did flag is reported as :data:`AIQCLIB_ONLY` instead: hiding a
#: computed flag because the input said nothing would defeat the point of the
#: comparison.
NO_INPUT_FLAG: str = "no_input_flag"

#: The categories in the order they are listed and drawn, with the label and
#: colour the site uses. Kept here so the legend, the table and the plots
#: cannot drift apart; the build copies it into ``catalog.json``.
STATUSES: List[Dict[str, str]] = [
    {"key": AGREE_BAD, "label": "Both flagged", "color": "#3f6fb5"},
    {"key": ORIGINAL_ONLY, "label": "Original only", "color": "#e07b39"},
    {"key": AIQCLIB_ONLY, "label": "aiqclib only", "color": "#cc3355"},
    {"key": AGREE_GOOD, "label": "Neither flagged", "color": "#b8c0c8"},
    {"key": NO_INPUT_FLAG, "label": "No input flag", "color": "#8e8e8e"},
]

#: The categories that mean the two sources disagree.
DISAGREEMENT_STATUSES: List[str] = [ORIGINAL_ONLY, AIQCLIB_ONLY]


def original_flag(variable: VariableSpec) -> pl.Expr:
    """
    The input's flag column as a whole number.

    Input flags arrive as integers, strings or floats depending on the source;
    anything unreadable becomes null and is reported as :data:`NO_INPUT_FLAG`
    rather than being folded into a real value.

    :param variable: The variable being compared.
    :return: The flag expression, cast to ``Int64``.
    """
    return pl.col(variable.flag).cast(pl.Int64, strict=False)


def computed_flag(variable: VariableSpec) -> pl.Expr:
    """
    The ``aiqclib`` flag column as a whole number.

    :param variable: The variable being compared.
    :return: The flag expression, cast to ``Int64``.
    """
    return pl.col(variable.nrt_flag).cast(pl.Int64, strict=False)


def status_expression(variable: VariableSpec) -> pl.Expr:
    """
    The agreement category of each observation for one variable.

    :param variable: The variable being compared.
    :return: A ``Utf8`` expression yielding one of the :data:`STATUSES` keys.
    """
    existing = original_flag(variable)
    is_bad_existing = existing.is_in(variable.bad_flag_values).fill_null(False)
    is_bad_computed = (computed_flag(variable) > FLAG_GOOD).fill_null(False)

    # The flagged cases are tested before the missing-flag case, so an
    # observation aiqclib flagged is always visible as flagged, whatever the
    # input did or did not say about it.
    return (
        pl.when(is_bad_existing & is_bad_computed)
        .then(pl.lit(AGREE_BAD))
        .when(is_bad_existing)
        .then(pl.lit(ORIGINAL_ONLY))
        .when(is_bad_computed)
        .then(pl.lit(AIQCLIB_ONLY))
        .when(existing.is_null())
        .then(pl.lit(NO_INPUT_FLAG))
        .otherwise(pl.lit(AGREE_GOOD))
        .alias(variable.status_column)
    )


def is_anomaly(variable: VariableSpec) -> pl.Expr:
    """
    Whether either source flags the observation.

    This is the trimming rule of the whole project: a profile reaches the site
    only when at least one of its observations satisfies this for at least one
    configured variable. Observations both sources call good are published
    too, but only as context inside a profile that already qualified.

    :param variable: The variable being compared.
    :return: A boolean expression, never null.
    """
    return pl.col(variable.status_column).is_in(
        [AGREE_BAD, ORIGINAL_ONLY, AIQCLIB_ONLY]
    )


def item_columns(columns: List[str], variable_names: List[str]) -> List[str]:
    """
    Pick out the per-item QC flag columns ``aiqclib`` wrote.

    An item writes either one column per variable (``temp_qc_spike``) or one
    for the whole profile (``qc_impossible_date``). Both forms are published,
    so the site can say which test drove a disagreement.

    :param columns: The columns of the NRT QC output.
    :param variable_names: The configured variable names.
    :return: The matching columns, in the order they appear in the input.
    """
    prefixes = tuple([f"{name}_qc_" for name in variable_names] + ["qc_"])
    return [column for column in columns if column.startswith(prefixes)]
