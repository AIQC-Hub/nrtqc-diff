# nrtqc-diff

A static dashboard showing where the near-real-time QC flags that ship with
ocean profile data disagree with the flags [`nrtqclib`](https://nrtqclib.readthedocs.io/en/latest/)
computes for the same observations.

Two halves:

1. A small Python **build step** (`uv run nrtqc-diff build`) that reads
   `nrtqclib` NRT QC output, keeps only the profiles where either source
   flagged something, and writes a handful of parquet files plus a catalog.
2. A **Quarto site** that queries those files in the browser with DuckDB WASM.
   There is no server and no backend: the rendered site is a folder of static
   files that can be served from GitHub Pages or any file host.

The site's **Analyses** pages are made separately, where the data is:
`uv run nrtqc-diff analyse` writes small tables and `Rscript
analysis/run_all.R` draws figures from them with ggplot2, as SVG for the
site and PDF for a manuscript. See [`docs/ANALYSES.md`](docs/ANALYSES.md).

## What the site shows

**Regions and products** is the overview: one row per region and product, how
much of each source dataset survived the trimming, and how the two flag
sources line up across what did, with a rate per 1,000 observations so
products of very different sizes can be compared.

**Platforms** is one product at a time: one row per platform, with the same
counts and rate, and a click on a platform lists its profiles underneath it.
It is the way to find which platforms of a product the disagreements come
from.

**The dashboard** is the drill-down. The sidebar carries the whole selection,
region to product to profile, and two tabs show what is selected:

```
 sidebar                  Plots                   Tables
 --------------------     ------------------      --------------------------
 Baltic Sea               temperature and         every profile of the
   CORA NRT            26 salinity against        product, counts per
   CORA delayed mode   20 pressure, side by       category and variable
 Arctic Ocean              side, coloured by
   CORA NRT            21 which source flagged    the contingency tables for
                                                  the selected profile
 BAL_CORA_NRT04:5  43   4 under them, which
 BAL_CORA_NRT04:10 35   4 nrtqclib checks
 ...                      flagged this profile
```

The sidebar is shared, so switching tabs keeps the profile you were looking
at, and either tab can be read against the other.

**Group** splits the profiles on every page, mostly by data mode. "NRT only"
profiles have every observation in real time or adjusted in real time, so
both sets of flags come from automated tests. "Delayed mode" profiles have
at least one delayed mode observation, or temperature or salinity flags
that look like an operator's: most of the variable's observations flagged
by the input and not by `nrtqclib`, which an automated test rarely does.
Their input flags are likely to be an operator's decisions, and CORA is
filed there whole, since its flags come from its own delayed mode QC:
comparing those with `nrtqclib` asks whether the automated tests anticipate
what an expert decided, which is a different question. "Data mode unknown"
holds the profiles whose files do not say. "On land" holds the profiles
the bathymetry puts at or above sea level, whatever their data mode, since
their disagreements are mostly about the position. A page offers a group
only when a product has profiles in it. NRT only is the default, and the
summary page puts the groups side by side.

The profile plots put the measurement on the x axis and pressure on the y
axis, increasing downwards, with every point coloured by which source flagged
it: both, the input only, `nrtqclib` only, or neither. They pan and zoom, and
stop at the edge of the profile's own data rather than drifting off into
empty space.

## Quick start

```bash
uv sync                                   # Python environment
uv run python scripts/make_demo_data.py   # synthetic input, no real data needed
uv run nrtqc-diff build -c config/datasets.yaml
bash scripts/fetch_assets.sh              # DuckDB WASM and Plotly, once
bash scripts/serve.sh                     # render and open the site
```

`config/test_nrt.yaml` builds the real `nrtqclib` batch from `data/` instead:
8 datasets, 330 million observations, about three minutes. Those inputs are
2.2 GB and are not in git. The deployed site is rendered from what this build
writes rather than rebuilt in CI: `scripts/data_release.py` publishes
`site/data/` to a release, and the workflow fetches it.

To use other data, point the `path` entries of `config/datasets.yaml` at your
own NRT QC output and rebuild. Nothing else changes.

## Requirements

- Python 3.12+ with [`uv`](https://docs.astral.sh/uv/)
- [Quarto](https://quarto.org) 1.9 or newer
- A browser with WebAssembly, which is every current browser
- For the Analyses figures only: R 4.3+ with the packages in
  `analysis/DESCRIPTION`

## Documentation

| Document | Covers |
| --- | --- |
| [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) | The published files and their schemas |
| [`docs/ETL.md`](docs/ETL.md) | Running and extending the build step |
| [`docs/ANALYSES.md`](docs/ANALYSES.md) | The Analyses pages: tables in Python, figures in R |
| [`docs/SITE.md`](docs/SITE.md) | How the site is put together |
| [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) | Dev setup, tests, git workflow |
| [`docs/RELEASING.md`](docs/RELEASING.md) | Versioning and deployment |

## License

MIT. See [`LICENSE`](LICENSE).
