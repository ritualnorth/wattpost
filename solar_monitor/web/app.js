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
  shunt: "battery",
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
  dcdc_charger:  {
    sources: [{ id: "alt", label: "Alternator", color: "grid", icon: "alternator",
                metric: "alt_power_w", vMetric: "alt_voltage_v", aMetric: "alt_current_a" }],
  },
  inverter: {
    loads: [{ id: "ac", label: "AC Load", color: "ac", icon: "bolt",
              metric: "ac_output_power_w", vMetric: "ac_output_voltage_v",
              aMetric: "ac_output_current_a" }],
  },
};

// ---------- helpers ----------
const $ = (sel) => document.querySelector(sel);

const fmt = {
  num(v, digits = 2) {
    if (v === null || v === undefined || typeof v !== "number") return "—";
    const abs = Math.abs(v);
    if (abs >= 10000) return (v / 1000).toFixed(1) + "k";
    if (abs >= 100)   return v.toFixed(0);
    if (abs >= 10)    return v.toFixed(1);
    return v.toFixed(digits);
  },
  wh(v) {
    if (v == null) return "—";
    if (Math.abs(v) >= 1000) return (v / 1000).toFixed(2) + " kWh";
    return v.toFixed(0) + " Wh";
  },
  ah(v, d = 1) {
    if (v == null) return "—";
    return v.toFixed(d);
  },
  signed(v, digits = 0) {
    if (v == null) return "—";
    return (v > 0 ? "+" : "") + v.toFixed(digits);
  },
  ago(unixTs) {
    const s = Math.floor(Date.now() / 1000) - unixTs;
    if (s < 60) return s + "s ago";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    return Math.floor(s / 3600) + "h ago";
  },
  duration(hours) {
    if (hours == null || !isFinite(hours)) return "—";
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
  else if (route === "dashboard") { refreshDriftSparkline?.(); }
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
async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

// Shared apply path: REST fallback and the SSE stream both flow through
// here. The frame shape (devices / poll_run / today) is the same in both
// directions so this stays a one-liner per renderer.
function applySnapshot(frame) {
  devices = frame.devices || [];
  lastRun = frame.poll_run?.last_run || null;
  todayAggregate = frame.today || null;
  renderStatus(frame.poll_run || {});
  renderHero();
  renderFlow();
  // Kiosk view shares aggregateBank/buildFlowModel but lives in a
  // separate DOM tree — mirror the flow strip into it on every frame.
  const kioskFlow = $("#kiosk-flow");
  if (kioskFlow) renderFlow(kioskFlow);
  renderToday();
  renderCells();
  renderDeviceCards();
  populateChartSelectors();
  renderAlerts();
  $("#devices-meta").textContent = lastRun
    ? `${devices.length} devices · last poll ${lastRun.elapsed_ms} ms · ${fmt.ago(lastRun.ts)}`
    : "";
}

async function refresh() {
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
  } catch (e) {
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
function openStream() {
  if (eventStream) return;
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

function renderStatus(run) {
  if (!run.last_run) { setStatus("warn", "Connecting…"); return; }
  const lr = run.last_run;
  const ageS = Math.floor(Date.now() / 1000) - lr.ts;
  // Detailed metrics live in Settings. Header pill is just the answer
  // to "am I OK?" — one word + (optional) error count.
  if (!run.scheduler_running)        setStatus("err",  "Offline");
  else if (ageS > 300)                setStatus("err",  "Stale");
  else if (lr.errors_count > 0)       setStatus("warn", `${lr.errors_count} error${lr.errors_count===1?"":"s"}`);
  else if (ageS > 120)                setStatus("warn", "Comms slow");
  else                                 setStatus("ok",   "Healthy");
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
function aggregateBank() {
  const shunt = devices.find(d => d.kind === "shunt");
  const batts = devices.filter(d => d.kind === "smart_battery");

  if (shunt) {
    const l = shunt.latest || {};
    const v = +l.voltage_v || 0;
    const i = +l.current_a || 0;
    const power_w = l.power_w != null ? +l.power_w : v * i;
    const totalCap = +l.bank_capacity_ah || +l.capacity_ah || 0;
    const totalRem = +l.remaining_ah || (totalCap * ((+l.soc_pct || 0) / 100));
    const soc = +l.soc_pct || (totalCap > 0 ? (totalRem / totalCap) * 100 : 0);
    return {
      source: "shunt",
      packs: batts.length,                  // declared packs alongside the shunt
      model: l.model || shunt.label || "smart shunt",
      soc, meanV: v, sumI: i, netW: power_w,
      totalCap, totalRem,
    };
  }

  if (batts.length === 0) return null;

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
  return {
    source: "batteries",
    packs: batts.length,
    model: batts[0]?.latest?.model || "battery",
    soc, meanV, sumI, netW: meanV * sumI, totalCap, totalRem,
  };
}

function computeRemaining(bank) {
  if (!bank) return { primary: "—", secondary: "" };
  const i = bank.sumI;
  if (Math.abs(i) < 0.5) return { primary: "Idle", secondary: "—" };
  if (i > 0) {
    const hoursToFull = (bank.totalCap - bank.totalRem) / i;
    return { primary: fmt.duration(hoursToFull), secondary: "until full" };
  } else {
    const hoursToEmpty = bank.totalRem / Math.abs(i);
    return { primary: fmt.duration(hoursToEmpty), secondary: "until empty" };
  }
}

// ---------- HERO ----------
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
  const socCls = pct < 20 ? "soc-low" : pct < 50 ? "soc-mid" : "soc-high";
  arc.classList.remove("soc-low", "soc-mid", "soc-high");
  arc.classList.add(socCls);
  // Mirror the SoC paint onto the kiosk donut (lives in a different DOM
  // tree but uses the same class hooks).
  const kioskArc = document.querySelector(".kiosk-donut .donut-arc");
  if (kioskArc) {
    kioskArc.setAttribute("stroke-dasharray", `${pct} ${100 - pct}`);
    kioskArc.classList.remove("soc-low", "soc-mid", "soc-high");
    kioskArc.classList.add(socCls);
  }
  const kioskSoc = $("#kiosk-soc");
  if (kioskSoc) kioskSoc.textContent = bank.soc.toFixed(1);
  // Tint the hero container with the same SoC band so the card hue
  // matches the donut color.
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

  // Donut wrapper state — drives ring color, pulse animation direction,
  // glow, and the small flow-indicator pill under "State of charge".
  // Applied to both the dashboard wrapper and the kiosk one (both carry
  // the shared .donut-state class).
  document.querySelectorAll(".donut-state").forEach(el => {
    el.classList.remove("charging", "discharging", "idle");
    el.classList.add(powerState);
  });
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

  // Other stats
  $("#bank-voltage").textContent = bank.meanV.toFixed(2);
  $("#bank-capacity").textContent = bank.totalCap.toFixed(0);
  $("#bank-remaining").textContent = bank.totalRem.toFixed(1);
  // Bank meta is a long string (e.g. "3× RBT100LFP12S-G1") — shrink to
  // text style so it fits the small grid cell on mobile.
  const bankMetaTile = $("#bank-meta").closest(".hero-stat-val");
  if (bankMetaTile) bankMetaTile.classList.add("is-text");
  // Use a compact model: just the trailing pack count + abbreviated SKU
  const shortModel = (bank.model || "")
    .replace(/^RBT/, "RBT")
    .replace(/-G\d$/, "");
  $("#bank-meta").textContent = `${bank.packs}× ${shortModel}`;
}

// ---------- POWER FLOW ----------
function buildFlowModel() {
  const sources = [];
  const loads   = [];
  let batteryNetW = 0;

  for (const dev of devices) {
    const mapping = FLOW_MAPPING[dev.kind];
    if (!mapping) continue;
    const l = dev.latest || {};

    if (mapping.battery) {
      // Smart batteries: sum V × I across packs. Shunts (future): just power_w.
      if (typeof l.voltage_v === "number" && typeof l.current_a === "number") {
        batteryNetW += l.voltage_v * l.current_a;
      } else if (typeof l.power_w === "number") {
        batteryNetW += l.power_w;
      }
    }
    // Sources: ALWAYS render configured sources, even when they're at 0 W.
    // A user who's set up an MPPT wants to see it on the dashboard at all
    // times — "0 W idle" is informative; hiding the tile makes the system
    // look misconfigured.
    for (const s of mapping.sources || []) {
      if (s.onlyIf && !s.onlyIf(l)) continue;
      const w = +l[s.metric] || 0;
      const subParts = [
        typeof l[s.vMetric] === "number" ? `${l[s.vMetric].toFixed(1)} V` : null,
        typeof l[s.aMetric] === "number" ? `${l[s.aMetric].toFixed(2)} A` : null,
      ].filter(Boolean);
      sources.push({
        id: `${dev.label}.${s.id}`,
        label: s.label,
        device: dev.label,
        color: s.color,
        icon: s.icon || COLOR_TO_ICON[s.color],
        power: w,
        active: w > 1,
        // When the source is idle, say so explicitly rather than just
        // showing V/A (which can look like the device is broken).
        sub: w > 0 ? subParts.join(" · ") : (subParts.length ? `${subParts.join(" · ")} · idle` : "idle"),
      });
    }
    // Loads: keep the onlyIf filter — the Rover's load output really is
    // off for most users, and showing "DC Load: 0 W idle" would be clutter,
    // not signal. Inferred bus-loads are still surfaced separately below.
    for (const lo of mapping.loads || []) {
      if (lo.onlyIf && !lo.onlyIf(l)) continue;
      const w = +l[lo.metric] || 0;
      if (w <= 0 && !lo.onlyIf) continue;
      loads.push({
        id: `${dev.label}.${lo.id}`,
        label: lo.label,
        device: dev.label,
        color: lo.color,
        icon: lo.icon || COLOR_TO_ICON[lo.color],
        power: w,
        active: w > 1,
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

  return { sources, loads, batteryNetW, bank };
}

function renderFlow(targetHost) {
  // Default to the dashboard's flow strip. The kiosk view passes its own
  // host so we can mount a second copy of the strip inside the kiosk
  // layout — same components, just scaled up by CSS.
  const host = targetHost || $("#flow");
  const sub  = host === $("#flow") ? $("#flow-sub") : null;
  host.innerHTML = "";

  const model = buildFlowModel();
  if (!model.bank && model.sources.length === 0 && model.loads.length === 0) {
    host.innerHTML = `<div class="flow-empty">No active devices yet.</div>`;
    if (sub) sub.textContent = "";
    return;
  }

  const hasSources = model.sources.length > 0;
  const hasLoads   = model.loads.length > 0;
  const totalSourceW = model.sources.reduce((a, s) => a + s.power, 0);
  const totalLoadW   = model.loads.reduce((a, l) => a + l.power, 0);

  // With configured sources always present, "no sources AND no loads" is
  // only true when the user genuinely hasn't set up any source/load
  // devices (e.g. battery-only topology — bank shunt and dumb packs).
  // In that case still show a clean centered battery tile.
  if (!hasSources && !hasLoads) {
    host.classList.add("flow--idle");
    const battCol = document.createElement("div");
    battCol.className = "flow-col flow-col--solo";
    if (model.bank) {
      const b = model.bank;
      battCol.appendChild(makeFlowTile({
        label: "Battery bank",
        color: "batt",
        icon: "battery",
        power: b.netW,
        signed: true,
        active: false,
        sub: `${b.soc.toFixed(1)} % · ${b.meanV.toFixed(2)} V`,
      }));
    }
    host.appendChild(battCol);
    if (sub) sub.textContent = "no sources or loads configured";
    return;
  }
  host.classList.remove("flow--idle");

  // ----- Sources column (only when present) -----
  if (hasSources) {
    const sourcesCol = document.createElement("div");
    sourcesCol.className = "flow-col flow-sources";
    for (const s of model.sources) sourcesCol.appendChild(makeFlowTile(s));
    host.appendChild(sourcesCol);

    host.appendChild(makeConnector({
      label: `${totalSourceW.toFixed(0)} W`,
      fromColor: model.sources[0]?.color || "neutral",
      toColor: "batt",
      active: totalSourceW > 1,
    }));
  }

  // ----- Battery tile (always) -----
  const battCol = document.createElement("div");
  battCol.className = "flow-col";
  if (model.bank) {
    const b = model.bank;
    battCol.appendChild(makeFlowTile({
      label: "Battery bank",
      color: "batt",
      icon: "battery",
      power: b.netW,
      signed: true,
      active: Math.abs(b.netW) > 1,
      sub: `${b.soc.toFixed(1)} % · ${b.meanV.toFixed(2)} V`,
    }));
  } else {
    battCol.appendChild(makeFlowTile({ label: "Battery", color: "neutral", icon: "battery", power: 0, sub: "—" }, true));
  }
  host.appendChild(battCol);

  // ----- Loads column (only when present) -----
  if (hasLoads) {
    host.appendChild(makeConnector({
      label: `${totalLoadW.toFixed(0)} W`,
      fromColor: "batt",
      toColor: model.loads[0]?.color || "neutral",
      active: totalLoadW > 1,
    }));

    const loadsCol = document.createElement("div");
    loadsCol.className = "flow-col flow-loads";
    for (const lo of model.loads) loadsCol.appendChild(makeFlowTile(lo));
    host.appendChild(loadsCol);
  }

  // Sub-header summary — describe whatever's actually present
  const parts = [];
  if (hasSources) parts.push(`${model.sources.length} source${model.sources.length === 1 ? "" : "s"} · ${totalSourceW.toFixed(0)} W in`);
  if (hasLoads)   parts.push(`${model.loads.length} load${model.loads.length === 1 ? "" : "s"} · ${totalLoadW.toFixed(0)} W out`);
  if (sub) sub.textContent = parts.join(" · ") || "system idle";
}

function makeFlowTile(t, muted = false) {
  const div = document.createElement("div");
  const classes = [`flow-tile`, t.color || "neutral"];
  if (muted) classes.push("muted");
  if (t.active) classes.push("is-active");
  if (t.inferred) classes.push("inferred");
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

// ---------- TODAY STRIP ----------
function renderToday() {
  const rover = devices.find(d => d.kind === "charge_controller");
  const l = rover?.latest || {};
  $("#today-pv").textContent       = fmt.wh(l.energy_today_wh);
  $("#today-charged").textContent  = (l.charging_ah_today ?? 0) + " Ah";
  $("#today-peak").textContent     = fmt.num(l.max_charging_power_today_w, 0) + " W";
  // Load today comes from /api/today (computed from energy balance across
  // all polls). The Rover's `consumption_today_wh` only counts its load
  // output terminals — useless for the typical busbar wiring.
  if (todayAggregate && typeof todayAggregate.load_today_wh === "number") {
    $("#today-load").textContent = fmt.wh(todayAggregate.load_today_wh);
  } else {
    $("#today-load").textContent = fmt.wh(l.consumption_today_wh);
  }
  $("#today-lifetime").textContent = fmt.wh(l.energy_total_wh);
}

// ---------- ALERTS ----------
function renderAlerts() {
  const host = $("#alerts");
  host.innerHTML = "";
  const alerts = [];

  // Comms / poll health
  if (lastRun) {
    const age = Math.floor(Date.now() / 1000) - lastRun.ts;
    if (age > 300) alerts.push({ level: "alarm", msg: `No successful poll for ${fmt.ago(lastRun.ts)}` });
    else if (age > 120) alerts.push({ level: "warn", msg: `Last poll ${fmt.ago(lastRun.ts)} — comms slow` });
    if (lastRun.errors_count > 0) alerts.push({
      level: "warn",
      msg: `${lastRun.errors_count} device error${lastRun.errors_count === 1 ? "" : "s"} on last poll`,
    });
  } else {
    alerts.push({ level: "warn", msg: "Daemon hasn't completed its first poll yet" });
  }

  // Bank-level
  const bank = aggregateBank();
  if (bank) {
    if (bank.soc < 10) alerts.push({ level: "alarm", msg: `Bank state of charge critical (${bank.soc.toFixed(1)} %)` });
    else if (bank.soc < 20) alerts.push({ level: "warn", msg: `Bank state of charge low (${bank.soc.toFixed(1)} %)` });
  }

  // Per-device alerts
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
        alerts.push({ level: "alarm", msg: `${dev.label}: cell drift ${spread.toFixed(2)} V` });
      else if (spread >= 0.10)
        alerts.push({ level: "warn", msg: `${dev.label}: cell drift ${spread.toFixed(2)} V` });
      if (cells.some(v => v > 3.65))
        alerts.push({ level: "alarm", msg: `${dev.label}: cell over-voltage` });
      if (cells.some(v => v < 2.8))
        alerts.push({ level: "alarm", msg: `${dev.label}: cell under-voltage` });
    }
    const temps = [];
    const tn = +l.temperature_sensor_count || 0;
    for (let i = 0; i < tn; i++) {
      const t = l[`temperature_${i}_c`];
      if (typeof t === "number") temps.push(t);
    }
    for (const t of temps) {
      if (t >= 60) { alerts.push({ level: "alarm", msg: `${dev.label}: cell temp ${t.toFixed(0)} °C` }); break; }
      if (t >= 50) { alerts.push({ level: "warn", msg: `${dev.label}: cell temp ${t.toFixed(0)} °C` }); break; }
    }
  }
  // Controller temps
  for (const dev of devices.filter(d => d.kind === "charge_controller")) {
    const t = +dev.latest?.controller_temperature_c;
    if (typeof t === "number") {
      if (t >= 70) alerts.push({ level: "alarm", msg: `${dev.label}: MPPT hot (${t.toFixed(0)} °C)` });
      else if (t >= 60) alerts.push({ level: "warn", msg: `${dev.label}: MPPT warm (${t.toFixed(0)} °C)` });
    }
  }

  if (alerts.length === 0) { host.hidden = true; return; }
  host.hidden = false;
  for (const a of alerts) {
    const row = document.createElement("div");
    row.className = `alert-row ${a.level}`;
    row.innerHTML = `
      <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M12 2L2 21h20L12 2z"/>
        <path d="M12 9v6M12 18h.01"/>
      </svg>
      <span>${a.msg}</span>`;
    host.appendChild(row);
  }
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

// ---------- device cards ----------
function renderDeviceCards() {
  const host = $("#device-cards");
  host.innerHTML = "";
  // Don't show the synthetic "bank" pseudo-device on the Devices tab —
  // it's an aggregate, not real hardware. It still appears in the History
  // dropdown so users can chart bank.soc_pct / .power_w / etc.
  const visible = devices.filter(d => d.kind !== "bank");
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
    name.textContent = dev.label;
    left.append(iconSpan, name);
    const right = document.createElement("div");
    right.className = "dev-card-head-right";
    right.innerHTML = `
      <span class="dev-card-slave">slave ${dev.slave_id}</span>
      <svg class="dev-card-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 6 15 12 9 18"/></svg>`;
    head.append(left, right);
    card.appendChild(head);

    const sub = document.createElement("div");
    sub.className = "dev-card-sub";
    const fw = l.firmware_version || l.firmware_version_raw || "";
    sub.textContent = `${dev.vendor} · ${dev.kind}${fw ? " · fw " + fw : ""}${l.model ? " · " + l.model : ""}`;
    card.appendChild(sub);

    // Lifetime stats strip for smart batteries — cycles + Ah throughput.
    // Fetched in the background; injected when ready.
    if (dev.kind === "smart_battery") {
      const lifeBar = document.createElement("div");
      lifeBar.className = "dev-card-lifetime";
      lifeBar.innerHTML = `
        <div class="lt-cell"><span class="meta-k">Cycles</span><span class="lt-v" data-lt="cycles">—</span></div>
        <div class="lt-cell"><span class="meta-k">Ah in</span><span class="lt-v" data-lt="ah_in">—</span></div>
        <div class="lt-cell"><span class="meta-k">Ah out</span><span class="lt-v" data-lt="ah_out">—</span></div>`;
      card.appendChild(lifeBar);
      ensureLifetime(dev.label).then(lt => {
        if (!lt) return;
        lifeBar.querySelector('[data-lt="cycles"]').textContent = lt.cycles?.toFixed(2) ?? "—";
        lifeBar.querySelector('[data-lt="ah_in"]').textContent = `${(+lt.ah_in).toFixed(1)} Ah`;
        lifeBar.querySelector('[data-lt="ah_out"]').textContent = `${(+lt.ah_out).toFixed(1)} Ah`;
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

async function refreshChart() {
  const label = $("#sel-device").value;
  const metric = $("#sel-metric").value;
  if (!label || !metric) return;

  let url;
  if (currentRange === "custom") {
    const p = customRangeParams();
    if (!p) return;  // user hasn't picked a valid range yet
    url = `/api/devices/${encodeURIComponent(label)}/history?metric=${encodeURIComponent(metric)}` +
          `&since=${p.since}&until=${p.until}&bucket=${p.bucket}`;
  } else {
    const [since, bucket] = sinceForRange(currentRange);
    url = `/api/devices/${encodeURIComponent(label)}/history?metric=${encodeURIComponent(metric)}` +
          `&since=${since}&bucket=${bucket}`;
  }

  let data;
  try { data = await api(url); }
  catch (e) { console.error(e); return; }
  updateStatStrip(metric, data);
  drawChart(label, metric, data);
}

function updateStatStrip(metric, data) {
  const unit = unitFromKey(metric);
  const s = data?.stats || {};
  const fmtV = (v) => v == null ? "—" : `${(+v).toFixed(2)}${unit ? " " + unit : ""}`;
  $("#cs-now").textContent   = fmtV(s.now);
  $("#cs-min").textContent   = fmtV(s.min);
  $("#cs-avg").textContent   = fmtV(s.avg);
  $("#cs-max").textContent   = fmtV(s.max);
  $("#cs-range").textContent = s.range == null ? "—" : `${(+s.range).toFixed(2)}${unit ? " " + unit : ""}`;

  // Resolution = bucket / table info: tell the user how dense the data is
  const tableLabel = {
    samples: "raw",
    samples_1min: "1-min avg",
    samples_1hour: "1-hour avg",
    samples_1day: "1-day avg",
  }[data?.table] || "—";
  $("#cs-res").textContent = `${s.count ?? 0} pts · ${tableLabel}`;
}

function drawChart(label, metric, data) {
  const root = $("#chart");
  if (chart) { chart.destroy(); chart = null; }
  const unit = unitFromKey(metric);
  const width = Math.max(root.clientWidth, 320);

  const ts = data.ts;
  const vals = data.values;
  const hasBand = Array.isArray(data.min) && Array.isArray(data.max) &&
                  data.min.length === ts.length && data.min.length > 0;

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
      value: (_u, v) => v == null ? "—" : `${(+v).toFixed(2)}${unit ? " " + unit : ""}`,
    });
    series.push({
      label: "max", stroke: "transparent", width: 0, points: { show: false },
      value: (_u, v) => v == null ? "—" : `${(+v).toFixed(2)}${unit ? " " + unit : ""}`,
    });
    dataCols.push(data.min, data.max);
    // Fill between max (series 2) and min (series 1).
    bands = [{ series: [2, 1], fill: pal.bandFill }];
  }

  // Main line — last series, always visible.
  series.push({
    label: prettyKey(metric),
    stroke: pal.accent,
    width: 2,
    fill: pal.accentFill,
    points: { show: ts.length < 60, size: 4, fill: pal.accent, stroke: pal.accent },
    value: (_u, v) => v == null ? "—" : `${(+v).toFixed(2)}${unit ? " " + unit : ""}`,
  });
  dataCols.push(vals);

  // Auto-fit X scale to actual data — uPlot otherwise extends the visible
  // range to weird year boundaries when only a tiny slice has data (which
  // is what's happening on a freshly-seeded daemon).
  const tsMin = ts.length ? ts[0] : null;
  const tsMax = ts.length ? ts[ts.length - 1] : null;
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
    legend: { live: true },
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
    stat.textContent = "—";
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
      legend: { show: false },
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
const VALID_ROUTES = new Set(["dashboard", "history", "devices", "setup", "settings", "kiosk"]);

function parseRoute() {
  const raw = (window.location.hash || "").replace(/^#\/?/, "").trim();
  const m = raw.match(/^device\/(.+)$/);
  if (m) return { name: "device", label: decodeURIComponent(m[1]) };
  return { name: VALID_ROUTES.has(raw) ? raw : "dashboard" };
}
function currentRouteName() { return parseRoute().name; }

function setRoute(_unused) {
  const route = parseRoute();
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
    requestAnimationFrame(() => { refreshChart(); refreshHeatmap(); });
  }
  if (route.name === "settings") { renderSettings(); startDiagTimer(); }
  else { stopDiagTimer(); }
  if (route.name === "dashboard") refreshDriftSparkline();
  if (route.name === "device") renderDeviceDetail(route.label);
  if (route.name === "setup") onEnterSetup();
  if (route.name === "kiosk") onEnterKiosk();
  else if (document.body.classList.contains("kiosk-active")) onLeaveKiosk();
  window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
}

window.addEventListener("hashchange", () => setRoute(currentRouteName()));

// ---------- settings panel ----------
function renderSettings() {
  if (lastRun) {
    $("#settings-last-poll").textContent =
      `${lastRun.elapsed_ms} ms · ${fmt.ago(lastRun.ts)}`;
    $("#settings-errors").textContent = String(lastRun.errors_count);
  }
  // Daemon status implied from the same source as the header pill.
  const ok = lastRun && lastRun.errors_count === 0;
  $("#settings-daemon").textContent = ok ? "running, healthy" : (lastRun ? "running, errors" : "no data");
  // MQTT we don't query directly; best effort message until /api/exporters exists
  $("#settings-mqtt").textContent = "see config.yaml";
  refreshAlertsPanel();
  refreshSystemInfo();
  refreshTailscale();
}

// ---------- system info (About block) ----------
function fmtBytes(b) {
  if (b == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = b, i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 10 ? 0 : 1)} ${units[i]}`;
}
function fmtDuration(s) {
  if (s == null) return "—";
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
  const d = info.disk || {};
  const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
  set("#settings-uptime", fmtDuration(info.uptime_seconds));
  set("#settings-python", info.python || "—");
  set(
    "#settings-disk",
    d.total
      ? `${fmtBytes(d.used)} / ${fmtBytes(d.total)} · ${d.percent}% used`
      : "—",
  );
}

// ---------- Tailscale (Network block) ----------
async function refreshTailscale() {
  const host = $("#settings-tailscale");
  if (!host) return;
  let s;
  try { s = await api("/api/system/tailscale/status"); }
  catch (e) {
    host.innerHTML = `<div class="settings-empty">Could not check Tailscale: ${e.message}</div>`;
    return;
  }
  if (!s.installed) {
    host.innerHTML = `
      <div class="ts-state-row">
        <div class="ts-state-main">
          <span class="ts-state-title">Tailscale isn't installed</span>
          <span class="ts-state-sub">Install on the appliance, then reload this page.</span>
        </div>
        <span class="ts-state-tag ts-state-tag--off">not installed</span>
      </div>
      <div class="ts-install">${s.install_hint || "curl -fsSL https://tailscale.com/install.sh | sh"}</div>`;
    return;
  }
  let html = "";
  if (s.logged_in && s.ipv4) {
    const dns = s.dns_name || "";
    const httpUrl  = dns ? `http://${dns}:8000/` : `http://${s.ipv4}:8000/`;
    const httpsUrl = s.https_url;  // server-side: only present when serve is active
    html += `
      <div class="ts-state-row">
        <div class="ts-state-main">
          <span class="ts-state-title">Connected · ${s.hostname || "wattpost"}</span>
          <span class="ts-state-sub">${s.ipv4}${dns ? ` · ${dns}` : ""}</span>
        </div>
        <span class="ts-state-tag ts-state-tag--ok">on tailnet</span>
      </div>`;
    if (httpsUrl) {
      html += `
        <div class="settings-foot">
          Open from anywhere (HTTPS, real cert):
          <a href="${httpsUrl}">${httpsUrl}</a>
        </div>
        <div class="settings-foot">Plain HTTP also works: <a href="${httpUrl}">${httpUrl}</a></div>`;
    } else {
      html += `
        <div class="settings-foot">Open from anywhere: <a href="${httpUrl}">${httpUrl}</a></div>
        <div class="settings-foot">
          Want a real HTTPS cert (no "Not Secure" warning)?
          <button id="ts-enable-https" class="alerts-add-btn" style="margin-left:.35rem">Enable HTTPS via Tailscale Serve</button>
        </div>`;
    }
    html += `
      <div class="ts-actions">
        <button id="ts-disconnect" class="alerts-add-btn">Disconnect</button>
      </div>`;
  } else {
    html += `
      <div class="ts-state-row">
        <div class="ts-state-main">
          <span class="ts-state-title">Not connected</span>
          <span class="ts-state-sub">${s.backend ? `state: ${s.backend}` : ""}</span>
        </div>
        <span class="ts-state-tag ts-state-tag--warn">offline</span>
      </div>
      <div class="ts-actions">
        <button id="ts-connect" class="alerts-add-btn">Connect to my tailnet</button>
      </div>`;
  }
  host.innerHTML = html;

  const connect    = $("#ts-connect");
  const disconnect = $("#ts-disconnect");
  const enableHttps = $("#ts-enable-https");
  if (connect)    connect.addEventListener("click", tailscaleConnect);
  if (disconnect) disconnect.addEventListener("click", tailscaleDisconnect);
  if (enableHttps) enableHttps.addEventListener("click", tailscaleEnableHttps);
}

async function tailscaleEnableHttps() {
  const btn = $("#ts-enable-https");
  if (btn) { btn.disabled = true; btn.textContent = "Enabling…"; }
  try {
    const r = await fetch("/api/system/tailscale/serve", { method: "POST" });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `${r.status} ${r.statusText}`);
    }
    // Give Tailscale a couple seconds to provision the cert, then
    // re-read status (will populate https_url).
    setTimeout(refreshTailscale, 1500);
  } catch (e) {
    alert(e.message);
    if (btn) { btn.disabled = false; btn.textContent = "Enable HTTPS via Tailscale Serve"; }
  }
}

async function tailscaleConnect() {
  const host = $("#settings-tailscale");
  if (!host) return;
  host.innerHTML = `<div class="settings-empty">Starting Tailscale… (this can take a few seconds)</div>`;
  try {
    const r = await fetch("/api/system/tailscale/up", { method: "POST" });
    const data = await r.json();
    if (data.already_authed) {
      // We're back on a known tailnet — refresh shows the connected pill.
      await refreshTailscale();
      return;
    }
    if (data.auth_url) {
      host.innerHTML = `
        <div class="ts-auth">
          <span class="ts-auth-title">Log in to your tailnet to finish</span>
          <a href="${data.auth_url}" target="_blank" rel="noopener">${data.auth_url}</a>
          <span class="settings-foot"><strong>Keep this link private.</strong> Anyone who opens it adds this appliance to <em>their</em> tailnet. It expires in ~10 minutes either way.</span>
          <span class="settings-foot">After authorising, refresh — this page should flip to "Connected · &lt;hostname&gt;".</span>
        </div>
        <div class="ts-actions">
          <button id="ts-refresh" class="alerts-add-btn">I've authorised — refresh</button>
        </div>`;
      $("#ts-refresh")?.addEventListener("click", refreshTailscale);
      return;
    }
    host.innerHTML = `<div class="settings-empty">Tailscale started but didn't return an auth URL. ${data.hint || ""}</div>`;
  } catch (e) {
    host.innerHTML = `<div class="settings-empty">Connect failed: ${e.message}</div>`;
  }
}

async function tailscaleDisconnect() {
  if (!confirm("Disconnect this appliance from your tailnet?")) return;
  try {
    const r = await fetch("/api/system/tailscale/down", { method: "POST" });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `${r.status} ${r.statusText}`);
    }
    await refreshTailscale();
  } catch (e) {
    alert(e.message);
  }
}

// ---------- alerts panel (full editor) ----------
const ALERT_OP_LABEL = { lt: "<", lte: "≤", gt: ">", gte: "≥", eq: "=", neq: "≠" };
// Common metric paths the user can pick without typing. Anything else
// works too — the field falls back to a free-text input.
const METRIC_SUGGESTIONS = [
  { value: "bank.soc_pct",              label: "Battery SoC (%)" },
  { value: "bank.netW",                 label: "Bank net power (W)" },
  { value: "bank.meanV",                label: "Bank voltage (V)" },
  { value: "bank.totalRem",             label: "Bank remaining (Ah)" },
  { value: "bank.totalCap",             label: "Bank capacity (Ah)" },
  { value: "bank.worst_pack_drift_v",   label: "Worst pack drift (V)" },
  { value: "aggregate.max_cell_drift_v",label: "Max cell drift (V)" },
];
const TRANSPORT_TYPES = [
  { value: "ntfy",            label: "ntfy",        keyField: "topic", placeholder: "my-private-topic" },
  { value: "discord_webhook", label: "Discord",     keyField: "url",   placeholder: "https://discord.com/api/webhooks/…" },
  { value: "webhook",         label: "Webhook",     keyField: "url",   placeholder: "https://example.com/hook" },
  { value: "smtp",            label: "Email (SMTP)",keyField: "host",  placeholder: "smtp.gmail.com" },
  { value: "mqtt",            label: "MQTT (LAN)",  keyField: "host",  placeholder: "127.0.0.1" },
];

let alertsState = { rules: [], transports: [], editing: null };  // editing: {type:'rule'|'transport', id, mode:'edit'|'add'}

async function refreshAlertsPanel() {
  const host = $("#settings-alerts");
  if (!host) return;
  try {
    const data = await api("/api/alerts");
    alertsState.rules = data.rules || [];
    alertsState.transports = data.transports || [];
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

  let html = `<div class="alerts-sub-head"><h4>Alert rules</h4>
    <button class="alerts-add-btn" data-add="rule">+ Add rule</button></div>`;

  if (alertsState.editing?.type === "rule" && alertsState.editing.mode === "add") {
    html += renderRuleForm(null, transportIds);
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
              `<option value="${m.value}" ${m.value === metric ? "selected" : ""}>${m.label} — ${m.value}</option>`).join("")}
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
          ? `<p class="settings-foot">No transports configured yet — add one below first.</p>`
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
  const extra = {};
  const fields = form.querySelectorAll("[data-transport-fields] input");
  fields.forEach(el => {
    if (el.type === "checkbox") {
      extra[el.name] = el.checked;
    } else if (el.value !== "" || el.name === "password") {
      if (el.name === "password" && el.value === "") return;  // don't overwrite with empty
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
  const editing = !!form.dataset.originalId;
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
  kioskExitBtn.addEventListener("click", () => {
    window.location.hash = "#/";
  });
}
const restartBtn = $("#restart-daemon-btn");
if (restartBtn) {
  restartBtn.addEventListener("click", restartDaemon);
}
const diagRefreshBtn = $("#diag-refresh");
if (diagRefreshBtn) diagRefreshBtn.addEventListener("click", refreshDiagLog);

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
// If this device is set to default-to-kiosk and the URL has no explicit
// hash, redirect before the initial setRoute runs.
if (kioskDefault() && (!window.location.hash || window.location.hash === "#" || window.location.hash === "#/")) {
  window.location.hash = "#/kiosk";
}

$("#sel-device").addEventListener("change", () => onDeviceChanged());
$("#sel-metric").addEventListener("change", refreshChart);
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
  else inner = buildGenericDetail(dev);

  // Prev/next nav between devices of the same kind makes "compare packs"
  // a one-tap operation.
  const siblings = devices.filter(d => d.kind === dev.kind);
  const idx = siblings.findIndex(d => d.label === dev.label);
  const prev = idx > 0 ? siblings[idx - 1] : null;
  const next = idx < siblings.length - 1 ? siblings[idx + 1] : null;

  const fw = dev.latest?.firmware_version || dev.latest?.firmware_version_raw || "";
  host.innerHTML = `
    <div class="dev-detail-head">
      <div class="dev-detail-crumb">
        <a href="#/devices">← Devices</a>
        <span class="dev-detail-title">
          <span class="dev-detail-icon">${ICONS[KIND_ICON[dev.kind] || "unknown"]}</span>
          ${dev.label}
        </span>
        <span class="dev-detail-meta">
          ${dev.vendor} · ${dev.kind} · slave ${dev.slave_id}${fw ? " · fw " + fw : ""}${dev.latest?.model ? " · " + dev.latest.model : ""}
        </span>
      </div>
      <div class="dev-detail-nav">
        ${prev ? `<a class="btn-action" href="#/device/${encodeURIComponent(prev.label)}">← ${prev.label}</a>` : ""}
        ${next ? `<a class="btn-action" href="#/device/${encodeURIComponent(next.label)}">${next.label} →</a>` : ""}
      </div>
    </div>
    ${inner}`;

  // Wire up the per-device chart after DOM is in place.
  wireDeviceDetailChart(dev);
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
  const meanTemp = temps.length ? (temps.reduce((a, c) => a + c, 0) / temps.length).toFixed(1) : "—";

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
          <span class="pill ${driftCls}"><span class="pill-dot"></span>spread ${spread.toFixed(2)} V · min ${cellMin ? cellMin.toFixed(2) : "—"} · max ${cellMax ? cellMax.toFixed(2) : "—"}</span>
        </div>
      </div>
      <div class="cell-row-cells" style="margin-top:.5rem">
        ${cells.map((cv, ci) => {
          let cls = "cell-chip";
          if (cv === cellMin && spread > 0.01) cls += " is-min";
          if (cv === cellMax && spread > 0.01) cls += " is-max";
          if (cv > 3.65) cls += " is-high";
          return `<div class="${cls}"><span class="cell-chip-k">cell ${ci+1}</span><span class="cell-chip-v">${cv != null ? cv.toFixed(2) + " V" : "—"}</span></div>`;
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
        <div class="panel-sub"><span class="pill green"><span class="pill-dot"></span>${l.charging_state || "—"}</span></div>
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
    ${buildHistoryBlock(dev, "pv_power_w")}
  `;
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
        <div class="lt-cell"><span class="meta-k">Cycles</span><span class="lt-v" data-lt="cycles">—</span></div>
        <div class="lt-cell"><span class="meta-k">Ah in</span><span class="lt-v" data-lt="ah_in">—</span></div>
        <div class="lt-cell"><span class="meta-k">Ah out</span><span class="lt-v" data-lt="ah_out">—</span></div>
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
            value: (_u, v) => v == null ? "—" : `${(+v).toFixed(2)}${unit ? " " + unit : ""}`,
          },
        ],
        axes: [
          { stroke: pal.axis, grid: { stroke: pal.grid } },
          { stroke: pal.axis, grid: { stroke: pal.grid },
            values: (_u, splits) => splits.map(v => v == null ? "" : `${(+v).toFixed(2)}${unit ? " " + unit : ""}`) },
        ],
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
      block.querySelector('[data-lt="cycles"]').textContent = lt.cycles?.toFixed(2) ?? "—";
      block.querySelector('[data-lt="ah_in"]').textContent = `${(+lt.ah_in).toFixed(1)} Ah`;
      block.querySelector('[data-lt="ah_out"]').textContent = `${(+lt.ah_out).toFixed(1)} Ah`;
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
      host.innerHTML = `<div class="wiz-empty">No transports configured yet. Add one to <code>config.yaml</code> and restart the daemon.</div>`;
      return;
    }
    host.innerHTML = transports.map(t => `
      <button class="wiz-transport ${t.id === wizState.transport ? 'active' : ''}" data-id="${t.id}" ${t.open ? '' : 'disabled'}>
        <div class="wiz-transport-main">
          <span class="wiz-transport-id">${t.id}</span>
          <span class="wiz-transport-addr">${t.address || ''}</span>
        </div>
        <span class="wiz-transport-state ${t.open ? 'on' : 'off'}">${t.open ? 'connected' : 'offline'}</span>
      </button>
    `).join("");
    host.querySelectorAll(".wiz-transport").forEach(btn => {
      btn.addEventListener("click", () => {
        if (btn.disabled) return;
        wizState.transport = btn.dataset.id;
        host.querySelectorAll(".wiz-transport").forEach(b => b.classList.toggle("active", b === btn));
        $("#wiz-step-scan").hidden = false;
        $("#wiz-scan-results").innerHTML = "";
        $("#wiz-scan-status").textContent = "";
      });
    });
  } catch (e) {
    host.innerHTML = `<div class="wiz-empty">Could not load transports: ${e.message}</div>`;
  }
}

async function wizScan() {
  if (!wizState.transport) return;
  const btn = $("#wiz-scan-btn");
  const status = $("#wiz-scan-status");
  const host = $("#wiz-scan-results");
  btn.disabled = true;
  status.textContent = "Probing slave IDs…";
  host.innerHTML = "";
  try {
    const r = await fetch("/api/setup/probe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transport: wizState.transport }),
    });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    const data = await r.json();
    wizState.scanResults = data.results;
    const alive = data.results.filter(x => x.alive);
    status.textContent = `${alive.length} device(s) responded out of ${data.results.length} probed`;
    renderScanResults(alive);
  } catch (e) {
    status.textContent = `Scan failed: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

function renderScanResults(alive) {
  const host = $("#wiz-scan-results");
  if (!alive.length) {
    host.innerHTML = `<div class="wiz-empty">No devices answered. Check the transport is connected and the gear is powered on, then try again.</div>`;
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
            <span class="wiz-row-model">${r.model || '—'}</span>
          </div>
          <div class="wiz-row-meta">
            <span class="wiz-tag">${r.vendor || 'unknown vendor'}</span>
            <span class="wiz-tag">${kindLbl}</span>
            ${known ? '<span class="wiz-tag wiz-tag--known">already added</span>' : ''}
          </div>
        </div>
        <div class="wiz-row-action">
          ${known ? '' : `<button class="btn-action btn-action--primary wiz-add-btn">+ Add</button>`}
        </div>
      </div>
    `;
  }).join("");
  host.querySelectorAll(".wiz-row").forEach(row => {
    const btn = row.querySelector(".wiz-add-btn");
    if (btn) btn.addEventListener("click", () => wizExpandRow(row));
  });
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
function onEnterSetup() { wizLoadTransports(); }

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
    host.textContent = "(no log lines captured yet — daemon just started)";
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
  if (currentRouteName() === "dashboard") refreshDriftSparkline();
  if (currentRouteName() === "history") { refreshChart(); refreshHeatmap(); }
});
openStream();
// Background charts still poll on their own slower cadence.
setInterval(() => {
  if (currentRouteName() === "history") refreshChart();
}, 30000);
