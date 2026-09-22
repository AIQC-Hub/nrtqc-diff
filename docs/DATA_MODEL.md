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

| Key | Input flag | aiqclib flag | Meaning |
| --- | --- | --- | --- |
| `agree_bad` | in `bad_flag_values` | above 1 | Both sources flag it |
| `original_only` | in `bad_flag_values` | 1 | Only the input flags it |
| `aiqclib_only` | good or missing | above 1 | Only aiqclib flags it |
| `no_input_flag` | in `missing_flag_values`, or unreadable | 1 | Nothing to compare |
| `agree_good` | anything else | 1 | Neither flags it |

The order of the table is the order of the test. The flagged cases are
decided before the missing-flag case, so an observation aiqclib flagged is
always visible as flagged, whatever the input said. That is why
`aiqclib_only` covers both "the input called it good" and "the input said
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

## `catalog.json`

```json
{
  "generated": "2026-09-21T18:34:38+00:00",
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
              "n_observations": 1002, "n_observations_total": 1556
            }
          ]
        }
      ]
    }
  ]
}
```

The regions and products are derived from the dataset entries rather than
configured separately, so the tree cannot disagree with what was built. The
colour key lives here too: the legend, the tables and the plots all read
`statuses`, so they cannot drift apart.

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
| `n_obs` | i64 | Observations in the profile |
| `n_disagree` | i32 | Disagreements summed over variables; the default sort |
| `{variable}_n_disagree` | i32 | `original_only` plus `aiqclib_only` |
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
| `{variable}_nrt_flag` | i64 | The flag aiqclib computed |
| `{variable}_status` | str | The agreement category |
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
observations in, 430 MB out.

| File | Size | Rule of thumb |
| --- | --- | --- |
| `catalog.json` | 6 KB | Grows with the number of datasets, not their size |
| `profiles.parquet` | 8.3 MB for 357,445 profiles | About 23 bytes per published profile |
| `obs/` | 421 MB for 87.7 M observations | About 5 bytes per published observation |

`profiles.parquet` is fetched whole at startup, so it is the one file whose
size the reader waits for. The observation files are not: they are read by
range request, so what matters about them is the per-profile cost above, not
the total.

DuckDB WASM itself is the large download: about 7 MB gzipped, cached by the
browser after the first visit. That is the price of running SQL client side,
and it is why `docs/SITE.md` keeps the option of swapping in stratum-sqlite
open for small deployments.
