# The site

`site/` is an ordinary Quarto website with two pages of data and one of
prose. There is no
build step for the JavaScript: `site/assets/js/` holds plain ES modules that
the browser loads directly.

```
site/
├── _quarto.yml           project, navbar, which folders are copied verbatim
├── index.qmd             the dashboard: one product, one profile at a time
├── summary.qmd           every region and product at once
├── about.qmd             what the comparison means
├── assets/
│   ├── dashboard.css     styling for the nq- widgets
│   └── js/
│       ├── app.js        the single module the pages import
│       ├── db.js         data access; the only file that knows about DuckDB
│       ├── queries.js    every SQL statement
│       ├── views.js      tree, tables, contingency, legend
│       └── plots.js      the profile plots
├── data/                 written by the build step  (gitignored)
├── libs/                 vendored by scripts/fetch_assets.sh  (gitignored)
└── _site/                rendered output  (gitignored)
```

## Rendering

```bash
bash scripts/serve.sh      # quarto preview with live reload
bash scripts/serve.sh --static   # render once, serve with range requests
quarto render site         # one-shot render into site/_site
```

`serve.sh` refuses to start when `site/data/` or `site/libs/` is missing and
says which command to run. Both are generated, so a fresh clone needs
`nrtqc-diff build` and `fetch_assets.sh` before it can render anything useful.

## The two pages

**`summary.qmd` is the way in.** One row per region and product, over every
published profile: how much of each source dataset survived the trimming, and
how the two flag sources line up across what did. It answers "which product is
worth opening" before the reader has to guess, and it is the only page that
compares products against each other. Everything on it comes from
`profiles.parquet`, which carries `region` and `product` as columns, so a
product built from several datasets aggregates in SQL rather than in the page;
the totals the trimming was measured against come from `catalog.json`.

The counts there cover published profiles only. That is why "neither flagged"
is the largest column on the page, and why the last column is a rate per 1,000
published observations: it is the only figure on the row that compares fairly
between a product of 800 observations and one of 25 million.

**`index.qmd` is the drill-down**, one page holding three levels, driven by
two selections.

1. **Product**, from the sidebar tree (`treeView`). Selecting one sets
   `selection = {region, product, datasets}`. The first product selects itself
   on load, because opening on an empty panel reads as a broken page.
2. **Profile**, from the summary table (`tableView` with `autoSelect: true`).
   One row per published profile of the selected product, worst disagreement
   first.
3. **Observations**, shown as contingency tables, the QC items that fired, and
   one plot per variable: measurement on the x axis, pressure down the y axis,
   markers coloured by agreement category, with pan, box zoom, scroll zoom and
   double click to reset.

Everything downstream of a selection is an Observable cell, so a click
re-evaluates exactly the cells that depend on it and nothing else.

## Conventions

**Views.** Everything in `views.js` returns a DOM node with a `value` property
that emits an `input` event when it changes, which is the contract Quarto's
`viewof` needs. Nothing in `views.js` queries the database: the page passes in
rows and gets a node back.

**Imports.** Observable cells can only load plain JavaScript through dynamic
`import()`, so each page imports `app.js` once and everything else hangs off
it. The specifier is resolved against `document.baseURI` so the page works
under a GitHub Pages project subfolder as well as at a domain root. Inside the
modules, paths are resolved from `import.meta.url` for the same reason.

**SQL.** Every statement lives in `queries.js`, and the page reads as layout.
Values interpolated into SQL go through `literal()` even when they came from
our own catalog.

**Colours and categories** are read from `catalog.json`, never hard coded.
Adding a category in `flags.py` makes it appear in the legend, the plots and
both summary tables without touching the site. That extends to which
categories mean a source flagged something: the build publishes a `flagged`
marker on each category, and `summary.qmd` filters on it rather than naming
`agree_bad`, `original_only` and `aiqclib_only`.

## The data access seam

`db.js` is the only file that knows how the data is read. It currently uses
[stratum-duckdb](https://github.com/stratum-toolkit/stratum-duckdb): DuckDB
compiled to WebAssembly, querying parquet in the browser.

It reads the two kinds of file differently, on purpose:

- **`profiles.parquet` is fetched whole**, once, when the page opens. Every
  query over it sorts or sums across all of its rows, so reading it in pieces
  would fetch the same bytes in more round trips. It is 8 MB for the real
  batch, and it is the one download the reader waits for.
- **`obs/{id}.parquet` are registered as remote views**, so DuckDB reads them
  with HTTP range requests and fetches only the row groups a query needs.
  These files run to 126 MB; opening one profile costs about 2 MB of them.
  The build makes that possible by writing them in platform order, which
  `docs/DATA_MODEL.md` explains.

This needs a server that answers range requests. GitHub Pages does.
`quarto preview` does not, so against real data use
`bash scripts/serve.sh --static`, which renders once and serves the result
with a server that does.

Swapping in
[stratum-sqlite](https://github.com/stratum-toolkit/stratum-sqlite) means
rewriting `db.js` and the build's writers, and nothing else, as long as
`query()` keeps returning an array of plain objects. That is the reason for
the seam, so here is the trade-off recorded while it is fresh:

| | stratum-duckdb (chosen) | stratum-sqlite |
| --- | --- | --- |
| Runtime download | About 7 MB gzipped | About 1 MB |
| Data format | Parquet, columnar | One SQLite file |
| Large datasets | HTTP range requests fetch only the needed row groups | The whole file is downloaded first |
| SQL | `UNPIVOT`, window functions, `PIVOT` | Narrower |
| Offline | Loads its main module from jsDelivr at runtime | Fully self-hosted |

DuckDB wins here because observation-level data grows quickly, because the
per-dataset split lets a reader pay only for what they open, and because the
QC item breakdown is an `UNPIVOT` over a column set discovered at runtime.
The real batch settles the first two: 430 MB of observations, read a couple
of megabytes at a time. Reconsider if a deployment's published data settles
below roughly 20 MB in total and the 7 MB engine starts to dominate, or if
the site has to work with no network at all: DuckDB always fetches its main
module from jsDelivr, even when the binary companions are self-hosted.

## Plotting

Plotly is used for one reason: pan, box zoom, scroll zoom and reset come with
it, and a QC reader spends most of the time zooming into a few decibars around
a flagged point. Observable Plot renders nicer static figures but has no
built-in zoom, which would have to be rebuilt with `d3-zoom`.

It is vendored locally by `scripts/fetch_assets.sh` and loaded through a
script tag rather than `import()`, because the distributed bundle is UMD.
`loadPlotly()` does this once per page and fails with the command to run when
the file is missing.

## Checking a change

The site has no test runner. After a change:

1. `quarto render site` must exit 0.
2. Open the rendered pages. On the dashboard, walk the three levels: pick each
   product, pick a profile with disagreements, confirm both plots draw and the
   contingency table's totals match the summary row. On the summary page,
   switch the variable and confirm the counts change with it.
3. The browser console must be clean. DuckDB's own logger is chatty at info
   level; anything at error level is a real problem.
