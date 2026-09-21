# nrtqc-diff

A static dashboard showing where the near-real-time QC flags that ship with
ocean profile data disagree with the flags [`aiqclib`](https://github.com/AIQC-Hub/aiqclib)
computes for the same observations.

Two halves:

1. A small Python **build step** (`uv run nrtqc-diff build`) that reads
   `aiqclib` NRT QC output, keeps only the profiles where either source
   flagged something, and writes a handful of parquet files plus a catalog.
2. A **Quarto site** that queries those files in the browser with DuckDB WASM.
   There is no server and no backend: the rendered site is a folder of static
   files that can be served from GitHub Pages or any file host.

## What the site shows

The layout is a three-level drill-down, all on one page:

```
 sidebar                     main panel
 -----------------------     --------------------------------------------
 Baltic Sea                  1. pick a product  -> profile summary table
   CORA NRT                     one row per profile, counts per category
   CORA delayed mode
 Arctic Ocean                2. pick a profile  -> contingency tables plus
   CORA NRT                     temperature and salinity profile plots
```

The profile plots put the measurement on the x axis and pressure on the y
axis, increasing downwards, with every point coloured by which source flagged
it: both, the input only, `aiqclib` only, or neither. They pan and zoom.

## Quick start

```bash
uv sync                                   # Python environment
uv run python scripts/make_demo_data.py   # synthetic input, no real data needed
uv run nrtqc-diff build -c config/datasets.yaml
bash scripts/fetch_assets.sh              # DuckDB WASM and Plotly, once
bash scripts/serve.sh                     # render and open the site
```

`config/test_nrt.yaml` builds the real `aiqclib` batch from `data/` instead,
if you have it: 8 datasets, 330 million observations, about two minutes.

To use other data, point the `path` entries of `config/datasets.yaml` at your
own NRT QC output and rebuild. Nothing else changes.

## Requirements

- Python 3.12+ with [`uv`](https://docs.astral.sh/uv/)
- [Quarto](https://quarto.org) 1.9 or newer
- A browser with WebAssembly, which is every current browser

## Documentation

| Document | Covers |
| --- | --- |
| [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) | The published files and their schemas |
| [`docs/ETL.md`](docs/ETL.md) | Running and extending the build step |
| [`docs/SITE.md`](docs/SITE.md) | How the site is put together |
| [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) | Dev setup, tests, git workflow |
| [`docs/RELEASING.md`](docs/RELEASING.md) | Versioning and deployment |

## License

MIT. See [`LICENSE`](LICENSE).
