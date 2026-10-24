// WattPost UI, part 01: icons, helpers, state, theme, kiosk mode, api, live stream, bank aggregate.
// Plain script (no modules): the numbered files in web/js/ are loaded in order by
// index.html and share one global scope, exactly as the old single app.js did.

// solar-monitor frontend (v3)
// Vanilla JS, no build step. Renders the convergent off-grid dashboard:
//   - conditional alert banner
//   - hero with SOC donut + remaining-time + net W
//   - data-driven power flow strip (sources / battery / loads, tiles
//     appear and disappear with installed devices)
//   - today's totals strip
//   - cell balance grid
//   - history chart (uPlot)
//   - per-device detail cards
//
// All values from the daemon's /api are SI. UI doesn't convert, display
// in SI; future user pref can convert at the edge.
//
// --------------- DEVICE → FLOW MAPPING --------------------
// Adding a new device kind means adding an entry here. Each device can
// contribute to "sources", "loads", or "battery" with a metric reference.
// The mapping is intentionally small and obvious: protocol-specific
// quirks belong in the driver, not the UI.

// ---------- inline icons (line-art SVG strings, tint via currentColor) ----------
const ICONS = {
  // Sun, for PV / solar sources
  sun: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="12" cy="12" r="4.2"/>
    <path d="M12 2v2.5M12 19.5V22M3.5 12H6M18 12h2.5M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8"/>
  </svg>`,
  // Battery, for the bank / storage tile
  battery: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <rect x="2.5" y="7" width="17" height="10" rx="2"/>
    <line x1="21.5" y1="10" x2="21.5" y2="14"/>
    <rect x="5" y="9.5" width="3" height="5" rx=".4" fill="currentColor"/>
    <rect x="9" y="9.5" width="3" height="5" rx=".4" fill="currentColor"/>
    <rect x="13" y="9.5" width="3" height="5" rx=".4" fill="currentColor"/>
  </svg>`,
  // Lightning bolt, AC loads / inverter output
  bolt: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <path d="M13 2 4 14h7l-1 8 9-12h-7l1-8z"/>
  </svg>`,
  // Plug, DC loads
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
  // Question mark, fallback / unknown
  unknown: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="12" cy="12" r="9"/>
    <path d="M9.5 9.5a2.5 2.5 0 1 1 4.6 1.4c-.6.8-1.6 1-1.6 2.1"/>
    <line x1="12" y1="17" x2="12" y2="17.01"/>
  </svg>`,
  // House, for inferred "everything else" loads (heater, fridge, lights,
  // anything pulling from the bank that doesn't go through the controller)
  house: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <path d="M3 11l9-7 9 7"/>
    <path d="M5 9.5V21h14V9.5"/>
    <path d="M10 21v-6h4v6"/>
  </svg>`,
  // Plug-in / external power feed, unmeasured source (mains charger, etc.)
  feed: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <path d="M3 12h8"/>
    <path d="M7 8v8"/>
    <rect x="11" y="9" width="10" height="6" rx="1"/>
    <path d="M14 9V6M18 9V6"/>
  </svg>`,
};

