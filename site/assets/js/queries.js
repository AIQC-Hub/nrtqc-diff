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

//: The columns the list may be ordered by. A whitelist rather than a check,
//: because this value reaches SQL as a column name and arrives from a click.
const ORDER_COLUMNS = ["n_disagree", "n_obs", "profile_id", "profile_timestamp"];

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
 * The column a profile query may be ordered by, defaulted if unrecognised.
 *
 * @param {string} name the requested column.
 * @returns {string} a column of `profiles`.
 */
function orderColumn(name) {
  return ORDER_COLUMNS.includes(name) ? name : "n_disagree";
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
 * @param {string} [request.orderBy="n_disagree"] one of `ORDER_COLUMNS`.
 * @param {boolean} [request.descending=true] the direction of the order.
 * @returns {Promise<Array<object>>}
 */
export async function profileSummary(datasets, request = {}) {
  const {
    limit = 500,
    offset = 0,
    search = "",
    orderBy = "n_disagree",
    descending = true,
  } = request;
  const ids = datasets.map((entry) => entry.id);
  return query(`
    SELECT *
    FROM profiles
    WHERE dataset_id IN (${literalList(ids)})
      ${searchClause(search)}
    ORDER BY ${orderColumn(orderBy)} ${descending ? "DESC" : "ASC"}, profile_id
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
 * @returns {Promise<number>}
 */
export async function profileCount(datasets, search = "") {
  const ids = datasets.map((entry) => entry.id);
  const rows = await query(`
    SELECT COUNT(*) AS n
    FROM profiles
    WHERE dataset_id IN (${literalList(ids)})
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
