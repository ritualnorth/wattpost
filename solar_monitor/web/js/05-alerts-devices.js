// WattPost UI, part 05: alert banner, cells, device cards, tank and ambient sensors.
// Plain script (no modules): the numbered files in web/js/ are loaded in order by
// index.html and share one global scope, exactly as the old single app.js did.

// ---------- ALERTS ----------
// Friendly per-category short labels for the banner summary. Keep
// them tight, 1-2 words; the summary line ellipsises if too long.
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
  if (lastRun && !CLOUD_VIEW) {
    const age = Math.floor(Date.now() / 1000) - lastRun.ts;
    if (age > 300) push({ level: "alarm", category: "comms_alarm",
      lbl: "No recent poll", value: fmt.ago(lastRun.ts) });
    else if (age > 120) push({ level: "warn", category: "comms_stale",
      lbl: "Comms slow", value: `last ${fmt.ago(lastRun.ts)}` });
    if (lastRun.errors_count > 0) push({ level: "warn", category: "poll_errors",
      lbl: `${lastRun.errors_count} device error${lastRun.errors_count === 1 ? "" : "s"}`,
      value: "last poll" });
  } else if (!CLOUD_VIEW) {
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

  // Per-device alerts, tagged with device_label so groupable later.
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

  // Detail rows, built once per render. The banner stays collapsed
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

  // Hide the panel entirely if we have nothing to show, scenario B users
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
  // Delete is keyed by the device label (the DB key on the card), so a
  // stale "silent" device with no transport — left behind when a device
  // was removed from config — can still be cleared. transport + slaveId
  // are passed when known so the config entry is removed too; without
  // them we just purge the orphaned DB rows.
  if (!confirm(`Remove "${label}"? Polling stops immediately and its history is cleared; the device itself keeps running, this just removes it from the dashboard. You can re-add it via Setup → Scan.`)) {
    return;
  }
  const params = new URLSearchParams({ label });
  if (transport) params.set("transport", transport);
  if (Number.isInteger(slaveId)) params.set("slave_id", String(slaveId));
  try {
    const r = await fetch(`/api/setup/device?${params.toString()}`, { method: "DELETE" });
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

// ---- Tank + Ambient sensors panel (#257) ----
//
// Renders the Mopeka / Govee / Ruuvi readings as a separate panel
// rather than mixing them into the power flow tile. The flow tile
// is about energy in/out, sensors are environmental state, kept
// visually distinct. Panel auto-hides when no tank/ambient devices
// are paired so non-vanlife users never see it.
function renderSensors() {
  const panel = document.getElementById("panel-sensors");
  const grid  = document.getElementById("sensors-grid");
  const sub   = document.getElementById("sensors-summary");
  if (!panel || !grid) return;

  const sensors = devices.filter(
    (d) => d.kind === "tank" || d.kind === "ambient",
  );
  if (!sensors.length) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;

  const tanks    = sensors.filter((d) => d.kind === "tank").length;
  const ambients = sensors.filter((d) => d.kind === "ambient").length;
  const parts = [];
  if (tanks)    parts.push(`${tanks} tank${tanks === 1 ? "" : "s"}`);
  if (ambients) parts.push(`${ambients} ambient`);
  sub.textContent = parts.join(" · ");

  grid.innerHTML = sensors.map((d) => _renderSensorCard(d)).join("");
}

function _renderSensorCard(dev) {
  const latest = dev.latest || {};
  const age    = +latest.advertisement_age_s;
  const stale  = !isFinite(age) || age > 600;
  const safe   = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const label  = safe(dev.display_name || dev.label || dev.address || "Sensor");
  const kindL  = dev.kind === "tank" ? "Tank" : "Ambient";

  // Primary metric per kind: tank → distance to liquid (raw mm
  // until per-tank calibration ships in #257 follow-up); ambient
  // → temperature.
  let primary = "";
  let secondaryRows = [];
  if (dev.kind === "tank") {
    const dist = latest.raw_distance_mm;
    primary = (typeof dist === "number")
      ? `<strong>${Math.round(dist)}</strong><span>mm to liquid</span>`
      : `<strong>—</strong><span>no reading</span>`;
    const t = latest.temperature_c;
    const b = latest.battery_pct;
    const tilt = latest.tilted ? "tilted" : "level";
    secondaryRows.push(
      `<div>${typeof t === "number" ? t.toFixed(1) + "°C" : "—"} ambient</div>`,
      `<div>${typeof b === "number" ? Math.round(b) + "%" : "—"} battery · ${tilt}</div>`,
    );
  } else {
    const t = latest.temperature_c;
    const h = latest.humidity_pct;
    primary = (typeof t === "number")
      ? `<strong>${t.toFixed(1)}</strong><span>°C</span>`
      : `<strong>—</strong><span>°C</span>`;
    secondaryRows.push(
      `<div>${typeof h === "number" ? h.toFixed(0) + "% RH" : "— RH"}</div>`,
    );
    const b = latest.battery_pct;
    if (typeof b === "number") {
      secondaryRows.push(`<div>${Math.round(b)}% battery</div>`);
    }
  }

  const staleNote = stale
    ? `<div class="sensor-card-stale">no fresh advert, last seen ${
        isFinite(age) ? fmt.ago(Math.floor(Date.now() / 1000) - age) : "never"
      }</div>`
    : "";

  return `
    <div class="sensor-card">
      <div class="sensor-card-head">
        <div class="sensor-card-name">${label}</div>
        <div class="sensor-card-kind">${kindL}</div>
      </div>
      <div class="sensor-card-metric">${primary}</div>
      <div class="sensor-card-row">${secondaryRows.join("")}</div>
      ${staleNote}
    </div>
  `;
}

// Devices page supports two views: rich cards (default) and a compact
// Beszel-style one-row-per-device list. Choice persists across sessions.
let deviceView = (() => {
  try { return localStorage.getItem("wp_device_view") || "cards"; }
  catch { return "cards"; }
})();

function renderDevices() {
  const host = $("#device-cards");
  if (!host) return;
  host.className = deviceView === "list" ? "device-list" : "device-cards";
  renderDeviceViewToggle();
  if (deviceView === "list") renderDeviceList();
  else renderDeviceCards();
}

function renderDeviceViewToggle() {
  const host = document.getElementById("device-view-toggle");
  if (!host) return;
  const opt = (id, label, icon) =>
    `<button type="button" class="dv-seg ${deviceView === id ? "is-active" : ""}" data-device-view="${id}" title="${label} view" aria-pressed="${deviceView === id}">
       <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${icon}</svg>
       <span>${label}</span>
     </button>`;
  host.innerHTML =
    opt("cards", "Cards", '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>') +
    opt("list", "List", '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>');
  host.querySelectorAll("[data-device-view]").forEach(b =>
    b.addEventListener("click", () => {
      const v = b.dataset.deviceView;
      if (v === deviceView) return;
      deviceView = v;
      try { localStorage.setItem("wp_device_view", v); } catch {}
      renderDevices();
    }));
}

// Per-column accessors that normalise the differing field names across
// device kinds (bank/smart_battery/charge_controller/ac_charger) so the
// list table can show real columns. Smart batteries don't report soc_pct
// or power_w directly, so derive them (SoC from remaining/capacity, power
// from V×I) rather than leaving the columns blank.
function devSoc(l) {
  let s = l.soc_pct ?? l.battery_percentage;
  if ((s == null || isNaN(+s)) && l.remaining_charge_ah != null && +l.capacity_ah)
    s = (+l.remaining_charge_ah / +l.capacity_ah) * 100;
  if ((s == null || isNaN(+s)) && l.remaining_ah != null && +l.capacity_ah)
    s = (+l.remaining_ah / +l.capacity_ah) * 100;
  return (s == null || isNaN(+s)) ? null : Math.max(0, Math.min(100, +s));
}
function devVolt(l) {
  // Include charger/inverter output rails (e.g. Victron AC charger reports
  // output_1_voltage_v, not battery_voltage_v) so the list shows V/A/W for
  // non-battery devices too.
  const v = l.voltage_v ?? l.battery_voltage_v ?? l.output_1_voltage_v ?? l.ac_output_voltage_v;
  return (v == null || isNaN(+v)) ? null : +v;
}
function devCurr(l) {
  const a = l.current_a ?? l.battery_current_a ?? l.output_1_current_a ?? l.ac_output_current_a;
  return (a == null || isNaN(+a)) ? null : +a;
}
function devPower(l) {
  // Charge controllers/loads use power_w/pv_power_w; AC chargers + DC-DC
  // surface their delivered power as output_(1_)power_w / ac_output_power_w.
  for (const k of ["power_w", "pv_power_w", "output_1_power_w", "output_power_w", "ac_output_power_w"]) {
    if (l[k] != null && !isNaN(+l[k])) return +l[k];
  }
  const v = devVolt(l), a = devCurr(l);
  if (v != null && a != null) return v * a;
  return null;
}
function devStatus(l) { return l.charging_state || l.mode || l.state || null; }

function renderDeviceList() {
  const host = $("#device-cards");
  const visible = [...devices].sort((a, b) =>
    a.kind === "bank" ? -1 : b.kind === "bank" ? 1 : 0);

  const numCell = (v, unit, cls = "") =>
    `<td class="dt-num ${cls}">${v == null ? '<span class="dt-na">—</span>' : `${fmt.num(v)}<span class="dt-u">${unit}</span>`}</td>`;

  const rows = visible.map(dev => {
    const l = dev.latest || {};
    const soc = devSoc(l);
    const status = devStatus(l);
    const ageS = (typeof l.advertisement_age_s === "number") ? l.advertisement_age_s : null;
    const silent = ageS != null && ageS > 60;
    let socCell = `<td class="dt-soc"><span class="dt-na">—</span></td>`;
    if (soc != null) {
      const lvl = soc < 20 ? "lvl-low" : soc < 40 ? "lvl-mid" : "lvl-ok";
      socCell = `<td class="dt-soc"><span class="dt-soc-wrap">
        <span class="dev-row-soc"><span class="dev-row-soc-fill ${lvl}" style="width:${soc.toFixed(0)}%"></span></span>
        <span class="dt-soc-pct">${soc.toFixed(0)}%</span></span></td>`;
    }
    const statusCell = status
      ? `<td class="dt-status dt-sec"><span class="dev-row-status"><span class="dev-row-dot"></span>${escHtml(String(status))}</span></td>`
      : `<td class="dt-status dt-sec"><span class="dt-na">—</span></td>`;
    return `<tr data-dev="${escHtml(dev.label)}" class="${silent ? "dev-row-silent" : ""}">
      <td class="dt-name"><span class="dev-row-ico">${ICONS[KIND_ICON[dev.kind] || "unknown"] || ICONS.unknown}</span><span>${escHtml(dispName(dev))}</span></td>
      ${socCell}
      ${numCell(devPower(l), " W")}
      ${numCell(devVolt(l), " V", "dt-sec")}
      ${numCell(devCurr(l), " A", "dt-sec")}
      ${statusCell}
      <td class="dt-chev"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 6 15 12 9 18"/></svg></td>
    </tr>`;
  }).join("");

  host.innerHTML = `<table class="dev-table">
    <thead><tr>
      <th class="dt-name">Device</th>
      <th class="dt-soc">SoC</th>
      <th class="dt-num">Power</th>
      <th class="dt-num dt-sec">Voltage</th>
      <th class="dt-num dt-sec">Current</th>
      <th class="dt-status dt-sec">Status</th>
      <th class="dt-chev"></th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
  host.querySelectorAll("tr[data-dev]").forEach(tr =>
    tr.addEventListener("click", () => {
      location.hash = `#/device/${encodeURIComponent(tr.dataset.dev)}`;
    }));
}

