# The analyses

The Analyses menu of the site holds four pages of figures and tables. Their
figures are meant to be reused in a manuscript, so they are drawn locally,
in R with ggplot2, rather than in the browser like the rest of the site.

```
nrtqclib output (2.2 GB)  ──┐
ctddump reports, dups (TSV) ├─ nrtqc-diff analyse ─→ site/data/analyses/tables/*.csv
seastamp reports (TSV)    ──┘                      ─→ site/data/analyses/meta.json
                                                            │
                       Rscript analysis/run_all.R  ←────────┘
                                │
                                └─→ site/data/analyses/figures/*.svg, *.pdf
```

## Running it

From a checkout whose `data/` holds the `nrtqclib` batch, with the ctddump
and seastamp reports beside it (see the `analyses` section of
`config/test_nrt.yaml`):

```bash
uv run nrtqc-diff analyse -c config/test_nrt.yaml   # about 2 minutes, 3 GB
Rscript analysis/run_all.R                          # about 15 seconds
quarto render site
```

One page's figures can be redrawn alone with
`Rscript -e 'source("analysis/R/depth.R")'`.

### Why two languages

**Python reads the data and R only draws.** Everything that touches the
observations is in `src/nrtqc_diff/analyse.py`, which streams each input
with polars and writes small tidy tables: the largest has a few tens of
thousands of rows, and all of them together are about 4 MB. The R scripts
read nothing else. R is never handed observations: large data in R and
ggplot2 has crashed the machine before, and at this size the scripts peak at
about 400 MB.

Keep it that way. A figure that needs something new from the observations
gets a new table in `analyse.py` first, then a script that reads it.

The Python step is careful about memory in two ways, both measured:

- A cast inside a streaming aggregation, ahead of `first()`, makes polars
  materialise every group. Casting after collecting kept the profile pass
  at 1.5 GB on the Baltic file instead of 3.4 GB, and that pass is what grows
  with the input.
- `pl.collect_all` runs its queries side by side. The item pass collects
  them one at a time instead, which took its peak on the Arctic NRT file
  from 5.9 GB to 2.7 GB.

The full batch peaks at about 3 GB. To check a change against that, run it
under a memory limit, for example `systemd-run --user --scope -p MemoryMax=8G`.

### R packages

`analysis/DESCRIPTION` lists the packages the scripts use, and
`analysis/renv.lock` records the versions the figures were drawn with
(R 4.4.1). The scripts do not activate renv; to rebuild that library
elsewhere, `renv::restore(project = "analysis")`.

## Configuration

The `analyses` section of the build configuration, and a `source` on each
dataset naming its file stem in the ctddump and seastamp reports
(`nrt_ar_ar`, `cora_bo`). `nrtqc-diff build` ignores both.

```yaml
analyses:
  out_dir: ../site/data/analyses
  ctddump_reports: ../data/../ctddump/report
  ctddump_headers: ../data/../ctddump/header
  ctddump_dups: ../data/../ctddump/dups
  seastamp_reports: ../data/../seastamp/report/stamp
  land:
    column: bathymetry      # the sea floor depth column of the NRT QC output
    positive_depth: false   # GEBCO elevation: negative under water
    margins_m: [0, 5, 10, 20, 50, 100]
  estimated_delayed:
    min_share: 0.6
    min_observations: 5
    groups: [nrt, unknown]
    sensitivity: [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
```

`ctddump_dups` holds the duplicate lists ctddump's `markdup` writes beside
its data (`output/dedup/markdup/<stem>.dups.tsv`), not under `report/`: they
are what tells a duplicate within one platform from one across platforms.

The report folders are resolved through symbolic links before `..`: they sit
next to the batch, which is reached through the `data` link, so
`../data/../ctddump` means the folder beside where the link points. A report
folder left out of the section only leaves its tables out.

## The groups

Every profile is filed under exactly one group, decided in this order:

