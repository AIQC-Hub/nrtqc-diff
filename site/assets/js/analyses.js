/**
 * The Analyses pages: reading the analysis tables and showing the figures.
 *
 * Nothing here queries DuckDB. The analyses are small CSV tables that
 * `nrtqc-diff analyse` writes to `data/analyses/tables/`, and SVG figures the
 * R scripts under `analysis/` draw from them into `data/analyses/figures/`;
 * a page fetches what it shows. Both are optional in a deployment: a data
 * release without them, or the demo build, renders pages that say so rather
 * than failing. See docs/ANALYSES.md.
 */

import { DATA_ROOT } from "./db.js";
import { formatPercent, tableView } from "./views.js";

/** Where the analyses are published. */
export const ANALYSES_ROOT = DATA_ROOT + "analyses/";

/** The `analyses_format` these pages read. See ANALYSES_FORMAT in analyse.py. */
const ANALYSES_FORMAT = 10;

/**
 * Columns kept as text whatever they hold: a platform code such as 6901234
 * is a name, and a flag value read as a number is fine but an empty data
 * mode is a value too.
 */
const TEXT_COLUMNS = new Set([
  "dataset_id",
  "platform_code",
  "data_mode",
  "obs_data_mode",
  "combination",
  "status",
  "group",
  "item",
  "column",
  "variable",
]);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/**
 * Read `meta.json`, or null when the analyses were not published.
 *
 * @returns {Promise<object|null>}
 */
export async function loadAnalyses() {
  const response = await fetch(ANALYSES_ROOT + "meta.json");
  if (!response.ok) return null;
  const meta = await response.json();
  if (meta.analyses_format !== ANALYSES_FORMAT) {
    throw new Error(
      `The analyses were written in format ${meta.analyses_format} and this ` +
        `site reads ${ANALYSES_FORMAT}. Run: uv run nrtqc-diff analyse`
    );
  }
  return meta;
}

/**
 * Split CSV text into rows of fields, honouring quoted fields.
 *
 * @param {string} text
 * @returns {Array<Array<string>>}
 */
