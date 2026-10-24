// WattPost UI, part 06: history chart, compare packs, drift, runtime, battery health, load heatmap.
// Plain script (no modules): the numbered files in web/js/ are loaded in order by
// index.html and share one global scope, exactly as the old single app.js did.

// ---------- history chart ----------
function populateChartSelectors() {
  const dSel = $("#sel-device");
  const prevDevice = dSel.value;
  const prevMetric = $("#sel-metric").value;
  dSel.innerHTML = "";

  // Bank first (most useful default), then real devices in their natural order.
  const bank = devices.find(d => d.kind === "bank");
  const others = devices.filter(d => d.kind !== "bank");
  const ordered = bank ? [bank, ...others] : others;

  for (const d of ordered) {
    const opt = document.createElement("option");
    opt.value = d.label;
    opt.textContent = d.kind === "bank" ? "Bank (aggregate)" : d.label;
    dSel.appendChild(opt);
  }
  if (prevDevice && devices.find(d => d.label === prevDevice)) {
    dSel.value = prevDevice;
  } else if (bank) {
    dSel.value = bank.label;
  }
  onDeviceChanged(prevMetric);
}

function onDeviceChanged(preferMetric) {
  const label = $("#sel-device").value;
  const dev = devices.find(d => d.label === label);
  const mSel = $("#sel-metric");
  mSel.innerHTML = "";
  const numericMetrics = Object.entries(dev?.latest || {})
    .filter(([k, v]) => typeof v === "number" && !k.startsWith("_"))
    .map(([k]) => k)
    .sort();
  for (const k of numericMetrics) {
    const opt = document.createElement("option");
    opt.value = k; opt.textContent = prettyKey(k);
    mSel.appendChild(opt);
  }
  if (preferMetric && numericMetrics.includes(preferMetric)) mSel.value = preferMetric;
  else if (dev?.kind === "bank" && numericMetrics.includes("soc_pct"))             mSel.value = "soc_pct";
  else if (dev?.kind === "charge_controller" && numericMetrics.includes("pv_power_w")) mSel.value = "pv_power_w";
  else if (dev?.kind === "smart_battery" && numericMetrics.includes("voltage_v"))  mSel.value = "voltage_v";
  refreshChart();
}

// Custom-range state, populated when the user picks dates in the
// datetime-local inputs. Both are unix seconds.
let customSince = null;
let customUntil = null;

function sinceForRange(r) {
  const now = Math.floor(Date.now() / 1000);
  switch (r) {
    case "1h":  return [now - 3600,        5];
    case "6h":  return [now - 6 * 3600,    30];
    case "24h": return [now - 86400,       120];
    case "7d":  return [now - 7 * 86400,   600];
    case "30d": return [now - 30 * 86400,  3600];
  }
  return [now - 86400, 120];
}

// Custom range returns [since, bucket, until]. Bucket size is picked so the
// chart has roughly 300 points regardless of how wide a window the user
// chose, keeps payload + render cheap and ticks readable.
function customRangeParams() {
  if (customSince == null || customUntil == null) return null;
  const span = customUntil - customSince;
  if (span <= 0) return null;
  const bucket = Math.max(1, Math.round(span / 300));
  return { since: customSince, until: customUntil, bucket };
}

// ---- compare-packs mode ----
//
// Toggle persists across page loads so the user's preference sticks.
// Only effective when the selected device is a smart_battery AND
// there are >=2 smart_battery devices configured, otherwise the
// checkbox is greyed out and refreshChart() falls through to the
// single-series path.
let compareMode = localStorage.getItem("compareMode") === "1";

// Five-pack colour palette. Hand-picked to read distinctly in both
// light and dark themes; if a user ever wires up >5 packs we cycle
// back to the start (the legend keeps them disambiguated).
const COMPARE_COLORS = [
  "#58a6ff",   // accent blue
  "#d29922",   // amber
  "#3fb950",   // green
  "#f85149",   // red
  "#a371f7",   // purple
];

function smartBatteries() {
  return devices.filter(d => d.kind === "smart_battery");
}

function updateCompareToggle() {
  const box = $("#chart-compare-packs");
  const label = box?.parentElement;
  if (!box || !label) return;
  const selDev = devices.find(d => d.label === $("#sel-device").value);
  const eligible = selDev?.kind === "smart_battery" && smartBatteries().length >= 2;
  box.disabled = !eligible;
  label.classList.toggle("is-disabled", !eligible);
  if (!eligible) box.checked = false;
  else box.checked = compareMode;
}

function buildHistoryURL(label, metric) {
  if (currentRange === "custom") {
    const p = customRangeParams();
    if (!p) return null;
    return `/api/devices/${encodeURIComponent(label)}/history?metric=${encodeURIComponent(metric)}` +
           `&since=${p.since}&until=${p.until}&bucket=${p.bucket}`;
  }
  const [since, bucket] = sinceForRange(currentRange);
  return `/api/devices/${encodeURIComponent(label)}/history?metric=${encodeURIComponent(metric)}` +
         `&since=${since}&bucket=${bucket}`;
}

async function refreshChart() {
  const label = $("#sel-device").value;
  const metric = $("#sel-metric").value;
  if (!label || !metric) return;

  updateCompareToggle();
  const selDev = devices.find(d => d.label === label);
  const inCompare = compareMode && selDev?.kind === "smart_battery"
                    && smartBatteries().length >= 2;

  if (inCompare) {
    return refreshChartCompare(metric, label);
  }

  const url = buildHistoryURL(label, metric);
  if (!url) return;
  let data;
  try { data = await api(url); }
  catch (e) { console.error(e); return; }
  updateStatStrip(metric, data);

  // PV forecast overlay: only meaningful when viewing pv_power_w (the
  // charge controller's incoming PV). Best-effort, a missing or
  // unconfigured forecast just falls through to a normal chart.
  let forecast = null;
  if (metric === "pv_power_w") {
    try {
      const f = await api("/api/forecast/pv");
      if (f?.points?.length) forecast = f;
    } catch (e) { /* swallow, no forecast is fine */ }
  }
  drawChart(label, metric, data, forecast);
}