| Group | Rule |
| --- | --- |
| `land` | The highest sea floor elevation among its positions is at or above 0 m. The rule of nrtqclib's position on land test (RTQC4), which the batch did not run. |
| `delayed` | A delayed mode observation in a data mode column, or a product configured as delayed (CORA), as on the rest of the site. |
| `estimated_delayed` | Marked NRT, or with no data mode, and at least `min_share` of its observations with a usable input flag are flagged by the input and not by nrtqclib, in either variable. A variable counts only with `min_observations` or more such observations. |
| `unknown` | No delayed mode observation, but one with no data mode. |
| `nrt` | Everything else: at sea, real time throughout, not estimated. The item tables are computed over this group alone. |

A usable input flag is one that is present and not among the variable's
`missing_flag_values` (0 and 9). The share is bimodal on this batch, with a
trough between about 50% and 90%, which is why 0.6 is the default and why
the threshold table barely moves across that range.

## The tables

All are CSV under `tables/`, with `dataset_id`, `region`, `product` and
`label` leading each row where they apply. `meta.json` lists the tables
written, the datasets, the variables with their flag value lists, the
agreement categories with their colours, the groups, the pressure bands and
the rules above. `analyses_format` is raised with any change to a table's
name or columns.

| Table | One row per | Holds |
| --- | --- | --- |
| `conversion_steps` | dataset, ctddump step | platforms, profiles and observations left. The last step is what nrtqclib checked: `analyse` warns if an output holds anything else |
| `conversion_data_mode` | dataset, step, variable, mode | observations per data mode, as converted and as kept; the mode is empty where ctddump found none, missing levels included |
| `conversion_duplicates` | dataset | profiles ctddump marked as duplicates (every member of a group, the one kept too), and the platforms they are on |
| `conversion_dup_kinds` | dataset, kind | duplicate groups `within` one platform, `across` platforms (one profile each), or `both` (several platforms, one repeating the cast): groups, profiles in them, profiles removed |
| `conversion_dup_sizes` | dataset, kind, group size | groups of that many profiles; above 10 pooled as `11+` |
| `conversion_dup_platforms` | dataset, platform set | the 15 commonest sets of platform codes sharing casts, over groups `across` and `both`, then the rest in one row |
| `conversion_files` | dataset, variable | source files carrying it |
| `conversion_file_data_mode` | dataset, mode | source files declaring that global `data_mode` |
| `conversion_extra_parameters` | dataset, parameter | source files carrying a parameter ctddump does not read |
| `conversion_compare` | reference, compared dataset | ctddump's profile matching between products of one region, with the platform code in the key |
| `conversion_overlap` | dataset, compared dataset of its region | its profiles the other also has, matched on the date and positions within 0.001° (see below), with the platform code and without |
| `conversion_match` | NRT dataset, CORA dataset of its region | pairs of the same cast on the same platform: how many, how many with the same number of observations, on which side there are more, how close their times are |
| `conversion_match_difference` | NRT dataset, CORA dataset, difference class | mismatched pairs by observations in NRT minus observations in CORA |
| `conversion_match_kinds` | NRT dataset, CORA dataset, kind | mismatched pairs by how their versions' levels relate: `compared_removed` (CORA is NRT with levels removed), `dataset_removed`, `compared_doubled` (CORA holds every level twice), `dataset_doubled`, `both_differ` |
| `conversion_match_levels` | NRT dataset, CORA dataset, side, position, content | levels in one version only (`side`), by `position` against the shared levels (`top`, `inside`, `bottom`, `no_common`) and `content` (`no_value`, `flagged_bad`, `repeat`, `good_value`), with the pairs they are in |
| `conversion_match_platforms` | NRT dataset, CORA dataset, platform | platforms with mismatched pairs, with the median difference |
| `depth_stamp` | dataset, report line | seastamp's own summary, by rows and locations |
| `depth_summary` | dataset | profiles, land profiles, and what the input flagged on them |
| `depth_land_margin` | dataset, margin | land profiles at least that many metres up |
| `depth_elevation_hist` | dataset, 5 m bin | profiles by sea floor height within 100 m of sea level |
| `depth_land_position_qc` | dataset, land or sea, value | profiles by `position_qc` |
| `depth_land_original` | dataset, variable, class | land profiles with every, some, no observation flagged by the input |
| `depth_land_flags` | dataset, variable, flag | observations of land profiles by input flag |
| `depth_land_profiles` | land profile | position, height, distance to the coast, flags |
| `dm_groups` | dataset, data mode, group | profiles, observations and agreement counts |
| `dm_share_hist` | dataset, data mode, share bin | profiles by input-only share; exactly 0 and exactly 1 are bins of their own |
| `dm_threshold` | dataset, data mode, threshold | profiles at or above it |
| `dm_estimated_platforms` | platform with an estimated profile | its profiles, candidates, estimated and declared delayed ones |
| `dm_estimated_profiles` | estimated profile | its counts and shares |
| `items_cube` | dataset, variable, pressure band, input flag, nrtqclib flag, failing checks | observations: every `items_` table below is a sum over this |
| `items_status` | dataset, variable, category | observations of NRT profiles |
| `items_by_item` | dataset, variable, check | failures, with the input too, only nrtqclib, and that check alone |
| `items_combinations` | dataset, variable, category, set of failing checks | observations |
| `items_depth` | dataset, variable, pressure band, category | observations |
| `items_original_flags` | dataset, variable, input flag, nrtqclib bad | observations |
| `items_platforms` | dataset, platform | profiles, observations and agreement counts |