function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') {
        field += '"';
        i += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        field += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      row.push(field);
      field = "";
    } else if (char === "\n" || char === "\r") {
      if (char === "\r" && text[i + 1] === "\n") i += 1;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += char;
    }
  }
  if (field !== "" || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

/**
 * Read one analysis table as an array of objects, or null when it is absent.
 *
 * Numbers are numbers and empty cells are null, except in the columns of
 * TEXT_COLUMNS, which stay strings.
 *
 * @param {string} name the table, without `.csv`.
 * @returns {Promise<Array<object>|null>}
 */
export async function loadAnalysisTable(name) {
  const response = await fetch(`${ANALYSES_ROOT}tables/${name}.csv`);
  if (!response.ok) return null;
  const [header, ...body] = parseCsv(await response.text());
  return body.map((fields) => {
    const row = {};
    header.forEach((key, index) => {
      const raw = fields[index] ?? "";
      if (TEXT_COLUMNS.has(key)) {
        row[key] = raw;
      } else if (raw === "") {
        row[key] = null;
      } else if (raw === "true" || raw === "false") {
        row[key] = raw === "true";
      } else {
        const number = Number(raw);
        row[key] = Number.isNaN(number) ? raw : number;
      }
    });
    return row;
  });
}

/**
 * A note that a part of the analyses is not in this deployment.
 *
 * @param {string} [what] what is missing.
 * @returns {HTMLElement}
 */
export function analysesMissing(what = "The analyses") {
  const note = el("p", "nq-empty");
  note.append(
    `${what} were not published with this data. They are made where the ` +
      "data is, with "
  );
  note.append(el("code", null, "uv run nrtqc-diff analyse"));
  note.append(" and ");
  note.append(el("code", null, "Rscript analysis/run_all.R"));
  note.append(".");
  return note;
}

/**
 * One figure the R scripts drew, with its caption and a link to the PDF.
 *
 * @param {string} name the figure, without extension.
 * @param {string} caption what it shows, in a sentence or two.
 * @param {string} alt a text alternative for the image.
 * @returns {HTMLElement}
 */
export function analysisFigure(name, caption, alt) {
  const figure = el("figure", "nq-figure");
  const image = el("img");
  image.src = `${ANALYSES_ROOT}figures/${name}.svg`;
  image.alt = alt ?? caption;
  image.loading = "lazy";
  image.addEventListener("error", () => {
    image.replaceWith(analysesMissing("This figure and the others"));
  });
  const text = el("figcaption");
  text.append(caption + " ");
  const pdf = el("a", null, "PDF");
  pdf.href = `${ANALYSES_ROOT}figures/${name}.pdf`;
  text.append("(", pdf, ")");
  figure.append(image, text);
  return figure;
}

/**
 * Spread one column of a long table into one column per value.
 *
 * @param {Array<object>} rows the long table.
 * @param {object} options
 * @param {Array<string>} options.index the columns identifying a wide row.
 * @param {string} options.column the column whose values become columns.
 * @param {string} options.value the column summed into each cell.
 * @param {function} [options.name] maps a value to its column name.
 * @returns {Array<object>} one row per index, missing cells 0.
 */
export function widen(rows, { index, column, value, name = (v) => String(v) }) {
  const byKey = new Map();
  const names = [];
  for (const row of rows) {
    const key = index.map((field) => row[field]).join("\u0000");
    if (!byKey.has(key)) {
      byKey.set(key, Object.fromEntries(index.map((field) => [field, row[field]])));
    }
    const target = byKey.get(key);
    const field = name(row[column]);
    if (!names.includes(field)) names.push(field);
    target[field] = (target[field] ?? 0) + Number(row[value] ?? 0);
  }
  const wide = [...byKey.values()];
  for (const row of wide) for (const field of names) row[field] ??= 0;
  return wide;
}

/** The short name of a dataset: region without "Sea" or "Ocean", then product. */
export function datasetName(meta, id) {
  const dataset = meta.datasets.find((d) => d.id === id);
  if (!dataset) return id;
  return `${dataset.region.replace(/ (Ocean|Sea)$/, "")} ${dataset.product}`;
}

/** The order the configuration lists the datasets in, for sorting rows. */
export function datasetOrder(meta, id) {
  return meta.datasets.findIndex((d) => d.id === id);
}

/** A share as a percentage with one decimal, empty for nothing to divide by. */
export function formatShare(part, whole) {
  if (!whole) return "";
  return `${((100 * part) / whole).toFixed(1)}%`;
}

/**
 * A share as a percentage that stays readable when it is small: one decimal
 * from 1% up, two significant digits below (0.35%, 0.0093%).
 */
export function formatSmallShare(part, whole) {
  if (!whole) return "";
  return formatPercent((100 * part) / whole);
}

/** A rate per 1,000 with one decimal. */
export function formatRate(value) {
  return value === null || value === undefined ? "" : Number(value).toFixed(1);
}

/** Per 1,000, or null when there is nothing to divide by. */
export function perThousandOf(part, whole) {
  return whole > 0 ? (1000 * part) / whole : null;
}

/**
 * A table of an analysis, in the order the page hands its rows over.
 *
 * `tableView` sorts on its first column unless told otherwise, which put the
 * datasets in alphabetical order rather than the configuration's. Each row
 * is given its position here and the table opens sorted on that; a click on
 * a heading still sorts by that column.
 *
 * @param {Array<object>|null} rows
 * @param {Array<object>} columns as for `tableView`; `wrap: true` lets a
 *        long text cell wrap instead of widening the table.
 * @param {object} [options] passed on to `tableView`.
 * @returns {HTMLElement}
 */
export function analysisTable(rows, columns, options = {}) {
  const box = el("div", "nq-analysis-table");
  const ordered = (rows ?? []).map((row, index) => ({
    _key: index,
    _order: index,
    ...row,
  }));
  box.append(
    tableView(ordered, {
      columns: columns.map((column) =>
        column.wrap ? { ...column, cellClass: () => "nq-wrap" } : column
      ),
      rowKey: "_key",
      selectable: false,
      sortKey: "_order",
      descending: false,
      empty: "Not in this data.",
      ...options,
    })
  );
  return box;
}
