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

## The configuration

`config/datasets.yaml`, with paths resolved relative to the file itself so a
config can be moved along with its data.

```yaml
site:
  title: NRT QC flag differences   # shown in the site header
  data_dir: ../site/data           # where the published files go

variables:
  - name: temp                     # the measurement column
    label: Temperature             # shown in the site
    unit: degC                     # shown on the plot axis
    flag: temp_qc                  # the flag that came with the input
    nrt_flag: temp_nrt_flag        # the flag aiqclib computed
    bad_flag_values: [3, 4, 6, 7]  # input values counted as an anomaly

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
- **`bad_flag_values` applies to the input flag only.** The computed flag
  always follows the IOC/Argo scheme, where anything above 1 is an anomaly.
- **Several datasets may share a region and product.** The tree shows one
  entry and the summary table covers all of them, with `dataset_id` telling
  them apart.
- **Every configured column must exist** in every dataset. A missing column is
  an error naming all of the missing ones at once, not a silent skip: a
  mistyped variable name usually breaks three columns together, and finding
  them one run at a time is tedious.

## Input requirements

The input is whatever `aiqclib`'s NRT QC `concat` step wrote. Beyond the
configured variables and their two flag columns, the build needs
`platform_code`, `profile_no`, `observation_no` and `pres`. It copies
`profile_timestamp`, `longitude`, `latitude` and `filename` when they are
there and works without them.

Per-item QC flag columns (`qc_impossible_date`, `temp_qc_spike`, and so on)
are discovered by name rather than configured, so a dataset built with a
different set of QC items needs no change to the configuration.

## Working without real data

```bash
uv run python scripts/make_demo_data.py
```

writes three synthetic datasets under `data/demo/`, in exactly the schema an
`aiqclib` run produces, seeded so that every agreement category and both
region and product levels have something to show. No real observation is
involved. This is what `config/datasets.yaml` points at as shipped, and it is
the fastest way to see the site working before wiring up your own files.

## Extending the build

| Change | Where |
| --- | --- |
| A new agreement category | `flags.py`: add to `STATUSES` and to `status_expression` |
| A new per-profile statistic | `build.py`: `_profile_frame` and its column list |
| A new published column | `build.py`: `_observation_frame` |
| A new catalog field | `build.py`: `_catalog` |

Adding to `STATUSES` is enough to make a category appear in the legend, the
plots, the product totals and the summary table: all four read the list from
`catalog.json` rather than hard coding it. Document the new column in
`DATA_MODEL.md` in the same commit; that file is the contract the site reads
against.