// Returns the user-visible name for a device, the override set via
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
  // Synthetic aggregate, re-uses the battery glyph but the label
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
  // Renogy DCC50S / DCC30S / DCC25S / DCC15S, alternator + MPPT
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
  // Renogy combo above, both are "engine-driven DC input."
  dcdc:    { sources: [{ id: "alt", label: "DC-DC", color: "dc", icon: "alternator",
                          metric: "output_power_w",
                          vMetric: "output_voltage_v", aMetric: "output_current_a" }] },
  dcdc_xs: { sources: [{ id: "alt", label: "DC-DC", color: "dc", icon: "alternator",
                          metric: "output_power_w",
                          vMetric: "output_voltage_v", aMetric: "output_current_a" }] },
  // Victron AC chargers (Blue Smart IP22 / IP65 / Phoenix Smart).
  // Up to 3 outputs but most installs use output 1 only; we surface
  // output_1 here. Multi-output models would need this expanded to
  // sum all three or render them separately, out of scope for now
  // since output_2 / output_3 are rare in van/cabin installs.
  ac_charger: {
    sources: [{ id: "ac_chg", label: "AC Charger", color: "grid", icon: "plug",
                metric: "output_1_power_w",
                vMetric: "output_1_voltage_v", aMetric: "output_1_current_a" }],
  },
  inverter: {
    // Hybrid inverter (Voltronic + EG4 XP families). Has a PV-input
    // side AND an AC-output side AND a battery side all on one
    // device, so it contributes to every node of the flow strip.
    // The Solar source uses pv_power_w; the AC load uses
    // ac_output_power_w; battery: {} routes battery V/A back to
    // the bank-net calculation with the inverter's per-domain
    // field names instead of generic voltage_v/current_a (which
    // on an inverter would be ambiguous, there are five voltages).
    sources: [{ id: "pv", label: "Solar", color: "pv", icon: "sun",
                metric: "pv_power_w",
                vMetric: "pv_voltage_v", aMetric: "pv_current_a" }],
    loads:   [{ id: "ac", label: "AC Load", color: "ac", icon: "bolt",
                metric: "ac_output_power_w", vMetric: "ac_output_voltage_v",
                aMetric: "ac_output_current_a" }],
    battery: { vMetric: "battery_voltage_v", aMetric: "battery_current_a",
               pMetric: "battery_power_w" },
  },
  // Deye / Sunsynk / Sol-Ark single-phase + split-phase + three-phase.
  // Same flow-tile shape as the generic inverter, split out only
  // because the device_kind enum is finer-grained. (We could have
  // collapsed to kind=inverter and stored phase-info separately, but
  // the two-kind split is honest about the chassis difference and
  // lets the wizard branch render variant-specific copy.)
  inverter_1p: {
    sources: [{ id: "pv", label: "Solar", color: "pv", icon: "sun",
                metric: "pv_power_w",
                vMetric: "pv_voltage_v", aMetric: "pv_current_a" }],
    loads:   [{ id: "ac", label: "AC Load", color: "ac", icon: "bolt",
                metric: "ac_output_power_w", vMetric: "ac_output_voltage_v",
                aMetric: "ac_output_current_a" }],
    battery: { vMetric: "battery_voltage_v", aMetric: "battery_current_a",
               pMetric: "battery_power_w" },
  },
  inverter_3p: {
    sources: [{ id: "pv", label: "Solar", color: "pv", icon: "sun",
                metric: "pv_power_w",
                vMetric: "pv_voltage_v", aMetric: "pv_current_a" }],
    loads:   [{ id: "ac", label: "AC Load", color: "ac", icon: "bolt",
                metric: "ac_output_power_w", vMetric: "ac_output_voltage_v",
                aMetric: "ac_output_current_a" }],
    battery: { vMetric: "battery_voltage_v", aMetric: "battery_current_a",
               pMetric: "battery_power_w" },
  },
  // Victron SmartBatteryProtect / similar load-disconnect modules.
  // Their telemetry is mostly state (connected / disconnected / fault)
  // not power, we don't have a clean "watts through" reading to plot,
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
  // Future-aware relative time: "in 2h" for upcoming, "5m ago" for past.
  // ago() assumes the past and prints "-61828s ago" for a future ts.
  rel(unixTs) {
    const d = unixTs - Math.floor(Date.now() / 1000);
    const a = Math.abs(d);
    const part = a < 60 ? `${a}s` : a < 3600 ? `${Math.floor(a / 60)}m`
               : a < 86400 ? `${Math.floor(a / 3600)}h` : `${Math.floor(a / 86400)}d`;
    return d >= 0 ? `in ${part}` : `${part} ago`;
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

// Cloud view: wattpost.cloud serves this same UI read-only against data the
// appliance published. No setup wizard, no Settings, no BLE status, and the
// status pill reports how fresh the appliance's last upload is.
const CLOUD_VIEW = !!window.WP_CLOUD;

// ---------- demo-mode banner (one-shot, runs at boot) ----------
// /api/system/info exposes `demo: true|false` from the WATTPOST_DEMO=1
// container env. The Settings → About flow also reads this and toggles
// the banner, but Settings isn't visited on most page loads, pull the
// check up to boot so the banner appears immediately on every page,
// kiosk mode included.
//
// Exposed as a top-level promise so renderStatus() can await it before
// triggering the "Setup needed" wizard redirect, without this gate,
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
          rows.push(`<div><span class="chart-value-tip-sw" style="background:${color}"></span>${escHtml(ser.label)}: <em>${escHtml(String(fmt))}</em></div>`);
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
const KIOSK_SKIN_KEY = "wp-kiosk-skin";
let wakeLock = null;
// Appliance-side kiosk config (#28). Stored on the box (not per-browser),
// so a setting made from a cloud session reaches the local wall display.
// localStorage mirrors it as an instant-boot cache.
let APPLIANCE_KIOSK = null;   // {default, skin} once /api/kiosk/config loads
function kioskDefault() {
  if (APPLIANCE_KIOSK) return !!APPLIANCE_KIOSK.default;
  try { return localStorage.getItem(KIOSK_KEY) === "1"; }
  catch (_) { return false; }
}
async function loadKioskConfig() {
  try {
    const k = await api("/api/kiosk/config");
    APPLIANCE_KIOSK = { default: !!k.default, skin: (k.skin || "halo") };
    try {
      localStorage.setItem(KIOSK_KEY, k.default ? "1" : "0");
      localStorage.setItem(KIOSK_SKIN_KEY, APPLIANCE_KIOSK.skin);
    } catch (_) {}
    // Reflect into the Settings controls if they're already on-screen.
    const _sel = document.getElementById("kiosk-skin-select");
    if (_sel) _sel.value = APPLIANCE_KIOSK.skin;
    const _tog = document.getElementById("kiosk-default-toggle");
    if (_tog) _tog.checked = !!APPLIANCE_KIOSK.default;
    // The local display may have booted before this loaded — if the
    // appliance now says default-kiosk and we're on the dashboard with
    // no explicit route, go to kiosk (never on the cloud view).
    if (APPLIANCE_KIOSK.default && !CLOUD_VIEW &&
        (!window.location.hash || window.location.hash === "#" || window.location.hash === "#/")) {
      window.location.hash = "#/kiosk";
    } else {
      renderKiosk();  // pick up a skin change while already in kiosk
    }
  } catch (_) { /* offline / older daemon: keep the localStorage cache */ }
}
async function setKioskDefault(on) {
  // Persist to the APPLIANCE so it travels (cloud session → wall screen),
  // not just this browser.
  try {
    await fetch(_withKiosk("/api/kiosk/config"), {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ default: !!on }),
    });
  } catch (_) {}
  if (!APPLIANCE_KIOSK) APPLIANCE_KIOSK = { default: !!on, skin: activeSkinId() };
  else APPLIANCE_KIOSK.default = !!on;
  try { localStorage.setItem(KIOSK_KEY, on ? "1" : "0"); } catch (_) {}
}
async function setKioskSkin(skin) {
  try {
    await fetch(_withKiosk("/api/kiosk/config"), {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skin }),
    });
  } catch (_) {}
  if (!APPLIANCE_KIOSK) APPLIANCE_KIOSK = { default: kioskDefault(), skin };
  else APPLIANCE_KIOSK.skin = skin;
  try { localStorage.setItem(KIOSK_SKIN_KEY, skin); } catch (_) {}
  renderKiosk();
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
// Exit-button discoverability (#kiosk-exit is easy to forget on a wall
// display): flash it for a few seconds on entry, then reveal it whenever
// the mouse moves or the screen is tapped and fade it back out after a
// couple of seconds of stillness.
let _kioskExitHideTimer = null;
function _kioskRevealExit() {
  const btn = document.getElementById("kiosk-exit");
  if (!btn) return;
  btn.classList.add("kiosk-exit--show");
  clearTimeout(_kioskExitHideTimer);
  _kioskExitHideTimer = setTimeout(
    () => btn.classList.remove("kiosk-exit--show"), 2500);
}
function onEnterKiosk() {
  document.body.classList.add("kiosk-active");
  requestWakeLock();
  const btn = document.getElementById("kiosk-exit");
  if (btn) {
    btn.classList.remove("kiosk-exit--flash");
    void btn.offsetWidth;                 // reflow so the flash re-triggers
    btn.classList.add("kiosk-exit--flash");
  }
  window.addEventListener("mousemove", _kioskRevealExit, { passive: true });
  window.addEventListener("touchstart", _kioskRevealExit, { passive: true });
}
function onLeaveKiosk() {
  document.body.classList.remove("kiosk-active");
  releaseWakeLock();
  window.removeEventListener("mousemove", _kioskRevealExit);
  window.removeEventListener("touchstart", _kioskRevealExit);
  clearTimeout(_kioskExitHideTimer);
  const btn = document.getElementById("kiosk-exit");
  if (btn) btn.classList.remove("kiosk-exit--show", "kiosk-exit--flash");
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
  // Drive both the nav pill (#status, shown on the bar on desktop) and the
  // Settings status row (#settings-status, the mobile/Settings home for it),
  // plus a colour dot on the Settings tab so problems surface even when the
  // pill is hidden (phones).
  const apply = (el) => {
    if (!el) return;
    el.classList.remove("ok", "warn", "err");
    if (cls) el.classList.add(cls);
    const t = el.querySelector(".text");
    if (t) t.textContent = text;
    const iconHost = el.querySelector(".status-icon");
    if (iconHost) iconHost.innerHTML = STATUS_ICONS[cls] || "";
  };
  apply(document.getElementById("status"));         // desktop nav pill
  apply(document.getElementById("status-mobile"));  // mobile top-bar pill
  apply(document.getElementById("settings-status"));
  const tab = document.querySelector('.nav-tab[data-tab="settings"]');
  if (tab) {
    tab.classList.remove("nav-alert-warn", "nav-alert-err");
    if (cls === "warn") tab.classList.add("nav-alert-warn");
    else if (cls === "err") tab.classList.add("nav-alert-err");
  }
}