async function refreshChartCompare(metric, selectedLabel) {
  const packs = smartBatteries();
  const urls = packs.map(p => [p.label, buildHistoryURL(p.label, metric)])
                    .filter(([, u]) => u != null);
  if (!urls.length) return;
  // Parallel fetch, a 3-pack rig with the daemon on LAN comes back in
  // well under 200 ms total, so no need for a request-coalescing layer.
  let results;
  try {
    results = await Promise.all(urls.map(async ([label, u]) => {
      const data = await api(u);
      return { label, data };
    }));
  } catch (e) {
    console.error("compare-packs fetch failed:", e);
    return;
  }
  // Stat strip reflects the device the dropdown is on, keeps the
  // "selected pack" semantic intact even when we render N of them.
  const sel = results.find(r => r.label === selectedLabel) || results[0];
  if (sel) updateStatStrip(metric, sel.data);
  drawCompareChart(metric, results);
}

function drawCompareChart(metric, datasets) {
  const root = $("#chart");
  if (chart) { chart.destroy(); chart = null; }
  const unit = unitFromKey(metric);
  const width = Math.max(root.clientWidth, 320);
  const pal = chartPalette();

  // Union the timestamps across all packs into one monotonic axis so
  // a slight phase difference between BMS polls doesn't fragment the
  // chart. uPlot wants every series aligned to one x array, with
  // null gaps where a series has no point at that x.
  const xs = new Set();
  for (const d of datasets) for (const t of d.data.ts || []) xs.add(t);
  const ts = Array.from(xs).sort((a, b) => a - b);
  const tsIndex = new Map(ts.map((t, i) => [t, i]));

  const series = [{}];
  const dataCols = [ts];
  datasets.forEach((d, i) => {
    const color = COMPARE_COLORS[i % COMPARE_COLORS.length];
    const col = new Array(ts.length).fill(null);
    const tsArr = d.data.ts || [];
    const vals = d.data.values || [];
    for (let j = 0; j < tsArr.length; j++) {
      const idx = tsIndex.get(tsArr[j]);
      if (idx != null) col[idx] = vals[j];
    }
    series.push({
      label: d.label,
      stroke: color,
      width: 2,
      points: { show: ts.length < 60, size: 4, fill: color, stroke: color },
      value: (_u, v) => v == null ? "·" : `${(+v).toFixed(2)}${unit ? " " + unit : ""}`,
    });
    dataCols.push(col);
  });

  const first = datasets[0]?.data || {};
  const win = chartWindow(first.since, first.until, ts[0], ts[ts.length - 1]);
  const xScale = win ? { time: true, range: win } : { time: true };

  const opts = {
    width, height: 340,
    cursor: { drag: { x: true, y: false } },
    scales: { x: xScale },
    series,
    axes: [
      { stroke: pal.axis, grid: { stroke: pal.grid },
        ticks: { stroke: pal.gridStrong }, space: 45, size: 36 },
      { stroke: pal.axis, grid: { stroke: pal.grid },
        ticks: { stroke: pal.gridStrong }, space: 36,
        values: (_u, splits) => splits.map(v =>
          v == null ? "" :
          (Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + "k" : v.toFixed(2))
          + (unit ? " " + unit : "")),
      },
    ],
    legend: { live: false },
    plugins: [valueTooltipPlugin()],
  };

  try {
    chart = new uPlot(opts, dataCols, root);
  } catch (e) {
    console.error("uPlot compare failed:", e);
    root.innerHTML = `<div style="padding:1rem;color:var(--red)">Compare chart render failed: ${e.message}</div>`;
  }
}

// ───────── Energy-today overview (top of /history) ─────────
//
// One request fetches all five series aligned to the same ts grid +
// kWh totals + self-powered breakdown. Then a single uPlot draws:
//   solar (yellow area, ≥0), charger (grey area, ≥0), load (purple
//   area, derived from sources−bank), battery-in (green area, ≥0),
//   battery-out (pink area, ≤0), SoC (red line on right axis).
// Caption + four stat cards + self-powered bar update from the same
// payload.

let energyChart = null;
let _energyRange = null;          // remembers user's range choice across reloads

// Range key → query params for /api/energy/today + /api/weather/history.
// Buckets balance "enough resolution to see the dips" against payload
// size + render speed; 30d at 5min would be 8640 points which uPlot
// can render but is wasteful when nobody reads sub-hour detail at
// month scale anyway.
const ENERGY_RANGES = {
  today: { label: "today",     bucket: 300,   span: null   /* midnight→now */ },
  "24h": { label: "last 24h",  bucket: 900,   span: 86400 },
  "7d":  { label: "last 7 days",  bucket: 3600,  span: 7 * 86400 },
  "30d": { label: "last 30 days", bucket: 21600, span: 30 * 86400 },
};

function _currentEnergyRange() {
  if (_energyRange && ENERGY_RANGES[_energyRange]) return _energyRange;
  try {
    const saved = localStorage.getItem("wp-energy-range");
    if (saved && ENERGY_RANGES[saved]) return (_energyRange = saved);
  } catch (_) {}
  return (_energyRange = "today");
}

