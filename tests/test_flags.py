"""The item column discovery rule the site relies on."""

from nrtqc_diff.flags import item_columns


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
