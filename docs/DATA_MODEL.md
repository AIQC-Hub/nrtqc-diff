# Data model

Everything the site reads is written by `uv run nrtqc-diff build` into
`site/data/`. Three kinds of file, one writer (`src/nrtqc_diff/build.py`), and
no other code in the repository writes any of them.

```
site/data/
├── catalog.json            the tree, the variables, the colour key
├── profiles.parquet        one row per published profile, all datasets
└── obs/
    ├── bal_cora_nrt.parquet   one row per published observation
    └── ...                    one file per dataset
```

The split is deliberate. `profiles.parquet` is small and is fetched once, so
the summary table is instant. Observation files are fetched only when a reader
opens a profile from that dataset, so a catalog of twenty datasets costs
nothing until it is used.

## The trimming rule

A profile is published when **at least one of its observations is flagged by
at least one source, for at least one configured variable**. Profiles that
both sources call entirely good carry no comparison and are dropped.

Trimming is by profile, never by observation. Once a profile qualifies, every
one of its observations is published, because a flagged point is only readable
next to the unflagged ones above and below it. `catalog.json` records both
numbers per dataset (`n_profiles`, `n_profiles_total`) so the site can be
honest about what was left out.

## The five agreement categories

Every observation falls into exactly one category per variable, computed at
build time in `src/nrtqc_diff/flags.py` and stored as `{variable}_status`.

| Key | Input flag | nrtqclib flag | Meaning |
| --- | --- | --- | --- |
| `agree_bad` | in `bad_flag_values` | above 1 | Both sources flag it |
| `original_only` | in `bad_flag_values` | 1 | Only the input flags it |
| `nrtqclib_only` | good or missing | above 1 | Only nrtqclib flags it |
| `no_input_flag` | in `missing_flag_values`, or unreadable | 1 | Nothing to compare |
| `agree_good` | anything else | 1 | Neither flags it |

The order of the table is the order of the test. The flagged cases are
decided before the missing-flag case, so an observation nrtqclib flagged is
always visible as flagged, whatever the input said. That is why
`nrtqclib_only` covers both "the input called it good" and "the input said
nothing".

Two per-variable lists decide the rest, because datasets differ in how they
use the scheme:

- **`bad_flag_values`** are the values that call the observation an anomaly.
- **`missing_flag_values`** are the values that say nothing about it at all:
  0 (no QC performed) and 9 (missing value) by default. These are reported as
  `no_input_flag` rather than as agreement, which matters more than it looks:
  a real NRT file uses the whole scale, and counting every 9 as "both sources
  agree this is good" would overstate agreement on exactly the observations
  nobody checked. A flag that will not parse at all, an empty string say,
  lands here too.

Anything not in either list is a judgement that the observation is usable:
1 (good), 2 (probably good), 5 (value changed) and 8 (interpolated) all count
as good. The computed flag always follows the IOC/Argo scheme, where anything
above 1 is an anomaly, so it needs no list.

`src/nrtqc_diff/flags.py` writes the rule once, as `status_predicates`, and
derives both the published `{variable}_status` column and the per-profile
counts from it.

## The data mode groups

The input carries a data mode per observation and parameter, as ctddump
writes it from the NetCDF files and `nrtqclib` passes it through: `temp_dm`,
`psal_dm` and `pres_dm`, each `R` (real time), `A` (adjusted in real time),
`D` (delayed mode) or empty. A delayed mode flag is likely to be an
operator's decision rather than the automated tests', so comparing it with
the flags `nrtqclib` computes asks a different question: not whether two
automated systems agree, but whether the automated tests anticipate what an
expert decided. The site keeps them apart.

ctddump reads the values and flags from the plain variables (`TEMP`,
`TEMP_QC`), never from `*_ADJUSTED`. It writes an empty data mode whenever
the file gives no usable one: a fill cell of a `<PARAM>_DM` variable, a
parameter marked `M` (mixed) with no per-level variable, a parameter with no
mode at all, a CORA file without `DATA_MODE`. An empty mode can therefore
hide delayed mode data.

