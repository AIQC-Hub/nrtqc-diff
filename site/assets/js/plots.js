/**
 * The profile plots: measurement on the x axis, pressure down the y axis.
 *
 * Plotly is used for one reason: pan, box zoom, scroll zoom and reset come
 * with it, and a QC reader spends most of the time zooming into a few
 * decibars around a flagged point. It is vendored locally by
 * scripts/fetch_assets.sh; see docs/SITE.md for the alternatives considered.
 */

import { LIB_ROOT } from "./db.js";

let plotlyPromise = null;

/**
 * Load Plotly once per page, from the vendored copy.
 *
 * @returns {Promise<object>} the Plotly namespace.
 */
export function loadPlotly() {
  if (plotlyPromise === null) {
    plotlyPromise = new Promise((resolve, reject) => {
      if (window.Plotly) {
        resolve(window.Plotly);
        return;
      }
      const script = document.createElement("script");
      script.src = LIB_ROOT + "plotly/plotly.min.js";
      script.onload = () => resolve(window.Plotly);
      script.onerror = () =>
        reject(
          new Error(
            "Could not load libs/plotly/plotly.min.js. Run: bash scripts/fetch_assets.sh"
          )
        );
      document.head.appendChild(script);
    });
  }
  return plotlyPromise;
}

/**
 * The interval an axis is allowed to reach, from the values drawn on it.
 *
 * Plotly lets you zoom and pan as far as you like by default, and a reader
 * who scrolls out once too often ends up looking at an empty frame with the
 * cast a speck in the corner, then has to double click to find it again.
 * `minallowed` and `maxallowed` stop the range leaving this interval, so
 * zooming out ends at the data rather than continuing into nothing.
 *
 * The margin is what keeps a point at the extreme off the frame edge: the
 * limits have to sit slightly outside the data, or the initial autorange gets
 * clamped to the data itself and the deepest marker is drawn on the axis.
 *
 * @param {Array<number>} values the values drawn on the axis, nulls included.
 * @param {number} [margin=0.05] how far past the data the limits sit, as a
 *                 share of the span.
 * @returns {object} `{minallowed, maxallowed}`, or `{}` when there is nothing
 *          finite to bound, which leaves the axis unconstrained.
 */
export function axisBounds(values, margin = 0.05) {
  let low = Infinity;
  let high = -Infinity;
  for (const value of values) {
    if (!Number.isFinite(value)) continue;
    if (value < low) low = value;
    if (value > high) high = value;
  }
  if (low > high) return {};

  const span = high - low;
  // A profile can be one observation deep, or hold one value repeated down
  // the cast. An axis whose two limits are equal has nothing to draw in, so
  // fall back to a margin that does not depend on the span.
  const pad = span > 0 ? span * margin : Math.max(Math.abs(high) * margin, 0.5);
  return { minallowed: low - pad, maxallowed: high + pad };
}

/**
 * Keep a drag from squeezing the range against the axis limits.
 *
 * Plotly enforces `minallowed` and `maxallowed` one edge at a time. Drag past
 * a limit and the edge against it is held while the far edge keeps coming, so
 * the pan quietly turns into a zoom: an axis already showing its whole range
 * creeps inwards every time the reader drags. A pan is a translation, so the
 * span is the thing to defend. Catch the drag, and if the span came back
 * smaller, restore it against whichever limit the drag ran into.
 *
 * Only a drag of the pan tool is watched. A box zoom is a drag that narrows
 * the range on purpose, and a scroll wheel changes it with no mouse button
 * down at all, so both pass straight through.
 *
 * @param {object} Plotly the Plotly namespace.
 * @param {HTMLElement} node the plot node, already drawn.
 */
function holdSpanWhileDragging(Plotly, node) {
  const axes = ["xaxis", "yaxis"];
  let started = null;
  let correcting = false;

  // `_fullLayout` is the only place the current range can be read from
  // whatever put it there: `node.layout` carries no range until something
  // sets one, and an autoranged axis never does.
  const rangeOf = (name) => node._fullLayout[name].range;
  const spanOf = (name) => Math.abs(rangeOf(name)[1] - rangeOf(name)[0]);
  const spans = () => Object.fromEntries(axes.map((name) => [name, spanOf(name)]));

  node.addEventListener("mousedown", () => {
    started = spans();
  });

  node.on("plotly_relayout", () => {
    const wanted = started;
    started = null;
    if (!wanted || correcting) return;
    // Only the pan tool is watched. With the zoom tool a drag draws a box,
    // and narrowing the range is the whole point of it.
    if (node._fullLayout.dragmode !== "pan") return;

    const update = {};
    for (const name of axes) {
      const axis = node._fullLayout[name];
      const { minallowed, maxallowed } = axis;
      if (!Number.isFinite(minallowed) || !Number.isFinite(maxallowed)) continue;

      const range = rangeOf(name);
      const span = Math.abs(range[1] - range[0]);
      // Only a shrink is ever wrong here: a double click reset grows the
      // span back, and nothing else that reaches this point changes it.
      if (span >= wanted[name] * (1 - 1e-9)) continue;

      const limit = maxallowed - minallowed;
      const width = Math.min(wanted[name], limit);
      const tolerance = limit * 1e-6;
      const anchorLow = Math.min(...range) - minallowed <= tolerance;
      const restored = anchorLow
        ? [minallowed, minallowed + width]
        : [maxallowed - width, maxallowed];
      // The pressure axis runs downwards, so its range is stored high first.
      update[`${name}.range`] = range[0] > range[1] ? restored.reverse() : restored;
    }

    if (Object.keys(update).length === 0) return;
    correcting = true;
    Plotly.relayout(node, update).finally(() => {
      correcting = false;
    });
  });
}

