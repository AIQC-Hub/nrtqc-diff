# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- A **Regions and products** page, between the dashboard and About. One row per
  region and product: how much of each source dataset survived the trimming,
  how the two flag sources line up across what did, and a rate per 1,000
  published observations so products of very different sizes can be compared.
- A `flagged` marker on each agreement category in `catalog.json`, saying
  whether at least one source flagged the observation. `is_anomaly` is now
  built from it, so the rule that decides which profiles are published and
  what the site is told about that rule cannot drift apart.
- `config/test_nrt.yaml`, building the real `aiqclib` `test_nrt` batch: 8
  datasets arranged as a region by product tree, 330 million observations in,
  430 MB out in about two minutes.
- `missing_flag_values` per variable, defaulting to `[0, 9]`. Values of the
  input flag that carry no judgement (no QC performed, missing value) are now
  reported as `no_input_flag` instead of being counted as the two sources
  agreeing the observation is good.
- `site.row_group_size`, the rows per parquet row group in the observation
  files, which is what one profile lookup costs the reader.
- `scripts/serve_static.py` and `scripts/serve.sh --static`, serving a
  rendered site with HTTP range requests. `quarto preview` does not answer
  them, so it cannot show what the deployed site really fetches.

### Changed

- The **Product totals** panel is gone from the dashboard. It answered a
  question about the selected product only, which the new page now answers for
  every product at once, side by side.
- The demo inputs are written to `demo-data/` rather than `data/demo/`, and
  the generator sorts them by platform. `data` is a symlink to an `aiqclib`
  output tree in a working checkout, so the old path wrote into the real data;
  the sort is what the streaming build now requires of any input.
- The build streams. It reads each dataset twice under polars' streaming
  engine instead of loading it, which takes the largest dataset here from
  about 90 GB of memory (it could not run) to 2.4 GB.
- Observation files keep the input's row order rather than being sorted, and
  the build now requires the input to be ordered by `platform_code` and stops
  if it is not. Sorting is what memory use scales with; the order is what
  lets the site find a profile by reading one row group.
- The site reads observation files as remote views over HTTP range requests
  rather than downloading them whole. Opening a profile in the largest
  dataset costs about 2 MB instead of 126 MB. `profiles.parquet` is still
  fetched whole, because every query over it reads all of it anyway.
- `flags.py` states the agreement rule once as `status_predicates`, and
  derives both the published status column and the per-profile counts from
  it.

## [0.1.0] - 2026-09-21

### Added

- Build step (`nrtqc-diff build`) turning `aiqclib` NRT QC output into the
  site's data files: a catalog, one profile summary file, and one observation
  file per dataset.
- Trimming to profiles in which at least one source flagged at least one
  observation, by profile rather than by observation.
- Five agreement categories per observation and variable, shared by the
  legend, the tables and the plots through `catalog.json`.
- Quarto dashboard with a region and product tree, a per-profile summary
  table, per-variable contingency tables, a QC item breakdown, and zoomable
  profile plots of temperature and salinity against pressure.
- Data access over DuckDB WASM through stratum-duckdb, isolated in
  `site/assets/js/db.js`.
- `scripts/make_demo_data.py`, generating synthetic inputs so the site can be
  built and rendered without access to real data.
- `scripts/fetch_assets.sh`, vendoring the browser libraries into
  `site/libs/`.