A `<PARAM>_DM` variable is also a fill wherever the parameter itself is, so
a level with no value has an empty mode that says nothing about the file.
An empty mode is therefore counted only where the parameter it describes has
a value: `temp_dm` describes `temp`, by the name less its `_dm`.

Every published profile belongs to exactly one group, computed at build time
in `src/nrtqc_diff/data_mode.py` and stored as `data_mode`. The site offers
them as "Group":

| Key | Label | Rule |
| --- | --- | --- |
| `land` | On land | A position of the profile is at or above sea level by the bathymetry, whatever its data mode |
| `delayed` | Delayed mode | At least one observation is delayed in at least one column |
| `estimated_delayed` | Estimated delayed mode | No observation is delayed, but the flags of temperature or salinity are estimated to be delayed mode ones (below) |
| `unknown` | Data mode unknown | None is delayed, but at least one with a value is empty in at least one column, and the flags are not estimated |
| `nrt` | NRT only | Every observation is `R` or `A` in every configured data mode column, and the flags are not estimated to be delayed mode ones |

The order of the table is the order of the test: land outranks everything,
the data mode included, as in the Analyses pages, and a delayed value
decides the profile even when another cell of it is empty. Land and the
estimate are not data modes the files declare, so a dataset cannot be
configured as either. The site offers the groups in another order: NRT
only, Estimated delayed mode, Delayed mode, Data mode unknown, On land.

**On land.** The highest sea floor elevation among the profile's positions
is at or above 0 m, from `analyses.land.column` (and its sign), or
`bathymetry` without an analyses section. An input without the column has
no land profiles. Their disagreements are mostly about the position (a
station given in whole degrees, a grid cell straddling the shore), so they
are kept out of the comparisons of the other groups; the Depth page sorts
them by cause.

**Estimated delayed mode.** A profile the columns call `nrt` or `unknown`
is published as `estimated_delayed` when the flags of temperature or salinity look
like an operator's: at least `min_share` (0.6) of the variable's
observations with a usable input flag (present, and not a missing flag
value) are flagged by the input and not by nrtqclib, counting only a
variable with at least `min_observations` (10) such observations. An
automated test rarely rejects most of a profile; an operator who judges a
cast bad often rejects all of it. A profile on land is not estimated: it
is in `land` already. `estimated_from` names the variables, and the rule is
`data_mode.estimated` in the configuration (`estimated: false` turns it
off). The Analyses pages apply the same rule per variable.

"NRT only" takes positive evidence, which is why an empty mode is a group of
its own rather than a quiet NRT.

The columns and the values that mean delayed are the `data_mode` section of
the configuration, `[temp_dm, psal_dm, pres_dm]` and `[D]` in both shipped
configs. Pressure counts: a profile whose pressure alone was revised in
delayed mode was still touched by an operator. `A` is an automatic adjustment
and counts as NRT.

**A dataset can be filed whole** under one group by setting `data_mode` on
its configuration entry, whatever its columns say. The three CORA datasets
are filed as `delayed`: CORA's `DATA_MODE` is the mode of the original
observation, not of the reprocessing QC that set CORA's flags, so a CORA
profile marked `R` throughout still carries delayed mode flags.
`n_obs_delayed` and `n_obs_unknown` keep what the columns say even then.

The split is by profile, like the trimming, because a profile is what a
reader opens. On the real batch that loses very little. Delayed mode data is
in the two Arctic NRT products and one Mediterranean profile, and a profile
mixing delayed with real time observations is rare (about 1,450 of 295,000
in the largest product), nearly always one whose deepest pressures were
revised. `n_obs_delayed` keeps the number of delayed observations per
profile, so splitting "all delayed" from "mixed" needs no rebuild of the
counts if it is ever wanted.

The trimming rule does not look at the data mode: a profile is published or
not on its flags alone, and then filed under its group.

## `catalog.json`