/**
 * Draw one variable of one profile.
 *
 * Every observation is drawn twice: once as a thin grey line giving the shape
 * of the cast, and once as a marker coloured by which source flagged it. The
 * line is what makes a single flagged point readable as a spike rather than
 * as a dot in space.
 *
 * @param {Array<object>} rows the profile's observations, any order.
 * @param {object} options
 * @param {object} options.variable a `variables` entry from catalog.json.
 * @param {Array<object>} options.statuses the `statuses` array of catalog.json.
 * @param {number} [options.height=440] the plot height in pixels.
 * @returns {Promise<HTMLElement>} the plot node.
 */
export async function profilePlot(rows, options) {
  const { variable, statuses, height = 440 } = options;
  const node = document.createElement("div");
  node.className = "nq-plot";

  const measured = rows.filter(
    (row) => row[variable.name] !== null && row[variable.name] !== undefined
  );
  if (measured.length === 0) {
    node.textContent = `No ${variable.label.toLowerCase()} measurements in this profile.`;
    node.classList.add("nq-empty");
    return node;
  }

  const Plotly = await loadPlotly();
  const ordered = [...measured].sort((a, b) => a.pres - b.pres);

  const traces = [
    {
      x: ordered.map((row) => row[variable.name]),
      y: ordered.map((row) => row.pres),
      mode: "lines",
      type: "scatter",
      line: { color: "#cfd6dd", width: 1 },
      hoverinfo: "skip",
      showlegend: false,
      name: "profile",
    },
  ];

  for (const status of statuses) {
    const subset = ordered.filter(
      (row) => row[variable.status_column] === status.key
    );
    if (subset.length === 0) continue;
    traces.push({
      x: subset.map((row) => row[variable.name]),
      y: subset.map((row) => row.pres),
      customdata: subset.map((row) => [
        row.observation_no,
        row[variable.flag],
        row[variable.nrt_flag],
      ]),
      mode: "markers",
      type: "scatter",
      name: status.label,
      marker: {
        color: status.color,
        size: status.key === "agree_good" ? 5 : 9,
        line: { color: "#33383d", width: status.key === "agree_good" ? 0 : 0.8 },
      },
      hovertemplate:
        `<b>%{x:.3f}</b> ${variable.unit}<br>` +
        "pressure %{y:.1f} db<br>" +
        "observation %{customdata[0]}<br>" +
        `${variable.flag} = %{customdata[1]}, ` +
        `${variable.nrt_flag} = %{customdata[2]}` +
        "<extra>" + status.label + "</extra>",
    });
  }

  const bounds = {
    x: axisBounds(ordered.map((row) => row[variable.name])),
    y: axisBounds(ordered.map((row) => row.pres)),
  };

  const layout = {
    height,
    margin: { l: 58, r: 16, t: 34, b: 44 },
    title: {
      text: variable.label,
      font: { size: 14 },
      x: 0,
      xanchor: "left",
    },
    xaxis: {
      title: { text: variable.unit ? `${variable.label} (${variable.unit})` : variable.label },
      zeroline: false,
      gridcolor: "#eceff2",
      ...bounds.x,
    },
    yaxis: {
      title: { text: "Pressure (db)" },
      autorange: "reversed",
      gridcolor: "#eceff2",
      ...bounds.y,
    },
    showlegend: false,
    plot_bgcolor: "#ffffff",
    paper_bgcolor: "#ffffff",
    hovermode: "closest",
  };

  const config = {
    responsive: true,
    scrollZoom: true,
    displaylogo: false,
    modeBarButtonsToRemove: ["select2d", "lasso2d", "toggleSpikelines"],
  };

  await Plotly.newPlot(node, traces, layout, config);
  if (bounds.x.minallowed !== undefined || bounds.y.minallowed !== undefined) {
    holdSpanWhileDragging(Plotly, node);
  }
  return node;
}
