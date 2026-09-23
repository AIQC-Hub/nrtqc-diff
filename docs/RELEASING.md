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

The workflow builds the data itself, from one of two sources, and chooses
between them by itself:

| `NRTQC_DATA_RELEASE` | What gets built |
| --- | --- |
| A release tag | The real batch, through `config/test_nrt.yaml` |
| Unset | The synthetic demo, through `config/datasets.yaml` |

The real inputs are 2.2 GB across 8 files and are not in git, by design:
`data/` is a symlink to a local `aiqclib` output tree. They live instead as
assets on a release of **`AIQC-Hub/nrtqc-diff-data`**, a repository of its own
so that a data refresh and a code release do not share a tag list and do not
have to happen together. `scripts/data_release.py` puts them there and takes
them back out; both halves read `config/test_nrt.yaml`, so the dataset ids are
what name the assets and neither side can drift from the other.

Leaving the variable unset is what keeps a fork, a clean checkout and a
`workflow_dispatch` run with `dataset: demo` deployable with no access to
anything.

The settings this needs, all on this repository:

| Setting | Kind | What it is |
| --- | --- | --- |
| `NRTQC_DATA_RELEASE` | Variable | The tag to build from, e.g. `data-2026-09-23` |
| `NRTQC_DATA_REPO` | Variable, optional | Overrides `AIQC-Hub/nrtqc-diff-data` |
| `NRTQC_DATA_TOKEN` | Secret, optional | Read access, needed only if the data repository is private |

### Publishing a new set of inputs

From a checkout whose `data/` holds the `aiqclib` run:

```bash
uv run python scripts/data_release.py publish data-2026-09-23 --dry-run
uv run python scripts/data_release.py publish data-2026-09-23
```

That creates the release if it is not there, uploads one asset per dataset
named after its id, and adds a `manifest.json` recording the sizes. Then set
`NRTQC_DATA_RELEASE` to the new tag and run the workflow. The old release is
left alone, so rolling back is a variable change and a re-run.

Refreshing the data is therefore a new tag rather than a commit, and that is
the point of using releases at all. Assets are held outside the git object
store, so publishing again adds one lightweight tag ref to the data
repository and nothing else: a clone of it stays a README however many
vintages of a 2.2 GB batch it is serving, and no version of the parquet files
is ever in a history that has to be carried forever. When an old one is no
longer worth the storage:

```bash
gh release delete data-2026-08-12 \
  --repo AIQC-Hub/nrtqc-diff-data --cleanup-tag --yes
```

Which repository holds the release is a setting on both sides, `--repo` here
and `NRTQC_DATA_REPO` in the workflow, so a release can live wherever suits;
`AIQC-Hub/nrtqc-diff-data` is a default, not an assumption the code makes.

The upload is the slow part: 2.2 GB up your own connection, with the largest
asset at 833 MB against a 2 GB per-asset limit. The same download inside the
workflow takes well under a minute.

`fetch` is the other half, and the workflow is not the only place it is
useful: on a machine with no `data/` it puts a real batch where
`config/test_nrt.yaml` expects it. It refuses to write over inputs that are
already there, because in a working checkout that path is the real `aiqclib`
output tree, and it checks every asset against the manifest before it moves
any of them into place.

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
a rebuild and a redeploy, with no code change and no version bump: publish the
new inputs, point `NRTQC_DATA_RELEASE` at their tag, and run the workflow.
Record what
changed in `CHANGELOG.md` under `### Changed` when the *shape* of the data
changes, since that is a contract with the site; a refreshed dataset with the
same schema needs no entry.