```json
{
  "generated": "2026-09-21T18:34:38+00:00",
  "data_format": 2,
  "title": "NRT QC flag differences",
  "variables": [
    {
      "name": "temp", "label": "Temperature", "unit": "degC",
      "flag": "temp_qc", "nrt_flag": "temp_nrt_flag",
      "status_column": "temp_status",
      "bad_flag_values": [3, 4, 6, 7], "missing_flag_values": [0, 9]
    }
  ],
  "statuses": [
    { "key": "agree_bad", "label": "Both flagged",
      "color": "#3f6fb5", "flagged": true }
  ],
  "data_mode": {
    "columns": ["temp_dm", "psal_dm", "pres_dm"],
    "delayed_values": ["D"],
    "delayed_column": "delayed",
    "estimated": { "enabled": true, "min_share": 0.6, "min_observations": 10,
                   "groups": ["nrt", "unknown"], "column": "estimated_from" },
    "land_column": "bathymetry"
  },
  "data_modes": [
    { "key": "nrt", "label": "NRT only", "description": "..." },
    { "key": "estimated_delayed", "label": "Estimated delayed mode", "description": "..." },
    { "key": "delayed", "label": "Delayed mode", "description": "..." },
    { "key": "unknown", "label": "Data mode unknown", "description": "..." },
    { "key": "land", "label": "On land", "description": "..." }
  ],
  "regions": [
    {
      "name": "Baltic Sea",
      "products": [
        {
          "name": "CORA NRT",
          "datasets": [
            {
              "id": "bal_cora_nrt",
              "region": "Baltic Sea", "product": "CORA NRT",
              "label": "Baltic CORA NRT 2021", "description": "",
              "observations_file": "obs/bal_cora_nrt.parquet",
              "n_profiles": 26, "n_profiles_total": 40,
              "n_observations": 1002, "n_observations_total": 1556,
              "data_mode": null,
              "modes": {
                "nrt": { "n_profiles": 21, "n_profiles_total": 32,
                         "n_observations": 810, "n_observations_total": 1243 },
                "estimated_delayed": { "n_profiles": 1, "n_profiles_total": 1,
                                       "n_observations": 40,
                                       "n_observations_total": 40 },
                "delayed": { "n_profiles": 4, "n_profiles_total": 7,
                             "n_observations": 152, "n_observations_total": 273 },
                "unknown": { "n_profiles": 0, "n_profiles_total": 0,
                             "n_observations": 0, "n_observations_total": 0 },
                "land": { "n_profiles": 0, "n_profiles_total": 0,
                          "n_observations": 0, "n_observations_total": 0 }
              }
            }
          ]
        }
      ]
    }
  ]
}
```

`data_format` is the shape of everything described here. The published data
is built where the inputs are and uploaded to a release rather than rebuilt on
every deploy, so a set of files can outlive the code that wrote it;
`DATA_FORMAT` in `build.py` is raised whenever the published columns, file
names or catalog fields change, and `scripts/data_release.py` refuses to
publish or deploy data stamped with anything else. See
[`RELEASING.md`](RELEASING.md).

The regions and products are derived from the dataset entries rather than
configured separately, so the tree cannot disagree with what was built. The
colour key lives here too: the legend, the tables and the plots all read
`statuses`, so they cannot drift apart.

`modes` repeats the four totals of a dataset per data mode group, so a page
filtered to one group can still say how much of that group the trimming left
out; the trimmed profiles are in no parquet. The groups add up to the
dataset's own totals. `data_mode` on an entry is the group its configuration
filed it under whole, or `null` when its profiles were classified one by one. `data_modes` lists the groups in the order the site
offers them, with the words it uses, read from `DATA_MODES` in
`data_mode.py`, and `data_mode` records what decided them.

`flagged` on a status says whether at least one of the two sources flagged the
observation, which is the rule the trimming applies. It is published so a page
can select those categories without naming them, and `flags.py` builds
`is_anomaly` from the same marker, so the two cannot disagree.

## `profiles.parquet`

One row per published profile, across every dataset.

