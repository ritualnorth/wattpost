// solar-monitor frontend (v3)
// Vanilla JS, no build step. Renders the convergent off-grid dashboard:
//   - conditional alert banner
//   - hero with SOC donut + remaining-time + net W
//   - data-driven power flow strip (sources / battery / loads — tiles
//     appear and disappear with installed devices)
//   - today's totals strip
//   - cell balance grid
//   - history chart (uPlot)
//   - per-device detail cards
//
// All values from the daemon's /api are SI. UI doesn't convert — display
// in SI; future user pref can convert at the edge.
//
// --------------- DEVICE → FLOW MAPPING --------------------
// Adding a new device kind means adding an entry here. Each device can
// contribute to "sources", "loads", or "battery" with a metric reference.
// The mapping is intentionally small and obvious: protocol-specific
// quirks belong in the driver, not the UI.

// ---------- inline icons (line-art SVG strings, tint via currentColor) ----------
const ICONS = {
  // Sun — for PV / solar sources
  sun: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="12" cy="12" r="4.2"/>
    <path d="M12 2v2.5M12 19.5V22M3.5 12H6M18 12h2.5M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8"/>
  </svg>`,
  // Battery — for the bank / storage tile
  battery: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <rect x="2.5" y="7" width="17" height="10" rx="2"/>
    <line x1="21.5" y1="10" x2="21.5" y2="14"/>
    <rect x="5" y="9.5" width="3" height="5" rx=".4" fill="currentColor"/>
    <rect x="9" y="9.5" width="3" height="5" rx=".4" fill="currentColor"/>
    <rect x="13" y="9.5" width="3" height="5" rx=".4" fill="currentColor"/>
  </svg>`,
  // Lightning bolt — AC loads / inverter output
  bolt: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <path d="M13 2 4 14h7l-1 8 9-12h-7l1-8z"/>
  </svg>`,
  // Plug — DC loads
  plug: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <path d="M9 2v4M15 2v4"/>
    <path d="M7 6h10v6a5 5 0 0 1-10 0V6z"/>
    <path d="M12 17v5"/>
  </svg>`,
  // Generator / engine
  generator: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <rect x="3" y="8" width="18" height="10" rx="2"/>
    <circle cx="9" cy="13" r="2"/>
    <path d="M15 11h3M15 15h3"/>
    <path d="M6 8V5h6v3"/>
  </svg>`,
  // Car / alternator
  alternator: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="12" cy="12" r="9"/>
    <circle cx="12" cy="12" r="3.5"/>
    <path d="M12 3v3.5M12 17.5V21M3 12h3.5M17.5 12H21"/>
  </svg>`,
  // Question mark — fallback / unknown
  unknown: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="12" cy="12" r="9"/>
    <path d="M9.5 9.5a2.5 2.5 0 1 1 4.6 1.4c-.6.8-1.6 1-1.6 2.1"/>
    <line x1="12" y1="17" x2="12" y2="17.01"/>
  </svg>`,
  // House — for inferred "everything else" loads (heater, fridge, lights,
  // anything pulling from the bank that doesn't go through the controller)
  house: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <path d="M3 11l9-7 9 7"/>
    <path d="M5 9.5V21h14V9.5"/>
    <path d="M10 21v-6h4v6"/>
  </svg>`,
  // Plug-in / external power feed — unmeasured source (mains charger, etc.)
  feed: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <path d="M3 12h8"/>
    <path d="M7 8v8"/>
    <rect x="11" y="9" width="10" height="6" rx="1"/>
    <path d="M14 9V6M18 9V6"/>
  </svg>`,
};

// Returns the user-visible name for a device — the override set via
// /api/devices/<label>/display-name if present, else the underlying
// label. Use this everywhere a device is *displayed* to a human; keep
// the raw label for routing, storage keys, and API paths.
function dispName(d) {
  if (!d) return "";
  const v = (d.display_name || "").trim();
  return v || d.label;
}

// Map color → icon key (used for default tile icons when no explicit override)
const COLOR_TO_ICON = {
  pv: "sun",
  batt: "battery",
  ac: "bolt",
  dc: "plug",
  grid: "generator",
  neutral: "unknown",
};

// Per-device-kind icon for detail cards
const KIND_ICON = {
  charge_controller: "sun",
  smart_battery: "battery",
  inverter: "bolt",
  dcdc_charger: "alternator",
  dcdc: "alternator",
  dcdc_xs: "alternator",
  ac_charger: "feed",
  shunt: "battery",
  bms: "battery",
  load_disconnect: "plug",
  // Synthetic aggregate — re-uses the battery glyph but the label
  // (and a CSS hook in styles.css, .dev-card.kind-bank) sets it
  // apart so users read it as "the whole bank" not "another pack".
  bank: "battery",
};

// Status pill icons by state
const STATUS_ICONS = {
  ok:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="5 13 10 18 19 7"/></svg>`,
  warn: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2 21h20L12 3z"/><line x1="12" y1="10" x2="12" y2="15"/><line x1="12" y1="18" x2="12" y2="18.01"/></svg>`,
  err:  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>`,
};

const FLOW_MAPPING = {
  charge_controller: {
    sources: [{ id: "pv", label: "Solar", color: "pv", icon: "sun",
                metric: "pv_power_w",
                vMetric: "pv_voltage_v", aMetric: "pv_current_a" }],
    loads:   [{ id: "ctrl_load", label: "DC Load", color: "dc", icon: "plug",
                metric: "load_power_w",
                vMetric: "load_voltage_v", aMetric: "load_current_a",
                onlyIf: (l) => (+l.load_power_w || 0) > 0 || l.load_status === "on" }],
  },
  smart_battery: { battery: true },
  shunt:         { battery: true },
  bms:           { battery: true },  // Victron Lynx Smart BMS, JK BMS
  // Renogy DCC50S / DCC30S / DCC25S / DCC15S — alternator + MPPT
  // combos. The unit has TWO input sides: an engine-driven DC-DC
  // (alternator) AND a built-in MPPT (PV). Both render as separate
  // source nodes so a customer with solar wired into their DCC sees
  // sun + engine independently rather than one ambiguous source.
  //
  // Latent bug fix: the alternator metric was named `alt_power_w`
  // here but the driver publishes `alternator_power_w`. DCC owners
  // had been seeing 0 W on the alternator side since #123 shipped.
  dcdc_charger:  {
    sources: [
      { id: "dcc_alt", label: "Alternator", color: "dc", icon: "alternator",
        metric: "alternator_power_w",
        vMetric: "alternator_voltage_v", aMetric: "alternator_current_a" },
      { id: "dcc_pv",  label: "Solar",      color: "pv", icon: "sun",
        metric: "pv_power_w",
        vMetric: "pv_voltage_v",          aMetric: "pv_current_a" },
    ],
  },
  // Victron Orion DC-DC chargers (Orion-Tr Smart + Orion XS). Same
  // flow role as a Renogy DCC: takes alternator/aux DC, outputs to
  // the bank. The Victron driver exposes input/output as paired
  // current/voltage; we surface the OUTPUT power as the contribution
  // to the bank (input - efficiency = output, the output is what
  // actually reaches the busbar). Colour `dc` (amber) same as the
  // Renogy combo above — both are "engine-driven DC input."
  dcdc:    { sources: [{ id: "alt", label: "DC-DC", color: "dc", icon: "alternator",
                          metric: "output_power_w",
                          vMetric: "output_voltage_v", aMetric: "output_current_a" }] },
  dcdc_xs: { sources: [{ id: "alt", label: "DC-DC", color: "dc", icon: "alternator",
                          metric: "output_power_w",
                          vMetric: "output_voltage_v", aMetric: "output_current_a" }] },
  // Victron AC chargers (Blue Smart IP22 / IP65 / Phoenix Smart).
  // Up to 3 outputs but most installs use output 1 only; we surface
  // output_1 here. Multi-output models would need this expanded to
  // sum all three or render them separately — out of scope for now
  // since output_2 / output_3 are rare in van/cabin installs.
  ac_charger: {
    sources: [{ id: "ac_chg", label: "AC Charger", color: "grid", icon: "plug",
                metric: "output_1_power_w",
                vMetric: "output_1_voltage_v", aMetric: "output_1_current_a" }],
  },
  inverter: {
    loads: [{ id: "ac", label: "AC Load", color: "ac", icon: "bolt",
              metric: "ac_output_power_w", vMetric: "ac_output_voltage_v",
              aMetric: "ac_output_current_a" }],
  },
  // Victron SmartBatteryProtect / similar load-disconnect modules.
  // Their telemetry is mostly state (connected / disconnected / fault)
  // not power — we don't have a clean "watts through" reading to plot,
  // so they don't appear in the flow strip directly. They show up in
  // the device cards under Devices instead. Listed here so the kind
  // is "known" to FLOW_MAPPING (avoids the "Other source" inferred
  // fallback firing because the kind wasn't in the table).
  load_disconnect: {},
};

// ---------- helpers ----------
const $ = (sel) => document.querySelector(sel);

const fmt = {
  num(v, digits = 2) {
    if (v === null || v === undefined || typeof v !== "number") return "·";
    const abs = Math.abs(v);
    if (abs >= 10000) return (v / 1000).toFixed(1) + "k";
    if (abs >= 100)   return v.toFixed(0);
    if (abs >= 10)    return v.toFixed(1);
    return v.toFixed(digits);
  },
  wh(v) {
    if (v == null) return "·";
    if (Math.abs(v) >= 1000) return (v / 1000).toFixed(2) + " kWh";
    return v.toFixed(0) + " Wh";
  },
  ah(v, d = 1) {
    if (v == null) return "·";
    return v.toFixed(d);
  },
  signed(v, digits = 0) {
    if (v == null) return "·";
    return (v > 0 ? "+" : "") + v.toFixed(digits);
  },
  ago(unixTs) {
    const s = Math.floor(Date.now() / 1000) - unixTs;
    if (s < 60) return s + "s ago";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    return Math.floor(s / 3600) + "h ago";
  },
  duration(hours) {
    if (hours == null || !isFinite(hours)) return "·";
    if (hours < 1)   return Math.round(hours * 60) + " m";
    if (hours < 48)  return `${Math.floor(hours)}h ${Math.round((hours % 1) * 60)}m`;
    const days = hours / 24;
    if (days < 30)   return `${Math.floor(days)}d ${Math.round((days % 1) * 24)}h`;
    return `${Math.floor(days)}d`;
  },
};
const unitFromKey = (k) => {
  if (k.endsWith("_v"))  return "V";
  if (k.endsWith("_a"))  return "A";
  if (k.endsWith("_w"))  return "W";
  if (k.endsWith("_wh")) return "Wh";
  if (k.endsWith("_ah")) return "Ah";
  if (k.endsWith("_c"))  return "°C";
  if (k.endsWith("_hz")) return "Hz";
  if (k === "battery_percentage") return "%";
  return "";
};
const prettyKey = (k) =>
  k.replace(/_v$/, " (V)").replace(/_a$/, " (A)").replace(/_w$/, " (W)")
   .replace(/_wh$/, " (Wh)").replace(/_ah$/, " (Ah)").replace(/_c$/, " (°C)")
   .replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());

// ---------- state ----------
let devices = [];
let chart = null;
let currentRange = "24h";
let lastRun = null;
let todayAggregate = null;  // /api/today result, refreshed alongside devices
// Last solar-pause decision from the daemon (#163). The Flow strip reads
// this to add a "Paused, solar covering" sub-label to the AC-charger
// tile so the rule's effect is visible from the dashboard, not just
// from Settings.
let solarPauseStatus = null;
// Per-frame cache for derived views the hero + flow + alerts all share
// (#162). applySnapshot() resets these every tick; consumer functions
// reuse the cached value within the same frame so all tiles agree on
// staleness boundaries and bank.netW.
const _frame = { nowSec: 0, bank: null, flowModel: null };

// ---------- demo-mode banner (one-shot, runs at boot) ----------
// /api/system/info exposes `demo: true|false` from the WATTPOST_DEMO=1
// container env. The Settings → About flow also reads this and toggles
// the banner, but Settings isn't visited on most page loads — pull the
// check up to boot so the banner appears immediately on every page,
// kiosk mode included.
//
// Exposed as a top-level promise so renderStatus() can await it before
// triggering the "Setup needed" wizard redirect — without this gate,
// the SSE snapshot can arrive first and demo visitors get yanked into
// the wizard before the banner classifies them as a demo session.
window._demoReady = (async () => {
  try {
    const r = await fetch("/api/system/info");
    if (!r.ok) return false;
    const info = await r.json();
    if (info.demo) {
      const b = document.getElementById("demo-banner");
      if (b) b.hidden = false;
      document.body.classList.add("is-demo");
      return true;
    }
  } catch (_) { /* no banner on fetch failure */ }
  return false;
})();

// ---------- theme ----------
// Preference is "system" | "dark" | "light". The inline <head> script sets
// the resolved data-theme before paint to avoid FOUC; here we react to
// Settings changes and OS changes, and republish a CSS-variable palette
// to whatever renders (charts, heatmap).
const THEME_KEY = "wp-theme";
const META_BG = { dark: "#0a0d12", light: "#f4f6fa" };
function themePref() {
  try { return localStorage.getItem(THEME_KEY) || "system"; }
  catch (_) { return "system"; }
}
function resolveTheme(pref) {
  if (pref === "dark" || pref === "light") return pref;
  return matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}
function applyTheme(pref) {
  try { localStorage.setItem(THEME_KEY, pref); } catch (_) {}
  const resolved = resolveTheme(pref);
  document.documentElement.setAttribute("data-theme", resolved);
  const meta = document.getElementById("meta-theme-color");
  if (meta) meta.setAttribute("content", META_BG[resolved]);
  document.querySelectorAll(".theme-opt").forEach(btn => {
    btn.setAttribute("aria-checked", btn.dataset.themePref === pref ? "true" : "false");
  });
  // Charts + heatmap bake CSS-derived colours at draw time, so re-run any
  // visible renderer.
  const route = currentRouteName?.();
  if (route === "history") { refreshChart?.(); refreshHeatmap?.(); }
  else if (route === "dashboard") { refreshDriftSparkline?.(); refreshBatteryHealth?.(); refreshRuntimeForecast?.(); }
}
// Floating value-at-cursor tooltip for every uPlot chart. Without
// this, tap/hover puts uPlot's cursor on a point but the value just
// vanishes into the (hidden / live:false) legend. With it, every
// chart answers "what was that bump at 14:23?" in-place.
//
// Reads the same series.value() formatters the legend would use, so
// units + decimal places stay consistent across the page.
function valueTooltipPlugin() {
  let tip;
  return {
    hooks: {
      init: (u) => {
        tip = document.createElement('div');
        tip.className = 'chart-value-tip';
        tip.style.display = 'none';
        u.over.appendChild(tip);
      },
      setCursor: (u) => {
        if (!tip) return;
        const { idx, left, top } = u.cursor;
        if (idx == null || left == null || left < 0) {
          tip.style.display = 'none';
          return;
        }
        const xVal = u.data[0]?.[idx];
        if (xVal == null) { tip.style.display = 'none'; return; }
        const d = new Date(xVal * 1000);
        const head = d.toLocaleString([], {
          month: 'short', day: 'numeric',
          hour: '2-digit', minute: '2-digit',
        });
        const rows = [];
        for (let s = 1; s < u.series.length; s++) {
          const ser = u.series[s];
          if (!ser.label || ser.show === false) continue;
          const v = u.data[s]?.[idx];
          if (v == null) continue;
          const fmt = (typeof ser.value === 'function')
            ? ser.value(u, v, s, idx)
            : String(v);
          const color = typeof ser.stroke === 'string' ? ser.stroke : 'currentColor';
          rows.push(`<div><span class="chart-value-tip-sw" style="background:${color}"></span>${ser.label}: <em>${fmt}</em></div>`);
        }
        if (!rows.length) { tip.style.display = 'none'; return; }
        tip.innerHTML = `<strong>${head}</strong>${rows.join('')}`;
        tip.style.display = 'block';
        // Flip to the left of the cursor near the right edge so the
        // tip never escapes the chart bounds.
        const tw = tip.offsetWidth;
        const cw = u.over.clientWidth;
        const x = (left + tw + 16 > cw) ? Math.max(0, left - tw - 8) : (left + 12);
        const y = Math.max(4, Math.min((top ?? 12) - 8, u.over.clientHeight - tip.offsetHeight - 4));
        tip.style.left = `${x}px`;
        tip.style.top = `${y}px`;
      },
      destroy: () => {
        if (tip && tip.parentNode) tip.parentNode.removeChild(tip);
        tip = null;
      },
    },
  };
}

function chartPalette() {
  const s = getComputedStyle(document.documentElement);
  const read = k => s.getPropertyValue(k).trim();
  return {
    axis:       read("--text-3")            || "#6b7689",
    grid:       read("--chart-grid")        || "rgba(106,118,137,0.08)",
    gridStrong: read("--chart-grid-strong") || "rgba(106,118,137,0.15)",
    accent:     read("--accent")            || "#58a6ff",
    accentFill: read("--chart-accent-fill") || "rgba(88,166,255,0.16)",
    bandFill:   read("--chart-band-fill")   || "rgba(88,166,255,0.12)",
    amber:      read("--amber")             || "#d29922",
    amberFill:  read("--chart-amber-fill")  || "rgba(210,153,34,0.15)",
  };
}
// Follow OS palette while in "system" mode.
matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
  if (themePref() === "system") applyTheme("system");
});

// ---------- kiosk mode ----------
// Wall-mounted tablet view: chrome-free, SoC + power flow at chunky size,
// with a Wake Lock to keep the screen on while the route is active. The
// "default to kiosk on this device" preference is localStorage so other
// devices keep their normal view.
const KIOSK_KEY = "wp-kiosk-default";
let wakeLock = null;
function kioskDefault() {
  try { return localStorage.getItem(KIOSK_KEY) === "1"; }
  catch (_) { return false; }
}
function setKioskDefault(on) {
  try { localStorage.setItem(KIOSK_KEY, on ? "1" : "0"); } catch (_) {}
}
async function requestWakeLock() {
  if (!("wakeLock" in navigator)) return;
  try {
    wakeLock = await navigator.wakeLock.request("screen");
    wakeLock.addEventListener?.("release", () => { wakeLock = null; });
  } catch (_) {
    // Browser denied (user not in a tab-visible state, no permission). The
    // tablet will still display; screen may eventually dim per OS rules.
  }
}
function releaseWakeLock() {
  if (!wakeLock) return;
  wakeLock.release().catch(() => {});
  wakeLock = null;
}
function onEnterKiosk() {
  document.body.classList.add("kiosk-active");
  requestWakeLock();
}
function onLeaveKiosk() {
  document.body.classList.remove("kiosk-active");
  releaseWakeLock();
}
// Reacquire the wake lock when the tab comes back to the foreground.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" &&
      document.body.classList.contains("kiosk-active") &&
      !wakeLock) {
    requestWakeLock();
  }
});

// ---------- status header ----------
function setStatus(cls, text) {
  const el = $("#status");
  el.classList.remove("ok", "warn", "err");
  if (cls) el.classList.add(cls);
  el.querySelector(".text").textContent = text;
  const iconHost = el.querySelector(".status-icon");
  if (iconHost) iconHost.innerHTML = STATUS_ICONS[cls] || "";
}

// ---------- api ----------
// Kiosk-via-tunnel uses ?key=<token> bearer auth (see middleware in
// solar_monitor/api/app.py). When the page was loaded at
// /kiosk?key=<token>, every subsequent /api/* fetch needs to carry
// that same key or the appliance will 401. Captured once at load
// from window.location and re-applied to outgoing URLs.
const KIOSK_KEY_PARAM = (() => {
  try {
    if (window.location.pathname !== "/kiosk") return "";
    return new URLSearchParams(window.location.search).get("key") || "";
  } catch (_) { return ""; }
})();

// True when the dashboard is being served via the cloud broker
// (`<slug>.wattpost.cloud` / legacy `.wattpost.io`) rather than
// directly on the appliance's LAN IP. Used to suppress UI bits
// that only make sense for someone with full local access — most
// notably the Kiosk → Exit button, which on broker would dump
// the customer into the Settings/Devices/etc full chrome that
// app.wattpost.cloud is supposed to own. Locally (192.168.x or
// wattpost.local) Exit stays — the van/cabin operator wants to
// flip between the SoC kiosk and the dashboard freely.
const IS_BROKER_VIEW = (() => {
  try {
    const h = (window.location.hostname || "").toLowerCase();
    return /\.wattpost\.cloud$|\.wattpost\.io$/.test(h);
  } catch (_) { return false; }
})();
function _withKiosk(path) {
  if (!KIOSK_KEY_PARAM) return path;
  // Only attach to relative paths (don't leak the token to third
  // parties on absolute URLs). The appliance API is always relative.
  if (/^https?:/.test(path)) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}key=${encodeURIComponent(KIOSK_KEY_PARAM)}`;
}
async function api(path) {
  const r = await fetch(_withKiosk(path));
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

// Shared apply path: REST fallback and the SSE stream both flow through
// here. The frame shape (devices / poll_run / today) is the same in both
// directions so this stays a one-liner per renderer.
//
// Snapshot lock (#162): every renderer derived from `devices` — hero,
// flow tile, alerts — used to call aggregateBank() independently, each
// recomputing its own staleness floor against a freshly-read Date.now().
// On the 90 s boundary that meant the hero could include a battery the
// flow tile had just classed as silent, leaving the two tiles in visible
// disagreement. We now stamp the frame's "now" once and memoise the
// bank + flow model so every consumer sees the same view of the world.
function applySnapshot(frame) {
  devices = frame.devices || [];
  lastRun = frame.poll_run?.last_run || null;
  // Keep the Settings tab's Daemon row in sync when poll data arrives
  // (no-op if the row isn't in the DOM yet — it's only rendered when
  // Settings is active).
  renderDaemonStatus();
  todayAggregate = frame.today || null;
  solarPauseStatus = frame.solar_pause || null;
  // Frame-scoped cache. Reset on every apply so a stale value from the
  // previous tick can't leak into the next render.
  _frame.nowSec = Date.now() / 1000;
  _frame.bank = null;
  _frame.flowModel = null;
  renderStatus(frame.poll_run || {});
  renderHero();
  renderFlow();
  // Kiosk view shares aggregateBank/buildFlowModel but lives in a
  // separate DOM tree — mirror the flow strip into it on every frame.
  const kioskFlow = $("#kiosk-flow");
  if (kioskFlow) renderFlow(kioskFlow);
  renderToday();
  renderWeather();
  renderLocationTile();  // #264 — "Where you are" map
  renderWeek();
  renderCells();
  renderDeviceCards();
  populateChartSelectors();
  renderAlerts();
  $("#devices-meta").textContent = lastRun
    ? `${devices.length} devices · last poll ${lastRun.elapsed_ms} ms · ${fmt.ago(lastRun.ts)}`
    : "";
  // If we landed on a #/device/<label> route before the first snapshot
  // arrived, `devices` was empty and renderDeviceDetail showed the
  // "no such device" message. Re-render now that data is in hand.
  try {
    const r = parseRoute();
    if (r.name === "device" && devices.find(d => d.label === r.label)) {
      const host = document.querySelector("#device-route");
      if (host && host.querySelector("a[href='#/devices']")) {
        // Only re-render if the placeholder is showing (avoid clobbering
        // an already-good detail view + its wireups on subsequent polls).
        const isPlaceholder = host.textContent.includes("No device named");
        if (isPlaceholder) renderDeviceDetail(r.label);
      }
    }
  } catch {}
}

async function refresh() {
  // One-fetch path (#162): /api/snapshot returns devices + poll_run +
  // today from a single store read so the polling-fallback frame is as
  // atomic as the SSE one. The old three-fetch path could let the
  // server complete a new poll between calls, leaving e.g. devices
  // from cycle N+1 paired with today from cycle N.
  try {
    const frame = await api("/api/snapshot");
    applySnapshot({
      devices: frame.devices || [],
      poll_run: frame.poll_run || {},
      today: frame.today || null,
    });
  } catch (e) {
    // Older builds (Docker users who haven't pulled :latest yet) won't
    // have /api/snapshot. Fall back to the three-fetch form so a stale
    // server doesn't break the dashboard outright.
    if (`${e.message || e}`.includes("404")) {
      try {
        const [devs, run, today] = await Promise.all([
          api("/api/devices"),
          api("/api/poll_run"),
          api("/api/today").catch(() => null),
        ]);
        applySnapshot({
          devices: devs.devices || [],
          poll_run: run,
          today: today,
        });
        return;
      } catch (inner) { e = inner; }
    }
    setStatus("err", "API error: " + e.message);
  }
}

// ---------- live stream (SSE) ----------
// EventSource is the lightest live-update mechanism the browser ships
// with — auto-reconnects, no protocol upgrade, plain HTTP. We open one
// on boot; the server hand-delivers a full snapshot immediately and a
// new one after every poll. Polling stays around as a fallback.
let eventStream = null;
let pollingFallbackTimer = null;
// One-shot guard for the first-boot auto-redirect into the setup
// wizard. Without it, removing the last transport mid-session would
// kick the user back to #/setup unexpectedly.
let _firstBootRedirected = false;
function openStream() {
  if (eventStream) return;
  // Broker-origin shortcut: never open SSE when loaded over the cloud
  // broker hostname. Previously we let SSE open and closed it later
  // once /api/system/auth-status resolved with origin=broker, but on
  // iOS Safari that race already cost you: the moment the EventSource
  // connection is in flight, Safari serialises its tiny per-host HTTP
  // pool around it. Every /api/* fetch then queues behind an SSE
  // that's about to close → white page. Skipping SSE entirely at the
  // URL level dodges the trap. See memory: ios_safari_sse_tunnel.
  if (IS_BROKER_VIEW) {
    startPollingFallback();
    return;
  }
  try {
    eventStream = new EventSource("/api/stream");
  } catch (e) {
    console.warn("EventSource unavailable, falling back to polling", e);
    startPollingFallback();
    return;
  }
  eventStream.addEventListener("message", (ev) => {
    try {
      const frame = JSON.parse(ev.data);
      applySnapshot(frame);
      stopPollingFallback();
    } catch (e) {
      console.error("snapshot parse failed", e);
    }
  });
  eventStream.addEventListener("error", () => {
    // EventSource will retry on its own; keep a polling tick alive so the
    // UI doesn't freeze in the meantime.
    startPollingFallback();
  });
}
function startPollingFallback() {
  if (pollingFallbackTimer) return;
  pollingFallbackTimer = setInterval(refresh, 5000);
}
function stopPollingFallback() {
  if (!pollingFallbackTimer) return;
  clearInterval(pollingFallbackTimer);
  pollingFallbackTimer = null;
}

async function _maybeFirstBootRedirect() {
  // Wait for /api/system/info to resolve before deciding — without
  // this gate, the SSE snapshot can arrive first and demo visitors
  // get yanked into the wizard before the demo flag classifies them.
  try { await window._demoReady; } catch (_) {}
  if (document.body.classList.contains("is-demo")) return;
  if (_firstBootRedirected) return;
  const h = (window.location.hash || "").replace(/^#\/?/, "").trim();
  const onLandable = (h === "" || h === "dashboard");
  if (!onLandable) return;
  _firstBootRedirected = true;
  window.location.hash = "#/setup";
}

function renderStatus(run) {
  // Daemon-side issues first — if the scheduler's not running or the
  // last_run is stale, nothing else matters.
  if (!run.scheduler_running) { setStatus("err", "Offline"); return; }
  const t = run.transports || {};
  // Demo mode (demo.wattpost.io) has a synthetic poller and no real
  // transports — the wizard redirect and "Setup needed" / "BLE not
  // connected" warnings are nonsense there. Body class is set by the
  // /api/system/info bootstrap at the top of this file.
  const isDemo = document.body.classList.contains("is-demo");
  // Setup-state checks. Order matters: "set up the wizard" wins over
  // "no devices yet" so a first-boot user is pointed at the right
  // next action.
  if (!isDemo && (t.configured || 0) === 0) {
    setStatus("warn", "Setup needed");
    // First-boot redirect: nothing's configured, nothing's worth
    // showing on the dashboard, so drop the user straight into the
    // wizard rather than making them hunt for Settings → Setup.
    // Only on the home/dashboard routes; respects kiosk, docs, and
    // anyone who's already in the wizard. One-shot via a module
    // flag so a slow-clicker mid-wizard isn't yanked back if the
    // first transport gets added then removed. Async because we
    // must wait for the demo check before firing.
    _maybeFirstBootRedirect();
    return;
  }
  if (!isDemo && (t.open || 0) === 0) { setStatus("warn", "BLE not connected"); return; }
  // Now the polling-health view. last_run might be null on a fresh
  // daemon that hasn't completed its first poll yet.
  if (!run.last_run) { setStatus("warn", "Connecting…"); return; }
  const lr = run.last_run;
  const ageS = Math.floor(Date.now() / 1000) - lr.ts;
  if      (ageS > 300)            setStatus("err",  "Stale");
  else if (lr.errors_count > 0)   setStatus("warn", `${lr.errors_count} error${lr.errors_count===1?"":"s"}`);
  else if (ageS > 120)            setStatus("warn", "Comms slow");
  else                            setStatus("ok",   "Healthy");
}

// ---------- BANK AGGREGATE ----------
// Bank-level numbers come from whichever data source is most authoritative:
//
//   1. A `shunt` device measures the real busbar current/voltage and tracks
//      SoC against a user-declared bank capacity. If one is present, it wins
//      for the bank's headline numbers — that's the case for users with
//      dumb LiFePO4 packs + a Victron SmartShunt / Renogy 500A monitor.
//
//   2. Otherwise, we sum across smart batteries (the typical Renogy rig).
//
//   3. With nothing addressable, we return null and the dashboard hides
//      the bank hero.
// Bank-level aggregator — mirrors the server-side reconciliation in
// solar_monitor/storage/sqlite.py:_compute_bank_aggregate (#121).
//
// Two distinct layers:
//   * System metrics (V, A, SoC, capacity, remaining) come from the
//     shunt when present, otherwise the BMS pack-sum. "Source" tag
//     in the output tells consumers which side fed the numbers.
//   * Cell metrics (min/max V across packs, worst pack drift) ALWAYS
//     come from BMSes — shunts don't have per-cell data. Surfaced
//     alongside the system metrics even when the shunt is driving.
//
// When BOTH a shunt and one or more BMSes are present AND their SoC
// readings disagree by >5 percentage points, attach a `disagreement`
// object so the hero tile can render a quiet "shunt 65%, BMS 72%,
// showing shunt — tap to investigate" hint.
function aggregateBank() {
  // Return the cached frame value if applySnapshot already computed one
  // (#162). Without this, hero + flow + alerts each re-derive their own
  // view of "fresh" against drifting Date.now() reads, leading to visible
  // disagreement on the 90 s staleness boundary.
  if (_frame.bank !== null) return _frame.bank;
  // Skip devices whose latest poll is stale (>90 s). Without this,
  // an offline Renogy BT-2 keeps feeding the bank aggregate from a
  // snapshot 50+ minutes old — the SoC donut and Net-power tile then
  // claim "live 96.8 % · -80 W" when the truth is "we haven't heard
  // from that BMS for an hour." Mirrors the buildFlowModel staleness
  // check; same 90 s threshold (~1.5 poll cycles of grace).
  const STALE_POLL_SECONDS = 90;
  // Use the frame-stamped "now" so every consumer in a frame agrees on
  // the staleness floor. Falls back to wall-clock for callers outside
  // an applySnapshot (e.g. ad-hoc renders mid-test).
  const nowSec = _frame.nowSec || (Date.now() / 1000);
  const isFresh = (d) => {
    const t = +(d.latest && d.latest._updated_at) || +(d.last_seen) || 0;
    return t > 0 && (nowSec - t) <= STALE_POLL_SECONDS;
  };
  const shunt = devices.find(d => d.kind === "shunt" && isFresh(d));
  const batts = devices.filter(d => d.kind === "smart_battery" && isFresh(d));
  if (!shunt && batts.length === 0) { _frame.bank = null; return null; }

  // ---------- Cell layer (always BMS-sourced) ----------
  let cellMin = null, cellMax = null, worstDrift = 0;
  for (const b of batts) {
    const l = b.latest || {};
    const n = +l.cell_count || 0;
    const cells = [];
    for (let j = 0; j < n; j++) {
      const cv = l[`cell_voltage_${j}_v`];
      if (typeof cv === "number") cells.push(cv);
    }
    if (cells.length) {
      const pmin = Math.min(...cells);
      const pmax = Math.max(...cells);
      worstDrift = Math.max(worstDrift, pmax - pmin);
      cellMin = cellMin === null ? pmin : Math.min(cellMin, pmin);
      cellMax = cellMax === null ? pmax : Math.max(cellMax, pmax);
    }
  }

  // ---------- Candidate system views ----------
  let shuntView = null;
  if (shunt) {
    const l = shunt.latest || {};
    const v = +l.voltage_v || 0;
    const i = +l.current_a || 0;
    const power_w = l.power_w != null ? +l.power_w : v * i;
    const totalCap = +l.bank_capacity_ah || +l.capacity_ah || 0;
    const totalRem = +l.remaining_ah || (totalCap * ((+l.soc_pct || 0) / 100));
    const soc = +l.soc_pct || (totalCap > 0 ? (totalRem / totalCap) * 100 : 0);
    shuntView = {
      source: "shunt",
      model: l.model || shunt.label || "smart shunt",
      soc, meanV: v, sumI: i, netW: power_w, totalCap, totalRem,
      timeToGoMinutes: typeof l.time_to_go_minutes === "number" ? l.time_to_go_minutes : null,
    };
  }
  let bmsView = null;
  if (batts.length) {
    let totalCap = 0, totalRem = 0, sumV = 0, sumI = 0;
    for (const b of batts) {
      const l = b.latest || {};
      totalCap += +l.capacity_ah || 0;
      totalRem += +l.remaining_charge_ah || 0;
      sumV += +l.voltage_v || 0;
      sumI += +l.current_a || 0;
    }
    const meanV = sumV / batts.length;
    const soc = totalCap > 0 ? (totalRem / totalCap) * 100 : 0;
    bmsView = {
      source: "bms",
      model: batts[0]?.latest?.model || "battery",
      soc, meanV, sumI, netW: meanV * sumI, totalCap, totalRem,
      timeToGoMinutes: null,
    };
  }

  // ---------- Source pick (auto policy: shunt > BMS) ----------
  const chosen = shuntView || bmsView;

  // ---------- Disagreement diagnostic ----------
  let disagreement = null;
  if (shuntView && bmsView) {
    const delta = Math.abs(shuntView.soc - bmsView.soc);
    if (delta >= 5) {
      disagreement = {
        shuntSoc: shuntView.soc,
        bmsSoc:   bmsView.soc,
        deltaPct: delta,
        showing:  chosen.source,
      };
    }
  }

  const result = {
    ...chosen,
    packs: batts.length,
    cellMinV: cellMin,
    cellMaxV: cellMax,
    worstDriftV: cellMin === null ? null : worstDrift,
    disagreement,
  };
  _frame.bank = result;
  return result;
}

function computeRemaining(bank) {
  if (!bank) return { primary: "—", secondary: "" };
  // Prefer the shunt's time_to_go_minutes when it's available — it's
  // a Coulomb-counted estimate that knows about your actual recent
  // discharge curve, much better than our V*I extrapolation.
  if (typeof bank.timeToGoMinutes === "number" && bank.timeToGoMinutes > 0) {
    return {
      primary:   fmt.duration(bank.timeToGoMinutes / 60),
      secondary: "until empty · shunt",
    };
  }
  const i = bank.sumI;
  const absI = Math.abs(i);
  // The 1.5 A "Idle" guard is one-sided — it only protects the
  // discharge case. Reporting "2 days until empty" off a 0.6 A
  // standby draw is misleading (the moment real load kicks in,
  // the estimate is wildly wrong). But applying the same threshold
  // to charging would label slow MPPT trickle as "Idle", which is
  // worse — the user IS charging, just slowly. So: only suppress
  // the discharge estimate; always show charging.
  if (i > 0) {
    if (absI < 0.2) {
      return { primary: "Charging", secondary: "trickle" };
    }
    const hoursToFull = (bank.totalCap - bank.totalRem) / i;
    return { primary: fmt.duration(hoursToFull), secondary: "until full" };
  }
  if (absI < 1.5) {
    return {
      primary: absI < 0.1 ? "Idle" : "Light load",
      secondary: absI < 0.1 ? "—" : `${absI.toFixed(2)} A draw`,
    };
  }
  // Discharging at a meaningful rate. Trim a 10% reserve off the
  // remaining capacity before the divide — LFP wants to stay above
  // 10% SoC, and the BMS will cut earlier than 0% anyway. Keeps the
  // estimate from over-promising runtime that the battery will
  // never actually deliver.
  const RESERVE_FRAC = 0.10;
  const usableRem = Math.max(0,
    bank.totalRem - bank.totalCap * RESERVE_FRAC);
  const hoursToEmpty = usableRem / absI;
  return { primary: fmt.duration(hoursToEmpty), secondary: "until empty" };
}

// ---------- HERO ----------
// Position the leading-edge dot (head + halo) at the tip of the arc
// for the given pct. Math: 12 o'clock = pct 0; clockwise to pct 100.
function positionDonutHead(wrap, pct) {
  const r = Number(wrap.dataset.radius || 86);
  const cx = 100, cy = 100;
  const theta = (pct / 100) * 2 * Math.PI;
  const x = cx + r * Math.sin(theta);
  const y = cy - r * Math.cos(theta);
  wrap.querySelectorAll(".donut-head, .donut-head-glow").forEach(el => {
    el.setAttribute("cx", x.toFixed(2));
    el.setAttribute("cy", y.toFixed(2));
  });
}

function renderHero() {
  const bank = aggregateBank();
  if (!bank) {
    $("#bank-soc").textContent = "—";
    $("#donut-arc").setAttribute("stroke-dasharray", "0 100");
    return;
  }
  // SoC
  $("#bank-soc").textContent = bank.soc.toFixed(1);
  const arc = $("#donut-arc");
  const pct = Math.min(100, Math.max(0, bank.soc));
  arc.setAttribute("stroke-dasharray", `${pct} ${100 - pct}`);
  // Mirror the SoC paint onto the kiosk donut (lives in a different DOM
  // tree but uses the same class hooks).
  const kioskArc = document.querySelector(".kiosk-donut .donut-arc");
  if (kioskArc) kioskArc.setAttribute("stroke-dasharray", `${pct} ${100 - pct}`);
  const kioskSoc = $("#kiosk-soc");
  if (kioskSoc) kioskSoc.textContent = bank.soc.toFixed(1);
  // Hero card still hue-tints by SoC band for backwards-compat. Future:
  // drop these classes once the new donut palette covers it.
  const socCls = pct < 20 ? "soc-low" : pct < 50 ? "soc-mid" : "soc-high";
  const heroEl = document.querySelector(".hero-v2");
  if (heroEl) {
    heroEl.classList.remove("soc-low", "soc-mid", "soc-high");
    heroEl.classList.add(socCls);
  }

  // Net power
  const powerTile = $("#bank-power-tile");
  powerTile.classList.remove("charging", "discharging", "idle");
  let powerState;
  if (Math.abs(bank.netW) < 1) powerState = "idle";
  else if (bank.netW > 0) powerState = "charging";
  else powerState = "discharging";
  powerTile.classList.add(powerState);
  $("#bank-power").textContent = fmt.signed(bank.netW, 0);
  $("#bank-power-sub").textContent = {
    idle: "no flow",
    charging: `+${bank.sumI.toFixed(2)} A · charging`,
    discharging: `${bank.sumI.toFixed(2)} A · discharging`,
  }[powerState];

  // Donut wrapper state — drives ring gradient + leading-edge dot color
  // + flow-pill color. Four states:
  //   critical    SoC < 15 (urgent palette overrides flow direction)
  //   charging    netW > +5  AND SoC ≥ 15
  //   discharging netW < −5  AND SoC ≥ 15
  //   holding     |netW| ≤ 5 AND SoC ≥ 15 (the no-flow / idle bucket)
  // Critical wins over flow so a draining-fast-at-12% bank reads RED,
  // not the calmer amber, regardless of direction.
  let donutState;
  if (pct < 15) donutState = "critical";
  else if (Math.abs(bank.netW) < 5) donutState = "holding";
  else if (bank.netW > 0) donutState = "charging";
  else donutState = "discharging";
  // Flow-direction modifier — drives the leading-edge dot + halo
  // colour independently of the arc colour. Only applied when the
  // bank has >5 W of net flow (the "holding" band keeps its neutral
  // blue head). The visible case this unlocks: critical SoC + net
  // charging shows a red arc with a GREEN pulsing head, telegraphing
  // recovery without losing the "we're low" message on the arc.
  let headFlow = null;
  if (bank.netW > 5)       headFlow = "donut-head-flow-up";
  else if (bank.netW < -5) headFlow = "donut-head-flow-down";
  document.querySelectorAll(".donut-state").forEach(el => {
    el.classList.remove("charging", "discharging", "holding", "critical", "idle",
                        "donut-head-flow-up", "donut-head-flow-down");
    el.classList.add(donutState);
    if (headFlow) el.classList.add(headFlow);
    positionDonutHead(el, pct);
  });
  // The flow pill colors still hook off the legacy charging/discharging/
  // idle classes — keep those applied alongside the new state so existing
  // .donut-state.charging .donut-flow rules still match.
  document.querySelectorAll(".donut-state").forEach(el => el.classList.add(powerState));
  const flowText = $("#donut-flow .donut-flow-text");
  if (flowText) {
    flowText.textContent = powerState === "idle"
      ? "Idle"
      : `${fmt.signed(bank.netW, 0)} W`;
  }
  const kioskFlowText = $("#kiosk-flow-text");
  if (kioskFlowText) {
    kioskFlowText.textContent = powerState === "idle"
      ? "Idle"
      : `${fmt.signed(bank.netW, 0)} W`;
  }

  // Remaining time
  const rem = computeRemaining(bank);
  $("#bank-time").textContent = rem.primary;
  $("#bank-time-sub").textContent = rem.secondary;
  // The forecast-aware line is populated by refreshRuntimeForecast()
  // on its own cadence — render here just keeps the existing values.

  // Source-disagreement hint (#121). Only rendered when both a
  // shunt and one or more BMSes are present AND their SoC readings
  // differ by >5 pp. Single quiet line — not an alarm.
  const dis = $("#donut-disagreement");
  if (dis) {
    if (bank.disagreement) {
      const d = bank.disagreement;
      // Compact one-line format so the hint fits inside the donut on
      // narrow viewports. Active source first (that's what the donut
      // is showing), other source second. The "we picked the shunt
      // because…" explanation lives in the title tooltip.
      const activeLabel = d.showing === "shunt" ? "shunt" : "BMS";
      const otherLabel  = d.showing === "shunt" ? "BMS"   : "shunt";
      const activeSoc   = d.showing === "shunt" ? d.shuntSoc : d.bmsSoc;
      const otherSoc    = d.showing === "shunt" ? d.bmsSoc   : d.shuntSoc;
      dis.textContent = `${activeLabel} ${activeSoc.toFixed(0)}% · ${otherLabel} ${otherSoc.toFixed(0)}%`;
      dis.hidden = false;
      dis.title = `Showing ${activeLabel} reading. Your BMS and shunt ` +
        "disagree by more than 5%. WattPost shows the more reliable " +
        "source for SoC (shunt by default). Override in Settings → " +
        "Power source if you trust one over the other.";
    } else {
      dis.hidden = true;
      dis.textContent = "";
    }
  }

  // Other stats
  $("#bank-voltage").textContent = bank.meanV.toFixed(2);
  $("#bank-capacity").textContent = bank.totalCap.toFixed(0);
  $("#bank-remaining").textContent = bank.totalRem.toFixed(1);
  // Bank meta is a long string (e.g. "3× RBT100LFP12S-G1") — shrink to
  // text style so it fits the small grid cell on mobile.
  const bankMetaTile = $("#bank-meta").closest(".hero-stat-val");
  if (bankMetaTile) bankMetaTile.classList.add("is-text");
  // Two patterns:
  //   * Shunt-only install (#115 "no-BMS mode") — there are no
  //     declared packs, so the count would render "0× SmartShunt 500A"
  //     which reads as broken. Drop the count when packs=0 and just
  //     show the model.
  //   * Standard BMS install — "3× RBT100LFP12S-G1".
  const shortModel = (bank.model || "")
    .replace(/^RBT/, "RBT")
    .replace(/-G\d$/, "");
  $("#bank-meta").textContent = bank.packs > 0
    ? `${bank.packs}× ${shortModel}`
    : shortModel;
}

// ---------- POWER FLOW ----------
function buildFlowModel() {
  // Frame-cache: applySnapshot resets _frame.flowModel every tick.
  // The dashboard renderFlow and the kiosk renderFlow both call us in
  // the same frame; without the cache we'd recompute identical state
  // and risk picking up a different staleness window on the boundary.
  if (_frame.flowModel !== null) return _frame.flowModel;
  const sources = [];
  const loads   = [];
  let batteryNetW = 0;

  for (const dev of devices) {
    const mapping = FLOW_MAPPING[dev.kind];
    if (!mapping) continue;
    const l = dev.latest || {};

    // Silent-device check (#171). Two cases collapse into one
    // "treat as silent" branch:
    //
    //  1. Stale broadcast — Victron BLE drivers stamp
    //     `advertisement_age_s` on every poll; > 60 s means we
    //     haven't decoded a fresh broadcast (charger switched off
    //     at the wall, dongle out of range, etc.).
    //
    //  2. Explicitly idle — the device IS still broadcasting fresh
    //     adverts but the payload reports it's not producing. Most
    //     common on Victron AC chargers in standby: the radio keeps
    //     blasting the LAST V/A readings (e.g. 13.7 V · 15.00 A ·
    //     206 W) forever even though `charging_state == "off"`.
    //     The fresh-advert age would otherwise fool the silent
    //     check and the tile would pretend 206 W is flowing.
    //
    // Both cases: power forced to 0, silent flag set, sub-label
    // explains which kind of silent it is.
    const ageS = (typeof l.advertisement_age_s === "number") ? l.advertisement_age_s : null;
    const isStale = ageS != null && ageS > 60;
    // Generic poll-freshness check — covers vendors that DON'T stamp
    // their own advertisement_age_s (Renogy and JK BMS today). Every
    // /api/devices entry carries `_updated_at` (Unix seconds) of the
    // last successful poll. If that's older than ~90 s, the transport
    // has been failing to reach the device and what we're rendering
    // is a snapshot from however long ago. Without this check, an
    // offline Renogy charge controller silently shows its last-known
    // V/A/W as if they were live — same lie as the old v0.1.15 AC
    // charger bug, just one vendor over.
    const STALE_POLL_SECONDS = 90;
    const updatedAt = +(l._updated_at) || +(dev.last_seen) || null;
    // Frame-stamped "now" so the staleness floor matches what
    // aggregateBank used for the same frame (#162).
    const nowSec = _frame.nowSec || (Date.now() / 1000);
    const pollAgeS = (updatedAt && nowSec > updatedAt) ? (nowSec - updatedAt) : null;
    const isPollStale = pollAgeS != null && pollAgeS > STALE_POLL_SECONDS;
    // Idle-state strings that mean "not actively producing watts".
    // Victron's ChargerState enum: OFF / LOW_POWER / FAULT are all
    // "not charging right now"; everything else (BULK, ABS, FLOAT,
    // STORAGE, EQUALIZE, INVERTING, POWER_SUPPLY) means active.
    const IDLE_CHARGE_STATES = new Set(["off", "low_power", "fault"]);
    const isExplicitlyIdle = (
      typeof l.charging_state === "string"
      && IDLE_CHARGE_STATES.has(l.charging_state.toLowerCase())
    );
    const isSilent = isStale || isExplicitlyIdle || isPollStale;
    const silentSub = (() => {
      if (!isSilent) return null;
      if (isPollStale && !isStale && !isExplicitlyIdle) {
        // No fresh poll for ≥90s. Cover Renogy / JK BMS / any vendor
        // that doesn't stamp advertisement_age_s itself. Wording mirrors
        // the Victron stale messages so the dashboard reads consistently.
        if (pollAgeS < 5400)  return `Stale — last poll ${Math.round(pollAgeS / 60)} min ago`;
        if (pollAgeS < 86000) return `Stale — last poll ${(pollAgeS / 3600).toFixed(1)} h ago`;
        return "Stale — no poll since restart";
      }
      if (isExplicitlyIdle && !isStale) {
        // Fresh broadcast says "I'm off" — be specific rather than
        // pretending we haven't heard from it.
        if (l.charging_state === "off")       return "Off";
        if (l.charging_state === "low_power") return "Standby (low power)";
        if (l.charging_state === "fault")     return "Fault — not producing";
        return "Idle";
      }
      if (ageS < 90)     return `Silent — last heard ${Math.round(ageS)} s ago`;
      if (ageS < 5400)   return `Silent — last heard ${Math.round(ageS / 60)} min ago`;
      if (ageS < 86000)  return `Silent — last heard ${(ageS / 3600).toFixed(1)} h ago`;
      return "Silent — no broadcast since restart";
    })();

    if (mapping.battery) {
      // Smart batteries: sum V × I across packs. Shunts (future): just power_w.
      if (isSilent) {
        // pass — stale battery contributes nothing
      } else if (typeof l.voltage_v === "number" && typeof l.current_a === "number") {
        batteryNetW += l.voltage_v * l.current_a;
      } else if (typeof l.power_w === "number") {
        batteryNetW += l.power_w;
      }
    }
    // Sources: ALWAYS render configured sources, even when they're at 0 W.
    // A user who's set up an MPPT wants to see it on the dashboard at all
    // times — "0 W idle" is informative; hiding the tile makes the system
    // look misconfigured.
    //
    // Solar-pause overlay (#163): when the daemon's solar-pause rule
    // has just told this charger to go off (or is keeping it off), the
    // tile's sub-label says so explicitly instead of the bare "idle".
    // Without this hint the user sees an inverted-by-default charger
    // tile with no obvious reason.
    const acPausedByRule = (() => {
      if (!solarPauseStatus || dev.kind !== "ac_charger") return false;
      const sp = solarPauseStatus;
      if (!sp.charger_on === false) {
        // sp.charger_on is the most recent observed state; we use a
        // pause indicator when the rule's last decision was force_off
        // OR when it explicitly says the charger is currently paused
        // and SoC sits above the recover threshold.
        if (sp.decision === "force_off") return true;
        if (!sp.charger_on && sp.applied !== true && /paused/i.test(sp.reason || "")) return true;
      }
      return false;
    })();
    for (const s of mapping.sources || []) {
      if (s.onlyIf && !s.onlyIf(l)) continue;
      const w = isSilent ? 0 : (+l[s.metric] || 0);
      const subParts = isSilent ? [] : [
        typeof l[s.vMetric] === "number" ? `${l[s.vMetric].toFixed(1)} V` : null,
        typeof l[s.aMetric] === "number" ? `${l[s.aMetric].toFixed(2)} A` : null,
      ].filter(Boolean);
      const subFromState = silentSub
             || (w > 0 ? subParts.join(" · ")
                       : (subParts.length ? `${subParts.join(" · ")} · idle` : "idle"));
      sources.push({
        id: `${dev.label}.${s.id}`,
        label: s.label,
        device: dev.label,
        color: s.color,
        icon: s.icon || COLOR_TO_ICON[s.color],
        power: w,
        active: w > 1,
        silent: isSilent,
        // The solar-pause overlay only overrides the sub-label when
        // the charger is genuinely off (silent / idle). If the
        // charger is actively producing despite the rule saying
        // "force_off", keep showing the real V/A so the user can
        // tell the write hasn't landed yet.
        sub: (acPausedByRule && (isSilent || w <= 1))
             ? "Paused · solar covering"
             : subFromState,
      });
    }
    // Loads: keep the onlyIf filter — the Rover's load output really is
    // off for most users, and showing "DC Load: 0 W idle" would be clutter,
    // not signal. Inferred bus-loads are still surfaced separately below.
    for (const lo of mapping.loads || []) {
      if (lo.onlyIf && !lo.onlyIf(l)) continue;
      const w = isSilent ? 0 : (+l[lo.metric] || 0);
      if (w <= 0 && !lo.onlyIf && !isSilent) continue;
      loads.push({
        id: `${dev.label}.${lo.id}`,
        label: lo.label,
        device: dev.label,
        color: lo.color,
        icon: lo.icon || COLOR_TO_ICON[lo.color],
        power: w,
        active: w > 1,
        silent: isSilent,
        sub: [
          typeof l[lo.vMetric] === "number" ? `${l[lo.vMetric].toFixed(1)} V` : null,
          typeof l[lo.aMetric] === "number" ? `${l[lo.aMetric].toFixed(2)} A` : null,
        ].filter(Boolean).join(" · "),
      });
    }
  }

  const bank = aggregateBank();

  // Energy-balance inference: anything the bank is gaining/losing that we
  // can't account for from visible sources/loads is an unmeasured load
  // (heater wired to a busbar, fridge on a separate fuse, etc.) or an
  // unmeasured source (the Victron charger we don't have a driver for).
  //
  //   bank.netW = sources_in - loads_out
  //   loads_out = visible_loads + inferred
  //   ⇒ inferred = sources_in - bank.netW - visible_loads
  //
  // Positive  ⇒ an unmeasured LOAD of that magnitude.
  // Negative  ⇒ an unmeasured SOURCE of that magnitude.
  // Noise floor: a ±1 W imbalance is essentially sampling skew between
  // the MPPT poll and the battery poll, plus integer rounding. Anything
  // above this is genuine system load (or an unmeasured source).
  //
  // We always surface the inferred figure now, not just above some 10 W
  // threshold — otherwise the dashboard reads as "PV 71 W → battery 66 W →
  // load 0 W" and users (correctly) think the maths is broken. Showing
  // the small 5 W difference as load — with an "estimated" sub-label —
  // makes the totals reconcile and tells the truth: load is computed
  // from energy balance unless a real load meter is wired in.
  const INFERRED_NOISE_W = 1;
  if (bank) {
    const visibleSourcesW = sources.reduce((a, s) => a + s.power, 0);
    const visibleLoadsW   = loads.reduce((a, l) => a + l.power, 0);
    const inferred = visibleSourcesW - bank.netW - visibleLoadsW;
    const visibleLoadsActive = loads.length > 0;

    if (inferred > INFERRED_NOISE_W) {
      loads.push({
        id: visibleLoadsActive ? "_bus_load" : "_load",
        label: visibleLoadsActive ? "Bus loads" : "Load",
        color: "dc",
        icon: "house",
        power: inferred,
        active: inferred > 50,
        // Only flag as "inferred" (dashed border + asterisk) when it's
        // a secondary tile alongside something we measured directly.
        inferred: visibleLoadsActive,
        sub: "estimated",
      });
    } else if (inferred < -INFERRED_NOISE_W) {
      // Before falling back to a phantom "Other source" tile,
      // check whether we have exactly one silent source already.
      // That's the common Victron BLE case: the charger went
      // quiet on BLE but is still physically pushing watts that
      // the bank is measuring.  Attributing the gap back to the
      // silent device is more honest than rendering a separate
      // "Other source" alongside — the user sees one tile, knows
      // why it's marked estimated, and the maths still balances.
      const silentSources = sources.filter((s) => s.silent && (+s.power || 0) === 0);
      if (silentSources.length === 1) {
        const silent = silentSources[0];
        silent.power    = -inferred;
        silent.active   = -inferred > 50;
        silent.inferred = true;  // dashed border + asterisk
        // Keep the "Silent — last heard …" sub-label so the user
        // still sees why the value is estimated rather than measured.
        silent.sub = silent.sub
          ? `${silent.sub} · estimated from bank`
          : "estimated from bank";
      } else {
        const visibleSourcesActive = sources.length > 0;
        sources.push({
          id: visibleSourcesActive ? "_bus_source" : "_source",
          label: visibleSourcesActive ? "Other source" : "Source",
          color: "grid",
          icon: "feed",
          power: -inferred,
          active: -inferred > 50,
          inferred: visibleSourcesActive,
          sub: "estimated",
        });
      }
    } else if (!visibleLoadsActive) {
      // True idle: |inferred| ≤ noise floor and nothing measured.
      // Placeholder tile keeps the strip symmetric with the always-
      // visible Sources column.
      loads.push({
        id: "_load_idle",
        label: "Load",
        color: "dc",
        icon: "house",
        power: 0,
        active: false,
        sub: "estimated · idle",
      });
    }
  }

  const model = { sources, loads, batteryNetW, bank };
  _frame.flowModel = model;
  return model;
}

function renderFlow(targetHost) {
  // v2 Power-flow tile: SVG with animated particles along curves,
  // icon-only nodes at the perimeter, watts as labels *outside* the
  // nodes, and a separate battery card below the diagram. Modelled
  // on the Powerwall-Dashboard / Tesla-app aesthetic. The kiosk view
  // passes its own host so we can mount a second copy inside the
  // kiosk layout — the SVG scales with the host width.
  const host = targetHost || $("#flow");
  const sub  = host === $("#flow") ? $("#flow-sub") : null;
  const cap  = host === $("#flow") ? document.getElementById("flow-cap") : null;
  host.innerHTML = "";
  host.classList.remove("flow--idle");

  const model = buildFlowModel();
  if (!model.bank && model.sources.length === 0 && model.loads.length === 0) {
    host.innerHTML = `<div class="flow-empty">No active devices yet.</div>`;
    if (sub) sub.textContent = "";
    if (cap) cap.textContent = "";
    return;
  }

  const totalSourceW = model.sources.reduce((a, s) => a + s.power, 0);
  const totalLoadW   = model.loads.reduce((a, l) => a + l.power, 0);
  const hasSources = model.sources.length > 0;
  const hasLoads   = model.loads.length > 0;
  const battNetW   = Math.round(model.batteryNetW || 0);

  // Show the battery as a node in the SVG when its flow is
  // meaningful (≥10 W). Otherwise it lives only in the card below —
  // keeps the diagram uncluttered for the common float / resting
  // case. Card is always present when bank exists.
  const showBattInSvg = !!model.bank && Math.abs(battNetW) >= 10;

  // Battery-only topology — no sources, no loads (e.g. bank shunt + dumb packs).
  if (!hasSources && !hasLoads) {
    host.classList.add("flow--idle");
    if (model.bank) host.appendChild(buildBatCard(model.bank, battNetW));
    if (sub) sub.textContent = "no sources or loads configured";
    if (cap) cap.textContent = "";
    return;
  }

  host.appendChild(buildFlowSvgV2(model, { showBattInSvg, battNetW }));
  if (model.bank) host.appendChild(buildBatCard(model.bank, battNetW));

  const parts = [];
  if (hasSources) parts.push(`${model.sources.length} source${model.sources.length === 1 ? "" : "s"} · ${totalSourceW.toFixed(0)} W in`);
  if (hasLoads)   parts.push(`${model.loads.length} load${model.loads.length === 1 ? "" : "s"} · ${totalLoadW.toFixed(0)} W out`);
  if (sub) sub.textContent = parts.join(" · ") || "system idle";
  if (cap) cap.textContent = flowCaption(model, totalSourceW, totalLoadW);
}

// ───────── v2 SVG flow diagram builders ─────────
//
// Layout: 400×260 viewBox. Sources arc across the top, the bus is a
// notional point at (200, 145), loads (and the battery node when
// shown) arc across the bottom. Smooth bezier connectors between
// each node and the bus. Particles travel along active paths via
// SMIL <animateMotion>; speed scales with W magnitude so heavy
// flows visibly stream faster.

const SVG_NS = "http://www.w3.org/2000/svg";
const FLOW_W = 400, FLOW_H = 260;
const FLOW_BUS_X = FLOW_W / 2, FLOW_BUS_Y = 145;
const FLOW_SRC_Y = 60, FLOW_BOT_Y = 220;

function buildFlowSvgV2(model, opts) {
  const { showBattInSvg, battNetW } = opts;

  // Position sources across the top, loads (+ battery when shown)
  // across the bottom. When the battery node is in the SVG it takes
  // the left slot and loads pack to the right.
  const srcPos = positionFlowNodes(model.sources.length, FLOW_SRC_Y, [70, FLOW_W - 70]);
  let loadPos, batPos = null;
  if (showBattInSvg) {
    batPos  = [80, FLOW_BOT_Y];
    loadPos = positionFlowNodes(model.loads.length, FLOW_BOT_Y, [180, FLOW_W - 70]);
  } else {
    loadPos = positionFlowNodes(model.loads.length, FLOW_BOT_Y, [70, FLOW_W - 70]);
  }

  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "flow-svg-v2");
  svg.setAttribute("viewBox", `0 0 ${FLOW_W} ${FLOW_H}`);
  svg.setAttribute("aria-hidden", "true");

  // Stable, scoped path IDs so multiple flow tiles (dashboard +
  // kiosk) don't collide their mpath references.
  const idPrefix = `fv2-${++FLOW_PATH_SEQ}`;
  const activePaths = [];

  // Source → bus (each source).
  model.sources.forEach((s, i) => {
    const [x, y] = srcPos[i];
    const pathId = `${idPrefix}-s${i}`;
    const active = (s.power || 0) >= 1 && !s.silent;
    addFlowPath(svg, pathId, x, y + 22, FLOW_BUS_X, FLOW_BUS_Y, active);
    if (active) activePaths.push({ id: pathId, color: colorKeyOf(s) || "pv", w: s.power });
  });

  // Battery <-> bus when shown. Direction encodes charge state.
  if (showBattInSvg) {
    const [bx, by] = batPos;
    const charging = battNetW > 0;
    const pathId = `${idPrefix}-bat`;
    const x1 = charging ? FLOW_BUS_X : bx;
    const y1 = charging ? FLOW_BUS_Y : by - 22;
    const x2 = charging ? bx : FLOW_BUS_X;
    const y2 = charging ? by - 22 : FLOW_BUS_Y;
    addFlowPath(svg, pathId, x1, y1, x2, y2, true);
    activePaths.push({ id: pathId, color: charging ? "batt" : "discharge", w: Math.abs(battNetW) });
  }

  // Bus → load (each load).
  model.loads.forEach((l, i) => {
    const [x, y] = loadPos[i];
    const pathId = `${idPrefix}-l${i}`;
    const active = (l.power || 0) >= 1;
    addFlowPath(svg, pathId, FLOW_BUS_X, FLOW_BUS_Y, x, y - 22, active);
    if (active) activePaths.push({ id: pathId, color: colorKeyOf(l) || "load", w: l.power });
  });

  // Particles on each active path. Two per path, half-phase offset.
  activePaths.forEach(p => addFlowParticles(svg, p.id, p.color, p.w));

  // Nodes (rendered last so they sit on top of path ends).
  model.sources.forEach((s, i) => addFlowNode(svg, srcPos[i][0], srcPos[i][1], s, true));
  if (showBattInSvg) {
    const [bx, by] = batPos;
    addFlowNode(svg, bx, by, {
      label: "Battery",
      color: battNetW > 0 ? "batt" : "discharge",
      icon: "battery",
      power: Math.abs(battNetW),
    }, false);
  }
  model.loads.forEach((l, i) => addFlowNode(svg, loadPos[i][0], loadPos[i][1], l, false));

  return svg;
}

let FLOW_PATH_SEQ = 0;

function positionFlowNodes(n, y, [xMin, xMax]) {
  if (n === 0) return [];
  if (n === 1) return [[(xMin + xMax) / 2, y]];
  if (n === 2) return [[xMin + 30, y], [xMax - 30, y]];
  const step = (xMax - xMin) / (n - 1);
  return Array.from({length: n}, (_, i) => [xMin + i * step, y]);
}

function addFlowPath(svg, id, x1, y1, x2, y2, active) {
  // Smooth bezier between two points. Control points are vertically
  // stretched so the curve enters/exits the nodes pointing up/down
  // — keeps source-to-bus arcs feeling like a real wiring diagram.
  const midY = (y1 + y2) / 2;
  const d = `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`;
  const el = document.createElementNS(SVG_NS, "path");
  el.setAttribute("id", id);
  el.setAttribute("d", d);
  el.setAttribute("class", `flow-conn-v2${active ? " active" : ""}`);
  svg.appendChild(el);
}

function addFlowParticles(svg, pathId, colorKey, w) {
  // Two particles, half-phase offset. Period scales with W so heavy
  // flows visibly stream faster — capped so even kW doesn't blur.
  const dur = Math.max(0.5, Math.min(2.2, 2.0 - Math.log10(Math.max(1, w)) * 0.4));
  for (let i = 0; i < 2; i++) {
    const dot = document.createElementNS(SVG_NS, "circle");
    dot.setAttribute("r", "3");
    dot.setAttribute("class", `flow-particle-v2 ${colorKey}`);
    const motion = document.createElementNS(SVG_NS, "animateMotion");
    motion.setAttribute("dur", `${dur.toFixed(2)}s`);
    motion.setAttribute("repeatCount", "indefinite");
    motion.setAttribute("begin", `${(i * dur / 2).toFixed(2)}s`);
    const mpath = document.createElementNS(SVG_NS, "mpath");
    mpath.setAttributeNS("http://www.w3.org/1999/xlink", "href", `#${pathId}`);
    motion.appendChild(mpath);
    dot.appendChild(motion);
    svg.appendChild(dot);
  }
}

function addFlowNode(svg, x, y, t, isSource) {
  const color = colorKeyOf(t);
  const silent = t.silent && (t.power || 0) < 1;
  const tone = silent ? "silent" : color;

  // Watt label — above for sources, below for loads/battery.
  const lbl = document.createElementNS(SVG_NS, "text");
  lbl.setAttribute("x", x);
  lbl.setAttribute("y", isSource ? y - 36 : y + 40);
  lbl.setAttribute("class", `flow-node-w ${silent ? "silent" : ""}`);
  lbl.setAttribute("text-anchor", "middle");
  lbl.textContent = `${Math.round(t.power || 0)} W`;
  svg.appendChild(lbl);

  // Ring (single stroked circle).
  const ring = document.createElementNS(SVG_NS, "circle");
  ring.setAttribute("cx", x);
  ring.setAttribute("cy", y);
  ring.setAttribute("r", "22");
  ring.setAttribute("class", `flow-node-ring ${tone}`);
  svg.appendChild(ring);

  // Icon (foreignObject with the inline SVG so we don't rebuild the
  // path data here — ICONS is the single source of truth).
  const iconKey = t.icon || COLOR_TO_ICON[color] || "unknown";
  const iconSvg = ICONS[iconKey] || ICONS.unknown;
  const g = document.createElementNS(SVG_NS, "g");
  g.setAttribute("transform", `translate(${x - 11} ${y - 11})`);
  g.setAttribute("class", `flow-node-icon ${tone}`);
  // Inline the icon's inner content scaled to 22×22. Strip the wrapper
  // <svg> since we're nesting; the inner paths get our stroke colour
  // via CSS `currentColor`.
  const inner = iconSvg.replace(/<\/?svg[^>]*>/g, "");
  const innerSvg = document.createElementNS(SVG_NS, "svg");
  innerSvg.setAttribute("viewBox", "0 0 24 24");
  innerSvg.setAttribute("width", "22");
  innerSvg.setAttribute("height", "22");
  innerSvg.innerHTML = inner;
  g.appendChild(innerSvg);
  svg.appendChild(g);
}

function colorKeyOf(t) {
  // Map flow-model colour tag to a tone class shared with CSS.
  const c = (t && t.color) || "";
  if (c === "pv" || c === "batt" || c === "load" || c === "grid" || c === "ac" || c === "dc") return c;
  return "neutral";
}

function buildBatCard(bank, battNetW) {
  // Battery card lives below the SVG. Slim, Tesla-app style:
  // icon-circle on the left, big SoC + state label in the middle,
  // signed flow on the right, thin horizontal fill bar underneath.
  const soc   = Math.max(0, Math.min(100, bank.soc || 0));
  const aNetW = Math.abs(battNetW);

  // State drives the icon-ring + fill colours. "Full" wins over a
  // tiny float trickle so users don't see amber discharging at 100 %.
  let state, tone;
  if (soc < 15 && battNetW > 0)      { state = "Low · Charging"; tone = "red"; }
  else if (soc < 15)                 { state = "Low";            tone = "red"; }
  else if (soc >= 98)                { state = "Full";           tone = "blue"; }
  else if (aNetW < 5)                { state = "Resting";        tone = "blue"; }
  else if (battNetW > 0)             { state = "Charging";       tone = "green"; }
  else                               { state = "Discharging";    tone = "discharge"; }

  let flowText, flowClass;
  if (aNetW < 1)         { flowText = "Idle";                                              flowClass = ""; }
  else if (battNetW > 0) { flowText = `↓ ${aNetW} W in`;                                   flowClass = "charging"; }
  else                   { flowText = `↑ ${aNetW} W out`;                                  flowClass = "discharging"; }

  const subParts = [];
  if (typeof bank.meanV === "number") subParts.push(`${bank.meanV.toFixed(2)} V`);
  if (typeof bank.sumI  === "number" && Math.abs(bank.sumI) >= 0.1) {
    subParts.push(`${bank.sumI >= 0 ? "+" : ""}${bank.sumI.toFixed(1)} A`);
  }

  const card = document.createElement("div");
  card.className = "bat-card";
  card.innerHTML = `
    <div class="bat-icon-wrap ${tone}">${ICONS.battery}</div>
    <div class="bat-headline">
      <div class="bat-soc">${soc.toFixed(0)}<span class="bat-soc-unit">%</span></div>
      <div class="bat-state">${state}</div>
    </div>
    <div class="bat-power ${flowClass}">
      <div>${flowText}</div>
      <div class="bat-power-sub">${subParts.join(" · ")}</div>
    </div>
    <div class="bat-fill"><div class="bat-fill-inner ${tone}" style="width:${soc.toFixed(0)}%"></div></div>
  `;
  return card;
}

function flowCaption(model, totalSourceW, totalLoadW) {
  if (!model.bank) return "";
  const battW = Math.round(model.batteryNetW || 0);
  const sourcesActive = totalSourceW >= 1;
  const loadsActive   = totalLoadW   >= 1;
  const soc = model.bank.soc || 0;
  // "Effectively full" — MPPTs go into float around 98-100%. We use
  // 98 so we catch the "100.0 % shown, 99.6 actual" case too.
  const bankFull = soc >= 98;

  // Battery is meaningfully charging (sources > loads, excess into bank).
  if (battW >= 1) {
    if (sourcesActive && loadsActive) return `Sources covering load · charging battery at ${battW} W`;
    if (sourcesActive)                return `Charging battery at ${battW} W`;
    return "";
  }

  // Bank is full and the MPPT is loafing — pulling just enough sun
  // to cover the load (and a few W of bus maintenance). This is the
  // most common "wait, why is solar only 94 W when it's sunny?"
  // case, so we call it out before the generic discharge text.
  // Heuristic: bank full + sources active + the bank itself isn't
  // doing much (|netW| < 50 W). If the load suddenly spikes the
  // MPPT ramps up and we fall through to the normal captions below.
  if (bankFull && sourcesActive && Math.abs(battW) < 50) {
    if (loadsActive) return `Battery full · solar throttled to load demand (${Math.round(totalSourceW)} W, panels not maxed)`;
    return `Battery full · solar throttled (no demand)`;
  }

  // Battery is meaningfully discharging (sources < loads, bank fills gap).
  if (battW <= -1) {
    const out = Math.abs(battW);
    if (sourcesActive && loadsActive) return `Load is ${out} W more than sources · battery making up the difference`;
    if (loadsActive)                  return `Running off battery · ${out} W to load`;
    return `Battery draining at ${out} W`;
  }

  // Battery at rest.
  if (sourcesActive && loadsActive) return `Sources matched to load · battery resting`;
  if (sourcesActive)                return `Sources idle to a full battery · no load drawing`;
  if (loadsActive)                  return `Running off battery · no draw beyond idle`;
  return `System idle`;
}

function makeFlowDonut(bank, batteryNetW) {
  // Battery donut for the Power-flow centerpiece. Reuses the SoC
  // palette + gradient defs from the Hero donut (url(#g-soc-…)) so
  // colour semantics match across the dashboard. The donut is the
  // single visual anchor; sources and loads stay as flanking tiles.
  const soc   = Math.max(0, Math.min(100, bank.soc || 0));
  const netW  = Math.round(batteryNetW || 0);
  const aNetW = Math.abs(netW);
  const meanV = typeof bank.meanV === "number" ? bank.meanV : null;
  const sumI  = typeof bank.sumI  === "number" ? bank.sumI  : null;

  // State drives colour. Treat ≥98% SoC as "Full" regardless of small
  // discharge noise — at full SoC a tiny −5 W trickle is the MPPT in
  // float, not a real discharge alert.
  let state, label;
  if (soc < 15)            { state = "critical";    label = "Low"; }
  else if (soc >= 98)      { state = "holding";     label = "Full"; }
  else if (aNetW < 5)      { state = "holding";     label = "Resting"; }
  else if (netW > 0)       { state = "charging";    label = "Charging"; }
  else                     { state = "discharging"; label = "Discharging"; }

  // Direction line inside the donut. ↓ = energy flowing INTO the
  // battery (charging), ↑ = energy LEAVING the battery (discharging).
  // We never label this in bus terms — it's strictly battery-relative.
  let flowHTML = "";
  if (aNetW >= 1) {
    const arrow = netW > 0 ? "↓" : "↑";
    const dir   = netW > 0 ? "in" : "out";
    flowHTML = `<div class="flow-donut-flow"><span class="flow-donut-arrow">${arrow}</span><span>${aNetW} W ${dir}</span></div>`;
  }

  // Sub-line: bus voltage (always useful), and shunt amperage when
  // there's enough current to be meaningful.
  const subParts = [];
  if (meanV != null) subParts.push(`${meanV.toFixed(2)} V`);
  if (sumI  != null && Math.abs(sumI) >= 0.1) {
    subParts.push(`${sumI >= 0 ? "+" : ""}${sumI.toFixed(1)} A`);
  }
  const subHTML = subParts.length
    ? `<div class="flow-donut-flow" style="margin-top:.25rem;font-size:.72rem;color:var(--text-2);">${subParts.join(" · ")}</div>`
    : "";

  const wrap = document.createElement("div");
  wrap.className = `flow-donut-wrap donut-state ${state}`;
  wrap.setAttribute("role", "img");
  wrap.setAttribute("aria-label", `Battery ${soc.toFixed(0)} percent, ${label.toLowerCase()}${aNetW >= 1 ? `, ${aNetW} watts ${netW > 0 ? "in" : "out"}` : ""}`);
  wrap.innerHTML = `
    <svg class="flow-donut" viewBox="0 0 200 200" aria-hidden="true">
      <circle class="donut-track" cx="100" cy="100" r="86" fill="none" stroke-width="14"/>
      <circle class="donut-arc"   cx="100" cy="100" r="86" fill="none" stroke-width="14"
              pathLength="100" stroke-dasharray="${soc.toFixed(1)} ${(100 - soc).toFixed(1)}"
              stroke-linecap="round" transform="rotate(-90 100 100)"/>
    </svg>
    <div class="flow-donut-center">
      <div class="flow-donut-pct"><span>${soc.toFixed(0)}</span><span class="flow-donut-pct-unit">%</span></div>
      <div class="flow-donut-state">${label}</div>
      ${flowHTML}
      ${subHTML}
    </div>
  `;
  return wrap;
}

function makeFlowTile(t, muted = false) {
  const div = document.createElement("div");
  const classes = [`flow-tile`, t.color || "neutral"];
  if (muted) classes.push("muted");
  if (t.active) classes.push("is-active");
  if (t.inferred) classes.push("inferred");
  if (t.silent) classes.push("is-silent");
  div.className = classes.join(" ");

  const iconKey = t.icon || COLOR_TO_ICON[t.color] || "unknown";
  const iconSvg = ICONS[iconKey] || ICONS.unknown;

  // Watermark background icon (large, faded, behind text)
  const bg = document.createElement("div");
  bg.className = "flow-tile-bg-icon";
  bg.innerHTML = iconSvg;
  div.appendChild(bg);

  const head = document.createElement("div");
  head.className = "flow-tile-head";
  const headIcon = document.createElement("span");
  headIcon.className = "flow-tile-icon";
  headIcon.innerHTML = iconSvg;
  const headLabel = document.createElement("span");
  headLabel.textContent = t.label;
  head.append(headIcon, headLabel);

  const val = document.createElement("div");
  // Signed values (battery net W) shift color with direction so the tile
  // matches the donut's amber-when-discharging / green-when-charging cue.
  let valClass = "flow-tile-val";
  if (t.signed) {
    if (t.power > 1) valClass += " power-charging";
    else if (t.power < -1) valClass += " power-discharging";
  }
  val.className = valClass;
  const powerText = t.signed ? fmt.signed(t.power, 0) : fmt.num(t.power, 0);
  val.innerHTML = `<span>${powerText}</span><span class="unit">W</span>`;

  div.append(head, val);
  if (t.sub) {
    const sub = document.createElement("div");
    sub.className = "flow-tile-sub";
    sub.textContent = t.sub;
    div.appendChild(sub);
  }
  return div;
}

function makeConnector(c) {
  const wrap = document.createElement("div");
  wrap.className = "flow-conn";
  const arrow = document.createElement("div");
  arrow.className = "flow-arrow" + (c.active ? " active" : "");
  arrow.style.setProperty("--from", `var(--${c.fromColor})`);
  arrow.style.setProperty("--to",   `var(--${c.toColor})`);
  if (c.active) {
    const dot = document.createElement("div");
    dot.className = "dot";
    arrow.appendChild(dot);
  }
  const label = document.createElement("div");
  label.className = "flow-arrow-label";
  label.textContent = c.label;
  wrap.append(arrow, label);
  return wrap;
}

// ---------- TOMORROW STRIP (PV forecast) ----------
//
// Forecast lives in /api/forecast/pv; we fetch on dashboard load and
// every ~5 min thereafter (poll cadence is hours, no point refreshing
// faster). When no forecast is configured or the cache is empty, the
// panel hides itself entirely so the dashboard isn't cluttered.

let forecastData = null;
let forecastLastFetched = 0;
const FORECAST_REFRESH_MS = 5 * 60 * 1000;

async function ensureForecast(force = false) {
  const now = Date.now();
  if (!force && forecastData && (now - forecastLastFetched) < FORECAST_REFRESH_MS) {
    return forecastData;
  }
  try {
    const f = await api("/api/forecast/pv");
    forecastData = (f?.points?.length) ? f : null;
    forecastLastFetched = now;
  } catch (e) { forecastData = null; }
  return forecastData;
}

// Group forecast points into per-day buckets keyed by midnight epoch
// (local time). Each bucket gets { wh: total energy, peak: {ts, w},
// points: [...] }. Used by the 7-day outlook strip.
function bucketByDay(points) {
  const buckets = new Map();   // dayMid -> bucket
  for (const p of points || []) {
    const d = new Date((p.ts - 900) * 1000);   // slice mid-point
    d.setHours(0, 0, 0, 0);
    const dayMid = Math.floor(d.getTime() / 1000);
    let b = buckets.get(dayMid);
    if (!b) { b = { dayMid, wh: 0, peak: null, points: [] };
              buckets.set(dayMid, b); }
    const w = p.pv_w || 0;
    b.wh += w * 0.5;
    b.points.push({ ts: p.ts, w });
    if (!b.peak || w > b.peak.w) b.peak = { ts: p.ts, w };
  }
  return [...buckets.values()].sort((a, b) => a.dayMid - b.dayMid);
}

// Bucket forecast points by local calendar day; returns
// { todayWh, tomorrowWh, dayAfterWh, tomorrowPeak: {ts, w} | null,
//   tomorrowPoints: [{ts, w}], dayAfterPoints: [...] }
function summariseForecast(points) {
  const out = {
    todayWh: 0, tomorrowWh: 0, dayAfterWh: 0,
    todayPoints: [], todayPeak: null, todayRemainingWh: 0,
    tomorrowPeak: null, tomorrowPoints: [], dayAfterPoints: [],
  };
  if (!points?.length) return out;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const todayMid = today.getTime() / 1000;
  const dayS = 86400;
  const nowS = Math.floor(Date.now() / 1000);
  // Solcast's `period_end` is the END of a 30-min slice, so the
  // slice that ends at exactly 00:00 tomorrow is actually 23:30 of
  // *today*. Bucket by the slice mid-point (ts - 900s) to keep the
  // attribution honest at day boundaries.
  for (const p of points) {
    const ts = p.ts;
    const w = p.pv_w || 0;
    const wh = w * 0.5;
    const bucketTs = ts - 900;
    if (bucketTs >= todayMid && bucketTs < todayMid + dayS) {
      out.todayWh += wh;
      out.todayPoints.push({ ts, w });
      if (!out.todayPeak || w > out.todayPeak.w) out.todayPeak = { ts, w };
      if (ts >= nowS) out.todayRemainingWh += wh;
    } else if (bucketTs >= todayMid + dayS && bucketTs < todayMid + 2*dayS) {
      out.tomorrowWh += wh;
      out.tomorrowPoints.push({ ts, w });
      if (!out.tomorrowPeak || w > out.tomorrowPeak.w) {
        out.tomorrowPeak = { ts, w };
      }
    } else if (bucketTs >= todayMid + 2*dayS && bucketTs < todayMid + 3*dayS) {
      out.dayAfterWh += wh;
      out.dayAfterPoints.push({ ts, w });
    }
  }
  return out;
}

// ---------- CURRENT WEATHER (Open-Meteo) ----------
let weatherData = null;
let weatherLastFetched = 0;
const WEATHER_REFRESH_MS = 5 * 60 * 1000;

async function ensureWeather(force = false) {
  const now = Date.now();
  if (!force && weatherData && (now - weatherLastFetched) < WEATHER_REFRESH_MS) {
    return weatherData;
  }
  try {
    const w = await api("/api/weather/current");
    weatherData = (w && w.provider) ? w : null;
    weatherLastFetched = now;
  } catch (e) { weatherData = null; }
  return weatherData;
}

// WMO weather code → human label + minimal SVG icon. We pick from a
// small palette (clear, partly cloudy, cloud, fog, rain, snow,
// thunder) rather than the full WMO ladder — anything more fine-
// grained than that doesn't read at this size.
const WMO = {
  describe(code, isDay) {
    if (code == null) return "—";
    if (code === 0)                              return isDay === false ? "Clear night" : "Sunny";
    if (code === 1)                              return "Mostly clear";
    if (code === 2)                              return "Partly cloudy";
    if (code === 3)                              return "Overcast";
    if (code === 45 || code === 48)              return "Fog";
    if (code >= 51 && code <= 57)                return "Drizzle";
    if (code >= 61 && code <= 67)                return "Rain";
    if (code >= 71 && code <= 77)                return "Snow";
    if (code >= 80 && code <= 82)                return "Showers";
    if (code === 85 || code === 86)              return "Snow showers";
    if (code >= 95 && code <= 99)                return "Thunderstorm";
    return "—";
  },
  iconSvg(code, isDay) {
    if (code == null) return "";
    // Big-friendly inline icons. Stroke uses currentColor so the
    // panel theme controls tint.
    const sun = `<circle cx="12" cy="12" r="4"/>
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>`;
    const moon = `<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>`;
    const cloud = `<path d="M17 18a4 4 0 0 0 0-8 6 6 0 0 0-11.7 1.8A3.5 3.5 0 0 0 6.5 18z"/>`;
    const partly = `<path d="M16 17a4 4 0 0 0 0-8 6 6 0 0 0-11.5 1.8A3.5 3.5 0 0 0 5.5 17z"/>
      <circle cx="17" cy="6" r="2.4"/>
      <path d="M17 1.5v1.5M22 6h-1.5M19.5 2.5l-1.05 1.05"/>`;
    const rain = cloud + `<path d="M9 21l1-2M13 21l1-2M17 21l1-2"/>`;
    const snow = cloud + `<path d="M10 21l.5-1M14 21l.5-1M18 21l.5-1"/>`;
    const fog  = cloud + `<path d="M4 21h16M6 19h12"/>`;
    const storm = cloud + `<path d="M11 18l-2 3h3l-2 3"/>`;
    let inner;
    if (code === 0)                              inner = isDay === false ? moon : sun;
    else if (code === 1)                         inner = partly;
    else if (code === 2)                         inner = partly;
    else if (code === 3)                         inner = cloud;
    else if (code === 45 || code === 48)         inner = fog;
    else if (code >= 51 && code <= 67)           inner = rain;
    else if (code >= 71 && code <= 77)           inner = snow;
    else if (code >= 80 && code <= 86)           inner = rain;
    else if (code >= 95 && code <= 99)           inner = storm;
    else                                         inner = cloud;
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
  },
};

// Map a WMO weather_code + is_day flag to a panel-tint class.
// Kept narrow — six daytime moods + clear-night + partly-night.
// Everything else falls back to a neutral "cloudy" tint rather
// than guess; the tile is meant to whisper, not shout.
function weatherTintClass(code, isDay) {
  if (code == null) return "wx-bg-cloudy";
  if (code === 0) return isDay === false ? "wx-bg-clear-night" : "wx-bg-clear-day";
  if (code === 1 || code === 2) return isDay === false ? "wx-bg-partly-night" : "wx-bg-partly-day";
  if (code === 3) return "wx-bg-cloudy";
  if (code === 45 || code === 48) return "wx-bg-fog";
  if (code >= 51 && code <= 57) return "wx-bg-drizzle";
  if ((code >= 61 && code <= 67) || (code >= 80 && code <= 82)) return "wx-bg-rain";
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return "wx-bg-snow";
  if (code >= 95) return "wx-bg-thunder";
  return "wx-bg-cloudy";
}

// #264 — "Where you are" location tile. Pulled from /api/location/status
// (LOCAL view, never gated by share-with-cloud). Cached + only re-painted
// when coordinates actually move so we don't churn Leaflet on every dashboard
// poll. Leaflet is defer-loaded; if it's not ready when this runs we just
// retry on the next frame.
let _locTileCache = null;     // last rendered {lat, lon, source}
let _locTileMap = null;
let _locTileMarker = null;
let _locTileFetching = false;
async function renderLocationTile() {
  const panel = document.getElementById("location-panel");
  if (!panel) return;
  if (_locTileFetching) return;       // single-flight; next dashboard poll
  _locTileFetching = true;
  let s;
  try { s = await api("/api/location/status"); }
  catch (_) { _locTileFetching = false; return; }
  _locTileFetching = false;
  const loc = s && s.current;
  if (!loc || loc.lat == null || loc.lon == null) {
    panel.hidden = true;
    return;
  }
  if (typeof L === "undefined") {
    // Leaflet still loading; try again on next dashboard frame.
    setTimeout(() => renderLocationTile(), 500);
    return;
  }
  panel.hidden = false;
  const sub = document.getElementById("location-sub");
  if (sub) {
    const src = loc.source === "gps" ? "GPS" : "static";
    const age = loc.fix_age_s != null ? ` · fix ${Math.round(loc.fix_age_s)}s` : "";
    sub.textContent = `${loc.lat.toFixed(4)}, ${loc.lon.toFixed(4)} (${src}${age})`;
  }
  // Skip the heavy re-paint if coords are basically unchanged. <0.0001°
  // (~11m) is well within GPS jitter, no point re-fitting the map.
  const moved = !_locTileCache
              || Math.abs(_locTileCache.lat - loc.lat) > 0.0001
              || Math.abs(_locTileCache.lon - loc.lon) > 0.0001;
  if (_locTileMap === null) {
    _locTileMap = L.map("location-map", {
      zoomControl: true, scrollWheelZoom: false, attributionControl: true,
    });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
    }).addTo(_locTileMap);
  }
  if (moved) {
    _locTileMap.setView([loc.lat, loc.lon], 13);
    if (_locTileMarker) _locTileMap.removeLayer(_locTileMarker);
    _locTileMarker = L.marker([loc.lat, loc.lon]).addTo(_locTileMap);
    _locTileCache = { lat: loc.lat, lon: loc.lon, source: loc.source };
  }
  // Leaflet needs a size-invalidate after the panel unhides, otherwise
  // tiles render as a grey square until the user resizes.
  setTimeout(() => { try { _locTileMap.invalidateSize(); } catch (_) {} }, 100);
}

function renderWeather() {
  const panel = $("#weather-panel");
  if (!panel) return;
  ensureWeather().then(w => {
    if (!w) { panel.hidden = true; return; }
    panel.hidden = false;
    // Apply the weather-aware tint as a class so CSS handles the
    // gradient transition (no inline-style churn each refresh).
    panel.classList.forEach((c) => {
      if (c.startsWith("wx-bg-")) panel.classList.remove(c);
    });
    panel.classList.add(weatherTintClass(w.weather_code, w.is_day));
    $("#weather-icon").innerHTML = WMO.iconSvg(w.weather_code, w.is_day);
    $("#weather-temp").textContent = w.temperature_c == null ? "—" : Math.round(w.temperature_c);
    $("#weather-cond").textContent = WMO.describe(w.weather_code, w.is_day);
    $("#weather-cloud").textContent = w.cloud_cover_pct == null ? "—" : `${Math.round(w.cloud_cover_pct)} %`;
    $("#weather-wind").textContent  = w.wind_speed_ms == null ? "—"
      : `${w.wind_speed_ms.toFixed(1)} m/s${w.wind_direction_deg == null ? "" : ` · ${windCompass(w.wind_direction_deg)}`}`;
    $("#weather-humidity").textContent = w.humidity_pct == null ? "—" : `${Math.round(w.humidity_pct)} %`;
    $("#weather-sunrise").textContent = w.sunrise_ts
      ? new Date(w.sunrise_ts * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "—";
    $("#weather-sunset").textContent  = w.sunset_ts
      ? new Date(w.sunset_ts * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "—";
    const feels = w.feels_like_c != null ? ` · feels ${Math.round(w.feels_like_c)}°` : "";
    $("#weather-sub").textContent = `Open-Meteo${feels} · refreshed ${fmt.ago(w.fetched_at)}`;
    renderWeatherHourly(w.hourly);
  });
}

// "Next few hours" strip — Apple-Weather-style hourly preview at the
// bottom of the Right-now tile. Each cell: HH:00 label · tiny WMO
// icon · °C. We always render from the next upcoming hour (drop any
// slice whose timestamp is already in the past — provider may send
// the current hour as the first slice, which would duplicate the
// hero reading).
function renderWeatherHourly(hourly) {
  const host = $("#weather-hourly");
  if (!host) return;
  if (!Array.isArray(hourly) || hourly.length === 0) {
    host.hidden = true; host.innerHTML = ""; return;
  }
  const nowS = Math.floor(Date.now() / 1000);
  const cells = hourly
    .filter(h => h && h.ts > nowS - 1800)   // keep the current hour through ~30 min in
    .slice(0, 8)
    .map(h => {
      const hour = new Date(h.ts * 1000).getHours();
      const label = `${String(hour).padStart(2, "0")}:00`;
      const temp  = h.temperature_c == null ? "—" : `${Math.round(h.temperature_c)}°`;
      const icon  = WMO.iconSvg(h.weather_code, h.is_day);
      return `
        <div class="weather-hour">
          <span class="weather-hour-t">${label}</span>
          <span class="weather-hour-i" aria-hidden="true">${icon}</span>
          <span class="weather-hour-v">${temp}</span>
        </div>`;
    }).join("");
  if (!cells) { host.hidden = true; host.innerHTML = ""; return; }
  host.innerHTML = cells;
  host.hidden = false;
}

function windCompass(deg) {
  const dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
  return dirs[Math.round((deg % 360) / 22.5) % 16];
}

const FORECAST_EMPTY_DISMISS_KEY = "wattpost.forecast.empty.dismissed";

function renderTomorrowEmpty(show) {
  const empty = $("#tomorrow-empty");
  if (!empty) return;
  const dismissed = localStorage.getItem(FORECAST_EMPTY_DISMISS_KEY) === "1";
  empty.hidden = !show || dismissed;
  if (!empty.dataset.wired) {
    empty.dataset.wired = "1";
    $("#tomorrow-empty-dismiss")?.addEventListener("click", () => {
      localStorage.setItem(FORECAST_EMPTY_DISMISS_KEY, "1");
      empty.hidden = true;
    });
  }
}

async function refreshAccuracyLine() {
  const row = $("#forecast-accuracy");
  if (!row) return;
  try {
    const r = await api("/api/forecast/accuracy?day_offset=1");
    if (!r?.ok) { row.hidden = true; return; }
    const pred = (r.predicted_wh / 1000).toFixed(2);
    const got  = (r.actual_wh / 1000).toFixed(2);
    const acc  = r.accuracy_pct == null ? "—" : `${r.accuracy_pct.toFixed(0)} %`;
    // Tint: green if within ±10%, amber if 10-25% off, red beyond.
    const dev = Math.abs((r.accuracy_pct || 100) - 100);
    const cls = dev <= 10 ? "ok" : (dev <= 25 ? "warn" : "off");
    $("#forecast-accuracy-line").innerHTML = `
      predicted <strong>${pred} kWh</strong> ·
      actual <strong>${got} kWh</strong> ·
      <span class="acc-${cls}">${acc} of forecast</span>`;
    row.hidden = false;
  } catch (e) { row.hidden = true; }
}

// Tomorrow tile was folded into Today (see renderToday) — the standalone
// renderTomorrow() / drawTomorrowSpark() entry points are gone. Anything
// the dashboard used to do for tomorrow lives inside renderToday now.

// ---------- 7-DAY OUTLOOK STRIP ----------
function renderWeek() {
  const panel = $("#week-panel");
  if (!panel) return;
  ensureForecast().then(f => {
    if (!f) { panel.hidden = true; return; }
    const buckets = bucketByDay(f.points);
    // Drop any buckets with no real energy — Solcast's window can
    // include an in-progress past day that gives 0 kWh. Cap at 5
    // days so the grid stays balanced even when Solcast returns the
    // full 7-day window; reads better at every viewport width.
    const days = buckets.filter(b => b.wh > 0).slice(0, 5);
    if (days.length === 0) { panel.hidden = true; return; }
    panel.hidden = false;
    // Title carries the actual day count so it never contradicts the
    // grid below. Sub-line stays as a pure freshness timestamp.
    $("#week-title").textContent = `${days.length}-day outlook`;
    $("#week-sub").textContent = `Refreshed ${fmt.ago(f.fetched_at)}`;
    drawWeekStrip(days);
  });
}

function drawWeekStrip(days) {
  const host = $("#week-strip");
  if (!host) return;
  // Common scale across all day cards so a quiet day visually reads
  // as quiet next to a sunny one — otherwise each card auto-fits its
  // own peak and the comparison loses meaning.
  const peakWAcrossWeek = Math.max(...days.flatMap(d => d.points.map(p => p.w)), 1);
  const todayMid = (() => { const d = new Date(); d.setHours(0,0,0,0); return Math.floor(d.getTime()/1000); })();
  host.innerHTML = days.map(d => {
    let label;
    if (d.dayMid === todayMid) label = "Today";
    else if (d.dayMid === todayMid + 86400) label = "Tomorrow";
    else label = new Date(d.dayMid * 1000).toLocaleDateString([], { weekday: "short" });
    const dateLabel = new Date(d.dayMid * 1000).toLocaleDateString([], { day: "numeric", month: "short" });
    const kwh = (d.wh / 1000).toFixed(1);
    const peakKw = d.peak ? (d.peak.w / 1000).toFixed(2) : "—";
    const peakAt = d.peak ? new Date(d.peak.ts * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "";
    // Today is the "you are here" anchor — matches the Today tile above.
    const isToday = d.dayMid === todayMid;
    return `
      <div class="week-card ${isToday ? "week-card--featured" : ""}">
        <div class="week-card-head">
          <span class="week-card-day">${label}</span>
          <span class="week-card-date">${dateLabel}</span>
        </div>
        <div class="week-card-spark">${weekSparkSvg(d.points, peakWAcrossWeek)}</div>
        <div class="week-card-num">${kwh} <span class="meta-k">kWh</span></div>
        <div class="week-card-foot">peak ${peakKw} kW${peakAt ? " · " + peakAt : ""}</div>
      </div>`;
  }).join("");
}

function weekSparkSvg(points, peakWAcrossWeek) {
  if (!points.length) return "";
  const W = 100, H = 32, padX = 2, padY = 2;
  const t0 = points[0].ts, tN = points[points.length - 1].ts;
  const span = Math.max(1, tN - t0);
  const pts = points.map(p => {
    const x = padX + ((p.ts - t0) / span) * (W - 2*padX);
    const y = H - padY - (p.w / peakWAcrossWeek) * (H - 2*padY);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const area = `M${padX},${H - padY} L ${pts} L ${W - padX},${H - padY} Z`;
  return `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" width="100%" height="${H}">
      <path d="${area}" fill="rgba(210,153,34,0.18)"/>
      <polyline points="${pts}" fill="none" stroke="#d29922" stroke-width="1.4" stroke-linejoin="round"/>
    </svg>`;
}

// ---------- TODAY PANEL ----------
//
// Headline tile: today's PV-so-far + a forecast curve for the rest of
// the day + sub-stats + one-line Tomorrow preview. After sunset (no PV
// expected today + tomorrow forecast available), the tile flips so
// Tomorrow becomes the headline and today demotes to a "final" line —
// the dashboard's "operational moment" stays unambiguous.
function renderToday() {
  const rover = devices.find(d => d.kind === "charge_controller");
  const l = rover?.latest || {};

  // Headline kWh: prefer the server-side integrated total across ALL
  // sources (PV + AC charger + DC-DC) so a Victron-only install or
  // a multi-source install reads correctly. Fall back to the Renogy
  // MPPT's own daily counter for legacy clients that haven't pulled
  // v0.0.81's `sources_today_wh` field yet.
  const sourcesWh = (todayAggregate
    && typeof todayAggregate.sources_today_wh === "number"
    && todayAggregate.sources_today_wh > 0)
    ? todayAggregate.sources_today_wh
    : (l.energy_today_wh || 0);
  const pvActualStr  = fmt.wh(sourcesWh);
  const chargedStr   = (l.charging_ah_today ?? 0) + " Ah";
  const peakSoFarStr = fmt.num(l.max_charging_power_today_w, 0) + " W";
  const loadWh = (todayAggregate && typeof todayAggregate.load_today_wh === "number")
    ? todayAggregate.load_today_wh
    : l.consumption_today_wh;
  const loadStr = fmt.wh(loadWh);

  // Default to live actuals; sleep-mode block below may overwrite the
  // hero number with tomorrow's expected kWh.
  $("#today-pv").textContent      = pvActualStr;
  $("#today-charged").textContent = chargedStr;
  $("#today-peak").textContent    = peakSoFarStr;
  $("#today-load").textContent    = loadStr;

  // Stored today = bank net (in − out, signed). Matches the cloud
  // dashboard's headline answer to "did my system win today?".
  // Uses today_aggregate's bank_net_today_wh when available; falls
  // back to (sources − load) for older builds.
  const storedEl = $("#today-stored");
  if (storedEl) {
    let storedWh = null;
    if (todayAggregate && typeof todayAggregate.bank_net_today_wh === "number") {
      storedWh = todayAggregate.bank_net_today_wh;
    } else if (typeof sourcesWh === "number" && typeof loadWh === "number") {
      storedWh = sourcesWh - loadWh;
    }
    if (storedWh == null) {
      storedEl.textContent = "—";
    } else if (Math.abs(storedWh) < 5) {
      storedEl.textContent = "0 Wh";
    } else {
      const sign = storedWh > 0 ? "↑ " : "↓ ";
      storedEl.textContent = sign + fmt.wh(Math.abs(storedWh));
    }
  }

  // Today's SoC envelope — answers "did the bank get critically low
  // overnight?" at a glance. Sourced from /api/today_soc_minmax
  // (fresh endpoint, cheap MIN/MAX over today's bank.soc_pct
  // samples). Render lazily so we don't block the rest of the tile;
  // the value lands on the next paint.
  const socEnvEl = $("#today-soc-env");
  if (socEnvEl) {
    fetch("/api/today/soc-envelope").then(r => r.ok ? r.json() : null).then(d => {
      if (!d || d.min_pct == null || d.max_pct == null) {
        socEnvEl.textContent = "—";
        return;
      }
      socEnvEl.textContent = `${d.min_pct.toFixed(1)} – ${d.max_pct.toFixed(1)}%`;
    }).catch(() => { socEnvEl.textContent = "—"; });
  }

  // Yesterday-accuracy line lives under this tile now — refresh on the
  // same cadence as the dashboard poll; the API call is cheap and the
  // result hides itself if no archive exists yet.
  refreshAccuracyLine();

  const panel    = $("#today-panel");
  const headline = $("#today-headline");
  const sub      = $("#today-sub");
  const foot     = $("#today-tomorrow");
  const footText = $("#today-tomorrow-text");
  const spark    = $("#today-spark");

  ensureForecast().then(f => {
    if (!f) {
      // No forecast configured. Show only live actuals and surface the
      // gentle "hook up Solcast" CTA card unless the user dismissed it.
      if (headline) headline.textContent = "Today";
      if (sub)  sub.textContent = "";
      if (foot) foot.hidden = true;
      if (spark) spark.innerHTML = "";
      panel?.classList.remove("today-panel--sleep");
      renderTomorrowEmpty(true);
      return;
    }
    renderTomorrowEmpty(false);

    const s = summariseForecast(f.points);
    // Sleep mode: today has no meaningful PV left to come and the
    // forecast knows about tomorrow. < 50 Wh covers noisy zero-ish
    // slices around dusk so we don't oscillate.
    const sleep = s.todayRemainingWh < 50 && s.tomorrowWh > 0;
    panel?.classList.toggle("today-panel--sleep", sleep);

    // Provider name for the sub-line credit. Backend returns the
    // active provider in the forecast blob ("solcast" / "openmeteo").
    // Map to user-friendly text rather than echoing the raw key.
    const provLabel = f.provider === "openmeteo"
      ? "Open-Meteo"
      : f.provider === "solcast" ? "Solcast" : "Forecast";

    if (sleep) {
      if (headline) headline.textContent = "Tomorrow";
      const kEl = document.getElementById("today-hero-k");
      if (kEl) kEl.textContent = "Forecast tomorrow";
      $("#today-pv").textContent = `${(s.tomorrowWh / 1000).toFixed(1)} kWh`;
      if (sub) {
        sub.textContent = s.tomorrowPeak
          ? `Expected · peak ${(s.tomorrowPeak.w / 1000).toFixed(2)} kW at ${_fmtHm(s.tomorrowPeak.ts)} · ${provLabel}`
          : `Expected · ${provLabel}`;
      }
      // Footer becomes the final tally for today.
      if (foot) {
        foot.hidden = false;
        footText.textContent = `Today (final): PV ${pvActualStr} · Load ${loadStr}`;
      }
      drawTodaySpark(s.tomorrowPoints, null, spark);
    } else {
      if (headline) headline.textContent = "Today";
      const kEl = document.getElementById("today-hero-k");
      if (kEl) kEl.textContent = "Harvested today";
      if (sub) {
        if (s.todayWh > 0) {
          // s.todayWh is the provider's full-day forecast at fetch
          // time; s.todayRemainingWh is the from-this-moment-to-EOD
          // portion. Earlier UX rendered both as
          //   "Of X kWh expected · Y still to come"
          // which read as a contradiction once Y == X (e.g. the
          // forecast was refreshed at midnight + hasn't moved while
          // the day passed). Show the actual harvested figure
          // against the forecast instead, so users see the ratio
          // they care about: "are we on track today?".
          const expected  = (s.todayWh / 1000).toFixed(1);
          const harvestWh = (todayAggregate && typeof todayAggregate.sources_today_wh === "number")
            ? todayAggregate.sources_today_wh
            : null;
          if (harvestWh != null) {
            const harvestStr = harvestWh >= 1000
              ? `${(harvestWh / 1000).toFixed(1)} kWh`
              : `${harvestWh.toFixed(0)} Wh`;
            const pct = s.todayWh > 0
              ? Math.round((harvestWh / s.todayWh) * 100) : 0;
            sub.textContent = `Forecast ${expected} kWh · ${harvestStr} so far (${pct} %) · ${provLabel}`;
          } else {
            sub.textContent = `Forecast ${expected} kWh today · ${provLabel}`;
          }
        } else {
          sub.textContent = `${provLabel} · refreshed ${fmt.ago(f.fetched_at)}`;
        }
      }
      // Tomorrow preview footer — only shown when the forecast
      // window has tomorrow's data populated.
      if (s.tomorrowWh > 0 && foot) {
        foot.hidden = false;
        const peakStr = s.tomorrowPeak
          ? ` · peak ${(s.tomorrowPeak.w / 1000).toFixed(2)} kW at ${_fmtHm(s.tomorrowPeak.ts)}`
          : "";
        footText.textContent = `Tomorrow: ${(s.tomorrowWh / 1000).toFixed(1)} kWh expected${peakStr}`;
      } else if (foot) {
        foot.hidden = true;
      }
      drawTodaySpark(s.todayPoints, Math.floor(Date.now() / 1000), spark);
    }
  });
}

function _fmtHm(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// Today's sparkline: pre-now points draw solid (the curve as Solcast
// originally forecast it), post-now points draw dashed + faded so the
// "still to come" portion is visually distinct from history. A faint
// vertical line marks "now". When nowTs is null the curve is rendered
// uniformly bright — used by sleep mode for tomorrow.
function drawTodaySpark(points, nowTs, host) {
  host = host || $("#today-spark");
  if (!host || !points || !points.length) { if (host) host.innerHTML = ""; return; }
  const W = host.clientWidth || 600;
  const H = 64;
  const padX = 8, padY = 6;
  const maxW = Math.max(...points.map(p => p.w), 1);
  const t0 = points[0].ts, tN = points[points.length - 1].ts;
  const span = Math.max(1, tN - t0);
  const xOf = (ts) => padX + ((ts - t0) / span) * (W - 2*padX);
  const yOf = (w)  => H - padY - (w / maxW) * (H - 2*padY);

  if (nowTs == null) {
    const pts = points.map(p => `${xOf(p.ts).toFixed(1)},${yOf(p.w).toFixed(1)}`).join(" ");
    const area = `M${padX},${H - padY} L ${pts} L ${W - padX},${H - padY} Z`;
    host.innerHTML = `
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" width="100%" height="${H}">
        <path d="${area}" fill="rgba(210,153,34,0.18)"/>
        <polyline points="${pts}" fill="none" stroke="#d29922" stroke-width="1.8" stroke-linejoin="round"/>
      </svg>`;
    return;
  }

  const past   = points.filter(p => p.ts <= nowTs);
  const future = points.filter(p => p.ts >= nowTs);
  const pastStr   = past.map(p => `${xOf(p.ts).toFixed(1)},${yOf(p.w).toFixed(1)}`).join(" ");
  const futureStr = future.map(p => `${xOf(p.ts).toFixed(1)},${yOf(p.w).toFixed(1)}`).join(" ");
  const nowX = Math.min(Math.max(xOf(nowTs), padX), W - padX);
  // Area only under the past — emphasises what has actually happened.
  const pastArea = past.length
    ? `<path d="M${xOf(past[0].ts).toFixed(1)},${H - padY} L ${pastStr} L ${nowX.toFixed(1)},${H - padY} Z" fill="rgba(210,153,34,0.18)"/>`
    : "";
  host.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" width="100%" height="${H}">
      ${pastArea}
      ${past.length   ? `<polyline points="${pastStr}"   fill="none" stroke="#d29922" stroke-width="1.8" stroke-linejoin="round"/>` : ""}
      ${future.length ? `<polyline points="${futureStr}" fill="none" stroke="#d29922" stroke-width="1.6" stroke-linejoin="round" stroke-dasharray="3 3" opacity="0.55"/>` : ""}
      <line x1="${nowX.toFixed(1)}" y1="${padY}" x2="${nowX.toFixed(1)}" y2="${H - padY}" stroke="#58a6ff" stroke-width="1" stroke-dasharray="2 2" opacity="0.5"/>
    </svg>`;
}

// ---------- ALERTS ----------
// Friendly per-category short labels for the banner summary. Keep
// them tight — 1-2 words; the summary line ellipsises if too long.
const ALERT_CAT_LABELS = {
  comms_stale:      "Comms slow",
  comms_alarm:      "No recent poll",
  poll_errors:      "Device errors",
  no_first_poll:    "Daemon starting",
  low_soc:          "Low SoC",
  critical_soc:     "Critical SoC",
  cell_drift:       "Cell drift",
  cell_over_voltage:"Cell over-V",
  cell_under_voltage:"Cell under-V",
  cell_temp:        "Cell temp",
  mppt_temp:        "MPPT temp",
};

function renderAlerts() {
  const host    = $("#alerts");
  const summary = $("#alerts-summary");
  const detail  = $("#alerts-detail");
  const cntEl   = $("#alerts-summary-count");
  const catsEl  = $("#alerts-summary-cats");

  // Build the raw alert list with category + value tags so we can
  // group same-category alerts (e.g. cell drift on two batteries at
  // the same value) into a single row. Per-device alerts carry a
  // `device_label` so the group can list them inline.
  const alerts = [];
  const push = (a) => alerts.push(a);

  // Comms / poll health
  if (lastRun) {
    const age = Math.floor(Date.now() / 1000) - lastRun.ts;
    if (age > 300) push({ level: "alarm", category: "comms_alarm",
      lbl: "No recent poll", value: fmt.ago(lastRun.ts) });
    else if (age > 120) push({ level: "warn", category: "comms_stale",
      lbl: "Comms slow", value: `last ${fmt.ago(lastRun.ts)}` });
    if (lastRun.errors_count > 0) push({ level: "warn", category: "poll_errors",
      lbl: `${lastRun.errors_count} device error${lastRun.errors_count === 1 ? "" : "s"}`,
      value: "last poll" });
  } else {
    push({ level: "warn", category: "no_first_poll",
      lbl: "Daemon starting", value: "no poll yet" });
  }

  // Bank-level
  const bank = aggregateBank();
  if (bank) {
    if (bank.soc < 10) push({ level: "alarm", category: "critical_soc",
      lbl: "Bank SoC critical", value: `${bank.soc.toFixed(1)} %` });
    else if (bank.soc < 20) push({ level: "warn", category: "low_soc",
      lbl: "Bank SoC low", value: `${bank.soc.toFixed(1)} %` });
  }

  // Per-device alerts — tagged with device_label so groupable later.
  for (const dev of devices.filter(d => d.kind === "smart_battery")) {
    const l = dev.latest || {};
    const cells = [];
    const cn = +l.cell_count || 0;
    for (let i = 0; i < cn; i++) {
      const v = l[`cell_voltage_${i}_v`];
      if (typeof v === "number") cells.push(v);
    }
    if (cells.length) {
      const spread = Math.max(...cells) - Math.min(...cells);
      if (spread >= 0.20)
        push({ level: "alarm", category: "cell_drift", device_label: dev.label,
          lbl: `${dev.label}: cell drift`, value: `${spread.toFixed(2)} V` });
      else if (spread >= 0.10)
        push({ level: "warn", category: "cell_drift", device_label: dev.label,
          lbl: `${dev.label}: cell drift`, value: `${spread.toFixed(2)} V` });
      if (cells.some(v => v > 3.65))
        push({ level: "alarm", category: "cell_over_voltage", device_label: dev.label,
          lbl: `${dev.label}: cell over-voltage`, value: "" });
      if (cells.some(v => v < 2.8))
        push({ level: "alarm", category: "cell_under_voltage", device_label: dev.label,
          lbl: `${dev.label}: cell under-voltage`, value: "" });
    }
    const temps = [];
    const tn = +l.temperature_sensor_count || 0;
    for (let i = 0; i < tn; i++) {
      const t = l[`temperature_${i}_c`];
      if (typeof t === "number") temps.push(t);
    }
    for (const t of temps) {
      if (t >= 60) { push({ level: "alarm", category: "cell_temp", device_label: dev.label,
        lbl: `${dev.label}: cell temp`, value: `${t.toFixed(0)} °C` }); break; }
      if (t >= 50) { push({ level: "warn", category: "cell_temp", device_label: dev.label,
        lbl: `${dev.label}: cell temp`, value: `${t.toFixed(0)} °C` }); break; }
    }
  }
  // Controller temps
  for (const dev of devices.filter(d => d.kind === "charge_controller")) {
    const t = +dev.latest?.controller_temperature_c;
    if (typeof t === "number") {
      if (t >= 70) push({ level: "alarm", category: "mppt_temp", device_label: dev.label,
        lbl: `${dev.label}: MPPT hot`, value: `${t.toFixed(0)} °C` });
      else if (t >= 60) push({ level: "warn", category: "mppt_temp", device_label: dev.label,
        lbl: `${dev.label}: MPPT warm`, value: `${t.toFixed(0)} °C` });
    }
  }

  if (alerts.length === 0) {
    host.hidden = true;
    detail.hidden = true;
    return;
  }

  // Group same-category alerts that share the same value into a single
  // row. e.g. two batteries at "cell drift 0.10 V" collapses into one
  // row with both labels comma-joined. Categories without a value
  // (over/under-voltage) also collapse if the device_label is the only
  // differentiator. Items without device_label or with distinct values
  // stay as-is.
  const grouped = [];
  const seen = new Map(); // key "<category>|<value>" → index in grouped
  for (const a of alerts) {
    if (!a.device_label) {
      grouped.push({ ...a, devices: [] });
      continue;
    }
    const key = `${a.category}|${a.value || ""}`;
    if (seen.has(key)) {
      const g = grouped[seen.get(key)];
      g.devices.push(a.device_label);
      // Severity escalates if any member is alarm.
      if (a.level === "alarm") g.level = "alarm";
    } else {
      seen.set(key, grouped.length);
      grouped.push({ ...a, devices: [a.device_label] });
    }
  }
  // Rewrite grouped rows where devices.length > 1: replace the lbl
  // (which was just one device's prefix) with category + device list.
  for (const g of grouped) {
    if (g.devices.length > 1) {
      const cat = ALERT_CAT_LABELS[g.category] || g.category;
      g.lbl = `${cat} · ${g.devices.join(", ")}`;
    }
  }

  // Top-level severity: any alarm in the set flips the banner to crit.
  const hasAlarm = grouped.some(g => g.level === "alarm");
  host.classList.toggle("crit", hasAlarm);

  // Summary text
  cntEl.textContent = grouped.length === 1
    ? "1 alert"
    : `${grouped.length} alerts`;
  const cats = [...new Set(grouped.map(g => ALERT_CAT_LABELS[g.category] || g.category))];
  catsEl.textContent = cats.slice(0, 3).join(" · ")
    + (cats.length > 3 ? ` · +${cats.length - 3}` : "");

  // Detail rows — built once per render. The banner stays collapsed
  // until the user taps the summary; collapse state is preserved
  // across renders via the [aria-expanded] attribute on the summary.
  detail.innerHTML = grouped.map(g => `
    <div class="alerts-detail-row">
      <span class="sev ${g.level === "alarm" ? "alarm" : ""}"></span>
      <span class="lbl">${g.lbl}</span>
      <span class="val">${g.value || ""}</span>
    </div>`).join("");

  host.hidden = false;
}

// ---------- cells ----------
function renderCells() {
  const batteries = devices.filter(d => d.kind === "smart_battery");
  const grid = $("#cells-grid");
  grid.innerHTML = "";

  let allV = [];
  for (const b of batteries) {
    const l = b.latest || {};
    const n = +l.cell_count || 0;
    for (let i = 0; i < n; i++) {
      const v = l[`cell_voltage_${i}_v`];
      if (typeof v === "number") allV.push(v);
    }
  }
  const minV = allV.length ? Math.min(...allV) : 0;
  const maxV = allV.length ? Math.max(...allV) : 0;
  const spread = maxV - minV;
  const cls = spread >= 0.20 ? "red" : spread >= 0.10 ? "amber" : "green";
  $("#cells-summary").innerHTML =
    `<span class="pill ${cls}"><span class="pill-dot"></span>` +
    `spread ${spread.toFixed(2)} V · min ${allV.length ? minV.toFixed(2) : "—"} · max ${allV.length ? maxV.toFixed(2) : "—"}</span>`;

  // Apply state class to the panel so the background tint matches the drift level.
  const panel = $("#panel-cells");
  if (panel) {
    panel.classList.remove("drift-warn", "drift-alarm");
    if (spread >= 0.20) panel.classList.add("drift-alarm");
    else if (spread >= 0.10) panel.classList.add("drift-warn");
  }

  // Hide the panel entirely if we have nothing to show — scenario B users
  // (shunt + dumb packs) have no cell data anywhere on the system.
  if (batteries.length === 0 || allV.length === 0) {
    if (panel) panel.hidden = true;
    return;
  }
  if (panel) panel.hidden = false;

  for (const b of batteries) {
    const l = b.latest || {};
    const n = +l.cell_count || 0;
    const tn = +l.temperature_sensor_count || 0;
    const tempVals = [];
    for (let i = 0; i < tn; i++) {
      const t = l[`temperature_${i}_c`];
      if (typeof t === "number") tempVals.push(t);
    }
    const tempStr = tempVals.length
      ? `${(tempVals.reduce((a, c) => a + c, 0) / tempVals.length).toFixed(1)} °C avg`
      : "—";

    const row = document.createElement("div");
    row.className = "cell-row";
    const label = document.createElement("div");
    label.className = "cell-row-label";
    label.textContent = b.label;
    row.appendChild(label);

    const cells = document.createElement("div");
    cells.className = "cell-row-cells";
    for (let i = 0; i < n; i++) {
      const v = l[`cell_voltage_${i}_v`];
      const chip = document.createElement("div");
      let chipCls = "cell-chip";
      if (v === minV && spread > 0.01) chipCls += " is-min";
      if (v === maxV && spread > 0.01) chipCls += " is-max";
      if (typeof v === "number" && v > 3.65) chipCls += " is-high";
      chip.className = chipCls;
      chip.innerHTML = `
        <span class="cell-chip-k">cell ${i + 1}</span>
        <span class="cell-chip-v">${typeof v === "number" ? v.toFixed(2) + " V" : "—"}</span>`;
      cells.appendChild(chip);
    }
    row.appendChild(cells);

    const temp = document.createElement("div");
    temp.className = "cell-row-temp";
    temp.textContent = tempStr;
    row.appendChild(temp);

    grid.appendChild(row);
  }
}

// ---------- lifetime stats cache (refreshed on a slower cadence) ----------
const lifetimeCache = {};  // label -> {data, fetchedAt}
async function ensureLifetime(label) {
  const now = Date.now();
  const entry = lifetimeCache[label];
  if (entry && (now - entry.fetchedAt) < 5 * 60 * 1000) return entry.data;
  try {
    const data = await api(`/api/devices/${encodeURIComponent(label)}/lifetime`);
    lifetimeCache[label] = { data, fetchedAt: now };
    return data;
  } catch (e) { return null; }
}

// ---------- charge efficiency cache ----------
// Same 5-minute TTL as lifetime; the underlying queries do a full table
// scan for coulomb integration, so we don't hit them every render.
const efficiencyCache = {};
async function ensureEfficiency(label) {
  const now = Date.now();
  const entry = efficiencyCache[label];
  if (entry && (now - entry.fetchedAt) < 5 * 60 * 1000) return entry.data;
  try {
    const data = await api(`/api/devices/${encodeURIComponent(label)}/efficiency`);
    efficiencyCache[label] = { data, fetchedAt: now };
    return data;
  } catch (e) { return null; }
}

// Pick the most informative efficiency value to surface in a single
// "η" tile: prefer 30d if reliable, else 90d, else lifetime, else
// show the 30d unreliable number with a "low cycles" caveat tag so
// the user knows why it's grey.
function efficiencyHeadline(data) {
  const w = data?.windows || {};
  for (const k of ["30d", "90d", "lifetime"]) {
    if (w[k]?.reliable && w[k].efficiency_pct != null) {
      return { window: k, value: w[k].efficiency_pct, reliable: true };
    }
  }
  for (const k of ["30d", "90d", "lifetime"]) {
    if (w[k]?.efficiency_pct != null) {
      return { window: k, value: w[k].efficiency_pct, reliable: false };
    }
  }
  return null;
}

// ---------- device cards ----------
// Delete a device from the Devices tab. Hits /api/setup/devices/
// which writes config.yaml + schedules a background hot-reload so
// polling stops without the user having to restart the daemon.
async function deleteDeviceFromList(label, slaveId, transport) {
  if (!transport) {
    alert(`Can't delete "${label}" — couldn't find its transport. ` +
          `Try Setup → Find my dongle to re-confirm the link.`);
    return;
  }
  if (!confirm(`Remove "${label}" (slave ${slaveId})? Polling stops immediately; the BMS keeps running, this just disconnects the dashboard. You can re-add it via Setup → Scan.`)) {
    return;
  }
  try {
    const r = await fetch(
      `/api/setup/devices/${slaveId}?transport=${encodeURIComponent(transport)}`,
      { method: "DELETE" },
    );
    if (!r.ok) {
      const d = await r.text();
      alert(`Couldn't remove device (HTTP ${r.status}). ${d.slice(0, 200)}`);
      return;
    }
    // Pull a fresh snapshot so the card vanishes without waiting
    // for the next SSE tick. refresh() repopulates the global
    // `devices` array; renderDeviceCards runs off that.
    await refresh();
  } catch (e) {
    alert(`Delete failed: ${e.message || e}`);
  }
}

function renderDeviceCards() {
  const host = $("#device-cards");
  host.innerHTML = "";
  // Bank pinned to the top — it's the headline "what's actually in
  // my battery bank right now" reading, so users expect to see it
  // alongside the per-pack cards even though it's a synthetic
  // aggregate, not real hardware. Sort: bank first, then everything
  // else in the order the API returned (which matches config.yaml).
  const visible = [...devices].sort((a, b) => {
    if (a.kind === "bank") return -1;
    if (b.kind === "bank") return 1;
    return 0;
  });
  for (const dev of visible) {
    const l = dev.latest || {};
    const card = document.createElement("a");
    card.className = `dev-card kind-${dev.kind}`;
    card.href = `#/device/${encodeURIComponent(dev.label)}`;

    const head = document.createElement("div");
    head.className = "dev-card-head";
    const left = document.createElement("div");
    left.className = "dev-card-head-left";
    const iconKey = KIND_ICON[dev.kind] || "unknown";
    const iconSpan = document.createElement("span");
    iconSpan.className = "dev-card-icon";
    iconSpan.innerHTML = ICONS[iconKey] || ICONS.unknown;
    const name = document.createElement("div");
    name.className = "dev-card-name";
    name.textContent = dispName(dev);
    left.append(iconSpan, name);
    const right = document.createElement("div");
    right.className = "dev-card-head-right";
    // Bank is a synthetic aggregate — there's nothing on disk to
    // delete. Real devices get a trash icon next to the slave label
    // that hits /api/setup/devices/<slave>?transport=… and refreshes
    // the list. Stop-propagation so a tap doesn't also navigate
    // into the device detail page.
    const slaveLabel = dev.kind === "bank"
      ? `<span class="dev-card-slave">aggregate</span>`
      : (dev.slave_id != null
         ? `<span class="dev-card-slave">slave ${dev.slave_id}</span>`
         : `<span class="dev-card-slave">${dev.vendor || ""}</span>`);
    const delBtnHtml = dev.kind === "bank" ? "" : `
      <button class="dev-card-del" type="button"
              data-del-label="${escHtml(dev.label)}"
              data-del-slave="${dev.slave_id}"
              data-del-transport="${escHtml(dev.transport || '')}"
              title="Remove this device from polling">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>
      </button>`;
    right.innerHTML = `
      ${slaveLabel}
      ${delBtnHtml}
      <svg class="dev-card-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 6 15 12 9 18"/></svg>`;
    head.append(left, right);
    card.appendChild(head);

    const delBtn = right.querySelector(".dev-card-del");
    if (delBtn) {
      delBtn.addEventListener("click", async (e) => {
        e.preventDefault();
        e.stopPropagation();
        await deleteDeviceFromList(
          delBtn.dataset.delLabel,
          +delBtn.dataset.delSlave,
          delBtn.dataset.delTransport,
        );
      });
    }

    const sub = document.createElement("div");
    sub.className = "dev-card-sub";
    const fw = l.firmware_version || l.firmware_version_raw || "";
    sub.textContent = `${dev.vendor} · ${dev.kind}${fw ? " · fw " + fw : ""}${l.model ? " · " + l.model : ""}`;
    card.appendChild(sub);

    // Silent-device indicator (#171). Victron BLE drivers stamp
    // `advertisement_age_s` on every poll, ticking up while the
    // device is broadcast-silent. If it crosses 60 s the transport
    // already considers the snapshot stale; we surface that on the
    // card so the customer doesn't read frozen 2-hour-old values
    // as if they were live. Grey out the whole tile + show how
    // long ago we last heard from it.
    const ageS = (typeof l.advertisement_age_s === "number") ? l.advertisement_age_s : null;
    if (ageS != null && ageS > 60) {
      card.classList.add("dev-card-silent");
      const silent = document.createElement("div");
      silent.className = "dev-card-silent-badge";
      const ageStr = ageS < 90 ? `${Math.round(ageS)} s`
                  : ageS < 5400 ? `${Math.round(ageS / 60)} min`
                  : `${(ageS / 3600).toFixed(1)} h`;
      silent.textContent = `Silent — last heard ${ageStr} ago`;
      card.appendChild(silent);
    }

    // Lifetime stats strip for smart batteries — cycles + Ah throughput.
    // Fetched in the background; injected when ready.
    if (dev.kind === "smart_battery") {
      const lifeBar = document.createElement("div");
      lifeBar.className = "dev-card-lifetime";
      lifeBar.innerHTML = `
        <div class="lt-cell"><span class="meta-k">Cycles</span><span class="lt-v" data-lt="cycles">—</span></div>
        <div class="lt-cell"><span class="meta-k">Ah in</span><span class="lt-v" data-lt="ah_in">—</span></div>
        <div class="lt-cell"><span class="meta-k">Ah out</span><span class="lt-v" data-lt="ah_out">—</span></div>
        <div class="lt-cell" data-lt-eff title="Coulombic charge efficiency, SoC-corrected. Healthy LFP is 95-99%. Dropping &lt;93% over months hints at pack degradation."><span class="meta-k">η <span class="lt-eff-win">—</span></span><span class="lt-v" data-lt="eff">—</span></div>`;
      card.appendChild(lifeBar);
      ensureLifetime(dev.label).then(lt => {
        if (!lt) return;
        lifeBar.querySelector('[data-lt="cycles"]').textContent = lt.cycles?.toFixed(2) ?? "—";
        lifeBar.querySelector('[data-lt="ah_in"]').textContent = `${(+lt.ah_in).toFixed(1)} Ah`;
        lifeBar.querySelector('[data-lt="ah_out"]').textContent = `${(+lt.ah_out).toFixed(1)} Ah`;
      });
      ensureEfficiency(dev.label).then(eff => {
        const cell = lifeBar.querySelector('[data-lt-eff]');
        const val  = cell?.querySelector('[data-lt="eff"]');
        const winLabel = cell?.querySelector('.lt-eff-win');
        if (!cell || !val) return;
        const h = efficiencyHeadline(eff);
        if (!h) { val.textContent = "—"; return; }
        val.textContent = `${h.value.toFixed(1)} %`;
        if (winLabel) winLabel.textContent = h.window;
        cell.classList.toggle("lt-cell--unreliable", !h.reliable);
        if (!h.reliable) {
          cell.title = "Not enough cycling yet for this efficiency number to be trustworthy. Comes back into focus once the pack has done at least one full cycle's worth of throughput inside the window.";
        }
      });
    }

    for (const k of headlineKeys(dev.kind, l)) {
      const v = l[k];
      if (v === undefined || v === null) continue;
      const row = document.createElement("div");
      row.className = "dev-card-row";
      const ke = document.createElement("span");
      ke.className = "k";
      ke.textContent = prettyKey(k);
      const ve = document.createElement("span");
      ve.className = "v";
      const unit = unitFromKey(k);
      ve.textContent = typeof v === "number"
        ? `${fmt.num(v)}${unit ? " " + unit : ""}`
        : String(v);
      row.append(ke, ve);
      card.appendChild(row);
    }

    // Tap-target hint at the bottom of the card
    const footer = document.createElement("div");
    footer.className = "dev-card-foot";
    footer.innerHTML = `<span>View detail</span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 6 15 12 9 18"/></svg>`;
    card.appendChild(footer);

    host.appendChild(card);
  }
}

function headlineKeys(kind, l) {
  if (kind === "charge_controller") {
    return [
      "charging_state", "battery_voltage_v", "battery_current_a",
      "battery_percentage", "pv_voltage_v", "pv_current_a", "pv_power_w",
      "energy_today_wh", "energy_total_wh",
      "controller_temperature_c", "battery_temperature_c",
      "battery_type", "serial",
    ];
  }
  if (kind === "smart_battery") {
    return ["voltage_v", "current_a", "remaining_charge_ah", "capacity_ah", "serial"];
  }
  return Object.keys(l).filter(k => !k.startsWith("_"));
}

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

// Custom-range state — populated when the user picks dates in the
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
// chose — keeps payload + render cheap and ticks readable.
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
// there are >=2 smart_battery devices configured — otherwise the
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
  // charge controller's incoming PV). Best-effort — a missing or
  // unconfigured forecast just falls through to a normal chart.
  let forecast = null;
  if (metric === "pv_power_w") {
    try {
      const f = await api("/api/forecast/pv");
      if (f?.points?.length) forecast = f;
    } catch (e) { /* swallow — no forecast is fine */ }
  }
  drawChart(label, metric, data, forecast);
}

async function refreshChartCompare(metric, selectedLabel) {
  const packs = smartBatteries();
  const urls = packs.map(p => [p.label, buildHistoryURL(p.label, metric)])
                    .filter(([, u]) => u != null);
  if (!urls.length) return;
  // Parallel fetch — a 3-pack rig with the daemon on LAN comes back in
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
  // Stat strip reflects the device the dropdown is on — keeps the
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

  const tsMin = ts[0], tsMax = ts[ts.length - 1];
  const xScale = (tsMin != null && tsMax > tsMin)
    ? { time: true, range: [tsMin, tsMax] } : { time: true };

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

async function refreshEnergyOverview() {
  const host = document.getElementById("energy-chart");
  if (!host) return;
  let payload;
  try {
    const r = await fetch("/api/energy/today");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    payload = await r.json();
  } catch (e) {
    host.innerHTML = `<div style="padding:1rem;color:var(--text-3);font-size:.85rem;">Couldn't load today's energy (${e.message}).</div>`;
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

  drawEnergyChart(host, ts, { solar, charger, load, batIn, batOut, soc });

  // Sub-line: time range.
  const sub = document.getElementById("energy-sub");
  if (sub && payload.since_ts && payload.until_ts) {
    const fmt = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    sub.textContent = `${fmt(payload.since_ts)} → ${fmt(payload.until_ts)}`;
  }

  // Stat cards.
  const totals = payload.totals || {};
  const kwh = (wh) => wh == null ? "·" : (wh / 1000).toFixed(1);
  setText("es-solar",  kwh(totals.solar_wh));
  setText("es-load",   kwh(totals.load_wh));
  setText("es-batin",  kwh(totals.bank_charged_wh));
  setText("es-batout", kwh(totals.bank_discharged_wh));

  // Self-powered breakdown — bar + legend rows.
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

function drawEnergyChart(root, ts, series) {
  if (energyChart) { energyChart.destroy(); energyChart = null; }
  if (!ts.length) {
    root.innerHTML = `<div style="padding:1rem;color:var(--text-3);font-size:.85rem;">No samples yet today.</div>`;
    return;
  }
  const width = Math.max(root.clientWidth, 320);
  const pal = chartPalette();

  const fmtW = (v) => v == null ? "·" : (Math.abs(v) >= 1000 ? (v/1000).toFixed(1) + " kW" : v.toFixed(0) + " W");
  const fmtPct = (v) => v == null ? "·" : v.toFixed(0) + " %";

  const opts = {
    width, height: 280,
    cursor: { drag: { x: true, y: false } },
    scales: {
      x: { time: true },
      // Power axis (signed — battery discharge dips below 0).
      y: { auto: true },
      // SoC % on right axis, fixed 0..100 so the line is comparable
      // across days regardless of power magnitudes.
      y2: { range: [0, 100], auto: false },
    },
    series: [
      {},
      // Solar — yellow filled area, ≥0.
      { label: "Solar",       stroke: "#f0c849", width: 1.2, fill: "rgba(240,200,73,0.35)",
        value: (_u, v) => fmtW(v) },
      // Charger — grey filled area, ≥0.
      { label: "Charger",     stroke: "#b1b9c2", width: 1.0, fill: "rgba(177,185,194,0.25)",
        value: (_u, v) => fmtW(v) },
      // Load — purple LINE (no fill) drawn on top of sources so the
      // line is legible against the yellow/grey area overlap. Earlier
      // versions filled this too, but on heavy-source days the load
      // line vanished behind the charger area; line-on-top reads
      // better at glance.
      { label: "Load",        stroke: "#a48cff", width: 1.8,
        value: (_u, v) => fmtW(v) },
      // Battery in — green filled area, ≥0.
      { label: "Into battery", stroke: "#56d364", width: 1.0, fill: "rgba(86,211,100,0.30)",
        value: (_u, v) => fmtW(v) },
      // Battery out — pink filled area, ≤0 (drawn below zero).
      { label: "Out of battery", stroke: "#f06292", width: 1.0, fill: "rgba(240,98,146,0.30)",
        value: (_u, v) => v == null ? "·" : fmtW(-v) },
      // SoC — red line on right axis.
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
    // No built-in legend on the Energy chart — we render a static
    // colour-chip legend below the chart in HTML, so the uPlot one
    // would duplicate it.
    legend: { show: false },
    plugins: [valueTooltipPlugin()],
  };

  const dataCols = [ts, series.solar, series.charger, series.load, series.batIn, series.batOut, series.soc];
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
  // is. Moved out of the stat strip — it's debug context, not a stat —
  // into a small subtitle line under the chart controls. The Range
  // cell (max - min) was redundant with Min + Max and got dropped.
  const tableLabel = {
    samples: "raw",
    samples_1min: "1-min avg",
    samples_1hour: "1-hour avg",
    samples_1day: "1-day avg",
  }[data?.table] || "·";
  const res = $("#cs-res");
  if (res) res.textContent = `${s.count ?? 0} points · ${tableLabel}`;
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
  //   series[1] = min — invisible line that anchors the band's bottom edge
  //   series[2] = max — invisible line, paired with min by `bands`
  //   series[3] = avg — the visible line
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

  // Main line — last historic series, always visible.
  series.push({
    label: prettyKey(metric),
    stroke: pal.accent,
    width: 2,
    fill: pal.accentFill,
    points: { show: ts.length < 60, size: 4, fill: pal.accent, stroke: pal.accent },
    value: (_u, v) => v == null ? "·" : `${(+v).toFixed(2)}${unit ? " " + unit : ""}`,
  });
  dataCols.push(vals);

  // Build the combined timeline including forecast points so the X
  // axis extends into the future. Historic columns get null-padded
  // past their last sample so all series share one X axis. The
  // forecast contributes up to three series:
  //   - p10 (invisible anchor, lower band edge)
  //   - p90 (invisible anchor, upper band edge)
  //   - median (the dashed line the user actually sees)
  // The fill between p10 and p90 visualises Solcast's stated
  // confidence interval — a wide band means the forecast model
  // isn't sure, a narrow one means high confidence.
  let combinedTs = ts.slice();
  if (forecastFuture.length) {
    const lastHistoric = ts.length ? ts[ts.length - 1] : 0;
    const fTs = forecastFuture.map(p => p.ts).filter(t => t > lastHistoric);
    combinedTs = combinedTs.concat(fTs);
    const pad = new Array(fTs.length).fill(null);
    for (let i = 1; i < dataCols.length; i++) {
      dataCols[i] = dataCols[i].concat(pad);
    }
    const histPad = new Array(ts.length).fill(null);
    const forecastCol = histPad.slice();
    const p10Col      = histPad.slice();
    const p90Col      = histPad.slice();
    const fTsSet = new Set(fTs);
    const hasBand = forecastFuture.some(p =>
      p.pv_w_p10 != null && p.pv_w_p90 != null
    );
    for (const p of forecastFuture) {
      if (!fTsSet.has(p.ts)) continue;
      forecastCol.push(p.pv_w);
      p10Col.push(p.pv_w_p10 ?? null);
      p90Col.push(p.pv_w_p90 ?? null);
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

  // Auto-fit X scale to whatever timeline we ended up with — historical
  // only, or historical + forecast extension.
  const tsMin = combinedTs.length ? combinedTs[0] : null;
  const tsMax = combinedTs.length ? combinedTs[combinedTs.length - 1] : null;
  const xScale = (tsMin != null && tsMax != null && tsMax > tsMin)
    ? { time: true, range: [tsMin, tsMax] }
    : { time: true };

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
        // Fewer ticks, more vertical space per tick — keeps labels
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
          // ticks — guarantees each tick is distinct. Cap at 3 decimals so
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

  // Pick the pack with the highest current drift — that's the one worth
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
// The Hero tile's "Remaining" line is naive — current power × current SoC.
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
      // Forecast says you stay above 10% across the horizon — express
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
    // No forecast — show the naive rolling-average view as a secondary line.
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
// sparkline. Cheap query — single rollup-table scan plus a couple
// of latest-table lookups.
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

  // BMS-direct numbers: only show when a BMS reports them. Otherwise
  // a dash + a quiet hint so customers don't think the tile is broken.
  const bms = data.bms || {};
  if (cy) {
    if (bms.cycle_count != null) {
      cy.textContent = Math.round(bms.cycle_count).toLocaleString();
      cy.title = "Reported by the BMS. Typically increments per full discharge-then-charge.";
    } else {
      cy.textContent = "·";
      cy.title = "Add a BMS to track cycles. (Equivalent cycles from current integration shown below.)";
    }
  }
  if (lf) {
    if (bms.lifetime_throughput_kwh != null) {
      const v = bms.lifetime_throughput_kwh;
      lf.textContent = v >= 1000 ? `${(v / 1000).toFixed(2)} MWh`
                                  : `${v.toFixed(1)} kWh`;
      lf.title = "Lifetime energy moved through the bank (BMS-reported).";
    } else {
      lf.textContent = "·";
      lf.title = "BMS-required. Connect a JK / Lynx BMS to track lifetime throughput.";
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

  // Residency histogram — 10 bars, % time in each 10% SoC band. A
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
      // Bars coloured by SoC band — red for low, amber mid, green high.
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
        // are saturated red — the same temperature reading on either bg.
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

// ---------- routing ----------
// Hash-based. Default route = dashboard. Two forms:
//   #/                   → named routes (dashboard / history / devices / ...)
//   #/device/<label>     → per-device detail page (dispatched by device kind)
const VALID_ROUTES = new Set(["dashboard", "history", "devices", "setup", "settings", "kiosk", "docs"]);

// Routes that mutate config + therefore require an authed session.
// Anonymous LAN viewers (kiosks, family members on the WiFi) get
// dashboard / history / devices / docs / kiosk for free; the moment
// they hit Settings or Setup they're bounced to /login. Backend
// mutation endpoints stay gated regardless — this is a UX guard,
// not a security boundary on its own.
const AUTH_GATED_ROUTES = new Set(["settings", "setup"]);

// Cached auth state — populated by wireHeaderAuth's auth-status
// fetch, refreshed on logout. setRoute() reads this when gating
// AUTH_GATED_ROUTES; null = unknown (treat as anonymous to be safe).
let _authState = null;
function _setAuthState(authed, origin) {
  _authState = { authed, origin: origin || null };
}
window._setAuthState = _setAuthState;  // for wireHeaderAuth handoff

function parseRoute() {
  const raw = (window.location.hash || "").replace(/^#\/?/, "").trim();
  const m = raw.match(/^device\/(.+)$/);
  if (m) return { name: "device", label: decodeURIComponent(m[1]) };
  // docs/<slug> — strip the slug into a separate field.
  const d = raw.match(/^docs\/(.+)$/);
  if (d) return { name: "docs", slug: d[1] };
  if (raw === "docs") return { name: "docs", slug: null };
  return { name: VALID_ROUTES.has(raw) ? raw : "dashboard" };
}
function currentRouteName() { return parseRoute().name; }

function setRoute(_unused) {
  const route = parseRoute();
  // Auth gate: redirect to /login when an unauthed visitor tries to
  // open Settings or Setup. Demo mode skips (no auth at all). If we
  // haven't yet resolved the auth state (race with the bootstrap
  // /api/system/auth-status fetch), fall back to a quick synchronous
  // check via fetch — UX cost is small (one round-trip on first nav
  // to settings).
  if (AUTH_GATED_ROUTES.has(route.name)
      && !document.body.classList.contains("is-demo")) {
    if (_authState && !_authState.authed) {
      window.location.href = "/login?next=/%23/" + encodeURIComponent(route.name);
      return;
    }
    if (_authState === null) {
      // Defer the route render until we know. Bounce home meanwhile
      // so the user isn't staring at a partially-rendered Settings
      // pane that's about to be replaced.
      fetch("/api/system/auth-status", { credentials: "same-origin" })
        .then((r) => r.ok ? r.json() : null)
        .then((data) => {
          _setAuthState(!!(data && data.authed));
          if (!_authState.authed) {
            window.location.href = "/login?next=/%23/" + encodeURIComponent(route.name);
          } else {
            setRoute();  // re-enter now that we know
          }
        })
        .catch(() => {
          // Network error — assume unauthed and bounce.
          window.location.href = "/login?next=/%23/" + encodeURIComponent(route.name);
        });
      return;
    }
  }

  // Mirror current route onto <body> so route-conditional CSS (e.g.
  // hiding the help FAB on /docs) has a hook without needing JS.
  document.body.dataset.route = route.name;
  document.querySelectorAll(".route").forEach(s => {
    s.classList.toggle("active", s.dataset.route === route.name);
  });
  // Top-nav highlights: device detail belongs under the Devices tab
  document.querySelectorAll(".nav-tab").forEach(t => {
    const tab = t.dataset.tab;
    const match = route.name === tab || (route.name === "device" && tab === "devices");
    t.classList.toggle("active", match);
  });
  if (route.name === "history") {
    requestAnimationFrame(() => { refreshEnergyOverview(); refreshChart(); refreshHeatmap(); });
  }
  if (route.name === "settings") { renderSettings(); startDiagTimer(); }
  else { stopDiagTimer(); }
  if (route.name === "dashboard") refreshDriftSparkline();
  if (route.name === "device") renderDeviceDetail(route.label);
  if (route.name === "setup") onEnterSetup();
  if (route.name === "docs")  onEnterDocs(route.slug);
  if (route.name === "kiosk") onEnterKiosk();
  else if (document.body.classList.contains("kiosk-active")) onLeaveKiosk();
  window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
}

window.addEventListener("hashchange", () => setRoute(currentRouteName()));

// White-label branding — applied once at page load. Cached via the
// cloud heartbeat into the appliance kv table; /api/branding hands
// it back here. When unset (Hobby/Pro accounts, or no cloud pair),
// stays on the default WattPost mark. Re-renders on next page load
// after a heartbeat picks up changes (no live hot-swap needed —
// branding updates are rare).
(async function applyBranding() {
  let b;
  try {
    const r = await fetch("/api/branding");
    if (!r.ok) return;
    b = await r.json();
  } catch (_) { return; }
  if (!b || (!b.brand_name && !b.brand_logo_url)) return;
  if (b.brand_name) {
    const title = document.getElementById("app-brand-title");
    if (title) title.textContent = b.brand_name;
    document.title = b.brand_name;
  }
  if (b.brand_logo_url) {
    const def = document.getElementById("app-brand-logo-default");
    const cus = document.getElementById("app-brand-logo-custom");
    if (def && cus) {
      cus.src = b.brand_logo_url;
      cus.alt = b.brand_name || "Logo";
      cus.hidden = false;
      def.hidden = true;
    }
  }
})();

// Set the Daemon row based on whatever poll data we have. Pulled out
// of renderSettings() so applySnapshot can call it too — otherwise the
// Settings tab opens before the first snapshot, shows "starting…",
// and never refreshes until the user navigates away and back.
function renderDaemonStatus() {
  const el = document.getElementById("settings-daemon");
  if (!el) return;
  if (lastRun) {
    el.textContent = lastRun.errors_count === 0 ? "running, healthy" : "running, errors";
  } else {
    el.textContent = "starting…";
  }
}

// ---------- settings panel ----------
function renderSettings() {
  if (lastRun) {
    $("#settings-last-poll").textContent =
      `${lastRun.elapsed_ms} ms · ${fmt.ago(lastRun.ts)}`;
    $("#settings-errors").textContent = String(lastRun.errors_count);
  }
  // Daemon status implied from the same source as the header pill.
  // First-paint race: if Settings opens before the first poll has
  // landed, lastRun is null. Show "starting…" rather than the legacy
  // "no data" which read as a fault to users. applySnapshot() also
  // calls renderDaemonStatus() when the first poll arrives so the
  // value updates without a tab switch.
  renderDaemonStatus();
  // MQTT export now has its own row in Settings → Integrations; the old
  // "see config.yaml" placeholder was removed when that UI shipped.
  refreshAlertsPanel();
  refreshSystemInfo();
  refreshIntegrationsPanel();
  wireBackupRestore();
  refreshBackupSchedule();
  wireBackupRunNow();
  refreshCloudBackups();
  refreshDiscoveryToggle();
  refreshHistorySettings();  // #172 — editable poll interval + retention
  refreshSolarPauseSettings();  // #163 — solar-aware charger pause
  refreshLocationPanel();    // #263/#264 — share-with-cloud toggle
  wireResetToDefaults();     // #138 — Danger zone
}

async function refreshLocationPanel() {
  const currentEl = document.getElementById("settings-location-current-v");
  const msgEl     = document.getElementById("settings-location-msg");
  const radios    = document.querySelectorAll('input[name="settings-location-share"]');
  if (!currentEl || !radios.length) return;
  let s;
  try { s = await api("/api/location/status"); }
  catch (e) {
    currentEl.textContent = "·";
    if (msgEl) msgEl.textContent = "Couldn't load location status: " + e.message;
    return;
  }
  if (s.current) {
    const lat = Number(s.current.lat).toFixed(4);
    const lon = Number(s.current.lon).toFixed(4);
    const src = s.current.source === "gps" ? "GPS" : "static";
    const age = s.current.fix_age_s != null ? ` · fix ${Math.round(s.current.fix_age_s)}s old` : "";
    currentEl.textContent = `${lat}, ${lon} (${src}${age})`;
  } else {
    currentEl.textContent = "No location configured. Add `forecast.lat`/`lon` or plug in a USB GPS to enable.";
  }
  // Pre-select the current mode.
  const mode = (s.share_with_cloud || "off").toLowerCase();
  for (const r of radios) {
    r.checked = (r.value === mode);
    r.disabled = !s.current;  // can't share what we don't have
    r.onchange = () => updateLocationShare(r.value);
  }
  if (!s.current && msgEl) {
    msgEl.textContent = "Sharing is disabled until a location source is configured.";
  } else if (msgEl) {
    msgEl.textContent = "";
  }
}

async function updateLocationShare(mode) {
  const msgEl = document.getElementById("settings-location-msg");
  if (msgEl) { msgEl.textContent = "Saving…"; msgEl.style.color = ""; }
  try {
    const r = await fetch(_withKiosk("/api/location/share"), {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({share_with_cloud: mode}),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status} ${r.statusText}`);
    const data = await r.json();
    if (msgEl) {
      msgEl.textContent = mode === "off"
        ? "Off — cloud will receive no location data from the next heartbeat."
        : (mode === "approx"
            ? "Approximate — coordinates rounded to ~10km before transmission."
            : "Precise — exact lat/lon will be shared on the next heartbeat.");
    }
  } catch (e) {
    if (msgEl) { msgEl.style.color = "var(--red)"; msgEl.textContent = "Save failed: " + e.message; }
  }
}

// Solar-pause panel (#163). Pulls /api/outputs/solar_pause for the live
// rule state + the controller's most recent decision, fills the form,
// and wires the Save button. Outputs come from /api/outputs filtered to
// charge-output kinds so the dropdown only shows things the user can
// reasonably auto-pause (not, say, a Renogy DC load output).
async function refreshSolarPauseSettings() {
  const block = document.getElementById("settings-solar-pause-block");
  if (!block) return;
  let cfg, outputs;
  try {
    [cfg, outputs] = await Promise.all([
      api("/api/outputs/solar_pause"),
      api("/api/outputs"),
    ]);
  } catch (e) {
    const s = document.getElementById("sp-status");
    if (s) s.textContent = `Couldn't load: ${e.message || e}`;
    return;
  }
  const sel = document.getElementById("sp-output");
  if (sel) {
    sel.innerHTML = "";
    // Sensible targets: smart plugs (the recommended path — they cut
    // mains upstream of the AC charger and work regardless of charger
    // vendor) plus any future native AC-charger writes. We deliberately
    // skip the Renogy Rover's DC load output here — it's not the right
    // thing to put on this rule.
    const ok = (outputs.outputs || []).filter(o => {
      const k = (o.kind || "").toLowerCase();
      return k === "smart_plug" || k.includes("ac_charger") || k.includes("charger");
    });
    if (!ok.length) {
      const opt = document.createElement("option");
      opt.textContent = "No smart plug or AC charger output configured";
      opt.value = "";
      sel.appendChild(opt);
      sel.disabled = true;
    } else {
      sel.disabled = false;
      for (const o of ok) {
        const opt = document.createElement("option");
        opt.value = o.id;
        const tag = (o.kind || "").toLowerCase() === "smart_plug" ? "plug" : "charger";
        opt.textContent = `${o.name || o.id} (${tag})`;
        sel.appendChild(opt);
      }
    }
  }
  const setVal = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.value = v; };
  document.getElementById("sp-enabled").checked = !!cfg.enabled;
  if (sel && cfg.charger_output_id) sel.value = cfg.charger_output_id;
  setVal("sp-target",   cfg.target_soc);
  setVal("sp-recover",  cfg.recover_soc);
  setVal("sp-floor",    cfg.hard_floor_soc);
  setVal("sp-pv",       cfg.pv_surplus_w);
  setVal("sp-cooldown", cfg.cooldown_minutes);
  const status = cfg.status || {};
  document.getElementById("sp-decision").textContent =
    status.decision ? `${status.decision}${status.applied ? " (applied)" : ""}`
                    : "·";
  document.getElementById("sp-reason").textContent = status.reason || "·";

  const btn = document.getElementById("sp-save");
  if (btn && !btn.dataset.bound) {
    btn.dataset.bound = "1";
    btn.addEventListener("click", saveSolarPauseSettings);
  }
}

async function saveSolarPauseSettings() {
  const status = document.getElementById("sp-status");
  const btn = document.getElementById("sp-save");
  if (!btn) return;
  btn.disabled = true;
  const orig = btn.textContent; btn.textContent = "Saving…";
  if (status) { status.textContent = ""; status.className = "settings-foot hist-status"; }
  const num = (id) => {
    const el = document.getElementById(id);
    if (!el || el.value === "") return null;
    const n = parseFloat(el.value);
    return Number.isFinite(n) ? n : null;
  };
  const body = {
    enabled:           document.getElementById("sp-enabled").checked,
    charger_output_id: document.getElementById("sp-output").value || null,
    target_soc:        num("sp-target"),
    recover_soc:       num("sp-recover"),
    hard_floor_soc:    num("sp-floor"),
    pv_surplus_w:      num("sp-pv"),
    cooldown_minutes:  num("sp-cooldown"),
  };
  try {
    const r = await fetch("/api/outputs/solar_pause", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    if (status) {
      status.classList.add("hist-status--ok");
      status.textContent = "Saved. Next poll cycle applies the new rule.";
    }
    // Refresh so the live-decision line reflects the new config.
    setTimeout(refreshSolarPauseSettings, 800);
  } catch (e) {
    if (status) {
      status.classList.add("hist-status--err");
      status.textContent = `Save failed: ${e.message || e}`;
    }
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

function wireResetToDefaults() {
  const input = document.getElementById("reset-confirm-input");
  const btn   = document.getElementById("reset-go");
  const keep  = document.getElementById("reset-keep-cloud");
  const msg   = document.getElementById("reset-msg");
  if (!input || !btn || btn.dataset.bound) return;
  btn.dataset.bound = "1";

  // Button only enables when the user has typed "RESET" exactly,
  // case-sensitive. Anything else keeps it disabled so a mistyped
  // confirmation can't trigger the wipe.
  input.addEventListener("input", () => {
    btn.disabled = input.value !== "RESET";
    if (msg) msg.textContent = "";
  });

  btn.addEventListener("click", async () => {
    if (!confirm(
      "This will wipe all transports, devices, exporters, alerts and " +
      "rules from this appliance. History and your web password are " +
      "kept. Continue?"
    )) return;
    btn.disabled = true;
    const orig = btn.textContent; btn.textContent = "Wiping…";
    if (msg) { msg.textContent = ""; msg.classList.remove("hist-status--err", "hist-status--ok"); }
    try {
      const r = await fetch("/api/system/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          confirm: input.value,
          keep_cloud_pairing: keep ? !!keep.checked : true,
        }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${r.status}`);
      }
      const data = await r.json();
      if (msg) {
        msg.classList.add("hist-status--ok");
        const wiped = data.wiped || {};
        const total = Object.values(wiped).reduce((a, b) => a + (b|0), 0);
        msg.textContent = `Wiped ${total} item${total === 1 ? "" : "s"}. Reloading…`;
      }
      // Force a hard reload so the wizard reopens against the empty config.
      setTimeout(() => { window.location.reload(); }, 1200);
    } catch (e) {
      if (msg) {
        msg.classList.add("hist-status--err");
        msg.textContent = `Reset failed: ${e.message || e}`;
      }
      btn.disabled = false;
      btn.textContent = orig;
    }
  });
}

// History & polling editor (#172). Pull current values, fill the form,
// wire Save and Reset buttons. Values apply live; the Save → PATCH
// also persists to config.yaml so a daemon restart doesn't revert.
async function refreshHistorySettings() {
  let data;
  try { data = await api("/api/system/history_settings"); }
  catch (e) {
    const status = $("#hist-status");
    if (status) status.textContent = `Couldn't load: ${e.message || e}`;
    return;
  }
  const live = data.live || {};
  const setVal = (id, v) => { const el = $(id); if (el && v != null) el.value = v; };
  setVal("#hist-poll", live.poll_interval_seconds);
  setVal("#hist-raw",  live.retention_raw_days);
  setVal("#hist-min",  live.retention_min_days);
  setVal("#hist-hour", live.retention_hour_days);
  // Save handler. Bind once via a data flag so re-rendering Settings
  // doesn't stack listeners.
  const saveBtn = $("#hist-save");
  if (saveBtn && !saveBtn.dataset.bound) {
    saveBtn.dataset.bound = "1";
    saveBtn.addEventListener("click", saveHistorySettings);
  }
  const resetBtn = $("#hist-reset");
  if (resetBtn && !resetBtn.dataset.bound) {
    resetBtn.dataset.bound = "1";
    resetBtn.addEventListener("click", () => {
      const d = data.defaults || {};
      setVal("#hist-poll", d.poll_interval_seconds);
      setVal("#hist-raw",  d.retention_raw_days);
      setVal("#hist-min",  d.retention_min_days);
      setVal("#hist-hour", d.retention_hour_days);
      const status = $("#hist-status");
      if (status) status.textContent = "Defaults loaded — click Save to apply.";
    });
  }
}

async function saveHistorySettings() {
  const status = $("#hist-status");
  const btn    = $("#hist-save");
  if (!btn) return;
  btn.disabled = true;
  const orig = btn.textContent; btn.textContent = "Saving…";
  if (status) { status.textContent = ""; status.className = "settings-foot hist-status"; }
  const num = (sel) => {
    const el = $(sel);
    if (!el || el.value === "") return null;
    const n = parseInt(el.value, 10);
    return Number.isFinite(n) ? n : null;
  };
  const body = {
    poll_interval_seconds: num("#hist-poll"),
    retention_raw_days:    num("#hist-raw"),
    retention_min_days:    num("#hist-min"),
    retention_hour_days:   num("#hist-hour"),
  };
  try {
    const r = await fetch("/api/system/history_settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    if (status) {
      status.classList.add("hist-status--ok");
      status.textContent = "Saved. Polling interval applies on the next cycle; retention on the next maintenance pass.";
    }
  } catch (e) {
    if (status) {
      status.classList.add("hist-status--err");
      status.textContent = `Save failed: ${e.message || e}`;
    }
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

// Discovery telemetry (#129). Reads /api/system/discovery to render
// the current opt-in state, wires the toggle to /toggle. Cheap +
// idempotent — safe to call on every settings render.
async function refreshDiscoveryToggle() {
  const btn = document.getElementById("discovery-toggle");
  const msg = document.getElementById("discovery-toggle-msg");
  if (!btn || !msg) return;
  let state;
  try {
    state = await api("/api/system/discovery");
  } catch (_) {
    btn.hidden = true;
    msg.textContent = "·";
    return;
  }
  btn.hidden = false;
  btn.dataset.enabled = state.enabled ? "1" : "0";
  btn.textContent = state.enabled ? "Disable discovery" : "Enable discovery";
  if (!state.paired) {
    btn.disabled = true;
    msg.textContent = "Pair the appliance to wattpost.cloud to enable.";
  } else {
    btn.disabled = false;
    msg.textContent = state.enabled
      ? "On. Unknown devices in scans send anonymous fingerprints."
      : "Off. No data leaves this appliance.";
  }
  if (btn.dataset.wired === "1") return;
  btn.dataset.wired = "1";
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    const next = btn.dataset.enabled !== "1";
    try {
      const r = await fetch("/api/system/discovery/toggle", {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({enabled: next}),
      });
      if (!r.ok) {
        let d = `HTTP ${r.status}`;
        try { const j = await r.json(); d = j.detail || d; } catch (_) {}
        msg.textContent = d;
      }
    } catch (e) {
      msg.textContent = e.message || String(e);
    } finally {
      await refreshDiscoveryToggle();
    }
  });
}

// One-shot wireup for the Backup & restore block. Idempotent (settings
// can re-render); we tag the input once we've bound it and bail out on
// subsequent renders.
function wireBackupRestore() {
  const input = document.getElementById("backup-restore-file");
  if (!input || input.dataset.wired === "1") return;
  input.dataset.wired = "1";
  const msg = document.getElementById("backup-msg");
  input.addEventListener("change", async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    const human = (file.size / (1024 * 1024)).toFixed(1) + " MB";
    if (!window.confirm(
      `Restore ${file.name} (${human})?\n\n` +
      "This will OVERWRITE the current database, config, and " +
      "local-UI password, then restart the daemon. The previous " +
      "config is saved as config.yaml.restored.bak in case you " +
      "need to back out."
    )) {
      input.value = "";
      return;
    }
    if (msg) msg.textContent = `Uploading ${human}…`;
    try {
      const r = await fetch(_withKiosk("/api/system/restore"), {
        method: "POST",
        headers: { "Content-Type": "application/gzip" },
        body: file,
      });
      if (!r.ok) {
        let detail = r.statusText;
        try { const j = await r.json(); if (j && j.detail) detail = j.detail; }
        catch {}
        throw new Error(`${r.status} ${detail}`);
      }
      const data = await r.json();
      if (msg) msg.textContent =
        `Restored ${(data?.applied?.files || []).length} file(s). Daemon restarting…`;
      // The daemon re-execs ~0.4 s after responding. Poll /api/health
      // until it comes back, then reload to pick up the new state.
      setTimeout(pollUntilHealthyThenReload, 1500);
    } catch (e) {
      if (msg) msg.textContent = "Restore failed: " + e.message;
      window.alert("Restore failed: " + e.message);
    } finally {
      input.value = "";  // let the same file be picked again
    }
  });
}

async function refreshBackupSchedule() {
  const summary = document.getElementById("backup-sched-summary");
  const list = document.getElementById("backup-snap-list");
  if (!summary || !list) return;
  let s;
  try { s = await api("/api/system/backup/schedule"); }
  catch (e) {
    summary.textContent = "Could not load backup schedule: " + e.message;
    return;
  }
  if (!s.enabled) {
    summary.innerHTML =
      `Scheduled backups are <strong>disabled</strong>. Set <code>backup.enabled: true</code> in <code>config.yaml</code> to turn them on.`;
    list.innerHTML = `<div class="settings-empty">No snapshots.</div>`;
    return;
  }
  const next = s.next_run_ts ? fmt.ago(s.next_run_ts) : "·";
  const last = s.last_run_ts ? fmt.ago(s.last_run_ts) : "never";
  const intervalH = s.interval_hours;
  const intervalLabel = intervalH % 24 === 0
    ? (intervalH / 24 === 1 ? "daily" : `every ${intervalH / 24} days`)
    : `every ${intervalH} h`;
  const cloudBit = s.cloud_upload_enabled
    ? ` · cloud upload <strong>on</strong>${s.last_cloud_upload_ts ? ` (last: ${fmt.ago(s.last_cloud_upload_ts)}, ${s.last_cloud_upload_ok ? "ok" : "failed"})` : ""}`
    : "";
  summary.innerHTML =
    `Running ${intervalLabel}, keeping the last ${s.keep_count}. ` +
    `Last run: ${last}. Next run: ${next}.${cloudBit}` +
    (s.last_run_error ? `<br><span style="color:#f87171">Last error: ${s.last_run_error}</span>` : "");
  if (!s.snapshots || !s.snapshots.length) {
    list.innerHTML = `<div class="settings-empty">No snapshots yet.</div>`;
    return;
  }
  list.innerHTML = `
    <div style="overflow-x:auto;-webkit-overflow-scrolling:touch">
    <table style="width:100%;border-collapse:collapse;font-size:.88rem">
      <thead><tr style="text-align:left;opacity:.7">
        <th style="padding:.3rem .6rem .3rem 0;white-space:nowrap">Name</th>
        <th style="padding:.3rem .6rem .3rem 0;white-space:nowrap">Size</th>
        <th style="padding:.3rem .6rem .3rem 0;white-space:nowrap">Created</th>
        <th></th>
      </tr></thead>
      <tbody>
        ${s.snapshots.map(snap => {
          const sizeMb = (snap.size_bytes / (1024 * 1024)).toFixed(1);
          return `<tr style="border-top:1px solid rgba(255,255,255,.06)">
            <td style="padding:.35rem .6rem .35rem 0;white-space:nowrap"><code>${snap.name}</code></td>
            <td style="padding:.35rem .6rem .35rem 0;white-space:nowrap">${sizeMb} MB</td>
            <td style="padding:.35rem .6rem .35rem 0;white-space:nowrap">${fmt.ago(snap.mtime_ts)}</td>
            <td style="padding:.35rem 0;text-align:right;white-space:nowrap">
              <a class="btn-action" style="padding:.15rem .5rem"
                 href="/api/system/backup/file/${encodeURIComponent(snap.name)}" download>Download</a>
              <button class="btn-action" style="padding:.15rem .5rem"
                      type="button" data-snap-del="${snap.name}">Delete</button>
            </td>
          </tr>`;
        }).join("")}
      </tbody>
    </table>
    </div>`;
  list.querySelectorAll("[data-snap-del]").forEach(b => {
    b.addEventListener("click", async () => {
      const name = b.dataset.snapDel;
      if (!window.confirm(`Delete snapshot ${name}? This can't be undone.`)) return;
      try {
        const r = await fetch(_withKiosk(`/api/system/backup/file/${encodeURIComponent(name)}`), {
          method: "DELETE",
        });
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        refreshBackupSchedule();
      } catch (e) {
        window.alert("Delete failed: " + e.message);
      }
    });
  });
}

function wireBackupRunNow() {
  const btn = document.getElementById("backup-run-now");
  if (!btn || btn.dataset.wired === "1") return;
  btn.dataset.wired = "1";
  btn.addEventListener("click", async () => {
    const msg = document.getElementById("backup-run-msg");
    btn.disabled = true;
    if (msg) msg.textContent = "Capturing snapshot…";
    try {
      const r = await fetch(_withKiosk("/api/system/backup/run-now"), { method: "POST" });
      if (!r.ok) {
        let detail = r.statusText;
        try { const j = await r.json(); if (j && j.detail) detail = j.detail; } catch {}
        throw new Error(`${r.status} ${detail}`);
      }
      const data = await r.json();
      if (msg) {
        const sizeMb = (data.size_bytes / (1024 * 1024)).toFixed(1);
        msg.textContent = `Snapshot ${data.snapshot} (${sizeMb} MB) written.`;
      }
      refreshBackupSchedule();
    } catch (e) {
      if (msg) msg.textContent = "Snapshot failed: " + e.message;
    } finally {
      btn.disabled = false;
    }
  });
}

async function refreshCloudBackups() {
  const list = document.getElementById("backup-cloud-list");
  const summary = document.getElementById("backup-cloud-summary");
  const toggleBtn = document.getElementById("backup-cloud-toggle");
  const toggleMsg = document.getElementById("backup-cloud-toggle-msg");
  if (!list || !toggleBtn) return;

  // Read schedule first — tells us whether cloud_upload is on
  // (and whether the local backup service is running at all).
  let sched;
  try { sched = await api("/api/system/backup/schedule"); }
  catch { sched = null; }
  const cloudOn = !!(sched && sched.cloud_upload_enabled);

  // Always probe cloud-list — its response carries the cues we
  // need (paired? Pro/Installer tier? cloud on a build that
  // supports backups?) to decide what to show. Doing this before
  // wiring the toggle means we can grey-out for Hobby etc.
  let data, fetchError;
  try {
    const r = await fetch(_withKiosk("/api/system/backup/cloud-list"), { cache: "no-store" });
    if (r.status === 503) {
      data = { _not_paired: true };
    } else if (r.ok) {
      data = await r.json();
    } else {
      let detail = r.statusText;
      try { const j = await r.json(); if (j && j.detail) detail = j.detail; } catch {}
      fetchError = detail;
    }
  } catch (e) {
    fetchError = e.message;
  }

  // Decide toggle state up-front based on what we learned.
  const notPaired = data && data._not_paired;
  const tierRequired = data && data.tier_required;
  const notYetAvailable = data && data.not_yet_available;
  const canToggle = !notPaired && !tierRequired && !notYetAvailable && !fetchError;

  toggleBtn.hidden = false;
  toggleBtn.disabled = !canToggle && !cloudOn;  // allow disabling even when cloud no longer accepts
  if (notPaired) {
    toggleBtn.textContent = "Pair to wattpost.cloud first";
  } else if (tierRequired) {
    toggleBtn.textContent = "Upgrade to enable";
  } else if (notYetAvailable) {
    toggleBtn.textContent = "Cloud account not ready";
  } else {
    toggleBtn.textContent = cloudOn ? "Disable cloud upload" : "Enable cloud upload";
  }
  toggleBtn.onclick = async () => {
    if (tierRequired) {
      window.location.href = "https://wattpost.cloud/app/account";
      return;
    }
    toggleBtn.disabled = true;
    toggleMsg.textContent = cloudOn ? "Disabling…" : "Enabling…";
    try {
      const r = await fetch(_withKiosk("/api/system/backup/cloud-toggle"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !cloudOn }),
      });
      if (!r.ok) {
        let detail = r.statusText;
        try { const j = await r.json(); if (j && j.detail) detail = j.detail; } catch {}
        throw new Error(`${r.status} ${detail}`);
      }
      toggleMsg.textContent = !cloudOn ? "Enabled." : "Disabled.";
      refreshBackupSchedule();
      refreshCloudBackups();
    } catch (e) {
      toggleMsg.textContent = "Failed: " + e.message;
    } finally {
      toggleBtn.disabled = false;
    }
  };

  // Now render the list area based on the same data.
  if (notPaired) {
    list.innerHTML = `<div class="settings-empty">
      Pair this appliance to wattpost.cloud first (Settings → Cloud)
      to enable off-site backups.
    </div>`;
    return;
  }
  if (tierRequired) {
    list.innerHTML = `<div class="settings-empty">
      Cloud backups require <strong>Pro</strong> or
      <strong>Installer</strong> tier on the paired account.
      <a href="https://wattpost.cloud/app/account">Upgrade here</a>.
    </div>`;
    return;
  }
  if (notYetAvailable) {
    list.innerHTML = `<div class="settings-empty">
      Cloud account is on an older build that doesn't accept backup
      uploads yet. The next cloud deploy will enable this. Your
      local snapshots are unaffected.
    </div>`;
    return;
  }
  if (fetchError) {
    list.innerHTML = `<div class="settings-empty" style="color:#f87171">${fetchError}</div>`;
    return;
  }
  if (!cloudOn) {
    list.innerHTML = `<div class="settings-empty">
      Cloud upload is off. Click <strong>Enable cloud upload</strong>
      to push each scheduled snapshot to wattpost.cloud.
    </div>`;
    return;
  }
  if (summary && data.backups && data.backups.length > 0) {
    summary.innerHTML =
      `Off-site copies stored on wattpost.cloud. ${data.backups.length} backup(s) on file.`;
  }
  if (!data.backups || !data.backups.length) {
    list.innerHTML = `<div class="settings-empty">No cloud backups yet. The next scheduled snapshot will upload automatically.</div>`;
    return;
  }
  list.innerHTML = `
    <div style="overflow-x:auto;-webkit-overflow-scrolling:touch">
    <table style="width:100%;border-collapse:collapse;font-size:.88rem">
      <thead><tr style="text-align:left;opacity:.7">
        <th style="padding:.3rem .6rem .3rem 0;white-space:nowrap">Name</th>
        <th style="padding:.3rem .6rem .3rem 0;white-space:nowrap">Size</th>
        <th style="padding:.3rem .6rem .3rem 0;white-space:nowrap">Uploaded</th>
        <th style="padding:.3rem .6rem .3rem 0;white-space:nowrap">From</th>
        <th></th>
      </tr></thead>
      <tbody>
        ${data.backups.map(b => {
          const sizeMb = (b.size_bytes / (1024 * 1024)).toFixed(1);
          const uploaded = b.uploaded_at ? fmt.ago(new Date(b.uploaded_at).getTime() / 1000) : "·";
          return `<tr style="border-top:1px solid rgba(255,255,255,.06)">
            <td style="padding:.35rem .6rem .35rem 0;white-space:nowrap"><code>${b.filename}</code></td>
            <td style="padding:.35rem .6rem .35rem 0;white-space:nowrap">${sizeMb} MB</td>
            <td style="padding:.35rem .6rem .35rem 0;white-space:nowrap">${uploaded}</td>
            <td style="padding:.35rem .6rem .35rem 0;white-space:nowrap">${b.manifest_version || "·"}</td>
            <td style="padding:.35rem 0;text-align:right;white-space:nowrap">
              <button class="btn-action" style="padding:.15rem .5rem" type="button"
                      data-cloud-restore="${b.id}" data-cloud-name="${b.filename}">Restore</button>
            </td>
          </tr>`;
        }).join("")}
      </tbody>
    </table>
    </div>`;
  list.querySelectorAll("[data-cloud-restore]").forEach(b => {
    b.addEventListener("click", async () => {
      const id = b.dataset.cloudRestore;
      const name = b.dataset.cloudName;
      if (!window.confirm(
        `Restore ${name} from wattpost.cloud?\n\n` +
        "This will OVERWRITE the current database, config, and " +
        "local-UI password, then restart the daemon."
      )) return;
      b.disabled = true; b.textContent = "Downloading…";
      try {
        const r = await fetch(_withKiosk(`/api/system/backup/cloud-restore/${id}`), { method: "POST" });
        if (!r.ok) {
          let detail = r.statusText;
          try { const j = await r.json(); if (j && j.detail) detail = j.detail; } catch {}
          throw new Error(`${r.status} ${detail}`);
        }
        b.textContent = "Daemon restarting…";
        setTimeout(pollUntilHealthyThenReload, 1500);
      } catch (e) {
        window.alert("Cloud restore failed: " + e.message);
        b.disabled = false; b.textContent = "Restore";
      }
    });
  });
}

async function pollUntilHealthyThenReload() {
  const msg = document.getElementById("backup-msg");
  const deadline = Date.now() + 60_000;  // give the daemon a full minute
  while (Date.now() < deadline) {
    try {
      const r = await fetch(_withKiosk("/api/health"), { cache: "no-store" });
      if (r.ok) {
        if (msg) msg.textContent = "Daemon back online. Reloading…";
        setTimeout(() => window.location.reload(), 600);
        return;
      }
    } catch { /* still restarting */ }
    await new Promise(res => setTimeout(res, 1500));
  }
  if (msg) msg.textContent =
    "Daemon hasn't come back in 60 s. Check the logs and reload manually.";
}

// ---------- system info (About block) ----------
function fmtBytes(b) {
  if (b == null) return "·";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = b, i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 10 ? 0 : 1)} ${units[i]}`;
}
function fmtDuration(s) {
  if (s == null) return "·";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}
async function refreshSystemInfo() {
  let info;
  try { info = await api("/api/system/info"); }
  catch (_) { return; }
  // Demo banner: revealed once at boot when the daemon reports
  // WATTPOST_DEMO=1. Set body class too so other components (alert
  // editor, settings forms) can grey themselves out visually even
  // though the server-side middleware already 403s their submits.
  if (info.demo) {
    const banner = document.getElementById("demo-banner");
    if (banner) banner.hidden = false;
    document.body.classList.add("is-demo");
  }
  const d = info.disk || {};
  const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
  set("#settings-uptime", fmtDuration(info.uptime_seconds));
  set("#settings-python", info.python || "·");
  set(
    "#settings-disk",
    d.total
      ? `${fmtBytes(d.used)} / ${fmtBytes(d.total)} · ${d.percent}% used`
      : "·",
  );
  refreshUpdateState();
}

async function refreshUpdateState() {
  let u;
  try { u = await api("/api/system/update"); }
  catch (_) { return; }
  const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
  set("#settings-version", "v" + (u.current_version || "?"));

  const isDocker = u.deployment === "docker";

  // "Latest available" row is ALWAYS visible — gives users a positive
  // confirmation either way (the previous behaviour silently hid the
  // row when up-to-date, which read as "data missing"). Two shapes:
  //   has_update + latest_version → "v0.1.34 available" + Release-notes link
  //   otherwise                   → "Up to date" (no link, no apply btn)
  // The version-known guard still matters for the transient first-boot
  // window where has_update=true but latest_version=null — we then
  // render "Checking…" instead of the misleading "v—".
  const row = $("#settings-update-row");
  if (row) row.hidden = false;
  const pendingUpdate = u.has_update && !!u.latest_version;
  const a = $("#settings-update-link");
  if (pendingUpdate) {
    set("#settings-update-latest", "v" + u.latest_version + " available");
    if (a) {
      // Always route to the in-app hash route — the appliance
      // dashboard uses hash routing (#/docs/<slug>), not server
      // paths. The manifest's release_url is shaped for the
      // wattpost.io marketing site and would 404 here.
      a.href = "#/docs/release-notes";
      a.hidden = false;
    }
    row?.classList.add("settings-row--update");
  } else {
    set("#settings-update-latest",
      u.has_update ? "Checking…" : "Up to date");
    if (a) a.hidden = true;
    row?.classList.remove("settings-row--update");
  }

  // Apply button: only on Pi installs with an actual pending update.
  // Docker users update via `docker compose pull` on the host — no
  // in-app button, by design (matches Immich, Pi-hole, Vaultwarden
  // conventions).
  const applyBtn = $("#settings-update-apply");
  if (applyBtn) applyBtn.hidden = isDocker || !pendingUpdate;

  // "Updates: docker compose pull..." row — only when there's
  // actually an update pending on a Docker install. Used to be
  // shown all the time on Docker, which read as "you need to do
  // something" even when up to date. The Docker-update path is
  // documented anyway; only surface the hint when actionable.
  const dockerRow = $("#settings-update-docker-row");
  if (dockerRow) dockerRow.hidden = !(isDocker && pendingUpdate);

  // Force-hide the in-flight progress row whenever refreshUpdateState
  // runs. The apply handler is the ONLY thing that should ever unhide
  // it — previously, the row could get stuck visible after a daemon
  // restart left the DOM in mid-apply state, showing a permanent
  // "waiting…" zombie to users who hadn't pressed Apply themselves.
  // Service workers caching the half-progress shell made it worse.
  // Apply handler still unhides on click, then calls back to here on
  // completion, so the row hides cleanly when the apply finishes.
  const progressRow = $("#settings-update-progress-row");
  if (progressRow) progressRow.hidden = true;

  if (u.last_checked_at) {
    set("#settings-update-checked", fmt.ago(u.last_checked_at)
      + (u.last_error ? ` · last error: ${u.last_error}` : ""));
  } else {
    set("#settings-update-checked", "never");
  }
}

// Alert banner expand/collapse — toggles the detail panel + the chevron
// rotation via the `expanded` class on the host. We deliberately use
// addEventListener (not the inline onclick from the wireframe) so the
// handler survives DOM diffs in renderAlerts (which only rewrites the
// summary text + detail innerHTML, never the summary button itself).
document.getElementById("alerts-summary")?.addEventListener("click", () => {
  const host   = document.getElementById("alerts");
  const detail = document.getElementById("alerts-detail");
  const btn    = document.getElementById("alerts-summary");
  if (!host || !detail || !btn) return;
  const open = host.classList.toggle("expanded");
  detail.hidden = !open;
  btn.setAttribute("aria-expanded", String(open));
});

document.getElementById("settings-update-now")?.addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true; btn.textContent = "Checking…";
  try {
    await fetch("/api/system/update/check", { method: "POST" });
    await refreshUpdateState();
  } finally {
    btn.disabled = false; btn.textContent = "Check now";
    // Drop focus so iOS Safari doesn't leave the button in its
    // pressed/highlighted state after the action completes — users
    // see the colour stuck and assume the button is still busy.
    btn.blur();
  }
});

// Update-now: fire-and-poll. /api/system/update/apply backgrounds the
// helper and 202s immediately; we then poll /api/system/update/log
// every 2s to surface progress. install.sh restarts the daemon mid-
// flight so /api/system/update/log will briefly 502 (cloudflared not
// proxying yet) — we tolerate that and resume polling once the daemon
// comes back up.
document.getElementById("settings-update-apply")?.addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  if (!confirm("Apply update now? The daemon will restart at the end of the install (dashboard reconnects automatically in ~30s).")) return;
  btn.disabled = true; btn.textContent = "Updating…";
  const row = $("#settings-update-progress-row");
  const out = $("#settings-update-progress");
  if (row) row.hidden = false;
  if (out) out.textContent = "starting…";
  try {
    const r = await fetch("/api/system/update/apply", { method: "POST" });
    if (!r.ok) {
      const t = await r.text();
      if (out) out.textContent = `failed to start: ${t}`;
      btn.disabled = false; btn.textContent = "Update now";
      return;
    }
  } catch (e) {
    if (out) out.textContent = `failed to start: ${e}`;
    btn.disabled = false; btn.textContent = "Update now";
    return;
  }
  // Poll the log.
  let consecutive502 = 0;
  const poll = async () => {
    try {
      const r = await fetch("/api/system/update/log");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      consecutive502 = 0;
      if (out) {
        out.textContent = (data.lines || []).join("");
        out.scrollTop = out.scrollHeight;
      }
      if (data.running) {
        setTimeout(poll, 2000);
      } else {
        // Lock released → either done or never started. Refresh
        // overall update state so the version flips.
        setTimeout(() => { refreshUpdateState(); }, 2000);
        btn.disabled = false; btn.textContent = "Update now";
      }
    } catch (e) {
      consecutive502 += 1;
      if (consecutive502 > 40) {
        // ~80s of unreachable daemon — give up auto-polling and let
        // the user refresh manually.
        if (out) out.textContent += `\n[connection lost. Refresh the page once the daemon is back]`;
        btn.disabled = false; btn.textContent = "Update now";
        return;
      }
      setTimeout(poll, 2000);
    }
  };
  setTimeout(poll, 1500);
});

// ---------- integrations panel (Solcast for now) ----------
//
// One-shot fetch on settings open; mutates inline when the user
// clicks Edit / Save / Test. State stays in module-scope so we don't
// re-fetch on every render.
let integrationsState = { forecast: null, weather: null, cloud: null, mqtt: null, editing: null };
// editing: null | "forecast" | "weather" | "cloud" | "mqtt"

async function refreshIntegrationsPanel() {
  const host = $("#settings-integrations");
  if (!host) return;
  try {
    const [fc, wc, cc, mc] = await Promise.all([
      api("/api/forecast/config"),
      api("/api/weather/config"),
      api("/api/cloud/config"),
      api("/api/exporters/mqtt/config"),
    ]);
    integrationsState.forecast = fc;
    integrationsState.weather  = wc;
    integrationsState.cloud    = cc;
    integrationsState.mqtt     = mc;
  } catch (e) {
    host.innerHTML = `<div class="settings-empty">Could not load integrations: ${e.message}</div>`;
    return;
  }
  renderIntegrationsPanel();
}

function renderIntegrationsPanel() {
  const host = $("#settings-integrations");
  if (!host) return;
  const fc = integrationsState.forecast || {};
  const wc = integrationsState.weather  || {};

  if (integrationsState.editing === "forecast") {
    host.innerHTML = renderForecastForm(fc);
    wireForecastForm();
    return;
  }
  if (integrationsState.editing === "weather") {
    host.innerHTML = renderWeatherForm(wc);
    wireWeatherForm();
    return;
  }
  if (integrationsState.editing === "cloud") {
    host.innerHTML = renderCloudForm(integrationsState.cloud || {});
    wireCloudForm();
    return;
  }
  if (integrationsState.editing === "mqtt") {
    host.innerHTML = renderMqttForm(integrationsState.mqtt || {});
    wireMqttForm();
    return;
  }

  const forecastConfigured = fc.configured;
  const weatherConfigured  = wc.configured;
  const cloudConfigured    = (integrationsState.cloud || {}).configured;
  const mqttEnabled        = (integrationsState.mqtt || {}).enabled;
  host.innerHTML = `
    <div class="integration-row" data-integration="solcast">
      <div class="integration-row-main">
        <div class="integration-row-head">
          <span class="integration-row-name">Solcast PV forecast</span>
          <span class="alerts-row-tag alerts-row-tag--${forecastConfigured ? "ok" : "warn"}">
            ${forecastConfigured ? "configured" : "not set up"}
          </span>
        </div>
        <div class="integration-row-sub">
          ${forecastConfigured
            ? `Polling every ${fc.poll_hours}h · resource ${fc.resource_id?.slice(0, 8) || "·"}…`
            : `Sign up at <a href="https://solcast.com/free-rooftop-solar-forecasting" target="_blank" rel="noopener">solcast.com</a> for a free hobbyist API key.`
          }
        </div>
      </div>
      <div class="integration-row-actions">
        <button class="alerts-add-btn" data-edit-forecast>
          ${forecastConfigured ? "Edit" : "Configure"}
        </button>
      </div>
    </div>
    <div class="integration-row" data-integration="openmeteo">
      <div class="integration-row-main">
        <div class="integration-row-head">
          <span class="integration-row-name">Open-Meteo weather</span>
          <span class="alerts-row-tag alerts-row-tag--${weatherConfigured ? "ok" : "warn"}">
            ${weatherConfigured ? "configured" : "not set up"}
          </span>
        </div>
        <div class="integration-row-sub">
          ${weatherConfigured
            ? `Polling every ${wc.poll_minutes}m · ${wc.lat?.toFixed(3)}, ${wc.lon?.toFixed(3)}`
            : `Current conditions (temp, cloud, sunrise/sunset). No API key. Free public service.`
          }
        </div>
      </div>
      <div class="integration-row-actions">
        <button class="alerts-add-btn" data-edit-weather>
          ${weatherConfigured ? "Edit" : "Configure"}
        </button>
      </div>
    </div>
    <div class="integration-row" data-integration="cloud">
      <div class="integration-row-main">
        <div class="integration-row-head">
          <span class="integration-row-name">WattPost cloud</span>
          <span class="alerts-row-tag alerts-row-tag--${cloudConfigured ? "ok" : "warn"}">
            ${cloudConfigured ? "paired" : "not paired"}
          </span>
        </div>
        <div class="integration-row-sub">
          ${cloudConfigured
            ? `Heartbeat every ${integrationsState.cloud.heartbeat_minutes}m · ${integrationsState.cloud.label || "·"}${integrationsState.cloud.appliance_id ? ` · #${integrationsState.cloud.appliance_id}` : ""}${
                // Used to display the raw CF Tunnel hostname here
                // (humnb7h4n6.wattpost.io). Removed because (a) the
                // tunnel URL is for cloud → appliance plumbing, not
                // for end-user navigation — customers should use the
                // broker URL <slug>.wattpost.cloud from their cloud
                // dashboard, and (b) surfacing it implied "you can
                // share this link" which is exactly wrong (the tunnel
                // requires an SSO session that only the cloud can
                // mint; sharing the URL gets you a "Sign in via
                // wattpost.cloud" page, not access). Keep the "no
                // tunnel" warning since that IS actionable diagnostic.
                integrationsState.cloud.tunnel_enabled === false
                  ? ` · <span class="alerts-row-tag alerts-row-tag--warn">no tunnel. Re-pair to enable remote access</span>`
                  : ""
              }`
            : `Pair with your <a href="${(integrationsState.cloud?.endpoint || "https://wattpost.cloud")}" target="_blank" rel="noopener">wattpost.cloud</a> account for the multi-site dashboard + offline alerts.`
          }
        </div>
      </div>
      <div class="integration-row-actions">
        <button class="alerts-add-btn" data-edit-cloud>
          ${cloudConfigured ? "Edit" : "Pair"}
        </button>
      </div>
    </div>
    <div class="integration-row" data-integration="mqtt">
      <div class="integration-row-main">
        <div class="integration-row-head">
          <span class="integration-row-name">MQTT export</span>
          <span class="alerts-row-tag alerts-row-tag--${mqttEnabled ? "ok" : "warn"}">
            ${mqttEnabled ? "enabled" : "not set up"}
          </span>
        </div>
        <div class="integration-row-sub">
          ${mqttEnabled
            ? `Publishing to <code>${integrationsState.mqtt.host}:${integrationsState.mqtt.port}</code> under <code>${integrationsState.mqtt.topic_prefix}/</code>${integrationsState.mqtt.ha_discovery ? " · HA discovery on" : ""}`
            : `Publish every poll snapshot to a local MQTT broker for Home Assistant, Node-RED, or your own subscribers. Local-LAN, no cloud.`
          }
        </div>
      </div>
      <div class="integration-row-actions">
        <button class="alerts-add-btn" data-edit-mqtt>
          ${mqttEnabled ? "Edit" : "Configure"}
        </button>
      </div>
    </div>`;
  $("[data-edit-forecast]")?.addEventListener("click", () => {
    integrationsState.editing = "forecast";
    renderIntegrationsPanel();
  });
  $("[data-edit-weather]")?.addEventListener("click", () => {
    integrationsState.editing = "weather";
    renderIntegrationsPanel();
  });
  $("[data-edit-cloud]")?.addEventListener("click", () => {
    integrationsState.editing = "cloud";
    renderIntegrationsPanel();
  });
  $("[data-edit-mqtt]")?.addEventListener("click", () => {
    integrationsState.editing = "mqtt";
    renderIntegrationsPanel();
  });
}

function renderMqttForm(mc) {
  const enabled = !!mc.enabled;
  const v = (k, d = "") => mc[k] != null ? String(mc[k]) : d;
  return `
    <form class="alerts-form" data-form="mqtt">
      <div class="alerts-form-grid">
        <label>Broker host
          <input type="text" name="host" value="${v("host")}" required placeholder="127.0.0.1"/>
        </label>
        <label>Port
          <input type="number" name="port" value="${v("port", "1883")}" min="1" max="65535" required/>
        </label>
        <label>Username
          <input type="text" name="username" value="${v("username")}" placeholder="(blank = anonymous)"/>
        </label>
        <label>Password
          <input type="password" name="password"
                 value="${mc.password === "****" ? "" : v("password")}"
                 placeholder="${mc.password === "****" ? "(unchanged)" : "(blank = anonymous)"}"/>
        </label>
        <label class="alerts-field-wide">Topic prefix
          <input type="text" name="topic_prefix" value="${v("topic_prefix", "solar")}" placeholder="solar"/>
        </label>
        <label>Client ID
          <input type="text" name="client_id" value="${v("client_id", "solar-monitor")}"/>
        </label>
        <label>QoS
          <select name="qos">
            ${[0,1,2].map(q => `<option value="${q}" ${String(mc.qos ?? 0) === String(q) ? "selected" : ""}>${q}</option>`).join("")}
          </select>
        </label>
      </div>
      <div class="alerts-form-grid alerts-form-grid--full" style="margin-top:.55rem">
        <label class="alerts-checkbox"><input type="checkbox" name="retain" ${mc.retain !== false ? "checked" : ""}/> Retain published messages</label>
        <label class="alerts-checkbox"><input type="checkbox" name="publish_per_metric" ${mc.publish_per_metric !== false ? "checked" : ""}/> Publish per-metric topics (otherwise only full-snapshot)</label>
        <label class="alerts-checkbox"><input type="checkbox" name="ha_discovery" ${mc.ha_discovery ? "checked" : ""}/> Home Assistant MQTT discovery (auto-create sensors)</label>
      </div>
      <details class="alerts-repair">
        <summary>Advanced. HA discovery options</summary>
        <div class="alerts-form-grid">
          <label>Discovery prefix
            <input type="text" name="ha_discovery_prefix" value="${v("ha_discovery_prefix", "homeassistant")}"/>
          </label>
          <label>Node ID
            <input type="text" name="ha_node_id" value="${v("ha_node_id", "solar_monitor")}"/>
          </label>
        </div>
      </details>
      <p class="settings-foot">
        Publishes a full device snapshot to <code>&lt;prefix&gt;/&lt;label&gt;/state</code> after every
        poll, plus a retained LWT at <code>&lt;prefix&gt;/_status</code>.
        See the <a href="#/docs/integrations">integrations doc</a> for the topic schema.
        Changes apply on next daemon restart.
      </p>
      <div class="alerts-form-actions">
        <button type="submit" class="btn-action btn-action--primary">Save</button>
        <button type="button" class="btn-action" data-test-mqtt>Test connection</button>
        ${enabled
          ? `<button type="button" class="btn-action alerts-icon-btn--danger" data-disable-mqtt>Disable</button>`
          : ""}
        <button type="button" class="btn-action" data-cancel-mqtt>Cancel</button>
        <span class="alerts-form-status"></span>
      </div>
    </form>`;
}

function wireMqttForm() {
  const form = document.querySelector("form[data-form='mqtt']");
  if (!form) return;
  form.addEventListener("submit", (e) => { e.preventDefault(); saveMqttConfig(form); });
  form.querySelector("[data-cancel-mqtt]")?.addEventListener("click", () => {
    integrationsState.editing = null;
    renderIntegrationsPanel();
  });
  form.querySelector("[data-test-mqtt]")?.addEventListener("click", () => testMqtt(form));
  form.querySelector("[data-disable-mqtt]")?.addEventListener("click", () => disableMqtt());
}

function _mqttPayload(form, enabled = true) {
  const pwd = form.elements["password"].value;
  return {
    enabled,
    host:                form.elements["host"].value.trim(),
    port:                parseInt(form.elements["port"].value, 10),
    username:            form.elements["username"].value.trim(),
    // Send "****" sentinel if blank — server preserves the existing one.
    password:            pwd === "" ? "****" : pwd,
    client_id:           form.elements["client_id"].value.trim(),
    topic_prefix:        form.elements["topic_prefix"].value.trim(),
    qos:                 parseInt(form.elements["qos"].value, 10),
    retain:              form.elements["retain"].checked,
    publish_per_metric:  form.elements["publish_per_metric"].checked,
    ha_discovery:        form.elements["ha_discovery"].checked,
    ha_discovery_prefix: form.elements["ha_discovery_prefix"].value.trim(),
    ha_node_id:          form.elements["ha_node_id"].value.trim(),
  };
}

async function saveMqttConfig(form) {
  const status = form.querySelector(".alerts-form-status");
  status.textContent = "Saving…"; status.className = "alerts-form-status";
  try {
    const r = await fetch("/api/exporters/mqtt/config", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(_mqttPayload(form)),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `${r.status} ${r.statusText}`);
    }
    integrationsState.editing = null;
    await refreshIntegrationsPanel();
  } catch (e) { status.textContent = e.message; status.classList.add("err"); }
}

async function testMqtt(form) {
  const status = form.querySelector(".alerts-form-status");
  status.textContent = "Connecting…"; status.className = "alerts-form-status";
  try {
    const r = await fetch("/api/exporters/mqtt/test", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(_mqttPayload(form)),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `${r.status} ${r.statusText}`);
    status.textContent = `✓ Connected to ${d.host}:${d.port}`; status.classList.add("ok");
  } catch (e) { status.textContent = e.message; status.classList.add("err"); }
}

async function disableMqtt() {
  if (!confirm("Disable the MQTT exporter? The broker still runs; we just stop publishing to it.")) return;
  try {
    const r = await fetch("/api/exporters/mqtt/config", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: false }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `${r.status} ${r.statusText}`);
    }
    integrationsState.editing = null;
    await refreshIntegrationsPanel();
  } catch (e) { alert(e.message); }
}

function renderCloudForm(cc) {
  const paired = !!cc.configured;
  const endpoint = cc.endpoint || "https://wattpost.cloud";
  return `
    <form class="alerts-form" data-form="cloud">
      <div class="alerts-form-grid">
        <label class="alerts-field-wide">Cloud endpoint
          <input type="url" name="endpoint" value="${endpoint}" required placeholder="https://wattpost.cloud"/>
        </label>
        <label>Heartbeat (minutes)
          <input type="number" name="heartbeat_minutes" value="${cc.heartbeat_minutes ?? 5}" min="1" max="60" required/>
        </label>
      </div>
      ${paired ? `
        <p class="settings-foot">
          Paired as <strong>${cc.label || "·"}</strong>${cc.appliance_id ? ` (#${cc.appliance_id})` : ""}.
          Save changes the endpoint or cadence; Send heartbeat now to confirm
          the cloud sees this appliance; Disable to unpair.
        </p>
        <div class="alerts-form-actions">
          <button type="submit" class="btn-action btn-action--primary">Save</button>
          <button type="button" class="btn-action" data-test-cloud>Send heartbeat now</button>
          <button type="button" class="btn-action alerts-icon-btn--danger" data-unpair-cloud>Disable</button>
          <button type="button" class="btn-action" data-cancel-cloud>Cancel</button>
          <span class="alerts-form-status"></span>
        </div>
        <details class="alerts-repair">
          <summary>Pair with a different account…</summary>
          <p class="settings-foot">
            Paste a pairing code from the (new) wattpost.cloud account. Submitting will
            replace the existing pairing. This appliance's old row stays on the
            previous account until that user removes it.
          </p>
          <label class="alerts-field-wide">New pairing code
            <input type="text" name="code" placeholder="e.g. 8MR7EYS6" pattern="[A-Za-z0-9]{6,12}"
                   maxlength="12" autocomplete="off" style="text-transform:uppercase; letter-spacing:.12em;"/>
          </label>
          <div class="alerts-form-actions">
            <button type="button" class="btn-action btn-action--primary" data-repair-cloud>Pair with new account</button>
          </div>
        </details>` : `
        <label class="alerts-field-wide">Pairing code
          <input type="text" name="code" placeholder="e.g. 8MR7EYS6" required pattern="[A-Za-z0-9]{6,12}"
                 maxlength="12" autocomplete="off" style="text-transform:uppercase; letter-spacing:.12em;"/>
        </label>
        <p class="settings-foot">
          Sign in at <a href="${endpoint}" target="_blank" rel="noopener">${endpoint.replace(/^https?:\/\//, "")}</a>,
          click <strong>+ Add appliance</strong>, paste the 8-character code here, hit Pair.
          Codes expire after 10 minutes.
        </p>
        <div class="alerts-form-actions">
          <button type="submit" class="btn-action btn-action--primary">Pair</button>
          <button type="button" class="btn-action" data-cancel-cloud>Cancel</button>
          <span class="alerts-form-status"></span>
        </div>`}
    </form>`;
}

function wireCloudForm() {
  const form = document.querySelector("form[data-form='cloud']");
  if (!form) return;
  const paired = !!(integrationsState.cloud || {}).configured;
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    paired ? saveCloudConfig(form) : pairCloud(form);
  });
  form.querySelector("[data-cancel-cloud]")?.addEventListener("click", () => {
    integrationsState.editing = null;
    renderIntegrationsPanel();
  });
  form.querySelector("[data-test-cloud]")?.addEventListener("click", () => testCloudHeartbeat(form));
  form.querySelector("[data-unpair-cloud]")?.addEventListener("click", () => unpairCloud(form));
  form.querySelector("[data-repair-cloud]")?.addEventListener("click", () => repairCloud(form));
}

async function repairCloud(form) {
  const status = form.querySelector(".alerts-form-status");
  const code = form.elements["code"]?.value?.trim().toUpperCase();
  if (!code) {
    status.textContent = "Enter a pairing code first."; status.classList.add("err");
    return;
  }
  status.textContent = "Pairing…"; status.className = "alerts-form-status";
  const payload = {
    endpoint: form.elements["endpoint"].value.trim(),
    code,
  };
  try {
    const r = await fetch("/api/cloud/pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `${r.status} ${r.statusText}`);
    // restart_required is only true when the server-side hot-start
    // failed. Default path: daemon's already heartbeating live, no
    // restart needed. The old hardcoded "Restart daemon" copy was a
    // regression that re-broke a documented UX gotcha.
    status.textContent = d.restart_required
      ? `✓ Paired with new account (${d.label || "·"} #${d.appliance_id}). Restart the daemon to switch over.`
      : `✓ Paired with new account (${d.label || "·"} #${d.appliance_id}). Heartbeats are live now.`;
    status.classList.add("ok");
    setTimeout(() => { integrationsState.editing = null; refreshIntegrationsPanel(); }, 2000);
  } catch (e) {
    status.textContent = e.message; status.classList.add("err");
  }
}

async function pairCloud(form) {
  const status = form.querySelector(".alerts-form-status");
  status.textContent = "Pairing…"; status.className = "alerts-form-status";
  const payload = {
    endpoint: form.elements["endpoint"].value.trim(),
    code:     form.elements["code"].value.trim().toUpperCase(),
  };
  try {
    const r = await fetch("/api/cloud/pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `${r.status} ${r.statusText}`);
    // See note above — hot-start makes the daemon live immediately.
    status.textContent = d.restart_required
      ? `✓ Paired (${d.label || "·"} #${d.appliance_id}). Restart the daemon to start heartbeats.`
      : `✓ Paired (${d.label || "·"} #${d.appliance_id}). Heartbeats are live now.`;
    status.classList.add("ok");
    setTimeout(() => { integrationsState.editing = null; refreshIntegrationsPanel(); }, 1500);
  } catch (e) {
    status.textContent = e.message;
    status.classList.add("err");
  }
}

async function saveCloudConfig(form) {
  const status = form.querySelector(".alerts-form-status");
  status.textContent = "Saving…"; status.className = "alerts-form-status";
  const payload = {
    endpoint: form.elements["endpoint"].value.trim(),
    heartbeat_minutes: parseInt(form.elements["heartbeat_minutes"].value, 10),
  };
  try {
    const r = await fetch("/api/cloud/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `${r.status} ${r.statusText}`);
    }
    integrationsState.editing = null;
    await refreshIntegrationsPanel();
  } catch (e) { status.textContent = e.message; status.classList.add("err"); }
}

async function testCloudHeartbeat(form) {
  const status = form.querySelector(".alerts-form-status");
  status.textContent = "Sending…"; status.className = "alerts-form-status";
  try {
    const r = await fetch("/api/cloud/heartbeat", { method: "POST" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `${r.status} ${r.statusText}`);
    if (d.ok) { status.textContent = "✓ Heartbeat accepted"; status.classList.add("ok"); }
    else      { status.textContent = "Cloud rejected the heartbeat. Check the daemon log"; status.classList.add("err"); }
  } catch (e) { status.textContent = e.message; status.classList.add("err"); }
}

async function unpairCloud() {
  if (!confirm("Unpair from wattpost.cloud? Cloud heartbeats stop and the local dashboard is unaffected.")) return;
  try {
    const r = await fetch("/api/cloud/unpair", { method: "POST" });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `${r.status} ${r.statusText}`);
    }
    integrationsState.editing = null;
    await refreshIntegrationsPanel();
  } catch (e) { alert(e.message); }
}

function renderWeatherForm(wc) {
  return `
    <form class="alerts-form" data-form="weather">
      <div class="alerts-form-grid">
        <label>Latitude
          <input type="number" name="lat" value="${wc.lat ?? ""}"
                 step="any" min="-90" max="90" required placeholder="51.5074"/>
        </label>
        <label>Longitude
          <input type="number" name="lon" value="${wc.lon ?? ""}"
                 step="any" min="-180" max="180" required placeholder="-0.1278"/>
        </label>
        <label>Poll every (minutes)
          <input type="number" name="poll_minutes" value="${wc.poll_minutes ?? 15}"
                 min="5" max="120" required/>
        </label>
      </div>
      <p class="settings-foot">
        No API key needed. Open-Meteo's public endpoint is free for hobbyist use.
        If you already wired up Solcast, paste the same lat/lon from your registered site.
      </p>
      <div class="alerts-form-actions">
        <button type="submit" class="btn-action btn-action--primary">Save</button>
        <button type="button" class="btn-action" data-test-weather>Test</button>
        ${wc.configured
          ? `<button type="button" class="btn-action alerts-icon-btn--danger" data-clear-weather>Disable</button>`
          : ""}
        <button type="button" class="btn-action" data-cancel-weather>Cancel</button>
        <span class="alerts-form-status"></span>
      </div>
    </form>`;
}

function wireWeatherForm() {
  const form = document.querySelector("form[data-form='weather']");
  if (!form) return;
  form.addEventListener("submit", (e) => { e.preventDefault(); saveWeatherConfig(form); });
  form.querySelector("[data-cancel-weather]")?.addEventListener("click", () => {
    integrationsState.editing = null;
    renderIntegrationsPanel();
  });
  form.querySelector("[data-test-weather]")?.addEventListener("click", () => testWeather(form));
  form.querySelector("[data-clear-weather]")?.addEventListener("click", () => clearWeather(form));
}

function _weatherPayload(form) {
  return {
    provider:     "openmeteo",
    lat:          parseFloat(form.elements["lat"].value),
    lon:          parseFloat(form.elements["lon"].value),
    poll_minutes: parseInt(form.elements["poll_minutes"].value, 10),
  };
}

async function saveWeatherConfig(form) {
  const status = form.querySelector(".alerts-form-status");
  status.textContent = "Saving…"; status.className = "alerts-form-status";
  try {
    const r = await fetch("/api/weather/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(_weatherPayload(form)),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `${r.status} ${r.statusText}`);
    }
    integrationsState.editing = null;
    await refreshIntegrationsPanel();
  } catch (e) {
    status.textContent = e.message; status.classList.add("err");
  }
}

async function testWeather(form) {
  const status = form.querySelector(".alerts-form-status");
  status.textContent = "Testing…"; status.className = "alerts-form-status";
  try {
    const r = await fetch("/api/weather/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(_weatherPayload(form)),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `${r.status} ${r.statusText}`);
    status.textContent = `✓ ${d.temperature_c}°C · ${d.cloud_cover}% cloud`;
    status.classList.add("ok");
  } catch (e) {
    status.textContent = e.message; status.classList.add("err");
  }
}

async function clearWeather() {
  if (!confirm("Disable Open-Meteo weather? Cached conditions are dropped.")) return;
  try {
    const r = await fetch("/api/weather/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: "openmeteo", lat: null, lon: null, poll_minutes: 15 }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `${r.status} ${r.statusText}`);
    }
    integrationsState.editing = null;
    await refreshIntegrationsPanel();
  } catch (e) {
    alert(e.message);
  }
}

function renderForecastForm(fc) {
  const apiKeyMasked = fc.api_key === "****";
  const provider = fc.provider || "solcast";
  // Two distinct field sets — Solcast wants API creds, Open-Meteo
  // wants array geometry. We render both and JS shows/hides based on
  // the picker. Easier to maintain than two separate render funcs.
  return `
    <form class="alerts-form" data-form="forecast">
      <div class="alerts-form-grid">
        <label>Provider
          <select name="provider">
            <option value="solcast"   ${provider === "solcast"   ? "selected" : ""}>Solcast (site-trained ML)</option>
            <option value="openmeteo" ${provider === "openmeteo" ? "selected" : ""}>Open-Meteo (irradiance estimate)</option>
          </select>
        </label>
      </div>

      <div class="alerts-form-grid" data-provider-fields="solcast" ${provider === "solcast" ? "" : "hidden"}>
        <label>API key
          <input type="password" name="api_key"
                 placeholder="${apiKeyMasked ? "(unchanged)" : "your Solcast API key"}"
                 autocomplete="off"/>
        </label>
        <label>Resource ID
          <input type="text" name="resource_id"
                 value="${fc.resource_id || ""}"
                 placeholder="e.g. abcd-1234-…"/>
        </label>
      </div>

      <div class="alerts-form-grid" data-provider-fields="openmeteo" ${provider === "openmeteo" ? "" : "hidden"}>
        <label>Latitude
          <input type="number" step="any" name="lat"
                 value="${fc.lat ?? ""}" placeholder="leave blank to inherit from weather"/>
        </label>
        <label>Longitude
          <input type="number" step="any" name="lon"
                 value="${fc.lon ?? ""}" placeholder="leave blank to inherit from weather"/>
        </label>
        <label>Array capacity (kW)
          <input type="number" step="0.1" min="0.1" name="array_kw"
                 value="${fc.array_kw ?? 1.0}" required/>
        </label>
        <label>Tilt (°, 0=flat 90=vertical)
          <input type="number" step="1" min="0" max="90" name="tilt_deg"
                 value="${fc.tilt_deg ?? 30}" required/>
        </label>
        <label>Azimuth (°, 0=south +west)
          <input type="number" step="1" min="-180" max="360" name="azimuth_deg"
                 value="${fc.azimuth_deg ?? 0}" required/>
        </label>
        <label>System efficiency (0-1)
          <input type="number" step="0.05" min="0.1" max="1.0" name="system_efficiency"
                 value="${fc.system_efficiency ?? 0.80}" required/>
        </label>
      </div>

      <div class="alerts-form-grid">
        <label>Poll every (hours)
          <input type="number" name="poll_hours"
                 value="${fc.poll_hours ?? 3}" min="1" max="24" required/>
        </label>
      </div>

      <p class="settings-foot" data-provider-help="solcast" ${provider === "solcast" ? "" : "hidden"}>
        Hobbyist tier allows 10 API calls/day per site. 3 hours = 8/day, leaves
        room for retries. Find your resource ID at
        <a href="https://toolkit.solcast.com.au/rooftop-sites" target="_blank" rel="noopener">solcast.com → My Sites</a>.
        Best quality for fixed-roof installs.
      </p>
      <p class="settings-foot" data-provider-help="openmeteo" ${provider === "openmeteo" ? "" : "hidden"}>
        Free, unlimited, lat/lon-based. No account needed. PV estimate is
        derived from solar irradiance + your array geometry. Less accurate
        than Solcast for fixed roofs (no site-specific calibration) but works
        for moving installs (vans/RVs) and as a no-setup default.
        Lat/lon left blank inherits from the weather integration's location.
      </p>

      <div class="alerts-form-actions">
        <button type="submit" class="btn-action btn-action--primary">Save</button>
        <button type="button" class="btn-action" data-test-forecast>Test</button>
        ${fc.configured
          ? `<button type="button" class="btn-action alerts-icon-btn--danger" data-clear-forecast>Disable</button>`
          : ""}
        <button type="button" class="btn-action" data-cancel-forecast>Cancel</button>
        <span class="alerts-form-status"></span>
      </div>
    </form>`;
}

function wireForecastForm() {
  const form = document.querySelector("form[data-form='forecast']");
  if (!form) return;
  form.addEventListener("submit", (e) => { e.preventDefault(); saveForecastConfig(form); });
  form.querySelector("[data-cancel-forecast]")?.addEventListener("click", () => {
    integrationsState.editing = null;
    renderIntegrationsPanel();
  });
  form.querySelector("[data-test-forecast]")?.addEventListener("click", () => testForecast(form));
  form.querySelector("[data-clear-forecast]")?.addEventListener("click", () => clearForecast(form));
  // Provider picker toggles which field-set is visible. Implemented
  // as a generic show/hide by data-attribute so adding a third
  // provider later doesn't need new switch logic here.
  const select = form.elements["provider"];
  if (select) {
    select.addEventListener("change", () => {
      const p = select.value;
      form.querySelectorAll("[data-provider-fields]").forEach(el => {
        el.hidden = el.dataset.providerFields !== p;
      });
      form.querySelectorAll("[data-provider-help]").forEach(el => {
        el.hidden = el.dataset.providerHelp !== p;
      });
    });
  }
}

function _forecastPayload(form) {
  const provider = form.elements["provider"].value;
  const base = {
    provider,
    poll_hours: parseInt(form.elements["poll_hours"].value, 10),
  };
  if (provider === "solcast") {
    const ak = form.elements["api_key"].value;
    return {
      ...base,
      api_key:     ak === "" ? "****" : ak,     // sentinel for "keep existing"
      resource_id: form.elements["resource_id"].value.trim(),
    };
  }
  // openmeteo — empty lat/lon means "inherit from weather block";
  // send null so backend can apply that fallback.
  const _f = (k) => {
    const v = form.elements[k].value.trim();
    return v === "" ? null : parseFloat(v);
  };
  return {
    ...base,
    lat:               _f("lat"),
    lon:               _f("lon"),
    array_kw:          parseFloat(form.elements["array_kw"].value),
    tilt_deg:          parseFloat(form.elements["tilt_deg"].value),
    azimuth_deg:       parseFloat(form.elements["azimuth_deg"].value),
    system_efficiency: parseFloat(form.elements["system_efficiency"].value),
  };
}

async function saveForecastConfig(form) {
  const status = form.querySelector(".alerts-form-status");
  status.textContent = "Saving…"; status.className = "alerts-form-status";
  try {
    const r = await fetch("/api/forecast/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(_forecastPayload(form)),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `${r.status} ${r.statusText}`);
    }
    integrationsState.editing = null;
    await refreshIntegrationsPanel();
  } catch (e) {
    status.textContent = e.message; status.classList.add("err");
  }
}

async function testForecast(form) {
  const status = form.querySelector(".alerts-form-status");
  status.textContent = "Testing…"; status.className = "alerts-form-status";
  try {
    const r = await fetch("/api/forecast/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(_forecastPayload(form)),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `${r.status} ${r.statusText}`);
    const peak = d.peak_ts ? new Date(d.peak_ts * 1000).toLocaleString() : null;
    status.textContent = peak
      ? `✓ ${d.points} forecast points · next peak ${(d.peak_w / 1000).toFixed(2)} kW at ${peak}`
      : `✓ ${d.points} forecast points received`;
    status.classList.add("ok");
  } catch (e) {
    status.textContent = e.message; status.classList.add("err");
  }
}

async function clearForecast(form) {
  if (!confirm("Disable the PV forecast? Existing cached data is dropped.")) return;
  const status = form.querySelector(".alerts-form-status");
  status.textContent = "Disabling…";
  try {
    const r = await fetch("/api/forecast/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: "solcast", api_key: null, resource_id: null, poll_hours: 3 }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `${r.status} ${r.statusText}`);
    }
    integrationsState.editing = null;
    await refreshIntegrationsPanel();
  } catch (e) {
    status.textContent = e.message; status.classList.add("err");
  }
}

// ---------- alerts panel (full editor) ----------
const ALERT_OP_LABEL = { lt: "<", lte: "≤", gt: ">", gte: "≥", eq: "=", neq: "≠" };
// Common metric paths the user can pick without typing. Anything else
// works too — the field falls back to a free-text input.
const METRIC_SUGGESTIONS = [
  // Bank-level metrics — the everyday "is the battery OK" stuff.
  { value: "bank.soc_pct",              label: "Battery SoC (%)" },
  { value: "bank.netW",                 label: "Bank net power (W)" },
  { value: "bank.meanV",                label: "Bank voltage (V)" },
  { value: "bank.totalRem",             label: "Bank remaining (Ah)" },
  { value: "bank.totalCap",             label: "Bank capacity (Ah)" },
  { value: "bank.time_to_go_minutes",   label: "Time to empty (minutes, shunt-only)" },
  // Cell-balance metrics (BMS-sourced) — early-warning for pack health.
  { value: "bank.worst_pack_drift_v",   label: "Worst pack drift (V)" },
  { value: "bank.cell_min_v",           label: "Lowest cell voltage (V)" },
  { value: "bank.cell_max_v",           label: "Highest cell voltage (V)" },
  // Disagreement diagnostic (#121).
  { value: "bank.source_disagreement.delta_pct",
                                        label: "Shunt-vs-BMS SoC delta (%)" },
  // Common per-device metrics (use the device label your wizard set).
  { value: "devices.charge_controller.pv_power_w",
                                        label: "PV power in (W). Charge_controller" },
  { value: "devices.charge_controller.battery_temperature_c",
                                        label: "Battery temp (°C). Charge_controller" },
  { value: "devices.charge_controller.controller_temperature_c",
                                        label: "Controller temp (°C). Charge_controller" },
  { value: "devices.charge_controller.load_status",
                                        label: "Load output state (on/off). Charge_controller" },
  // Legacy aggregate metric retained for upgraders who already used it.
  { value: "aggregate.max_cell_drift_v",label: "Max cell drift (V). Legacy alias" },
];

// One-tap rule templates. Each entry pre-fills the add-rule form with
// sensible defaults so users don't have to learn the metric-path
// schema or thinks about op/threshold defaults. The "voltage" rules
// assume a 12 V system; users on 24/48 V tweak the threshold once
// the form opens.
const ALERT_TEMPLATES = [
  { id: "low_soc",
    label: "Low SoC (< 30%)",
    rule: { name: "Low battery", metric: "bank.soc_pct", op: "lt",
            threshold: 30, severity: "warn", cooldown_seconds: 3600 } },
  { id: "critical_soc",
    label: "Critical SoC (< 15%)",
    rule: { name: "Critical battery", metric: "bank.soc_pct", op: "lt",
            threshold: 15, severity: "alarm", cooldown_seconds: 900 } },
  { id: "low_v_12v",
    label: "Low voltage (< 11.5 V, 12 V system)",
    rule: { name: "Low voltage", metric: "bank.meanV", op: "lt",
            threshold: 11.5, severity: "alarm", cooldown_seconds: 600 } },
  { id: "high_temp",
    label: "Bank over-temp (> 50 °C)",
    rule: { name: "Battery over-temperature", metric: "devices.charge_controller.battery_temperature_c",
            op: "gt", threshold: 50, severity: "alarm", cooldown_seconds: 600 } },
  { id: "cell_drift_warn",
    label: "Cell drift warning (> 100 mV)",
    rule: { name: "Cell drift warning", metric: "bank.worst_pack_drift_v",
            op: "gt", threshold: 0.10, severity: "warn", cooldown_seconds: 21600 } },
  { id: "cell_drift_alarm",
    label: "Cell drift alarm (> 200 mV)",
    rule: { name: "Cell drift alarm", metric: "bank.worst_pack_drift_v",
            op: "gt", threshold: 0.20, severity: "alarm", cooldown_seconds: 3600 } },
  { id: "soc_disagreement",
    label: "Shunt-vs-BMS disagree (>10 %)",
    rule: { name: "Shunt/BMS SoC disagree", metric: "bank.source_disagreement.delta_pct",
            op: "gt", threshold: 10, severity: "warn", cooldown_seconds: 21600 } },
];
const TRANSPORT_TYPES = [
  { value: "ntfy",            label: "ntfy",        keyField: "topic", placeholder: "my-private-topic" },
  { value: "discord_webhook", label: "Discord",     keyField: "url",   placeholder: "https://discord.com/api/webhooks/…" },
  { value: "webhook",         label: "Webhook",     keyField: "url",   placeholder: "https://example.com/hook" },
  { value: "smtp",            label: "Email (SMTP)",keyField: "host",  placeholder: "smtp.gmail.com" },
  { value: "mqtt",            label: "MQTT (LAN)",  keyField: "host",  placeholder: "127.0.0.1" },
  { value: "pushover",        label: "Pushover",    keyField: "user_key", placeholder: "u…" },
];

// Field names treated like passwords: rendered blank with "(unchanged)"
// placeholder when the server has masked them as "****", and not
// re-sent on PUT if left empty (so editing other fields doesn't blank
// the secret).
const SECRET_FIELDS = new Set(["password", "app_token", "user_key"]);

let alertsState = { rules: [], transports: [], quietHours: null, editing: null };  // editing: {type:'rule'|'transport'|'quiet_hours', id, mode:'edit'|'add'}

async function refreshAlertsPanel() {
  const host = $("#settings-alerts");
  if (!host) return;
  try {
    const data = await api("/api/alerts");
    alertsState.rules = data.rules || [];
    alertsState.transports = data.transports || [];
    alertsState.quietHours = data.quiet_hours || null;
  } catch (e) {
    host.innerHTML = `<div class="settings-empty">Could not load alerts: ${e.message}</div>`;
    return;
  }
  renderAlertsPanel();
}

function renderAlertsPanel() {
  const host = $("#settings-alerts");
  if (!host) return;
  const transportIds = alertsState.transports.map(t => t.id);

  let html = renderQuietHoursBlock();
  html += `<div class="alerts-sub-head"><h4>Alert rules</h4>
    <button class="alerts-add-btn" data-add="rule">+ Add rule</button></div>`;

  // One-tap templates row — pre-fills the add-rule form with sensible
  // thresholds for the most common alarms. Hidden when the form is
  // already open so we don't clutter the editing flow.
  if (!(alertsState.editing?.type === "rule" && alertsState.editing.mode === "add")) {
    html += `<div class="alerts-templates">
      <span class="settings-foot">Quick templates:</span>
      ${ALERT_TEMPLATES.map(t => `
        <button class="alerts-template-chip" data-alert-template="${t.id}">${escHtml(t.label)}</button>
      `).join("")}
    </div>`;
  }

  if (alertsState.editing?.type === "rule" && alertsState.editing.mode === "add") {
    // A `prefill` on the editing state means the user clicked a
    // quick-template chip — pass the template rule into the form
    // so all the fields land pre-filled.
    html += renderRuleForm(alertsState.editing.prefill || null, transportIds);
  }
  if (!alertsState.rules.length && !(alertsState.editing?.type === "rule" && alertsState.editing.mode === "add")) {
    html += `<div class="settings-empty">No rules yet. Click "+ Add rule" to create one.</div>`;
  }
  for (const r of alertsState.rules) {
    if (alertsState.editing?.type === "rule" && alertsState.editing.id === r.id) {
      html += renderRuleForm(r, transportIds);
    } else {
      html += renderRuleRow(r, transportIds);
    }
  }

  html += `<div class="alerts-sub-head"><h4>Transports</h4>
    <button class="alerts-add-btn" data-add="transport">+ Add transport</button></div>`;
  if (alertsState.editing?.type === "transport" && alertsState.editing.mode === "add") {
    html += renderTransportForm(null);
  }
  if (!alertsState.transports.length && !(alertsState.editing?.type === "transport" && alertsState.editing.mode === "add")) {
    html += `<div class="settings-empty">No transports yet. Add one before creating rules.</div>`;
  }
  for (const t of alertsState.transports) {
    if (alertsState.editing?.type === "transport" && alertsState.editing.id === t.id) {
      html += renderTransportForm(t);
    } else {
      html += renderTransportRow(t);
    }
  }

  host.innerHTML = html;
  wireAlertsHandlers();
}

function renderRuleRow(r, transportIds) {
  const opLbl = ALERT_OP_LABEL[r.op] || r.op;
  const cond = `${r.metric} ${opLbl} ${r.threshold}`;
  const lastFired = r.last_fired_ts ? `fired ${fmt.ago(r.last_fired_ts)}` : "never fired";
  const cooldown = `cooldown ${Math.round(r.cooldown_seconds / 60)} min`;
  const tlist = (r.transports || [])
    .map(tid => transportIds.includes(tid) ? tid : `${tid} ⚠ missing`)
    .join(", ") || "⚠ none";
  return `
    <div class="alerts-row alerts-row--${r.severity}" data-rule="${r.id}">
      <div class="alerts-row-main">
        <div class="alerts-row-title">
          <span class="alerts-row-name">${r.name}</span>
          <span class="alerts-row-cond">${cond}</span>
        </div>
        <div class="alerts-row-meta">
          <span class="alerts-row-tag alerts-row-tag--${r.severity}">${r.severity}</span>
          <span class="alerts-row-tag">→ ${tlist}</span>
          <span class="alerts-row-tag">${cooldown}</span>
          <span class="alerts-row-tag">${lastFired}</span>
        </div>
      </div>
      <div class="alerts-row-action">
        <button class="alerts-test-btn" data-test-rule="${r.id}">Test</button>
        <button class="alerts-icon-btn" data-edit-rule="${r.id}" title="Edit">✎</button>
        <button class="alerts-icon-btn alerts-icon-btn--danger" data-delete-rule="${r.id}" title="Delete">×</button>
        <span class="alerts-test-status" data-rule="${r.id}"></span>
      </div>
    </div>`;
}

function renderRuleForm(r, transportIds) {
  const editing = !!r;
  const id = r?.id || "";
  const name = r?.name || "";
  const metric = r?.metric || METRIC_SUGGESTIONS[0].value;
  const op = r?.op || "lt";
  const threshold = r?.threshold ?? 30;
  const severity = r?.severity || "warn";
  const cooldownMin = Math.round((r?.cooldown_seconds ?? 1800) / 60);
  const ts = new Set(r?.transports || []);
  return `
    <form class="alerts-form" data-form="rule" data-original-id="${id}">
      <div class="alerts-form-grid">
        <label>ID
          <input type="text" name="id" value="${id}" pattern="[a-zA-Z0-9_\\-]+" required ${editing ? "readonly" : ""} />
        </label>
        <label>Name
          <input type="text" name="name" value="${name}" required />
        </label>
        <label>Metric
          <select name="metric">
            ${METRIC_SUGGESTIONS.map(m =>
              `<option value="${m.value}" ${m.value === metric ? "selected" : ""}>${m.label} · ${m.value}</option>`).join("")}
            <option value="__custom__" ${METRIC_SUGGESTIONS.some(m => m.value === metric) ? "" : "selected"}>Custom…</option>
          </select>
          <input type="text" name="metric_custom" value="${METRIC_SUGGESTIONS.some(m => m.value === metric) ? "" : metric}" placeholder="e.g. devices.battery_0.cell_drift_v" />
        </label>
        <label>Op
          <select name="op">
            ${Object.keys(ALERT_OP_LABEL).map(o =>
              `<option value="${o}" ${o === op ? "selected" : ""}>${o} (${ALERT_OP_LABEL[o]})</option>`).join("")}
          </select>
        </label>
        <label>Threshold
          <input type="number" name="threshold" value="${threshold}" step="any" required />
        </label>
        <label>Severity
          <select name="severity">
            <option value="warn"  ${severity === "warn" ? "selected" : ""}>Warn</option>
            <option value="alarm" ${severity === "alarm" ? "selected" : ""}>Alarm</option>
          </select>
        </label>
        <label>Cooldown (min)
          <input type="number" name="cooldown_min" value="${cooldownMin}" min="0" step="1" required />
        </label>
      </div>
      <fieldset class="alerts-form-transports">
        <legend>Send via</legend>
        ${transportIds.length === 0
          ? `<p class="settings-foot">No transports configured yet. Add one below first.</p>`
          : transportIds.map(tid =>
              `<label class="alerts-checkbox"><input type="checkbox" name="transport" value="${tid}" ${ts.has(tid) ? "checked" : ""}/>${tid}</label>`).join("")}
      </fieldset>
      <div class="alerts-form-actions">
        <button type="submit" class="btn-action btn-action--primary">${editing ? "Save" : "Create rule"}</button>
        <button type="button" class="btn-action" data-cancel-edit>Cancel</button>
        <span class="alerts-form-status"></span>
      </div>
    </form>`;
}

function renderQuietHoursBlock() {
  const qh = alertsState.quietHours;
  const editing = alertsState.editing?.type === "quiet_hours";
  const summary = qh
    ? `${pad2(qh.start_hour)}:00 → ${pad2(qh.end_hour)}:00 · warn-severity buffers until the window ends`
    : "Off · every alert pages immediately, day or night";
  if (editing) return renderQuietHoursForm(qh);
  return `
    <div class="alerts-sub-head">
      <h4>Quiet hours</h4>
      <button class="alerts-add-btn" data-edit-quiet>${qh ? "Edit" : "Configure"}</button>
    </div>
    <div class="alerts-row alerts-row--quiet">
      <div class="alerts-row-main">
        <div class="alerts-row-title">
          <span class="alerts-row-name">${qh ? "Enabled" : "Disabled"}</span>
          <span class="alerts-row-cond">${summary}</span>
        </div>
        <div class="alerts-row-meta">
          <span class="alerts-row-tag">alarm severity always pages through</span>
        </div>
      </div>
    </div>`;
}

function pad2(n) { return String(n).padStart(2, "0"); }

function renderQuietHoursForm(qh) {
  const enabled = !!qh;
  const start = qh?.start_hour ?? 22;
  const end   = qh?.end_hour   ?? 7;
  return `
    <div class="alerts-sub-head"><h4>Quiet hours</h4></div>
    <form class="alerts-form" data-form="quiet_hours">
      <label class="alerts-checkbox alerts-quiet-enable">
        <input type="checkbox" name="enabled" ${enabled ? "checked" : ""}/>
        Buffer warn-severity alerts during a daily quiet window
      </label>
      <div class="alerts-form-grid alerts-quiet-grid">
        <label>Start hour
          <input type="number" name="start_hour" min="0" max="23" value="${start}" required/>
        </label>
        <label>End hour
          <input type="number" name="end_hour" min="0" max="23" value="${end}" required/>
        </label>
      </div>
      <p class="settings-foot">
        Hours are in local time (0-23). Overnight windows work. Set
        start &gt; end (e.g. 22 → 7) for a "from 10pm to 7am" buffer.
        Alarm-severity alerts always page through, even inside the
        window. Changes apply on next daemon restart.
      </p>
      <div class="alerts-form-actions">
        <button type="submit" class="btn-action btn-action--primary">Save</button>
        <button type="button" class="btn-action" data-cancel-edit>Cancel</button>
        <span class="alerts-form-status"></span>
      </div>
    </form>`;
}

function renderTransportRow(t) {
  const cfg = t.config || {};
  const keyField = TRANSPORT_TYPES.find(x => x.value === t.type)?.keyField;
  const keyValue = keyField ? cfg[keyField] : "";
  const aliveTag = t.alive
    ? `<span class="alerts-row-tag alerts-row-tag--ok">active</span>`
    : `<span class="alerts-row-tag alerts-row-tag--warn">restart pending</span>`;
  return `
    <div class="alerts-row" data-transport="${t.id}">
      <div class="alerts-row-main">
        <div class="alerts-row-title">
          <span class="alerts-row-name">${t.id}</span>
          <span class="alerts-row-cond">${t.type}${keyValue ? ` · ${keyField}: ${keyValue}` : ""}</span>
        </div>
        <div class="alerts-row-meta">${aliveTag}</div>
      </div>
      <div class="alerts-row-action">
        <button class="alerts-icon-btn" data-edit-transport="${t.id}" title="Edit">✎</button>
        <button class="alerts-icon-btn alerts-icon-btn--danger" data-delete-transport="${t.id}" title="Delete">×</button>
      </div>
    </div>`;
}

function renderTransportForm(t) {
  const editing = !!t;
  const type = t?.type || "ntfy";
  const id = t?.id || "";
  const cfg = t?.config || {};
  return `
    <form class="alerts-form" data-form="transport" data-original-id="${id}">
      <div class="alerts-form-grid">
        <label>ID
          <input type="text" name="id" value="${id}" pattern="[a-zA-Z0-9_\\-]+" required ${editing ? "readonly" : ""} />
        </label>
        <label>Type
          <select name="type" ${editing ? "disabled" : ""}>
            ${TRANSPORT_TYPES.map(tt =>
              `<option value="${tt.value}" ${tt.value === type ? "selected" : ""}>${tt.label}</option>`).join("")}
          </select>
        </label>
      </div>
      <div class="alerts-form-grid alerts-form-grid--full" data-transport-fields>
        ${transportTypeFields(type, cfg)}
      </div>
      <div class="alerts-form-actions">
        <button type="submit" class="btn-action btn-action--primary">${editing ? "Save" : "Create transport"}</button>
        <button type="button" class="btn-action" data-cancel-edit>Cancel</button>
        <span class="alerts-form-status"></span>
      </div>
    </form>`;
}

function transportTypeFields(type, cfg) {
  const v = (k, d = "") => cfg[k] != null ? String(cfg[k]) : d;
  switch (type) {
    case "ntfy":
      return `
        <label>Topic <input type="text" name="topic" value="${v("topic")}" required placeholder="something-obscure-pick-me"/></label>
        <label>Server <input type="text" name="server" value="${v("server", "https://ntfy.sh")}" placeholder="https://ntfy.sh"/></label>`;
    case "discord_webhook":
      return `
        <label class="alerts-field-wide">Webhook URL <input type="url" name="url" value="${v("url")}" required placeholder="https://discord.com/api/webhooks/…"/></label>`;
    case "webhook":
      return `
        <label class="alerts-field-wide">URL <input type="url" name="url" value="${v("url")}" required placeholder="https://example.com/hook"/></label>
        <label>Method <input type="text" name="method" value="${v("method", "POST")}" placeholder="POST"/></label>`;
    case "smtp":
      return `
        <label>Host <input type="text" name="host" value="${v("host")}" required placeholder="smtp.gmail.com"/></label>
        <label>Port <input type="number" name="port" value="${v("port", "587")}" required /></label>
        <label>Username <input type="text" name="username" value="${v("username")}" /></label>
        <label>Password <input type="password" name="password" value="${cfg.password === "****" ? "" : v("password")}" placeholder="${cfg.password === "****" ? "(unchanged)" : ""}"/></label>
        <label class="alerts-field-wide">From <input type="text" name="from_addr" value="${v("from_addr")}" placeholder="WattPost <alerts@example.com>"/></label>
        <label class="alerts-field-wide">To (comma-separated) <input type="text" name="to_addrs" value="${(cfg.to_addrs || []).join(", ")}" required /></label>
        <label class="alerts-checkbox"><input type="checkbox" name="use_starttls" ${cfg.use_starttls !== false ? "checked" : ""}/> STARTTLS</label>
        <label class="alerts-checkbox"><input type="checkbox" name="use_ssl" ${cfg.use_ssl ? "checked" : ""}/> SSL (port 465)</label>`;
    case "mqtt":
      return `
        <label>Broker host <input type="text" name="host" value="${v("host", "127.0.0.1")}" required placeholder="127.0.0.1"/></label>
        <label>Port <input type="number" name="port" value="${v("port", "1883")}" required /></label>
        <label>Username <input type="text" name="username" value="${v("username")}" /></label>
        <label>Password <input type="password" name="password" value="${cfg.password === "****" ? "" : v("password")}" placeholder="${cfg.password === "****" ? "(unchanged)" : ""}"/></label>
        <label class="alerts-field-wide">Topic prefix <input type="text" name="topic_prefix" value="${v("topic_prefix", "wattpost/alerts")}" placeholder="wattpost/alerts"/></label>
        <label>QoS <input type="number" name="qos" value="${v("qos", "1")}" min="0" max="2"/></label>
        <label class="alerts-checkbox"><input type="checkbox" name="retain" ${cfg.retain ? "checked" : ""}/> Retain</label>`;
    case "pushover":
      return `
        <label>App token <input type="password" name="app_token" value="${cfg.app_token === "****" ? "" : v("app_token")}" placeholder="${cfg.app_token === "****" ? "(unchanged)" : "azGDORePK8gMaC0…"}" required/></label>
        <label>User key  <input type="password" name="user_key"  value="${cfg.user_key  === "****" ? "" : v("user_key")}"  placeholder="${cfg.user_key  === "****" ? "(unchanged)" : "uQiRzpo4DXghDmr…"}" required/></label>
        <label>Device (optional) <input type="text" name="device" value="${v("device")}" placeholder="leave blank for all devices"/></label>`;
    default:
      return "";
  }
}

function wireAlertsHandlers() {
  const host = $("#settings-alerts");
  if (!host) return;
  host.querySelectorAll("[data-add]").forEach(btn => {
    btn.addEventListener("click", () => {
      alertsState.editing = { type: btn.dataset.add, mode: "add", id: null };
      renderAlertsPanel();
    });
  });
  // Quick-template chips — open the add-rule form pre-filled with the
  // template's metric, op, threshold, severity, cooldown.
  host.querySelectorAll("[data-alert-template]").forEach(btn => {
    btn.addEventListener("click", () => {
      const tpl = ALERT_TEMPLATES.find(t => t.id === btn.dataset.alertTemplate);
      if (!tpl) return;
      alertsState.editing = {
        type: "rule", mode: "add", id: null,
        prefill: { ...tpl.rule },
      };
      renderAlertsPanel();
    });
  });
  host.querySelectorAll("[data-cancel-edit]").forEach(btn => {
    btn.addEventListener("click", () => {
      alertsState.editing = null;
      renderAlertsPanel();
    });
  });
  host.querySelectorAll("[data-edit-rule]").forEach(btn => {
    btn.addEventListener("click", () => {
      alertsState.editing = { type: "rule", mode: "edit", id: btn.dataset.editRule };
      renderAlertsPanel();
    });
  });
  host.querySelectorAll("[data-edit-transport]").forEach(btn => {
    btn.addEventListener("click", () => {
      alertsState.editing = { type: "transport", mode: "edit", id: btn.dataset.editTransport };
      renderAlertsPanel();
    });
  });
  host.querySelectorAll("[data-delete-rule]").forEach(btn => {
    btn.addEventListener("click", () => deleteRule(btn.dataset.deleteRule));
  });
  host.querySelectorAll("[data-delete-transport]").forEach(btn => {
    btn.addEventListener("click", () => deleteTransport(btn.dataset.deleteTransport));
  });
  host.querySelectorAll("[data-test-rule]").forEach(btn => {
    btn.addEventListener("click", () => testAlert(btn.dataset.testRule));
  });
  host.querySelectorAll("form[data-form='rule']").forEach(f => {
    // Custom metric input toggles based on the dropdown
    const sel = f.elements["metric"];
    const custom = f.elements["metric_custom"];
    const toggleCustom = () => { custom.hidden = sel.value !== "__custom__"; };
    sel.addEventListener("change", toggleCustom);
    toggleCustom();
    f.addEventListener("submit", (e) => { e.preventDefault(); submitRuleForm(f); });
  });
  host.querySelectorAll("form[data-form='transport']").forEach(f => {
    const sel = f.elements["type"];
    const fieldsBox = f.querySelector("[data-transport-fields]");
    sel.addEventListener("change", () => {
      fieldsBox.innerHTML = transportTypeFields(sel.value, {});
    });
    f.addEventListener("submit", (e) => { e.preventDefault(); submitTransportForm(f); });
  });
  host.querySelectorAll("[data-edit-quiet]").forEach(btn => {
    btn.addEventListener("click", () => {
      alertsState.editing = { type: "quiet_hours", mode: "edit", id: null };
      renderAlertsPanel();
    });
  });
  host.querySelectorAll("form[data-form='quiet_hours']").forEach(f => {
    // Disabling the toggle greys the hour inputs so the form's intent
    // is obvious — saving with the box unchecked clears the window.
    const enable = f.elements["enabled"];
    const sync = () => {
      const dis = !enable.checked;
      f.elements["start_hour"].disabled = dis;
      f.elements["end_hour"].disabled   = dis;
    };
    enable.addEventListener("change", sync);
    sync();
    f.addEventListener("submit", (e) => { e.preventDefault(); submitQuietHoursForm(f); });
  });
}

async function submitQuietHoursForm(form) {
  const status = form.querySelector(".alerts-form-status");
  status.textContent = "Saving…"; status.className = "alerts-form-status";
  const enabled = form.elements["enabled"].checked;
  const payload = enabled
    ? { start_hour: parseInt(form.elements["start_hour"].value, 10),
        end_hour:   parseInt(form.elements["end_hour"].value, 10) }
    : { start_hour: null, end_hour: null };
  try {
    const r = await fetch("/api/alerts/quiet_hours", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `${r.status} ${r.statusText}`);
    }
    alertsState.editing = null;
    await refreshAlertsPanel();
  } catch (e) {
    status.textContent = e.message;
    status.classList.add("err");
  }
}

async function submitRuleForm(form) {
  const status = form.querySelector(".alerts-form-status");
  status.textContent = "Saving…"; status.className = "alerts-form-status";
  const transports = Array.from(form.querySelectorAll("input[name='transport']:checked"))
    .map(el => el.value);
  const metricSel = form.elements["metric"].value;
  const metric = metricSel === "__custom__" ? form.elements["metric_custom"].value.trim() : metricSel;
  const payload = {
    id: form.elements["id"].value.trim(),
    name: form.elements["name"].value.trim(),
    metric,
    op: form.elements["op"].value,
    threshold: parseFloat(form.elements["threshold"].value),
    severity: form.elements["severity"].value,
    cooldown_seconds: parseInt(form.elements["cooldown_min"].value, 10) * 60,
    transports,
  };
  const editing = !!form.dataset.originalId;
  const url = editing ? `/api/alerts/rules/${encodeURIComponent(payload.id)}` : "/api/alerts/rules";
  const method = editing ? "PUT" : "POST";
  try {
    const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `${r.status} ${r.statusText}`);
    }
    alertsState.editing = null;
    await refreshAlertsPanel();
  } catch (e) {
    status.textContent = e.message;
    status.classList.add("err");
  }
}

async function submitTransportForm(form) {
  const status = form.querySelector(".alerts-form-status");
  status.textContent = "Saving…"; status.className = "alerts-form-status";
  const type = form.elements["type"].value;
  const editing = !!form.dataset.originalId;
  const extra = {};
  const fields = form.querySelectorAll("[data-transport-fields] input");
  fields.forEach(el => {
    if (el.type === "checkbox") {
      extra[el.name] = el.checked;
    } else if (el.value !== "" || (editing && SECRET_FIELDS.has(el.name))) {
      // On edit, an empty secret means "leave the existing one alone" —
      // skip it so the PUT doesn't blank it out. On create, fall through
      // so an empty value is sent (and server-side validation can flag it).
      if (editing && SECRET_FIELDS.has(el.name) && el.value === "") return;
      if (el.name === "to_addrs") {
        extra[el.name] = el.value.split(",").map(s => s.trim()).filter(Boolean);
      } else if (el.type === "number") {
        extra[el.name] = el.value === "" ? null : Number(el.value);
      } else {
        extra[el.name] = el.value;
      }
    }
  });
  const payload = {
    id: form.elements["id"].value.trim(),
    type,
    extra,
  };
  const url = editing ? `/api/alerts/transports/${encodeURIComponent(payload.id)}` : "/api/alerts/transports";
  const method = editing ? "PUT" : "POST";
  try {
    const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `${r.status} ${r.statusText}`);
    }
    const result = await r.json();
    alertsState.editing = null;
    await refreshAlertsPanel();
    if (result.restart_required) showAlertsRestartBanner();
  } catch (e) {
    status.textContent = e.message;
    status.classList.add("err");
  }
}

async function deleteRule(id) {
  if (!confirm(`Delete rule "${id}"?`)) return;
  try {
    const r = await fetch(`/api/alerts/rules/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `${r.status} ${r.statusText}`);
    }
    await refreshAlertsPanel();
  } catch (e) { alert(e.message); }
}

async function deleteTransport(id) {
  if (!confirm(`Delete transport "${id}"?\nRules referencing it must be removed first.`)) return;
  try {
    const r = await fetch(`/api/alerts/transports/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `${r.status} ${r.statusText}`);
    }
    await refreshAlertsPanel();
    showAlertsRestartBanner();
  } catch (e) { alert(e.message); }
}

function showAlertsRestartBanner() {
  // Reuse the wizard's restart banner pattern. It lives in the Setup
  // route; the user will see it when they next visit Setup. We also
  // show a transient notice at the top of the alerts panel.
  const host = $("#settings-alerts");
  if (!host) return;
  let banner = host.querySelector(".alerts-restart-banner");
  if (banner) return;
  banner = document.createElement("div");
  banner.className = "alerts-restart-banner";
  banner.textContent = "Transport changes will take effect after the daemon restarts.";
  host.prepend(banner);
}

async function testAlert(ruleId) {
  const btn    = document.querySelector(`[data-test-rule="${ruleId}"]`);
  const status = document.querySelector(`.alerts-test-status[data-rule="${ruleId}"]`);
  if (!btn || !status) return;
  btn.disabled = true;
  status.textContent = "sending…"; status.className = "alerts-test-status";
  try {
    const r = await fetch(`/api/alerts/${encodeURIComponent(ruleId)}/test`, { method: "POST" });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `${r.status} ${r.statusText}`);
    }
    status.textContent = "sent"; status.classList.add("ok");
  } catch (e) {
    status.textContent = e.message; status.classList.add("err");
  } finally {
    btn.disabled = false;
    setTimeout(() => { if (status) status.textContent = ""; }, 4000);
  }
}

// ---------- wiring ----------
// Theme picker — System / Dark / Light.
document.querySelectorAll(".theme-opt").forEach(btn => {
  btn.addEventListener("click", () => applyTheme(btn.dataset.themePref));
});
applyTheme(themePref());  // paints meta-color + button selection state

// Kiosk default toggle + exit button. The default-on-this-device flag
// only triggers a redirect if the user landed without an explicit route
// in the URL — otherwise an inbound link to /#/history or /#/devices
// would be silently stomped on every refresh.
const kioskToggle = $("#kiosk-default-toggle");
if (kioskToggle) {
  kioskToggle.checked = kioskDefault();
  kioskToggle.addEventListener("change", () => {
    setKioskDefault(kioskToggle.checked);
  });
}
const kioskExitBtn = $("#kiosk-exit");
if (kioskExitBtn) {
  // Hide Exit Kiosk for two cases:
  //   - kiosk-token share URL (#150): visitor has no session at
  //     all, would land in the dashboard with no auth and see
  //     401s everywhere — bad UX.
  //   - Cloud-broker view: customer IS authed via the cloud, but
  //     the broker dashboard at xyz.wattpost.cloud should be
  //     SoC-kiosk-only. Full Settings / Devices / Setup chrome
  //     belongs in app.wattpost.cloud, not on the appliance
  //     broker URL. Keeps the broker view focused.
  // The Exit button still renders on direct local access
  // (192.168.x.x / wattpost.local) where the van/cabin operator
  // genuinely wants to flip between SoC kiosk and the full UI.
  if (KIOSK_KEY_PARAM || IS_BROKER_VIEW) {
    kioskExitBtn.hidden = true;
  }
  kioskExitBtn.addEventListener("click", () => {
    // Belt-and-braces: even if the button is somehow clicked while
    // KIOSK_KEY_PARAM or IS_BROKER_VIEW is set, just stay on /kiosk
    // rather than navigating into chrome that should be hidden.
    if (KIOSK_KEY_PARAM) {
      window.location.href = "/";
    } else if (IS_BROKER_VIEW) {
      // Broker — stay put, no breakout.
      return;
    } else {
      window.location.hash = "#/";
    }
  });
}
const restartBtn = $("#restart-daemon-btn");
if (restartBtn) {
  restartBtn.addEventListener("click", restartDaemon);
}
// Rotate web password — Settings → System → "Rotate web password".
// Generates a fresh ~16-char random password on the appliance and
// shows it once. Docker users specifically asked for this — they
// don't have wattpost-config TUI access on the host. Old hash is
// replaced atomically; existing sessions on OTHER browsers stay
// valid until they natural-expire (so you don't sign yourself out
// of the tab you're rotating from).
const rotatePwBtn = $("#rotate-pw-btn");
if (rotatePwBtn) {
  rotatePwBtn.addEventListener("click", async () => {
    if (!confirm(
      "Rotate the local web password?\n\n" +
      "You'll be shown the new password ONCE. Save it before " +
      "closing this dialog. Existing browser sessions stay valid " +
      "until they expire (30 days)."
    )) return;
    const out = document.getElementById("rotate-pw-result");
    rotatePwBtn.disabled = true;
    try {
      const r = await fetch("/api/system/web-password/rotate", {
        method: "POST", credentials: "include",
      });
      if (!r.ok) {
        const t = await r.text();
        throw new Error(`HTTP ${r.status}: ${t}`);
      }
      const j = await r.json();
      if (out) {
        out.hidden = false;
        out.innerHTML = `
          <div class="rotate-pw-label">New password. Save it now:</div>
          <code class="rotate-pw-code">${j.password.replace(/[<&>]/g, c => ({"<":"&lt;","&":"&amp;",">":"&gt;"}[c]))}</code>
          <button class="rotate-pw-copy" type="button">Copy</button>
          <div class="rotate-pw-foot">
            Also written to <code>/etc/wattpost/web-password</code>
            inside the container (host bind-mount on Docker installs).
          </div>`;
        out.querySelector(".rotate-pw-copy")?.addEventListener("click", () => {
          navigator.clipboard?.writeText(j.password).catch(() => {});
        });
      }
    } catch (e) {
      alert("Couldn't rotate the password:\n\n" + (e.message || e));
    } finally {
      rotatePwBtn.disabled = false;
    }
  });
}
// Sign-out button — lives inside Settings → System (the only place
// where being signed in actually matters; mutations require a
// session, everything else on LAN is anonymous read-only). Hidden
// when the user isn't authed (no session to end). Sign-IN is
// triggered implicitly by tapping Settings or Setup — the SPA
// router (AUTH_GATED_ROUTES) bounces unauthed visitors to /login.
(function wireSignout() {
  const signoutBtn = document.getElementById("signout-btn");
  if (!signoutBtn) return;
  if (document.body.classList.contains("is-demo")) return;

  signoutBtn.addEventListener("click", async () => {
    try {
      await fetch("/api/logout", {
        method: "POST", credentials: "same-origin",
      });
    } catch (_) { /* network error. Still reload */ }
    // Land on the dashboard (read-only anonymous). If the user
    // re-opens Settings the auth gate will bounce them to /login.
    window.location.href = "/";
  });

  // Hide until we know the user is actually signed in. Avoids
  // showing a Sign-out button to an anonymous LAN viewer who's
  // never authed in the first place. Also hides for broker-origin
  // sessions — the broker re-injects an HMAC header on every
  // request from the user's wattpost.cloud session, so there's no
  // appliance-side session for "Sign out" to actually end. The
  // only way to sign out of a broker view is to log out of
  // wattpost.cloud itself; rendering a button here that does
  // nothing useful is just confusing.
  signoutBtn.hidden = true;
  fetch("/api/system/auth-status", { credentials: "same-origin" })
    .then((r) => r.ok ? r.json() : null)
    .then((data) => {
      const authed = !!(data && data.authed);
      const origin = data && data.origin;
      if (typeof window._setAuthState === "function") window._setAuthState(authed, origin);
      if (authed && origin !== "broker") signoutBtn.hidden = false;
      // Broker-origin: skip SSE, use polling. iOS Safari serialises
      // its small per-host HTTP connection pool around a long-lived
      // EventSource through the Cloudflare tunnel, queuing every
      // /api/* fetch behind the SSE — dashboard sits at "connecting"
      // forever even though the data is one request away. Polling
      // every 5 s is plenty for a remote view and dodges the trap.
      // Local LAN keeps SSE — it works fine on a fresh connection.
      if (authed && origin === "broker") {
        if (typeof eventStream !== "undefined" && eventStream) {
          try { eventStream.close(); } catch (_) {}
          eventStream = null;
        }
        if (typeof startPollingFallback === "function") startPollingFallback();
      }
    })
    .catch(() => { /* leave hidden on network error */ });
})();
const diagRefreshBtn = $("#diag-refresh");
if (diagRefreshBtn) diagRefreshBtn.addEventListener("click", refreshDiagLog);

// ---------- kiosk share URL (Settings → Kiosk share URL) ----------
// Surfaces the per-appliance kiosk_token as a copy-paste-able URL
// + lets the user rotate it (revoke a leaked URL with one click).
// Hidden until the cloud tunnel is provisioned — share URL needs
// a slug to point anywhere.
(function wireKioskShare() {
  const block    = document.getElementById("kiosk-block");
  const input    = document.getElementById("kiosk-url");
  const copyBtn  = document.getElementById("kiosk-copy-btn");
  const rotBtn   = document.getElementById("kiosk-rotate-btn");
  const msg      = document.getElementById("kiosk-msg");
  if (!block || !input || !copyBtn || !rotBtn) return;

  async function load() {
    try {
      const r = await fetch("/api/system/kiosk", { credentials: "same-origin" });
      if (!r.ok) return;  // unauthed user. Settings tab gate will redirect anyway
      const data = await r.json();
      block.hidden = false;
      if (data.share_url) {
        input.value = data.share_url;
        copyBtn.disabled = false;
      } else {
        input.value = "";
        input.placeholder = "No cloud tunnel. Pair the appliance first";
        copyBtn.disabled = true;
      }
    } catch (_) { /* network error. Leave block hidden */ }
  }

  copyBtn.addEventListener("click", async () => {
    if (!input.value) return;
    try {
      await navigator.clipboard.writeText(input.value);
      msg.textContent = "Copied ✓";
      setTimeout(() => { msg.textContent = ""; }, 1500);
    } catch (_) {
      input.select();
      msg.textContent = "Press Ctrl+C / Cmd+C to copy";
    }
  });

  rotBtn.addEventListener("click", async () => {
    if (!confirm(
      "Rotate the kiosk token?\n\n" +
      "The current share URL stops working immediately. Anyone you " +
      "previously shared it with will need the new URL. Use this if " +
      "the URL leaked or you want to revoke a specific share."
    )) return;
    rotBtn.disabled = true;
    msg.textContent = "Rotating…";
    try {
      const r = await fetch("/api/system/kiosk/rotate", {
        method: "POST", credentials: "same-origin",
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      if (data.share_url) {
        input.value = data.share_url;
        copyBtn.disabled = false;
      }
      msg.textContent = "Rotated ✓. Old URL is dead";
      setTimeout(() => { msg.textContent = ""; }, 3000);
    } catch (e) {
      msg.textContent = `Rotate failed: ${e.message}`;
    } finally {
      rotBtn.disabled = false;
    }
  });

  // Lazy-load when Settings tab is opened, not on every page load.
  // Re-uses the same hashchange firing setRoute() — when route ==
  // settings AND we haven't loaded yet, fetch once.
  let loaded = false;
  function maybeLoad() {
    if (loaded) return;
    if (!document.body.dataset.route || document.body.dataset.route === "settings") {
      loaded = true;
      load();
    }
  }
  window.addEventListener("hashchange", maybeLoad);
  maybeLoad();
})();

// Status pill legend popover — click the pill to open, click outside or
// the close button to dismiss.
const statusEl = $("#status");
const legendEl = $("#status-legend");
if (statusEl && legendEl) {
  const close = () => { legendEl.hidden = true; };
  const open  = () => { legendEl.hidden = false; };
  statusEl.addEventListener("click", (e) => {
    e.stopPropagation();
    legendEl.hidden ? open() : close();
  });
  legendEl.querySelector(".status-legend-close")?.addEventListener("click", close);
  document.addEventListener("click", (e) => {
    if (!legendEl.hidden && !legendEl.contains(e.target) && e.target !== statusEl) {
      close();
    }
  });
  legendEl.querySelector(".status-legend-link")?.addEventListener("click", close);
}
// If the URL path is /kiosk (real server route, hits anonymously even
// when local-auth is on), flip the SPA into kiosk mode by setting the
// hash before initial setRoute runs. Bookmarkable, shareable, and the
// auth middleware whitelists this exact path.
if (window.location.pathname === "/kiosk") {
  window.location.hash = "#/kiosk";
}
// If this device is set to default-to-kiosk and the URL has no explicit
// hash, redirect before the initial setRoute runs.
else if (kioskDefault() && (!window.location.hash || window.location.hash === "#" || window.location.hash === "#/")) {
  window.location.hash = "#/kiosk";
}

$("#sel-device").addEventListener("change", () => onDeviceChanged());
$("#sel-metric").addEventListener("change", refreshChart);
// Compare-packs toggle: persist the preference and re-render. The
// checkbox is greyed out when the eligibility conditions don't hold
// (need a smart_battery selected + >=2 packs), but we still update
// state so flipping back to an eligible device remembers the choice.
$("#chart-compare-packs")?.addEventListener("change", (e) => {
  compareMode = !!e.target.checked;
  localStorage.setItem("compareMode", compareMode ? "1" : "0");
  refreshChart();
});
// Export the currently-selected metric + range as a CSV download.
// Browser handles the file save via the Content-Disposition header on
// the response.
$("#chart-export-csv")?.addEventListener("click", () => {
  const label  = $("#sel-device").value;
  const metric = $("#sel-metric").value;
  if (!label || !metric) return;
  let url = `/api/devices/${encodeURIComponent(label)}/history.csv?metric=${encodeURIComponent(metric)}`;
  if (currentRange === "custom") {
    const p = customRangeParams();
    if (!p) return;
    url += `&since=${p.since}&until=${p.until}&bucket=${p.bucket}`;
  } else {
    const [since, bucket] = sinceForRange(currentRange);
    url += `&since=${since}&bucket=${bucket}`;
  }
  // Anchor + click is the most reliable cross-browser download trigger;
  // window.location replace would also work but breaks the SPA back-stack.
  const a = document.createElement("a");
  a.href = url;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
});
for (const btn of document.querySelectorAll("[data-range]")) {
  btn.addEventListener("click", () => {
    document.querySelectorAll("[data-range]").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentRange = btn.dataset.range;

    const customWrap = $("#custom-range");
    if (currentRange === "custom") {
      // Reveal the picker, prefill last 6h if empty
      customWrap.hidden = false;
      const now = new Date();
      const fromInput = $("#custom-from");
      const toInput   = $("#custom-to");
      if (!fromInput.value) {
        const sixHoursAgo = new Date(now.getTime() - 6 * 3600 * 1000);
        fromInput.value = toLocalInputValue(sixHoursAgo);
        customSince = Math.floor(sixHoursAgo.getTime() / 1000);
      }
      if (!toInput.value) {
        toInput.value = toLocalInputValue(now);
        customUntil = Math.floor(now.getTime() / 1000);
      }
    } else {
      customWrap.hidden = true;
    }

    refreshChart();
  });
}

// datetime-local inputs use the format YYYY-MM-DDTHH:MM in local time.
function toLocalInputValue(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
         `T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

document.addEventListener("change", (e) => {
  if (e.target.id === "custom-from" || e.target.id === "custom-to") {
    const t = new Date(e.target.value);
    if (!isFinite(t.getTime())) return;
    const seconds = Math.floor(t.getTime() / 1000);
    if (e.target.id === "custom-from") customSince = seconds;
    else                                customUntil = seconds;
    // Auto-swap if user picked from > to
    if (customSince && customUntil && customSince > customUntil) {
      [customSince, customUntil] = [customUntil, customSince];
      $("#custom-from").value = toLocalInputValue(new Date(customSince * 1000));
      $("#custom-to").value   = toLocalInputValue(new Date(customUntil * 1000));
    }
    if (currentRange === "custom") refreshChart();
  }
});
window.addEventListener("resize", () => {
  if (chart && currentRouteName() === "history") {
    chart.setSize({ width: $("#chart").clientWidth, height: 340 });
  }
});

// ---------- Per-device detail page ----------
// Single route at #/device/<label>. Content dispatched by device kind so
// a smart battery, shunt, or charge controller each get a layout that
// matches what data they actually report.

let devDetailChart = null;

function renderDeviceDetail(label) {
  const host = $("#device-route");
  if (!host) return;
  const dev = devices.find(d => d.label === label);
  if (!dev) {
    host.innerHTML = `
      <section class="panel">
        <p style="padding:1rem">No device named <code>${label}</code>. <a href="#/devices">Back to Devices</a></p>
      </section>`;
    return;
  }
  // Per-kind dispatcher. Each builder returns HTML for the panel stack;
  // returning identical structure lets us reuse layout + styling.
  let inner;
  if (dev.kind === "smart_battery") inner = buildSmartBatteryDetail(dev);
  else if (dev.kind === "charge_controller") inner = buildControllerDetail(dev);
  else if (dev.kind === "shunt") inner = buildShuntDetail(dev);
  else if (dev.kind === "ac_charger") inner = buildAcChargerDetail(dev);
  else inner = buildGenericDetail(dev);

  // Prev/next nav between devices of the same kind makes "compare packs"
  // a one-tap operation.
  const siblings = devices.filter(d => d.kind === dev.kind);
  const idx = siblings.findIndex(d => d.label === dev.label);
  const prev = idx > 0 ? siblings[idx - 1] : null;
  const next = idx < siblings.length - 1 ? siblings[idx + 1] : null;

  const fw = dev.latest?.firmware_version || dev.latest?.firmware_version_raw || "";
  const shown = dispName(dev);
  const renamed = shown !== dev.label;
  host.innerHTML = `
    <div class="dev-detail-head">
      <div class="dev-detail-crumb">
        <a href="#/devices">← Devices</a>
        <span class="dev-detail-title">
          <span class="dev-detail-icon">${ICONS[KIND_ICON[dev.kind] || "unknown"]}</span>
          <span data-dev-display-name>${shown}</span>
          <button class="dev-rename-btn" data-dev-rename="${dev.label}"
                  title="Rename this device"
                  aria-label="Rename"
                  style="background:none;border:none;cursor:pointer;padding:.15rem .3rem;opacity:.55;color:inherit">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"
                 stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M12 20h9"/>
              <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/>
            </svg>
          </button>
        </span>
        <span class="dev-detail-meta">
          ${dev.vendor} · ${dev.kind}${dev.slave_id != null ? " · slave " + dev.slave_id : ""}${fw ? " · fw " + fw : ""}${dev.latest?.model ? " · " + dev.latest.model : ""}${renamed ? ` · label <code>${dev.label}</code>` : ""}
        </span>
      </div>
      <div class="dev-detail-nav">
        ${prev ? `<a class="btn-action" href="#/device/${encodeURIComponent(prev.label)}">← ${dispName(prev)}</a>` : ""}
        ${next ? `<a class="btn-action" href="#/device/${encodeURIComponent(next.label)}">${dispName(next)} →</a>` : ""}
      </div>
    </div>
    ${inner}
    <div id="outputs-host"></div>
    <div id="settings-host"></div>`;

  // Wire up the per-device chart after DOM is in place.
  wireDeviceDetailChart(dev);
  // Async-load charger value-add stats (lifetime kWh, today active,
  // 24h state ribbon) when the device exposes a charger power metric.
  wireChargerStats(dev);
  // Rename button → prompt → PUT /display-name → optimistic refresh.
  wireDeviceRename(dev);
  // Async-fetch controllable outputs registered against this device.
  // Renders nothing if the device has none — Rovers get a Load panel,
  // smart batteries / shunts get nothing today (until #114 adds JK BMS
  // charge/discharge MOS outputs).
  renderDeviceOutputs(dev.label);
  // Async-fetch declared writable settings (#111). Renders nothing
  // if the driver doesn't expose any. Phase 1 = read-only display;
  // phase 2 will add the edit modal.
  renderDeviceSettings(dev.label);
}

// ---------- DEVICE SETTINGS (#111 phase 1, read-only) ----------
async function renderDeviceSettings(label) {
  const host = document.getElementById("settings-host");
  if (!host) return;
  let data;
  try {
    data = await api(`/api/devices/${encodeURIComponent(label)}/settings`);
  } catch (_) {
    return; // 404 or network. Leave the host empty.
  }
  const items = (data && data.items) || [];
  if (!items.length) return;
  const rows = items.map((s) => {
    let cur = "·";
    if (s.current_value != null) {
      if (s.kind === "enum") {
        const match = (s.choices || []).find((c) => c.value === s.current_value);
        cur = match ? esc(match.label) : esc(String(s.current_value));
      } else {
        const val = (typeof s.current_value === "number")
          ? s.current_value.toFixed(s.step && s.step < 1 ? 1 : 0)
          : esc(String(s.current_value));
        cur = `${val}${s.units ? " " + esc(s.units) : ""}`;
      }
    }
    const editAttrs = s.editable
      ? `data-setting-key="${esc(s.key)}" data-setting-label="${esc(label)}"`
      : `disabled title="Read-only. Driver hasn't declared a write path"`;
    return `
      <tr>
        <td>
          <div class="settings-row-label">${esc(s.label)}</div>
          ${s.help_text ? `<div class="settings-row-help">${esc(s.help_text)}</div>` : ""}
        </td>
        <td class="settings-row-value">${cur}</td>
        <td>
          <button class="btn-action settings-edit-btn" type="button" ${editAttrs}>
            Edit
          </button>
        </td>
      </tr>`;
  }).join("");
  host.innerHTML = `
    <section class="device-detail-card">
      <h3 style="margin:0 0 .5rem">Settings</h3>
      <p class="settings-foot" style="margin:0 0 .75rem">
        Live values pulled from the device. Edits write FC06
        Modbus to the device and read back to confirm.
      </p>
      <table class="settings-table">
        <tbody>${rows}</tbody>
      </table>
    </section>`;
  // Wire each enabled Edit button. The handler captures the
  // descriptor by key from the items array so the modal has the
  // full schema (choices / min / max / step / units) without
  // re-fetching.
  host.querySelectorAll(".settings-edit-btn").forEach((btn) => {
    if (btn.disabled) return;
    const key = btn.dataset.settingKey;
    const dev = btn.dataset.settingLabel;
    const setting = items.find((it) => it.key === key);
    if (!setting) return;
    btn.addEventListener("click", () => openSettingsEditModal(dev, setting));
  });
}

function openSettingsEditModal(deviceLabel, setting) {
  // Build a modal in-place. The dashboard doesn't have a generic
  // modal component yet; rather than carry one in just for this,
  // we hand-roll the dom + escape on Esc / click-outside.
  const existing = document.getElementById("settings-edit-modal");
  if (existing) existing.remove();

  const modal = document.createElement("div");
  modal.id = "settings-edit-modal";
  modal.className = "settings-edit-modal-backdrop";

  // Input markup depends on kind.
  let inputHtml = "";
  if (setting.kind === "enum") {
    const opts = (setting.choices || []).map((c) =>
      `<option value="${c.value}" ${c.value === setting.current_value ? "selected" : ""}>${esc(c.label)}</option>`
    ).join("");
    inputHtml = `<select id="settings-edit-input">${opts}</select>`;
  } else {
    const step = setting.step || 1;
    const val = setting.current_value != null ? setting.current_value : "";
    const minA = setting.min != null ? ` min="${setting.min}"` : "";
    const maxA = setting.max != null ? ` max="${setting.max}"` : "";
    inputHtml = `<input type="number" step="${step}"${minA}${maxA}
                        id="settings-edit-input" value="${val}"
                        inputmode="decimal" />`;
  }

  const curStr = (() => {
    if (setting.current_value == null) return "·";
    if (setting.kind === "enum") {
      const m = (setting.choices || []).find((c) => c.value === setting.current_value);
      return m ? m.label : String(setting.current_value);
    }
    const v = (typeof setting.current_value === "number")
      ? setting.current_value.toFixed(setting.step && setting.step < 1 ? 1 : 0)
      : String(setting.current_value);
    return `${v}${setting.units ? " " + setting.units : ""}`;
  })();

  modal.innerHTML = `
    <div class="settings-edit-modal">
      <h3>${esc(setting.label)}</h3>
      <p class="settings-foot">${esc(setting.help_text || "")}</p>
      <div class="settings-edit-row">
        <span class="settings-edit-cur-label">Current</span>
        <span class="settings-edit-cur">${esc(curStr)}</span>
      </div>
      <div class="settings-edit-row">
        <label for="settings-edit-input">New value${setting.units ? " (" + esc(setting.units) + ")" : ""}</label>
        ${inputHtml}
      </div>
      <div class="settings-edit-warn" id="settings-edit-warn" hidden></div>
      <div class="settings-edit-actions">
        <button type="button" class="btn-action" id="settings-edit-cancel">Cancel</button>
        <button type="button" class="btn-action btn-action--primary"
                id="settings-edit-save">Apply</button>
      </div>
    </div>`;
  document.body.appendChild(modal);

  const close = () => modal.remove();
  modal.addEventListener("click", (e) => { if (e.target === modal) close(); });
  document.addEventListener("keydown", function onKey(e) {
    if (e.key === "Escape") { close(); document.removeEventListener("keydown", onKey); }
  });
  document.getElementById("settings-edit-cancel").addEventListener("click", close);

  document.getElementById("settings-edit-save").addEventListener("click", async () => {
    const input = document.getElementById("settings-edit-input");
    const warn = document.getElementById("settings-edit-warn");
    const saveBtn = document.getElementById("settings-edit-save");
    const raw = input.value;
    let val;
    if (setting.kind === "enum") {
      val = parseInt(raw, 10);
    } else {
      val = parseFloat(raw);
      if (!Number.isFinite(val)) {
        warn.hidden = false; warn.textContent = "Enter a number";
        return;
      }
    }
    saveBtn.disabled = true; saveBtn.textContent = "Writing…";
    warn.hidden = true;
    try {
      const r = await fetch(
        `/api/devices/${encodeURIComponent(deviceLabel)}/settings/${encodeURIComponent(setting.key)}`,
        {
          method: "PATCH",
          credentials: "same-origin",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({value: val}),
        },
      );
      const body = await r.json().catch(() => ({}));
      if (r.ok && body.ok) {
        close();
        renderDeviceSettings(deviceLabel);
      } else {
        warn.hidden = false;
        warn.textContent = body.detail || `HTTP ${r.status}`;
        saveBtn.disabled = false; saveBtn.textContent = "Apply";
      }
    } catch (e) {
      warn.hidden = false; warn.textContent = e.message || String(e);
      saveBtn.disabled = false; saveBtn.textContent = "Apply";
    }
  });
}

// ---------- CONTROLLABLE OUTPUTS (#104) ----------
//
// Per-device panel: toggle + state + last-command + safety-confirm.
// State is sourced from /api/outputs which the daemon refreshes on
// each poll cycle. A toggle round-trips through /api/outputs/<id>/
// toggle and surfaces the WriteResult; success updates state from
// the response's `confirmed_state` immediately, so the UI doesn't
// have to wait for the next 60s poll to reflect truth.

async function renderDeviceOutputs(label) {
  const host = $("#outputs-host");
  if (!host) return;
  let outs = [];
  try {
    const r = await api(`/api/outputs?device=${encodeURIComponent(label)}`);
    outs = r?.outputs || [];
  } catch (e) { outs = []; }
  if (!outs.length) { host.innerHTML = ""; return; }
  host.innerHTML = outs.map(renderOutputPanelHtml).join("");
  outs.forEach(wireOutputPanel);
}

function renderOutputPanelHtml(o) {
  const stateLabel =
    o.state === 1 ? `<span class="output-state-pill output-on">●  ON</span>` :
    o.state === 0 ? `<span class="output-state-pill output-off">○  OFF</span>` :
    `<span class="output-state-pill output-unknown">· unknown</span>`;
  const lastCmd = o.last_command;
  const lastLine = lastCmd
    ? `Last command: <strong>${lastCmd.action}</strong> · ${fmt.ago(lastCmd.at)} · by ${lastCmd.by} · ${lastCmd.result}`
    : `No command issued yet.`;
  const safetyBanner = o.safety_confirmed ? "" : `
    <div class="output-safety">
      <strong>Advanced control.</strong> This sends a write command to your
      charger that switches the load terminal. If anything is wired to it,
      that thing will turn off or on. Continue?
      <button class="btn-action btn-action--primary" data-output-confirm="${o.id}">
        I understand. Enable controls
      </button>
    </div>`;
  const controls = o.safety_confirmed ? `
    <div class="output-controls">
      <button class="output-toggle ${o.state === 1 ? 'is-on' : 'is-off'}"
              data-output-toggle="${o.id}"
              aria-pressed="${o.state === 1 ? 'true' : 'false'}">
        <span class="output-toggle-thumb"></span>
        <span class="output-toggle-label">${o.state === 1 ? 'On' : 'Off'}</span>
      </button>
      <span class="output-busy" data-output-busy="${o.id}" hidden>Writing…</span>
    </div>` : "";
  const schedulesSection = o.safety_confirmed ? `
    <details class="output-schedules" data-output-schedules="${o.id}">
      <summary>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 9h18"/><path d="M8 3v4M16 3v4"/></svg>
        <span>Schedules</span>
        <span class="output-schedules-count meta-k" data-output-schedules-count="${o.id}"></span>
      </summary>
      <div class="output-schedules-list" data-output-schedules-list="${o.id}">
        <div class="settings-empty">Loading…</div>
      </div>
    </details>` : "";
  return `
    <section class="panel output-panel" data-output-id="${o.id}">
      <div class="panel-header">
        <h2>
          <svg class="h-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M9 18h6"/><path d="M10 22h4"/>
            <path d="M2 12a10 10 0 0 1 20 0c0 3-1.5 5.5-4 7h-12c-2.5-1.5-4-4-4-7z"/>
          </svg>
          ${o.name}
        </h2>
        <div class="panel-sub">${stateLabel}</div>
      </div>
      ${safetyBanner}
      ${controls}
      <div class="output-foot meta-k">${lastLine}</div>
      ${schedulesSection}
    </section>`;
}

function wireOutputPanel(o) {
  // Lazy-load schedules when the user opens the <details> for the
  // first time. Keeps the initial output-panel render cheap and
  // skips the network round-trip for users who never tap Schedules.
  const details = document.querySelector(`[data-output-schedules="${o.id}"]`);
  if (details && !details.dataset.loaded) {
    details.addEventListener("toggle", () => {
      if (details.open && !details.dataset.loaded) {
        details.dataset.loaded = "1";
        renderSchedulesList(o.id);
      }
    });
  }

  document.querySelector(`[data-output-confirm="${o.id}"]`)?.addEventListener("click", async () => {
    try {
      await fetch(`/api/outputs/${encodeURIComponent(o.id)}/confirm`, {method: "POST"});
      // Re-render this device's outputs so the controls appear.
      renderDeviceOutputs(o.device_label);
    } catch (e) {
      alert(`Couldn't enable: ${e}`);
    }
  });
  document.querySelector(`[data-output-toggle="${o.id}"]`)?.addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    const busy = document.querySelector(`[data-output-busy="${o.id}"]`);
    btn.disabled = true; if (busy) busy.hidden = false;
    const want = !(o.state === 1);
    try {
      const r = await fetch(`/api/outputs/${encodeURIComponent(o.id)}/toggle`, {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify({on: want}),
      });
      if (!r.ok) {
        // Body may be JSON with detail{} or a plain string. Either
        // way we want a one-line user-readable message.
        let msg = `HTTP ${r.status}`;
        try {
          const j = await r.json();
          msg = j?.detail?.detail || j?.detail || msg;
        } catch (_) {}
        throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
      }
      const data = await r.json();
      // Re-render with the new state from the server (which includes
      // confirmed_state from the post-write FC03 read-back).
      const fresh = data?.output;
      if (fresh) {
        // Swap one panel's worth of HTML in-place so we don't lose the
        // scroll position on long device pages.
        const host = document.querySelector(`[data-output-id="${o.id}"]`);
        if (host) {
          host.outerHTML = renderOutputPanelHtml(fresh);
          wireOutputPanel(fresh);
        }
      }
    } catch (e) {
      alert(`Couldn't change state: ${e.message || e}`);
      btn.disabled = false; if (busy) busy.hidden = true;
    }
  });
}

// ---------- output schedules UI (#117) ----------
//
// One schedule row per cron-like rule; clicking "+ Add schedule"
// reveals an inline form that posts to the API and re-renders the
// list. Sunrise/sunset triggers carry a ± offset; time triggers
// carry an HH:MM. Day-mask is rendered as 7 toggleable letter chips
// (M T W T F S S, Monday is bit 0 — matches `datetime.weekday()`).

const DAY_LABELS = ["M","T","W","T","F","S","S"];

function _scheduleSummary(s) {
  const action = s.action === "on" ? "Turn ON" : "Turn OFF";
  let when;
  if (s.trigger_kind === "time") {
    when = `at ${s.trigger_time || "?"}`;
  } else {
    const off = s.offset_min || 0;
    const sign = off > 0 ? "+" : "";
    const ofs = off === 0 ? "" : ` (${sign}${off} min)`;
    when = `at ${s.trigger_kind}${ofs}`;
  }
  // Day-mask pretty-print: full week = "every day", weekdays =
  // "weekdays", weekend = "weekends", else explicit letters.
  let days = "";
  const m = s.days_mask ?? 127;
  if (m === 127) days = "every day";
  else if (m === 0b0011111) days = "weekdays";
  else if (m === 0b1100000) days = "weekends";
  else {
    days = DAY_LABELS.map((d, i) => (m & (1 << i)) ? d : "").join("") || "(no days)";
  }
  return `${action} ${when} · ${days}`;
}

function _formatLastRun(s) {
  if (!s.last_run_at) return "Never run yet.";
  const ago = fmt.ago(s.last_run_at);
  const res = s.last_run_result || "?";
  const cls = res === "ok" ? "ok" : "err";
  return `Last run: <span class="acc-${cls}">${escHtml(res)}</span> · ${ago}`;
}

async function renderSchedulesList(outputId) {
  const host = document.querySelector(`[data-output-schedules-list="${outputId}"]`);
  const counter = document.querySelector(`[data-output-schedules-count="${outputId}"]`);
  if (!host) return;
  let schedules = [];
  try {
    const r = await api(`/api/outputs/${encodeURIComponent(outputId)}/schedules`);
    schedules = r?.schedules || [];
  } catch (e) {
    host.innerHTML = `<div class="settings-empty">Couldn't load schedules: ${escHtml(e.message || String(e))}</div>`;
    return;
  }
  if (counter) counter.textContent = schedules.length ? `(${schedules.length})` : "";

  const rows = schedules.map(s => `
    <div class="schedule-row" data-schedule-id="${s.id}">
      <label class="schedule-enabled" title="${s.enabled ? 'Enabled. Uncheck to pause' : 'Disabled'}">
        <input type="checkbox" data-schedule-toggle="${s.id}" ${s.enabled ? "checked" : ""}/>
      </label>
      <div class="schedule-info">
        <div class="schedule-summary">${escHtml(_scheduleSummary(s))}</div>
        <div class="schedule-last meta-k">${_formatLastRun(s)}</div>
      </div>
      <button class="alerts-icon-btn alerts-icon-btn--danger"
              data-schedule-delete="${s.id}"
              title="Delete this schedule">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>
      </button>
    </div>`).join("");

  host.innerHTML = `
    ${rows || '<div class="settings-empty">No schedules yet. Add one below.</div>'}
    <div class="schedule-add-host" data-schedule-add-host="${outputId}">
      <button class="btn-action" data-schedule-add-show="${outputId}">+ Add schedule</button>
    </div>`;

  // Wire row controls + the add-button.
  host.querySelectorAll("[data-schedule-toggle]").forEach(cb => {
    cb.addEventListener("change", () => _scheduleToggle(outputId, +cb.dataset.scheduleToggle, cb.checked));
  });
  host.querySelectorAll("[data-schedule-delete]").forEach(btn => {
    btn.addEventListener("click", () => _scheduleDelete(outputId, +btn.dataset.scheduleDelete));
  });
  host.querySelector(`[data-schedule-add-show="${outputId}"]`)?.addEventListener("click", () => {
    _scheduleShowAddForm(outputId);
  });
}

function _scheduleShowAddForm(outputId) {
  const host = document.querySelector(`[data-schedule-add-host="${outputId}"]`);
  if (!host) return;
  // Sensible default for new schedules: turn ON at sunset (the
  // archetypal van-light "shed lighting" use case).
  host.innerHTML = `
    <form class="schedule-form" data-schedule-form="${outputId}">
      <div class="schedule-form-row">
        <label class="schedule-form-label">Action</label>
        <div class="schedule-form-group">
          <label><input type="radio" name="action" value="on" checked/> Turn ON</label>
          <label><input type="radio" name="action" value="off"/> Turn OFF</label>
        </div>
      </div>
      <div class="schedule-form-row">
        <label class="schedule-form-label">Trigger</label>
        <div class="schedule-form-group">
          <label><input type="radio" name="trigger_kind" value="time"/> at time</label>
          <label><input type="radio" name="trigger_kind" value="sunrise"/> sunrise</label>
          <label><input type="radio" name="trigger_kind" value="sunset" checked/> sunset</label>
        </div>
      </div>
      <div class="schedule-form-row" data-show-when-kind="time" hidden>
        <label class="schedule-form-label">Time (HH:MM)</label>
        <input type="time" name="trigger_time" value="22:00"/>
      </div>
      <div class="schedule-form-row" data-show-when-kind="sunrise sunset">
        <label class="schedule-form-label">Offset (minutes ± sunrise/sunset)</label>
        <input type="number" name="offset_min" value="0" min="-720" max="720" step="5"/>
      </div>
      <div class="schedule-form-row">
        <label class="schedule-form-label">Days</label>
        <div class="schedule-form-group schedule-days">
          ${DAY_LABELS.map((d, i) => `
            <label class="schedule-day"><input type="checkbox" name="day_${i}" checked/> ${d}</label>
          `).join("")}
        </div>
      </div>
      <div class="schedule-form-actions">
        <button type="submit" class="btn-action btn-action--primary">Add</button>
        <button type="button" class="btn-action" data-schedule-add-cancel="${outputId}">Cancel</button>
        <span class="schedule-form-status"></span>
      </div>
    </form>`;
  const form = host.querySelector("form");
  // Toggle visibility of time-input / offset-input based on selected
  // trigger_kind. data-show-when-kind is space-separated, listing
  // which kinds the row should be visible for.
  const refreshKindVisibility = () => {
    const kind = form.querySelector('input[name="trigger_kind"]:checked').value;
    form.querySelectorAll("[data-show-when-kind]").forEach(el => {
      el.hidden = !el.dataset.showWhenKind.split(/\s+/).includes(kind);
    });
  };
  form.querySelectorAll('input[name="trigger_kind"]').forEach(r => {
    r.addEventListener("change", refreshKindVisibility);
  });
  refreshKindVisibility();
  form.addEventListener("submit", (e) => { e.preventDefault(); _scheduleSubmit(outputId, form); });
  form.querySelector(`[data-schedule-add-cancel="${outputId}"]`).addEventListener("click", () => {
    renderSchedulesList(outputId);
  });
}

async function _scheduleSubmit(outputId, form) {
  const status = form.querySelector(".schedule-form-status");
  status.textContent = "Saving…"; status.className = "schedule-form-status";
  const kind = form.elements["trigger_kind"].value;
  const action = form.elements["action"].value;
  let days_mask = 0;
  for (let i = 0; i < 7; i++) {
    if (form.elements[`day_${i}`].checked) days_mask |= (1 << i);
  }
  const payload = {
    action, trigger_kind: kind, days_mask, enabled: true,
  };
  if (kind === "time") {
    payload.trigger_time = form.elements["trigger_time"].value;
  } else {
    payload.offset_min = parseInt(form.elements["offset_min"].value, 10) || 0;
  }
  try {
    const r = await fetch(`/api/outputs/${encodeURIComponent(outputId)}/schedules`, {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${r.status}`);
    }
  } catch (e) {
    status.textContent = e.message || String(e); status.classList.add("err");
    return;
  }
  renderSchedulesList(outputId);
}

async function _scheduleToggle(outputId, scheduleId, enabled) {
  try {
    await fetch(`/api/outputs/${encodeURIComponent(outputId)}/schedules/${scheduleId}`, {
      method: "PUT",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({enabled}),
    });
  } catch (e) {
    alert(`Couldn't update: ${e.message || e}`);
    renderSchedulesList(outputId);
  }
}

async function _scheduleDelete(outputId, scheduleId) {
  if (!confirm("Delete this schedule?")) return;
  try {
    await fetch(`/api/outputs/${encodeURIComponent(outputId)}/schedules/${scheduleId}`, {
      method: "DELETE",
    });
  } catch (e) {
    alert(`Couldn't delete: ${e.message || e}`);
    return;
  }
  renderSchedulesList(outputId);
}

function buildSmartBatteryDetail(dev) {
  const l = dev.latest || {};
  const cap = +l.capacity_ah || 0;
  const rem = +l.remaining_charge_ah || 0;
  const pct = cap > 0 ? (rem / cap) * 100 : 0;
  const v = +l.voltage_v || 0;
  const i = +l.current_a || 0;
  const w = v * i;
  const direction = Math.abs(w) < 1 ? "idle" : (w > 0 ? "charging" : "discharging");
  const flowText = direction === "idle" ? "Idle" : `${fmt.signed(w, 0)} W`;

  // Per-pack cells
  const n = +l.cell_count || 0;
  const cells = [];
  for (let j = 0; j < n; j++) cells.push(+l[`cell_voltage_${j}_v`]);
  const cellMin = cells.length ? Math.min(...cells) : 0;
  const cellMax = cells.length ? Math.max(...cells) : 0;
  const spread = cellMax - cellMin;
  const driftCls = spread >= 0.2 ? "red" : spread >= 0.1 ? "amber" : "green";

  const tempSensors = +l.temperature_sensor_count || 0;
  const temps = [];
  for (let j = 0; j < tempSensors; j++) {
    const t = l[`temperature_${j}_c`];
    if (typeof t === "number") temps.push(t);
  }
  const meanTemp = temps.length ? (temps.reduce((a, c) => a + c, 0) / temps.length).toFixed(1) : "·";

  return `
    <section class="hero-v2 soc-${pct < 20 ? "low" : pct < 50 ? "mid" : "high"}">
      <div class="hero-donut-wrap ${direction}">
        <svg class="hero-donut" viewBox="0 0 200 200" aria-hidden="true">
          <circle class="donut-track" cx="100" cy="100" r="86" fill="none" stroke-width="14" />
          <circle class="donut-arc soc-${pct < 20 ? "low" : pct < 50 ? "mid" : "high"}"
                  cx="100" cy="100" r="86" fill="none" stroke-width="14"
                  pathLength="100" stroke-dasharray="${pct} ${100 - pct}"
                  stroke-linecap="round" transform="rotate(-90 100 100)" />
        </svg>
        <div class="donut-center">
          <div class="donut-pct"><span>${pct.toFixed(1)}</span><span class="donut-pct-unit">%</span></div>
          <div class="donut-label">This pack</div>
          <div class="donut-flow ${direction}"><span class="donut-flow-arrow"></span><span>${flowText}</span></div>
        </div>
      </div>
      <div class="hero-stats">
        <div class="hero-stat"><div class="meta-k">Voltage</div><div class="hero-stat-val"><span>${v.toFixed(2)}</span><span class="hero-stat-unit">V</span></div></div>
        <div class="hero-stat"><div class="meta-k">Current</div><div class="hero-stat-val ${direction === "charging" ? "power-charging" : direction === "discharging" ? "power-discharging" : ""}"><span>${fmt.signed(i, 2)}</span><span class="hero-stat-unit">A</span></div></div>
        <div class="hero-stat"><div class="meta-k">Remaining</div><div class="hero-stat-val"><span>${rem.toFixed(1)}</span><span class="hero-stat-unit">Ah</span></div></div>
        <div class="hero-stat"><div class="meta-k">Capacity</div><div class="hero-stat-val"><span>${cap.toFixed(0)}</span><span class="hero-stat-unit">Ah</span></div></div>
        <div class="hero-stat"><div class="meta-k">Avg cell temp</div><div class="hero-stat-val"><span>${meanTemp}</span><span class="hero-stat-unit">°C</span></div></div>
        <div class="hero-stat"><div class="meta-k">Drift</div><div class="hero-stat-val"><span>${(spread * 1000).toFixed(0)}</span><span class="hero-stat-unit">mV</span></div></div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2><svg class="h-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="7" width="14" height="10" rx="1.5"/><path d="M17 10v4"/><path d="M6 9v6M10 9v6M14 9v6"/></svg> Cells</h2>
        <div class="panel-sub">
          <span class="pill ${driftCls}"><span class="pill-dot"></span>spread ${spread.toFixed(2)} V · min ${cellMin ? cellMin.toFixed(2) : "·"} · max ${cellMax ? cellMax.toFixed(2) : "·"}</span>
        </div>
      </div>
      <div class="cell-row-cells" style="margin-top:.5rem">
        ${cells.map((cv, ci) => {
          let cls = "cell-chip";
          if (cv === cellMin && spread > 0.01) cls += " is-min";
          if (cv === cellMax && spread > 0.01) cls += " is-max";
          if (cv > 3.65) cls += " is-high";
          return `<div class="${cls}"><span class="cell-chip-k">cell ${ci+1}</span><span class="cell-chip-v">${cv != null ? cv.toFixed(2) + " V" : "·"}</span></div>`;
        }).join("")}
      </div>
    </section>

    ${buildLifetimeBlock(dev)}
    ${buildHistoryBlock(dev, "voltage_v")}
  `;
}

function buildShuntDetail(dev) {
  const l = dev.latest || {};
  const v = +l.voltage_v || 0;
  const i = +l.current_a || 0;
  const w = l.power_w != null ? +l.power_w : v * i;
  const soc = +l.soc_pct || 0;
  const direction = Math.abs(w) < 1 ? "idle" : (w > 0 ? "charging" : "discharging");
  const flowText = direction === "idle" ? "Idle" : `${fmt.signed(w, 0)} W`;

  return `
    <section class="hero-v2 soc-${soc < 20 ? "low" : soc < 50 ? "mid" : "high"}">
      <div class="hero-donut-wrap ${direction}">
        <svg class="hero-donut" viewBox="0 0 200 200" aria-hidden="true">
          <circle class="donut-track" cx="100" cy="100" r="86" fill="none" stroke-width="14" />
          <circle class="donut-arc soc-${soc < 20 ? "low" : soc < 50 ? "mid" : "high"}"
                  cx="100" cy="100" r="86" fill="none" stroke-width="14"
                  pathLength="100" stroke-dasharray="${soc} ${100 - soc}"
                  stroke-linecap="round" transform="rotate(-90 100 100)" />
        </svg>
        <div class="donut-center">
          <div class="donut-pct"><span>${soc.toFixed(1)}</span><span class="donut-pct-unit">%</span></div>
          <div class="donut-label">Bank SoC</div>
          <div class="donut-flow ${direction}"><span class="donut-flow-arrow"></span><span>${flowText}</span></div>
        </div>
      </div>
      <div class="hero-stats">
        <div class="hero-stat hero-stat--big"><div class="meta-k">Voltage</div><div class="hero-stat-val"><span>${v.toFixed(2)}</span><span class="hero-stat-unit">V</span></div></div>
        <div class="hero-stat hero-stat--big"><div class="meta-k">Current</div><div class="hero-stat-val ${direction === "charging" ? "power-charging" : direction === "discharging" ? "power-discharging" : ""}"><span>${fmt.signed(i, 2)}</span><span class="hero-stat-unit">A</span></div></div>
        <div class="hero-stat"><div class="meta-k">Power</div><div class="hero-stat-val"><span>${fmt.signed(w, 0)}</span><span class="hero-stat-unit">W</span></div></div>
        <div class="hero-stat"><div class="meta-k">Remaining</div><div class="hero-stat-val"><span>${(+l.remaining_ah || 0).toFixed(1)}</span><span class="hero-stat-unit">Ah</span></div></div>
      </div>
    </section>

    ${buildHistoryBlock(dev, "voltage_v")}
  `;
}

function buildControllerDetail(dev) {
  const l = dev.latest || {};
  return `
    <section class="panel">
      <div class="panel-header">
        <h2><svg class="h-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4.2"/><path d="M12 2v2.5M12 19.5V22M3.5 12H6M18 12h2.5M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8"/></svg> Charging</h2>
        <div class="panel-sub"><span class="pill green"><span class="pill-dot"></span>${l.charging_state || "·"}</span></div>
      </div>
      <div class="today-strip" style="margin-top:.4rem">
        <div class="today-cell"><span class="meta-k">PV input</span><span class="today-v">${fmt.num(l.pv_power_w, 0)} W</span></div>
        <div class="today-cell"><span class="meta-k">PV voltage</span><span class="today-v">${fmt.num(l.pv_voltage_v, 1)} V</span></div>
        <div class="today-cell"><span class="meta-k">PV current</span><span class="today-v">${fmt.num(l.pv_current_a, 2)} A</span></div>
        <div class="today-cell"><span class="meta-k">To battery</span><span class="today-v">${fmt.num(l.battery_current_a, 2)} A @ ${fmt.num(l.battery_voltage_v, 1)} V</span></div>
        <div class="today-cell"><span class="meta-k">Today</span><span class="today-v">${fmt.wh(l.energy_today_wh)}</span></div>
        <div class="today-cell"><span class="meta-k">Lifetime</span><span class="today-v">${fmt.wh(l.energy_total_wh)}</span></div>
        <div class="today-cell"><span class="meta-k">Peak today</span><span class="today-v">${fmt.num(l.max_charging_power_today_w, 0)} W</span></div>
        <div class="today-cell"><span class="meta-k">Temp</span><span class="today-v">${fmt.num(l.controller_temperature_c, 0)} °C</span></div>
      </div>
    </section>
    ${buildChargerStatsBlock(dev)}
    ${buildHistoryBlock(dev, "pv_power_w")}
  `;
}

function buildAcChargerDetail(dev) {
  const l = dev.latest || {};
  // Each output channel as a column — many Blue Smart IP65 chargers
  // have 3 outputs feeding separate banks (engine / aux / start).
  const outs = [1, 2, 3].map(n => {
    const v = l[`output_${n}_voltage_v`];
    const i = l[`output_${n}_current_a`];
    const p = l[`output_${n}_power_w`];
    if (v == null && i == null && p == null) return "";
    return `
      <div class="today-cell">
        <span class="meta-k">Output ${n}</span>
        <span class="today-v">${fmt.num(p, 0)} W</span>
        <span class="meta-k" style="opacity:.7">${fmt.num(v, 1)} V · ${fmt.num(i, 1)} A</span>
      </div>`;
  }).join("");
  return `
    <section class="panel">
      <div class="panel-header">
        <h2><svg class="h-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3v4M15 3v4"/><rect x="6" y="7" width="12" height="9" rx="2"/><path d="M12 16v5"/></svg> AC Charger</h2>
        <div class="panel-sub"><span class="pill green"><span class="pill-dot"></span>${l.charging_state || "·"}</span></div>
      </div>
      <div class="today-strip" style="margin-top:.4rem">
        ${l.ac_input_current_a != null ? `<div class="today-cell"><span class="meta-k">AC input</span><span class="today-v">${fmt.num(l.ac_input_current_a, 2)} A</span></div>` : ""}
        ${outs}
        ${l.temperature_c != null ? `<div class="today-cell"><span class="meta-k">Temp</span><span class="today-v">${fmt.num(l.temperature_c, 0)} °C</span></div>` : ""}
        ${l.charger_error && String(l.charger_error).toLowerCase() !== "no_error" ? `<div class="today-cell"><span class="meta-k">Error</span><span class="today-v">${l.charger_error}</span></div>` : ""}
      </div>
    </section>
    ${buildChargerStatsBlock(dev)}
    ${buildHistoryBlock(dev, "output_1_power_w")}
  `;
}

// Lifetime kWh + today active time + 24h charging-state ribbon.
// Populated async by wireChargerStats from /api/devices/.../charger-stats.
function buildChargerStatsBlock(dev) {
  return `
    <section class="panel" id="dd-chgstats-${dev.label}" hidden>
      <div class="panel-header">
        <h2><svg class="h-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L4 14h7l-1 8 9-12h-7z"/></svg> Charger stats</h2>
      </div>
      <div class="today-strip" style="margin-top:.4rem" data-chgstats-tiles></div>
      <div class="chgstats-ribbon-wrap" style="margin-top:.7rem">
        <div class="meta-k" style="margin-bottom:.3rem">Last 24 hours</div>
        <div data-chgstats-ribbon class="chgstats-ribbon"></div>
        <div class="chgstats-legend" data-chgstats-legend
             style="margin-top:.45rem;display:flex;flex-wrap:wrap;gap:.4rem .9rem;font-size:.85rem"></div>
      </div>
    </section>
  `;
}

const CHARGING_STATE_COLOR = {
  bulk:       "#f59e0b",
  absorption: "#eab308",
  float:      "#22c55e",
  equalize:   "#ef4444",
  storage:    "#94a3b8",
  off:        "#334155",
  fault:      "#dc2626",
};
function chargingStateColor(s) {
  if (!s) return "#334155";
  const k = String(s).toLowerCase();
  return CHARGING_STATE_COLOR[k] || "#64748b";
}
function fmtHm(sec) {
  if (!sec || sec < 60) return `${Math.round(sec || 0)}s`;
  const h = Math.floor(sec / 3600);
  const m = Math.round((sec % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

async function wireChargerStats(dev) {
  const panel = document.getElementById(`dd-chgstats-${dev.label}`);
  if (!panel) return;
  let stats;
  try {
    stats = await api(`/api/devices/${encodeURIComponent(dev.label)}/charger-stats`);
  } catch { return; }
  if (!stats || stats.error) return;
  panel.hidden = false;

  // Tiles
  const tilesHost = panel.querySelector("[data-chgstats-tiles]");
  if (tilesHost) {
    tilesHost.innerHTML = `
      <div class="today-cell"><span class="meta-k">Lifetime delivered</span><span class="today-v">${fmt.wh(stats.lifetime_energy_wh)}</span></div>
      <div class="today-cell"><span class="meta-k">Today delivered</span><span class="today-v">${fmt.wh(stats.today_energy_wh)}</span></div>
      <div class="today-cell"><span class="meta-k">Active today</span><span class="today-v">${fmtHm(stats.today_active_s)}</span></div>
    `;
  }

  // Ribbon — single horizontal bar of colored state segments over 24h.
  const ribbonHost = panel.querySelector("[data-chgstats-ribbon]");
  if (ribbonHost) {
    const segs = stats.state_ribbon || [];
    if (!segs.length) {
      ribbonHost.innerHTML = `<div class="settings-empty" style="padding:.6rem">No state history yet. Wait for the next poll cycle.</div>`;
    } else {
      const total = segs[segs.length - 1].end_ts - segs[0].start_ts;
      const parts = segs.map(s => {
        const pct = total > 0 ? ((s.end_ts - s.start_ts) / total) * 100 : 0;
        const minutes = Math.round((s.end_ts - s.start_ts) / 60);
        const c = chargingStateColor(s.state);
        const fromHm = new Date(s.start_ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const untilHm = new Date(s.end_ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        return `<span style="flex:${pct} 0 0;background:${c};height:100%;display:block"
                      title="${s.state} · ${fromHm}–${untilHm} · ${minutes}m"></span>`;
      }).join("");
      ribbonHost.innerHTML = `
        <div style="display:flex;height:22px;width:100%;border-radius:6px;overflow:hidden;background:#1e293b">
          ${parts}
        </div>
        <div style="display:flex;justify-content:space-between;margin-top:.25rem;font-size:.75rem;opacity:.7">
          <span>${new Date(segs[0].start_ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
          <span>now</span>
        </div>`;
    }
  }

  // Per-state minutes breakdown legend.
  const legendHost = panel.querySelector("[data-chgstats-legend]");
  if (legendHost) {
    const byState = stats.today_state_seconds || {};
    const entries = Object.entries(byState).sort(([, a], [, b]) => b - a);
    if (!entries.length) {
      legendHost.innerHTML = `<span class="meta-k">No state breakdown yet today.</span>`;
    } else {
      legendHost.innerHTML = entries.map(([state, sec]) => `
        <span style="display:inline-flex;align-items:center;gap:.35rem">
          <span style="width:12px;height:12px;border-radius:3px;background:${chargingStateColor(state)};display:inline-block"></span>
          <span style="text-transform:capitalize">${state}</span>
          <span class="meta-k">${fmtHm(sec)}</span>
        </span>
      `).join("");
    }
  }
}

async function wireDeviceRename(dev) {
  const btn = document.querySelector(`[data-dev-rename="${CSS.escape(dev.label)}"]`);
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const current = dispName(dev);
    const next = window.prompt(
      `Rename "${current}" to:\n\n(Leave blank to reset to the original label "${dev.label}".)`,
      current === dev.label ? "" : current,
    );
    if (next === null) return;  // cancelled
    try {
      const r = await fetch(_withKiosk(`/api/devices/${encodeURIComponent(dev.label)}/display-name`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: next }),
      });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      const data = await r.json();
      // Optimistic update — patch the in-memory devices entry so the
      // header re-renders correctly even before the next snapshot.
      const target = devices.find(d => d.label === dev.label);
      if (target) target.display_name = data.display_name;
      renderDeviceDetail(dev.label);
      // Force a snapshot refresh so cards etc. catch up immediately
      // (the SSE will deliver it on next poll anyway, but this feels
      // instant from the user's POV).
      refresh();
    } catch (e) {
      window.alert("Rename failed: " + e.message);
    }
  });
}

function buildGenericDetail(dev) {
  // Fallback: dump all numeric metrics as a metric table + history.
  const l = dev.latest || {};
  const rows = Object.entries(l)
    .filter(([k, v]) => typeof v === "number" && !k.startsWith("_"))
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => {
      const unit = unitFromKey(k);
      return `<div class="dev-card-row"><span class="k">${prettyKey(k)}</span><span class="v">${fmt.num(v)}${unit ? " " + unit : ""}</span></div>`;
    }).join("");
  return `
    <section class="panel">
      <div class="panel-header"><h2>All metrics</h2></div>
      ${rows}
    </section>
    ${buildHistoryBlock(dev, Object.keys(l).find(k => typeof l[k] === "number" && !k.startsWith("_")) || "")}
  `;
}

function buildLifetimeBlock(dev) {
  return `
    <section class="panel" id="dd-lifetime-${dev.label}">
      <div class="panel-header">
        <h2><svg class="h-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg> Lifetime</h2>
      </div>
      <div class="dev-card-lifetime" style="margin-top:.25rem">
        <div class="lt-cell"><span class="meta-k">Cycles</span><span class="lt-v" data-lt="cycles">·</span></div>
        <div class="lt-cell"><span class="meta-k">Ah in</span><span class="lt-v" data-lt="ah_in">·</span></div>
        <div class="lt-cell"><span class="meta-k">Ah out</span><span class="lt-v" data-lt="ah_out">·</span></div>
        <div class="lt-cell" data-lt-eff
             title="Coulombic charge efficiency, SoC-corrected. Healthy LFP is 95-99%. Dropping below ~93% over months hints at pack degradation.">
          <span class="meta-k">η <span class="lt-eff-win">·</span></span>
          <span class="lt-v" data-lt="eff">·</span>
        </div>
      </div>
      <div class="dev-efficiency-detail" id="dd-eff-${dev.label}" hidden>
        <h4>Charge efficiency by window</h4>
        <div class="eff-grid" data-eff-grid></div>
        <p class="settings-foot">
          Only windows with at least one full cycle's worth of
          throughput are shown in colour. Greyed entries don't yet
          have enough cycling to be trustworthy. Numbers are
          coulomb-corrected: <code>(Ah out + Δ remaining) / Ah in</code>.
        </p>
      </div>
    </section>
  `;
}

function buildHistoryBlock(dev, defaultMetric) {
  return `
    <section class="panel">
      <div class="panel-header">
        <h2><svg class="h-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l5-6 4 3 4-7 5 6"/><path d="M3 21h18"/></svg> History</h2>
        <div class="chart-controls">
          <select id="dd-metric" aria-label="Metric"></select>
          <div class="range-btns" id="dd-range">
            <button data-range="1h">1h</button>
            <button data-range="6h">6h</button>
            <button data-range="24h" class="active">24h</button>
            <button data-range="7d">7d</button>
            <button data-range="30d">30d</button>
          </div>
        </div>
      </div>
      <div id="dd-chart" class="chart-host" data-default-metric="${defaultMetric}"></div>
    </section>
  `;
}

function wireDeviceDetailChart(dev) {
  const select = document.querySelector("#dd-metric");
  const host = document.querySelector("#dd-chart");
  if (!select || !host) return;
  const defaultMetric = host.dataset.defaultMetric;

  // Populate metric dropdown from this device's latest numeric keys
  const l = dev.latest || {};
  const keys = Object.entries(l)
    .filter(([k, v]) => typeof v === "number" && !k.startsWith("_"))
    .map(([k]) => k)
    .sort();
  select.innerHTML = keys.map(k => `<option value="${k}">${prettyKey(k)}</option>`).join("");
  if (keys.includes(defaultMetric)) select.value = defaultMetric;

  let range = "24h";
  document.querySelectorAll("#dd-range button").forEach(b => {
    b.addEventListener("click", () => {
      document.querySelectorAll("#dd-range button").forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      range = b.dataset.range;
      draw();
    });
  });
  select.addEventListener("change", draw);

  async function draw() {
    const m = select.value;
    if (!m) return;
    const [since, bucket] = sinceForRange(range);
    let data;
    try {
      data = await api(`/api/devices/${encodeURIComponent(dev.label)}/history?metric=${encodeURIComponent(m)}&since=${since}&bucket=${bucket}`);
    } catch (e) { console.error(e); return; }
    if (devDetailChart) { devDetailChart.destroy(); devDetailChart = null; }
    const unit = unitFromKey(m);
    const width = Math.max(host.clientWidth, 320);
    const pal = chartPalette();
    try {
      devDetailChart = new uPlot({
        width, height: 320,
        scales: { x: { time: true } },
        series: [
          {},
          {
            label: prettyKey(m),
            stroke: pal.accent,
            width: 2,
            fill: pal.accentFill,
            value: (_u, v) => v == null ? "·" : `${(+v).toFixed(2)}${unit ? " " + unit : ""}`,
          },
        ],
        axes: [
          { stroke: pal.axis, grid: { stroke: pal.grid } },
          { stroke: pal.axis, grid: { stroke: pal.grid },
            // Wider left margin so multi-digit values like "15.0 A"
            // don't get their leading digit clipped by uPlot's default
            // axis allotment (~40 px, fits "0.00 A" but truncates
            // "10.00 A" → looks like "0.00 A"; "15.00 A" → looks
            // like "5.00 A"). 60 px holds up to "999.9 W".
            size: 60,
            // Adaptive precision: integers for values ≥ 10, one
            // decimal for small values. Drops the perma-".00 A" tail
            // that wasn't useful and was making labels overflow on
            // narrow screens.
            values: (_u, splits) => splits.map(v => {
              if (v == null) return "";
              const n = +v;
              const abs = Math.abs(n);
              const txt = abs >= 100 ? n.toFixed(0)
                        : abs >= 10  ? n.toFixed(1)
                        :              n.toFixed(2);
              return unit ? `${txt} ${unit}` : txt;
            }) },
        ],
        plugins: [valueTooltipPlugin()],
      }, [data.ts, data.values], host);
    } catch (e) { console.error(e); }
  }
  draw();

  // Lifetime stats (smart battery only)
  if (dev.kind === "smart_battery") {
    ensureLifetime(dev.label).then(lt => {
      if (!lt) return;
      const block = document.querySelector(`#dd-lifetime-${dev.label}`);
      if (!block) return;
      block.querySelector('[data-lt="cycles"]').textContent = lt.cycles?.toFixed(2) ?? "·";
      block.querySelector('[data-lt="ah_in"]').textContent = `${(+lt.ah_in).toFixed(1)} Ah`;
      block.querySelector('[data-lt="ah_out"]').textContent = `${(+lt.ah_out).toFixed(1)} Ah`;
    });
    ensureEfficiency(dev.label).then(eff => {
      if (!eff) return;
      const block = document.querySelector(`#dd-lifetime-${dev.label}`);
      const detail = document.querySelector(`#dd-eff-${dev.label}`);
      if (!block) return;
      // Headline cell (same logic as device-card)
      const cell = block.querySelector('[data-lt-eff]');
      const val  = cell?.querySelector('[data-lt="eff"]');
      const winLabel = cell?.querySelector('.lt-eff-win');
      const h = efficiencyHeadline(eff);
      if (h && val) {
        val.textContent = `${h.value.toFixed(1)} %`;
        if (winLabel) winLabel.textContent = h.window;
        cell.classList.toggle("lt-cell--unreliable", !h.reliable);
      }
      // Per-window breakdown table — only shown on the device detail page
      const grid = detail?.querySelector("[data-eff-grid]");
      if (!grid) return;
      const order = ["7d", "30d", "90d", "lifetime"];
      grid.innerHTML = order.map(k => {
        const w = eff.windows?.[k];
        if (!w) return "";
        const v = w.efficiency_pct;
        const cls = w.reliable ? "" : " eff-cell--unreliable";
        const txt = v == null ? "·" : `${v.toFixed(1)} %`;
        return `<div class="eff-cell${cls}">
          <span class="meta-k">${k}</span>
          <span class="eff-cell-val">${txt}</span>
          <span class="eff-cell-foot">${w.cycle_equivalents?.toFixed(2) ?? "·"} cyc</span>
        </div>`;
      }).join("");
      detail.hidden = false;
    });
  }
}

// ---------- setup wizard ----------
// Two REST endpoints carry it: /api/setup/transports + /api/setup/probe +
// /api/setup/add_device. UI is intentionally bare — no modals, no router —
// just an inline state machine that walks transport → scan → add per row.
let wizState = { transport: null, scanResults: null, knownKeys: new Set() };

const KIND_LABEL = {
  smart_battery: "Smart battery",
  charge_controller: "Charge controller",
};

function wizKnownKey(transport, slaveId) { return `${transport}::${slaveId}`; }

async function wizLoadTransports() {
  const host = $("#wiz-transports");
  try {
    const [{ transports }, { devices }] = await Promise.all([
      api("/api/setup/transports"),
      api("/api/setup/known_devices"),
    ]);
    wizState.knownKeys = new Set(devices.map(d => wizKnownKey(d.transport, d.slave_id)));
    if (!transports.length) {
      host.innerHTML = `<div class="wiz-empty">
        <p><strong>First time? Let's connect your gear.</strong></p>
        <p>How is your charger / battery / shunt wired to the Pi? Most people use a Bluetooth dongle (Renogy BT-2). Choose one to start scanning:</p>
        <div class="wiz-controls" style="flex-wrap:wrap;gap:.5rem">
          <button id="wiz-find-dongle-btn" class="btn-action btn-action--primary">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 7l10 10M7 17L17 7M12 2v20M7 7l5-5 5 5M7 17l5 5 5-5"/></svg>
            <span>Bluetooth (e.g. Renogy BT-2)</span>
          </button>
          <button id="wiz-find-usb-btn" class="btn-action">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="2.5" r="1.5"/><path d="M12 4v18"/><path d="M8 9h8"/><path d="M8 9l-2 4 2 4h8l2-4-2-4"/></svg>
            <span>Wired (USB-RS485 adapter)</span>
          </button>
          <span id="wiz-find-status" class="wiz-status"></span>
        </div>
        <p class="settings-foot" style="margin:.4rem 0 0">
          Wired uses a USB-to-RS485 dongle (~£10, FTDI / CH340 chip) on the Pi, with Cat5 to your charger's RJ45 port. That port is RS-485, NOT Ethernet, so it doesn't plug into the Pi's network jack.
        </p>
        <p class="settings-foot" style="margin:.4rem 0 0">
          Victron and JK BMS devices broadcast over Bluetooth on their own. No dongle needed. Pick Bluetooth above and the wizard will find them.
        </p>
        <div id="wiz-find-results" class="wiz-results"></div>
      </div>`;
      document.getElementById("wiz-find-dongle-btn")?.addEventListener("click", wizFindDongle);
      document.getElementById("wiz-find-usb-btn")?.addEventListener("click", wizFindUsb);
      return;
    }
    host.innerHTML = transports.map(t => {
      // Serial transports carry `port` (/dev/ttyUSB0); BLE carry
      // `address` (MAC). Either reads as the transport's identity.
      const addr = t.address || t.port || '';
      const kind = t.type === 'serial_modbus' ? 'USB-RS485'
                 : t.type === 've_direct'     ? 'Victron VE.Direct'
                 : 'Bluetooth';
      return `
      <div class="wiz-transport-row">
        <button class="wiz-transport ${t.id === wizState.transport ? 'active' : ''}" data-id="${escHtml(t.id)}">
          <div class="wiz-transport-main">
            <span class="wiz-transport-id">${escHtml(t.id)}</span>
            <span class="wiz-transport-addr">${escHtml(kind)} · ${escHtml(addr)}</span>
          </div>
          <span class="wiz-transport-state ${t.open ? 'on' : 'off'}" title="${t.open ? 'Transport is connected and polled every cycle.' : 'Daemon is auto-retrying every poll (~60 s). No action needed; it reconnects as soon as the device starts advertising again.'}">${t.open ? 'connected' : 'offline · retrying'}</span>
        </button>
        <button class="wiz-transport-del" data-edit-transport="${escHtml(t.id)}"
                data-edit-type="${escHtml(t.type || '')}"
                data-edit-address="${escHtml(t.address || '')}"
                data-edit-port="${escHtml(t.port || '')}"
                title="Edit this transport's address / encryption key">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>
        </button>
        <button class="wiz-transport-del" data-del-transport="${escHtml(t.id)}" title="Disconnect + remove this transport">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>
        </button>
      </div>`;
    }).join("") + `
      <div class="wiz-add-another">
        <button class="btn-action wiz-add-another-btn" id="wiz-add-another-btn">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          <span>Add another connection</span>
        </button>
        <div class="wiz-add-another-panel" id="wiz-add-another-panel" hidden>
          <p class="settings-foot" style="margin:.5rem 0">
            Bluetooth + USB-RS485 can run side by side on the same Pi ·
            e.g. a Renogy BT-2 for the MPPT and a USB dongle for a
            JK BMS. Pick the connection type for the next adapter:
          </p>
          <div class="wiz-controls" style="flex-wrap:wrap;gap:.5rem">
            <button id="wiz-find-dongle-btn" class="btn-action btn-action--primary">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 7l10 10M7 17L17 7M12 2v20M7 7l5-5 5 5M7 17l5 5 5-5"/></svg>
              <span>Bluetooth</span>
            </button>
            <button id="wiz-find-usb-btn" class="btn-action">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="2.5" r="1.5"/><path d="M12 4v18"/><path d="M8 9h8"/><path d="M8 9l-2 4 2 4h8l2-4-2-4"/></svg>
              <span>Wired (USB-RS485)</span>
            </button>
            <span id="wiz-find-status" class="wiz-status"></span>
          </div>
          <div id="wiz-find-results" class="wiz-results"></div>
        </div>
      </div>`;
    // Wire the collapsible "Add another" panel — same two scan
    // buttons reuse wizFindDongle / wizFindUsb so the BLE+USB
    // mixed-install path is identical regardless of whether the
    // user is adding their first or fifth transport.
    document.getElementById("wiz-add-another-btn")?.addEventListener("click", () => {
      const panel = document.getElementById("wiz-add-another-panel");
      if (panel) panel.hidden = !panel.hidden;
    });
    document.getElementById("wiz-find-dongle-btn")?.addEventListener("click", wizFindDongle);
    document.getElementById("wiz-find-usb-btn")?.addEventListener("click", wizFindUsb);
    // No `disabled` on offline rows — the scan endpoint auto-reopens
    // a dropped BLE link, so users should always be able to select
    // and try. The pill text tells them what to expect.
    const selectTransport = (id) => {
      wizState.transport = id;
      host.querySelectorAll(".wiz-transport").forEach(
        b => b.classList.toggle("active", b.dataset.id === id),
      );
      $("#wiz-step-scan").hidden = false;
      $("#wiz-scan-results").innerHTML = "";
      $("#wiz-scan-status").textContent = "";
    };
    host.querySelectorAll(".wiz-transport").forEach(btn => {
      btn.addEventListener("click", () => selectTransport(btn.dataset.id));
    });
    host.querySelectorAll("[data-del-transport]").forEach(btn => {
      btn.addEventListener("click", () => wizDeleteTransport(btn.dataset.delTransport));
    });
    host.querySelectorAll("[data-edit-transport]").forEach(btn => {
      btn.addEventListener("click", () => wizEditTransport({
        id:      btn.dataset.editTransport,
        type:    btn.dataset.editType,
        address: btn.dataset.editAddress,
        port:    btn.dataset.editPort,
      }));
    });
    // Auto-select when there's only one transport — there's no
    // meaningful "pick" to make, so making the user tap a row before
    // Scan works is friction with no upside. The previous fix told
    // users to "Pick a transport above first" but the wizard didn't
    // make obvious that the row was tappable; this short-circuits
    // that whole UX trap.
    if (transports.length === 1 && !wizState.transport) {
      selectTransport(transports[0].id);
    }
  } catch (e) {
    host.innerHTML = `<div class="wiz-empty">Could not load transports: ${e.message}</div>`;
  }
}

async function wizScan() {
  const btn = $("#wiz-scan-btn");
  const status = $("#wiz-scan-status");
  const host = $("#wiz-scan-results");
  if (!wizState.transport) {
    // Don't silently no-op — the previous version did, which left
    // a user clicking with no feedback when their transport
    // selection was lost (e.g. after a page refresh).
    if (status) {
      status.textContent = "Pick a transport above first.";
    }
    return;
  }
  btn.disabled = true;
  host.innerHTML = "";
  status.innerHTML = `<span class="wiz-spinner" aria-hidden="true"></span> Starting scan…`;

  // Live state — devices found so far, used both to render
  // progressively and to feed renderScanResults at the end.
  const alive = [];
  let total = 0;
  let probedCount = 0;
  let aborted = false;

  const repaint = () => {
    status.innerHTML = total > 0
      ? `<span class="wiz-spinner" aria-hidden="true"></span> Probed ${probedCount} of ${total} · ${alive.length} responded`
      : `<span class="wiz-spinner" aria-hidden="true"></span> Probing…`;
    renderScanResults(alive, /*partial*/ true);
  };

  try {
    const r = await fetch("/api/setup/probe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transport: wizState.transport }),
    });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    if (!r.body) throw new Error("no response body");

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // NDJSON: split on newlines, keep the trailing partial line in buf.
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        let ev;
        try { ev = JSON.parse(line); } catch (_) { continue; }
        if (ev.event === "reopening") {
          status.innerHTML = `<span class="wiz-spinner" aria-hidden="true"></span> Reconnecting BLE link…`;
        } else if (ev.event === "open_failed") {
          aborted = true;
          status.textContent = `Couldn't open BLE link: ${ev.error}`;
        } else if (ev.event === "start") {
          total = ev.total;
          repaint();
        } else if (ev.event === "probing") {
          probedCount = ev.index - 1;
          repaint();
        } else if (ev.event === "result") {
          probedCount += 1;
          if (ev.alive) alive.push(ev);
          repaint();
        } else if (ev.done) {
          // Final summary — fall through to the "finished" block.
        }
      }
    }

    if (!aborted) {
      status.textContent = alive.length
        ? `${alive.length} device(s) found · ${total} probed`
        : `No devices responded · ${total} probed`;
      renderScanResults(alive, /*partial*/ false);
    }
  } catch (e) {
    // A save's hot-reload kills the scan stream — that's expected,
    // not an error. Detect via the wizState.saveInFlight counter:
    // any save in flight when the stream tore down means the abort
    // came from us, not from a real failure. Tell the user honestly
    // so they know the scan paused, didn't crash.
    if ((wizState.saveInFlight || 0) > 0) {
      status.textContent = alive.length
        ? `Scan paused · ${alive.length} device(s) found so far. Click Scan to continue.`
        : `Scan paused while adding a device. Click Scan to continue.`;
      renderScanResults(alive, /*partial*/ false);
    } else {
      status.textContent = `Scan failed: ${e.message}`;
    }
  } finally {
    btn.disabled = false;
    wizState.scanResults = alive;
  }
}

function renderScanResults(alive, partial = false) {
  const host = $("#wiz-scan-results");
  if (!alive.length) {
    // While the scan is still running, don't drop the long help
    // text in — it'd flash on screen for milliseconds before the
    // first device arrives and feel jumpy. Show a simple
    // placeholder; the full troubleshooter appears only if the
    // scan finishes with zero hits.
    if (partial) {
      host.innerHTML = `<div class="wiz-empty wiz-empty--quiet">Waiting for the first device to respond…</div>`;
      return;
    }
    host.innerHTML = `<div class="wiz-empty">
      <p><strong>No Renogy devices responded.</strong></p>
      <p>The BT-2 is connected to the daemon, but nothing on its RS-485 side answered Modbus. Most common causes, in order:</p>
      <ol style="margin:.5rem 0 .5rem 1.2rem">
        <li><strong>The BT-2 isn't plugged into a powered Renogy device.</strong> Push it firmly into the RJ45 / RJ12 comms port on a charge controller or battery; that device needs power (solar panel connected for an MPPT, or a battery being load-tested).</li>
        <li><strong>The Renogy device is asleep.</strong> DCC chargers / some BMS units go to sleep with no solar input. Cover the panels with a cloth and shine a torch on them, or attach a small load to wake them.</li>
        <li><strong>Non-standard slave ID.</strong> We probe 1, 16, 32–36, 48–55, 96, 97 (Renogy factory defaults). If you've reconfigured a device's slave ID via the Renogy app, you'll need to add the device manually for now.</li>
        <li><strong>Cold BLE link.</strong> If the BT-2 only just connected, try Scan one more time. First round-trip is occasionally slow enough to time out.</li>
      </ol>
      <p class="settings-foot">Diagnostics tab in Settings has live daemon logs if you want to see what each probe is doing.</p>
    </div>`;
    return;
  }
  host.innerHTML = alive.map(r => {
    const known = wizState.knownKeys.has(wizKnownKey(wizState.transport, r.slave_id));
    const kindLbl = KIND_LABEL[r.kind] || r.kind || "Unknown";
    return `
      <div class="wiz-row ${known ? 'known' : ''}" data-slave="${r.slave_id}">
        <div class="wiz-row-main">
          <div class="wiz-row-title">
            <span class="wiz-row-slave">#${r.slave_id}</span>
            <span class="wiz-row-model">${r.model || '·'}</span>
          </div>
          <div class="wiz-row-meta">
            <span class="wiz-tag">${r.vendor || 'unknown vendor'}</span>
            <span class="wiz-tag">${kindLbl}</span>
            ${known ? '<span class="wiz-tag wiz-tag--known">already added</span>' : ''}
          </div>
        </div>
        <div class="wiz-row-action">
          ${known
            ? `<button class="wiz-transport-del" data-del-device="${r.slave_id}" title="Remove this device">
                 <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>
               </button>`
            : `<button class="btn-action btn-action--primary wiz-add-btn">+ Add</button>`}
        </div>
      </div>
    `;
  }).join("");
  host.querySelectorAll(".wiz-row").forEach(row => {
    const btn = row.querySelector(".wiz-add-btn");
    if (btn) btn.addEventListener("click", () => wizExpandRow(row));
    const delBtn = row.querySelector("[data-del-device]");
    if (delBtn) delBtn.addEventListener("click", () => wizDeleteDevice(+delBtn.dataset.delDevice));
  });
}

async function wizDeleteDevice(slaveId) {
  if (!wizState.transport) return;
  if (!confirm(
    `Remove device on slave ${slaveId} from transport ${wizState.transport}?\n\n` +
    "Polling stops immediately. The current config.yaml is backed up to .bak."
  )) return;
  let res;
  try {
    const r = await fetch(
      `/api/setup/devices/${slaveId}?transport=${encodeURIComponent(wizState.transport)}`,
      { method: "DELETE" },
    );
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    res = await r.json();
  } catch (e) {
    alert("Couldn't remove device: " + (e.message || String(e)));
    return;
  }
  // Re-render the scan list so the row flips back to "+ Add" / no
  // trash icon, then reload transports list for fresh state.
  await wizLoadTransports();
  if (res.reload_error) {
    alert(`Device removed, but hot-reload failed: ${res.reload_error}\nRestart the daemon.`);
  }
}

// In-place edit: change a transport's address / encryption key / port
// without the delete-and-recreate dance. The transport id stays the
// same (history rows + MQTT topics + device references all key off
// it), so all we're mutating is the credentials.
async function wizEditTransport({ id, type, address, port }) {
  let promptLabel, promptDefault, field, second = null;
  if (type === "ble_modbus") {
    promptLabel = "New BLE MAC for " + id + " (e.g. CC:45:A5:83:B7:42):";
    promptDefault = address || "";
    field = "address";
  } else if (type === "ble_victron_advertise") {
    promptLabel = "New BLE MAC for " + id + " (leave blank to keep):";
    promptDefault = address || "";
    field = "address";
    second = {
      label: "New encryption key (32 hex chars, leave blank to keep):",
      field: "encryption_key",
    };
  } else if (type === "serial_modbus") {
    promptLabel = "New serial port for " + id + " (e.g. /dev/ttyUSB0):";
    promptDefault = port || "";
    field = "port";
  } else {
    alert("Don't know how to edit a " + type + " transport here yet.");
    return;
  }
  const v1 = window.prompt(promptLabel, promptDefault);
  if (v1 === null) return;  // cancelled
  const body = {};
  if (v1.trim()) body[field] = v1.trim();
  if (second) {
    const v2 = window.prompt(second.label, "");
    if (v2 === null) return;  // cancelled
    if (v2.trim()) body[second.field] = v2.trim();
  }
  if (Object.keys(body).length === 0) {
    // Nothing to update — let them know rather than silently no-op.
    alert("Nothing changed.");
    return;
  }
  try {
    const r = await fetch(
      `/api/setup/transports/${encodeURIComponent(id)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
  } catch (e) {
    alert("Couldn't update transport: " + (e.message || String(e)));
    return;
  }
  await wizLoadTransports();
}

async function wizDeleteTransport(transportId) {
  if (!confirm(
    `Disconnect and remove transport "${transportId}"?\n\n` +
    "This also removes every device configured on it. " +
    "The current config.yaml is backed up to .bak. Continue?"
  )) return;
  let res;
  try {
    const r = await fetch(
      `/api/setup/transports/${encodeURIComponent(transportId)}`,
      { method: "DELETE" },
    );
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    res = await r.json();
  } catch (e) {
    alert("Couldn't remove transport: " + (e.message || String(e)));
    return;
  }
  if (wizState.transport === transportId) {
    wizState.transport = null;
    $("#wiz-step-scan").hidden = true;
  }
  await wizLoadTransports();
  if (res.devices_removed > 0) {
    // Surface the cascade so the user isn't surprised later that
    // their device list shrank.
    console.info(`Removed ${res.devices_removed} child device(s) along with transport ${transportId}`);
  }
}

function wizExpandRow(row) {
  const slave = +row.dataset.slave;
  const result = wizState.scanResults.find(r => r.slave_id === slave);
  if (!result) return;
  const defaultLabel = result.kind === "smart_battery"
    ? `battery_${slave - 48 >= 0 && slave - 48 < 16 ? slave - 48 : slave}`
    : (result.kind === "charge_controller" ? "charge_controller" : `device_${slave}`);
  row.querySelector(".wiz-row-action").innerHTML = `
    <form class="wiz-add-form">
      <input type="text" class="wiz-label" value="${defaultLabel}" placeholder="label" required />
      <button type="submit" class="btn-action btn-action--primary">Save</button>
      <button type="button" class="btn-action wiz-cancel">Cancel</button>
    </form>
  `;
  const form = row.querySelector(".wiz-add-form");
  form.querySelector(".wiz-cancel").addEventListener("click", () => {
    row.querySelector(".wiz-row-action").innerHTML = `<button class="btn-action btn-action--primary wiz-add-btn">+ Add</button>`;
    row.querySelector(".wiz-add-btn").addEventListener("click", () => wizExpandRow(row));
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const label = form.querySelector(".wiz-label").value.trim();
    if (!label) return;
    const save = form.querySelector("button[type='submit']");
    save.disabled = true;
    save.textContent = "Saving…";
    // Tell the scan loop "an in-flight save is about to kill your
    // stream — don't treat that as a real error." Cleared after the
    // save settles, regardless of outcome. Concurrent saves bump
    // the counter so the last one to finish clears it.
    wizState.saveInFlight = (wizState.saveInFlight || 0) + 1;
    try {
      const r = await fetch("/api/setup/add_device", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          transport: wizState.transport,
          vendor: result.vendor,
          kind: result.kind,
          slave_id: slave,
          label,
        }),
      });
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}));
        // "Already configured" can fire when the response from a
        // previous save was disrupted (e.g. the scan stream dying
        // killed our network state). Treat as soft success: pull
        // the existing record + render Saved so the row stops
        // begging for input.
        if (r.status === 409 && /already configured/i.test(detail.detail || "")) {
          wizState.knownKeys.add(wizKnownKey(wizState.transport, slave));
          row.classList.add("known");
          row.querySelector(".wiz-row-action").innerHTML =
            `<span class="wiz-saved">Saved · ${escHtml(label)}</span>`;
          return;
        }
        throw new Error(detail.detail || `${r.status} ${r.statusText}`);
      }
      const data = await r.json();
      wizState.knownKeys.add(wizKnownKey(wizState.transport, slave));
      row.classList.add("known");
      row.querySelector(".wiz-row-action").innerHTML = `<span class="wiz-saved">Saved · ${data.label}</span>`;
      if (data.restart_required) showRestartBanner();
    } catch (e) {
      save.disabled = false;
      save.textContent = "Save";
      const msg = document.createElement("div");
      msg.className = "wiz-error";
      msg.textContent = e.message;
      form.appendChild(msg);
    } finally {
      wizState.saveInFlight = Math.max(0, (wizState.saveInFlight || 1) - 1);
      // Refresh server-side state so the UI doesn't drift if a
      // previous save's response was eaten by the stream tear-down.
      // Best-effort — failures here are silent.
      try {
        const kr = await fetch("/api/setup/known_devices");
        if (kr.ok) {
          const kd = await kr.json();
          wizState.knownKeys = new Set(
            kd.devices.map(d => wizKnownKey(d.transport, d.slave_id)),
          );
        }
      } catch (_) { /* ignore */ }
    }
  });
}

function showRestartBanner() {
  const el = $("#wiz-restart");
  if (el) el.hidden = false;
}

$("#wiz-scan-btn").addEventListener("click", wizScan);

// Lazy-load when user navigates to Setup so we don't waste a request on
// every page load. setRoute() fires this hook.
function onEnterSetup() {
  wizLoadTransports();
  wizCheckBleStatus();
}

// "Find my dongle" — scans BLE for ~8 s and surfaces every advertising
// device the host can see. User picks theirs by name pattern (BT-2
// dongles advertise as BT-TH-…) or by the MAC printed on the dongle.
async function wizFindDongle() {
  const btn   = document.getElementById("wiz-find-dongle-btn");
  const stat  = document.getElementById("wiz-find-status");
  const list  = document.getElementById("wiz-find-results");
  if (!btn) return;
  btn.disabled = true;
  stat.textContent = "Scanning Bluetooth for 8 seconds…";
  list.innerHTML = "";
  let data;
  try {
    const r = await fetch("/api/setup/ble_scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ seconds: 8 }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    data = await r.json();
  } catch (e) {
    stat.textContent = "";
    list.innerHTML = `<div class="wiz-empty">${escHtml(e.message || String(e))}</div>`;
    btn.disabled = false;
    return;
  }
  stat.textContent = `Found ${data.devices?.length || 0} device(s) in ${data.scanned_seconds || 8} s`;
  btn.disabled = false;

  // Build the "recently disappeared" panel first if any — this is
  // the #1 debugging signal for "where did my Renogy BT-2 go?"
  // shown ABOVE the live results so users notice it before they
  // give up assuming nothing's there.
  const missingHtml = renderMissingPanel(data.seen_recently_missing || []);
  // LAN peer panel (#184). Surfaces "another WattPost on your
  // network is probably holding the BT-2" when the scan came back
  // without any Renogy hits AND the backend found peers on the
  // same subnet. Backend only populates lan_peers when zero Renogy
  // devices were found, so we render unconditionally if present.
  const lanPeerHtml = renderLanPeerPanel(data.lan_peers || []);

  if (!data.devices?.length) {
    list.innerHTML = missingHtml + lanPeerHtml + `<div class="wiz-empty">Nothing visible right now. Check the dongle is powered (BT-2 has a small LED) and within ~5 m. Try again.</div>`;
    return;
  }
  // Build a row per discovered device. Detection happens server-side
  // (manufacturer ID for Victron, name pattern for Renogy/JK); the UI
  // routes each row to the right add-transport flow.
  //
  //   * Renogy BT-2 → "Use this" → wizAddTransportFromMac (ble_modbus)
  //   * Victron     → "Pair Victron" → expands a key-entry inline form
  //                                    → wizAddTransportFromVictron
  //   * JK BMS      → "Pair JK BMS" → wizAddTransportFromJk (placeholder
  //                                   until #114-wizard support lands)
  //   * Unknown     → "Use as Modbus" + manual-Y type warning
  list.innerHTML = missingHtml + lanPeerHtml + data.devices.map(d => {
    const nameStr = d.name
      ? `<strong>${escHtml(d.name)}</strong>`
      : `<em class="settings-foot">(no name)</em>`;
    const rssi = d.rssi != null ? `${d.rssi} dBm` : "·";

    // Vendor-specific hint badge + action button.
    const vendor = d.vendor || "unknown";
    let hintHtml = "";
    let actionHtml = "";
    let keyFormHtml = "";
    if (vendor === "victron") {
      // The Victron-specific device class (when we could detect it
      // from the payload header — SmartShunt/SmartSolar/Orion-Tr etc.)
      // makes the badge richer than just "Victron".
      const dc = d.victron_device_class || "Victron device";
      const dcRaw = d.victron_device_class || "";
      hintHtml = `<span class="wiz-vendor-hint wiz-vendor-hint--victron">${escHtml(dc)}</span>`;
      // Stash device_class on the Pair button so the Save handler can
      // pass it to the backend — needed to pick the right device_kind
      // when auto-creating the device row (#159-adjacent fix).
      actionHtml = `<button class="btn-action btn-action--primary" data-pair-victron="${escHtml(d.address)}" data-victron-class="${escHtml(dcRaw)}">Pair Victron</button>`;
      // The key form is rendered hidden and revealed when the user
      // taps "Pair Victron" — keeps the scan-results card compact.
      keyFormHtml = `
        <div class="wiz-victron-key" data-victron-key-for="${escHtml(d.address)}" hidden style="margin-top:.6rem;padding:.7rem;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--r-md)">
          <p class="settings-foot" style="margin:0 0 .5rem">
            Open VictronConnect on your phone, connect to this device,
            tap <strong>Settings</strong> → <strong>Product info</strong>,
            then <strong>Show</strong> next to <strong>Instant readout
            encryption data</strong>. Paste it here.
          </p>
          <label style="display:block;margin-bottom:.5rem">
            <span class="settings-foot" style="display:block;margin-bottom:.25rem">Encryption key (32 hex chars)</span>
            <input type="text"
                   inputmode="latin"
                   autocapitalize="none"
                   autocorrect="off"
                   spellcheck="false"
                   autocomplete="off"
                   class="wiz-key-input"
                   placeholder="e.g. 2cf1e4a8…"
                   pattern="[0-9a-fA-F]{32}"
                   maxlength="32"
                   style="display:block;width:100%;box-sizing:border-box;padding:.55rem .65rem;font-family:var(--mono);font-size:1rem;background:var(--surface-3);border:1px solid var(--border);border-radius:var(--r-sm);color:var(--text)"/>
          </label>
          <div style="display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.5rem">
            <button class="btn-action btn-action--primary" data-victron-save="${escHtml(d.address)}" style="min-height:2.4rem;flex:1;min-width:6rem">Save key</button>
            <button class="btn-action" data-victron-cancel="${escHtml(d.address)}" style="min-height:2.4rem">Cancel</button>
          </div>
          <div class="wiz-victron-status settings-foot" data-victron-status="${escHtml(d.address)}" style="margin-top:.5rem;min-height:1.2rem"></div>
        </div>`;
    } else if (vendor === "renogy") {
      hintHtml = `<span class="wiz-vendor-hint">Renogy BT-2 / BT-1</span>`;
      actionHtml = `<button class="btn-action btn-action--primary" data-use-mac="${escHtml(d.address)}" data-use-name="${escHtml(d.name || '')}">Use this</button>`;
    } else if (vendor === "jkbms") {
      hintHtml = `<span class="wiz-vendor-hint wiz-vendor-hint--warn">JK BMS</span>`;
      // JK BMS wizard support isn't built yet (driver shipped in v0.0.21);
      // surface the device + the manual-config workaround.
      actionHtml = `<button class="btn-action" disabled title="JK BMS wizard support is on the roadmap. For now, add the transport manually via config.yaml (type: ble_jkbms).">JK BMS. Manual config needed</button>`;
    } else {
      hintHtml = "";
      actionHtml = `<button class="btn-action btn-action--primary" data-use-mac="${escHtml(d.address)}" data-use-name="${escHtml(d.name || '')}">Use as Modbus</button>`;
    }
    return `
      <div class="wiz-result-row">
        <div class="wiz-result-info">
          ${nameStr} ${hintHtml}
          <div class="settings-foot">${escHtml(d.address)} · ${rssi}</div>
        </div>
        ${actionHtml}
      </div>
      ${keyFormHtml}`;
  }).join("");

  // Renogy / fallback path — unchanged.
  list.querySelectorAll("[data-use-mac]").forEach(b => {
    b.addEventListener("click", () => wizAddTransportFromMac(b.dataset.useMac, b.dataset.useName));
  });
  // Victron pairing — show + wire the key-entry form.
  list.querySelectorAll("[data-pair-victron]").forEach(b => {
    const mac = b.dataset.pairVictron;
    b.addEventListener("click", () => {
      const form = list.querySelector(`[data-victron-key-for="${mac}"]`);
      if (form) {
        form.hidden = false;
        b.disabled = true;
        form.querySelector(".wiz-key-input")?.focus();
      }
    });
  });
  list.querySelectorAll("[data-victron-cancel]").forEach(b => {
    const mac = b.dataset.victronCancel;
    b.addEventListener("click", () => {
      const form = list.querySelector(`[data-victron-key-for="${mac}"]`);
      const pair = list.querySelector(`[data-pair-victron="${mac}"]`);
      if (form) form.hidden = true;
      if (pair) pair.disabled = false;
    });
  });
  list.querySelectorAll("[data-victron-save]").forEach(b => {
    const mac = b.dataset.victronSave;
    // Pick up the device_class the scan card stashed on the Pair
    // button (the one in the same row as this Save button). Passed
    // through to wizAddTransportFromVictron so the backend can
    // auto-create the device row with the right kind.
    const pairBtn = list.querySelector(`[data-pair-victron="${mac}"]`);
    const deviceClass = pairBtn?.dataset.victronClass || "";
    b.addEventListener("click", () => {
      const form = list.querySelector(`[data-victron-key-for="${mac}"]`);
      const status = list.querySelector(`[data-victron-status="${mac}"]`);
      const raw = (form?.querySelector(".wiz-key-input")?.value || "").trim();
      // Normalise: lowercase, strip any spaces / colons / dashes the
      // user might have included copy-pasting. Victron-Connect copies
      // a clean 32-char hex but transit through chat / notepad apps
      // can sneak whitespace in.
      const key = raw.replace(/[\s:\-]/g, "").toLowerCase();
      if (!/^[0-9a-f]{32}$/.test(key)) {
        if (status) {
          status.textContent = `Expected 32 hex chars; got ${key.length}. ` +
            `Double-check VictronConnect → Settings → Product info → ` +
            `Instant readout encryption data.`;
          status.style.color = "var(--err)";
        }
        return;
      }
      if (status) status.style.color = "";
      wizAddTransportFromVictron(mac, key, deviceClass);
    });
  });
}

function renderMissingPanel(missing) {
  if (!missing || !missing.length) return "";
  // Format "30 seconds ago" / "3 min ago" — a generic relative
  // string is more useful than "37 seconds ago" precision.
  const ago = (s) => {
    if (s == null) return "recently";
    if (s < 60)   return `${s}s ago`;
    if (s < 3600) return `${Math.round(s/60)} min ago`;
    return `${Math.round(s/3600)} h ago`;
  };
  return `<div class="wiz-missing">
    <div class="wiz-missing-head">
      <strong>Recently visible, not broadcasting right now</strong>
      <span class="settings-foot">${missing.length} device(s)</span>
    </div>
    ${missing.map(m => `
      <div class="wiz-missing-row">
        <div>
          ${m.name ? `<strong>${escHtml(m.name)}</strong>` : `<em class="settings-foot">(no name)</em>`}
          <div class="settings-foot">${escHtml(m.address)} · last RSSI ${m.last_rssi ?? "·"} dBm · ${ago(m.seconds_ago)}</div>
        </div>
        <div class="wiz-missing-cause">${escHtml(m.likely_cause || "")}</div>
      </div>
    `).join("")}
  </div>`;
}

// Warn the user when another WattPost on the same LAN looks
// suspicious. Issue #184: a Renogy BT-2 dongle is a single-master
// device; if a laptop / NAS / other Pi on the network has the
// same dongle in its config and is connected to it, the BT-2 stops
// advertising entirely and our local scan finds nothing. The
// debugging story for this took most of a day; surfacing it as a
// first-class hint saves customers from the same trip.
function renderLanPeerPanel(peers) {
  if (!peers || !peers.length) return "";
  const lines = peers.map(p => {
    const v = p.version ? ` (v${escHtml(String(p.version))})` : "";
    return `<li><code>${escHtml(p.ip)}</code>${v}</li>`;
  }).join("");
  return `<div class="wiz-missing">
    <div class="wiz-missing-head">
      <strong>Another WattPost is running on this network</strong>
      <span class="settings-foot">${peers.length} peer(s)</span>
    </div>
    <div class="wiz-missing-row">
      <div>
        <ul style="margin:0 0 .5rem 1rem;padding:0">${lines}</ul>
        <div class="settings-foot">
          A Renogy BT-2 dongle only allows one connection at a time.
          If the same dongle is already paired on the host above,
          this appliance won't see it. Either remove the dongle from
          the other host (Settings → Devices → Delete) or stop its
          WattPost daemon, then scan again.
        </div>
      </div>
    </div>
  </div>`;
}

async function wizAddTransportFromVictron(mac, key, deviceClass) {
  // Per-row status reflects what's happening so the user doesn't
  // have to hunt for the wiz-find-status global.
  const status = document.querySelector(`[data-victron-status="${mac}"]`);
  if (status) status.textContent = "Saving…";
  let res;
  try {
    const r = await fetch("/api/setup/transports/add", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        type: "ble_victron_advertise",
        address: mac,
        encryption_key: key,
        // Pass the victron-ble class name the scan detected so the
        // backend auto-creates the device row with the right kind
        // (ac_charger / shunt / charge_controller / …) instead of
        // falling back to a generic default.
        device_class: deviceClass || "",
      }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    res = await r.json();
  } catch (e) {
    if (status) status.textContent = e.message || String(e);
    return;
  }
  if (status) status.textContent = `Added · ${res.label || res.id}. Polling now.`;
  // Refresh the transport list so the new Victron row appears.
  await new Promise(r => setTimeout(r, 1200));
  await wizLoadTransports();
}

// USB-RS485 path — same shape as wizFindDongle/wizAddTransportFromMac
// but driven by /api/setup/usb_scan + the serial_modbus transport type.
// Customers who skip the BT-2 (or replace it with a wired dongle for
// reliability — sub-ms round-trips, no BLE timeouts, proper FC06 acks)
// pair through here.
async function wizFindUsb() {
  const btn  = document.getElementById("wiz-find-usb-btn");
  const stat = document.getElementById("wiz-find-status");
  const list = document.getElementById("wiz-find-results");
  if (!btn) return;
  btn.disabled = true;
  stat.textContent = "Looking for USB adapters…";
  list.innerHTML = "";
  let data;
  try {
    const r = await fetch("/api/setup/usb_scan");
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    data = await r.json();
  } catch (e) {
    stat.textContent = "";
    list.innerHTML = `<div class="wiz-empty">${escHtml(e.message || String(e))}</div>`;
    btn.disabled = false;
    return;
  }
  btn.disabled = false;
  const adapters = data.adapters || [];
  stat.textContent = `Found ${adapters.length} USB serial adapter(s)`;
  if (!adapters.length) {
    list.innerHTML = `<div class="wiz-empty">
      <strong>No USB serial adapters detected.</strong>
      Plug a USB-RS485 dongle into the Pi (FTDI or CH340 chip · ~£10 from any electronics supplier). The Pi should see it as <code>/dev/ttyUSB0</code> within a few seconds. Reload this page and try again. If nothing shows up, run <code>lsusb</code> on the Pi to confirm it's enumerating.
    </div>`;
    return;
  }
  list.innerHTML = adapters.map(a => {
    const chip = a.chip ? `<span class="wiz-vendor-hint">${escHtml(a.chip)}</span>` : "";
    const product = a.product ? `<strong>${escHtml(a.product)}</strong>` : `<strong>${escHtml(a.port)}</strong>`;
    const ids = (a.vendor_id && a.product_id)
      ? ` · VID ${escHtml(a.vendor_id)} PID ${escHtml(a.product_id)}`
      : "";
    const serial = a.serial ? ` · S/N ${escHtml(a.serial)}` : "";

    // Brief read-back classification from the backend:
    //   modbus_rtu — silent serial, hint Modbus (the dominant case).
    //   nmea_gps   — emitted GPS sentences during the sniff window.
    //   ve_direct  — Victron VE.Direct text frames at 19200 baud.
    //   unknown    — port opens but bytes don't match anything we know.
    //   busy       — port held by another process (probably us).
    const proto = a.protocol || "unknown";
    let badge = "";
    let action = `<button class="btn-action btn-action--primary"
                data-use-port="${escHtml(a.port)}"
                data-use-label="${escHtml(a.product || a.port)}">Use as Modbus</button>`;
    if (proto === "ve_direct") {
      badge = `<span class="wiz-vendor-hint wiz-vendor-hint--victron">Victron VE.Direct</span>`;
      // Wire the VE.Direct path directly — only sensible action for
      // a port emitting Victron frames is to add it as a ve_direct
      // transport. The device-kind selection (shunt vs MPPT vs Phoenix
      // inverter) happens in the next wizard step.
      action = `<button class="btn-action btn-action--primary"
                  data-use-vedirect-port="${escHtml(a.port)}"
                  data-use-vedirect-label="${escHtml(a.product || a.port)}">
                  Add Victron (VE.Direct)
                </button>`;
    } else if (proto === "nmea_gps") {
      badge = `<span class="wiz-vendor-hint wiz-vendor-hint--gps">NMEA GPS</span>`;
      // GPS receiver path lands with #125. Until then we surface the
      // detection but block the wrong action — adding a GPS as a
      // Modbus transport would just sit there failing every poll.
      action = `<button class="btn-action" disabled
                  title="GPS support is on the roadmap (#125). Coming soon">
                  GPS support coming soon
                </button>`;
    } else if (proto === "busy") {
      badge = `<span class="wiz-vendor-hint wiz-vendor-hint--warn">port busy</span>`;
      action = `<button class="btn-action" disabled
                  title="Port couldn't be opened. Already in use by another process (often the daemon itself if you've already added this transport)">
                  port busy
                </button>`;
    } else if (proto === "unknown") {
      badge = `<span class="wiz-vendor-hint wiz-vendor-hint--warn">unknown output</span>`;
      // Still offer Modbus — user may know what it is even if we don't.
    }
    return `
      <div class="wiz-result-row">
        <div class="wiz-result-info">
          ${product} ${chip} ${badge}
          <div class="settings-foot">${escHtml(a.port)}${ids}${serial}</div>
        </div>
        ${action}
      </div>`;
  }).join("");
  list.querySelectorAll("[data-use-port]").forEach(b => {
    b.addEventListener("click", () => wizAddTransportFromPort(b.dataset.usePort, b.dataset.useLabel));
  });
  list.querySelectorAll("[data-use-vedirect-port]").forEach(b => {
    b.addEventListener("click", () => wizAddVeDirectFromPort(
      b.dataset.useVedirectPort, b.dataset.useVedirectLabel,
    ));
  });
}

// VE.Direct add-transport flow (#199). The USB-scan sniff already
// confirmed this port is emitting VE.Direct text frames; we just
// hand the port to the same /api/setup/transports/add endpoint
// with type=ve_direct. The user picks the device kind (shunt /
// charge_controller / inverter) in the existing Add-device step
// that runs after every successful Add-transport.
async function wizAddVeDirectFromPort(port, label) {
  const stat = document.getElementById("wiz-find-status");
  stat.textContent = `Adding ${port} as VE.Direct…`;
  let res;
  try {
    const r = await fetch("/api/setup/transports/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type:     "ve_direct",
        port:     port,
        label:    label || null,
        baudrate: 19200,
      }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    res = await r.json();
  } catch (e) {
    stat.textContent = "";
    alert("Couldn't add VE.Direct transport: " + (e.message || String(e)));
    return;
  }
  if (res.reloaded) {
    stat.textContent = `Added ${res.label} (id: ${res.id}). Polling now.`;
  } else if (res.reload_error) {
    stat.textContent = `Added ${res.label}, but hot-reload failed: ${res.reload_error}. Restart the daemon to apply.`;
  } else {
    stat.textContent = `Added ${res.label} (id: ${res.id}). Restart the daemon to start polling.`;
  }
  await new Promise(r => setTimeout(r, 1500));
  await wizLoadTransports();
}

async function wizAddTransportFromPort(port, label) {
  const stat = document.getElementById("wiz-find-status");
  stat.textContent = `Adding ${port}…`;
  let res;
  try {
    const r = await fetch("/api/setup/transports/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: "serial_modbus",
        port: port,
        label: label || null,
        baudrate: 9600,   // Renogy/Epever default; future: ask in a form
      }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    res = await r.json();
  } catch (e) {
    stat.textContent = "";
    alert("Couldn't add transport: " + (e.message || String(e)));
    return;
  }
  if (res.reloaded) {
    stat.textContent = `Added ${res.label} (id: ${res.id}). Polling now. Give it ~5 s.`;
  } else if (res.reload_error) {
    stat.textContent = `Added ${res.label}, but hot-reload failed: ${res.reload_error}. Restart the daemon to apply.`;
  } else {
    stat.textContent = `Added ${res.label} (id: ${res.id}). Restart the daemon to start polling.`;
  }
  await new Promise(r => setTimeout(r, 1500));
  await wizLoadTransports();
}

async function wizAddTransportFromMac(mac, name) {
  const stat = document.getElementById("wiz-find-status");
  stat.textContent = `Adding ${mac}…`;
  let res;
  try {
    const r = await fetch("/api/setup/transports/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        address: mac,
        label: name || null,
        type: "ble_modbus",
      }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    res = await r.json();
  } catch (e) {
    stat.textContent = "";
    alert("Couldn't add transport: " + (e.message || String(e)));
    return;
  }
  if (res.reloaded) {
    stat.textContent = `Added ${res.label} (id: ${res.id}). Polling now. Give it ~10 s.`;
  } else if (res.reload_error) {
    stat.textContent = `Added ${res.label}, but hot-reload failed: ${res.reload_error}. Restart the daemon to apply.`;
  } else {
    stat.textContent = `Added ${res.label} (id: ${res.id}). Restart the daemon to start polling.`;
  }
  // Give the new scheduler a moment to open the transport, then
  // refresh the list so the open=true state shows up.
  await new Promise(r => setTimeout(r, 1500));
  await wizLoadTransports();
}

// Surface BLE adapter status at the top of the wizard. Cheap diagnostic
// that answers "is my dongle / docker passthrough even reaching the
// daemon?" before the user starts wondering why scan does nothing.
async function wizCheckBleStatus() {
  const host = $("#wiz-ble-status");
  if (!host) return;
  let data;
  try {
    data = await api("/api/setup/ble_status");
  } catch (e) {
    host.className = "wiz-ble-status wiz-ble-warn";
    host.innerHTML = `<span class="wiz-ble-dot"></span><span class="wiz-ble-label">Bluetooth status check failed: ${escHtml(String(e.message || e))}</span>`;
    return;
  }
  if (!data.available || !data.adapters?.length) {
    host.className = "wiz-ble-status wiz-ble-bad";
    const reason = data.reason || "no Bluetooth controllers found";
    host.innerHTML = `<span class="wiz-ble-dot"></span><span class="wiz-ble-label"><strong>Bluetooth not reachable</strong> · ${escHtml(reason)}. Check your USB BLE dongle is plugged in${" "}${navigator.userAgent.includes("Docker") ? "" : "(if running in Docker, confirm network_mode: host + /var/run/dbus is bind-mounted)"}.</span>`;
    return;
  }
  const list = data.adapters.map(a => {
    const power = a.powered === false ? " · <span class='wiz-ble-warn-text'>powered off</span>" :
                  a.powered === true ? "" : "";
    const def = a.default ? " <em>(default)</em>" : "";
    return `<code>${escHtml(a.name)}</code> ${escHtml(a.address)}${def}${power}`;
  }).join(" · ");
  host.className = "wiz-ble-status wiz-ble-ok";
  host.innerHTML = `<span class="wiz-ble-dot"></span><span class="wiz-ble-label"><strong>Bluetooth ready</strong> · ${list}</span>`;
}

// Deeper BLE diagnostic. Click handler for #wiz-ble-diagnose-btn.
// Hits /api/setup/ble_diagnose which runs bleak and bluetoothctl
// side by side and returns a verdict. Surfaces the Realtek+BlueZ
// silent-failure case (#158) so users on those chips know the
// dashboard polling still works even when bluetoothctl looks dead.
async function wizRunBleDiagnose() {
  const btn = $("#wiz-ble-diagnose-btn");
  const out = $("#wiz-ble-diag-out");
  if (!btn || !out) return;
  btn.disabled = true;
  const origLabel = btn.textContent;
  btn.textContent = "Running…";
  out.hidden = false;
  out.className = "wiz-ble-diag-out wiz-ble-diag-running";
  out.textContent = "Scanning with bleak (3 s) and bluetoothctl (3 s)…";
  let data;
  try {
    data = await api("/api/setup/ble_diagnose");
  } catch (e) {
    out.className = "wiz-ble-diag-out wiz-ble-diag-bad";
    out.textContent = `Diagnostic failed: ${e.message || e}`;
    btn.disabled = false;
    btn.textContent = origLabel;
    return;
  }
  const fmtCount = (c, err) => {
    if (err) return `<span class="wiz-ble-diag-err">error: ${escHtml(err)}</span>`;
    if (c == null) return "·";
    return `<strong>${c}</strong> device${c === 1 ? "" : "s"}`;
  };
  const verdictCls = {
    ok: "wiz-ble-diag-ok",
    scan_silent_failure: "wiz-ble-diag-warn",
    bleak_silent_failure: "wiz-ble-diag-warn",
    bleak_failed: "wiz-ble-diag-bad",
    bluetoothctl_failed: "wiz-ble-diag-warn",
    no_scanner_available: "wiz-ble-diag-bad",
    no_devices_seen: "wiz-ble-diag-warn",
  }[data.verdict] || "wiz-ble-diag-warn";
  out.className = `wiz-ble-diag-out ${verdictCls}`;
  const sug = data.suggestion
    ? `<div class="wiz-ble-diag-suggest">${escHtml(data.suggestion)}</div>`
    : "";
  out.innerHTML = `
    <div class="wiz-ble-diag-counts">
      <span>bleak: ${fmtCount(data.bleak?.count, data.bleak?.error)}</span>
      <span>·</span>
      <span>bluetoothctl: ${fmtCount(data.bluetoothctl?.count, data.bluetoothctl?.error)}</span>
    </div>
    ${sug}
  `;
  btn.disabled = false;
  btn.textContent = origLabel;
}
document.addEventListener("click", (e) => {
  if (e.target && e.target.id === "wiz-ble-diagnose-btn") {
    wizRunBleDiagnose();
  }
});

// ---------- diagnostics (log tail) ----------
let diagTimer = null;
const DIAG_REFRESH_MS = 4000;
function diagLevelClass(level) {
  const l = (level || "").toLowerCase();
  if (l === "error" || l === "critical") return "diag-line--error";
  if (l === "warning") return "diag-line--warning";
  if (l === "debug")   return "diag-line--debug";
  return "diag-line--info";
}
function diagFmtTs(epoch) {
  const d = new Date(epoch * 1000);
  return d.toLocaleTimeString([], { hour12: false });
}
async function refreshDiagLog() {
  const host = $("#diag-log");
  if (!host) return;
  let data;
  try { data = await api("/api/system/logs?n=300"); }
  catch (e) {
    host.textContent = `Could not load logs: ${e.message}`;
    return;
  }
  const lines = data.lines || [];
  const meta = $("#diag-meta");
  if (meta) meta.textContent = `${lines.length} line${lines.length === 1 ? "" : "s"}`;
  if (!lines.length) {
    host.textContent = "(no log lines captured yet. Daemon just started)";
    return;
  }
  // Escape HTML — log lines may contain < > & from tracebacks etc.
  const esc = (s) => String(s).replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
  host.innerHTML = lines.map(l => {
    const lvl = (l.level || "INFO").padEnd(7);
    return `<span class="diag-line ${diagLevelClass(l.level)}">${diagFmtTs(l.ts)}  ${esc(lvl)}  ${esc(l.logger)}: ${esc(l.msg)}</span>`;
  }).join("\n");
  if ($("#diag-autoscroll")?.checked) {
    host.scrollTop = host.scrollHeight;
  }
}
function startDiagTimer() {
  if (diagTimer) return;
  refreshDiagLog();
  diagTimer = setInterval(refreshDiagLog, DIAG_REFRESH_MS);
}
function stopDiagTimer() {
  if (diagTimer) { clearInterval(diagTimer); diagTimer = null; }
}

// ---------- docs (markdown topics) ----------
// Tiny markdown renderer — handles the subset our bundled docs use:
// # ## ### headings, **bold**, *italic*, `inline code`, ```fences```,
// - / * / 1. lists, > blockquotes, --- rules, [text](url) links,
// pipe tables, and plain paragraphs. Escapes HTML in source text so
// the docs themselves can show <tags> safely.
function escHtml(s) {
  return String(s).replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
function renderInline(s) {
  let out = escHtml(s);
  out = out.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>');
  return out;
}
function renderMarkdown(src) {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const ln = lines[i];
    // Fenced code block
    if (/^```/.test(ln)) {
      const lang = ln.slice(3).trim();
      i++;
      const buf = [];
      while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i++; }
      i++; // skip closing ```
      out.push(`<pre><code data-lang="${escHtml(lang)}">${escHtml(buf.join("\n"))}</code></pre>`);
      continue;
    }
    // Heading
    const h = ln.match(/^(#{1,6})\s+(.+)$/);
    if (h) {
      const level = h[1].length;
      out.push(`<h${level}>${renderInline(h[2])}</h${level}>`);
      i++; continue;
    }
    // Horizontal rule
    if (/^---+\s*$/.test(ln)) { out.push("<hr/>"); i++; continue; }
    // Pipe table (header | --- | row …)
    if (/^\s*\|.*\|\s*$/.test(ln) && i + 1 < lines.length && /^\s*\|[-:\s|]+\|\s*$/.test(lines[i + 1])) {
      const split = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map(s => s.trim());
      const head = split(ln);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        rows.push(split(lines[i])); i++;
      }
      const thead = head.map(c => `<th>${renderInline(c)}</th>`).join("");
      const tbody = rows.map(r => `<tr>${r.map(c => `<td>${renderInline(c)}</td>`).join("")}</tr>`).join("");
      out.push(`<table><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`);
      continue;
    }
    // Blockquote
    if (/^>\s?/.test(ln)) {
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^>\s?/, "")); i++;
      }
      out.push(`<blockquote>${renderMarkdown(buf.join("\n"))}</blockquote>`);
      continue;
    }
    // Ordered list — captures multi-line items: any subsequent line
    // indented by 2+ spaces (and not a new marker) is folded into the
    // current item. Without this, hard-wrapped 80-char list items
    // render as half-bullet/half-paragraph with mismatched fonts.
    if (/^\s*\d+\.\s+/.test(ln)) {
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(/^\s*\d+\.\s+(.+)$/);
        if (!m) break;
        let buf = m[1];
        i++;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*[-*]\s+|^\s*\d+\.\s+/.test(lines[i])) {
          buf += " " + lines[i].trim();
          i++;
        }
        items.push(buf);
      }
      out.push(`<ol>${items.map(it => `<li>${renderInline(it)}</li>`).join("")}</ol>`);
      continue;
    }
    // Unordered list — same continuation rule as ordered.
    if (/^\s*[-*]\s+/.test(ln)) {
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(/^(\s*)[-*]\s+(.+)$/);
        if (!m) break;
        const indent = m[1].length;
        let buf = m[2];
        i++;
        // Consume continuation lines indented strictly more than the
        // marker. Stops at blank line, new marker, or unindented text.
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*[-*]\s+|^\s*\d+\.\s+/.test(lines[i])) {
          buf += " " + lines[i].trim();
          i++;
        }
        items.push(buf);
        // Allow blank line between siblings without exiting the list.
        if (i < lines.length && /^\s*$/.test(lines[i])
            && i + 1 < lines.length && /^\s*[-*]\s+/.test(lines[i + 1])) {
          i++;
        }
        void indent;
      }
      out.push(`<ul>${items.map(it => `<li>${renderInline(it)}</li>`).join("")}</ul>`);
      continue;
    }
    // Blank line
    if (/^\s*$/.test(ln)) { i++; continue; }
    // Paragraph — gather consecutive non-blank, non-special lines.
    const para = [ln];
    i++;
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|>\s?|---|\s*[-*]\s|\s*\d+\.\s|```)/.test(lines[i])) {
      para.push(lines[i]); i++;
    }
    out.push(`<p>${renderInline(para.join(" "))}</p>`);
  }
  return out.join("\n");
}

let docsIndex = null;  // cached topic list
let docsLoaded = false;

async function onEnterDocs(slug) {
  if (!docsLoaded) {
    try {
      const r = await fetch("/web/docs/index.appliance.json");
      docsIndex = await r.json();
      docsLoaded = true;
    } catch (e) {
      $("#docs-nav").innerHTML = `<div class="settings-empty">Could not load docs index: ${e.message}</div>`;
      return;
    }
  }
  renderDocsNav(slug);
  if (slug) {
    loadDocPage(slug);
  } else if (docsIndex.topics?.length) {
    // No slug — auto-open the first topic so the user isn't staring at
    // an empty pane.
    window.location.hash = `#/docs/${docsIndex.topics[0].slug}`;
  }
}

function renderDocsNav(activeSlug) {
  const host = $("#docs-nav");
  if (!host || !docsIndex) return;
  // Group by section in document order.
  const sections = [];
  for (const t of docsIndex.topics) {
    let s = sections.find(x => x.name === t.section);
    if (!s) { s = { name: t.section, topics: [] }; sections.push(s); }
    s.topics.push(t);
  }
  host.innerHTML = sections.map(s => `
    <div class="docs-nav-section">${escHtml(s.name)}</div>
    ${s.topics.map(t => `
      <a class="docs-nav-link ${t.slug === activeSlug ? "active" : ""}"
         href="#/docs/${encodeURIComponent(t.slug)}">${escHtml(t.title)}</a>`).join("")}
  `).join("");
}

async function loadDocPage(slug) {
  const host = $("#docs-content");
  if (!host) return;
  host.innerHTML = `<p class="docs-placeholder">Loading…</p>`;
  try {
    // release-notes is special: prefer the live cache from
    // /api/releases/changelog so users can see "what's in 0.0.3"
    // BEFORE they install 0.0.3. The bundled .md only knows about
    // versions <= the running release. Fall back to bundled if the
    // cache is empty (first boot, or offline since boot).
    let md = null;
    if (slug === "release-notes") {
      try {
        const r = await fetch("/api/releases/changelog");
        if (r.ok && r.status !== 204) md = await r.text();
      } catch (_) { /* fall through to bundled */ }
    }
    if (md == null) {
      const r = await fetch(`/web/docs/${encodeURIComponent(slug)}.md`);
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      md = await r.text();
    }
    host.innerHTML = renderMarkdown(md);
  } catch (e) {
    host.innerHTML = `<p class="docs-placeholder">Could not load "${escHtml(slug)}": ${escHtml(e.message)}</p>`;
  }
}

// ---------- daemon restart ----------
// Settings → System → Restart. POSTs /api/system/restart (which fires
// asyncio task that os.execv's the process), shows a full-screen
// overlay, polls /api/health every ~600 ms until the daemon answers,
// then dismisses the overlay. The existing SSE EventSource reconnects
// on its own once the new process is listening.
async function restartDaemon() {
  if (!confirm("Restart the daemon? The UI will reconnect in about 5 seconds.")) return;
  const overlay = $("#restart-overlay");
  const sub     = $("#restart-overlay-sub");
  overlay.hidden = false;
  sub.textContent = "Sending restart signal…";
  try {
    const r = await fetch("/api/system/restart", { method: "POST" });
    if (!r.ok && r.status !== 202) throw new Error(`${r.status} ${r.statusText}`);
  } catch (e) {
    sub.textContent = `Failed to send restart: ${e.message}`;
    setTimeout(() => { overlay.hidden = true; }, 3000);
    return;
  }
  sub.textContent = "Waiting for daemon to come back…";
  await pollUntilHealthy(45000);
  sub.textContent = "Reloading page…";
  // Easiest way to re-establish SSE + flush stale snapshot state.
  setTimeout(() => window.location.reload(), 500);
}

async function pollUntilHealthy(timeoutMs) {
  const start = Date.now();
  // Brief grace period so we don't hit /api/health before the old
  // process has actually torn down.
  await new Promise(r => setTimeout(r, 800));
  while (Date.now() - start < timeoutMs) {
    try {
      const r = await fetch("/api/health", { cache: "no-store" });
      if (r.ok) return true;
    } catch (_) { /* still down, retry */ }
    await new Promise(r => setTimeout(r, 600));
  }
  return false;
}

// boot
setRoute(currentRouteName());
// Do one REST refresh so the page is populated even before the SSE
// connection is established (and as a backstop on browsers that fail to
// open EventSource). The stream takes over once the first event lands.
refresh().then(() => {
  if (currentRouteName() === "dashboard") { refreshDriftSparkline(); refreshBatteryHealth(); refreshRuntimeForecast(); }
  if (currentRouteName() === "history") { refreshChart(); refreshHeatmap(); }
});
openStream();
// Background charts still poll on their own slower cadence.
setInterval(() => {
  if (currentRouteName() === "history") refreshChart();
}, 30000);
