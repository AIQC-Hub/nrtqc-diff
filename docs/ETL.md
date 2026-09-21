# The build step

`nrtqc-diff build` is the whole Python side of the project. It reads `aiqclib`
NRT QC output, applies the trimming rule, and writes the files documented in
[`DATA_MODEL.md`](DATA_MODEL.md).

```bash
uv run nrtqc-diff build -c config/datasets.yaml
uv run nrtqc-diff build -c config/datasets.yaml -o /tmp/try   # elsewhere
uv run nrtqc-diff build -c config/datasets.yaml -q            # errors only
```

The build is a pure function of the configuration plus the input files. It
never appends and never reads what a previous run wrote, so deleting
`site/data/` and rebuilding is always safe and always enough.

`config/test_nrt.yaml` is the same thing against the real `aiqclib` batch
under `data/`: 8 datasets, 330 million observations, about two minutes and
430 MB of output.

## How one dataset is built

Nothing holds a whole dataset in memory, because an input can carry a hundred
million observations. Each one is read twice under polars' streaming engine:

1. **The profile pass** groups every observation by `platform_code` and
   `profile_no`, and comes back with one row per profile: its observation
   count, the per-category counts the summary table shows, and whether the
   trimming rule keeps it. One row per profile is small enough to collect,
   whatever the input was, and it answers `n_profiles_total` and
   `n_observations_total` on the way past.
2. **The observation pass** rescans, keeps the qualifying profiles with a
   semi-join against that list, and streams the result straight into
   `obs/{id}.parquet` without collecting it.

Two rules keep this bounded, and both are easy to undo by accident:

- **Nothing is sorted.** The build requires the input to be non-decreasing in
  `platform_code` and preserves that order. A global sort of the observation
  pass costs 11 GB on the largest dataset here, against 2.4 GB for the whole
  build. `docs/DATA_MODEL.md` explains what the order buys the site.
- **No `is_in` inside an aggregation.** `flags.py` tests flag values with a
  chain of equalities, through `_is_one_of`, because `is_in` in a group-by
  makes the streaming engine materialise the whole input: 14 GB against
  0.9 GB, for the same answer. The comment there says so; keep it.

## The configuration

`config/datasets.yaml`, with paths resolved relative to the file itself so a
config can be moved along with its data.

```yaml
site:
  title: NRT QC flag differences   # shown in the site header
  data_dir: ../site/data           # where the published files go
  row_group_size: 100000           # rows per parquet row group, optional

variables:
  - name: temp                     # the measurement column
    label: Temperature             # shown in the site
    unit: degC                     # shown on the plot axis
    flag: temp_qc                  # the flag that came with the input
    nrt_flag: temp_nrt_flag        # the flag aiqclib computed
    bad_flag_values: [3, 4, 6, 7]  # input values counted as an anomaly
    missing_flag_values: [0, 9]    # input values that say nothing at all

datasets:
  - id: bal_cora_nrt               # lower case, digits, underscores only
    region: Baltic Sea             # first level of the sidebar tree
    product: CORA NRT              # second level
    label: Baltic CORA NRT 2021    # shown for the dataset itself
    description: One sentence.     # optional
    path: ../data/nrt_qc/bal.parquet
```

Notes worth knowing:

- **`id` is restricted** to lower case letters, digits and underscores. It
  becomes an SQL view name and a file name in the browser;
  `site/assets/js/db.js` enforces the same rule from the other side.
- **`bad_flag_values` and `missing_flag_values` apply to the input flag
  only.** The computed flag always follows the IOC/Argo scheme, where
  anything above 1 is an anomaly. The two lists may not overlap, and a value
  in neither counts as good; see `docs/DATA_MODEL.md`.
- **`row_group_size` is what a profile lookup costs.** The site fetches
  observation files by range request, so opening a profile reads the parquet
  footer plus the row group holding it. Smaller row groups mean a bigger
  footer, not a cheaper read: the total is smallest in the middle, near
  120,000 rows for a dataset of 25 million observations. Leave it alone
  unless you have measured.
- **Several datasets may share a region and product.** The tree shows one
  entry and the summary table covers all of them, with `dataset_id` telling
  them apart.
- **Every configured column must exist** in every dataset. A missing column is
  an error naming all of the missing ones at once, not a silent skip: a
  mistyped variable name usually breaks three columns together, and finding
  them one run at a time is tedious.

## Input requirements

The input is whatever `aiqclib`'s NRT QC `concat` step wrote, which is the
`nrt_qc/nrt_qc_output.parquet` of a run, not the `qc/nrt_qc_flags.parquet`
beside it: the latter is the same rows before the roll-up flag columns were
added. Beyond the configured variables and their two flag columns, the build
needs `platform_code`, `profile_no`, `observation_no` and `pres`. It copies
`profile_timestamp`, `longitude`, `latitude` and `filename` when they are
there and works without them.

The input must be **ordered by `platform_code`**, which an `aiqclib` run
already is. The build checks this and stops with a message naming the dataset
rather than publishing a file that is expensive to query.

Per-item QC flag columns (`qc_impossible_date`, `temp_qc_spike`, and so on)
are discovered by name rather than configured, so a dataset built with a
different set of QC items needs no change to the configuration.

## Working without real data

```bash
uv run python scripts/make_demo_data.py
```

writes three synthetic datasets under `demo-data/`, in exactly the schema an
`aiqclib` run produces, seeded so that every agreement category and both
region and product levels have something to show. No real observation is
involved. This is what `config/datasets.yaml` points at as shipped, and it is
the fastest way to see the site working before wiring up your own files.

## Extending the build

| Change | Where |
| --- | --- |
| A new agreement category | `flags.py`: add to `STATUSES`, with its `flagged` marker, and to `status_predicates` |
| A new per-profile statistic | `build.py`: `_profile_frame` and its column list |
| A new published column | `build.py`: `_observation_frame` |
| A new catalog field | `build.py`: `_catalog` |

Adding to `STATUSES` is enough to make a category appear in the legend, the
plots and both summary tables: they all read the list from `catalog.json`
rather than hard coding it. Set its `flagged` marker honestly while you are
there: `is_anomaly` is built from the marked categories, so it decides which
profiles are published, not just how the summary page draws its bars. Document the new column in
`DATA_MODEL.md` in the same commit; that file is the contract the site reads
against.