| Column | Type | Notes |
| --- | --- | --- |
| `dataset_id`, `region`, `product` | str | Which catalog entry it came from |
| `profile_id` | str | `platform_code:profile_no`, the site's selection key |
| `platform_code`, `profile_no` | str, i32 | The original key |
| `profile_timestamp`, `longitude`, `latitude`, `filename` | varies | Copied when the input carries them |
| `data_mode` | str | The group: `nrt`, `delayed`, `unknown` or `land`; see "The data mode groups" |
| `estimated_from` | str | The variables whose flags put the profile in `estimated_delayed`, comma separated (`psal`, `temp,psal`), or null |
| `n_obs` | i64 | Observations in the profile |
| `n_obs_delayed` | i32 | Observations delayed in at least one data mode column |
| `n_obs_unknown` | i32 | Observations empty in at least one data mode column whose parameter has a value |
| `n_disagree` | i32 | Disagreements summed over variables; the default sort |
| `{variable}_n_disagree` | i32 | `original_only` plus `nrtqclib_only` |
| `{variable}_n_{category}` | i32 | One column per agreement category |

The per-category columns of a variable sum to `n_obs`, which is what
`tests/test_build.py` pins.

## `obs/{dataset_id}.parquet`

One row per published observation.

| Column | Type | Notes |
| --- | --- | --- |
| `dataset_id`, `profile_id` | str | Join keys |
| `platform_code`, `profile_no`, `observation_no` | str, i32, i32 | The original key |
| `pres` | f64 | Pressure in decibars, the plots' y axis |
| `{variable}` | f64 | The measurement, the plots' x axis |
| `{variable}_qc` | i64 | The input's flag, cast to a whole number |
| `{variable}_nrt_flag` | i64 | The flag nrtqclib computed |
| `{variable}_status` | str | The agreement category |
| `temp_dm`, `psal_dm`, `pres_dm` | str | The configured data mode columns, as the input had them |
| `delayed` | bool | Whether any of them holds a delayed value |
| `qc_{item}`, `{variable}_qc_{item}` | i32 | Every per-item flag column found in the input |

The item columns are copied through rather than being selected by name, so a
dataset built with a different set of QC items needs no change here or in the
site: `site/assets/js/queries.js` discovers them with `DESCRIBE`.

Rows keep the order the input had. See "Row order" below: the site depends on
it.

## Row order, and why it matters

`obs/{dataset_id}.parquet` is written in the order the input had, which the
build requires to be non-decreasing in `platform_code`. Nothing is sorted:
sorting is the one step whose memory grows with the data, and on the largest
input here it costs 11 GB against 2.4 GB for the whole build.

The order is not cosmetic. The site reads these files over HTTP range
requests, and a parquet row group records the smallest and largest value of
each of its columns. Because the rows arrive grouped by platform, a row group
covers a narrow range of `profile_id`, so `WHERE profile_id = ...` can skip
almost every row group in the file. Measured on the largest dataset: 126 MB
in 255 row groups, and the average profile is served by 2.4 of them, so
opening one costs about 2 MB rather than 126 MB.

Shuffle the input and the file still builds correct answers, but every query
reads everything. That is why the build refuses it instead.

## Sizes

The demo (120 profiles, three datasets) produces about 55 KB of parquet.
Parquet with zstd compresses flag columns very well, since they are mostly
runs of `1`.

The `test_nrt` batch is the realistic measure: 8 datasets, 330 million
observations in, 426 MB out.

| File | Size | Rule of thumb |
| --- | --- | --- |
| `catalog.json` | 11 KB | Grows with the number of datasets, not their size |
| `profiles.parquet` | 7.9 MB for 343,431 profiles | About 23 bytes per published profile |
| `obs/` | 418 MB for 82.8 M observations | About 5 bytes per published observation |

`profiles.parquet` is fetched whole at startup, so it is the one file whose
size the reader waits for. The observation files are not: they are read by
range request, so what matters about them is the per-profile cost above, not
the total.

DuckDB WASM itself is the large download: about 7 MB gzipped, cached by the
browser after the first visit. That is the price of running SQL client side,
and it is why `docs/SITE.md` keeps the option of swapping in stratum-sqlite
open for small deployments.
