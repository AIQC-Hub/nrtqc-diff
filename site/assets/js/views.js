/**
 * The interactive pieces of the dashboard, as Observable-style views.
 *
 * Each exported function returns a DOM node carrying a `value` property and
 * emitting an `input` event when that value changes, which is the contract
 * Quarto's `viewof` needs. Nothing here queries the database; the page passes
 * in rows and gets a node back.
 */

/**
 * Create an element with a class and optional text.
 *
 * @param {string} tag
 * @param {string} className
 * @param {string} [text]
 * @returns {HTMLElement}
 */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/**
 * Give a node a read-only `value` and a way to announce changes.
 *
 * @param {HTMLElement} node
 * @param {*} initial
 * @returns {function(*): void} call it to set the value and fire `input`.
 */
function asView(node, initial) {
  let current = initial;
  Object.defineProperty(node, "value", {
    get: () => current,
    configurable: true,
  });
  return (next) => {
    current = next;
    node.dispatchEvent(new CustomEvent("input", { bubbles: true }));
  };
}

/**
 * Format a number for a table cell, leaving nulls visibly empty.
 *
 * @param {number|null|undefined} value
 * @returns {string}
 */
export function formatCount(value) {
  if (value === null || value === undefined) return "";
  return Number(value).toLocaleString("en-GB");
}

/**
 * Format a timestamp as a short UTC date and time.
 *
 * @param {*} value anything Date can parse, or null.
 * @returns {string}
 */
export function formatTime(value) {
  if (value === null || value === undefined) return "";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return String(value);
  return date.toISOString().slice(0, 16).replace("T", " ");
}

/**
 * The region and product tree of the sidebar.
 *
 * Regions are disclosure sections; products are the selectable leaves. A
 * product carrying several datasets selects all of them at once, so the
 * summary table below always answers "this product", however many files it
 * was built from.
 *
 * @param {object} catalog the parsed catalog.json.
 * @returns {HTMLElement} a view whose value is `{region, product, datasets}`.
 */
export function treeView(catalog) {
  const root = el("div", "nq-tree");
  const setValue = asView(root, null);
  const buttons = [];

  for (const region of catalog.regions) {
    const section = el("details", "nq-region");
    section.open = true;
    section.appendChild(el("summary", "nq-region-name", region.name));

    const list = el("ul", "nq-products");
    for (const product of region.products) {
      const profiles = product.datasets.reduce((n, d) => n + d.n_profiles, 0);
      const item = el("li", "nq-product");
      const button = el("button", "nq-product-button");
      button.type = "button";
      button.appendChild(el("span", "nq-product-name", product.name));
      button.appendChild(el("span", "nq-product-count", formatCount(profiles)));
      button.addEventListener("click", () => {
        for (const other of buttons) other.classList.remove("is-selected");
        button.classList.add("is-selected");
        setValue({
          region: region.name,
          product: product.name,
          datasets: product.datasets,
        });
      });
      buttons.push(button);
      item.appendChild(button);
      list.appendChild(item);
    }
    section.appendChild(list);
    root.appendChild(section);
  }

  if (buttons.length > 0) {
    // Opening on an empty main panel hides what the page is for, so the first
    // product is selected as soon as the tree exists.
    queueMicrotask(() => buttons[0].click());
  }
  return root;
}

/**
 * A sortable table whose rows can be clicked to select one.
 *
 * @param {Array<object>} rows
 * @param {object} options
 * @param {Array<object>} options.columns `{key, label, align, format, title}`,
 *        or `{key, label, align, render}` for a cell that is a node rather
 *        than text. A rendered cell still sorts on `row[key]`.
 * @param {string} options.rowKey the column holding a unique row identifier.
 * @param {string} [options.sortKey] the column to sort by initially.
 * @param {boolean} [options.descending=true] the initial sort direction.
 * @param {boolean} [options.autoSelect=false] select the first row on creation.
 * @param {boolean} [options.selectable=true] whether clicking a row selects
 *        it. Pass false for a table nothing downstream reads.
 * @param {string} [options.empty] the message shown when there are no rows.
 * @returns {HTMLElement} a view whose value is the selected row, or null.
 */
