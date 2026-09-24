/**
 * Every SQL statement the site runs, in one place.
 *
 * Keeping the queries here rather than inline in index.qmd means the page
 * reads as layout and the schema knowledge stays next to db.js. Each function
 * returns rows, not SQL.
 */

import { literal, literalList, query, registerDataset } from "./db.js";
import { isItemColumn } from "./labels.js";

//: The columns a reader can search the profile list by. `profile_id` is what
//: the list shows; `platform_code` is the part of it people actually know,
//: and a product can hold many profiles from one platform.
const SEARCH_COLUMNS = ["profile_id", "platform_code"];


/**
 * The `AND` clause restricting a profile query to a reader's search text.
 *
 * Matched case-insensitively against :data:`SEARCH_COLUMNS`, anywhere in the
 * value, because a platform code is a fragment of a profile id rather than a
 * prefix of it. `%` and `_` are wildcards to `LIKE` and a reader typing a
 * platform code should not have to know that, so they are escaped and matched
 * as themselves.
 *
 * @param {string} search the reader's text, possibly empty.
 * @returns {string} SQL beginning with `AND`, or an empty string.
 */
function searchClause(search) {
  const text = String(search ?? "").trim();
  if (!text) return "";
  const pattern = "%" + text.toLowerCase().replace(/[\\%_]/g, "\\$&") + "%";
  const tests = SEARCH_COLUMNS.map(
    (column) =>
      `lower(CAST(${column} AS VARCHAR)) LIKE ${literal(pattern)} ESCAPE '\\'`
  );
  return `AND (${tests.join(" OR ")})`;
}

/**
 * The `AND` clause restricting a profile query to one platform.
 *
 * An exact match, unlike the search: a platform's own list must not pick up
 * the profiles of another platform whose code happens to contain this one.
 *
 * @param {string|null} platform a `platform_code`, or null for every one.
 * @returns {string} SQL beginning with `AND`, or an empty string.
 */
function platformClause(platform) {
  if (platform === null || platform === undefined) return "";
  return `AND platform_code = ${literal(String(platform))}`;
}

let profileColumnsPromise = null;

/**
 * The columns `profiles.parquet` actually has.
 *
 * Asked of the file rather than assumed, for two reasons. The build copies
 * `profile_timestamp` and its neighbours only when the input carries them, so
 * a page showing dates has to know whether there are any. And the list may be
 * ordered by any of these columns, a whitelist that grows with the configured
 * variables and categories without anyone having to extend it.
 *
 * @returns {Promise<Array<string>>}
 */
export function profileColumns() {
  if (profileColumnsPromise === null) {
    profileColumnsPromise = query("DESCRIBE profiles").then((rows) =>
      rows.map((row) => row.column_name)
    );
  }
  return profileColumnsPromise;
}

/**
 * The column a profile query may be ordered by, defaulted if unrecognised.
 *
 * A column of `profiles` or nothing, because this value reaches SQL as a
 * column name and arrives from a click.
 *
 * @param {string} name the requested column.
 * @returns {Promise<string>} a column of `profiles`.
 */
async function orderColumn(name) {
  const columns = await profileColumns();
  return columns.includes(name) ? name : "n_disagree";
}

/**
 * One page of the selected product's profiles.
 *
 * The order and the search are applied here rather than in the page, because
 * a page of 500 out of 81,541 profiles can only be a slice of the product if
 * the database decides which slice. Sorting the rows that were fetched would
 * sort the slice and say nothing about the rest.
 *
 * @param {Array<object>} datasets the selected product's dataset entries.
 * @param {object} [request] the page to fetch.
 * @param {number} [request.limit=500] rows per page.
 * @param {number} [request.offset=0] rows to skip.
 * @param {string} [request.search=""] text to match, empty for all.
 * @param {string} [request.orderBy="n_disagree"] a column of `profiles`.
 * @param {boolean} [request.descending=true] the direction of the order.
 * @param {string|null} [request.platform=null] one `platform_code` only.
 * @returns {Promise<Array<object>>}
 */
