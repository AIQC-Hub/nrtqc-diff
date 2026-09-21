"""The agreement categories and the item column discovery rule."""

import polars as pl
import pytest

from nrtqc_diff.config import VariableSpec
from nrtqc_diff.flags import (
    AGREE_BAD,
    AGREE_GOOD,
    AIQCLIB_ONLY,
    NO_INPUT_FLAG,
    ORIGINAL_ONLY,
    item_columns,
    status_expression,
)


class TestStatusExpression:
    """
    The five-way split, over the flags a real input actually carries.

    Copernicus NRT files write the flag as a string and use the whole scheme,
    so these cases are ``'0'`` through ``'9'`` and the empty string rather
    than the two values the demo generator emits.
    """

    @staticmethod
    def status(flag, computed=1, **overrides) -> str:
        """
        The category of one observation.

        :param flag: The input's flag, as it appears in the file.
        :param computed: The flag aiqclib computed.
        :return: The agreement category.
        """
        variable = VariableSpec(
            "temp", "Temperature", "temp_qc", "temp_nrt_flag", **overrides
        )
        frame = pl.DataFrame(
            {"temp_qc": [flag], "temp_nrt_flag": [computed]},
            schema={"temp_qc": pl.Utf8, "temp_nrt_flag": pl.Int64},
        )
        return frame.select(status_expression(variable)).item()

    @pytest.mark.parametrize(
        "flag,computed,expected",
        [
            ("1", 1, AGREE_GOOD),
            ("4", 4, AGREE_BAD),
            ("4", 1, ORIGINAL_ONLY),
            ("1", 4, AIQCLIB_ONLY),
            # 2 (probably good), 5 (value changed) and 8 (interpolated) are
            # all judgements that the observation is usable.
            ("2", 1, AGREE_GOOD),
            ("5", 1, AGREE_GOOD),
            ("8", 1, AGREE_GOOD),
        ],
    )
    def test_a_flag_that_carries_a_judgement(self, flag, computed, expected):
        assert self.status(flag, computed) == expected

    @pytest.mark.parametrize("flag", ["0", "9", "", None, "not a number"])
    def test_a_flag_that_carries_no_judgement_is_not_agreement(self, flag):
        """
        Nothing unreadable or unchecked may be scored as the sources agreeing.

        0 means no QC was performed and 9 means the value is missing; neither
        says the observation is good. Before ``missing_flag_values`` existed
        both landed in ``agree_good``, which overstated agreement.
        """
        assert self.status(flag) == NO_INPUT_FLAG

    @pytest.mark.parametrize("flag", ["0", "9", "", None])
    def test_a_computed_flag_still_wins(self, flag):
        """The point of the site is what aiqclib found, so it stays visible."""
        assert self.status(flag, computed=4) == AIQCLIB_ONLY

    def test_an_empty_value_list_still_covers_every_row(self):
        """
        ``_is_one_of`` is anchored to the column, not written as a literal.

        A bare literal would collapse to one row and the per-profile counts
        would stop summing to the observation count.
        """
        variable = VariableSpec(
            "temp", "Temperature", "temp_qc", "temp_nrt_flag", missing_flag_values=[]
        )
        frame = pl.DataFrame(
            {"temp_qc": ["1", "9", None], "temp_nrt_flag": [1, 1, 1]},
            schema={"temp_qc": pl.Utf8, "temp_nrt_flag": pl.Int64},
        )
        statuses = frame.select(status_expression(variable))
        assert statuses.height == 3
        assert statuses.to_series().to_list() == [
            AGREE_GOOD,
            AGREE_GOOD,
            NO_INPUT_FLAG,
        ]

    def test_the_missing_values_are_configurable(self):
        """A dataset that uses 9 as a real judgement can say so."""
        assert self.status("9", missing_flag_values=[]) == AGREE_GOOD
        assert self.status("9", missing_flag_values=[], bad_flag_values=[9]) == (
            ORIGINAL_ONLY
        )


def test_per_variable_and_profile_items_are_both_found():
    columns = [
        "platform_code",
        "temp",
        "temp_qc",
        "temp_nrt_flag",
        "temp_qc_spike",
        "qc_impossible_date",
    ]
    assert item_columns(columns, ["temp"]) == ["temp_qc_spike", "qc_impossible_date"]


def test_the_variable_flag_itself_is_not_an_item():
    """``temp_qc`` is the input's flag, not a per-item column."""
    assert item_columns(["temp_qc", "temp_qc_dm"], ["temp"]) == ["temp_qc_dm"]


def test_columns_of_other_variables_are_included():
    columns = ["temp_qc_spike", "psal_qc_spike"]
    assert item_columns(columns, ["temp", "psal"]) == columns


def test_order_follows_the_input():
    columns = ["qc_b", "temp_qc_a"]
    assert item_columns(columns, ["temp"]) == ["qc_b", "temp_qc_a"]
