# The analyses

The Analyses menu of the site holds eight pages of figures and tables. Their
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
uv run nrtqc-diff analyse -c config/test_nrt.yaml   # about 4 minutes, 3.8 GB
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

The full batch peaks at about 3.8 GB. To check a change against that, run it
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
    sensitivity: [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
```

The estimated delayed mode rule itself (`min_share`, `min_observations`,
`groups`) is `data_mode.estimated`, outside this section, because the build
applies it too: the dashboard files a profile as delayed mode when either
variable is estimated, while the analyses keep the variables apart.

`ctddump_dups` holds the duplicate lists ctddump's `markdup` writes beside
its data (`output/dedup/markdup/<stem>.dups.tsv`), not under `report/`: they
are what tells a duplicate within one platform from one across platforms.

The report folders are resolved through symbolic links before `..`: they sit
next to the batch, which is reached through the `data` link, so
`../data/../ctddump` means the folder beside where the link points. A report
folder left out of the section only leaves its tables out.

## The groups

Every profile is filed under exactly one group per variable, decided in
this order. Land and declared delayed mode are the same for every variable;
the estimate is made for each variable on its own, since an operator often
reworks salinity and leaves temperature alone (on this batch, most
estimated profiles are estimated for salinity only).

| Group | Rule |
| --- | --- |
| `land` | The highest sea floor elevation among its positions is at or above 0 m. The rule of nrtqclib's position on land test (RTQC4), which the batch did not run. |
| `delayed` | A delayed mode observation in a data mode column, or a product configured as delayed (CORA), as on the rest of the site. |
| `estimated_delayed` | Marked NRT, or with no data mode, and at least `min_share` of the variable's observations with a usable input flag are flagged by the input and not by nrtqclib. The variable counts only with `min_observations` or more such observations. |
| `unknown` | No delayed mode observation, but one with no data mode. |
| `nrt` | Everything else: at sea, real time throughout, not estimated for the variable. Each variable's item tables are computed over its own `nrt` profiles. |

The land group is the Depth page's subject. The Delayed mode in NRT
products page shows the other groups only, over the profiles at sea, and
the analyses after it never count land profiles. The Probably good and
probably bad page reads its groups for input flags 2 and 3 (the
`dm_probable_` tables). Both pages and their `dm_` tables also leave out every dataset configured whole under one data mode
(CORA): none of its profiles can be estimated, and it would only add rows
that are all delayed mode.

A usable input flag is one that is present and not among the variable's
`missing_flag_values` (0 and 9). The share is bimodal on this batch, and 0.6
is the default: a clear majority of the profile, since an automated test can
plausibly reject half of one. How much the threshold matters differs by
product. The count barely moves between 60% and 90% where the estimated
profiles are almost all flagged in full (Arctic regional), and falls
steadily where the shares spread between the humps (Arctic global salinity,
Mediterranean global temperature). `min_observations` is 10: with 5, three
flagged values of five made exactly 60%, and many short profiles sat on the
threshold.

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
| `conversion_overlap` | dataset, compared dataset of its region | its profiles the other also has, matched on the date and positions within 0.001° (see below), with the platform code and without. The page shows the NRT products' rows |
| `conversion_coverage` | NRT dataset, CORA dataset of its region | CORA's period (first and last date) and the NRT profiles in each coverage class (see below) |
| `conversion_coverage_years` | NRT dataset, CORA dataset, year, coverage class | NRT profiles |
| `conversion_match` | NRT dataset, CORA dataset, coverage class | pairs of the same cast (`same_platform` or `other_platform`): how many, how many with the same number of observations, and on which side there are more |
| `depth_stamp` | dataset, report line | seastamp's own summary, by rows and locations |
| `depth_summary` | dataset | profiles, land profiles, and what the input flagged on them |
| `depth_land_margin` | dataset, margin | land profiles at least that many metres up |
| `depth_elevation_hist` | dataset, 5 m bin | profiles by sea floor height within 100 m of sea level |
| `depth_land_position_qc` | dataset, land or sea, value | profiles by `position_qc` |
| `depth_land_original` | dataset, variable, class | land profiles with every, some, no observation flagged by the input |
| `depth_land_flags` | dataset, variable, flag | observations of land profiles by input flag |
| `depth_land_profiles` | land profile | position, height, distance to the coast, flags |
| `depth_land_cause` | dataset, likely cause | land profiles and their distinct positions: `whole_degrees` (both coordinates whole degrees), `shore` (at most 5 m up and 1 km from the coast), `low_far` (as low, further out), `inland` (higher), tested in that order |
| `input_flags` | dataset, variable, input flag, whether the profile has a `position_qc` | observations, over every profile: land, delayed mode and the rest |
| `dm_groups` | dataset, variable, data mode, group | profiles, observations and the variable's agreement counts |
| `dm_share_hist` | dataset, variable, data mode, share bin | profiles by input-only share; exactly 0 and exactly 1 are bins of their own |
| `dm_threshold` | dataset, variable, data mode, threshold | profiles at or above it |
| `dm_estimated_platforms` | platform and variable with an estimated profile | its profiles, candidates, estimated and declared delayed ones |
| `dm_estimated_profiles` | profile estimated for either variable | its counts, and per variable its share and whether it is estimated |
| `dm_probable_obs` | dataset, variable, input flag 2 or 3, the observation's own data mode, group, nrtqclib bad | observations at sea, and how many each nrtqclib check failed (`fail_{item}`) |
| `dm_probable_profiles` | dataset, variable, input flag 2 or 3, group | profiles at sea carrying it, with it on one observation, and with it on most of the profile (the estimate's share and minimum) |
| `items_cube` | dataset, variable, pressure band, input flag, nrtqclib flag, failing checks | observations: every `items_` table below is a sum over this |
| `items_status` | dataset, variable, category | observations of NRT profiles |
| `items_by_item` | dataset, variable, check | failures, with the input too, only nrtqclib, and that check alone |
| `items_combinations` | dataset, variable, category, set of failing checks | observations |
| `items_depth` | dataset, variable, pressure band, category | observations |
| `items_original_flags` | dataset, variable, input flag, nrtqclib bad | observations |
| `items_platforms` | dataset, variable, platform | profiles, observations and agreement counts, over the variable's NRT profiles |
| `items_shape` | dataset, variable, category (original only or nrtqclib only), place in the profile, input flag, negative pressure | observations, and the profiles they are in |
| `items_response` | dataset, test, flag it is set against (a variable, `density` for either variable's flag, `pres` for the pressure flag), band of how far past nrtqclib's threshold | failures (`n`, as observations, pairs of levels or profiles) and how many the input flags (`n_flagged`) |

### Where a one-sided flag sits

`items_shape` places each observation one source alone flags against the
observations the same source calls good in its profile, by pressure:
`whole` when it calls none of them good, `top` above the shallowest of
them, `bottom` below the deepest, and between them `inside_alone` when it
is its profile's only one there, `inside_several` otherwise. `no_pressure`
has no pressure to place it by. A test that judges a whole profile, one
that judges a level, and a check on the start of a cast (a soaking depth
left in) leave different marks, which is what the two "Flagged only by"
pages read. It takes two streaming passes per dataset and variable (the
good pressure range per profile, then the one-sided observations placed in
it) and adds about 25 seconds to the run.

### Whether the input runs a test

`items_response` checks what the regional centres say they run (see
[`REGIONAL_QC.md`](REGIONAL_QC.md)) against their flags. Each failure of
an nrtqclib test is measured again from the values, in recorded order as
nrtqclib does, and put in a band by how far past nrtqclib's threshold it
is; the bands and which of them count as clear failures are
`RESPONSE_TESTS` in `analyse.py`, written to `meta.json` as
`response_tests`. A producer that runs the test flags nearly all of the
clear failures; one that does not flags them about as often as any other
observation.

- **Thresholds** are those of the `test_nrt` batch, as constants in
  `analyse.py` (`RESPONSE_GLOBAL_RANGE`, `RESPONSE_SPIKE`,
  `RESPONSE_GRADIENT`); change them with the batch's
  `nrt_qc_config.yaml`.
- **Keeping the tests apart**: spike and gradient only on values inside
  the global range with both neighbours inside it, gradient only on
  levels that pass the spike test, and stuck value and density inversion
  only on values inside the range. A stuck profile counts as flagged when
  the input flags at least 95% of its values.
- **Density** is EOS-80 density at zero pressure from the in situ
  temperature, not nrtqclib's TEOS-10 potential density: close enough to
  band an inversion, not to repeat the test exactly. The centres give
  their threshold per dbar, so `density_inversion_per_dbar` divides by the
  pressure between the levels.
- **Pressure** is set against the pressure flag and each variable's flag,
  since a producer can flag a repeated pressure in the pressure flag only.

A test needs a level's neighbours, which the streaming engine cannot give,
so the pass reads 2 million rows at a time, carries a profile cut by the
end of a slice over to the next, and numbers the profiles of a slice so
that it compares and groups on integers. NaN values are read as missing.
It adds about a minute to the run.

### Matching profiles across products

Two profiles of different products are the same cast when they share a
date and their positions are within 0.001° in longitude and in latitude.
ctddump's own comparison rounds positions to 3 decimals instead, and since
the products rarely store a position to the same last digit, a pair either
side of a rounding boundary is missed: the tolerance finds 4% to 11% more
pairs than the rounding on this batch. Each profile is paired at most once,
closest in time first, and when the platform code is not part of the match
a partner on the same platform is preferred.

### NRT and CORA

An NRT product carries real time QC and CORA delayed mode QC of the same
casts, so each NRT product is compared with the products of its region
configured as `data_mode: delayed`. Every NRT profile is filed under the
first coverage class that fits:

| Class | Rule |
| --- | --- |
| `outside_period` | Dated before CORA's first profile or after its last. Left out of the coverage. |
| `same_platform` | Matched with the platform code. |
| `other_platform` | Matched on date and place alone, among the profiles still unpaired on both sides. |
| `nearby` | Same platform and date within 0.01°: probably the same cast, not paired. |
| `platform_only` | The platform is in CORA, the cast is not. |
| `no_platform` | The platform is not in CORA. |

The coverage is `same_platform` plus `other_platform` over the profiles in
CORA's period. Those two classes are the pairs. A pair under another code
is often one instrument named two ways (an ice-tethered profiler by its own
number in one product and a WMO number in the other), and its two versions
are usually sampled at different levels, so `conversion_match` keeps the
classes apart.

## The figures

Each figure is written twice, as SVG for the site and PDF for the
manuscript, at a journal's double-column width of 174 mm. `setup.R` holds
the theme, the palettes and `save_figure()`.

| Script | Figures |
| --- | --- |
| `conversion.R` | `conversion_steps`, `conversion_data_mode`, `conversion_duplicates`, `conversion_coverage`, `conversion_coverage_years` |
| `depth.R` | `depth_land_qc`, `depth_land_map`, `depth_elevation`, `depth_land_cause` |
| `input_flags.R` | `input_flags_shares` |
| `data_mode.R` | `dm_groups`, `dm_share`, `dm_threshold`, `dm_probable` |
| `flag_differences.R` | `items_status`, `items_attribution`, `items_combinations`, `items_depth`, `items_shape_original`, `items_shape_nrtqclib`, `items_response` |

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
