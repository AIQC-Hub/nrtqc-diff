/**
 * What the Arctic, Baltic and Mediterranean regional centres say about the
 * near real time QC tests they run on CTD profiles, from their replies
 * (docs/REGIONAL_QC.md), and what their flags show of it.
 *
 * This is the site's one copy of that table: the Flag differences in NRT
 * page draws it, and the Flagged only by nrtqclib page reads it per check.
 * Change it together with docs/REGIONAL_QC.md.
 */

/** The regional products the replies describe, by dataset id. */
export const REGIONAL_PRODUCTS = [
  { id: "ar_ar", label: "Arctic regional" },
  { id: "bo_bo", label: "Baltic regional" },
  { id: "mo_mo", label: "Mediterranean regional" },
];

/** A test the centres were not asked about and did not mention. */
export const NOT_ASKED = "not asked";

/** A product whose QC the replies do not cover, such as a global one. */
export const NOT_DESCRIBED = "not described";

const ALL = (claim) => ({ ar_ar: claim, bo_bo: claim, mo_mo: claim });

/**
 * The tests, in the order the page lists them. `item` is the nrtqclib check
 * the test corresponds to, if any; `response` the tests of the
 * `items_response` table that measure it in the data.
 */
export const REGIONAL_TESTS = [
  {
    key: "global_range", label: "Global range", item: "global_range",
    nrtqclib: "yes", claims: ALL("yes"), response: ["global_range"],
  },
  {
    key: "regional_range", label: "Regional range", item: "regional_range",
    nrtqclib: "yes", claims: ALL("yes"), response: [],
  },
  {
    key: "spike", label: "Spike", item: "spike",
    nrtqclib: "yes", claims: ALL("yes"), response: ["spike"],
  },
  {
    key: "gradient", label: "Gradient", item: "gradient",
    nrtqclib: "yes", claims: { ar_ar: "yes", bo_bo: "no", mo_mo: "yes" },
    response: ["gradient"],
  },
  {
    key: "stuck_value", label: "Stuck value", item: "stuck_value",
    nrtqclib: "yes", claims: { ar_ar: "yes", bo_bo: "no", mo_mo: "yes" },
    response: ["stuck_value"],
  },
  {
    key: "density_inversion", label: "Density inversion", item: "density_inversion",
    nrtqclib: "yes, 0.03 kg/m³",
    claims: {
      ar_ar: "yes, 0.05 kg/m³ per dbar",
      bo_bo: "yes, 0.05 kg/m³ per dbar",
      mo_mo: "listed, but the reply says not run yet",
    },
    response: ["density_inversion", "density_inversion_per_dbar"],
  },
  {
    key: "pressure_increasing", label: "Pressure increasing", item: "pressure_increasing",
    nrtqclib: "yes",
    claims: { ar_ar: NOT_ASKED, bo_bo: "yes, on the whole profile first", mo_mo: NOT_ASKED },
    response: ["pressure_increasing"],
  },
  {
    key: "digit_rollover", label: "Digit rollover", item: "digit_rollover",
    nrtqclib: "yes", claims: ALL(NOT_ASKED), response: [],
  },
  {
    key: "temp_to_psal", label: "Temperature flag carried over to salinity", item: "temp_to_psal",
    nrtqclib: "yes", claims: ALL(NOT_ASKED), response: [],
  },
  {
    key: "frozen_profile", label: "Frozen profile", item: null,
    nrtqclib: "no", claims: ALL("yes"), response: [],
  },
  {
    key: "whole_profile", label: "Negative pressure, pressure spikes, soaking depth left in", item: null,
    nrtqclib: "no",
    claims: { ar_ar: NOT_ASKED, bo_bo: "yes, on the whole profile first", mo_mo: NOT_ASKED },
    response: [],
  },
];

/**
 * What the producer of a product says about the test behind an nrtqclib
 * check: its claim, "not asked", or "not described" for a product the
 * replies do not cover.
 */
export function producerRuns(datasetId, item) {
  if (!REGIONAL_PRODUCTS.some((p) => p.id === datasetId)) return NOT_DESCRIBED;
  const test = REGIONAL_TESTS.find((t) => t.item === item);
  return test ? test.claims[datasetId] : NOT_ASKED;
}

/** Fewer clear failures than this are too few to say anything by. */
export const MIN_CLEAR_FAILURES = 20;

/**
 * The share of one test's clear failures the input flags, per flag it is
 * set against, for one product: the `clear` bands of the test in meta.json,
 * summed. `share` is null where there are fewer than MIN_CLEAR_FAILURES.
 *
 * @param {object[]} response - The `items_response` table.
 * @param {object} meta - meta.json.
 * @param {string} datasetId - The product.
 * @param {string} testKey - The test, as in `meta.response_tests`.
 * @returns {{variable: string, n: number, flagged: number, share: number|null}[]}
 */
export function clearFailures(response, meta, datasetId, testKey) {
  const test = meta.response_tests.find((t) => t.key === testKey);
  if (!test) return [];
  const byVariable = new Map();
  for (const row of response) {
    if (row.dataset_id !== datasetId || row.test !== testKey) continue;
    const entry = byVariable.get(row.variable) ?? { variable: row.variable, n: 0, flagged: 0 };
    if (test.clear.includes(row.band)) {
      entry.n += row.n;
      entry.flagged += row.n_flagged;
    }
    byVariable.set(row.variable, entry);
  }
  const order = [...meta.variables.map((v) => v.name), "density", "pres"];
  return [...byVariable.values()]
    .sort((a, b) => order.indexOf(a.variable) - order.indexOf(b.variable))
    .map((e) => ({ ...e, share: e.n >= MIN_CLEAR_FAILURES ? e.flagged / e.n : null }));
}

/** The flag a row of `items_response` is set against, in words. */
export function responseFlagLabel(meta, variable) {
  if (variable === "pres") return "pressure flag";
  if (variable === "density") return "either flag";
  const found = meta.variables.find((v) => v.name === variable);
  return found ? found.label.toLowerCase() : variable;
}
