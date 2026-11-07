# NRT QC in nc2parquet and nrtqclib

`nc2parquet` is an earlier, private set of scripts from the AIQC project. It
converts NetCDF profile data to parquet, and it also has its own
implementation of the NRT QC tests. This document compares those tests with
the QC items of `nrtqclib`, the library whose flags this dashboard shows. For
each `nrtqclib` item it covers two things: whether `nc2parquet` implements the
item at all, and how the logic differs where it does.

This file is documentation for the repository only. The site renders `site/`
and nothing outside it, so the file is never published as a page.

## Sources

| Side | What was read |
| --- | --- |
| `nrtqclib` | Version 0.1.6: its documentation (one page per QC item) and the item classes it runs, which live in `aiqclib` 0.15.2 (`prepare/features/qc_*.py`) |
| Our runs | The `nrtqclib` configuration the published data was built with, which sets the parameters per region (Arctic, Baltic, Mediterranean) |
| `nc2parquet` | A copy of the scripts without git history. The tests are in `source/ctd_nrtqc.py`. Two drivers run them: `source/set_nrtqc.py` and `source/process_profiles.py` |

The threshold tables the `nc2parquet` tests read (`data/NRTQC/`: a range
table, a spike and gradient table, a frozen profile table and a gridded region
mask) are not part of the copy. So this document compares the logic, but the
`nc2parquet` threshold values are unknown. The defects listed below were
reproduced by running the `nc2parquet` functions on synthetic profiles with
made-up tables (see [How this was checked](#how-this-was-checked)).

## At a glance

| `nrtqclib` item | Test | In `nc2parquet` | Main difference |
| --- | --- | --- | --- |
| `impossible_date` | RTQC2 | No | Rows are filtered on the source file's own time flag instead |
| `impossible_location` | RTQC3 | No | Rows are filtered on the source file's own position flag instead |
| `position_on_land` | RTQC4 | No | A GEBCO depth is attached as a feature, never tested. Our runs do not use this item either |
| `global_range` | RTQC6 | Merged into the range test | One test with a global fallback |
| `regional_range` | RTQC7 | Yes, a different design | Region looked up from a grid; bounds depend on depth (and possibly month) |
| `pressure_increasing` | RTQC8 | No | Profiles are sorted by pressure, so a reversal is reordered, not flagged |
| `spike` | RTQC9 | Yes, combined with gradient | Same formula; one column for both tests; neighbours in pressure order |
| `gradient` | RTQC11 | Yes, combined with spike | Same formula; see `spike` |
| `digit_rollover` | RTQC12 | No | |
| `stuck_value` | RTQC13 | Yes | Same rule; the observation count is taken differently |
| `density_inversion` | RTQC14 | Yes, off by default | In-situ density instead of sigma-0, threshold 0.05 instead of 0.03, compares across gaps |
| `temp_to_psal` | rule | No | |
| (none) | | Frozen profile test, off by default | Does not compare a profile with the previous one as written |

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
temperature and salinity. Oxygen and fluorescence are out of scope here.

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
temperature or [2.0, 41.0] for salinity, bounds inclusive.

**nc2parquet.** No separate test. The single range test below falls back to a
global entry (`GLO`) of its table for any region the table does not list.
There is no global check on top of the regional one.

### regional_range

**nrtqclib.** The same check with a region's bounds. The region is not
detected: each configuration set has one pair of ranges, and each dataset is
assigned a set. Our runs use:

| Set | Temperature | Salinity |
| --- | --- | --- |
| Arctic | [-1.92, 25.0] | [2.0, 40.0] |
| Baltic | [-2.5, 40.0] | [2.0, 40.0] |
| Mediterranean | [10.0, 40.0] | [2.0, 40.0] |

The bounds do not depend on depth or season.

**nc2parquet.** The region is looked up per position:

1. The position is truncated to 0.1° and matched to the nearest cell of a
   gridded region mask, which has one layer per variable.
2. The region's rows in the range table give bounds per **depth band**: each
   row holds a maximum depth with a minimum and maximum value. The table may
   also have a **month** column, in which case the bands are per month.
3. The pressure is rounded to a whole decibar, and the observation takes the
   band with the smallest maximum depth that is still greater than its
   pressure.
4. An observation fails when it is outside that band's bounds. The bounds are
   inclusive, as in `nrtqclib`.

Differences beyond the design:

* A level at or below the deepest band of its region has no bounds and
  passes.
* A position in no named region of the mask, or in a region the table does
  not list, uses the global entry. See the defects for what this does to the
  rest of the table.
* The values are unknown, since the table is not in the copy.

### pressure_increasing

**nrtqclib.** In recorded order, an observation fails when its pressure equals
the previous one, or is shallower than the deepest pressure reached before
it.

**nc2parquet.** Not implemented. Every profile is sorted by pressure before
testing, and in `set_nrtqc.py` observations with a repeated pressure are
dropped, so nothing of this kind is ever flagged.

### spike

**nrtqclib.** For a level V2 between V1 (above) and V3 (below), the test value
is `|V2 - (V3 + V1)/2| - |(V3 - V1)/2|`. V2 fails when it exceeds the
threshold: 6.0 °C and 0.9 for salinity above 500 dbar, 2.0 °C and 0.3 from
there down. Its own column.

**nc2parquet.** The same formula, and the same rule for missing values and the
ends of a profile. The differences:

* **One column for spike and gradient.** For temperature and salinity a level
  is flagged when it fails either test. For oxygen it has to fail both.
* **Thresholds per depth band from a table.** Rows have a minimum depth, a
  maximum depth and a resolution. A level takes the threshold of the band
  with the smallest maximum depth greater than its pressure; a level below
  the deepest band has no threshold and passes.
* **The resolution column does not select anything.** The profile's vertical
  spacing is never computed. Where the table has rows that differ only in
  resolution, the row with the smallest resolution is used for every profile.
* **Neighbours in pressure order**, not recorded order (see above).

### gradient

**nrtqclib.** The test value is `|V2 - (V3 + V1)/2|`. V2 fails above the
threshold: 9.0 °C and 1.5 for salinity above 500 dbar, 3.0 °C and 0.5 below.
Its own column.

**nc2parquet.** The same formula, computed in the same pass as the spike test
with the same depth bands, and written to the same column. Everything under
`spike` applies.

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

* It is compared with **"greater than"** the threshold, where `nrtqclib` uses
  "at least".
* In `set_nrtqc.py` it is the number of **rows** in the profile, missing
  values included. A profile with three identical values and one missing value
  counts as four.
* In `process_profiles.py` it is the largest `profile_no` in the profile. In
  `nc2parquet`'s converted files, `profile_no` is the profile's position in
  its source file, not a count of observations, so the threshold is compared
  with something unrelated. (This is from reading the code; it was not run on
  real files.)
* The threshold value is in the table, so unknown.

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
* **Threshold 0.05 kg/m³** by default, against 0.03.
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
maximum, minimum and mean are all below the table's thresholds.

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
| The range fallback writes the global bounds over the **last row** of the range table, instead of adding a row | When a position falls in an unlisted region, the table's last region loses its bounds for the rest of that batch and its observations pass. A region processed earlier (regions go in alphabetical order) is not affected | Run: an observation of 30 °C in a region bounded at 24 °C failed alone and passed with a profile from an unlisted region in the same batch |
| The fallback reads the global entry as a single row | A global entry with more than one depth band would not work | From the code; not run |
| The spike and gradient table's resolution column selects nothing | The smallest resolution's thresholds are used everywhere | Run: a 1.5 °C spike passed at both 1 and 10 dbar spacing, against a table with 1.0 °C for 10 dbar |
| The frozen profile test shifts along bins instead of profiles | It tests flatness, not repetition | Run: see above |
| The density test cannot run through `set_nrtqc.py`: the call passes the arguments in the wrong order, and the data passed lacks the other variable's flag column | The run stops with a `KeyError`. Were it to run, its result would replace the column and drop the flags of the tests before it | Run: `KeyError: "['PSAL_NRTQC'] not in index"` |
| In `process_profiles.py` the stuck value count is the largest `profile_no` | The threshold is compared with the profile's position in its file | From the code; not run |
| In `set_nrtqc.py` a profile is time and position, without the platform | Two platforms at the same time and place are merged, and their shared pressures deduplicated | From the code |

## How this was checked

The `nc2parquet` QC modules were copied to a scratch directory outside this
repository, next to synthetic threshold tables and a synthetic region mask (an
Arctic, a shelf and an unlisted region), and run on small hand-made profiles.
The `nrtqclib` side was run with the `aiqclib` item classes and our runs'
parameters. Neither the copies nor the scripts are kept; nothing from
`nc2parquet` is in this repository except what this page describes. With the
real tables, the threshold comparisons above could be filled in, and the
defects would need checking again against them.