async function refreshEnergyOverview(rangeKey) {
  const host = document.getElementById("energy-chart");
  if (!host) return;

  const range = ENERGY_RANGES[rangeKey] ? rangeKey : _currentEnergyRange();
  _energyRange = range;
  try { localStorage.setItem("wp-energy-range", range); } catch (_) {}

  // Reflect selection on the range buttons + heading.
  document.querySelectorAll(".energy-ranges .erange").forEach(b => {
    b.classList.toggle("is-active", b.dataset.range === range);
  });
  const titleEl = document.getElementById("energy-title");
  if (titleEl) {
    titleEl.textContent = range === "today" ? "Energy today" : `Energy · ${ENERGY_RANGES[range].label}`;
  }

  const cfg = ENERGY_RANGES[range];
  const now = Math.floor(Date.now() / 1000);
  const qp = new URLSearchParams();
  if (cfg.span) qp.set("since", String(now - cfg.span));
  qp.set("bucket", String(cfg.bucket));
  const energyUrl  = `/api/energy/today?${qp.toString()}`;
  const weatherUrl = `/api/weather/history?${qp.toString()}`;

  // Energy is the must-have; weather is best-effort (no lat/lon =
  // empty series, upstream failure = empty series). Run both in
  // parallel and don't fail the chart if weather rejects.
  let payload, weather = null;
  try {
    const [er, wr] = await Promise.all([
      fetch(energyUrl),
      fetch(weatherUrl).catch(() => null),
    ]);
    if (!er.ok) throw new Error(`HTTP ${er.status}`);
    payload = await er.json();
    if (wr && wr.ok) {
      try { weather = await wr.json(); } catch (_) { weather = null; }
    }
  } catch (e) {
    host.innerHTML = `<div style="padding:1rem;color:var(--text-3);font-size:.85rem;">Couldn't load energy (${e.message}).</div>`;
    return;
  }

  const s = payload.series || {};
  const ts = s.ts || [];
  const solar   = (s.solar_w   || []).map(v => v == null ? 0 : Math.max(0, v));
  const charger = (s.charger_w || []).map(v => v == null ? 0 : Math.max(0, v));
  const bank    = s.bank_w || [];
  // Load = sources − bank (when bank charging, eats sources; when
  // discharging, adds to load). Clip ≥ 0 since we render as a
  // positive area; the "out of battery" series captures discharge
  // separately for the green-on-pink Powerwall look.
  const load = ts.map((_, i) => {
    const so = solar[i]   || 0;
    const ch = charger[i] || 0;
    const bk = bank[i] == null ? 0 : bank[i];
    return Math.max(0, so + ch - bk);
  });
  const batIn  = bank.map(v => v == null ? 0 : Math.max(0,  v));
  const batOut = bank.map(v => v == null ? 0 : Math.min(0,  v)); // negative
  // SoC ≤ 0 is physically impossible (BMS would have cut off first).
  // When a poll bucket misses, the average can land at 0 and produce
  // a misleading drop-to-zero line. Treat those as gaps.
  const soc    = (s.soc_pct || []).map(v => (v == null || v <= 0) ? null : v);

  // Cloud cover, aligned to the chart grid by the weather endpoint.
  // null when unavailable, drawEnergyChart skips the series rather
  // than rendering a flat zero line.
  const cloudAvailable = !!(weather && weather.available && weather.series?.cloud_cover_pct);
  const cloud = cloudAvailable
    ? (weather.series.cloud_cover_pct || []).map(v => v == null ? null : Math.max(0, Math.min(100, v)))
    : null;
  const legendCloud = document.getElementById("energy-legend-weather");
  if (legendCloud) legendCloud.hidden = !cloudAvailable;

  drawEnergyChart(host, ts, { solar, charger, load, batIn, batOut, soc, cloud },
                  { since: payload.since_ts, until: payload.until_ts });

  // Sub-line: time range. Days for multi-day windows so it reads
  // "May 17 → 24" instead of two midnight timestamps.
  const sub = document.getElementById("energy-sub");
  if (sub && payload.since_ts && payload.until_ts) {
    const wide = (payload.until_ts - payload.since_ts) > 36 * 3600;
    const fmt = wide
      ? (t) => new Date(t * 1000).toLocaleDateString([], { month: "short", day: "numeric" })
      : (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    sub.textContent = `${fmt(payload.since_ts)} → ${fmt(payload.until_ts)}`;
  }

  // Stat cards.
  const totals = payload.totals || {};
  const kwh = (wh) => wh == null ? "·" : (wh / 1000).toFixed(1);
  setText("es-solar",  kwh(totals.solar_wh));
  setText("es-load",   kwh(totals.load_wh));
  setText("es-batin",  kwh(totals.bank_charged_wh));
  setText("es-batout", kwh(totals.bank_discharged_wh));

  // Self-powered breakdown, bar + legend rows.
  const bd = payload.self_powered || {};
  const bars = document.getElementById("energy-self-bars");
  const leg  = document.getElementById("energy-self-legend");
  if (bars && leg) {
    bars.innerHTML = "";
    leg.innerHTML = "";
    const slices = [
      { k: "solar",   label: "From solar",   color: "#f0c849", pct: bd.solar   || 0 },
      { k: "battery", label: "From battery", color: "#56d364", pct: bd.battery || 0 },
      { k: "charger", label: "From charger", color: "#b1b9c2", pct: bd.charger || 0 },
    ];
    for (const sl of slices) {
      if (sl.pct < 0.5) continue;
      const span = document.createElement("span");
      span.style.background = sl.color;
      span.style.width = `${sl.pct}%`;
      bars.appendChild(span);
      const row = document.createElement("div");
      row.className = "self-row";
      row.innerHTML = `<span class="dot" style="background:${sl.color}"></span>${sl.label}<span class="pct">${sl.pct.toFixed(0)}%</span>`;
      leg.appendChild(row);
    }
    if (!bars.children.length) {
      bars.innerHTML = `<span style="background:#3a4351; width:100%"></span>`;
      leg.innerHTML = `<div class="self-row" style="color:var(--text-3)">No load detected today yet</div>`;
    }
  }
}

function setText(id, t) {
  const el = document.getElementById(id);
  if (el) el.textContent = t;
}

function drawEnergyChart(root, ts, series, window_ = null) {
  if (energyChart) { energyChart.destroy(); energyChart = null; }
  if (!ts.length) {
    root.innerHTML = `<div style="padding:1rem;color:var(--text-3);font-size:.85rem;">No samples yet today.</div>`;
    return;
  }
  const width = Math.max(root.clientWidth, 320);
  const pal = chartPalette();

  const fmtW = (v) => v == null ? "·" : (Math.abs(v) >= 1000 ? (v/1000).toFixed(1) + " kW" : v.toFixed(0) + " W");
  const fmtPct = (v) => v == null ? "·" : v.toFixed(0) + " %";

  // Cloud cover (0-100%) on a hidden Y axis. Shares the same 0..100
  // range as SoC but drawn behind everything else as a soft grey
  // wash, looks like "shade behind the energy curves". Only added
  // when weather data is available.
  const hasCloud = Array.isArray(series.cloud) && series.cloud.some(v => v != null);

  const cloudSeries = hasCloud ? [
    // Drawn first so all energy lines paint on top of it.
    { label: "Cloud cover", stroke: "rgba(180,190,210,0.55)", width: 1.0,
      fill: "rgba(180,190,210,0.20)", scale: "y2",
      value: (_u, v) => v == null ? "·" : v.toFixed(0) + " %" },
  ] : [];

  const opts = {
    width, height: 280,
    cursor: { drag: { x: true, y: false } },
    scales: {
      x: (() => {
        const w = chartWindow(window_?.since, window_?.until, ts[0], ts[ts.length - 1]);
        return w ? { time: true, range: w } : { time: true };
      })(),
      // Power axis (signed, battery discharge dips below 0).
      y: { auto: true },
      // SoC + cloud cover share the 0..100 right axis. Both are
      // percentages, both belong on a fixed scale so the line is
      // comparable across days regardless of power magnitudes.
      y2: { range: [0, 100], auto: false },
    },
    series: [
      {},
      // Cloud cover first so the energy series paint on top.
      ...cloudSeries,
      // Solar, yellow filled area, ≥0.
      { label: "Solar",       stroke: "#f0c849", width: 1.2, fill: "rgba(240,200,73,0.35)",
        value: (_u, v) => fmtW(v) },
      // Charger, grey filled area, ≥0.
      { label: "Charger",     stroke: "#b1b9c2", width: 1.0, fill: "rgba(177,185,194,0.25)",
        value: (_u, v) => fmtW(v) },
      // Load, purple LINE (no fill) drawn on top of sources so the
      // line is legible against the yellow/grey area overlap. Earlier
      // versions filled this too, but on heavy-source days the load
      // line vanished behind the charger area; line-on-top reads
      // better at glance.
      { label: "Load",        stroke: "#a48cff", width: 1.8,
        value: (_u, v) => fmtW(v) },
      // Battery in, green filled area, ≥0.
      { label: "Into battery", stroke: "#56d364", width: 1.0, fill: "rgba(86,211,100,0.30)",
        value: (_u, v) => fmtW(v) },
      // Battery out, pink filled area, ≤0 (drawn below zero).
      { label: "Out of battery", stroke: "#f06292", width: 1.0, fill: "rgba(240,98,146,0.30)",
        value: (_u, v) => v == null ? "·" : fmtW(-v) },
      // SoC, red line on right axis.
      { label: "SoC", stroke: "#ff5e57", width: 1.6, scale: "y2",
        value: (_u, v) => fmtPct(v) },
    ],
    axes: [
      { stroke: pal.axis, grid: { stroke: pal.grid }, ticks: { stroke: pal.gridStrong }, space: 50, size: 36 },
      { stroke: pal.axis, grid: { stroke: pal.grid }, ticks: { stroke: pal.gridStrong }, space: 36,
        values: (_u, splits) => splits.map(v => v == null ? "" :
          (Math.abs(v) >= 1000 ? (v/1000).toFixed(1) + "kW" : v.toFixed(0) + " W")) },
      { stroke: pal.axis, scale: "y2", side: 1, grid: { show: false },
        values: (_u, splits) => splits.map(v => v == null ? "" : v.toFixed(0) + "%") },
    ],
    // No built-in legend on the Energy chart, we render a static
    // colour-chip legend below the chart in HTML, so the uPlot one
    // would duplicate it.
    legend: { show: false },
    plugins: [valueTooltipPlugin()],
  };

  const dataCols = hasCloud
    ? [ts, series.cloud, series.solar, series.charger, series.load, series.batIn, series.batOut, series.soc]
    : [ts,                series.solar, series.charger, series.load, series.batIn, series.batOut, series.soc];
  try {
    energyChart = new uPlot(opts, dataCols, root);
  } catch (e) {
    console.error("uPlot energy failed:", e);
    root.innerHTML = `<div style="padding:1rem;color:var(--red);font-size:.85rem;">Chart render failed: ${e.message}</div>`;
  }
}

function updateStatStrip(metric, data) {
  const unit = unitFromKey(metric);
  const s = data?.stats || {};
  const fmtV = (v) => v == null ? "·" : `${(+v).toFixed(2)}${unit ? " " + unit : ""}`;
  $("#cs-now").textContent = fmtV(s.now);
  $("#cs-min").textContent = fmtV(s.min);
  $("#cs-avg").textContent = fmtV(s.avg);
  $("#cs-max").textContent = fmtV(s.max);

  // Resolution = bucket / table info: tell the user how dense the data
  // is. Moved out of the stat strip, it's debug context, not a stat,
  // into a small subtitle line under the chart controls. The Range
  // cell (max - min) was redundant with Min + Max and got dropped.
  const tableLabel = {
    samples: "raw",
    samples_1min: "1-min avg",
    samples_1hour: "1-hour avg",
    samples_1day: "1-day avg",
  }[data?.table] || "·";
  const res = $("#cs-res");
  if (res) {
    res.textContent = `${s.count ?? 0} points · ${tableLabel}`;
    res.dataset.full = res.textContent;   // remembered so zoom can restore it
  }
}

// Recompute min / avg / max over a zoomed x-window (epoch seconds) from the
// actual samples, so the stat strip reflects what you've drilled into rather
// than the whole window. null = nothing in range.
function computeRangeStats(ts, vals, xMin, xMax) {
  let mn = Infinity, mx = -Infinity, sum = 0, n = 0;
  for (let i = 0; i < ts.length; i++) {
    const t = ts[i], v = vals[i];
    if (t < xMin || t > xMax || v == null) continue;
    if (v < mn) mn = v;
    if (v > mx) mx = v;
    sum += v; n++;
  }
  return n ? { min: mn, max: mx, avg: sum / n, count: n } : null;
}

// Linear-interpolate a forecast field (pv_w / pv_w_p10 / pv_w_p90) at an
// arbitrary timestamp from the hourly Solcast points, so the forecast can
// be overlaid onto the per-minute actuals for the same window (compare
// "forecast said X" vs "actual was Y"). Returns null outside the forecast
// range. Points are sparse (hourly) so a linear scan is plenty.
function makeForecastInterp(points) {
  const pts = (points || []).slice().sort((a, b) => a.ts - b.ts);
  return function (t, key) {
    if (!pts.length || t < pts[0].ts || t > pts[pts.length - 1].ts) return null;
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i], b = pts[i + 1];
      if (t >= a.ts && t <= b.ts) {
        const va = a[key], vb = b[key];
        if (va == null || vb == null) return va ?? vb ?? null;
        const f = (b.ts - a.ts) ? (t - a.ts) / (b.ts - a.ts) : 0;
        return va + (vb - va) * f;
      }
    }
    return null;
  };
}

