# NRT QC in nc2parquet and nrtqclib

`nc2parquet` is an earlier, private set of scripts from the AIQC project. It
converts NetCDF profile data to parquet, and it also has its own
implementation of the NRT QC tests. This document compares those tests with
the QC items of `nrtqclib`, the library whose flags this dashboard shows. For
each `nrtqclib` item it covers two things: whether `nc2parquet` implements the
item at all, and how the logic and the thresholds differ where it does.

`nc2parquet`'s NRT QC is used for Arctic data only, so the comparison that
matters is with our Arctic datasets (`ar_ar`, `ar_gl`, `cora_ar`) and the
Arctic parameters of our runs. Where `nc2parquet`'s design would behave
differently elsewhere, that is noted briefly.

This file is documentation for the repository only. The site renders `site/`
and nothing outside it, so the file is never published as a page.

## Sources

| Side | What was read |
| --- | --- |
| `nrtqclib` | Version 0.1.6: its documentation (one page per QC item) and the item classes it runs, which live in `aiqclib` 0.15.2 (`prepare/features/qc_*.py`) |
| Our runs | The `nrtqclib` configuration the published data was built with, which sets the parameters per region set (Arctic, Baltic, Mediterranean), and the `nrtqclib` output itself |
| `nc2parquet` | A copy of the scripts without git history. The tests are in `source/ctd_nrtqc.py`; two drivers run them, `source/set_nrtqc.py` and `source/process_profiles.py`. Their threshold tables and region mask are in `data/NRTQC/` of the same repository, copied separately |

Only temperature and salinity are compared. `nc2parquet` also tests oxygen
and fluorescence, and its tables have rows for chlorophyll; those come up only
where they explain a defect.

## At a glance

| `nrtqclib` item | Test | In `nc2parquet` | Main difference |
| --- | --- | --- | --- |
| `impossible_date` | RTQC2 | No | Rows are filtered on the source file's own time flag instead |
| `impossible_location` | RTQC3 | No | Rows are filtered on the source file's own position flag instead |
| `position_on_land` | RTQC4 | No | A GEBCO depth is attached as a feature, never tested. Our runs do not use this item either |
| `global_range` | RTQC6 | Merged into the range test | Same global bounds, but used only as the fallback for regions without their own |
| `regional_range` | RTQC7 | Yes, a different design | Region looked up per position from a grid, not per dataset. In the Arctic the bounds are nearly the same: -1.93 °C against -1.92 °C |
| `pressure_increasing` | RTQC8 | No | Profiles are sorted by pressure, so a reversal is reordered, not flagged |
| `spike` | RTQC9 | Yes, combined with gradient | Same formula and thresholds; one column for both tests; neighbours in pressure order |
| `gradient` | RTQC11 | Yes, combined with spike | Same formula and thresholds; see `spike` |
| `digit_rollover` | RTQC12 | No | |
| `stuck_value` | RTQC13 | Yes | Same rule, but a profile needs more than 5 rows, against at least 2 values |
| `density_inversion` | RTQC14 | Yes, off by default | In-situ density instead of sigma-0, threshold 0.05 instead of 0.03, compares across gaps |
| `temp_to_psal` | rule | No | |
| (none) | | Frozen profile test, off by default | Does not compare a profile with the previous one as written |

## On our Arctic data

The rules above, applied to the 247.5 million observations of our three
Arctic datasets (`ar_ar`, `ar_gl`, `cora_ar`), with `nc2parquet` run as
`set_nrtqc.py` runs it by default: range, stuck value, and spike and
gradient, with density off. The `nrtqclib` side is its output from our runs.

### Where both run a test

`nc2parquet` first drops 1,797,790 rows that repeat a pressure in their
profile, so these counts are over the 245.7 million rows it keeps. Each cell
is a number of observations:

