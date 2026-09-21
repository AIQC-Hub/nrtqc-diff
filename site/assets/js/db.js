/**
 * Data access for the site.
 *
 * This module is the only place that knows how the published files are read.
 * It currently uses stratum-duckdb (DuckDB WASM over parquet); swapping in
 * stratum-sqlite means rewriting this file and nothing else, as long as
 * `query()` keeps returning an array of plain objects. See docs/SITE.md.
 */

// assets/js/ -> the site root. Using import.meta.url keeps every path correct
// whatever page imports this module and wherever the site is deployed.
const SITE_ROOT = new URL("../../", import.meta.url).href;

/** Where the build step writes its files. */
export const DATA_ROOT = SITE_ROOT + "data/";

/** Where scripts/fetch_assets.sh vendors the browser libraries. */
export const LIB_ROOT = SITE_ROOT + "libs/";

let databasePromise = null;
const registeredDatasets = new Set();

/**
 * Open the database, once per page load.
 *
 * @returns {Promise<object>} the stratum-duckdb Database.
 */
export function connect() {
  if (databasePromise === null) {
    databasePromise = (async () => {
      const module = await import(LIB_ROOT + "duckdb/stratum-duckdb.esm.js");
      const database = await module.default.open({
        duckdbPath: LIB_ROOT + "duckdb/",
        // Relative paths in registerFile() resolve against the data folder,
        // so callers pass the paths exactly as catalog.json records them.
        siteRoot: DATA_ROOT,
      });
      // The profile summary of every dataset lives in one small file, so it
      // is worth having ready before the reader picks anything.
      await database.registerFile("profiles", "profiles.parquet");
      return database;
    })();
  }
  return databasePromise;
}

/**
 * Read catalog.json: the region/product tree, the variables, the categories.
 *
 * @returns {Promise<object>} the catalog written by the build step.
 */
export async function loadCatalog() {
  const response = await fetch(DATA_ROOT + "catalog.json");
  if (!response.ok) {
    throw new Error(
      `Could not read catalog.json (${response.status}). ` +
        "Run: uv run nrtqc-diff build -c config/datasets.yaml"
    );
  }
  return response.json();
}

/**
 * Run SQL and get rows back.
 *
 * @param {string} sql
 * @returns {Promise<Array<object>>}
 */
export async function query(sql) {
  const database = await connect();
  return database.query(sql);
}

/**
 * Make sure a dataset's observations are queryable, fetching them once.
 *
 * Observation files are registered on demand rather than at startup: a reader
 * who opens one profile should not pay for every dataset in the catalog.
 *
 * @param {object} entry a dataset entry from catalog.json.
 * @returns {Promise<string>} the view name to use in SQL.
 */
export async function registerDataset(entry) {
  const view = observationView(entry.id);
  if (!registeredDatasets.has(view)) {
    const database = await connect();
    await database.registerFile(view, entry.observations_file);
    registeredDatasets.add(view);
  }
  return view;
}

/**
 * The SQL view name holding one dataset's observations.
 *
 * @param {string} datasetId
 * @returns {string}
 */
export function observationView(datasetId) {
  if (!/^[a-z0-9_]+$/.test(datasetId)) {
    throw new Error(`Unusable dataset id '${datasetId}'.`);
  }
  return `obs_${datasetId}`;
}

/**
 * Quote a value for inlining in SQL.
 *
 * Everything the site puts in a query comes from its own catalog or from a
 * row it just read, but quoting is cheap and a platform code with an
 * apostrophe should not break the page.
 *
 * @param {string|number} value
 * @returns {string}
 */
export function literal(value) {
  if (typeof value === "number") return String(value);
  return "'" + String(value).replace(/'/g, "''") + "'";
}

/**
 * Quote a list of values for an SQL `IN` clause.
 *
 * @param {Array<string|number>} values
 * @returns {string}
 */
export function literalList(values) {
  return values.map(literal).join(", ");
}
