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
clean checkout. Publishing real data means one of:

- committing the built `site/data/` to a separate branch or repository that
  the workflow checks out, or
- uploading the parquet files to a GitHub Release or Zenodo and pointing the
  site at those URLs, which DuckDB reads with HTTP range requests.

The second scales better and is the reason the data lives in parquet. See the
`registerRemote` note in `docs/SITE.md`.

## When the data changes

The site reads whatever is in `site/data/` at render time, so a data update is
a rebuild and a redeploy, with no code change and no version bump. Record what
changed in `CHANGELOG.md` under `### Changed` when the *shape* of the data
changes, since that is a contract with the site; a refreshed dataset with the
same schema needs no entry.
