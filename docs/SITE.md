# The site

`site/` is an ordinary Quarto website with two pages of data and one of
prose. There is no build step for the JavaScript: `site/assets/js/` holds plain ES modules that
the browser loads directly.

```
site/
├── _quarto.yml           project, navbar, which folders are copied verbatim
├── index.qmd             the dashboard: sidebar, then Plots and Tables
├── summary.qmd           every region and product at once
├── about.qmd             what the comparison means
├── assets/
│   ├── dashboard.css     styling for the nq- widgets
│   └── js/
│       ├── app.js        the single module the pages import
│       ├── db.js         data access; the only file that knows about DuckDB
│       ├── queries.js    every SQL statement
│       ├── views.js      tree, tables, contingency, legend, bars
│       ├── labels.js     the column names and flag values, said in words
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

**`index.qmd` is the drill-down.** It is a Quarto dashboard with two pages of
its own, `Plots` and `Tables`, declared as level 1 headings and drawn as tabs
in the dashboard's own navbar, under the site navbar. A third level 1 heading,
`# {.sidebar}`, makes the sidebar global, so it is the same sidebar on both
pages rather than one per page. Its `title` is `Dashboard`, not the project
title: that bar sits directly under the site navbar, which already carries the
project title, so repeating it there said nothing twice.

**The sidebar is the whole selection**, and nothing else:

1. **Product**, from the tree (`treeView`). Selecting one sets
   `selection = {region, product, datasets}`. The first product selects itself
   on load, because opening on an empty panel reads as a broken page.
2. **The profile**, from a searchable paged list (`profileListView`), three
   columns wide because the sidebar is narrow. It opens on the first page,
   worst disagreement first, with a row already selected: an empty panel
   reads as a broken page.

The list is a page at a time because a product here runs to 81,541 profiles
and 81,541 rows is not a thing a browser can hold: building them takes about
six seconds and every re-sort another five, measured on a fast desktop. So
the search box, the column the rows are ordered by and the page boundaries
are all settled by the query, in `profileSummary`, with `profileCount`
supplying the size of the whole answer for the pager. Sorting a page in the
browser would sort the page and say nothing about the rest of the product,
which is why `tableView` takes an `onSortChange` handler: given one, it shows
the rows in the order it was handed and passes the click back to whoever
fetched them.

The search matches `profile_id` or `platform_code`, anywhere in the value and
either case, against the whole product rather than the page on screen. That
is what makes a profile 60,000 rows down reachable: two words instead of 120
clicks through the pager.

Putting the profile list there rather than on one of the pages is what lets a
reader switch between the plots and the tables without losing their place, and
pick a different profile from either one. The cost is that the sidebar is
380px rather than 260px, and that the full per-variable breakdown does not fit
in it; that lives on the Tables page instead, for the selected profile.

**`Plots`** is two rows. The upper one shows one plot per variable for the
selected profile, side by side: measurement on the x axis, pressure down the y
axis, markers coloured by agreement category, with pan, box zoom, scroll zoom
and double click to reset. Zooming and panning stop at the data: see Plotting.
Side by side rather than stacked because a cast is tall and narrow, so two
plots fit the page where two stacked ones do not. Past two or three variables
they wrap and the box scrolls. `.nq-plot` has a 320px minimum, which is what
decides when that happens.

The **colour key** (`statusLegend`) heads that same panel, above the markers
it explains, so no card is spent on it and nothing pushes it off the screen.
Five categories take one row on any usual window and fall into two on a narrow
one, which is what the base `.nq-legend` rule already does.

**The hover box** on a marker is where that observation says what happened to
it. Both flag columns are named the way the contingency tables name them, in
words with the column name beside them in grey, and each value carries the
word the IOC/Argo scheme gives it: `temp_qc = 4, temp_nrt_flag = 3`, which is
what the box used to say, needs the schema to be read at all.

Under those two lines the box names the checks behind the computed flag. That
flag is the most severe flag among the checks that apply to the variable, so
the checks holding its value are listed as having set it, and anything else
that fired is listed under them with its own flag, which is how a reader sees
that the spike test called a point bad while the gradient test only called it
probably good. `firedChecks` in `labels.js` does the splitting, off the item
columns of the observation row that `SELECT *` has already brought back, so
the hover costs no query. The label itself is a white box with the category's
colour as its border rather than that colour behind the text: five lines of
white on crimson is not a paragraph anybody reads.

The plots have no height of their own. `.nq-profile` is a column, `.nq-plots`
takes what the key leaves, and `.nq-plot` is the full height of that, with a
200px floor under which the box scrolls instead. Plotly is created responsive
and `fitToBox` watches the node, so the plots follow the panel: a taller
window means taller plots, not more white space.

The lower row is **`aiqclib QC checks that flagged`** (`profileItemBreakdown`):
which of `aiqclib`'s own checks raised a flag on this profile, and on how many
observations. It answers the question the plots raise, which is why it sits
under them rather than with the tables. The code calls this an item firing;
the panel says it in words a reader who has never seen the code can follow.
The check is named twice over, in words and as the column it writes under
them, and the name links to the table on the About page that describes it.

