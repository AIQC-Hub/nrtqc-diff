#!/usr/bin/env bash
#
# Render the site and open it with live reload.
#
# Usage: bash scripts/serve.sh [--port 4321]

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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

exec quarto preview "$ROOT/site" "$@"
