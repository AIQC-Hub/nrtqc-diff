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
  ctddump_dups: ../data/../ctddump/dedup/markdup
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
When `markdup`'s parquet (`<stem>.parquet`) is beside a list, each group is
also checked against time and values (see
[Checking the duplicate groups](#checking-the-duplicate-groups)).

The report folders are resolved through symbolic links before `..`: they sit
next to the batch, which is reached through the `data` link, so
`../data/../ctddump` means the folder beside where the link points. A report
folder left out of the section only leaves its tables out.

### Provenance

Neither ctddump's nor nrtqclib's parquet records which version wrote it, so
an optional top-level `provenance` section says where the batch came from,
and `nrtqc-diff analyse` copies it into `meta.json` under `provenance`:

```yaml
provenance:
  note: free text about the batch
  tools:            # name, version; optional doi, note
    - {name: ctddump, version: "0.37.2", doi: 10.5281/zenodo.24093111}
  nrtqclib_config: ../data/config/nrt_qc_config.yaml
  reference_data:   # name; optional version, doi, note
    - {name: "GSHHG", version: "2.3.7"}
  products:         # product, dataset, datasets; optional part, files, doi, downloaded, note
    - product: INSITU_BAL_PHYBGCWAV_DISCRETE_MYNRT_013_032
      dataset: cmems_obs-ins_bal_phybgcwav_mynrt_na_irr_202311
      doi: 10.48670/moi-00032
      datasets: [bo_bo]
  regions:          # region, include; optional exclude, source
    - region: Baltic Sea
      source: ctddump 0.37.2, scripts/clean_data.sh
      include: [{min_lon: 6, max_lon: 30, min_lat: 53, max_lat: 66}]
      exclude: [{min_lon: 6, max_lon: 15, min_lat: 60, max_lat: 66}]
```

Values are kept as text, as written. An unknown key, a missing required one,
or a product naming a dataset the configuration lacks is an error, so a typo
cannot drop a line from the record. `nrtqclib_config` is resolved like the
report folders and recorded as its file name and SHA-256 checksum, not its
path; a missing file is an error. Each dataset in `meta.json` also gets
`first_profile` and `last_profile`, the dates of its first and last profile,
which bound the download date when nothing else records it.

Keep the section current whenever the batch is rerun: it is the only record
of the versions behind the tables.

### Test parameters and regions

`meta.json` also gets `nrtqc_parameters`, the parameters a reader needs to
know what ran, without the files they came from:

- `nrtqclib.qc_item_sets`: each `qc_item_set` of the nrtqclib
  configuration that some dataset ran, its tests in order, each with its
  `params` as written there (an empty mapping when it has none): global
  and regional range, spike and gradient (`shallow`, `deep` and the
  `depth_threshold` between them), digit rollover, density inversion.
- `nrtqclib.datasets`: the set each dataset ran. A dataset is matched to an
  `nrt_qc_sets` entry by its `source` and the entry's input file name, or
  else by the entry's dataset folder among the parts of its `path`.
  Nothing else of the configuration is copied: no paths, folders or steps.
- `ctddump_regions`: `provenance.regions` as configured, ctddump's boxes
  per region (its step 4, `filter`), which no ctddump report records. A
  profile is kept inside an `include` box and outside every `exclude` box,
  edges included, a missing position outside. Each region gains `outside`,
  the profiles of each of its datasets in nrtqclib's output that lie
  outside it: there should be none, and `analyse` warns otherwise. A region
  name must be one a dataset has, and a box needs all four edges, its
  minimum at most its maximum.

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
written, the datasets with their first and last profile dates, the
variables with their flag value lists, the agreement categories with their
colours, the groups, the pressure bands, the rules above and, when the
configuration has one, the provenance of the batch. `analyses_format` is raised with any change to a table's
name or columns.

| Table | One row per | Holds |
| --- | --- | --- |
| `conversion_steps` | dataset, ctddump step | platforms, profiles and observations left. The last step is what nrtqclib checked: `analyse` warns if an output holds anything else |
| `conversion_data_mode` | dataset, step, variable, mode | observations per data mode, as converted and as kept; the mode is empty where ctddump found none, missing levels included |
| `conversion_duplicates` | dataset | profiles ctddump marked as duplicates (every member of a group, the one kept too), and the platforms they are on |
| `conversion_dup_kinds` | dataset, kind, match | duplicate groups `within` one platform, `across` platforms (one profile each), or `both` (several platforms, one repeating the cast): groups, profiles in them, profiles removed. `match` (`confirmed`, `same_time`, `candidate`) only when the groups were checked; summed over it the counts are the same |
| `conversion_dup_sizes` | dataset, kind, match, group size | groups of that many profiles; above 10 pooled as `11+`. `match` as above |
| `conversion_dup_checks` | dataset, kind, time, levels, values, position, whole degree, match | duplicate groups, the profiles in them and the profiles removed, by every check (see [Checking the duplicate groups](#checking-the-duplicate-groups)) |
| `conversion_dup_kept` | dataset, kept by | duplicate groups and the profiles in them, by which member reached nrtqclib's output (see [Checking the duplicate groups](#checking-the-duplicate-groups)) |
| `conversion_dup_platforms` | dataset, platform set | the 15 commonest sets of platform codes sharing casts, over groups `across` and `both`, then the rest in one row |
| `conversion_files` | dataset, variable | source files carrying it |
| `conversion_pressure_order` | dataset | ctddump's `checkpres` summary (`convert/checkpres/<stem>.summary.tsv`, ctddump 0.37 or later): profiles, platforms and files checked before the merge sorts by pressure, profiles without pressure, and those whose pressure steps down, `reversed` and `non_monotonic`, with the platforms and files holding them. Then from the profile list beside it (see [The stored pressure order](#the-stored-pressure-order)): the listed profiles found in nrtqclib's output (`n_reversed_matched`, `n_non_monotonic_matched`) and the output's profiles merged from several source profiles (`n_merged`) |
| `conversion_records` | dataset | why `checkpres` counts more profiles than `convert` (see [Records and profiles](#records-and-profiles)): source files and their records from the header (`n_files`, `n_records`), records checked and without pressure from `checkpres` (`n_checked`, `n_no_pres`), profiles after the merge (`n_converted`), the records the merge joined to another (`n_merged_records`, checked less converted) and records neither checked nor without pressure (`n_unchecked`) |
| `conversion_dates` | dataset | the dates each product covers either side of `convert`, which records none (see [Product dates](#product-dates)): the source files and those dated from the header (`n_files`, `n_files_dated`), how they are dated (`date_source`: `time_coverage_start`, `file_name` or both), the first date declared and, when every file is a daily file dated by its name, the last (`declared_first`, `declared_last`), and the first and last profile inside the region before `dedup` (`region_first`, `region_last`), as ISO dates |
| `conversion_dropqc` | dataset | the profiles ctddump's `dropqc` dropped (time or position flag neither good nor missing), from the platform reports before and after it, and where they were as far as the reports tell: `n_moving` on a platform that moved, so unplaced; on a fixed platform (one point before the step) the batch stamped there, `n_sea` or `n_land`, the land ones by cause as on the Depth page; `n_not_stamped` on a fixed platform the batch holds nowhere at that point. seastamp runs after `dropqc`, so a dropped profile has no stamp of its own |
| `conversion_dropqc_platforms` | dataset, platform that lost a profile | its profiles before the step and dropped, its status as above, and for a fixed platform its point, the elevation and distance to the coast stamped there, and the land cause |
| `conversion_file_data_mode` | dataset, mode | source files declaring that global `data_mode` |
| `conversion_extra_parameters` | dataset, parameter | source files carrying a parameter ctddump does not read |
| `conversion_compare` | reference, compared dataset | ctddump's profile matching between products of one region, with the platform code in the key |
| `conversion_overlap` | dataset, compared dataset of its region | its profiles the other also has, matched on the date and positions within 0.001° (see below), with the platform code and without. The page shows the NRT products' rows |
| `conversion_overlap_checks` | NRT dataset, the NRT dataset of its region after it in the configuration, platform (`same` or `other`), time, levels, values, position, match | pairs of `n_matched_any_platform`, checked as a duplicate group of two would be, from nrtqclib's output |
| `conversion_coverage` | NRT dataset, CORA dataset of its region | CORA's period (first and last date) and the NRT profiles in each coverage class (see below) |
| `conversion_coverage_years` | NRT dataset, CORA dataset, year, coverage class | NRT profiles |
| `conversion_match` | NRT dataset, CORA dataset, coverage class | pairs of the same cast (`same_platform` or `other_platform`): how many, how many with the same number of observations, and on which side there are more |
| `cora_pairs` | NRT dataset, CORA dataset, variable, coverage class | the pairs counted through each condition of the level by level comparison (see below): `n_pairs`, `n_same_obs`, `n_nrt` (the NRT profile is `nrt` for the variable), `n_aligned` (the levels line up), `n_pres_exact` (at the same pressures exactly), and the observations compared (`n_obs`) |
| `cora_flags` | NRT dataset, CORA dataset, variable, coverage class, pressure band, the four flags | observations of the aligned pairs: the producer's NRT flag (`original_flag`), nrtqclib's on the NRT copy (`nrtqclib_flag`), CORA's (`cora_flag`) and nrtqclib's on the CORA copy (`cora_nrtqclib_flag`), as raw values, empty where missing |
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
| `items_cube` | dataset, variable, stored pressure order (`pres_order`), pressure band, input flag, nrtqclib flag, failing checks | observations: every `items_` table below is a sum over this |
| `items_status` | dataset, variable, category | observations of NRT profiles |
| `items_by_item` | dataset, variable, check | failures, with the input too, only nrtqclib, and that check alone |
| `items_combinations` | dataset, variable, category, set of failing checks | observations |
| `items_depth` | dataset, variable, pressure band, category | observations |
| `items_original_flags` | dataset, variable, input flag, nrtqclib bad | observations |
| `items_platforms` | dataset, variable, platform | profiles, observations and agreement counts, over the variable's NRT profiles |
| `items_shape` | dataset, variable, category (original only or nrtqclib only), place in the profile, input flag, negative pressure | observations, and the profiles they are in |
| `items_response` | dataset, test, flag it is set against (a variable, `density` for either variable's flag, `pres` for the pressure flag), stored pressure order (`pres_order`), band of how far past nrtqclib's threshold | failures (`n`, as observations, pairs of levels or profiles) and how many the input flags (`n_flagged`) |
| `pressure_repeats` | dataset, flag it is set against (as in `items_response`), `pres_order`, kind (`copied`: the level above has the same pressure and values, both missing counting as equal; `other`) | the repeated pressures of `items_response`'s band 1 of `pressure_increasing` (`n`), how many the input flags, and the profiles and platforms holding them |
| `pressure_repeat_platforms` | dataset, flag, `pres_order`, platform with a repeated pressure | its profiles with one, its repeats, the copied ones, and how many of each the input flags |
| `density_threshold` | dataset, platform group (`ice_tethered` or `other`), measure (`level`: kg/m³ between levels; `dbar`: per dbar), band (`lower` exclusive, `upper` inclusive, empty for the last) | density inversions, and how many the input flags at either level (`n_flagged`), the deeper one (`n_flagged_deeper`) and the upper one (`n_flagged_upper`) |
| `density_platforms` | dataset, platform, its group and median level spacing (`spacing_dbar`), for platforms with an inversion | pairs of levels, inversions, those up to 0.05 kg/m³ (`n_small`) and above it (`n_clear`), and how many the input flags |

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
an nrtqclib test is measured again from the values, in the order nrtqclib
sees them (ctddump's, by increasing pressure), and put in a band by how far past nrtqclib's threshold it
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
- **Frozen profile**, which nrtqclib does not run: each profile is set
  against the same platform's previous one at another time (one at the
  same time is a duplicate), across slices as well. The bands are
  different, within the Argo test's limits on 50 dbar slab means
  (`FROZEN_LIMITS`), and an exact repeat (the same pressures and values at
  every level); only an exact repeat is a clear failure, since profiles
  hours apart often pass Argo's limits. Only profiles inside the range and
  not one value throughout are measured, and one counts as flagged as a
  stuck profile does.
- **Density** is EOS-80 density at zero pressure from the in situ
  temperature, not nrtqclib's TEOS-10 potential density: close enough to
  band an inversion, not to repeat the test exactly. The centres give
  their threshold per dbar, so `density_inversion_per_dbar` divides by the
  pressure between the levels.
- **Pressure** is set against the pressure flag and each variable's flag,
  since a producer can flag a repeated pressure in the pressure flag only.
- **Density by platform.** The same pass counts the density inversions
  again for two tables a product that runs its test on some platforms
  only needs. `density_threshold` bands them finer around 0.05 kg/m³
  (`DENSITY_EDGES`), between levels and per dbar, per platform group:
  ice-tethered profilers, whose codes start `ITP-` or `TOP-`
  (`ICE_TETHERED_PREFIXES`), and the rest. It also counts the deeper and
  the upper level's flag apart, since a test flags one of the two.
  `density_platforms` counts each platform's inversions, clear above
  `DENSITY_CLEAR` (0.05 kg/m³ between levels, the clear bands of
  `items_response`), with its median level spacing, rounded to 0.01 dbar.
  Both count the pairs of `items_response`'s density inversion, and
  `meta.json` has the edges, the clear limit and the prefixes under
  `rules.density`.

A test needs a level's neighbours, which the streaming engine cannot give,
so the pass reads 2 million rows at a time, carries a profile cut by the
end of a slice over to the next, and numbers the profiles of a slice so
that it compares and groups on integers. NaN values are read as missing.
It adds about a minute to the run.

### The stored pressure order

ctddump's `concat convert` merges the source profiles of one platform,
time and position into one profile, from one file or several, and sorts
its levels by pressure before nrtqclib runs. A cast stored down and up again, or a
downcast and an upcast stored as two profiles, is then interleaved, and its
spikes, gradients and density inversions may come from the sort rather than
the water. `pres_order` in `items_cube`, `items_response` and the pressure
repeat tables says how each profile was stored, so the results can be read
with and without them. Summed over it, the tables are what they were
without it.

ctddump's `checkpres` lists each source profile whose pressure steps down
(`convert/checkpres/<stem>.profiles.tsv`). Its profile number counts within
one source file, so a listed profile is matched to nrtqclib's output on its
file, platform and `profile_time` exactly and its position within 0.0001°
(`rules.pres_order` in `meta.json`); one listed profile matching two of the
output's is an error. Several listed profiles can match one: those are the
merges.

| `pres_order` | Rule |
| --- | --- |
| `reversed` | One listed source profile, stored bottom up, and all of the profile's levels are its own. |
| `non_monotonic` | The same, stored down and up again. |
| `merged` | Two or more listed source profiles, or one with fewer levels than the profile: the rest came from a source profile the list does not hold. |
| `monotonic` | No listed source profile. It never steps down, though it may repeat a pressure, and it may still be a merge of parts each stored in order, which the list cannot show. |
| empty | The dataset has no list (ctddump before 0.37). |

A listed profile that matches nothing was dropped by a later ctddump step:
outside the region, `dropqc`, `dropna`, `filter` or `dedup`. After the
sort, `pressure_increasing` can only find repeated pressures (band 1);
`pressure_repeats` tells a level written twice (`copied`) from a repeat with
other values.

### Records and profiles

ctddump converts each source file to parquet keeping every record (one
`TIME` index in the NRT products, one `N_PROF` index in CORA) as a profile,
and `checkpres` counts those. `concat convert` then drops the observations
without a pressure, and numbers profiles by the dense rank of
`platform_code`, `profile_timestamp`, `longitude` and `latitude` within the
platform (ctddump 0.37.2, `src/concat.rs`): records of one platform with the
same time and position become one profile, whichever files they came from.
So `n_checked` less `n_converted` is the number of records joined to an
earlier one, as long as `n_no_pres` is 0. Nothing else is dropped at this
step.

`n_records` sums the record dimension each file's header lists
(`header/<stem>.yaml`, `N_PROF` before `TIME`). A record the per-file
conversion writes no row for (every observation missing) is in
`n_records` but neither checked nor without pressure: `n_unchecked`.

### Product dates

No ctddump report records the dates of the profiles `concat convert`
writes, and its parquet is not kept, so `conversion_dates` gives the dates
either side of it.

- **Declared** (`header/<stem>.yaml`). An NRT file states where it starts,
  in its `time_coverage_start` global attribute, and nothing where it ends;
  `declared_first` is the earliest. A CORA daily file is dated by its name
  (`CO_DMQCGL01_<YYYYMMDD>_PR_CT`), which gives both ends. A file with the
  attribute is dated by it, and a file with neither is not dated.
- **In the region** (`markdup`'s `<stem>.parquet` in `ctddump_dups`). The
  first and last profile after `clean` (`dropqc`, `dropna`, then the
  region's boxes) and before `dedup`.

The kept dates, `first_profile` and `last_profile` of each dataset in
`meta.json`, come from nrtqclib's output. A declared start earlier than the
region's first profile means the earliest profiles were dropped at
`clean`: a global file reaches outside the region, or a file's earliest
profiles fail `dropqc` or `dropna`.

### Matching profiles across products

Two profiles of different products are the same cast when they share a
date and their positions are within 0.001° in longitude and in latitude.
ctddump's own comparison rounds positions to 3 decimals instead, and since
the products rarely store a position to the same last digit, a pair either
side of a rounding boundary is missed: the tolerance finds 4% to 11% more
pairs than the rounding on this batch. Each profile is paired at most once,
closest in time first, and when the platform code is not part of the match
a partner on the same platform is preferred.

### Checking the duplicate groups

ctddump's `markdup` groups profiles on the date and positions within
0.001° alone. Each group is checked further, from the list and from the
observations in `markdup`'s parquet, where `is_dup` marks every member:

| Check | Values | Rule |
| --- | --- | --- |
| `time` | `same_second`, `within_minute`, `within_hour`, `same_date` | the spread of the members' `profile_timestamp`: 0 s, under 60 s, under 3600 s, more |
| `levels` | `same`, `differ` | whether every member has the same number of observations |
| `values` | `identical`, `close`, `near`, `different`, `not_compared` | members with the same number of levels, level by level in stored order (ctddump's sort by pressure): the spread (largest minus smallest) of pressure, temperature and salinity at each level. `identical`: every spread 0, a value missing in every member counting as equal; `close`: within 0.1 dbar, 0.001 °C and 0.001, allowing 1% for single precision; `near`: within 0.1 dbar, 0.01 °C and 0.01, as when one copy holds temperature or salinity to two decimals and another to three (products, regions and CORA may each round differently); `different`: more, or a value missing in some members only. `not_compared` when the levels differ |
| `position` | `equal`, `within` | whether every member has the same longitude and latitude |
| `whole_degree` | true, false | every member at whole degrees in both coordinates |
| `match` | `confirmed`, `same_time`, `candidate` | `confirmed`: `time` is `same_second` or `within_minute`, `values` `identical` or `close`; `same_time`: the rest at those times, `near` among them; `candidate`: the rest |

A group of three or more counts as `identical` or `close` only if all its
members agree. The classes and cuts are in `meta.json` under
`rules.duplicates`. The groups are read in chunks of about 5 million
observations, which keeps CORA Arctic's near 1.7 GB.

`conversion_dup_kept` says which member of each group reached nrtqclib's
output. ctddump states that `dedup` keeps the member with the most
observation rows in `markdup`'s parquet, ties going to the first to appear
there (ctddump 0.37.2, `src/dedup.rs`), and it keeps `platform_code` and
`profile_no`. The member's rows and first row are read from the parquet:

| `kept_by` | Rule |
| --- | --- |
| `most_obs` | The one member with the most observations. |
| `first_of_tied` | Of members tied on the most, the first in `markdup`'s row order. |
| `other` | Any other member: the stated rule did not hold. |
| `none` | No member is in the output. |
| `several` | More than one member is in the output. |

`conversion_overlap_checks` applies the same checks to the casts two NRT
products of one region share (`n_matched_any_platform` of
`conversion_overlap`), each pair of products once: `time` from the time
between the two, `values` from nrtqclib's output, and `platform` whether
the two carry the same code.

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

### NRT flags against CORA's

Machine learning trains on delayed mode flags, so `cora_flags` sets the NRT
flags of a cast against CORA's for the same levels. A pair of the coverage
is compared level by level when:

1. the two copies hold the same number of observations;
2. for the variable, the NRT profile is `nrt`, as in the `items_` tables
   (delayed mode, estimated delayed mode, unknown and land profiles are
   left out);
3. every observation number of one copy is in the other, and the two
   pressures of each level agree within 0.1 dbar (`rules.cora` in
   `meta.json`), both missing counting as agreeing.

`cora_pairs` counts the pairs left after each condition, so the
observations compared can be traced back to the casts. The pairs are taken
in platform order, about two million levels at a time, and both products are
read for each chunk, keeping only its profiles' levels. The files are
sorted by platform, so a chunk reads only the row groups of its platforms.

## The figures

Each figure is written twice, as SVG for the site and PDF for the
manuscript, at a journal's double-column width of 174 mm. `setup.R` holds
the theme, the palettes and `save_figure()`.

| Script | Figures |
| --- | --- |
| `conversion.R` | `conversion_steps`, `conversion_data_mode`, `conversion_duplicates`, `conversion_pressure_order`, `conversion_coverage`, `conversion_coverage_years` |
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
