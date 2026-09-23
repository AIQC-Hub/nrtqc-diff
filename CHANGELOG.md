# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.5] - 2026-09-23

### Changed

- The QC check breakdown under the plots is one table per variable, side by
  side, rather than one list with `temp_` and `psal_` prefixes mixed together.
  A check that judges the whole profile is listed under both, because both
  computed flags take it in, and the line above the tables says so.
- The panel is titled `aiqclib QC checks that flagged`, and each row carries
  the column the check writes under its name rather than in a column of its
  own, which is what leaves the two tables room to sit beside each other.

## [0.3.4] - 2026-09-23

### Changed

- The panel breaking a profile down by QC check says whose flags it is
  showing. It is titled `aiqclib checks that flagged`, and a line above the
  table adds that the flags which came with the input data are one value per
  observation and break down no further. The plots beside it draw both
  sources at once, so a table of flag values could be read as either.

## [0.3.3] - 2026-09-23

### Changed

- Nothing on the dashboard shows a column name or a flag number on its own.
  The contingency tables head their axes "Flag in the input data" and "Flag
  aiqclib computed" with the column name under each, every flag value carries
  the word the IOC/Argo scheme gives it, and a line above the tables says that
  the red marks the values this product counts as an anomaly. Reading them
  used to need the schema.
- The `QC checks that flagged` panel names each check in words, keeps the
  column name in a second column, glosses the flag it raised, and links to the
  table on the About page that describes what the check looks for.
- The two contingency tables sit side by side while the window has room for
  both, which is down to a 1280px laptop, instead of stacking the second one
  out of sight.
- The demo generator writes every QC check the About page lists, rather than
  nine of them, and each one fires in at least one profile: the published site
  runs on this data, so a check it never wrote was a check no reader could see
  working. It also derives the rolled-up `temp_nrt_flag` and `psal_nrt_flag`
  from the item columns instead of from a second list.
- The About page counts the checks it lists correctly (twelve, eleven of them
  real-time tests) and says plainly that a check listed there can be absent
  from a product whose `aiqclib` run left it out.

## [0.3.2] - 2026-09-22

### Changed

- The colour key is on the Tables page too, down the right of the profile
  summary. The bar in that panel is the only thing on the page that says
  anything in colour, and its key was on the other tab.

## [0.3.1] - 2026-09-22

### Changed

- The first panel of the dashboard's Tables page is a summary of the selected
  profile alone: one row per variable, one column per agreement category, and
  a bar splitting what was flagged. It replaces the list of every profile in
  the product, which the sidebar already carries, so the top of the page is
  about the profile the reader picked rather than about the ones they did not.

## [0.3.0] - 2026-09-22

### Added

- A summary of the `aiqclib` QC checks on the About page: one row per check,
  with the Argo/CTD real-time test number, the flag column it writes and what
  it looks for. The column names are the ones the `QC checks that flagged`
  panel shows, so a reader can turn `temp_qc_spike` into a sentence without
  leaving the site. It summarises the `aiqclib` NRT QC guide, which it links
  to for thresholds and settings.

## [0.2.2] - 2026-09-22

### Changed

- The colour key heads the plot panel, above the markers it explains, rather
  than standing in a column of its own. The width that column took goes back
  to the plots. (The unreleased rule that forced the key into one column goes
  with the panel it was written for.)
- The QC checks a profile tripped move from the Tables tab to their own panel
  under the plots, where the question they answer is asked.
- That panel is titled `QC checks that flagged` rather than `QC items that
  fired`, and its first column is `QC check`. Firing is what the code calls
  it; this is the same thing in words that assume nothing.
- The plots are the height of the panel holding them rather than a fixed 700
  pixels, so a tall window gives tall plots and a short one still fits both
  rows. Under 200 pixels the box scrolls instead of flattening a cast.
- The contingency tables, which now have the lower half of the Tables tab to
  themselves, are held to a readable width rather than stretched across it.

## [0.2.1] - 2026-09-22

### Changed

- The dashboard's own navbar is titled `Dashboard` rather than the project
  title, which the site navbar directly above it already carries.
- The colour key moves out of the sidebar into its own column on the Plots
  tab, beside the markers it explains. The profile list gets the room it
  leaves behind.

## [0.2.0] - 2026-09-22

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

- The dashboard is split into two tabs, **Plots** and **Tables**, in the
  dashboard's own navbar under the site navbar. Plots draws one profile plot
  per variable, side by side and full width; Tables carries every published
  profile of the product with the full per-variable breakdown, the contingency
  tables and the QC items that fired.
- The profile list moves into the sidebar, which is now the whole drill-down:
  region, product, profile. Both tabs show the selected profile, so switching
  between them keeps your place and either one can be read against the other.
  The table on the Tables page follows that selection without being rebuilt,
  so picking a profile no longer resets the column it was sorted by.
- The profile plots no longer zoom or pan past their data. Both axes are
  bounded to the profile's values plus a 5% margin, so scrolling out stops at
  the cast instead of shrinking it to a speck in an empty frame. Panning holds
  its span at the limits rather than squeezing against them, which is what
  Plotly's own edge-by-edge clamping does. Axis ranges are set outright rather
  than left to autorange, which with the limits in place drew the salinity
  axis backwards.
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