`aiqclib` is in the title because the plots beside it draw both sources at
once, and a table of flag values headed `QC check` could be read as either of
them.

There is one table per variable, side by side on the same terms as the
contingency tables, because a single list left the reader matching `psal_`
prefixes by eye against the plot they were looking at. A check that judges the
whole profile, its date or its pressure feeds both computed flags, so it is
listed under both variables rather than in a third table off to the side, and
the line above says so. Splitting the tables is also what makes room for the
column name: with the variable in the heading above the table, the check name
and the column it writes fit in one column of it.

**`Tables`** shows the same profile as numbers: a summary of the selected
profile, then the contingency tables. The summary is one row per variable and
one column per agreement category, over that profile alone, with the flagged
categories also drawn as a bar. Neither table selects anything, because the
sidebar does: the sidebar is the product's profile list, and listing every
profile here as well spent the top of the page on rows the reader had not
asked about while pushing their own profile's counts out to the edge.

The **colour key** sits down the right of the summary panel. The bar is the
only thing on this page that says anything in colour, and its key was a tab
away. It is beside the table rather than above it because what a table of two
rows leaves spare is width at its side, not height over it, and it is a
column rather than a row for the same reason. The two do not wrap: a narrow
window takes the width out of the table, which scrolls sideways in the box it
already has, rather than dropping the key under a panel too short to show it.

Everything downstream of a selection is an Observable cell, so a click
re-evaluates exactly the cells that depend on it and nothing else. Both pages
are in the DOM at once and the tabs only show and hide them, so a cell on the
hidden page still updates.

The **contingency tables** are the cross-tabulation the project is named for,
one per variable, side by side while the window has room for both and stacked
when it does not. Both axes carry a name in words with the column name under
it, every flag value carries the word the IOC/Argo scheme gives it, and the
line above the tables says what the red means, because that one is a setting
of the build rather than anything the scheme fixes.

**`about.qmd` is the prose page**, and carries one thing worth keeping
current: a table of the `aiqclib` QC checks, one row per check, with the
column name it writes. The `aiqclib QC checks that flagged` panel shows those
same column names and links to this table, which is how a reader turns
`temp_qc_spike` into a sentence. It is a summary of the `aiqclib` NRT QC guide
it links to; if that library adds or renames a check, this table is the place
in this repository that goes stale, together with `QC_CHECKS` in `labels.js`.

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

**Names in words.** Nothing on a page shows a column name or a flag number on
its own. `labels.js` holds the words: what the IOC/Argo scheme calls a flag
value, what a per-item QC column is called in English, and what this product
does with one input flag value, which comes from the variable entry rather
than from the scheme because `bad_flag_values` is a build setting. The column
name stays visible beside the words: it is what the team who ran the build
thinks in, and it is what a bug report has to quote.

**Styling against the theme.** Quarto tags every `<button>` it finds in a
cell's output with `btn btn-quarto`, so Bootstrap's button colours apply to
anything `views.js` builds. A widget that does not state its own `color` gets
the theme's, which is a near-white meant for a dark fill: the pager's steps
were drawn invisible on white, and the disabled one, which `.btn:disabled`
fills dark grey, was the only one a reader could see. Any button here states
its colour, its background and its disabled look.

**Sidebar height.** The sidebar is a flex column, but Quarto sizes each cell's
output to its content, so a tall tree and a page of profiles added up to more
than the panel and pushed the pager past the bottom edge. `dashboard.css`
gives the tree a share of the height (45%, scrolling inside itself beyond it)
and the profile list the rest, which is what keeps the pager on screen at any
window size without the sidebar scrolling.

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

### Zoom limits

Out of the box Plotly lets you zoom and pan without end, and one scroll too
many leaves the reader looking at an empty frame with the cast a speck in the
corner. Both axes are therefore bounded to the profile's own data plus a 5%
margin, through `minallowed` and `maxallowed` (Plotly 2.27 and later, so mind
the pinned version in `fetch_assets.sh` if it is ever moved backwards). The
margin matters: with the limits set exactly at the data, the initial autorange
is clamped to it and the deepest marker is drawn on the axis.

Plotly applies those limits one edge at a time, which is fine for zooming and
wrong for panning: drag past a limit and the edge against it is held while the
far edge keeps coming, so the pan quietly becomes a zoom and an axis already
showing everything creeps inwards on every drag. `holdSpanWhileDragging` in
`plots.js` defends the span, which is the thing a pan must not change. It
watches drags of the pan tool only, because a box zoom is a drag that narrows
the range deliberately, and a scroll wheel arrives with no button down.

The limits are derived per profile rather than configured. A plausible-range
setting per variable would be a different feature: this one is about not
losing the cast off the edge of the frame.

## Checking a change

The site has no test runner. After a change:

1. `quarto render site` must exit 0.
2. Open the rendered pages. On the dashboard, walk the three levels: pick each
   product, pick a profile with disagreements, confirm both plots draw on
   `Plots` and that `Tables` shows the same profile, with its row marked in
   the table and the contingency totals matching it. On the summary page,
   switch the variable and confirm the counts change with it.
3. The browser console must be clean. DuckDB's own logger is chatty at info
   level; anything at error level is a real problem.
