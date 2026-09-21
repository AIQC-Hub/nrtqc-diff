"""The build step: trimming, categories, counts and the catalog."""

import polars as pl
import pytest

from nrtqc_diff.build import build_site_data
from nrtqc_diff.config import BuildConfig, DatasetSpec, VariableSpec
from nrtqc_diff.flags import (
    AGREE_BAD,
    AGREE_GOOD,
    AIQCLIB_ONLY,
    NO_INPUT_FLAG,
    ORIGINAL_ONLY,
)


def _single_dataset_config(tmp_path, frame, **overrides) -> BuildConfig:
    """
    Write ``frame`` to a parquet and build a config publishing just it.

    :param tmp_path: The pytest temporary directory.
    :param frame: The NRT QC output to publish.
    :return: The configuration, writing into ``tmp_path / "out"``.
    """
    path = tmp_path / "input.parquet"
    frame.write_parquet(path)
    settings = {
        "title": "T",
        "data_dir": str(tmp_path / "out"),
        "variables": [
            VariableSpec(
                "temp",
                "Temperature",
                "temp_qc",
                "temp_nrt_flag",
                bad_flag_values=[3, 4],
            )
        ],
        "datasets": [DatasetSpec("one", "R", "P", "One", str(path))],
    }
    settings.update(overrides)
    return BuildConfig(**settings)


class TestTrimming:
    """Only profiles one of the two sources flagged reach the site."""

    def test_clean_profiles_are_dropped(self, built):
        _, profiles, _ = built
        assert "CLEAN" not in profiles["platform_code"].to_list()

    def test_a_profile_only_flagged_as_unchecked_is_dropped(self, built):
        """Flag 0 says nothing was checked, which is not a disagreement."""
        _, profiles, _ = built
        assert "NOQC" not in profiles["platform_code"].to_list()

    def test_flagged_profiles_are_kept(self, built):
        _, profiles, _ = built
        assert sorted(profiles["platform_code"].to_list()) == [
            "AGREE",
            "AIQC",
            "INPUT",
            "MISSING",
            "NOFLAG",
        ]

    def test_a_kept_profile_keeps_all_of_its_observations(self, built):
        _, _, observations = built
        counts = observations.group_by("platform_code").len()
        assert set(counts["len"].to_list()) == {3}


class TestStatusCategories:
    """Each observation lands in exactly one agreement category."""

    @pytest.mark.parametrize(
        "platform,observation_no,expected",
        [
            ("AGREE", 2, AGREE_BAD),
            ("AGREE", 1, AGREE_GOOD),
            ("INPUT", 1, ORIGINAL_ONLY),
            ("AIQC", 2, AIQCLIB_ONLY),
            ("NOFLAG", 1, NO_INPUT_FLAG),
            ("NOFLAG", 2, AIQCLIB_ONLY),
            ("MISSING", 1, NO_INPUT_FLAG),
            ("MISSING", 2, AIQCLIB_ONLY),
            ("MISSING", 3, AGREE_GOOD),
        ],
    )
    def test_category(self, built, platform, observation_no, expected):
        _, _, observations = built
        row = observations.filter(
            (pl.col("platform_code") == platform)
            & (pl.col("observation_no") == observation_no)
        )
        assert row["temp_status"].item() == expected

    def test_an_unreadable_input_flag_stays_visible(self, built):
        """A missing input flag is its own category, not a silent good."""
        _, _, observations = built
        noflag = observations.filter(pl.col("platform_code") == "NOFLAG")
        assert noflag["temp_status"].to_list().count(NO_INPUT_FLAG) == 1

    def test_a_computed_flag_survives_a_missing_input_flag(self, built):
        """Nothing aiqclib flagged may be hidden by the input saying nothing."""
        _, _, observations = built
        row = observations.filter(
            (pl.col("platform_code") == "NOFLAG") & (pl.col("observation_no") == 2)
        )
        assert row["temp_status"].item() == AIQCLIB_ONLY


