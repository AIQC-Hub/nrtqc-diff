# Releasing and deploying

Two things can be released here and they move independently: the **code**
(this repository) and the **published site** (the rendered pages plus the data
they read).

## Code

Semantic versioning, in `pyproject.toml`. Choose the number from what
`CHANGELOG.md` has accumulated: any `### Added` entry means the minor version
goes up, and a patch bump is for `### Fixed` and `### Changed` only.

1. `uv run ruff check src tests scripts` and `uv run pytest`.
2. `quarto render site` must exit 0.
3. Move the `## [Unreleased]` entries under a new `## [x.y.z] - YYYY-MM-DD`
   heading, leaving `## [Unreleased]` empty above it.
4. Bump `version` in `pyproject.toml`, then `uv sync` to update `uv.lock`.
5. Commit on a `release/x.y.z` branch, merge into `main` and back into
   `develop` with `--no-ff`, tag `vx.y.z` on `main`.
6. Delete the release branch.

## Site

The site is static, so deploying is copying `site/_site/` to a host. The
workflow in `.github/workflows/pages.yml` does it on every push to `main`:
build the data, vendor the browser libraries, render, publish to GitHub Pages.

### The first ten minutes after a deploy

GitHub Pages serves every file with `cache-control: max-age=600`, and the
JavaScript is a handful of ES modules the browser fetches one by one. A
reader who was on the site shortly before a deploy can therefore get the new
`index.html`, because browsers revalidate the top-level document, together
with a module still held from the old one. The page then calls a function its
module does not have yet, and the panel that needed it shows an OJS error such
as `TypeError: app.checkAppliesTo is not a function`.

It is not a broken deploy, and checking is quick: load the site in a private
window, which has no cache to be stale. A hard reload (`Ctrl+Shift+R`, or
`Cmd+Shift+R`) fixes it for whoever hit it, twice if once is not enough: the
reload bypasses the cache for the page and what it loads with it, while the
modules arrive later through the dynamic `import()` in the first cell of the
page, which that bypass need not cover. It also clears itself ten minutes
after the deploy. Only a change that adds or renames an export can cause it,
so it follows a release rather than a data rebuild.

### Where the data comes from

The trimming runs where the inputs are, not in the workflow. An `aiqclib`
batch is 2.2 GB that the build reads twice to write the 429 MB the site
serves, and this workflow runs on every push to `main`, so building there
would spend two minutes and a 2.2 GB download re-deriving byte-identical
files on a commit that only touched a docstring. The build runs once, on the
machine that has the data; the deploy fetches what it wrote and renders it.

So the workflow takes its data from one of two places, and chooses by itself:

| `NRTQC_DATA_RELEASE` | What gets deployed |
| --- | --- |
| A release tag | `site/data` as published by that release |
| Unset | The synthetic demo, generated and built in the job |

The release holds the contents of `site/data`, one asset per file:
`catalog.json`, `profiles.parquet`, and one observation file per dataset.
`catalog.json` is what maps an asset back to the path it belongs at, so
fetching needs no build configuration and cannot disagree with what was
published. They live on **`AIQC-Hub/nrtqc-diff-data`**, a repository of its
own so that a data refresh and a code release do not share a tag list.

Leaving the variable unset is what keeps a fork, a clean checkout and a
`workflow_dispatch` run with `dataset: demo` deployable with no access to
anything.

The settings this needs, all on this repository:

| Setting | Kind | What it is |
| --- | --- | --- |
| `NRTQC_DATA_RELEASE` | Variable | The tag to deploy, e.g. `data-2026-09-23` |
| `NRTQC_DATA_REPO` | Variable, optional | Overrides `AIQC-Hub/nrtqc-diff-data` |
| `NRTQC_DATA_TOKEN` | Secret, optional | Read access, needed only if the data repository is private |

### What the deploy no longer guarantees

Building in the workflow bought one thing: the published data was always what
the current build code produces. Publishing the output instead means a
release can outlive the code that wrote it, and a change to the published
columns or file names would leave the site rendering against files that no
longer match.