### Matching profiles across products

Two profiles of different products are the same cast when they share a
date and their positions are within 0.001° in longitude and in latitude.
ctddump's own comparison rounds positions to 3 decimals instead, and since
the products rarely store a position to the same last digit, a pair either
side of a rounding boundary is missed: the tolerance finds 4% to 11% more
pairs than the rounding on this batch. Each
profile is paired at most once, closest in time first, and when the platform
code is not part of the match a partner on the same platform is preferred.
"The same cast in NRT and CORA" pairs each product with the products of its
region configured as `data_mode: delayed`, on the platform code too.

The pairs whose numbers of observations differ are then compared level by
level, reading only their observations from the two files. Levels are
matched by pressure within 0.05 dbar: a pressure converted from depth
differs in its last digits between the versions, so exact or rounded
pressures split one level in two (in the Baltic, most of the apparent
differences). Both versions' pressures are sorted together and a new level
starts at a gap of more than the tolerance, which keeps repeats countable.

## The figures

Each figure is written twice, as SVG for the site and PDF for the
manuscript, at a journal's double-column width of 174 mm. `setup.R` holds
the theme, the palettes and `save_figure()`.

| Script | Figures |
| --- | --- |
| `conversion.R` | `conversion_steps`, `conversion_data_mode`, `conversion_duplicates`, `conversion_overlap`, `conversion_match_difference`, `conversion_match_kinds`, `conversion_match_levels` |
| `depth.R` | `depth_land_qc`, `depth_land_map`, `depth_elevation`, `depth_land_coast` |
| `data_mode.R` | `dm_groups`, `dm_share`, `dm_threshold` |
| `flag_differences.R` | `items_status`, `items_attribution`, `items_combinations`, `items_depth` |

Agreement categories take the site's own colours, read from `meta.json`, so
a figure and the dashboard say the same thing in the same colour. Products
take three categorical slots checked to stay apart under colour vision
deficiency in every pairing, and the groups four more plus a neutral grey.
Where more series than that share a panel, colour carries the product and
line type the region rather than adding hues.

## The pages

`site/analyses-*.qmd`, under the Analyses menu. Each reads `meta.json` and
the tables with `assets/js/analyses.js`, shows the figures as images, and
draws its tables with the same `tableView` as the rest of the site. Prose on
the pages carries no counts: those are in the tables, so the text stays
true when the data changes.

If the analyses are not in `site/data`, as in the demo build, every page
says so instead of failing, and the site renders all the same.

The site is public. The pages describe what the data shows; anything
learned from code that is not public stays out of them.

## Publishing

`scripts/data_release.py publish` uploads everything under
`site/data/analyses/` with the rest of the build, each file named by its
path with `--` for `/` (`analyses--tables--dm_groups.csv`), and `fetch`
puts them back. Run `nrtqc-diff analyse` and `run_all.R` before publishing;
a release without them deploys pages that say the analyses were not
published.