export async function profileSummary(datasets, request = {}) {
  const {
    limit = 500,
    offset = 0,
    search = "",
    orderBy = "n_disagree",
    descending = true,
    platform = null,
  } = request;
  const ids = datasets.map((entry) => entry.id);
  return query(`
    SELECT *
    FROM profiles
    WHERE dataset_id IN (${literalList(ids)})
      ${platformClause(platform)}
      ${searchClause(search)}
    ORDER BY ${await orderColumn(orderBy)} ${descending ? "DESC" : "ASC"}, profile_id
    LIMIT ${Number(limit)} OFFSET ${Number(offset)}
  `);
}

/**
 * How many profiles the selected product has for a search.
 *
 * The pager needs the size of the whole answer, not of the page it is
 * showing, and it is the one number that says a search found nothing beyond
 * what is listed rather than nothing at all.
 *
 * @param {Array<object>} datasets the selected product's dataset entries.
 * @param {string} [search=""] text to match, empty for all.
 * @param {string|null} [platform=null] one `platform_code` only.
 * @returns {Promise<number>}
 */
export async function profileCount(datasets, search = "", platform = null) {
  const ids = datasets.map((entry) => entry.id);
  const rows = await query(`
    SELECT COUNT(*) AS n
    FROM profiles
    WHERE dataset_id IN (${literalList(ids)})
      ${platformClause(platform)}
      ${searchClause(search)}
  `);
  return Number(rows[0]?.n ?? 0);
}

/**
 * One row per region and product, over every published profile.
 *
 * This is the whole of the summary page in one statement. `profiles.parquet`
 * carries `region` and `product` as columns, so a product built from several
 * datasets aggregates here rather than in the page, and a product with no
 * published profile simply does not come back: the page fills that in from
 * the catalog, which knows the product exists.
 *
 * The counts describe published profiles only. The totals the trimming was
 * measured against live in catalog.json, not here.
 *
 * @param {Array<object>} variables the `variables` array of catalog.json.
 * @param {Array<object>} statuses the `statuses` array of catalog.json.
 * @returns {Promise<Array<object>>} rows keyed by `{region, product}`, with
 *          `n_profiles`, `n_obs`, `n_disagree` and one `{variable}_{status}`
 *          column per variable and category.
 */
export async function regionProductTotals(variables, statuses) {
  const sums = [];
  for (const variable of variables) {
    for (const status of statuses) {
      const column = `${variable.name}_n_${status.key}`;
      sums.push(`SUM(${column}) AS ${variable.name}_${status.key}`);
    }
  }
  return query(`
    SELECT region,
           product,
           COUNT(*)         AS n_profiles,
           SUM(n_obs)       AS n_obs,
           SUM(n_disagree)  AS n_disagree,
           ${sums.join(",\n           ")}
    FROM profiles
    GROUP BY region, product
    ORDER BY region, product
  `);
}

/**
 * One row per platform of the selected product.
 *
 * The platform page's list, in one statement over `profiles`. A product holds
 * a few hundred platforms at most, so unlike the profiles they come back all
 * at once and the page sorts and filters them itself.
 *
 * The first and last profile times are there only when `profiles.parquet`
 * carries `profile_timestamp`; the build copies it when the input has it.
 *
 * Every sum is cast to `BIGINT`. DuckDB sums whole numbers into a `HUGEINT`,
 * which reaches the page as an object rather than a number: it formats
 * correctly, and then `+` joins two of them as strings and a rate comes out
 * a million times too large.
 *
 * @param {Array<object>} datasets the selected product's dataset entries.
 * @param {Array<object>} variables the `variables` array of catalog.json.
 * @param {Array<object>} statuses the `statuses` array of catalog.json.
 * @returns {Promise<Array<object>>} rows keyed by `platform_code`, with
 *          `n_profiles`, `n_obs`, `n_disagree`, and per variable
 *          `{variable}_{status}` observation counts and
 *          `{variable}_n_profiles_disagree`, the profiles with at least one
 *          disagreement on that variable.
 */
