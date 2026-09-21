#!/usr/bin/env bash
#
# Vendor the browser libraries the site needs into site/libs/.
#
# Nothing downloaded here belongs in git: site/libs/ is in .gitignore and the
# deploy workflow runs this script fresh. Re-running it is cheap, since files
# already present are left alone unless --force is given.
#
# Usage: bash scripts/fetch_assets.sh [--force]

set -euo pipefail

# DuckDB WASM must match the version stratum-duckdb pins in its own source.
# Mixing versions gives "_setThrew is not defined" at runtime rather than a
# clear error, so check this when upgrading either one.
DUCKDB_VERSION="1.29.0"
PLOTLY_VERSION="2.35.2"
STRATUM_DUCKDB_TAG="${STRATUM_DUCKDB_TAG:-latest}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB_DIR="$ROOT/site/libs"
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

fetch() {
  local url="$1" target="$2"
  if [[ -f "$target" && $FORCE -eq 0 ]]; then
    echo "  have  $(basename "$target")"
    return
  fi
  echo "  get   $(basename "$target")"
  curl -sSfL "$url" -o "$target"
}

echo "Vendoring browser libraries into site/libs/"
mkdir -p "$LIB_DIR/duckdb" "$LIB_DIR/plotly"

# ---------------------------------------------------------------- DuckDB ---
# Both the mvp and eh bundles are needed: stratum-duckdb picks between them at
# runtime from the browser's WASM capabilities.
DUCKDB_BASE="https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@${DUCKDB_VERSION}/dist"
for file in duckdb-browser-mvp.worker.js duckdb-mvp.wasm \
            duckdb-browser-eh.worker.js duckdb-eh.wasm; do
  fetch "$DUCKDB_BASE/$file" "$LIB_DIR/duckdb/$file"
done

# ------------------------------------------------------- stratum-duckdb ---
# A local checkout wins when one is given, which is how to test an unreleased
# change to the library without publishing it.
if [[ -n "${STRATUM_DUCKDB_DIR:-}" ]]; then
  echo "  copy  stratum-duckdb.esm.js from $STRATUM_DUCKDB_DIR"
  cp "$STRATUM_DUCKDB_DIR/dist/stratum-duckdb.esm.js" "$LIB_DIR/duckdb/"
else
  if [[ "$STRATUM_DUCKDB_TAG" == "latest" ]]; then
    STRATUM_URL="https://github.com/stratum-toolkit/stratum-duckdb/releases/latest/download/stratum-duckdb.esm.js"
  else
    STRATUM_URL="https://github.com/stratum-toolkit/stratum-duckdb/releases/download/${STRATUM_DUCKDB_TAG}/stratum-duckdb.esm.js"
  fi
  fetch "$STRATUM_URL" "$LIB_DIR/duckdb/stratum-duckdb.esm.js"
fi

# ---------------------------------------------------------------- Plotly ---
fetch "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@${PLOTLY_VERSION}/plotly.min.js" \
      "$LIB_DIR/plotly/plotly.min.js"

echo "Done. site/libs/ holds $(du -sh "$LIB_DIR" | cut -f1)."