| Test | Variable | Both flag | Only `nrtqclib` | Only `nc2parquet` |
| --- | --- | --- | --- | --- |
| Range | Temperature | 1,579,798 | 477 | 0 |
| Range | Salinity | 1,735,487 | 0 | 0 |
| Stuck value | Temperature | 967,306 | 2,454 | 60 |
| Stuck value | Salinity | 22,844 | 1,686 | 3,569 |
| Spike or gradient | Temperature | 3,175 | 78 | 5 |
| Spike or gradient | Salinity | 32,482 | 137 | 24 |
| Density (off in `nc2parquet`) | Both | 429,624 | 577,155 | 338,747 |

Where `nc2parquet` runs a test by default, it agrees with `nrtqclib` on all
but a fraction of a percent:

* **Range:** the -1.93 against -1.92 °C lower bound (see
  [`regional_range`](#regional_range)).
* **Stuck value:** 1,057 temperature and 695 salinity profiles fail only in
  `nrtqclib`, being too short for `nc2parquet`'s more-than-5-rows rule. 5 and
  76 fail only in `nc2parquet`: profiles with a single non-null value among
  more than 5 rows, which `nrtqclib` does not judge.
* **Spike and gradient:** taking neighbours in pressure order rather than
  recorded order changes almost nothing here, since these profiles are
  recorded with increasing pressure. A re-implementation of `nrtqclib`'s rule
  reproduced its spike and gradient columns exactly, so these counts measure
  the ordering and the dropped rows, not differences in the code.

Density would disagree widely if it were turned on. In a sample (an eighth of
`ar_ar`, 10 million rows):

* 23,615 pairs fail `nc2parquet`'s rule. 10,873 also fail `nrtqclib`'s. In
  the other 12,742, `nrtqclib` has no density for one of the levels, because
  its salinity is outside [2, 40] (all but one pair); the range test flags
  that salinity already. `nc2parquet` computes a density from it anyway, so
  the temperature at that level, and both variables at the neighbouring
  level, are flagged as well. This is where most of the 338,747 come from.
* 14,901 pairs fail only `nrtqclib`'s rule. 13,769 are inversions between
  0.03 and 0.05 kg/m³, under `nc2parquet`'s threshold. 1,132 are larger, but
  hidden by the pressure term of in-situ density.

### What the items nc2parquet lacks account for

Over all 247.5 million observations. "Alone" counts the observations where
the item fails and none of range, stuck value, spike or gradient does, so
they would be good in `nc2parquet`. The alone counts overlap: an
observation can fail more than one of these items.

| `nrtqclib` item | Temperature: fail | Temperature: alone | Salinity: fail | Salinity: alone |
| --- | --- | --- | --- | --- |
| `impossible_date` | 0 | 0 | 0 | 0 |
| `impossible_location` | 0 | 0 | 0 | 0 |
| `pressure_increasing` | 1,797,790 | 1,797,759 | 1,797,790 | 1,797,567 |
| `digit_rollover` | 1,931 | 260 | 14,857 | 1,918 |
| `density_inversion` | 1,015,510 | 994,947 | 1,015,510 | 999,037 |
| `temp_to_psal` | | | 4,376,634 | 3,625,317 |

The rows `nc2parquet` drops as repeated pressures number exactly as many as
`nrtqclib`'s `pressure_increasing` failures (1,797,790), and every dropped
row has a bad `nrtqclib` flag. Where `nrtqclib` flags a repeated pressure,
`nc2parquet` removes the row.

### The final flags

| Variable | Bad in `nrtqclib` | Both | Only `nrtqclib` | Dropped by `nc2parquet` | Only `nc2parquet` |
| --- | --- | --- | --- | --- | --- |
| Temperature | 4,376,634 | 1,590,287 | 988,557 | 1,797,790 | 63 |
| Salinity | 5,406,566 | 1,777,626 | 1,831,150 | 1,797,790 | 3,547 |

`nc2parquet` as run by default marks about a third of the observations
`nrtqclib` marks bad, and almost nothing that `nrtqclib` passes. The rest
are the items it does not run:

* **Temperature:** density inversion (about 1.0 million) and the repeated
  pressures it drops (1.8 million).
* **Salinity:** the same, plus `temp_to_psal`, which copies every bad
  temperature flag to salinity, whatever test set it.

With `nc2parquet`'s density test turned on, the temperature split becomes
2,011,592 both, 567,252 only `nrtqclib` and 337,060 only `nc2parquet`; for
salinity, 2,193,610, 1,415,166 and 4,873.

## How the two run

These differences apply to every test, before any item's own logic.

### What a profile is

| | Profile identity | Vertical order |
| --- | --- | --- |
| `nrtqclib` | `platform_code` and `profile_no` | `observation_no`, the order of the source file |
| `nc2parquet`, `set_nrtqc.py` | Time, longitude and latitude; the platform is ignored | Pressure, ascending |
| `nc2parquet`, `process_profiles.py` | Platform and time | Pressure, ascending |

`set_nrtqc.py` also drops observations that repeat the time, position and
pressure of another one before testing. Two platforms reporting at the same
time and place therefore become one profile there.

Because `nc2parquet` sorts by pressure, it never sees a profile in the order
it was recorded: a pressure reversal disappears into the sort, and the spike,
gradient and density tests compare each level with its neighbours in pressure
rather than in time. `nrtqclib` keeps the recorded order and flags reversals
with `pressure_increasing`.

### Missing values

Both treat a missing value as a pass in the observation-level tests, and both
let the first and last level of a profile pass the neighbour tests. Two items
differ, and are described under [`stuck_value`](#stuck_value) and
[`density_inversion`](#density_inversion).

### Flags and the final flag

| | Flags written | Final flag per variable |
| --- | --- | --- |
| `nrtqclib` | One column per item and variable, `1` or the item's fail flag (`4` by default, `3` if configured; our runs use `4` throughout) | The maximum of the variable's item columns and the shared profile-level columns, computed from scratch; then `temp_to_psal` raises salinity where temperature is `3` or `4` |
| `nc2parquet`, `set_nrtqc.py` | One column per variable, `1` or `4` | Starts at `1`, and each enabled test raises it to `4` in turn: range, stuck value, spike and gradient, frozen profile. The density test would replace the column instead (see the defects) |
| `nc2parquet`, `process_profiles.py` | One column per test (range, stuck value, spike and gradient) plus the final column | The maximum of the three tests **and the final column already in the file**, unless the caller asks for a reset. A separate pass runs the density test and raises both temperature and salinity |

So a `process_profiles.py` final flag can carry flags from an earlier run.
`nrtqclib` never reads earlier flags into its own; the input's flags are kept
apart, for the comparison step.

### Variables

`nc2parquet` runs one variable at a time, from `TEMP`, `PSAL`, `DOX2` and
`FLU2`. Range and stuck value apply to all four, spike and gradient to all but
`FLU2`, and the frozen profile test to all but `DOX2`. `nrtqclib` checks
temperature and salinity.

## Item by item

### impossible_date

**nrtqclib.** A profile fails when its timestamp is missing, falls in a year
up to and including 1950, or lies in the future.

**nc2parquet.** Not implemented as a test. When the yearly files are
aggregated, only rows whose time flag in the source file is `1`, `2` or `5`
are kept. A row is never flagged; it is either kept or dropped, and the
judgement is the data provider's.

### impossible_location

**nrtqclib.** A profile fails when its latitude or longitude is missing or
outside [-90, 90] and [-180, 180].

**nc2parquet.** Not implemented as a test. As for dates, the aggregation keeps
only rows whose position flag in the source file is `1`, `2` or `7`.

### position_on_land

**nrtqclib.** A profile fails when the sea floor depth at its position, read
from a column added by `seastamp`, says it is not in the ocean. Our runs do
not list this item.

**nc2parquet.** Not implemented. A GEBCO 2024 depth is attached to each
position, but only as a feature for later use.

### global_range

**nrtqclib.** An observation fails when its value is outside [-2.5, 40.0] for
temperature or [2.0, 41.0] for salinity, bounds inclusive. It runs alongside
the regional range, so an observation must pass both.

**nc2parquet.** No separate test. The range table's global entry, `GLO`, has
the same bounds as `nrtqclib`'s, but it is used only for positions whose
region has no entry of its own (see below). There is no global check on top
of a region's bounds. The `ARC` bounds lie within the global ones, so for
Arctic data this makes no difference. (Elsewhere it would: the `NWS` and
`SWS` entries allow salinity down to 0, where `nrtqclib` fails anything
below 2.)

### regional_range

**nrtqclib.** The same check with a region's bounds. The region is not
detected: each configuration set has one pair of ranges, and each dataset is
assigned a set. Our runs use:

| Set | Datasets | Temperature | Salinity |
| --- | --- | --- | --- |
| Arctic | `ar_ar`, `ar_gl`, `cora_ar` | [-1.92, 25.0] | [2.0, 40.0] |
| Baltic | `bo_bo`, `cora_bo` | [-2.5, 40.0] | [2.0, 40.0] |
| Mediterranean | `mo_mo`, `mo_gl`, `cora_mo` | [10.0, 40.0] | [2.0, 40.0] |

The bounds do not depend on depth or season.

**nc2parquet.** The region is looked up per position:

1. The position is truncated to 0.1° and matched to the nearest cell of a
   **1° region mask**. The mask has one layer per variable; the temperature
   and salinity layers are identical.
2. The region's rows in the range table give the bounds. The table can hold
   several depth bands per region, and a `month` column for seasonal bounds.
   For temperature and salinity every region has a single band from the
   surface to 120,000 dbar and no month, so in practice the bounds depend on
   the region alone. (Oxygen and chlorophyll do use depth bands.)
3. An observation fails when it is outside the bounds. The bounds are
   inclusive, as in `nrtqclib`.
4. A region the table does not list gets the global entry.

The table's temperature and salinity rows that matter here:

| Region | Temperature | Salinity |
| --- | --- | --- |
| `ARC` | [-1.93, 25] | [2, 40] |
| `GLO` (fallback) | [-2.5, 40] | [2, 41] |

Against our Arctic set ([-1.92, 25.0] and [2.0, 40.0]) the only difference is
the lower temperature bound. Mapped onto the mask, every input profile of our
three Arctic datasets (881,504 profiles) falls in `ARC`, so the per-position
lookup and the per-dataset assignment pick the same bounds. Over their
248 million observations, 477 temperatures (between -1.93 and -1.92 °C) fail
in `nrtqclib` and pass in `nc2parquet`; the salinity range flags are
identical. Both sides flag about 1.58 million temperature and 1.74 million
salinity observations on range in these datasets.

Outside the Arctic the two designs would part more. The mask names nine
regions and the table has temperature and salinity rows for six, and the
Baltic (`BAL`), Black Sea (`BLA`), Iberia-Biscay-Ireland (`IBI`) and one of
the two Mediterranean codes (`MED`) have no rows and fall back to the global
bounds. Most of our Baltic profiles would also fall in `NWS`, the North West
Shelf, rather than `BAL`. This does not affect how `nc2parquet` is used.

### pressure_increasing

**nrtqclib.** In recorded order, an observation fails when its pressure equals
the previous one, or is shallower than the deepest pressure reached before
it.

**nc2parquet.** Not implemented. Every profile is sorted by pressure before
testing, and in `set_nrtqc.py` observations with a repeated pressure are
dropped, so nothing of this kind is ever flagged. In our Arctic data the
dropped rows match `nrtqclib`'s failures in number (1,797,790); see
[On our Arctic data](#on-our-arctic-data).

### spike

**nrtqclib.** For a level V2 between V1 (above) and V3 (below), the test value
is `|V2 - (V3 + V1)/2| - |(V3 - V1)/2|`. V2 fails when it exceeds the
threshold: 6.0 °C and 0.9 for salinity above 500 dbar, 2.0 °C and 0.3 from
there down. Its own column.

**nc2parquet.** The same formula, the same thresholds and the same 500 dbar
split (the table's bands are 0 to 500 and 500 to 120,000 dbar), and the same
rule for missing values and the ends of a profile. The differences:

* **One column for spike and gradient.** For temperature and salinity a level
  is flagged when it fails either test. For oxygen it has to fail both.
* **Neighbours in pressure order**, not recorded order (see above).
* The table has a resolution column, which selects nothing (see the defects).
  Temperature and salinity have one resolution each, so it makes no
  difference to them.

### gradient

**nrtqclib.** The test value is `|V2 - (V3 + V1)/2|`. V2 fails above the
threshold: 9.0 °C and 1.5 for salinity above 500 dbar, 3.0 °C and 0.5 below.
Its own column.

**nc2parquet.** The same formula and thresholds, computed in the same pass as
the spike test and written to the same column. Everything under `spike`
applies.

### digit_rollover

**nrtqclib.** An observation fails when it differs from the previous one in
the profile by more than 10 °C or 5 in salinity.

**nc2parquet.** Not implemented.

### stuck_value

**nrtqclib.** A variable fails throughout a profile when all its non-null
values are identical and there are at least `min_observations` of them (2).
Every observation of the profile gets the flag, missing ones included.

**nc2parquet.** The same idea: all non-null values identical, then the whole
profile is flagged, missing values included. The count is different:

* The threshold is 5 for temperature and salinity, and the count has to be
  **greater** than it. A profile of 2 to 5 levels is never judged, where
  `nrtqclib` judges any profile with 2 values.
* In `set_nrtqc.py` the count is the number of **rows** in the profile,
  missing values included. A profile with five identical values and one
  missing value counts as six, and fails. So does a profile with a single
  non-null value among six or more rows, which `nrtqclib` never judges.
* In `process_profiles.py` it is the largest `profile_no` in the profile. In
  `nc2parquet`'s converted files, `profile_no` is the profile's position in
  its source file, not a count of observations, so the threshold is compared
  with something unrelated. (This is from reading the code; it was not run on
  real files.)

### density_inversion

**nrtqclib.** It computes the potential density anomaly sigma-0 (UNESCO 1983)
at every level. Both levels of a consecutive pair fail when the lower one is
lighter than the upper one by more than 0.03 kg/m³. A level whose
temperature, salinity or pressure is missing has no density, so every
comparison involving it passes, and the levels on either side of it are not
compared with each other.

**nc2parquet.** The same pair rule, flagging both levels, with these
differences:

* **In-situ density, not potential density.** It uses TEOS-10 (`gsw.rho` from
  absolute salinity and conservative temperature at the level's own pressure).
  In-situ density rises with pressure by about 0.0045 kg/m³ per dbar, so part
  of any inversion is cancelled by the pressure difference between the two
  levels. The coarser the profile, the larger an inversion has to be.
* **Threshold 0.05 kg/m³**, set in the code rather than a table, against
  0.03.
* **Comparisons across gaps.** Levels with a missing temperature or salinity
  are removed before the pairs are formed, so the levels on either side of a
  gap are compared with each other. Only the fill value `-99` is excluded
  besides that.
* **Off by default** in both drivers. Through `set_nrtqc.py` it cannot run at
  all (see the defects).

The effect of the first two, on synthetic profiles at a uniform 4 °C and
salinity 34.9 with one level warmed. `1144111` means the warmed level and the
one above it fail:

| Warmed by | Level spacing | `nrtqclib` | `nc2parquet` |
| --- | --- | --- | --- |
| 0.3 °C | 1, 10 or 25 dbar | `1144111` | `1111111` |
| 0.6 °C | 1 dbar | `1144111` | `1144111` |
| 0.6 °C | 10 or 25 dbar | `1144111` | `1111111` |
| 1.0 °C | 1 or 10 dbar | `1144111` | `1144111` |
| 1.0 °C | 25 dbar | `1144111` | `1111111` |
| 1.5 °C | 1, 10 or 25 dbar | `1144111` | `1144111` |

On a three-level profile whose middle salinity is missing and whose bottom
level is lighter than its top, `nrtqclib` passes all three (`111`) and
`nc2parquet` flags the top and bottom (`414`).

### temp_to_psal

**nrtqclib.** Copies a final temperature flag of `3` or `4` to salinity.

**nc2parquet.** Not implemented. Each variable is flagged on its own; only the
density test flags both together.

## Only in nc2parquet: the frozen profile test

Meant to catch a platform that keeps sending the same profile. Each profile is
averaged into 50 dbar bins, and the absolute differences between bins are
summarised; the profile fails when at least five differences exist and their
maximum, minimum and mean are all below the thresholds. These are 0.3, 0.001
and 0.02 °C for temperature, and 0.3, 0.001 and 0.004 for salinity.

As written, the differences are taken between **neighbouring bins of the same
profile**, not between a profile and the previous one, because the shift that
should move the data one profile along moves it one bin along instead. So it
flags vertically flat profiles and misses repeated ones. On synthetic
profiles, a copy of the previous profile passed, and a nearly uniform profile
unlike its predecessor failed. It is off by default, and is also commented
out of `process_profiles.py`. Even with the shift corrected, "the previous
profile" would be the previous one in time across the whole file, not the
same platform's.

## Defects found in nc2parquet

| Defect | Effect | How it was checked |
| --- | --- | --- |
| The range fallback writes the global bounds over the **last row** of the range table, instead of adding a row | A region processed after the fallback (regions go in alphabetical order) loses its bounds for the rest of that batch, and its observations pass. With the real tables the last temperature and salinity row is `ARC`, which sorts before every region that falls back, so these two variables are not affected; Arctic positions do not fall back in any case | Run on synthetic tables: an observation of 30 °C in a region bounded at 24 °C failed alone and passed with a profile from an unlisted region in the same batch |
| The fallback reads the global entry as a single row | A global entry with more than one depth band would not work. The real `GLO` rows for temperature and salinity are single rows | From the code |
| The spike and gradient table's resolution column selects nothing | Where rows differ only in resolution, the smallest resolution's thresholds are used everywhere. No effect on temperature and salinity, which have one resolution; oxygen's shallow thresholds are always the stricter of its two | Run: a 1.5 °C spike passed at both 1 and 10 dbar spacing, against a synthetic table with 1.0 °C for 10 dbar |
| The stuck value table has depth ranges the code ignores | No effect on temperature and salinity, whose range covers the whole water column. Oxygen's stuck value threshold is applied at every depth, not only in its 800 to 1000 dbar range | From the code and the table |
| The frozen profile test shifts along bins instead of profiles | It tests flatness, not repetition | Run: see above |
| The frozen profile table's fluorescence row has its maximum (0.001) below its minimum (0.02), which looks like shifted columns | As read, a fluorescence profile fails only when every difference is below 0.001. Temperature and salinity are not affected | From the table |
| The density test cannot run through `set_nrtqc.py`: the call passes the arguments in the wrong order, and the data passed lacks the other variable's flag column | The run stops with a `KeyError`. Were it to run, its result would replace the column and drop the flags of the tests before it | Run: `KeyError: "['PSAL_NRTQC'] not in index"` |
| In `process_profiles.py` the stuck value count is the largest `profile_no` | The threshold is compared with the profile's position in its file | From the code; not run |
| In `set_nrtqc.py` a profile is time and position, without the platform | Two platforms at the same time and place are merged, and their shared pressures deduplicated | From the code |

## How this was checked

The `nc2parquet` QC modules were copied to a scratch directory outside this
repository and run on small hand-made profiles, first against synthetic
tables and a synthetic region mask, to exercise the code paths. The range
comparison over our Arctic data reproduces `nc2parquet`'s region lookup and
bounds with the real mask and table, in a separate script, rather than
running `nc2parquet`'s own range function over 248 million rows. To check
the replica, both were run on 1,500 random profiles from each of `ar_ar`,
`bo_bo`, `mo_gl` and `cora_mo` (about 1.7 million observations; the
non-Arctic datasets put fallback regions and `ARC` in the same batches): they
gave the same flag on every observation, for both variables. The counts in
[On our Arctic data](#on-our-arctic-data) re-implement `nc2parquet`'s rules
in a separate script over the full Arctic files (the range replica checked as
above; the spike and gradient code checked by reproducing `nrtqclib`'s own
columns exactly in recorded order). The `nrtqclib` side is its output from
our runs, and for the density examples, the `aiqclib` item class run with
our runs' parameters. Neither the copies nor the scripts are
kept; nothing from `nc2parquet` is in this repository except what this page
describes.
