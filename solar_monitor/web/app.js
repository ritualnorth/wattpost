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

async function refresh() {
  try {
    const [devs, run, today] = await Promise.all([
      api("/api/devices"),
      api("/api/poll_run"),
      api("/api/today").catch(() => null),
    ]);
    devices = devs.devices || [];
    lastRun = run.last_run;
    todayAggregate = today;
    renderStatus(run);
    renderHero();
    renderFlow();
    renderToday();
    renderCells();
    renderDeviceCards();
    populateChartSelectors();
    renderAlerts();
    $("#devices-meta").textContent = lastRun
      ? `${devices.length} devices · last poll ${lastRun.elapsed_ms} ms · ${fmt.ago(lastRun.ts)}`
      : "";
  } catch (e) {
    setStatus("err", "API error: " + e.message);
  }
}

function renderStatus(run) {
  if (!run.last_run) { setStatus("warn", "no poll yet"); return; }
  const lr = run.last_run;
  const cls = lr.errors_count > 0 ? "warn" : (run.scheduler_running ? "ok" : "err");
  const parts = [`${lr.devices_ok} device${lr.devices_ok === 1 ? "" : "s"}`,
                 `${lr.elapsed_ms} ms`, fmt.ago(lr.ts)];
  if (lr.errors_count) parts.push(`${lr.errors_count} err`);
  setStatus(cls, parts.join(" · "));
}

