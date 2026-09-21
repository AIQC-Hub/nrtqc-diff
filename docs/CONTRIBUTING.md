# Contributing

## Setup

```bash
uv sync                                   # Python environment
uv run python scripts/make_demo_data.py   # synthetic inputs
uv run nrtqc-diff build -c config/datasets.yaml
bash scripts/fetch_assets.sh              # browser libraries, once
bash scripts/serve.sh
```

Quarto 1.9 or newer is needed to render the site. Everything else is handled
by `uv`; there is no Node toolchain and no JavaScript build step.

## Checks before a commit

```bash
uv run pytest                 # the suite is small; run all of it
uv run ruff check src tests scripts
uv run ruff format src tests scripts
quarto render site            # must exit 0
git ls-files -z | xargs -0 grep -lP '[\x{2013}\x{2014}]'   # must print nothing
```

The last one enforces the writing style rule: no em dashes and no en dashes
anywhere in the repository, including commit messages. Use a colon, comma,
semicolon, parentheses, or two sentences. Ranges and minus signs use a plain
hyphen: `1-4`, `47-65%`, `-90`.

## Tests

`tests/` covers the build step only, which is where the logic is. The
fixtures build one profile per agreement case in memory, so the suite runs in
under a second and needs no data files.

| File | Covers |
| --- | --- |
| `tests/conftest.py` | The synthetic NRT QC output and a complete build over it |
| `tests/test_config.py` | Path resolution, defaults, the errors the reader sees |
| `tests/test_build.py` | Trimming, categories, counts, the catalog |
| `tests/test_flags.py` | Which columns count as per-item QC flags |

A change to what is published belongs in `tests/test_build.py` and in
`docs/DATA_MODEL.md` in the same commit: that file is the contract the site
reads against.

The site itself has no test runner. `docs/SITE.md` lists the three things to
check by hand after a change to `site/`.

## Git workflow

Gitflow:

- `main` holds releases, `develop` holds integration.
- Features branch off `develop` as `feature/<name>` and merge back with
  `--no-ff`.
- Hotfixes branch off `main` as `hotfix/<name>`.
- Never commit directly to `main` or `develop`.

## What never goes in git

`data/`, `site/data/`, `site/libs/` and `site/_site/` are all generated and
are in `.gitignore`. Nothing derived from a source dataset belongs in the
repository: the build reproduces it from the files named in the config, and
`scripts/make_demo_data.py` produces a synthetic stand-in for anyone without
access to the real ones.
