/**
 * Plain names for the things the data calls by column name.
 *
 * The columns this site reads are the `aiqclib` output's own: `temp_qc`,
 * `temp_nrt_flag`, `psal_qc_spike`. Whoever ran the build reads those at a
 * glance and nobody else does, so no panel shows one without words beside it.
 * The words live here, in one module, rather than in whichever page happened
 * to need them first.
 *
 * Nothing here is configurable, because none of it is a choice the build
 * makes: the flag scheme is the IOC/Argo one and the check names are
 * `aiqclib`'s. What the build does decide, which input values count as an
 * anomaly, is read from the variable entry instead of being restated here.
 */

/**
 * The meaning the IOC/Argo scheme gives a flag value.
 *
 * Only the values the scheme fixes are listed. Anything else gets no gloss
 * rather than a guess: input datasets do put the upper values to their own
 * uses, and a wrong word under a number is worse than no word at all.
 */
const FLAG_MEANINGS = new Map([
  [0, "not assessed"],
  [1, "good"],
  [2, "probably good"],
  [3, "probably bad"],
  [4, "bad"],
  [9, "missing"],
]);

/**
 * The checks `aiqclib` runs, keyed by the item part of the column name.
 *
 * The same list is in the table on the About page, which describes what each
 * one looks for; this is the short name and the test number alone, because a
 * panel showing twenty rows has no room for a description and the About page
 * is one click away.
 */
const QC_CHECKS = new Map([
  ["impossible_date", { test: "RTQC2", label: "Impossible date" }],
  ["impossible_location", { test: "RTQC3", label: "Impossible location" }],
  ["position_on_land", { test: "RTQC4", label: "Position on land" }],
  ["global_range", { test: "RTQC6", label: "Global range" }],
  ["regional_range", { test: "RTQC7", label: "Regional range" }],
  ["pressure_increasing", { test: "RTQC8", label: "Pressure increasing" }],
  ["spike", { test: "RTQC9", label: "Spike" }],
  ["gradient", { test: "RTQC11", label: "Gradient" }],
  ["digit_rollover", { test: "RTQC12", label: "Digit rollover" }],
  ["stuck_value", { test: "RTQC13", label: "Stuck value" }],
  ["density_inversion", { test: "RTQC14", label: "Density inversion" }],
  ["temp_to_psal", { test: null, label: "Temperature flag carried over" }],
]);

/**
 * What the flag scheme calls one value.
 *
 * @param {number|string|null} value
 * @returns {string|null} the meaning, or null for a value the scheme leaves
 *          open and for no flag at all.
 */
export function flagMeaning(value) {
  if (value === null || value === undefined) return null;
  return FLAG_MEANINGS.get(Number(value)) ?? null;
}

/**
 * A flag value with its meaning in brackets, for a cell: `4 (bad)`.
 *
 * @param {number|string|null} value
 * @returns {string}
 */
export function flagWithMeaning(value) {
  if (value === null || value === undefined) return "none";
  const meaning = flagMeaning(value);
  return meaning === null ? String(value) : `${value} (${meaning})`;
}

/**
 * What this product does with one value of its input flag, in a sentence.
 *
 * The scheme cannot answer this on its own. Which input values count as an
 * anomaly is `bad_flag_values`, a per-variable setting of the build, so the
 * same number can be an anomaly in one product and not in the next; a reader
 * looking at a contingency row is owed that rather than left to infer it from
 * the colour of the header.
 *
 * @param {number|null} value the input flag value.
 * @param {object} variable a `variables` entry of catalog.json.
 * @returns {string}
 */
export function inputFlagNote(value, variable) {
  if (value === null || value === undefined) {
    return (
      "No usable flag came with these observations, so there is nothing to " +
      "compare unless aiqclib flagged them."
    );
  }
  const meaning = flagMeaning(value);
  const named =
    meaning === null ? `Input flag ${value}` : `Input flag ${value} (${meaning})`;
  const number = Number(value);
  if ((variable.bad_flag_values ?? []).includes(number)) {
    return `${named}: counted as an anomaly for this product.`;
  }
  if ((variable.missing_flag_values ?? []).includes(number)) {
    return `${named}: carries no judgement, so there is nothing to compare.`;
  }
  return `${named}: not counted as an anomaly for this product.`;
}

/**
 * Split a per-item QC column into the check it belongs to.
 *
 * An item writes either one column per variable (`temp_qc_spike`) or one for
 * the whole profile (`qc_impossible_date`), and the set of items is whatever
 * the `aiqclib` run wrote, so a column this list does not know still has to
 * come back with something readable.
 *
 * @param {string} column the column name.
 * @param {Array<object>} [variables] the `variables` array of catalog.json.
 * @returns {{column: string, item: string, label: string, test: string|null,
 *           variable: object|null}}
 */
export function qcCheck(column, variables = []) {
  let item = column;
  let variable = null;
  for (const candidate of variables) {
    const prefix = `${candidate.name}_qc_`;
    if (column.startsWith(prefix)) {
      variable = candidate;
      item = column.slice(prefix.length);
      break;
    }
  }
  if (variable === null && column.startsWith("qc_")) {
    item = column.slice("qc_".length);
  }
  const known = QC_CHECKS.get(item);
  return {
    column,
    item,
    label: known ? known.label : sentenceCase(item),
    test: known ? known.test : null,
    variable,
  };
}

/**
 * The name to show for a per-item QC column, variable and all.
 *
 * @param {string} column the column name.
 * @param {Array<object>} [variables] the `variables` array of catalog.json.
 * @returns {string} for example `Spike (temperature)`.
 */
export function checkName(column, variables = []) {
  const check = qcCheck(column, variables);
  if (check.variable === null) return check.label;
  return `${check.label} (${check.variable.label.toLowerCase()})`;
}

/**
 * The tooltip for a check: its test number, where it has one.
 *
 * @param {string} column the column name.
 * @param {Array<object>} [variables] the `variables` array of catalog.json.
 * @returns {string}
 */
export function checkNote(column, variables = []) {
  const check = qcCheck(column, variables);
  const where = "Described on the About page.";
  return check.test === null ? where : `${check.test}. ${where}`;
}

/**
 * An underscored name as a sentence: `stuck_value` becomes `Stuck value`.
 *
 * @param {string} name
 * @returns {string}
 */
function sentenceCase(name) {
  const words = name.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