class TestProfileCounts:
    """The summary table's numbers."""

    def test_disagreement_count(self, built):
        _, profiles, _ = built
        by_platform = dict(
            zip(profiles["platform_code"].to_list(), profiles["n_disagree"].to_list())
        )
        assert by_platform["INPUT"] == 1
        assert by_platform["AIQC"] == 1
        assert by_platform["AGREE"] == 0

    def test_category_counts_sum_to_the_observation_count(self, built):
        _, profiles, _ = built
        columns = [
            "temp_n_agree_bad",
            "temp_n_original_only",
            "temp_n_aiqclib_only",
            "temp_n_agree_good",
            "temp_n_no_input_flag",
        ]
        totals = profiles.select(pl.sum_horizontal(columns).alias("total"))
        assert totals["total"].to_list() == profiles["n_obs"].to_list()

    def test_profiles_are_sorted_worst_first(self, built):
        _, profiles, _ = built
        assert profiles["n_disagree"].to_list() == sorted(
            profiles["n_disagree"].to_list(), reverse=True
        )

    def test_profile_id_joins_the_two_files(self, built):
        _, profiles, observations = built
        assert set(profiles["profile_id"]) == set(observations["profile_id"])


class TestCatalog:
    """What the sidebar reads."""

    def test_tree_shape(self, built):
        catalog, _, _ = built
        assert [region["name"] for region in catalog["regions"]] == ["Region A"]
        products = catalog["regions"][0]["products"]
        assert [product["name"] for product in products] == ["Product A"]

    def test_dataset_entry_records_what_was_dropped(self, built):
        catalog, _, _ = built
        entry = catalog["regions"][0]["products"][0]["datasets"][0]
        assert entry["n_profiles"] == 5
        assert entry["n_profiles_total"] == 7
        assert entry["n_observations"] == 15
        assert entry["n_observations_total"] == 21
        assert entry["observations_file"] == "obs/one.parquet"

    def test_variables_and_statuses_are_published(self, built):
        catalog, _, _ = built
        assert catalog["variables"][0]["status_column"] == "temp_status"
        assert [status["key"] for status in catalog["statuses"]][:2] == [
            AGREE_BAD,
            ORIGINAL_ONLY,
        ]


class TestObservationFileLayout:
    """What the site's range requests depend on."""

    def test_observations_keep_the_input_order(self, built):
        """Sorting is what the build refuses to do, so the input must hold."""
        _, _, observations = built
        platforms = observations["platform_code"].to_list()
        assert platforms == sorted(platforms)

    def test_row_groups_follow_the_configured_size(self, tmp_path, nrt_qc_frame):
        """A profile costs one row group, so the size is worth pinning."""
        import pyarrow.parquet as pq

        config = _single_dataset_config(tmp_path, nrt_qc_frame, row_group_size=4)
        build_site_data(config)

        written = pq.ParquetFile(tmp_path / "out" / "obs" / "one.parquet").metadata
        assert written.num_rows == 15
        assert written.num_row_groups == 4


class TestInputValidation:
    """Failures the reader can act on."""

    def test_an_unordered_input_is_rejected(self, tmp_path, nrt_qc_frame):
        """The build copies rows out in input order, so it checks the order."""
        shuffled = nrt_qc_frame.sort("platform_code", descending=True)
        config = _single_dataset_config(tmp_path, shuffled)
        with pytest.raises(ValueError, match="platform_code"):
            build_site_data(config)

    def test_missing_file_names_the_dataset(self, tmp_path):
        config = BuildConfig(
            title="T",
            data_dir=str(tmp_path / "out"),
            variables=[VariableSpec("temp", "Temperature", "temp_qc", "temp_nrt_flag")],
            datasets=[
                DatasetSpec("one", "R", "P", "One", str(tmp_path / "absent.parquet"))
            ],
        )
        with pytest.raises(FileNotFoundError, match="one"):
            build_site_data(config)

    def test_missing_columns_are_reported_together(self, tmp_path):
        path = tmp_path / "input.parquet"
        pl.DataFrame(
            {
                "platform_code": ["A"],
                "profile_no": [1],
                "observation_no": [1],
                "pres": [1.0],
            }
        ).write_parquet(path)

        config = BuildConfig(
            title="T",
            data_dir=str(tmp_path / "out"),
            variables=[VariableSpec("temp", "Temperature", "temp_qc", "temp_nrt_flag")],
            datasets=[DatasetSpec("one", "R", "P", "One", str(path))],
        )
        with pytest.raises(ValueError) as error:
            build_site_data(config)
        message = str(error.value)
        assert "temp_qc" in message and "temp_nrt_flag" in message and "temp" in message
