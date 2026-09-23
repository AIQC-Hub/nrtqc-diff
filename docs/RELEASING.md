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

The workflow needs the input data to exist. As shipped it generates the
synthetic demo, which is what makes the published site reproducible from a
clean checkout. A GitHub runner cannot reach the real inputs: `data/` is a
symlink to a local `aiqclib` output tree and is not in git, by design.

Publishing real data is therefore still an open decision, and the size is
what makes it one. `config/test_nrt.yaml` produces 430 MB, of which a single
observation file is 126 MB. That fits GitHub Pages, whose published sites are
capped at 1 GB, but not comfortably, and the artifact has to be built
somewhere with the inputs to hand. The options:

- build the data elsewhere and commit `site/data/` to a separate branch or
  repository that the workflow checks out, or
- upload the parquet files to a GitHub Release or Zenodo and point the site
  at those URLs.

The second scales better and is nearly free to adopt: the site already reads
observation files by URL through `registerRemote`, so it is a change to what
`catalog.json` records in `observations_file`, not to the site. It also needs
CORS on the host, which GitHub Releases and Zenodo both give. See the data
access seam in `docs/SITE.md`.

## When the data changes

The site reads whatever is in `site/data/` at render time, so a data update is
a rebuild and a redeploy, with no code change and no version bump. Record what
changed in `CHANGELOG.md` under `### Changed` when the *shape* of the data
changes, since that is a contract with the site; a refreshed dataset with the
same schema needs no entry.
