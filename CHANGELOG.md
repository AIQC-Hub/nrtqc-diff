# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