// ---------- BANK AGGREGATE ----------
function aggregateBank() {
  const batts = devices.filter(d => d.kind === "smart_battery");
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
  const netW = meanV * sumI;   // V × A across the bank
  return {
    packs: batts.length, model: batts[0]?.latest?.model || "battery",
    soc, meanV, sumI, netW, totalCap, totalRem,
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
  const donutWrap = $("#donut-wrap");
  if (donutWrap) {
    donutWrap.classList.remove("charging", "discharging", "idle");
    donutWrap.classList.add(powerState);
    const flowText = $("#donut-flow .donut-flow-text");
    if (flowText) {
      flowText.textContent = powerState === "idle"
        ? "Idle"
        : `${fmt.signed(bank.netW, 0)} W`;
    }
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
    for (const s of mapping.sources || []) {
      if (s.onlyIf && !s.onlyIf(l)) continue;
      const w = +l[s.metric] || 0;
      if (w <= 0 && !s.onlyIf) continue;
      sources.push({
        id: `${dev.label}.${s.id}`,
        label: s.label,
        device: dev.label,
        color: s.color,
        icon: s.icon || COLOR_TO_ICON[s.color],
        power: w,
        active: w > 1,
        sub: [
          typeof l[s.vMetric] === "number" ? `${l[s.vMetric].toFixed(1)} V` : null,
          typeof l[s.aMetric] === "number" ? `${l[s.aMetric].toFixed(2)} A` : null,
        ].filter(Boolean).join(" · "),
      });
    }
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
  // Threshold below which we ignore noise / BMS rounding.
  const INFERRED_THRESHOLD_W = 10;
  if (bank) {
    const visibleSourcesW = sources.reduce((a, s) => a + s.power, 0);
    const visibleLoadsW   = loads.reduce((a, l) => a + l.power, 0);
    const inferred = visibleSourcesW - bank.netW - visibleLoadsW;

    // Loads almost never go through the charge controller's load
     // terminals — they're wired to a busbar. So the "balance" calculation
     // is in fact the real consumption, not a fallback. Promote it to the
     // primary Load tile and drop the dashed-border treatment.
     //
     // If the controller's load output IS active (rare — usually a small
     // LED or fan), we leave its existing tile in place and only the
     // *additional* load coming via the bus is surfaced as a balance number.
    if (inferred > INFERRED_THRESHOLD_W) {
      const visibleLoadsActive = loads.length > 0;
      loads.push({
        id: visibleLoadsActive ? "_bus_load" : "_load",
        label: visibleLoadsActive ? "Bus loads" : "Load",
        color: "dc",
        icon: "house",
        power: inferred,
        active: inferred > 50,
        // Only flag as "inferred" (dashed border + asterisk) when it's a
        // secondary tile alongside something we measured directly.
        inferred: visibleLoadsActive,
        sub: visibleLoadsActive ? "from energy balance" : null,
      });
    } else if (inferred < -INFERRED_THRESHOLD_W) {
      const visibleSourcesActive = sources.length > 0;
      sources.push({
        id: visibleSourcesActive ? "_bus_source" : "_source",
        label: visibleSourcesActive ? "Other source" : "Source",
        color: "grid",
        icon: "feed",
        power: -inferred,
        active: -inferred > 50,
        inferred: visibleSourcesActive,
        sub: visibleSourcesActive ? "from energy balance" : null,
      });
    }
  }

  return { sources, loads, batteryNetW, bank };
}

function renderFlow() {
  const host = $("#flow");
  const sub  = $("#flow-sub");
  host.innerHTML = "";

  const model = buildFlowModel();
  if (!model.bank && model.sources.length === 0 && model.loads.length === 0) {
    host.innerHTML = `<div class="flow-empty">No active devices yet.</div>`;
    sub.textContent = "";
    return;
  }

  // ----- Sources column -----
  const sourcesCol = document.createElement("div");
  sourcesCol.className = "flow-col flow-sources";
  if (model.sources.length === 0) {
    sourcesCol.appendChild(makeFlowTile({ label: "No source", color: "neutral", power: 0, sub: "—" }, true));
  } else {
    for (const s of model.sources) sourcesCol.appendChild(makeFlowTile(s));
  }

  // ----- Connector: sources → battery -----
  const totalSourceW = model.sources.reduce((a, s) => a + s.power, 0);
  const connIn = makeConnector({
    label: `${totalSourceW.toFixed(0)} W`,
    fromColor: model.sources[0]?.color || "neutral",
    toColor: "batt",
    active: totalSourceW > 1,
  });

  // ----- Battery tile -----
  const battCol = document.createElement("div");
  battCol.className = "flow-col";
  if (model.bank) {
    const b = model.bank;
    const battTile = makeFlowTile({
      label: "Battery bank",
      color: "batt",
      icon: "battery",
      power: b.netW,
      signed: true,
      active: Math.abs(b.netW) > 1,
      sub: `${b.soc.toFixed(1)} % · ${b.meanV.toFixed(2)} V`,
    });
    battCol.appendChild(battTile);
  } else {
    battCol.appendChild(makeFlowTile({ label: "Battery", color: "neutral", icon: "battery", power: 0, sub: "—" }, true));
  }

  // ----- Connector: battery → loads -----
  const totalLoadW = model.loads.reduce((a, l) => a + l.power, 0);
  const connOut = makeConnector({
    label: `${totalLoadW.toFixed(0)} W`,
    fromColor: "batt",
    toColor: model.loads[0]?.color || "neutral",
    active: totalLoadW > 1,
  });

  // ----- Loads column -----
  const loadsCol = document.createElement("div");
  loadsCol.className = "flow-col flow-loads";
  if (model.loads.length === 0) {
    loadsCol.appendChild(makeFlowTile({ label: "No active load", color: "neutral", power: 0, sub: "—" }, true));
  } else {
    for (const lo of model.loads) loadsCol.appendChild(makeFlowTile(lo));
  }

  host.append(sourcesCol, connIn, battCol, connOut, loadsCol);

  // Sub-header summary
  sub.textContent = `${model.sources.length} source${model.sources.length === 1 ? "" : "s"} · ` +
                    `${model.loads.length} load${model.loads.length === 1 ? "" : "s"} · ` +
                    `${totalSourceW.toFixed(0)} W in · ${totalLoadW.toFixed(0)} W out`;
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

  if (batteries.length === 0) {
    grid.innerHTML = `<div class="dev-card-sub">no batteries yet</div>`;
    return;
  }

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

// ---------- device cards ----------
function renderDeviceCards() {
  const host = $("#device-cards");
  host.innerHTML = "";
  for (const dev of devices) {
    const l = dev.latest || {};
    const card = document.createElement("div");
    card.className = `dev-card kind-${dev.kind}`;

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
    const slave = document.createElement("div");
    slave.className = "dev-card-slave";
    slave.textContent = `slave ${dev.slave_id}`;
    head.append(left, slave);
    card.appendChild(head);

    const sub = document.createElement("div");
    sub.className = "dev-card-sub";
    const fw = l.firmware_version || l.firmware_version_raw || "";
    sub.textContent = `${dev.vendor} · ${dev.kind}${fw ? " · fw " + fw : ""}${l.model ? " · " + l.model : ""}`;
    card.appendChild(sub);

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
  const mSel = $("#sel-metric");
  const prevDevice = dSel.value;
  const prevMetric = mSel.value;
  dSel.innerHTML = "";
  for (const d of devices) {
    const opt = document.createElement("option");
    opt.value = d.label; opt.textContent = d.label;
    dSel.appendChild(opt);
  }
  if (prevDevice && devices.find(d => d.label === prevDevice)) dSel.value = prevDevice;
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
  else if (dev?.kind === "charge_controller" && numericMetrics.includes("pv_power_w")) mSel.value = "pv_power_w";
  else if (dev?.kind === "smart_battery" && numericMetrics.includes("voltage_v")) mSel.value = "voltage_v";
  refreshChart();
}

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

async function refreshChart() {
  const label = $("#sel-device").value;
  const metric = $("#sel-metric").value;
  if (!label || !metric) return;
  const [since, bucket] = sinceForRange(currentRange);
  let data;
  try {
    data = await api(`/api/devices/${encodeURIComponent(label)}/history?metric=${encodeURIComponent(metric)}&since=${since}&bucket=${bucket}`);
  } catch (e) { console.error(e); return; }
  drawChart(label, metric, data);
}

function drawChart(label, metric, data) {
  const root = $("#chart");
  if (chart) { chart.destroy(); chart = null; }
  const unit = unitFromKey(metric);
  const width = Math.max(root.clientWidth, 320);
  const opts = {
    width, height: 340,
    scales: { x: { time: true } },
    series: [
      {},
      {
        label: `${prettyKey(metric)}${unit ? " (" + unit + ")" : ""}`,
        stroke: "#58a6ff",
        width: 1.7,
        fill: "rgba(88,166,255,0.12)",
        value: (u, v) => v == null ? "—" : v.toFixed(2) + (unit ? " " + unit : ""),
      },
    ],
    axes: [
      { stroke: "#9aa6b8", grid: { stroke: "rgba(106,118,137,0.12)" }, ticks: { stroke: "rgba(106,118,137,0.2)" } },
      { stroke: "#9aa6b8", grid: { stroke: "rgba(106,118,137,0.12)" }, ticks: { stroke: "rgba(106,118,137,0.2)" } },
    ],
  };
  try {
    chart = new uPlot(opts, [data.ts, data.values], root);
  } catch (e) {
    console.error("uPlot failed:", e, { data, width, root });
    root.innerHTML = `<div style="padding:1rem;color:var(--red)">Chart render failed: ${e.message}</div>`;
  }
}

// ---------- wiring ----------
$("#sel-device").addEventListener("change", () => onDeviceChanged());
$("#sel-metric").addEventListener("change", refreshChart);
for (const btn of document.querySelectorAll("[data-range]")) {
  btn.addEventListener("click", () => {
    document.querySelectorAll("[data-range]").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentRange = btn.dataset.range;
    refreshChart();
  });
}
window.addEventListener("resize", () => {
  if (chart) chart.setSize({ width: $("#chart").clientWidth, height: 340 });
});

// boot
refresh();
setInterval(refresh, 5000);
setInterval(refreshChart, 30000);
