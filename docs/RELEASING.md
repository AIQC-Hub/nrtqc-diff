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