// The x-range a history chart should show: the window the user asked for
// (the server echoes `since` / `until`), widened to cover the data if it
// somehow lies outside. Fitting to the data alone made a 30-day chart with
// one hour of samples look like an hour-long chart, and hid how little
// history exists. Falls back to the data's own span when no usable window
// comes back. Returns [min, max] or null (let uPlot decide).
function chartWindow(since, until, tsMin, tsMax) {
  const ok = (v) => typeof v === "number" && isFinite(v) && v > 0;
  let lo = tsMin, hi = tsMax;
  if (ok(since) && ok(until) && until > since && (until - since) <= 6 * 366 * 86400) {
    lo = lo == null ? since : Math.min(since, lo);
    hi = hi == null ? until : Math.max(until, hi);
  }
  return (lo != null && hi != null && hi > lo) ? [lo, hi] : null;
}

function drawChart(label, metric, data, forecast = null) {
  const root = $("#chart");
  if (chart) { chart.destroy(); chart = null; }
  const unit = unitFromKey(metric);
  const width = Math.max(root.clientWidth, 320);

  const ts = data.ts;
  const vals = data.values;
  const hasBand = Array.isArray(data.min) && Array.isArray(data.max) &&
                  data.min.length === ts.length && data.min.length > 0;

  // Filter forecast to future-only points AND bound the horizon to
  // roughly the same width as the user's selected history range. A
  // 6-hour history with 7 days of forecast overlay (Solcast's full
  // window) made the X-axis span a week and visually drowned the
  // historic data. Mirroring the window keeps the chart balanced:
  //   1h history  → 1h  forecast
  //   6h          → 6h
  //   24h         → 24h
  //   7d/30d      → cap at Solcast's max (~7d)
  let forecastFuture = [];
  if (forecast?.points?.length) {
    const now = Math.floor(Date.now() / 1000);
    // Width of the historic window in seconds, derived from the
    // server-stamped since/until so a custom range works too.
    let historyWindow = 0;
    if (typeof data.since === "number" && typeof data.until === "number") {
      historyWindow = Math.max(0, data.until - data.since);
    } else if (data.ts && data.ts.length > 1) {
      historyWindow = Math.max(0, data.ts[data.ts.length - 1] - data.ts[0]);
    }
    // 1h floor so the line has somewhere to live even on the
    // shortest range; cap to whatever Solcast returned (usually 7d).
    const horizon = Math.max(3600, historyWindow);
    forecastFuture = forecast.points.filter(p => p.ts >= now && p.ts <= now + horizon);
  }

  // Build series + data matrix in lockstep so indices always line up.
  //   series[0] = x (always)
  //   series[1] = min, invisible line that anchors the band's bottom edge
  //   series[2] = max, invisible line, paired with min by `bands`
  //   series[3] = avg, the visible line
  // Without bands: series[1] = avg, data = [ts, vals]
  const pal = chartPalette();
  const series = [{}];
  const dataCols = [ts];
  let bands = [];

  if (hasBand) {
    series.push({
      label: "min", stroke: "transparent", width: 0, points: { show: false },
      value: (_u, v) => v == null ? "·" : `${(+v).toFixed(2)}${unit ? " " + unit : ""}`,
    });
    series.push({
      label: "max", stroke: "transparent", width: 0, points: { show: false },
      value: (_u, v) => v == null ? "·" : `${(+v).toFixed(2)}${unit ? " " + unit : ""}`,
    });
    dataCols.push(data.min, data.max);
    // Fill between max (series 2) and min (series 1).
    bands = [{ series: [2, 1], fill: pal.bandFill }];
  }

  // Main line, last historic series, always visible.
  series.push({
    label: prettyKey(metric),
    stroke: pal.accent,
    width: 2,
    fill: pal.accentFill,
    points: { show: ts.length < 60, size: 4, fill: pal.accent, stroke: pal.accent },
    value: (_u, v) => v == null ? "·" : `${(+v).toFixed(2)}${unit ? " " + unit : ""}`,
  });
  dataCols.push(vals);

  // Forecast overlay. Draw the PV forecast across BOTH the historical
  // window (interpolated onto the actuals' timestamps, so you can compare
  // what the forecast expected vs what actually happened for the same
  // hours) AND the future extension. Up to three series: p10/p90
  // (invisible band edges) + the median dashed line. The p10–p90 fill is
  // the forecast confidence interval (wide = unsure, narrow = confident).
  let combinedTs = ts.slice();
  if (forecast?.points?.length && ts.length) {
    const interp = makeForecastInterp(forecast.points);
    const hasBand = forecast.points.some(p => p.pv_w_p10 != null && p.pv_w_p90 != null);

    // Historical window: interpolate the forecast at each actual sample so
    // the dashed forecast line sits directly over the measured line.
    const forecastCol = ts.map(t => interp(t, "pv_w"));
    const p10Col = hasBand ? ts.map(t => interp(t, "pv_w_p10")) : null;
    const p90Col = hasBand ? ts.map(t => interp(t, "pv_w_p90")) : null;

    // Future extension beyond the last actual sample (the original behaviour).
    const lastHistoric = ts[ts.length - 1];
    const fTs = forecastFuture.map(p => p.ts).filter(t => t > lastHistoric);
    if (fTs.length) {
      combinedTs = combinedTs.concat(fTs);
      const pad = new Array(fTs.length).fill(null);
      for (let i = 1; i < dataCols.length; i++) dataCols[i] = dataCols[i].concat(pad);
      const byTs = new Map(forecastFuture.map(p => [p.ts, p]));
      for (const t of fTs) {
        const p = byTs.get(t);
        forecastCol.push(p ? p.pv_w : null);
        if (p10Col) p10Col.push(p ? (p.pv_w_p10 ?? null) : null);
        if (p90Col) p90Col.push(p ? (p.pv_w_p90 ?? null) : null);
      }
    }

    if (hasBand) {
      const p10Idx = series.length;
      series.push({
        label: "p10", stroke: "transparent", width: 0, points: { show: false },
        value: (_u, v) => v == null ? "·" : `${(+v).toFixed(0)}${unit ? " " + unit : ""}`,
      });
      const p90Idx = series.length;
      series.push({
        label: "p90", stroke: "transparent", width: 0, points: { show: false },
        value: (_u, v) => v == null ? "·" : `${(+v).toFixed(0)}${unit ? " " + unit : ""}`,
      });
      dataCols.push(p10Col, p90Col);
      bands = bands.concat([{ series: [p90Idx, p10Idx], fill: "rgba(210,153,34,0.13)" }]);
    }
    series.push({
      label: "forecast",
      stroke: pal.amber || "#d29922",
      width: 2,
      dash: [6, 4],
      points: { show: false },
      value: (_u, v) => v == null ? "·" : `${(+v).toFixed(0)}${unit ? " " + unit : ""}`,
    });
    dataCols.push(forecastCol);
    dataCols[0] = combinedTs;
  }

  // Auto-fit X scale to whatever timeline we ended up with, historical
  // only, or historical + forecast extension.
  const dataMin = combinedTs.length ? combinedTs[0] : null;
  const dataMax = combinedTs.length ? combinedTs[combinedTs.length - 1] : null;
  const win = chartWindow(data?.since, data?.until, dataMin, dataMax);
  const tsMin = win ? win[0] : dataMin;
  const tsMax = win ? win[1] : dataMax;
  const xScale = win ? { time: true, range: win } : { time: true };

  const opts = {
    width, height: 340,
    cursor: { drag: { x: true, y: false } },
    scales: { x: xScale },
    series,
    bands,
    axes: [
      {
        stroke: pal.axis,
        grid:  { stroke: pal.grid },
        ticks: { stroke: pal.gridStrong },
        // Tighter tick spacing so a 6-hour chart shows 1-hour increments
        // (6pm/7pm/8pm/9pm/10pm) rather than just 2-hour bookends.
        space: 45,
        size: 36,
      },
      {
        stroke: pal.axis,
        grid:  { stroke: pal.grid },
        ticks: { stroke: pal.gridStrong },
        // Fewer ticks, more vertical space per tick, keeps labels
        // breathable and lets us afford slightly longer text.
        space: 36,
        // Computed below from the actual longest label.
        size: (u, values, axisIdx, cycleNum) => {
          if (!values || !values.length) return 56;
          // Rough monospace metric: ~7.5 px per char @ 11 px font.
          const longest = values.reduce((m, s) => Math.max(m, (s || "").length), 0);
          return Math.max(48, Math.min(112, longest * 7.5 + 14));
        },
        values: (_u, splits) => {
          // Pick decimals based on the smallest gap between consecutive
          // ticks, guarantees each tick is distinct. Cap at 3 decimals so
          // tiny-noise data (e.g. bank Ah jiggling in the third decimal)
          // doesn't produce 11-character monster labels like "246.8120 Ah".
          let minDelta = Infinity;
          for (let i = 1; i < splits.length; i++) {
            const d = Math.abs(splits[i] - splits[i - 1]);
            if (d > 0) minDelta = Math.min(minDelta, d);
          }
          let decimals = 0;
          if (isFinite(minDelta)) {
            if      (minDelta >= 10)    decimals = 0;
            else if (minDelta >= 1)     decimals = 1;
            else if (minDelta >= 0.1)   decimals = 2;
            else                         decimals = 3;  // capped here
          }
          return splits.map(v => {
            if (v == null) return "";
            const abs = Math.abs(v);
            let txt;
            if (abs >= 1000) txt = (v / 1000).toFixed(Math.max(1, decimals - 1)) + "k";
            else             txt = v.toFixed(decimals);
            return unit ? `${txt} ${unit}` : txt;
          });
        },
      },
    ],
    legend: { live: false },
    plugins: [valueTooltipPlugin()],
    hooks: {
      ready: [
        (u) => {
          // Double-click the plot to reset a drag-zoom back to the full
          // window. uPlot drag-zooms the X axis, but with a static range
          // there was no way back out — this is the reset.
          u.over.addEventListener("dblclick", () => {
            if (tsMin != null && tsMax != null) {
              u.setScale("x", { min: tsMin, max: tsMax });
            }
          });
        },
      ],
      setScale: [
        (u, key) => {
          // Make MIN/AVG/MAX range-aware: when you drag-zoom, recompute
          // them over the visible window; on reset, restore the server's
          // whole-window stats.
          if (key !== "x") return;
          const xs = u.scales.x;
          const fmtV = (v) => v == null ? "·" : `${(+v).toFixed(2)}${unit ? " " + unit : ""}`;
          const isFull = tsMin == null || (xs.min <= tsMin + 1 && xs.max >= tsMax - 1);
          if (isFull) {
            const s = data?.stats || {};
            $("#cs-min").textContent = fmtV(s.min);
            $("#cs-avg").textContent = fmtV(s.avg);
            $("#cs-max").textContent = fmtV(s.max);
            const res = $("#cs-res");
            if (res && res.dataset.full) res.textContent = res.dataset.full;
          } else {
            const r = computeRangeStats(ts, vals, xs.min, xs.max);
            if (r) {
              $("#cs-min").textContent = fmtV(r.min);
              $("#cs-avg").textContent = fmtV(r.avg);
              $("#cs-max").textContent = fmtV(r.max);
              const res = $("#cs-res");
              if (res) res.textContent = `${r.count} points · selected range`;
            }
          }
        },
      ],
    },
  };

  try {
    chart = new uPlot(opts, dataCols, root);
  } catch (e) {
    console.error("uPlot failed:", e, { data, width, opts });
    // Fall back to the simplest possible chart so the user sees *something*.
    try {
      chart = new uPlot({
        width, height: 340,
        scales: { x: { time: true } },
        series: [{}, { label: prettyKey(metric), stroke: pal.accent, width: 2, fill: pal.accentFill }],
      }, [ts, vals], root);
    } catch (e2) {
      root.innerHTML = `<div style="padding:1rem;color:var(--red)">Chart render failed: ${e.message}</div>`;
    }
  }
}

