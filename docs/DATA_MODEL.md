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

## The two data mode groups

The input carries a data mode per observation and parameter, as ctddump
writes it from the NetCDF files and `nrtqclib` passes it through: `temp_dm`,
`psal_dm` and `pres_dm`, each `R` (real time), `A` (adjusted in real time),
`D` (delayed mode) or empty. A delayed mode flag is likely to be an
operator's decision rather than the automated tests', so comparing it with
the flags `nrtqclib` computes asks a different question: not whether two
automated systems agree, but whether the automated tests anticipate what an
expert decided. The site keeps the two apart.

Every published profile belongs to exactly one group, computed at build time
in `src/nrtqc_diff/data_mode.py` and stored as `data_mode`:

| Key | Label | Rule |
| --- | --- | --- |
| `nrt` | NRT only | No observation is delayed in any configured data mode column |
| `delayed` | Contains delayed mode | At least one observation is |

The columns and the values that mean delayed are the `data_mode` section of
the configuration, `[temp_dm, psal_dm, pres_dm]` and `[D]` in both shipped
configs. Pressure counts: a profile whose pressure alone was revised in
delayed mode was still touched by an operator. `A` is an automatic adjustment
and counts as NRT, and an empty cell says nothing either way.

The split is by profile, like the trimming, because a profile is what a
reader opens. On the real batch that loses very little. Delayed mode data is
in the two Arctic NRT products and one Mediterranean profile, and a profile
mixing delayed with real time observations is rare (about 1,450 of 295,000
in the largest product), nearly always one whose deepest pressures were
revised. `n_obs_delayed` keeps the number of delayed observations per
profile, so a three way split (all delayed, mixed, NRT) needs no rebuild of
the counts if it is ever wanted.

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
    "delayed_column": "delayed"
  },
  "data_modes": [
    { "key": "nrt", "label": "NRT only", "description": "..." },
    { "key": "delayed", "label": "Contains delayed mode", "description": "..." }
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
              "modes": {
                "nrt": { "n_profiles": 21, "n_profiles_total": 32,
                         "n_observations": 810, "n_observations_total": 1243 },
                "delayed": { "n_profiles": 5, "n_profiles_total": 8,
                             "n_observations": 192, "n_observations_total": 313 }
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
dataset's own totals. `data_modes` lists the groups in the order the site
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
| `data_mode` | str | `nrt` or `delayed`; see "The two data mode groups" |
| `n_obs` | i64 | Observations in the profile |
| `n_obs_delayed` | i32 | Observations delayed in at least one data mode column |
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
