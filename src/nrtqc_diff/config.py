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
from typing import Any, Dict, List

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
    :ivar nrt_flag: The flag ``aiqclib`` computed, e.g. ``temp_nrt_flag``.
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
class DatasetSpec:
    """
    One published dataset: a single NRT QC output file plus where it belongs.

    :ivar id: The identifier used in file names and site URLs. Keep it short
              and filesystem-safe; it is not shown to the reader.
    :ivar region: The top level of the site's tree, e.g. ``Baltic Sea``.
    :ivar product: The second level, e.g. ``CORA NRT``.
    :ivar label: The name shown for the dataset itself.
    :ivar path: The ``aiqclib`` NRT QC output parquet.
    :ivar description: One sentence shown beside the dataset in the site.
    """

    id: str
    region: str
    product: str
    label: str
    path: str
    description: str = ""


@dataclass(frozen=True)
class BuildConfig:
    """
    Everything one build run needs.

    :ivar title: The site title.
    :ivar data_dir: Where the published files are written.
    :ivar variables: The variables to compare.
    :ivar datasets: The datasets to publish.
    :ivar row_group_size: Rows per parquet row group in the observation files.
                          See :data:`DEFAULT_ROW_GROUP_SIZE`.
    """

    title: str
    data_dir: str
    variables: List[VariableSpec]
    datasets: List[DatasetSpec]
    row_group_size: int = DEFAULT_ROW_GROUP_SIZE

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

    row_group_size = int(site.get("row_group_size", DEFAULT_ROW_GROUP_SIZE))
    if row_group_size < 1:
        raise ValueError("site.row_group_size must be a positive number of rows.")

    return BuildConfig(
        title=site.get("title", "NRT QC flag differences"),
        data_dir=_resolve(site.get("data_dir", "site/data"), base_dir),
        variables=variables,
        datasets=datasets,
        row_group_size=row_group_size,
    )


def _resolve(path: str, base_dir: str) -> str:
    """
    Expand ``~`` and make a relative path absolute against the config's folder.

    :param path: The path as written in the configuration.
    :param base_dir: The directory holding the configuration file.
    :return: An absolute path.
    """
    path = os.path.expanduser(path)
    if os.path.isabs(path):
        return path
    return os.path.abspath(os.path.join(base_dir, path))