// ---------- drift sparkline (Cell balance panel) ----------
let driftSpark = null;
async function refreshDriftSparkline() {
  const root = document.querySelector("#cell-drift-spark");
  if (!root) return;
  const batts = devices.filter(d => d.kind === "smart_battery");
  if (batts.length === 0) return;

  // Pick the pack with the highest current drift, that's the one worth
  // tracking. Fetch its cell_drift_v over the last 24h.
  let target = batts[0].label;
  let maxDrift = -1;
  for (const b of batts) {
    const v = +b.latest?.cell_drift_v || 0;
    if (v > maxDrift) { maxDrift = v; target = b.label; }
  }
  const since = Math.floor(Date.now() / 1000) - 24 * 3600;
  let data;
  try {
    data = await api(`/api/devices/${encodeURIComponent(target)}/history?metric=cell_drift_v&since=${since}&bucket=300`);
  } catch (e) { return; }
  const stat = $("#cell-drift-stat");
  if (data?.stats?.max != null && data.stats.now != null) {
    stat.textContent = `now ${(+data.stats.now*1000).toFixed(0)} mV · max ${(+data.stats.max*1000).toFixed(0)} mV (${target})`;
  } else {
    stat.textContent = "·";
  }
  // Tiny sparkline using uPlot
  if (driftSpark) { driftSpark.destroy(); driftSpark = null; }
  if (!data?.ts?.length) {
    root.innerHTML = '<div class="empty-spark">collecting…</div>';
    return;
  }
  root.innerHTML = "";
  const pal = chartPalette();
  try {
    driftSpark = new uPlot({
      width: Math.max(root.clientWidth, 280),
      height: 90,
      scales: { x: { time: true } },
      cursor: { show: false },
      legend: { live: false },
      series: [
        {},
        { stroke: pal.amber, width: 1.5, fill: pal.amberFill, points: { show: false } },
      ],
      axes: [
        { stroke: pal.axis, grid: { stroke: pal.grid }, space: 80, size: 18 },
        {
          stroke: pal.axis, grid: { stroke: pal.grid },
          // Auto-size the gutter to the longest rendered label so
          // "100 mV" doesn't clip to " mV" the way it did at size:36.
          size: (u, values) => {
            if (!values || !values.length) return 56;
            const longest = values.reduce((m, s) => Math.max(m, (s || "").length), 0);
            return Math.max(44, Math.min(96, longest * 7.5 + 14));
          },
          values: (_u, splits) => splits.map(v => v == null ? "" : `${(v*1000).toFixed(0)} mV`),
        },
      ],
    }, [data.ts, data.values], root);
  } catch (e) { console.error("drift sparkline:", e); }
}

