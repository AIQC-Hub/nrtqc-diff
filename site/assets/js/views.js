/**
 * The interactive pieces of the dashboard, as Observable-style views.
 *
 * Each exported function returns a DOM node carrying a `value` property and
 * emitting an `input` event when that value changes, which is the contract
 * Quarto's `viewof` needs. Nothing here queries the database; the page passes
 * in rows and gets a node back.
 */

import { flagMeaning, inputFlagNote } from "./labels.js";

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
 * @param {function} [options.onSortChange] `(key, descending) => void`, called
 *        instead of sorting when a header is clicked. Pass it when the rows
 *        are one page of a larger answer: the table then shows them in the
 *        order it was given and leaves the ordering to whoever fetched them.
 * @param {function} [options.expand] `(row) => HTMLElement`. When given, a
 *        click on a row opens a full-width row under it holding what this
 *        returns, and a second click closes it, instead of selecting the row.
 *        Each row's node is built the first time it is opened and kept, so
 *        re-sorting the table or closing and reopening a row finds it as the
 *        reader left it.
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
    onSortChange = null,
    expand = null,
  } = options;

  // A table that expands answers a click, so it is not static even though it
  // selects nothing.
  const mode = expand ? " nq-expandable" : selectable ? "" : " nq-static";
  const root = el("div", `nq-table-wrap${mode}`);
  const setValue = asView(root, null);

  if (!rows || rows.length === 0) {
    root.appendChild(el("p", "nq-empty", empty));
    return root;
  }

  let sortColumn = sortKey ?? columns[0].key;
  let sortDescending = descending;
  let selectedKey = null;

  // What each expanded row shows, by row key, and which of them are open.
  const details = new Map();
  const open = new Set();

  const table = el("table", "nq-table");
  const head = el("thead");
  const headRow = el("tr");
  if (expand) {
    // The column the open and closed markers sit in. It has no heading and
    // does not sort: there is nothing in it to sort by.
    headRow.appendChild(el("th", "nq-expand-cell"));
  }
  for (const column of columns) {
    const cell = el("th", `nq-align-${column.align ?? "right"}`);
    const button = el("button", "nq-sort", column.label);
    button.type = "button";
    if (column.title) button.title = column.title;
    button.addEventListener("click", () => {
      const nextDescending = sortColumn === column.key ? !sortDescending : true;
      if (onSortChange) {
        // The rows here are a page of a larger answer, so ordering them
        // would order the page and say nothing about the rest. Whoever
        // fetched them re-fetches in the new order instead.
        onSortChange(column.key, nextDescending);
        return;
      }
      sortColumn = column.key;
      sortDescending = nextDescending;
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
    // Rows that arrived in an order chosen elsewhere keep it: re-sorting them
    // here would break the ties that order settled, and the column being
    // sorted on is the one it was already fetched by.
    const sorted = onSortChange
      ? [...rows]
      : sortedRows();

    body.replaceChildren();
    for (const row of sorted) {
      const tr = el("tr", "nq-row");
      if (row[rowKey] === selectedKey) tr.classList.add("is-selected");
      if (expand) addExpandCell(tr, row);
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
      if (expand) {
        tr.addEventListener("click", () => toggle(tr, row));
        body.appendChild(tr);
        if (open.has(row[rowKey])) body.appendChild(detailRow(row));
        continue;
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
   * The marker at the start of an expandable row.
   *
   * A button, so the row can be opened from the keyboard and a screen reader
   * says whether it is open; the click it receives is the row's click, which
   * bubbles up to the row's own listener.
   *
   * @param {HTMLElement} tr
   * @param {object} row
   */
  function addExpandCell(tr, row) {
    const cell = el("td", "nq-expand-cell");
    const button = el("button", "nq-expand");
    button.type = "button";
    const isOpen = open.has(row[rowKey]);
    button.setAttribute("aria-expanded", String(isOpen));
    button.setAttribute("aria-label", `Details of ${row[rowKey]}`);
    if (isOpen) tr.classList.add("is-open");
    cell.appendChild(button);
    tr.appendChild(cell);
  }

  /**
   * Open a row if it is closed and close it if it is open.
   *
   * @param {HTMLElement} tr the row that was clicked.
   * @param {object} row
   */
  function toggle(tr, row) {
    const key = row[rowKey];
    const button = tr.querySelector(".nq-expand");
    if (open.has(key)) {
      open.delete(key);
      tr.nextElementSibling?.remove();
      tr.classList.remove("is-open");
      button.setAttribute("aria-expanded", "false");
    } else {
      open.add(key);
      tr.after(detailRow(row));
      tr.classList.add("is-open");
      button.setAttribute("aria-expanded", "true");
    }
  }

  /**
   * The full-width row under an open row, built once per row key.
   *
   * @param {object} row
   * @returns {HTMLElement}
   */
  function detailRow(row) {
    const key = row[rowKey];
    if (!details.has(key)) {
      const tr = el("tr", "nq-detail");
      const cell = el("td", "nq-detail-cell");
      cell.colSpan = columns.length + 1;
      const node = expand(row);
      // What is inside may be a view of its own. Its value is nothing this
      // table offers, so its events stop here rather than reaching a page
      // that reads this table as a view.
      node.addEventListener("input", (event) => event.stopPropagation());
      cell.appendChild(node);
      tr.appendChild(cell);
      details.set(key, tr);
    }
    return details.get(key);
  }

  /** The rows in the order the header says, for a table that owns them. */
  function sortedRows() {
    const sorted = [...rows].sort((a, b) => compare(a[sortColumn], b[sortColumn]));
    if (sortDescending) sorted.reverse();
    return sorted;
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
 * A searchable, paged list of profiles, as one view.
 *
 * A product here can hold 81,541 profiles and the browser cannot have them
 * all on the page: 81,541 rows take six seconds to build and five more every
 * time they are re-sorted. So the list shows a page, and the search, the
 * order and the page boundaries are all decided by the query rather than by
 * this file. That is also what makes the search worth having: it reaches the
 * whole product, not the page in front of the reader.
 *
 * The queries arrive as functions rather than imports, which keeps this file
 * clear of the database and lets a test drive the list with rows of its own.
 *
 * The value is the selected row, the same contract :func:`tableView` offers,
 * so a page reading it needs to know nothing about the paging around it.
 *
 * @param {object} options
 * @param {Array<object>} options.columns column definitions for `tableView`.
 * @param {string} options.rowKey the column holding a unique row identifier.
 * @param {function} options.fetchRows `(request) => Promise<Array<object>>`,
 *        given `{limit, offset, search, orderBy, descending}`.
 * @param {function} options.countRows `(search) => Promise<number>`, the size
 *        of the whole answer the page is a slice of.
 * @param {Array<number>} [options.pageSizes] the page sizes offered.
 * @param {number} [options.pageSize] the page size to open on.
 * @param {string} [options.sortKey] the column to order by first.
 * @param {boolean} [options.descending=true] the direction to open in.
 * @param {string} [options.empty] the message shown when nothing matches.
 * @param {string} [options.searchLabel] the placeholder of the search box.
 * @param {boolean} [options.searchable=true] whether to offer the search box.
 *        A list already narrowed to a platform has little left to search.
 * @param {boolean} [options.selectable=true] whether a click selects a
 *        profile. Pass false for a list nothing downstream reads; the first
 *        row is then not selected on arrival either.
 * @returns {HTMLElement} a view whose value is the selected row, or null.
 */
export function profileListView(options) {
  const {
    columns,
    rowKey,
    fetchRows,
    countRows,
    pageSizes = [100, 500, 2000],
    pageSize = 500,
    sortKey = "n_disagree",
    descending = true,
    empty = "Nothing to show.",
    searchLabel = "Profile or platform",
    searchable = true,
    selectable = true,
  } = options;

  const root = el("div", "nq-profile-list");
  const setValue = asView(root, null);

  const state = {
    search: "",
    page: 0,
    size: pageSizes.includes(pageSize) ? pageSize : pageSizes[0],
    orderBy: sortKey,
    descending,
    total: 0,
  };

  // Every load is numbered, so a slow answer cannot land on top of a newer
  // one. Typing into the search box starts a query per pause and they do not
  // necessarily come back in the order they were asked.
  let latest = 0;
  let typing = null;

  const controls = el("div", "nq-list-controls");
  const search = el("input", "nq-search");
  search.type = "search";
  search.placeholder = searchLabel;
  search.setAttribute("aria-label", searchLabel);
  const size = el("select", "nq-page-size");
  size.setAttribute("aria-label", "Profiles per page");
  for (const value of pageSizes) {
    const option = el("option", null, formatCount(value));
    option.value = String(value);
    if (value === state.size) option.selected = true;
    size.appendChild(option);
  }
  if (searchable) controls.append(search);
  controls.append(size);

  const host = el("div", "nq-list-table");

  const pager = el("div", "nq-pager");
  const previous = el("button", "nq-page-step", "Previous");
  previous.type = "button";
  const label = el("span", "nq-pager-label", "Loading");
  const next = el("button", "nq-page-step", "Next");
  next.type = "button";
  pager.append(previous, label, next);

  root.append(controls, host, pager);

  /** Fetch the current page and draw it, unless a newer load overtakes it. */
  async function load() {
    const token = ++latest;
    root.classList.add("is-loading");
    const [rows, total] = await Promise.all([
      fetchRows({
        limit: state.size,
        offset: state.page * state.size,
        search: state.search,
        orderBy: state.orderBy,
        descending: state.descending,
      }),
      countRows(state.search),
    ]);
    if (token !== latest) return;
    state.total = total;
    if (rows.length === 0 && state.page > 0) {
      // A search, or a larger page size, can leave the reader past the end of
      // an answer that was longer when they got there. Step back to the last
      // page that exists rather than show them an empty list.
      state.page = Math.max(0, Math.ceil(total / state.size) - 1);
      load();
      return;
    }
    root.classList.remove("is-loading");
    draw(rows);
  }

  /** Replace the table with one page of rows, and say where the page sits. */
  function draw(rows) {
    const table = tableView(rows, {
      columns,
      rowKey,
      sortKey: state.orderBy,
      descending: state.descending,
      autoSelect: selectable,
      selectable,
      empty,
      onSortChange: (key, isDescending) => {
        state.orderBy = key;
        state.descending = isDescending;
        // A new order makes a new first page: keeping the offset would show
        // the reader rows 500 to 1,000 of an order they have not seen.
        state.page = 0;
        load();
      },
    });
    // The inner table is an implementation detail, so its events stop here
    // and this view re-emits them as its own.
    table.addEventListener("input", (event) => {
      event.stopPropagation();
      setValue(table.value);
    });
    host.replaceChildren(table);
    setValue(table.value);

    const first = state.total === 0 ? 0 : state.page * state.size + 1;
    const last = state.page * state.size + rows.length;
    label.textContent =
      state.total === 0
        ? "No profiles"
        : `${formatCount(first)}-${formatCount(last)} of ${formatCount(state.total)}`;
    previous.disabled = state.page === 0;
    next.disabled = last >= state.total;
  }

  search.addEventListener("input", () => {
    // One query per keystroke would run six for a platform code; the pause
    // between letters is what tells typing apart from having typed.
    clearTimeout(typing);
    typing = setTimeout(() => {
      state.search = search.value;
      state.page = 0;
      load();
    }, 250);
  });

  size.addEventListener("change", () => {
    // Hold the reader's place rather than sending them back to the top: they
    // asked for a different page size, not for a different part of the list.
    const anchor = state.page * state.size;
    state.size = Number(size.value);
    state.page = Math.floor(anchor / state.size);
    load();
  });

  previous.addEventListener("click", () => {
    if (state.page === 0) return;
    state.page -= 1;
    load();
  });

  next.addEventListener("click", () => {
    state.page += 1;
    load();
  });

  load();
  return root;
}

/**
 * A contingency table of input flag value against computed flag value.
 *
 * Both axes are named twice over: what the column is, in words, and the
 * column name it has in the data underneath. The first is for a reader
 * meeting the comparison for the first time, the second for whoever runs the
 * build and thinks in `temp_qc`. The same goes for the values: a flag number
 * carries the scheme's word for it wherever the scheme fixes one.
 *
 * @param {Array<object>} rows `{existing_flag, new_flag, n}` in any order.
 * @param {object} options
 * @param {object} options.variable a `variables` entry of catalog.json. It
 *        supplies both column names and the values this product counts as an
 *        anomaly, which are marked in the row header.
 * @param {string} [options.rowLabel] what the input flag is, in words.
 * @param {string} [options.columnLabel] what the computed flag is, in words.
 * @returns {HTMLElement}
 */
export function contingencyTable(rows, options = {}) {
  const {
    variable,
    rowLabel = "Flag in the input data",
    columnLabel = "Flag aiqclib computed",
  } = options;
  const badValues = variable.bad_flag_values ?? [];

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
  topRow.appendChild(named("nq-align-left", rowLabel, variable.flag));
  const spanning = named("nq-align-center", columnLabel, variable.nrt_flag);
  spanning.colSpan = columnValues.length + 1;
  topRow.appendChild(spanning);
  head.appendChild(topRow);

  const valueRow = el("tr");
  valueRow.appendChild(el("th", "nq-align-left", ""));
  for (const value of columnValues) {
    // The computed flag always follows the scheme, so every value it can
    // hold has a word for it and none of them needs the build's opinion.
    valueRow.appendChild(valueHeader(value, null));
  }
  valueRow.appendChild(el("th", "nq-align-right", "Total"));
  head.appendChild(valueRow);
  table.appendChild(head);

  const body = el("tbody");
  for (const rowValue of rowValues) {
    const tr = el("tr");
    const header = valueHeader(rowValue, inputFlagNote(rowValue, variable));
    header.className = "nq-align-left";
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
   * A header cell carrying a name in words and its column name under it.
   *
   * @param {string} className
   * @param {string} text what the column is, in words.
   * @param {string} columnName the name it has in the data.
   * @returns {HTMLElement}
   */
  function named(className, text, columnName) {
    const cell = el("th", className, text);
    cell.appendChild(el("span", "nq-colname", columnName));
    return cell;
  }

  /**
   * A header cell for one flag value, with the scheme's word under it.
   *
   * @param {number|null} value
   * @param {string|null} note a tooltip, where there is more to say than the
   *        word under the number.
   * @returns {HTMLElement}
   */
  function valueHeader(value, note) {
    const cell = el("th", "nq-align-right", label(value));
    const meaning = flagMeaning(value);
    if (meaning !== null) {
      cell.appendChild(el("span", "nq-flag-meaning", meaning));
    }
    if (note !== null) cell.title = note;
    return cell;
  }

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