export async function platformSummary(datasets, variables, statuses) {
  const ids = datasets.map((entry) => entry.id);
  const columns = await profileColumns();
  const fields = [];
  if (columns.includes("profile_timestamp")) {
    fields.push("MIN(profile_timestamp) AS first_timestamp");
    fields.push("MAX(profile_timestamp) AS last_timestamp");
  }
  for (const variable of variables) {
    for (const status of statuses) {
      const column = `${variable.name}_n_${status.key}`;
      fields.push(
        `CAST(SUM(${column}) AS BIGINT) AS ${variable.name}_${status.key}`
      );
    }
    fields.push(
      `COUNT(*) FILTER (WHERE ${variable.name}_n_disagree > 0) ` +
        `AS ${variable.name}_n_profiles_disagree`
    );
  }
  return query(`
    SELECT platform_code,
           COUNT(*)                          AS n_profiles,
           CAST(SUM(n_obs) AS BIGINT)        AS n_obs,
           CAST(SUM(n_disagree) AS BIGINT)   AS n_disagree,
           ${fields.join(",\n           ")}
    FROM profiles
    WHERE dataset_id IN (${literalList(ids)})
    GROUP BY platform_code
    ORDER BY platform_code
  `);
}

/**
 * Every observation of one profile, shallowest first.
 *
 * @param {object} entry the dataset entry the profile belongs to.
 * @param {string} profileId the `profile_id` of the profile.
 * @returns {Promise<Array<object>>}
 */
export async function profileObservations(entry, profileId) {
  const view = await registerDataset(entry);
  return query(`
    SELECT *
    FROM ${view}
    WHERE profile_id = ${literal(profileId)}
    ORDER BY observation_no
  `);
}

/**
 * The contingency table of one profile and one variable.
 *
 * @param {object} entry the dataset entry the profile belongs to.
 * @param {string} profileId the `profile_id` of the profile.
 * @param {object} variable a `variables` entry from catalog.json.
 * @returns {Promise<Array<object>>} `{existing_flag, new_flag, n}` rows.
 */
export async function profileContingency(entry, profileId, variable) {
  const view = await registerDataset(entry);
  return query(`
    SELECT ${variable.flag}     AS existing_flag,
           ${variable.nrt_flag} AS new_flag,
           COUNT(*)             AS n
    FROM ${view}
    WHERE profile_id = ${literal(profileId)}
    GROUP BY 1, 2
    ORDER BY 1 NULLS LAST, 2 NULLS LAST
  `);
}

/**
 * How often each QC item fired in one profile.
 *
 * The item columns are discovered from the file rather than configured, so a
 * dataset built with a different set of QC items needs no change here. The
 * result answers the question the contingency table raises: when aiqclib
 * flagged something the input did not, which test was it?
 *
 * @param {object} entry the dataset entry the profile belongs to.
 * @param {string} profileId the `profile_id` of the profile.
 * @returns {Promise<Array<object>>} `{item, flag, n}` rows, worst flag first.
 */
export async function profileItemBreakdown(entry, profileId) {
  const view = await registerDataset(entry);
  const columns = await itemColumns(view);
  if (columns.length === 0) return [];

  const names = columns.map((name) => `"${name}"`).join(", ");
  return query(`
    SELECT item, flag, COUNT(*) AS n
    FROM (
      SELECT *
      FROM (SELECT * FROM ${view} WHERE profile_id = ${literal(profileId)})
      UNPIVOT (flag FOR item IN (${names}))
    )
    WHERE flag > 1
    GROUP BY 1, 2
    ORDER BY n DESC, item
  `);
}

/**
 * The shallowest and deepest pressure of one profile.
 *
 * For the line of facts heading the Tables page, which has no observations
 * of its own to take them from. Two numbers over the same rows the
 * contingency tables under it read.
 *
 * @param {object} entry the dataset entry the profile belongs to.
 * @param {string} profileId the `profile_id` of the profile.
 * @returns {Promise<Array<number|null>>} `[shallowest, deepest]` in dbar.
 */
export async function profilePressureRange(entry, profileId) {
  const view = await registerDataset(entry);
  const rows = await query(`
    SELECT MIN(pres) AS shallowest, MAX(pres) AS deepest
    FROM ${view}
    WHERE profile_id = ${literal(profileId)}
  `);
  return [rows[0]?.shallowest ?? null, rows[0]?.deepest ?? null];
}

const itemColumnCache = new Map();

/**
 * The per-item QC flag columns of one observations file.
 *
 * @param {string} view the registered view name.
 * @returns {Promise<Array<string>>}
 */
async function itemColumns(view) {
  if (!itemColumnCache.has(view)) {
    const described = await query(`DESCRIBE ${view}`);
    itemColumnCache.set(
      view,
      described
        .map((row) => row.column_name)
        .filter(isItemColumn)
    );
  }
  return itemColumnCache.get(view);
}