// ---------- runtime prediction (#99) ----------
// The Hero tile's "Remaining" line is naive, current power × current SoC.
// This fetcher overlays a forecast-aware line below it: "Forecast: lasts
// 4.5 days" (sunny) or "depleted Wed 02:00" (cloudy). Driven from a
// rolling 1-hour avg load and the cached Open-Meteo / Solcast forecast.
async function refreshRuntimeForecast() {
  const fcEl = document.getElementById("bank-time-forecast");
  if (!fcEl) return;
  let data;
  try { data = await api("/api/runtime-forecast"); }
  catch (e) { fcEl.hidden = true; return; }
  if (!data || !data.ok) {
    fcEl.hidden = true;
    return;
  }

  const naive = data.naive || {};
  const fc    = data.forecast || {};
  let text = "";
  let title = "";

  if (fc.available) {
    if (fc.reserves_indefinite) {
      // Forecast says you stay above 10% across the horizon, express
      // as "reserve days" using the naive rate.
      if (naive.hours_to_empty != null) {
        const days = naive.hours_to_empty / 24;
        text = `Forecast: holds for the ${fc.horizon_hours.toFixed(0)}h window`;
      } else {
        text = `Forecast: stable across ${fc.horizon_hours.toFixed(0)}h`;
      }
      title = "PV input covers your average draw across the forecast horizon.";
    } else if (fc.depletion_ts) {
      const when = new Date(fc.depletion_ts * 1000);
      const hours = fc.hours_to_empty;
      if (hours != null) {
        // Choose phrasing by horizon
        if (hours < 24) {
          text = `Forecast: ~${hours.toFixed(1)}h until 10% (${when.toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"})})`;
        } else {
          const days = hours / 24;
          text = `Forecast: ~${days.toFixed(1)} days until 10% (${when.toLocaleDateString([], {weekday:"short", month:"short", day:"numeric"})})`;
        }
      }
      title = "Hourly walk of avg load minus forecast PV until SoC hits 10 % reserve.";
    }
  } else if (naive.hours_to_empty != null) {
    // No forecast, show the naive rolling-average view as a secondary line.
    const hours = naive.hours_to_empty;
    if (hours < 24) {
      text = `1h-avg load: ~${hours.toFixed(1)}h to 10%`;
    } else {
      text = `1h-avg load: ~${(hours / 24).toFixed(1)} days to 10%`;
    }
    title = "Average draw over the last hour, no PV factored in.";
  } else if (naive.status === "charging") {
    text = "1h-avg: charging";
  } else if (naive.status === "idle") {
    text = "1h-avg: idle";
  }

  if (text) {
    fcEl.textContent = text;
    fcEl.title = title;
    fcEl.hidden = false;
  } else {
    fcEl.hidden = true;
  }
}

