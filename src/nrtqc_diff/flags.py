"""
Turning two flag columns into one agreement category per observation.

Every comparison in the site rests on the same split of an observation: both
sources call it good, both call it bad, exactly one of them flags it, or the
input says nothing worth comparing. Computing that once here, at build time,
keeps the browser queries to counting and the plots to a colour lookup.
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
#: The input carries no usable flag and aiqclib flagged nothing either, so
#: there is nothing to compare. This covers both a flag that will not parse
#: (an empty string, say) and one of the variable's ``missing_flag_values``,
#: the values of the scheme that carry no judgement: 0 means no QC was
#: performed and 9 means the value itself is missing. Neither says the
#: observation is good, so neither may be counted as the two sources agreeing.
#: An observation whose input flag is missing but which aiqclib did flag is
#: reported as :data:`AIQCLIB_ONLY` instead: hiding a computed flag because
#: the input said nothing would defeat the point of the comparison.
NO_INPUT_FLAG: str = "no_input_flag"

#: The categories in the order they are listed and drawn, with the label and
#: colour the site uses. Kept here so the legend, the table and the plots
#: cannot drift apart; the build copies it into ``catalog.json``.
#:
#: ``flagged`` says whether at least one of the two sources flagged the
#: observation, which is the same rule :func:`is_anomaly` applies. It is
#: published so a page can pick out those categories without naming them:
#: the summary page draws its composition bar over the flagged categories
#: alone, because a published profile is mostly observations both sources
#: call good and drawing all five would make every bar the same grey block.
STATUSES: List[Dict[str, object]] = [
    {"key": AGREE_BAD, "label": "Both flagged", "color": "#3f6fb5", "flagged": True},
    {
        "key": ORIGINAL_ONLY,
        "label": "Original only",
        "color": "#e07b39",
        "flagged": True,
    },
    {
        "key": AIQCLIB_ONLY,
        "label": "aiqclib only",
        "color": "#cc3355",
        "flagged": True,
    },
    {
        "key": AGREE_GOOD,
        "label": "Neither flagged",
        "color": "#b8c0c8",
        "flagged": False,
    },
    {
        "key": NO_INPUT_FLAG,
        "label": "No input flag",
        "color": "#8e8e8e",
        "flagged": False,
    },
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


def _is_one_of(expression: pl.Expr, values: List[int]) -> pl.Expr:
    """
    Whether ``expression`` holds one of ``values``, as a never-null boolean.

    Written as a chain of equalities rather than the ``is_in`` it looks like,
    on purpose. The two mean the same thing, but ``is_in`` inside a group-by
    aggregation makes polars' streaming engine give up and materialise the
    whole input: on the largest dataset here that is 14 GB of memory against
    0.9 GB for the chain, for the same answer. The flag value lists are three
    or four entries long, so the chain costs nothing to build.

    :param expression: The flag expression to test.
    :param values: The values that count as a match, possibly none.
    :return: A boolean expression the length of the frame, never null.
    """
    if not values:
        # Nothing can match. Anchored to the column rather than written as a
        # bare literal so the result keeps the frame's length.
        return expression.is_null() & pl.lit(False)

    test = expression == values[0]
    for value in values[1:]:
        test = test | (expression == value)
    return test.fill_null(False)


def status_predicates(variable: VariableSpec) -> Dict[str, pl.Expr]:
    """
    One boolean expression per agreement category, for one variable.

    This is where the rule actually lives. Both the published
    ``{variable}_status`` column and the per-profile counts are derived from
    it, so there is no second place for the definition to drift to.

    The categories in the table of ``docs/DATA_MODEL.md`` are written as a
    priority chain, which is how it reads: the flagged cases are decided
    before the missing-flag case, so an observation aiqclib flagged is always
    visible as flagged, whatever the input did or did not say about it. Here
    the same chain is written out as five conditions that cannot both hold,
    which is what lets a count be a sum rather than a string comparison.

    :param variable: The variable being compared.
    :return: An expression per :data:`STATUSES` key, none of them ever null.
    """
    existing = original_flag(variable)
    bad_existing = _is_one_of(existing, variable.bad_flag_values)
    bad_computed = (computed_flag(variable) > FLAG_GOOD).fill_null(False)
    # A flag that would not parse is null by now; one that parsed but says
    # nothing about the observation is caught by the value list.
    says_nothing = existing.is_null() | _is_one_of(
        existing, variable.missing_flag_values
    )
    neither = ~bad_existing & ~bad_computed

    return {
        AGREE_BAD: bad_existing & bad_computed,
        ORIGINAL_ONLY: bad_existing & ~bad_computed,
        AIQCLIB_ONLY: ~bad_existing & bad_computed,
        NO_INPUT_FLAG: neither & says_nothing,
        AGREE_GOOD: neither & ~says_nothing,
    }


def status_expression(variable: VariableSpec) -> pl.Expr:
    """
    The agreement category of each observation for one variable.

    :param variable: The variable being compared.
    :return: A ``Utf8`` expression yielding one of the :data:`STATUSES` keys.
    """
    predicates = status_predicates(variable)
    expression = pl.when(predicates[STATUSES[0]["key"]]).then(
        pl.lit(STATUSES[0]["key"])
    )
    for status in STATUSES[1:-1]:
        expression = expression.when(predicates[status["key"]]).then(
            pl.lit(status["key"])
        )
    return expression.otherwise(pl.lit(STATUSES[-1]["key"])).alias(
        variable.status_column
    )


def is_anomaly(variable: VariableSpec) -> pl.Expr:
    """
    Whether either source flags the observation.

    This is the trimming rule of the whole project: a profile reaches the site
    only when at least one of its observations satisfies this for at least one
    configured variable. Observations both sources call good are published
    too, but only as context inside a profile that already qualified.

    It reads the flag columns rather than ``{variable}_status``, so the
    trimming pass never has to build the status strings. Over a hundred
    million observations that is the difference between a few hundred
    megabytes and tens of gigabytes.

    :param variable: The variable being compared.
    :return: A boolean expression, never null.
    """
    predicates = status_predicates(variable)
    # Read off the ``flagged`` marker rather than listing the three keys
    # again, so the rule and what the site is told about it cannot disagree.
    keys = [status["key"] for status in STATUSES if status["flagged"]]
    expression = predicates[keys[0]]
    for key in keys[1:]:
        expression = expression | predicates[key]
    return expression


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