export function tableView(rows, options) {
  const {
    columns,
    rowKey,
    sortKey = null,
    descending = true,
    selectable = true,
    empty = "Nothing to show.",
  } = options;

  const root = el("div", `nq-table-wrap${selectable ? "" : " nq-static"}`);
  const setValue = asView(root, null);

  if (!rows || rows.length === 0) {
    root.appendChild(el("p", "nq-empty", empty));
    return root;
  }

  let sortColumn = sortKey ?? columns[0].key;
  let sortDescending = descending;
  let selectedKey = null;

  const table = el("table", "nq-table");
  const head = el("thead");
  const headRow = el("tr");
  for (const column of columns) {
    const cell = el("th", `nq-align-${column.align ?? "right"}`);
    const button = el("button", "nq-sort", column.label);
    button.type = "button";
    if (column.title) button.title = column.title;
    button.addEventListener("click", () => {
      if (sortColumn === column.key) {
        sortDescending = !sortDescending;
      } else {
        sortColumn = column.key;
        sortDescending = true;
      }
      draw();
    });
    cell.appendChild(button);
    headRow.appendChild(cell);
  }
  head.appendChild(headRow);
  table.appendChild(head);

  const body = el("tbody");
  table.appendChild(body);
  root.appendChild(table);

  /** Sort the rows and rebuild the body. */
  function draw() {
    const sorted = [...rows].sort((a, b) => compare(a[sortColumn], b[sortColumn]));
    if (sortDescending) sorted.reverse();

    body.replaceChildren();
    for (const row of sorted) {
      const tr = el("tr", "nq-row");
      if (row[rowKey] === selectedKey) tr.classList.add("is-selected");
      for (const column of columns) {
        const cell = el("td", `nq-align-${column.align ?? "right"}`);
        if (column.render) {
          cell.appendChild(column.render(row[column.key], row));
        } else {
          const format = column.format ?? formatCount;
          cell.textContent = format(row[column.key], row);
        }
        if (column.cellClass) cell.classList.add(column.cellClass(row));
        tr.appendChild(cell);
      }
      if (selectable) {
        tr.addEventListener("click", () => {
          selectedKey = row[rowKey];
          for (const other of body.children) other.classList.remove("is-selected");
          tr.classList.add("is-selected");
          setValue(row);
        });
      }
      body.appendChild(tr);
    }
  }

  /**
   * Order two cell values, keeping nulls at the bottom of a descending sort.
   *
   * @param {*} a
   * @param {*} b
   * @returns {number}
   */
  function compare(a, b) {
    if (a === b) return 0;
    if (a === null || a === undefined) return -1;
    if (b === null || b === undefined) return 1;
    if (typeof a === "number" && typeof b === "number") return a - b;
    return String(a).localeCompare(String(b));
  }

  draw();
  if (options.autoSelect && body.firstChild) {
    // Landing on a table with no profile chosen leaves the panels below
    // empty, which reads as a broken page rather than as a waiting one.
    queueMicrotask(() => body.firstChild.click());
  }
  return root;
}

/**
 * A contingency table of input flag value against computed flag value.
 *
 * @param {Array<object>} rows `{existing_flag, new_flag, n}` in any order.
 * @param {object} [options]
 * @param {string} [options.rowLabel] the header above the input flag values.
 * @param {string} [options.columnLabel] the header above the computed values.
 * @param {Array<number>} [options.badValues] input values counted as anomalies,
 *                        which are marked in the row header.
 * @returns {HTMLElement}
 */
