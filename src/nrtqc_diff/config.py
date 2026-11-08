"""
Reading and validating the build configuration.

One YAML file describes everything the build needs: which variables carry
comparable flags, which values of the existing flag count as an anomaly, and
which datasets to publish under which region and product. Paths in the file
are resolved relative to the file itself, so a config can be moved with its
data and keep working.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

#: Flag values the IOC/Argo scheme treats as good.
DEFAULT_BAD_FLAG_VALUES: List[int] = [3, 4, 6, 7]

#: Flag values that carry no judgement: 0 is "no QC performed" and 9 is
#: "missing value". Neither says the observation is good, so neither may be
#: scored as agreement; both mean there is nothing to compare.
DEFAULT_MISSING_FLAG_VALUES: List[int] = [0, 9]

#: Rows per parquet row group in the observation files.
#:
#: The site reads those files over HTTP range requests, so opening one profile
#: costs the file's footer plus the one row group holding it. The footer grows
#: with the number of row groups (about 3 KB each for a 32-column file) while
#: a row group costs about 5 bytes per observation, so the total is smallest
#: in the middle: near 120,000 rows for the largest dataset here. Smaller row
#: groups make the footer dominate rather than helping.
DEFAULT_ROW_GROUP_SIZE: int = 100_000

#: What a dataset id may contain. The id becomes an SQL view name and a file
#: name in the browser, so it is kept to the characters both accept without
#: quoting; ``site/assets/js/db.js`` enforces the same rule on its side.
DATASET_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")

#: The data mode values that mark an observation as delayed mode, and so as
#: likely to have been edited by an operator: ctddump writes ``D``. ``R`` (real
#: time) and ``A`` (adjusted in real time) are automatic and count as NRT, and
#: an empty cell says nothing either way.
DEFAULT_DELAYED_VALUES: List[str] = ["D"]

#: The data mode groups a profile can be published under. They are the keys of
#: ``DATA_MODES`` in :mod:`nrtqc_diff.data_mode`, repeated here so a dataset's
#: ``data_mode`` override can be checked when the configuration is read; that
#: module imports this one, not the other way round, and a test pins the two
#: lists together.
DATA_MODE_KEYS: List[str] = ["nrt", "delayed", "unknown"]

#: The sea floor depth bands of the land sensitivity table, in metres above
#: sea level: a profile counts at a margin when its position is at least that
#: high. 0 is the rule itself, the one nrtqclib's position on land test
#: applies; the others show how many of those profiles sit just at the
#: coastline, where a grid cell of the bathymetry straddles it.
DEFAULT_LAND_MARGINS_M: List[float] = [0.0, 5.0, 10.0, 20.0, 50.0, 100.0]

#: The share of a variable's observations that only the input flags, at and
#: above which an NRT profile is estimated to carry delayed mode flags: a
#: clear majority, since an automated test can plausibly reject half a
#: profile. Most of the profiles it catches are flagged at exactly 100%, so
#: they do not depend on it; the sensitivity table shows how the rest move.
DEFAULT_ESTIMATED_MIN_SHARE: float = 0.6

#: Fewer observations with a usable input flag than this, and a profile's
#: share is not computed. On a short profile a few flagged values make a
#: large share: with 5, three of them made exactly 60%, and many short
#: profiles sat on the threshold.
DEFAULT_ESTIMATED_MIN_OBSERVATIONS: int = 10

#: The thresholds of the estimated delayed mode sensitivity table.
DEFAULT_ESTIMATED_SENSITIVITY: List[float] = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]

#: The sea floor column seastamp adds: GEBCO elevation, negative under water.
DEFAULT_BATHYMETRY_COLUMN: str = "bathymetry"

#: Columns every dataset must carry, whatever else it holds.
REQUIRED_COLUMNS: List[str] = [
    "platform_code",
    "profile_no",
    "observation_no",
    "pres",
]


@dataclass(frozen=True)
class VariableSpec:
    """
    One variable whose two flag columns are compared.

    :ivar name: The measurement column, e.g. ``temp``.
    :ivar label: The name shown in the site, e.g. ``Temperature``.
    :ivar unit: The unit shown on the plot axis.
    :ivar flag: The existing flag column from the input, e.g. ``temp_qc``.
    :ivar nrt_flag: The flag ``nrtqclib`` computed, e.g. ``temp_nrt_flag``.
    :ivar bad_flag_values: Values of :attr:`flag` that mean "anomaly".
    :ivar missing_flag_values: Values of :attr:`flag` that carry no judgement,
                               such as 0 (no QC performed) and 9 (missing
                               value). They are reported as "no input flag"
                               rather than being counted as agreement.
    """

    name: str
    label: str
    flag: str
    nrt_flag: str
    unit: str = ""
    bad_flag_values: List[int] = field(
        default_factory=lambda: list(DEFAULT_BAD_FLAG_VALUES)
    )
    missing_flag_values: List[int] = field(
        default_factory=lambda: list(DEFAULT_MISSING_FLAG_VALUES)
    )

    @property
    def status_column(self) -> str:
        """The per-observation agreement category column written for this variable."""
        return f"{self.name}_status"


@dataclass(frozen=True)
class DataModeSpec:
    """
    Where the observation-level data mode lives, and which values mean delayed.

    A profile with even one delayed mode observation, in any of
    :attr:`columns`, is published as ``delayed`` rather than ``nrt``: its
    input flags are likely to be an operator's decisions rather than the
    automated NRT tests', which is a different comparison.

    :ivar columns: The data mode columns, e.g. ``temp_dm``, ``psal_dm`` and
                   ``pres_dm``. Pressure belongs here as well: a profile whose
                   pressure was revised in delayed mode was touched by one.
    :ivar delayed_values: The values of those columns that mean delayed mode.
    :ivar estimate: Whether to estimate delayed mode from the flags, for
                    profiles whose data mode columns do not say it.
    :ivar estimated_min_share: See :data:`DEFAULT_ESTIMATED_MIN_SHARE`.
    :ivar estimated_min_observations: See
                                      :data:`DEFAULT_ESTIMATED_MIN_OBSERVATIONS`.
    :ivar estimated_groups: The data mode groups whose profiles can be
                            estimated as delayed mode.
    """

    columns: List[str]
    delayed_values: List[str] = field(
        default_factory=lambda: list(DEFAULT_DELAYED_VALUES)
    )
    estimate: bool = True
    estimated_min_share: float = DEFAULT_ESTIMATED_MIN_SHARE
    estimated_min_observations: int = DEFAULT_ESTIMATED_MIN_OBSERVATIONS
    estimated_groups: List[str] = field(default_factory=lambda: ["nrt", "unknown"])


@dataclass(frozen=True)
class DatasetSpec:
    """
    One published dataset: a single NRT QC output file plus where it belongs.

    :ivar id: The identifier used in file names and site URLs. Keep it short
              and filesystem-safe; it is not shown to the reader.
    :ivar region: The top level of the site's tree, e.g. ``Baltic Sea``.
    :ivar product: The second level, e.g. ``CORA NRT``.
    :ivar label: The name shown for the dataset itself.
    :ivar path: The ``nrtqclib`` NRT QC output parquet.
    :ivar description: One sentence shown beside the dataset in the site.
    :ivar data_mode: A data mode group to file every profile of the dataset
                     under, whatever its data mode columns say, or None to
                     classify each profile by those columns. For a product
                     whose input flags come from its own delayed mode QC:
                     CORA's ``DATA_MODE`` describes the original observation,
                     not the reprocessing that set CORA's flags, so a CORA
                     profile marked ``R`` still carries delayed mode flags.
    :ivar source: The file stem the ctddump and seastamp reports use for the
                  dataset, such as ``nrt_ar_ar``, or None when it has none.
                  Only the analyses read it.
    """

    id: str
    region: str
    product: str
    label: str
    path: str
    description: str = ""
    data_mode: Optional[str] = None
    source: Optional[str] = None


@dataclass(frozen=True)
class AnalysesSpec:
    """
    What ``nrtqc-diff analyse`` reads besides the datasets, and its rules.

    :ivar out_dir: Where the analysis tables are written; the figures go
                   beside them. Normally ``site/data/analyses``, so that a
                   data release carries them with the rest.
    :ivar ctddump_reports: The ctddump ``report`` directory, or None.
    :ivar ctddump_headers: The ctddump ``header`` directory, or None.
    :ivar ctddump_dups: The directory of ctddump's duplicate lists
                        (``<stem>.dups.tsv``, written by ``markdup``), or None.
    :ivar seastamp_reports: The seastamp ``report/stamp`` directory, or None.
    :ivar bathymetry_column: The column holding the sea floor depth at the
                             position of each observation.
    :ivar positive_depth: Whether that column is positive under water. The
                          seastamp default, GEBCO elevation, is negative
                          under water, so the default is False. The same
                          parameter as nrtqclib's position on land test.
    :ivar land_margins_m: See :data:`DEFAULT_LAND_MARGINS_M`.
    :ivar estimated_sensitivity: See :data:`DEFAULT_ESTIMATED_SENSITIVITY`.
                                 The rule itself is in :class:`DataModeSpec`,
                                 which the build applies too.
    """

    out_dir: str
    ctddump_reports: Optional[str] = None
    ctddump_headers: Optional[str] = None
    ctddump_dups: Optional[str] = None
    seastamp_reports: Optional[str] = None
    bathymetry_column: str = DEFAULT_BATHYMETRY_COLUMN
    positive_depth: bool = False
    land_margins_m: List[float] = field(
        default_factory=lambda: list(DEFAULT_LAND_MARGINS_M)
    )
    estimated_sensitivity: List[float] = field(
        default_factory=lambda: list(DEFAULT_ESTIMATED_SENSITIVITY)
    )


@dataclass(frozen=True)
class BuildConfig:
    """
    Everything one build run needs.

    :ivar title: The site title.
    :ivar data_dir: Where the published files are written.
    :ivar variables: The variables to compare.
    :ivar data_mode: Where the data mode is and what counts as delayed.
    :ivar datasets: The datasets to publish.
    :ivar row_group_size: Rows per parquet row group in the observation files.
                          See :data:`DEFAULT_ROW_GROUP_SIZE`.
    :ivar analyses: The ``analyses`` section, or None when the configuration
                    has none; only ``nrtqc-diff analyse`` needs it.
    """

    title: str
    data_dir: str
    variables: List[VariableSpec]
    data_mode: DataModeSpec
    datasets: List[DatasetSpec]
    row_group_size: int = DEFAULT_ROW_GROUP_SIZE
    analyses: Optional[AnalysesSpec] = None

    def variable(self, name: str) -> VariableSpec:
        """
        Look up a variable by name.

        :raises KeyError: If no variable of that name is configured.
        """
        for variable in self.variables:
            if variable.name == name:
                return variable
        raise KeyError(f"No variable named '{name}' in the configuration.")


def _require(mapping: Dict[str, Any], key: str, context: str) -> Any:
    """
    Fetch a required key, naming what was being read when it is missing.

    :raises ValueError: If the key is absent.
    """
    if key not in mapping:
        raise ValueError(f"{context} is missing the required key '{key}'.")
    return mapping[key]


def read_config(file_name: str) -> BuildConfig:
    """
    Read and validate a build configuration.

    Relative dataset paths are resolved against the directory holding the
    config file, and ``~`` is expanded, so a config travels with its data.

    :param file_name: The YAML file to read.
    :return: The parsed configuration.
    :raises ValueError: If a required key is missing or a section is empty.
    """
    file_name = os.path.abspath(os.path.expanduser(file_name))
    with open(file_name, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    base_dir = os.path.dirname(file_name)
    site = raw.get("site", {})

    variables = [
        VariableSpec(
            name=_require(entry, "name", "A 'variables' entry"),
            label=entry.get("label", entry.get("name", "")),
            unit=entry.get("unit", ""),
            flag=_require(entry, "flag", f"Variable '{entry.get('name')}'"),
            nrt_flag=_require(entry, "nrt_flag", f"Variable '{entry.get('name')}'"),
            bad_flag_values=[
                int(value)
                for value in entry.get("bad_flag_values", DEFAULT_BAD_FLAG_VALUES)
            ],
            missing_flag_values=[
                int(value)
                for value in entry.get(
                    "missing_flag_values", DEFAULT_MISSING_FLAG_VALUES
                )
            ],
        )
        for entry in raw.get("variables", [])
    ]
    if not variables:
        raise ValueError("The configuration lists no variables to compare.")

    for variable in variables:
        overlap = sorted(
            set(variable.bad_flag_values) & set(variable.missing_flag_values)
        )
        if overlap:
            raise ValueError(
                f"Variable '{variable.name}' lists "
                f"{', '.join(str(value) for value in overlap)} as both a bad "
                "and a missing flag value; a flag cannot be both."
            )

    data_mode = _data_mode(raw.get("data_mode"))

    datasets = [
        DatasetSpec(
            id=_require(entry, "id", "A 'datasets' entry"),
            region=_require(entry, "region", f"Dataset '{entry.get('id')}'"),
            product=_require(entry, "product", f"Dataset '{entry.get('id')}'"),
            label=entry.get("label", entry.get("id", "")),
            path=_resolve(
                _require(entry, "path", f"Dataset '{entry.get('id')}'"), base_dir
            ),
            description=entry.get("description", ""),
            data_mode=entry.get("data_mode"),
            source=entry.get("source"),
        )
        for entry in raw.get("datasets", [])
    ]
    if not datasets:
        raise ValueError("The configuration lists no datasets to publish.")

    seen = {d.id for d in datasets}
    if len(seen) != len(datasets):
        raise ValueError("Dataset ids must be unique; found a duplicate.")

    bad_ids = [d.id for d in datasets if not DATASET_ID_PATTERN.match(d.id)]
    if bad_ids:
        raise ValueError(
            "Dataset ids may hold lower case letters, digits and underscores "
            f"only; rejected: {', '.join(bad_ids)}."
        )

    bad_modes = [
        f"{d.id} ({d.data_mode})"
        for d in datasets
        if d.data_mode is not None and d.data_mode not in DATA_MODE_KEYS
    ]
    if bad_modes:
        raise ValueError(
            f"A dataset's data_mode must be one of {', '.join(DATA_MODE_KEYS)}; "
            f"rejected: {', '.join(bad_modes)}."
        )

    row_group_size = int(site.get("row_group_size", DEFAULT_ROW_GROUP_SIZE))
    if row_group_size < 1:
        raise ValueError("site.row_group_size must be a positive number of rows.")

    return BuildConfig(
        title=site.get("title", "NRT QC flag differences"),
        data_dir=_resolve(site.get("data_dir", "site/data"), base_dir),
        variables=variables,
        data_mode=data_mode,
        datasets=datasets,
        row_group_size=row_group_size,
        analyses=_analyses(raw.get("analyses"), base_dir),
    )


def _data_mode(raw: Any) -> DataModeSpec:
    """
    Read the ``data_mode`` section.

    Required rather than defaulted: a build that silently called every
    profile NRT, because the section was left out, would mix delayed mode
    flags into the NRT comparison, which is the thing the split exists to
    prevent.

    :param raw: The section as parsed from YAML.
    :return: The parsed section.
    :raises ValueError: If the section or its columns are missing.
    """
    if not isinstance(raw, dict):
        raise ValueError(
            "The configuration has no 'data_mode' section naming the data mode "
            "columns, such as temp_dm, psal_dm and pres_dm."
        )
    columns = [str(column) for column in raw.get("columns") or []]
    if not columns:
        raise ValueError("data_mode.columns must name at least one column.")
    delayed = [
        str(value) for value in raw.get("delayed_values", DEFAULT_DELAYED_VALUES)
    ]
    if not delayed:
        raise ValueError("data_mode.delayed_values must list at least one value.")

    estimated = raw.get("estimated", {})
    if estimated is False:
        return DataModeSpec(columns=columns, delayed_values=delayed, estimate=False)
    if not isinstance(estimated, dict):
        raise ValueError("data_mode.estimated must be a mapping, or false.")
    spec = DataModeSpec(
        columns=columns,
        delayed_values=delayed,
        estimated_min_share=float(
            estimated.get("min_share", DEFAULT_ESTIMATED_MIN_SHARE)
        ),
        estimated_min_observations=int(
            estimated.get("min_observations", DEFAULT_ESTIMATED_MIN_OBSERVATIONS)
        ),
        estimated_groups=[
            str(value) for value in estimated.get("groups", ["nrt", "unknown"])
        ],
    )
    if not 0.0 < spec.estimated_min_share <= 1.0:
        raise ValueError("data_mode.estimated.min_share must be above 0 and at most 1.")
    if spec.estimated_min_observations < 1:
        raise ValueError("data_mode.estimated.min_observations must be 1 or more.")
    bad_groups = [g for g in spec.estimated_groups if g not in DATA_MODE_KEYS]
    if bad_groups:
        raise ValueError(
            f"data_mode.estimated.groups must be among "
            f"{', '.join(DATA_MODE_KEYS)}; rejected: {', '.join(bad_groups)}."
        )
    return spec


def _analyses(raw: Any, base_dir: str) -> Optional[AnalysesSpec]:
    """
    Read the optional ``analyses`` section.

    Its directories are resolved through symbolic links before ``..`` is
    applied, unlike the dataset paths. The report directories sit beside the
    nrtqclib batch rather than in it, and the batch is reached through the
    ``data`` symbolic link, so ``../data/../ctddump`` has to mean the folder
    next to where the link points, not the repository root that a purely
    textual ``..`` would make of it.

    :param raw: The section as parsed from YAML, or None.
    :param base_dir: The directory holding the configuration file.
    :return: The parsed section, or None when there is none.
    :raises ValueError: If a value is out of range.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("The 'analyses' section must be a mapping.")

    def directory(key: str) -> Optional[str]:
        value = raw.get(key)
        if value is None:
            return None
        return os.path.realpath(_resolve(str(value), base_dir, textual=False))

    estimated = raw.get("estimated_delayed") or {}
    moved = [k for k in ("min_share", "min_observations", "groups") if k in estimated]
    if moved:
        raise ValueError(
            f"analyses.estimated_delayed.{moved[0]} has moved to "
            f"data_mode.estimated.{moved[0]}: the build applies the rule too, "
            "so it lives with the rest of the data mode settings."
        )
    land = raw.get("land") or {}
    spec = AnalysesSpec(
        out_dir=_resolve(
            str(_require(raw, "out_dir", "The 'analyses' section")), base_dir
        ),
        ctddump_reports=directory("ctddump_reports"),
        ctddump_headers=directory("ctddump_headers"),
        ctddump_dups=directory("ctddump_dups"),
        seastamp_reports=directory("seastamp_reports"),
        bathymetry_column=str(land.get("column", DEFAULT_BATHYMETRY_COLUMN)),
        positive_depth=bool(land.get("positive_depth", False)),
        land_margins_m=[
            float(value) for value in land.get("margins_m", DEFAULT_LAND_MARGINS_M)
        ],
        estimated_sensitivity=[
            float(value)
            for value in estimated.get("sensitivity", DEFAULT_ESTIMATED_SENSITIVITY)
        ],
    )
    if any(not 0.0 < share <= 1.0 for share in spec.estimated_sensitivity):
        raise ValueError(
            "analyses.estimated_delayed.sensitivity values must be above 0 and "
            "at most 1."
        )
    return spec


def _resolve(path: str, base_dir: str, textual: bool = True) -> str:
    """
    Expand ``~`` and make a relative path absolute against the config's folder.

    :param path: The path as written in the configuration.
    :param base_dir: The directory holding the configuration file.
    :param textual: Whether to normalise ``..`` as text. Pass False to leave
                    it for :func:`os.path.realpath`, which applies it after
                    following symbolic links.
    :return: An absolute path, or with ``textual`` False, one that
             ``realpath`` will make absolute.
    """
    path = os.path.expanduser(path)
    if os.path.isabs(path):
        return path
    joined = os.path.join(base_dir, path)
    return os.path.abspath(joined) if textual else joined