// ---------- battery health tile (#109) ----------
// SoC residency histogram + cycle/lifetime numbers. Refreshed on
// route-enter and on every dashboard tick alongside the drift
// sparkline. Cheap query, single rollup-table scan plus a couple
// of latest-table lookups.

// Show/clear a small "from BMS" / "from shunt" caption beneath a
// metric value. Creates the caption span on first call; updates
// text on later calls; hides when src is null.
function _setHealthSource(valueId, src) {
  const val = document.getElementById(valueId);
  if (!val) return;
  let cap = document.getElementById(valueId + "-src");
  if (!cap) {
    cap = document.createElement("div");
    cap.id = valueId + "-src";
    cap.className = "bhealth-src";
    val.parentNode.insertBefore(cap, val.nextSibling);
  }
  if (src) {
    cap.textContent = src;
    cap.hidden = false;
  } else {
    cap.textContent = "";
    cap.hidden = true;
  }
}

async function refreshBatteryHealth() {
  const root = document.querySelector("#panel-battery-health");
  if (!root) return;
  let data;
  try { data = await api("/api/battery-health?days=30"); }
  catch (e) { return; }
  if (!data) return;

  const cy = document.getElementById("bhealth-cycles");
  const lf = document.getElementById("bhealth-lifetime");
  const wc = document.getElementById("bhealth-window-cycles");
  const dy = document.getElementById("bhealth-days");
  const rs = document.getElementById("bhealth-residency-stat");
  const bars = document.getElementById("bhealth-bars");

  // Cycle + lifetime layered fallback (#295):
  //   1. BMS-reported (cleanest, manufacturer cycle definition)
  //   2. Shunt-reported (Junctek surfaces its own counter; most don't)
  //   3. Shunt-derived (cumulative_discharge_ah ÷ bank capacity,
  //      works on any Renogy / Junctek shunt because they all expose
  //      the lifetime Ah counter)
  //   4. "BMS or shunt only", no upstream reports it.
  // Provenance lives in the title tooltip so the headline number
  // stays uncluttered. The label hint shows where it came from in
  // small grey text below the number.
  const bms = data.bms || {};
  const shunt = data.shunt || {};
  if (cy) {
    let value = null, src = null, tip = null;
    if (bms.cycle_count != null) {
      value = Math.round(bms.cycle_count).toLocaleString();
      src = "from BMS";
      tip = "Reported by the BMS. Typically increments per full discharge-then-charge.";
    } else if (shunt.cycle_count != null) {
      value = Math.round(shunt.cycle_count).toLocaleString();
      src = "from shunt";
      tip = "Reported by the shunt's onboard cycle counter.";
    } else if (shunt.estimated_cycle_count != null) {
      value = shunt.estimated_cycle_count.toFixed(1);
      src = "from shunt";
      tip = "Estimated: lifetime discharged Ah ÷ bank capacity. Lands within ~5% of the BMS counter where both are present.";
    }
    if (value != null) {
      cy.textContent = value;
      cy.classList.remove("bhealth-v-empty");
      cy.title = tip;
      _setHealthSource("bhealth-cycles", src);
    } else {
      cy.textContent = "BMS only";
      cy.classList.add("bhealth-v-empty");
      cy.title = "Add a BMS or supported shunt (Renogy / Junctek) to surface a cycle count. Equivalent cycles below works without either.";
      _setHealthSource("bhealth-cycles", null);
    }
  }
  if (lf) {
    let value = null, src = null, tip = null;
    if (bms.lifetime_throughput_kwh != null) {
      const v = bms.lifetime_throughput_kwh;
      value = v >= 1000 ? `${(v / 1000).toFixed(2)} MWh` : `${v.toFixed(1)} kWh`;
      src = "from BMS";
      tip = "Lifetime energy moved through the bank (BMS-reported).";
    } else if (shunt.lifetime_throughput_kwh != null) {
      const v = shunt.lifetime_throughput_kwh;
      value = v >= 1000 ? `${(v / 1000).toFixed(2)} MWh` : `${v.toFixed(1)} kWh`;
      src = "from shunt";
      tip = "Lifetime discharged Ah (shunt accumulator) × bank nominal voltage.";
    }
    if (value != null) {
      lf.textContent = value;
      lf.classList.remove("bhealth-v-empty");
      lf.title = tip;
      _setHealthSource("bhealth-lifetime", src);
    } else {
      lf.textContent = "BMS only";
      lf.classList.add("bhealth-v-empty");
      lf.title = "Connect a JK / Lynx BMS, or any Renogy / Junctek shunt, to track lifetime throughput across the bank.";
      _setHealthSource("bhealth-lifetime", null);
    }
  }
  if (wc) {
    const ec = data.window_equivalent_cycles;
    if (ec != null) {
      wc.textContent = ec.toFixed(1);
      wc.title = `Computed: discharged kWh in window ÷ bank capacity. Works without a BMS.`;
    } else {
      wc.textContent = "·";
    }
  }
  if (dy) {
    dy.textContent = data.days_online != null ? `${data.days_online}` : "·";
  }

  // Residency histogram, 10 bars, % time in each 10% SoC band. A
  // healthy LFP bank lives in 50-95%; visible weight at the low end
  // means the user's draining too deep and shortening lifespan.
  const resid = data.soc_residency || [];
  const total = resid.reduce((a, b) => a + (b.pct || 0), 0);
  if (rs) {
    if (total > 0) {
      // Highlight the band where the bank spends most time.
      const peak = resid.reduce((p, c) => (c.pct > p.pct ? c : p), { pct: -1 });
      rs.textContent = peak.pct > 0
        ? `mostly ${peak.range} (${peak.pct.toFixed(0)}% of the time)`
        : "·";
    } else {
      rs.textContent = "collecting…";
    }
  }
  if (bars) {
    if (total === 0) {
      bars.innerHTML = '<div class="empty-spark">collecting…</div>';
    } else {
      // Bars coloured by SoC band, red for low, amber mid, green high.
      const palette = ["#dc4d4d", "#dc7a4d", "#e0a04a", "#e0c04a", "#c6c04a",
                       "#8fc04a", "#5fc06a", "#4fbf7f", "#43b88f", "#3aa080"];
      const maxPct = Math.max(...resid.map(r => r.pct || 0), 1);
      bars.innerHTML = resid.map((r, i) => {
        const h = Math.max(2, (r.pct / maxPct) * 100);
        const colour = palette[i] || "#888";
        return `<div class="bhealth-bar" style="height:${h}%;background:${colour}" title="${r.range}: ${r.pct.toFixed(1)}%"></div>`;
      }).join("");
    }
  }
}