function renderDeviceCards() {
  const host = $("#device-cards");
  host.innerHTML = "";
  // Bank pinned to the top, it's the headline "what's actually in
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
    // Bank is a synthetic aggregate, there's nothing on disk to
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
      silent.textContent = `Silent, last heard ${ageStr} ago`;
      card.appendChild(silent);
    }

    // Lifetime stats strip for smart batteries, cycles + Ah throughput.
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

    // SoC gauge (Beszel-style): a thin coloured fill under the header on
    // any card that reports state of charge.
    const socRaw = l.soc_pct ?? l.battery_percentage;
    if (socRaw != null && !isNaN(+socRaw)) {
      const pct = Math.max(0, Math.min(100, +socRaw));
      const lvl = pct < 20 ? "lvl-low" : pct < 40 ? "lvl-mid" : "lvl-ok";
      const bar = document.createElement("div");
      bar.className = "dev-card-soc";
      bar.innerHTML = `<div class="dev-card-soc-fill ${lvl}" style="width:${pct.toFixed(0)}%"></div>`;
      card.appendChild(bar);
    }

    for (const k of headlineKeys(dev.kind, l)) {
      const v = l[k];
      if (v === undefined || v === null) continue;
      const row = document.createElement("div");
      row.className = "dev-card-row";
      const left = document.createElement("span");
      left.className = "dev-card-row-left";
      const ico = document.createElement("span");
      ico.className = "dev-card-row-ico";
      ico.innerHTML = metricIcon(k);
      const ke = document.createElement("span");
      ke.className = "k";
      ke.textContent = prettyKey(k);
      left.append(ico, ke);
      const ve = document.createElement("span");
      ve.className = "v";
      const unit = unitFromKey(k);
      ve.textContent = typeof v === "number"
        ? `${fmt.num(v)}${unit ? " " + unit : ""}`
        : String(v);
      row.append(left, ve);
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

// A small line icon per metric (Beszel-style), keyed loosely off the
// metric name. Muted by CSS so it reads as a quiet glyph, not clutter.
function metricIcon(k) {
  const m = (k || "").toLowerCase();
  const I = (p) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${p}</svg>`;
  if (/soc|percent|remaining|capacity|charge_ah|^bank/.test(m))
    return I('<rect x="2" y="7" width="16" height="10" rx="2"/><line x1="22" y1="11" x2="22" y2="13"/>');
  if (/pv|solar|irradiance/.test(m))
    return I('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4 12H2M22 12h-2M5 5l1.4 1.4M17.6 17.6 19 19M19 5l-1.4 1.4M6.4 17.6 5 19"/>');
  if (/power|energy|watt|_w$|_wh$/.test(m))
    return I('<path d="M13 2 3 14h8l-2 8 10-12h-8l2-8z"/>');
  if (/volt|_v$/.test(m))
    return I('<path d="M9 2v6M15 2v6M5 8h14v3a7 7 0 0 1-14 0V8zM12 18v4"/>');
  if (/current|amp|_a$/.test(m))
    return I('<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>');
  if (/temp/.test(m))
    return I('<path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4 4 0 1 0 5 0z"/>');
  if (/state|status|mode|type|serial|drift|pack/.test(m))
    return I('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>');
  return I('<circle cx="12" cy="12" r="3"/>');
}

// Which metrics show on the device CARD (the rest are one tap away under
// "View detail"). Curated + capped per kind so the card stays a tight
// glance, not a full dump — the bank used to spill 11 rows.
function headlineKeys(kind, l) {
  const present = (keys) => keys.filter(k => l[k] !== undefined && l[k] !== null);
  if (kind === "bank") {
    const cur = present(["soc_pct", "power_w", "voltage_v", "current_a",
                         "remaining_ah", "capacity_ah"]);
    if (cur.length >= 3) return cur;
  } else if (kind === "charge_controller") {
    const cur = present(["charging_state", "battery_voltage_v", "battery_current_a",
                         "battery_percentage", "pv_power_w", "energy_today_wh"]);
    if (cur.length >= 3) return cur;
  } else if (kind === "smart_battery") {
    const cur = present(["soc_pct", "voltage_v", "current_a",
                         "remaining_charge_ah", "capacity_ah"]);
    if (cur.length >= 3) return cur;
  }
  // Unknown / sparse kind: show the highest-priority generic metrics, capped.
  const PRIORITY = ["soc_pct", "power_w", "voltage_v", "current_a", "remaining_ah",
                    "capacity_ah", "output_power_w", "pv_power_w", "load_w",
                    "temperature_c", "state", "charging_state"];
  const keys = Object.keys(l).filter(k => !k.startsWith("_"));
  keys.sort((a, b) => {
    const ia = PRIORITY.indexOf(a), ib = PRIORITY.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  return keys.slice(0, 6);
}
