#!/usr/bin/env bash
#
# Render the site and open it with live reload.
#
# Usage: bash scripts/serve.sh [--static] [--port 4321]
#
#   (default)  quarto preview: re-renders and reloads as files change. Use
#              this while working on the site.
#   --static   render once, then serve the result with a server that answers
#              range requests. The site reads its observation files with
#              them, and quarto preview does not answer them, so against a
#              real dataset preview downloads whole files where the deployed
#              site fetches a couple of megabytes. Use this to check what the
#              site really costs to open.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

STATIC=0
ARGS=()
for argument in "$@"; do
  if [[ "$argument" == "--static" ]]; then
    STATIC=1
  else
    ARGS+=("$argument")
  fi
done

if [[ ! -f "$ROOT/site/data/catalog.json" ]]; then
  echo "site/data/catalog.json is missing. Run:" >&2
  echo "  uv run nrtqc-diff build -c config/datasets.yaml" >&2
  exit 1
fi

if [[ ! -f "$ROOT/site/libs/duckdb/stratum-duckdb.esm.js" ]]; then
  echo "site/libs/ is missing. Run:" >&2
  echo "  bash scripts/fetch_assets.sh" >&2
  exit 1
fi

if [[ "$STATIC" == "1" ]]; then
  quarto render "$ROOT/site"
  exec uv run python "$ROOT/scripts/serve_static.py" "$ROOT/site/_site" \
    ${ARGS[@]+"${ARGS[@]}"}
fi

exec quarto preview "$ROOT/site" ${ARGS[@]+"${ARGS[@]}"}
