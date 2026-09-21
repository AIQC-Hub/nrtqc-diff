/**
 * Every SQL statement the site runs, in one place.
 *
 * Keeping the queries here rather than inline in index.qmd means the page
 * reads as layout and the schema knowledge stays next to db.js. Each function
 * returns rows, not SQL.
 */

import { literal, literalList, query, registerDataset } from "./db.js";

/**
 * One row per profile of the selected product, worst disagreement first.
 *
 * @param {Array<object>} datasets the selected product's dataset entries.
 * @param {number} [limit=500] how many rows to fetch.
 * @returns {Promise<Array<object>>}
 */
export async function profileSummary(datasets, limit = 500) {
  const ids = datasets.map((entry) => entry.id);
  return query(`
    SELECT *
    FROM profiles
    WHERE dataset_id IN (${literalList(ids)})
    ORDER BY n_disagree DESC, profile_id
    LIMIT ${Number(limit)}
  `);
}

/**
 * The category totals of a whole product, summed over its profiles.
 *
 * @param {Array<object>} datasets the selected product's dataset entries.
 * @param {Array<object>} variables the `variables` array of catalog.json.
 * @param {Array<object>} statuses the `statuses` array of catalog.json.
 * @returns {Promise<Array<object>>} one row per variable and category.
 */
export async function productTotals(datasets, variables, statuses) {
  const ids = datasets.map((entry) => entry.id);
  const parts = [];
  for (const variable of variables) {
    for (const status of statuses) {
      parts.push(`
        SELECT ${literal(variable.name)} AS variable,
               ${literal(status.key)}    AS status,
               SUM(${variable.name}_n_${status.key}) AS n
        FROM profiles
        WHERE dataset_id IN (${literalList(ids)})
      `);
    }
  }
  return query(parts.join("\nUNION ALL\n"));
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
        .filter((name) => /(^|_)qc_[a-z0-9_]+$/.test(name))
    );
  }
  return itemColumnCache.get(view);
}