export function contingencyTable(rows, options = {}) {
  const {
    rowLabel = "Input flag",
    columnLabel = "aiqclib flag",
    badValues = [],
  } = options;

  const root = el("div", "nq-table-wrap");
  if (!rows || rows.length === 0) {
    root.appendChild(el("p", "nq-empty", "No observations."));
    return root;
  }

  const rowValues = unique(rows.map((r) => r.existing_flag));
  const columnValues = unique(rows.map((r) => r.new_flag));
  const counts = new Map(
    rows.map((r) => [`${r.existing_flag}|${r.new_flag}`, Number(r.n)])
  );
  const total = rows.reduce((sum, r) => sum + Number(r.n), 0);

  const table = el("table", "nq-table nq-contingency");
  const head = el("thead");
  const topRow = el("tr");
  topRow.appendChild(el("th", "nq-align-left", rowLabel));
  const spanning = el("th", "nq-align-center", columnLabel);
  spanning.colSpan = columnValues.length + 1;
  topRow.appendChild(spanning);
  head.appendChild(topRow);

  const valueRow = el("tr");
  valueRow.appendChild(el("th", "nq-align-left", ""));
  for (const value of columnValues) {
    valueRow.appendChild(el("th", "nq-align-right", label(value)));
  }
  valueRow.appendChild(el("th", "nq-align-right", "Total"));
  head.appendChild(valueRow);
  table.appendChild(head);

  const body = el("tbody");
  for (const rowValue of rowValues) {
    const tr = el("tr");
    const header = el("th", "nq-align-left", label(rowValue));
    if (badValues.includes(rowValue)) header.classList.add("is-bad");
    tr.appendChild(header);

    let rowTotal = 0;
    for (const columnValue of columnValues) {
      const count = counts.get(`${rowValue}|${columnValue}`) ?? 0;
      rowTotal += count;
      const cell = el("td", "nq-align-right", count === 0 ? "" : formatCount(count));
      if (count > 0) cell.title = `${((100 * count) / total).toFixed(1)}% of the profile`;
      tr.appendChild(cell);
    }
    tr.appendChild(el("td", "nq-align-right nq-total", formatCount(rowTotal)));
    body.appendChild(tr);
  }

  const totals = el("tr", "nq-total-row");
  totals.appendChild(el("th", "nq-align-left", "Total"));
  for (const columnValue of columnValues) {
    const columnTotal = rows
      .filter((r) => r.new_flag === columnValue)
      .reduce((sum, r) => sum + Number(r.n), 0);
    totals.appendChild(el("td", "nq-align-right", formatCount(columnTotal)));
  }
  totals.appendChild(el("td", "nq-align-right nq-total", formatCount(total)));
  body.appendChild(totals);

  table.appendChild(body);
  root.appendChild(table);
  return root;

  /**
   * Render one flag value, naming the null case rather than printing "null".
   *
   * @param {number|null} value
   * @returns {string}
   */
  function label(value) {
    return value === null || value === undefined ? "none" : String(value);
  }
}

/**
 * A one-line stacked bar: how a total splits between a few categories.
 *
 * Meant for a table cell, beside the counts it summarises. That pairing is
 * what makes it legible: the bar carries the shape, the numbers next to it
 * carry the values, so nothing here is encoded by colour alone.
 *
 * Segments are separated by a 2px gap in the surface colour rather than by a
 * border, and a category with any count at all keeps a visible sliver, so a
 * product with a handful of observations in a category does not read as
 * having none.
 *
 * @param {Array<object>} parts `{label, value, color}`, drawn in order.
 * @param {object} [options]
 * @param {string} [options.empty="no observations"] the title used when every
 *                 part is zero.
 * @returns {HTMLElement}
 */
export function compositionBar(parts, options = {}) {
  const { empty = "no observations" } = options;
  const root = el("div", "nq-bar");
  const total = parts.reduce((sum, part) => sum + Number(part.value || 0), 0);

  if (total === 0) {
    root.classList.add("is-empty");
    root.title = empty;
    return root;
  }

  for (const part of parts) {
    const value = Number(part.value || 0);
    if (value === 0) continue;
    const share = (100 * value) / total;
    const segment = el("span", "nq-bar-part");
    segment.style.backgroundColor = part.color;
    segment.style.width = `${share}%`;
    segment.title = `${part.label}: ${formatCount(value)} (${share.toFixed(1)}%)`;
    root.appendChild(segment);
  }
  return root;
}

/**
 * The colour key shared by the plots and the summary table.
 *
 * @param {Array<object>} statuses the `statuses` array of catalog.json.
 * @returns {HTMLElement}
 */
export function statusLegend(statuses) {
  const root = el("div", "nq-legend");
  for (const status of statuses) {
    const item = el("span", "nq-legend-item");
    const swatch = el("span", "nq-swatch");
    swatch.style.backgroundColor = status.color;
    item.appendChild(swatch);
    item.appendChild(el("span", "nq-legend-label", status.label));
    root.appendChild(item);
  }
  return root;
}

/**
 * The distinct values of an array, sorted, with null last.
 *
 * @param {Array<*>} values
 * @returns {Array<*>}
 */
function unique(values) {
  const seen = [...new Set(values)];
  seen.sort((a, b) => {
    if (a === null || a === undefined) return 1;
    if (b === null || b === undefined) return -1;
    return a - b;
  });
  return seen;
}
