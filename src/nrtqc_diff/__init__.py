"""
Build web-ready comparisons of existing NRT QC flags against ``aiqclib`` ones.

The package turns the parquet written by ``aiqclib``'s NRT QC module into the
small set of files the Quarto site queries in the browser. See
``docs/DATA_MODEL.md`` for what those files contain and ``docs/ETL.md`` for
how to run the build.
"""

from nrtqc_diff.build import build_site_data
from nrtqc_diff.config import BuildConfig, DatasetSpec, VariableSpec, read_config

__version__ = "0.1.0"

__all__ = [
    "BuildConfig",
    "DatasetSpec",
    "VariableSpec",
    "build_site_data",
    "read_config",
]
