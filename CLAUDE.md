# CLAUDE.md

`nrtqc-diff`: a static Quarto dashboard comparing the NRT QC flags already
present in ocean profile data with the flags `nrtqclib` computes for the same
observations. Two halves: a Python **build step** that trims `nrtqclib` NRT QC
output into small web-ready parquet files, and a **Quarto site** that queries
those files in the browser with DuckDB WASM (no server, no backend).

See `README.md` for the workflow and `docs/` for the details:

| Document | Covers |
| --- | --- |
| `docs/DATA_MODEL.md` | The published files, their schemas, the trimming rule |
| `docs/ETL.md` | Running and extending the build step |
| `docs/ANALYSES.md` | The Analyses pages: `nrtqc-diff analyse` tables, R figures |
| `docs/REGIONAL_QC.md` | The NRT QC tests each regional product runs; not the global ones |
| `docs/SITE.md` | Quarto/OJS conventions, page contracts, the data access seam |
| `docs/CONTRIBUTING.md` | Dev setup, tests, lint, git workflow |
| `docs/RELEASING.md` | Version bumps, data releases, deploying the site |
| `docs/NC2PARQUET.md` | The NRT QC tests of `nc2parquet` against `nrtqclib`'s (repository only, never a site page) |

## Environment & dependencies

- Use **`uv`** for the Python side: `uv sync` installs deps, `uv run <cmd>`
  runs in the env. No separate install step is needed.
- The site needs **Quarto 1.9+** and nothing else at render time. Browser
  libraries are vendored by `bash scripts/fetch_assets.sh` (run once) and are
  **not** in git.
- There is no Node build step. The JavaScript under `site/assets/js/` is
  plain ES modules loaded directly by the browser.

## Build and preview

```bash
uv run nrtqc-diff build --config config/datasets.yaml   # writes site/data/
bash scripts/fetch_assets.sh                            # writes site/libs/
bash scripts/serve.sh                                   # renders and previews
```

## Analyses (tables in Python, figures in R)

```bash
uv run nrtqc-diff analyse -c config/test_nrt.yaml   # site/data/analyses/tables
Rscript analysis/run_all.R                          # site/data/analyses/figures
```

- **R never reads observations.** Anything that touches the NRT QC output is
  computed in `src/nrtqc_diff/analyse.py` and written as a small CSV table;
  the R scripts read only those. Large data in R/ggplot2 has crashed the
  machine before.
- Polars passes must stay streaming: no cast before `first()` inside an
  aggregation, and no `collect_all` over large scans. Check memory on the
  real batch before committing a new pass (the full run peaks near 3.8 GB).

## Tests, lint, format

- `uv run pytest` (the suite is small; run all of it).
- `uv run ruff check src tests` / `uv run ruff format src tests`.
- The site has no test runner. Check it by rendering: `quarto render site`
  must exit 0, then click through the three levels described in `docs/SITE.md`.

## Writing style

- **Never use em dashes (`U+2014`) or en dashes (`U+2013`)** anywhere in the
  repository: docs, README, CHANGELOG, docstrings, comments, commit messages.
  Use a colon, comma, semicolon, parentheses, or two sentences instead.
- This covers ranges and minus signs too: write `1-4`, `47-65%`, `-90` with a
  plain hyphen.
- Check before committing:
  `git ls-files -z | xargs -0 grep -lP '[\x{2013}\x{2014}]'` must print nothing.

## Git (gitflow)

- Follows **gitflow**: `main` (releases), `develop` (integration), plus
  `feature/*`, `release/*`, `hotfix/*` branches.
- Branch off `develop` for features (`feature/<name>`); branch off `main` for
  hotfixes.
- Do **not** commit directly to `main` or `develop`; merge via the appropriate
  branch.

## Data, never in git

`site/data/`, `site/libs/` and `site/_site/` are all generated and are in
`.gitignore`. Nothing derived from a source dataset belongs in the repository:
the build step reproduces it from the `nrtqclib` output named in the config.