// ---------- load heatmap ----------
async function refreshHeatmap() {
  const root = document.querySelector("#heatmap");
  if (!root) return;
  let data;
  try { data = await api("/api/load_heatmap?days=30"); }
  catch (e) { return; }
  drawHeatmap(root, data);

  // Sub-header: how much data is actually in the grid? Empty cells look
  // like bugs unless the user knows the daemon just hasn't seen them yet.
  const sub = document.querySelector("#heatmap-sub");
  if (sub && data?.counts) {
    let filled = 0, total = 0;
    for (const row of data.counts) {
      for (const c of row) { total++; if (c > 0) filled++; }
    }
    sub.textContent = `last 30 days · hour × day · ${filled}/${total} cells with data`;
  }
}

function drawHeatmap(root, data) {
  const grid = data.grid;
  const max = data.max_w || 1;
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  let html = '<div class="heatmap-rows">';
  // X axis hours header
  html += '<div class="hm-row hm-row--head">';
  html += '<div class="hm-cell hm-cell--label"></div>';
  for (let h = 0; h < 24; h++) {
    html += `<div class="hm-cell hm-cell--hour">${h % 6 === 0 ? h : ""}</div>`;
  }
  html += '</div>';

  for (let d = 0; d < 7; d++) {
    html += '<div class="hm-row">';
    html += `<div class="hm-cell hm-cell--label">${days[d]}</div>`;
    for (let h = 0; h < 24; h++) {
      const v = grid[d][h];
      if (v == null) {
        html += '<div class="hm-cell hm-cell--empty" title="no data"></div>';
      } else {
        const intensity = Math.min(1, v / max);
        // HSL warm-gold→red ramp. In dark mode low values start as dim
        // charcoal and climb to vivid red. In light mode we invert the
        // brightness ramp so low values are near-white and high values
        // are saturated red, the same temperature reading on either bg.
        const hue = 50 - 50 * intensity;
        const light = document.documentElement.getAttribute("data-theme") === "light";
        const sat = light ? (40 + 50 * intensity) : (35 + 60 * intensity);
        const lig = light ? (92 - 40 * intensity) : (18 + 32 * intensity);
        const color = `hsl(${hue}, ${sat}%, ${lig}%)`;
        const label = `${days[d]} ${String(h).padStart(2,"0")}:00 · ${v.toFixed(0)} W avg`;
        html += `<div class="hm-cell hm-cell--data" style="background:${color}" title="${label}"></div>`;
      }
    }
    html += '</div>';
  }
  html += '</div>';

  // Legend
  html += `<div class="hm-legend">
    <span>Low</span>
    <span class="hm-legend-grad"></span>
    <span>High · ${data.max_w.toFixed(0)} W max</span>
  </div>`;

  root.innerHTML = html;
}