// ---------- api ----------
// Local kiosk auth (cloud #15). A wall display is opened at
// /kiosk?token=<token>; the appliance middleware verifies the token on
// each kiosk-scoped GET and grants the read-only kiosk allow-list. We
// capture the token once at load and re-attach it to every outgoing
// /api/* fetch (+ the SSE stream) so they keep authing. Accepts the
// path-segment kiosk routes (/kiosk and /kiosk/<mode>).
const KIOSK_KEY_PARAM = (() => {
  try {
    if (!window.location.pathname.startsWith("/kiosk")) return "";
    return new URLSearchParams(window.location.search).get("token") || "";
  } catch (_) { return ""; }
})();

function _withKiosk(path) {
  if (!KIOSK_KEY_PARAM) return path;
  // Only attach to relative paths (don't leak the token to third
  // parties on absolute URLs). The appliance API is always relative.
  if (/^https?:/.test(path)) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}token=${encodeURIComponent(KIOSK_KEY_PARAM)}`;
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
// Snapshot lock (#162): every renderer derived from `devices`, hero,
// flow tile, alerts, used to call aggregateBank() independently, each
// recomputing its own staleness floor against a freshly-read Date.now().
// On the 90 s boundary that meant the hero could include a battery the
// flow tile had just classed as silent, leaving the two tiles in visible
// disagreement. We now stamp the frame's "now" once and memoise the
// bank + flow model so every consumer sees the same view of the world.
// ---------- kiosk skin view-model + render (kiosk-skins.js) ----------
// Which skin is active. Per-device (localStorage) so a wall tablet and a
// van screen on the same box can differ. Default Halo.
const _VALID_SKINS = ["halo", "ember", "command"];
function activeSkinId() {
  // A ?skin= on the URL wins for this display only — that's how a cloud
  // kiosk share pins its look (the /k/<token> exchange redirects to
  // /kiosk?skin=command). Non-secret, so it's fine to carry in the URL.
  try {
    const q = (new URLSearchParams(window.location.search).get("skin") || "").toLowerCase();
    if (_VALID_SKINS.indexOf(q) !== -1) return q;
  } catch (e) {}
  if (APPLIANCE_KIOSK && APPLIANCE_KIOSK.skin) return APPLIANCE_KIOSK.skin;
  try { return localStorage.getItem(KIOSK_SKIN_KEY) || "halo"; }
  catch (e) { return "halo"; }
}

// Map the dashboard's bank + flow model onto the stable KioskViewModel
// (see kiosk-skins.js for the contract). Skins bind to THIS, never to
// app internals, so they survive refactors.
function buildKioskViewModel() {
  const bank = _frame.bank || aggregateBank();
  const fm = _frame.flowModel || buildFlowModel();
  const KS = window.WattPostKioskSkins;
  const netW = +bank.netW || 0;
  let state = Math.abs(netW) < 1 ? "holding" : (netW > 0 ? "charging" : "discharging");
  if ((+bank.soc || 0) <= 12 && state !== "charging") state = "critical";
  const stateLabel = { charging: "Charging", discharging: "Discharging",
                       holding: "Holding", critical: "Low battery" }[state];
  const sources = (fm.sources || []).map((s) => ({
    id: s.id, label: s.label, role: s.color || "pv", watts: Math.max(0, +s.power || 0),
  }));
  const loads = (fm.loads || []).map((l) => ({
    id: l.id, label: l.label, role: l.color || "load", watts: Math.max(0, +l.power || 0),
  }));
  const cap = +bank.totalCap || 0, rem = +bank.totalRem || 0, a = +bank.sumI || 0;
  let timeToFullMin = null, timeToEmptyMin = null;
  if (state === "charging" && a > 0.1 && cap > rem) timeToFullMin = ((cap - rem) / a) * 60;
  if (state === "discharging" && a < -0.1 && rem > 0) timeToEmptyMin = (rem / Math.abs(a)) * 60;
  const sourcesWh = (todayAggregate && +todayAggregate.sources_today_wh) || 0;
  const now = new Date();
  const nowS = Date.now() / 1000;

  // Sun (Ember's day-arc + night flag), weather + per-day forecast bars
  // (Command) — built from the same data the dashboard already fetches
  // each frame. All best-effort: a missing source just degrades the skin.
  let sun = null, isNight = false;
  if (weatherData && weatherData.sunrise_ts && weatherData.sunset_ts) {
    const rise = weatherData.sunrise_ts, set = weatherData.sunset_ts;
    const fmtT = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    sun = {
      riseStr: fmtT(rise), setStr: fmtT(set),
      progress: set > rise ? Math.max(0, Math.min(1, (nowS - rise) / (set - rise))) : 0,
    };
    isNight = nowS < rise || nowS > set;
  }
  let weather = null;
  if (weatherData && weatherData.provider) {
    weather = {
      desc: (typeof WMO !== "undefined" ? WMO.describe(weatherData.weather_code, weatherData.is_day) : ""),
      tempC: weatherData.temperature_c,
    };
  }
  let forecast = [];
  try {
    if (forecastData && forecastData.points && typeof bucketByDay === "function") {
      forecast = bucketByDay(forecastData.points).slice(0, 7).map((b) => ({ kwh: (b.wh || 0) / 1000 }));
    }
  } catch (_) {}

  // Today's PV production profile for the "harvested today" sparkline:
  // the solar_w series from /api/energy/today, normalised 0..1 by the
  // day's peak and downsampled. Empty (graceful) until the async fetch
  // (kicked off in renderKiosk) lands.
  let daySeries = [];
  try {
    const sw = energyTodayData && energyTodayData.series && energyTodayData.series.solar_w;
    if (Array.isArray(sw) && sw.length > 1) {
      const peak = sw.reduce((m, v) => (typeof v === "number" && v > m ? v : m), 0);
      if (peak > 0) {
        const clean = sw.map((v) => (typeof v === "number" && v > 0 ? v / peak : 0));
        daySeries = downsampleSeries(clean, 48);
      }
    }
  } catch (_) {}

  return {
    version: KS ? KS.VERSION : 1,
    siteName: (window.WP_SITE_NAME || ""),
    clock: now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    isNight,
    soc: +bank.soc || 0, state, stateLabel, netW,
    battery: { v: +bank.meanV || 0, a, ah: rem, cap },
    timeToFullMin, timeToEmptyMin,
    sources, loads,
    todayKwh: sourcesWh / 1000,
    daySeries, forecast, weather, sun,
    bankLabel: cap ? (Math.round(cap) + "Ah bank") : "Bank",
    cells: (bank && bank.bankCells) || [],
  };
}

function renderKiosk() {
  const root = document.getElementById("kiosk-skin-root");
  const KS = window.WattPostKioskSkins;
  if (!root || !KS) return;       // engine not loaded / not on kiosk route
  try {
    const skinId = activeSkinId();
    const vm = buildKioskViewModel();
    KS.get(skinId).render(root, vm);
    // Expose the active skin so the page background can match it — the
    // skin SVG is letterboxed on most displays, and a skin-blind page
    // bg shows through as a mismatched band (e.g. cool dark behind
    // Ember's warm palette). CSS paints a per-skin bg keyed off this.
    document.body.dataset.kioskSkin = skinId;
    // Night mode: gently dim the wall display after local sunset so it
    // isn't a lightbox in a dark van/cabin/bedroom.
    document.body.classList.toggle("kiosk-night", !!vm.isNight);
    // Lazily pull today's intraday PV series for the harvested-sparkline.
    // Throttled cache, so calling each render is cheap; repaint once when
    // the first payload lands so the curve appears without waiting for the
    // next snapshot tick.
    if (document.body.classList.contains("kiosk-active")) {
      ensureEnergyToday().then((d) => {
        if (d && !_energyKioskPainted) { _energyKioskPainted = true; renderKiosk(); }
      });
    }
  } catch (e) {
    if (window.console) console.warn("kiosk skin render failed", e);
  }
}
let _energyKioskPainted = false;

function applySnapshot(frame) {
  devices = frame.devices || [];
  lastRun = frame.poll_run?.last_run || null;
  // Keep the Settings tab's Daemon row in sync when poll data arrives
  // (no-op if the row isn't in the DOM yet, it's only rendered when
  // Settings is active).
  renderDaemonStatus();
  todayAggregate = frame.today || null;
  solarPauseStatus = frame.solar_pause || null;
  // Frame-scoped cache. Reset on every apply so a stale value from the
  // previous tick can't leak into the next render.
  // Cloud view: "now" is the moment the appliance built this snapshot, so
  // staleness means "was this device silent when the snapshot was taken",
  // not "was the snapshot taken a while ago" (the pill reports that).
  _frame.nowSec = (CLOUD_VIEW && frame.ts) ? frame.ts : Date.now() / 1000;
  _frame.bank = null;
  _frame.flowModel = null;
  renderStatus(frame.poll_run || {});
  renderHero();
  renderFlow();
  // Kiosk skin: build the versioned view-model from the same bank + flow
  // model the dashboard uses, and render the active skin. Wrapped so a
  // skin error can never break the main render loop.
  renderKiosk();
  renderToday();
  renderWeather();
  renderLocationTile();  // #264, "Where you are" map
  renderWeek();
  renderCells();
  renderSensors();
  renderDevices();
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
// with, auto-reconnects, no protocol upgrade, plain HTTP. We open one
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
  try {
    // _withKiosk attaches the kiosk ?token= when in kiosk mode so a
    // token'd wall display gets live updates; a no-op otherwise (the
    // session cookie auths the stream for a normal logged-in dashboard).
    eventStream = new EventSource(_withKiosk("/api/stream"));
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
  if (CLOUD_VIEW) return;
  // Wait for /api/system/info to resolve before deciding, without
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
  if (CLOUD_VIEW) {
    const age = run && run.cloud ? run.cloud.age_s : null;
    if (age == null) { setStatus("warn", "Waiting for data"); return; }
    if (age <= 600)  { setStatus("ok", "Online"); return; }
    if (age <= 3600) { setStatus("warn", "Delayed"); return; }
    setStatus("err", "Offline"); return;
  }
  // Daemon-side issues first, if the scheduler's not running or the
  // last_run is stale, nothing else matters.
  if (!run.scheduler_running) { setStatus("err", "Offline"); return; }
  const t = run.transports || {};
  // Demo mode (demo.wattpost.io) has a synthetic poller and no real
  // transports, the wizard redirect and "Setup needed" / "BLE not
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
//      for the bank's headline numbers, that's the case for users with
//      dumb LiFePO4 packs + a Victron SmartShunt / Renogy 500A monitor.
//
//   2. Otherwise, we sum across smart batteries (the typical Renogy rig).
//
//   3. With nothing addressable, we return null and the dashboard hides
//      the bank hero.
// Bank-level aggregator, mirrors the server-side reconciliation in
// solar_monitor/storage/sqlite.py:_compute_bank_aggregate (#121).
//
// Two distinct layers:
//   * System metrics (V, A, SoC, capacity, remaining) come from the
//     shunt when present, otherwise the BMS pack-sum. "Source" tag
//     in the output tells consumers which side fed the numbers.
//   * Cell metrics (min/max V across packs, worst pack drift) ALWAYS
//     come from BMSes, shunts don't have per-cell data. Surfaced
//     alongside the system metrics even when the shunt is driving.
//
// When BOTH a shunt and one or more BMSes are present AND their SoC
// readings disagree by >5 percentage points, attach a `disagreement`
// object so the hero tile can render a quiet "shunt 65%, BMS 72%,
// showing shunt, tap to investigate" hint.
function aggregateBank() {
  // Return the cached frame value if applySnapshot already computed one
  // (#162). Without this, hero + flow + alerts each re-derive their own
  // view of "fresh" against drifting Date.now() reads, leading to visible
  // disagreement on the 90 s staleness boundary.
  if (_frame.bank !== null) return _frame.bank;
  // Skip devices whose latest poll is stale (>90 s). Without this,
  // an offline Renogy BT-2 keeps feeding the bank aggregate from a
  // snapshot 50+ minutes old, the SoC donut and Net-power tile then
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
  // Inverter-as-source: when neither shunt nor BMS is present, a
  // hybrid inverter is the only device that knows SoC + bank V/A.
  // Match any kind starting with "inverter" so Voltronic (kind=
  // inverter), EG4 XP (kind=inverter), and Deye/Sunsynk/Sol-Ark
  // (kind=inverter_1p or inverter_3p) all qualify. Mirrors the
  // server-side fallback chain in
  // storage/sqlite._compute_bank_aggregate.
  const inverter = devices.find(d => (d.kind || "").startsWith("inverter")
    && isFresh(d)
    && d.latest && d.latest.battery_voltage_v != null
    && d.latest.soc_pct != null);
  // Charge controller as the last-resort source: a controller-only rig
  // (e.g. a Renogy Rover with no shunt/BMS) reports battery voltage + a
  // rough SoC but no pack capacity. Mirrors the server-side fallback
  // chain in storage/sqlite._compute_bank_aggregate.
  const controller = devices.find(d => d.kind === "charge_controller"
    && isFresh(d)
    && d.latest && d.latest.battery_voltage_v != null);
  if (!shunt && batts.length === 0 && !inverter && !controller) { _frame.bank = null; return null; }

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

  // Inverter view, last-resort source when neither shunt nor BMS is
  // configured. The inverter knows SoC + battery V/A but NOT capacity
  // (capacity is bank-config knowledge that lives on a shunt/BMS),
  // so totalCap/totalRem are left at 0 and the "time to empty" hint
  // gracefully degrades to V*I extrapolation.
  let inverterView = null;
  if (inverter) {
    const l = inverter.latest || {};
    const v = +l.battery_voltage_v || 0;
    const i = +l.battery_current_a || 0;
    const power_w = l.battery_power_w != null ? +l.battery_power_w : v * i;
    const soc = +l.soc_pct || 0;
    inverterView = {
      source: "inverter",
      model: inverter.label || "hybrid inverter",
      soc, meanV: v, sumI: i, netW: power_w, totalCap: 0, totalRem: 0,
      timeToGoMinutes: null,
    };
  }

  // Charge-controller view, last resort. Battery voltage + the
  // controller's rough SoC; no capacity (a controller can't measure
  // pack Ah), so totalCap/totalRem stay 0. Mirrors the server.
  let controllerView = null;
  if (controller) {
    const l = controller.latest || {};
    const v = +l.battery_voltage_v || 0;
    const i = +l.battery_current_a || 0;
    const soc = +l.battery_percentage || 0;
    controllerView = {
      source: "controller",
      model: l.model || controller.label || "charge controller",
      soc, meanV: v, sumI: i, netW: v * i, totalCap: 0, totalRem: 0,
      timeToGoMinutes: null,
    };
  }

  // ---------- Source pick (auto policy: shunt > BMS > inverter > controller) ----------
  const chosen = shuntView || bmsView || inverterView || controllerView;

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

  // Per-pack capacity bars for the kiosk Command skin's Bank tile.
  // BMS path → one bar per physical pack (capacity + fill); shunt /
  // inverter path → a single bar standing in for the whole declared
  // bank, so the tile is never blank on a single-bank system.
  const _clamp01 = (x) => Math.max(0, Math.min(1, x));
  let bankCells = [];
  if (batts.length) {
    bankCells = batts.map((b) => {
      const l = b.latest || {};
      const cap = +l.capacity_ah || 0;
      const rem = +l.remaining_charge_ah || 0;
      return { ah: cap, frac: cap > 0 ? _clamp01(rem / cap) : 0 };
    }).filter((c) => c.ah > 0);
  }
  if (!bankCells.length && chosen && +chosen.totalCap > 0) {
    bankCells = [{ ah: +chosen.totalCap, frac: _clamp01((+chosen.soc || 0) / 100) }];
  }

  const result = {
    ...chosen,
    packs: batts.length,
    bankCells,
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
  // Prefer the shunt's time_to_go_minutes when it's available, it's
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
  // The 1.5 A "Idle" guard is one-sided, it only protects the
  // discharge case. Reporting "2 days until empty" off a 0.6 A
  // standby draw is misleading (the moment real load kicks in,
  // the estimate is wildly wrong). But applying the same threshold
  // to charging would label slow MPPT trickle as "Idle", which is
  // worse, the user IS charging, just slowly. So: only suppress
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
  // remaining capacity before the divide, LFP wants to stay above
  // 10% SoC, and the BMS will cut earlier than 0% anyway. Keeps the
  // estimate from over-promising runtime that the battery will
  // never actually deliver.
  const RESERVE_FRAC = 0.10;
  const usableRem = Math.max(0,
    bank.totalRem - bank.totalCap * RESERVE_FRAC);
  const hoursToEmpty = usableRem / absI;
  return { primary: fmt.duration(hoursToEmpty), secondary: "until empty" };
}