That is what `data_format` in `catalog.json` is for. `DATA_FORMAT` in
`src/nrtqc_diff/build.py` is raised in the same commit as any change to the
published shape, and `scripts/data_release.py` compares the two at both ends:
publishing data built by an older checkout is refused, and so is deploying a
release stamped with anything but what this checkout reads. A stale release
therefore stops the deploy with a message saying to rebuild, rather than
quietly producing a broken site.

### Publishing a new build of the data

From a checkout whose `data/` holds the `aiqclib` run:

```bash
uv run nrtqc-diff build -c config/test_nrt.yaml
uv run python scripts/data_release.py publish data-2026-09-23 --dry-run
uv run python scripts/data_release.py publish data-2026-09-23
```

That creates the release if it is not there and uploads what the build wrote,
with a `manifest.json` recording the sizes. Then set `NRTQC_DATA_RELEASE` to
the new tag and run the workflow. The old release is left alone, so rolling
back is a variable change and a re-run.

A refresh is therefore a new tag rather than a commit, and that is the point
of using releases at all. Assets are held outside the git object store, so
publishing again adds one lightweight tag ref to the data repository and
nothing else: a clone of it stays a README however many vintages of a 429 MB
build it is serving, and no parquet file ever enters a history that has to be
carried forever. When an old one is no longer worth the storage:

```bash
gh release delete data-2026-08-12 \
  --repo AIQC-Hub/nrtqc-diff-data --cleanup-tag --yes
```

Which repository holds the release is a setting on both sides, `--repo` here
and `NRTQC_DATA_REPO` in the workflow, so a release can live wherever suits;
`AIQC-Hub/nrtqc-diff-data` is a default, not an assumption the code makes.

`fetch` is the other half, and the workflow is not the only place it is
useful: it puts a published build into `site/data` on a machine that has no
inputs at all, which is enough to render the site. It replaces that directory
rather than merging into it, so a dataset dropped from the catalog cannot
linger as a file nothing references and the deploy still ships, and it checks
every asset against the manifest before it writes anything.

### Why the site does not read the release directly

It cannot. The data is served from the Pages site, inside the same artifact as
the pages, because GitHub sends no `access-control-allow-origin` header on a
release download, on either the `github.com` hop or the signed one it
redirects to. Range requests work there, but a browser will not make the
request at all. Pages does both: `access-control-allow-origin: *`, and a `GET`
carrying a `Range` comes back `206`. (A `HEAD` carrying a `Range` comes back
`200` with the full length, which makes range support look absent. It is not.)

That settles the size question the other way from how it was once written
here. The published site is about 510 MB, of which 429 MB is data and 79 MB
the vendored browser libraries, against a 1 GB cap on a Pages site: half the
budget, with the largest observation file at 120 MB read by range a row group
at a time. What to watch as readership grows is bandwidth rather than size,
since `profiles.parquet` is fetched whole at 7.9 MB a visit and Pages has a
soft limit of 100 GB a month. Splitting that file per product is the answer
when it comes to that, and it is a build change, not a site one.

If the data ever does have to move off Pages, the seam is `observations_file`
in `catalog.json`, which `db.js` resolves against `DATA_ROOT`. The host would
need CORS and range requests, which Zenodo and an object store give and a
GitHub release does not. See the data access seam in `docs/SITE.md`.

## When the data changes

The site reads whatever is in `site/data/` at render time, so a data update is
a rebuild and a redeploy, with no code change and no version bump: build it,
publish it, point `NRTQC_DATA_RELEASE` at the new tag, and run the workflow.
A change to the *shape* of what the build writes needs one more step, raising
`DATA_FORMAT`, which is what stops a release from before it being deployed
against code that has moved on. Record what
changed in `CHANGELOG.md` under `### Changed` when the *shape* of the data
changes, since that is a contract with the site; a refreshed dataset with the
same schema needs no entry.
