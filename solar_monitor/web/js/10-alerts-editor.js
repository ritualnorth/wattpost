// WattPost UI, part 10: alert rules editor.
// Plain script (no modules): the numbered files in web/js/ are loaded in order by
// index.html and share one global scope, exactly as the old single app.js did.

// ---------- alerts panel (full editor) ----------
const ALERT_OP_LABEL = { lt: "<", lte: "≤", gt: ">", gte: "≥", eq: "=", neq: "≠" };
// Common metric paths the user can pick without typing. Anything else
// works too, the field falls back to a free-text input.
const METRIC_SUGGESTIONS = [
  // Bank-level metrics, the everyday "is the battery OK" stuff.
  { value: "bank.soc_pct",              label: "Battery SoC (%)" },
  { value: "bank.netW",                 label: "Bank net power (W)" },
  { value: "bank.meanV",                label: "Bank voltage (V)" },
  { value: "bank.totalRem",             label: "Bank remaining (Ah)" },
  { value: "bank.totalCap",             label: "Bank capacity (Ah)" },
  { value: "bank.time_to_go_minutes",   label: "Time to empty (minutes, shunt-only)" },
  // Cell-balance metrics (BMS-sourced), early-warning for pack health.
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
// `short` + `cond` drive the two-line template tile (name on top, the
// threshold in mono underneath); `label` is the full description, kept
// for the tile's tooltip/aria.
const ALERT_TEMPLATES = [
  { id: "low_soc",
    short: "Low SoC", cond: "< 30%", label: "Low SoC (< 30%)",
    rule: { name: "Low battery", metric: "bank.soc_pct", op: "lt",
            threshold: 30, severity: "warn", cooldown_seconds: 3600 } },
  { id: "critical_soc",
    short: "Critical SoC", cond: "< 15%", label: "Critical SoC (< 15%)",
    rule: { name: "Critical battery", metric: "bank.soc_pct", op: "lt",
            threshold: 15, severity: "alarm", cooldown_seconds: 900 } },
  { id: "low_v_12v",
    short: "Low voltage", cond: "< 11.5 V", label: "Low voltage (< 11.5 V, 12 V system)",
    rule: { name: "Low voltage", metric: "bank.meanV", op: "lt",
            threshold: 11.5, severity: "alarm", cooldown_seconds: 600 } },
  { id: "high_temp",
    short: "Bank over-temp", cond: "> 50 °C", label: "Bank over-temp (> 50 °C)",
    rule: { name: "Battery over-temperature", metric: "devices.charge_controller.battery_temperature_c",
            op: "gt", threshold: 50, severity: "alarm", cooldown_seconds: 600 } },
  { id: "cell_drift_warn",
    short: "Cell drift warn", cond: "> 100 mV", label: "Cell drift warning (> 100 mV)",
    rule: { name: "Cell drift warning", metric: "bank.worst_pack_drift_v",
            op: "gt", threshold: 0.10, severity: "warn", cooldown_seconds: 21600 } },
  { id: "cell_drift_alarm",
    short: "Cell drift alarm", cond: "> 200 mV", label: "Cell drift alarm (> 200 mV)",
    rule: { name: "Cell drift alarm", metric: "bank.worst_pack_drift_v",
            op: "gt", threshold: 0.20, severity: "alarm", cooldown_seconds: 3600 } },
  { id: "soc_disagreement",
    short: "Shunt vs BMS", cond: "> 10%", label: "Shunt-vs-BMS disagree (> 10%)",
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
  html += `<div class="alerts-sub-head"><h2>Alert rules</h2>
    <button class="alerts-add-btn" data-add="rule">+ Add rule</button></div>`;

  // One-tap templates row, pre-fills the add-rule form with sensible
  // thresholds for the most common alarms. Hidden when the form is
  // already open so we don't clutter the editing flow.
  if (!(alertsState.editing?.type === "rule" && alertsState.editing.mode === "add")) {
    html += `<div class="alerts-templates">
      <span class="alerts-templates-head">Start from a template</span>
      <div class="alerts-template-grid">
        ${ALERT_TEMPLATES.map(t => `
          <button class="alerts-template-chip" data-alert-template="${t.id}" title="${escHtml(t.label)}">
            <span class="tmpl-name">${escHtml(t.short)}</span>
            <span class="tmpl-cond">${escHtml(t.cond)}</span>
          </button>
        `).join("")}
      </div>
    </div>`;
  }

  if (alertsState.editing?.type === "rule" && alertsState.editing.mode === "add") {
    // A `prefill` on the editing state means the user clicked a
    // quick-template chip, pass the template rule into the form
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

  html += `<div class="alerts-sub-head"><h2>Transports</h2>
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

// Sensible slider bounds inferred from the metric name, so the threshold
// drag feels right whether it's a 0–100 % SoC or a 0–5 kW load. The number
// box is always there for precise entry, and the range stretches to fit a
// threshold that sits outside the inferred bounds.
function metricRange(metric, threshold) {
  const m = (metric || "").toLowerCase();
  let min = 0, max = 100, step = 1;
  if (/soc|pct|percent/.test(m))         { min = 0;    max = 100;  step = 1; }
  else if (/temp/.test(m))               { min = -20;  max = 90;   step = 1; }
  else if (/drift/.test(m))              { min = 0;    max = 1;    step = 0.01; }
  else if (/_v$|volt/.test(m))           { min = 0;    max = 60;   step = 0.1; }
  else if (/_a$|current|amp/.test(m))    { min = -200; max = 200;  step = 1; }
  else if (/_w$|power|watt|load/.test(m)){ min = 0;    max = 5000; step = 25; }
  else { max = Math.max(100, Math.ceil((+threshold || 0) * 2)); }
  const t = +threshold || 0;
  if (t < min) min = t;
  if (t > max) max = t;
  return { min, max, step };
}

// PUT the rule with a partial change merged over its current fields.
// Optimistic: the caller has usually already updated `r` + the DOM.
async function patchRule(r, changes) {
  const body = {
    id: r.id, name: r.name, metric: r.metric, op: r.op,
    threshold: r.threshold, severity: r.severity,
    cooldown_seconds: r.cooldown_seconds, transports: r.transports || [],
    enabled: r.enabled !== false, ...changes,
  };
  try {
    const resp = await fetch(`/api/alerts/rules/${encodeURIComponent(r.id)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${resp.status}`);
    }
    Object.assign(r, changes);
  } catch (e) {
    if (window.console) console.warn("rule save failed", e);
    refreshAlertsPanel();   // reload to reset the UI to server truth
  }
}

const _BELL_ICO = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>`;
const _MOON_ICO = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>`;
const _ICO = {
  send:  `<path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4z"/>`,
  msg:   `<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>`,
  link:  `<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>`,
  mail:  `<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>`,
  cast:  `<path d="M2 16.1A5 5 0 0 1 5.9 20M2 12.05A9 9 0 0 1 9.95 20M2 20h.01"/><path d="M2 8V6a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-6"/>`,
  sun:   `<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>`,
  cloud: `<path d="M17.5 19a4.5 4.5 0 0 0 .5-9 6 6 0 0 0-11.6 1.5A4 4 0 0 0 6.5 19z"/>`,
  graph: `<path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/>`,
  // lucide icons for the settings section nav (replacing emoji)
  plug:  `<path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/>`,
  bell:  `<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>`,
  puzzle:`<path d="M15.39 4.39a1 1 0 0 0 1.68-.474 2.5 2.5 0 1 1 3.014 3.015 1 1 0 0 0-.474 1.68l1.683 1.682a2.414 2.414 0 0 1 0 3.414L19.61 19.39a1 1 0 0 1-1.68-.474 2.5 2.5 0 1 0-3.014 3.015 1 1 0 0 1 .474 1.68l-1.683 1.682"/><path d="M2.61 8.61a2.414 2.414 0 0 0 0 3.414L4.293 13.7a1 1 0 0 0 1.68-.474 2.5 2.5 0 1 1 3.014 3.015 1 1 0 0 0-.474 1.68l1.683 1.682"/>`,
  drive: `<line x1="22" x2="2" y1="12" y2="12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" x2="6.01" y1="16" y2="16"/><line x1="10" x2="10.01" y1="16" y2="16"/>`,
  lock:  `<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>`,
  sliders:`<line x1="21" x2="14" y1="4" y2="4"/><line x1="10" x2="3" y1="4" y2="4"/><line x1="21" x2="12" y1="12" y2="12"/><line x1="8" x2="3" y1="12" y2="12"/><line x1="21" x2="16" y1="20" y2="20"/><line x1="12" x2="3" y1="20" y2="20"/><line x1="14" x2="14" y1="2" y2="6"/><line x1="8" x2="8" y1="10" y2="14"/><line x1="16" x2="16" y1="18" y2="22"/>`,
  info:  `<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>`,
};
function _ico(name) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${_ICO[name] || _ICO.send}</svg>`;
}
// Per-transport-type glyph for the card icon chip.
function transportIcon(type) {
  return _ico(({
    ntfy: "send", discord_webhook: "msg", webhook: "link",
    smtp: "mail", mqtt: "cast", pushover: "send",
  })[type] || "send");
}
// Per-integration glyph for the card icon chip.
function integrationIcon(id) {
  return _ico(({
    solcast: "sun", openmeteo: "cloud", cloud: "cloud",
    mqtt: "cast", prometheus: "graph",
  })[id] || "link");
}

// Beszel-style alert card: header (icon + name + condition + arm toggle);
// when armed it reveals the threshold slider + number and a meta/action
// row. Disarmed rules collapse to the header + a compact edit/delete line.
function renderRuleRow(r, transportIds) {
  const opLbl = ALERT_OP_LABEL[r.op] || r.op;
  const on = r.enabled !== false;
  const cond = `${r.metric} ${opLbl} ${r.threshold}`;
  const lastFired = r.last_fired_ts ? `fired ${fmt.ago(r.last_fired_ts)}` : "never fired";
  const cdMin = Math.round((r.cooldown_seconds || 0) / 60);
  const tmissing = (r.transports || []).some(t => !transportIds.includes(t));
  const tlist = (r.transports || []).join(", ") || "⚠ none";
  const rng = metricRange(r.metric, r.threshold);
  const fill = Math.max(0, Math.min(100,
    Math.round(((+r.threshold - rng.min) / (rng.max - rng.min || 1)) * 100)));
  const reveal = on ? `
      <div class="ar-controls">
        <div class="ar-ctl-label" data-rule-cond="${r.id}">Triggers when <b>${escHtml(r.metric)} ${opLbl} ${r.threshold}</b></div>
        <div class="ar-ctl-grid">
          <input type="range" class="ar-slider" data-rule-threshold="${r.id}" aria-label="${escHtml(r.name || r.metric)} threshold"
                 min="${rng.min}" max="${rng.max}" step="${rng.step}" value="${r.threshold}" style="--fill:${fill}%">
          <input type="number" class="ar-num" data-rule-threshold-num="${r.id}" aria-label="${escHtml(r.name || r.metric)} threshold value" step="${rng.step}" value="${r.threshold}">
        </div>
        <div class="ar-meta">
          <span class="ar-tag ar-tag--${r.severity}">${r.severity}</span>
          <span class="ar-tag">→ ${escHtml(tlist)}${tmissing ? " ⚠" : ""}</span>
          <span class="ar-tag">repeat ≤ ${cdMin}m</span>
          <span class="ar-tag">${lastFired}</span>
        </div>
      </div>` : "";
  return `
    <div class="ar-card ${on ? "is-on" : ""}" data-rule="${r.id}">
      <div class="ar-head">
        <div class="ar-ico">${_BELL_ICO}</div>
        <div class="ar-head-body">
          <div class="ar-title">${escHtml(r.name)}</div>
          <div class="ar-desc">${escHtml(cond)}${on ? "" : " · disarmed"}</div>
        </div>
        <label class="ar-switch" title="${on ? "Disarm" : "Arm"}">
          <input type="checkbox" data-rule-toggle="${r.id}" ${on ? "checked" : ""}>
          <span class="ar-track"></span>
        </label>
      </div>
      ${reveal}
      <div class="ar-foot">
        <button class="ar-link" data-test-rule="${r.id}">Test</button>
        <button class="ar-link" data-edit-rule="${r.id}">Edit</button>
        <button class="ar-link ar-link--danger" data-delete-rule="${r.id}">Delete</button>
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
  const enabled = r ? (r.enabled !== false) : true;
  const isCustom = !METRIC_SUGGESTIONS.some(m => m.value === metric);
  return `
    <form class="ar-card ar-card--edit ar-form" data-form="rule" data-original-id="${id}">
      <div class="ar-form-head">
        <div class="ar-form-title">${editing ? "Edit rule" : "New rule"}</div>
        <label class="ar-switch" title="Armed">
          <input type="checkbox" name="enabled" ${enabled ? "checked" : ""}>
          <span class="ar-track"></span>
        </label>
      </div>
      <div class="ar-form-grid">
        <label class="ar-field"><span>Name</span>
          <input type="text" name="name" value="${escHtml(name)}" required placeholder="Battery low"/></label>
        <label class="ar-field"><span>Metric</span>
          <select name="metric">
            ${METRIC_SUGGESTIONS.map(m =>
              `<option value="${escHtml(m.value)}" ${m.value === metric ? "selected" : ""}>${escHtml(m.label)}</option>`).join("")}
            <option value="__custom__" ${isCustom ? "selected" : ""}>Custom…</option>
          </select>
          <input type="text" name="metric_custom" class="ar-field-custom" value="${isCustom ? escHtml(metric) : ""}" placeholder="devices.battery_0.cell_drift_v" ${isCustom ? "" : "hidden"}/></label>
        <label class="ar-field ar-field--sm"><span>Condition</span>
          <select name="op">
            ${Object.keys(ALERT_OP_LABEL).map(o =>
              `<option value="${o}" ${o === op ? "selected" : ""}>${ALERT_OP_LABEL[o]}&nbsp;&nbsp;${o}</option>`).join("")}
          </select></label>
        <label class="ar-field ar-field--sm"><span>Threshold</span>
          <input type="number" name="threshold" value="${threshold}" step="any" required/></label>
        <label class="ar-field ar-field--sm"><span>Severity</span>
          <select name="severity">
            <option value="warn"  ${severity === "warn" ? "selected" : ""}>Warn</option>
            <option value="alarm" ${severity === "alarm" ? "selected" : ""}>Alarm</option>
          </select></label>
        <label class="ar-field ar-field--sm"><span>Repeat ≤ (min)</span>
          <input type="number" name="cooldown_min" value="${cooldownMin}" min="0" step="1" required/></label>
        ${editing
          ? `<input type="hidden" name="id" value="${escHtml(id)}"/>`
          : `<label class="ar-field"><span>ID</span>
               <input type="text" name="id" value="${escHtml(id)}" pattern="[a-zA-Z0-9_\\-]+" required placeholder="batt_low"/></label>`}
      </div>
      <div class="ar-form-section">
        <div class="ar-form-section-label">Send via</div>
        <div class="ar-chips">
          ${transportIds.length === 0
            ? `<span class="ar-muted">No transports yet — add one below first.</span>`
            : transportIds.map(tid =>
                `<label class="ar-chip"><input type="checkbox" name="transport" value="${escHtml(tid)}" ${ts.has(tid) ? "checked" : ""}/>${escHtml(tid)}</label>`).join("")}
        </div>
      </div>
      <div class="ar-form-actions">
        <button type="submit" class="ar-btn ar-btn--primary">${editing ? "Save changes" : "Create rule"}</button>
        <button type="button" class="ar-btn" data-cancel-edit>Cancel</button>
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
      <h2>Quiet hours</h2>
      <button class="alerts-add-btn" data-edit-quiet>${qh ? "Edit" : "Configure"}</button>
    </div>
    <div class="ar-card${qh ? " is-on" : ""}">
      <div class="ar-head">
        <div class="ar-ico">${_MOON_ICO}</div>
        <div class="ar-head-body">
          <div class="ar-title">${qh ? "Enabled" : "Disabled"}</div>
          <div class="ar-desc ar-desc--prose">${summary}</div>
        </div>
      </div>
      <div class="ar-meta">
        <span class="ar-tag">alarm severity always pages through</span>
      </div>
      <div class="ar-foot">
        <button class="ar-link" data-edit-quiet>${qh ? "Edit" : "Configure"}</button>
      </div>
    </div>`;
}

function pad2(n) { return String(n).padStart(2, "0"); }

function renderQuietHoursForm(qh) {
  const enabled = !!qh;
  const start = qh?.start_hour ?? 22;
  const end   = qh?.end_hour   ?? 7;
  return `
    <div class="alerts-sub-head"><h2>Quiet hours</h2></div>
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
    ? `<span class="ar-tag ar-tag--ok">active</span>`
    : `<span class="ar-tag ar-tag--warn">restart pending</span>`;
  return `
    <div class="ar-card is-on" data-transport="${t.id}">
      <div class="ar-head">
        <div class="ar-ico">${transportIcon(t.type)}</div>
        <div class="ar-head-body">
          <div class="ar-title">${escHtml(t.id)}</div>
          <div class="ar-desc">${escHtml(t.type)}${keyValue ? ` · ${escHtml(keyField)}: ${escHtml(String(keyValue))}` : ""}</div>
        </div>
      </div>
      <div class="ar-meta">${aliveTag}</div>
      <div class="ar-foot">
        <button class="ar-link" data-edit-transport="${t.id}">Edit</button>
        <button class="ar-link ar-link--danger" data-delete-transport="${t.id}">Delete</button>
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
  // Quick-template chips, open the add-rule form pre-filled with the
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
  // Arm/disarm toggle — optimistic flip + reveal/collapse, then persist.
  host.querySelectorAll("[data-rule-toggle]").forEach(cb => {
    cb.addEventListener("change", () => {
      const r = alertsState.rules.find(x => x.id === cb.dataset.ruleToggle);
      if (!r) return;
      r.enabled = cb.checked;
      renderAlertsPanel();
      patchRule(r, { enabled: cb.checked });
    });
  });
  // Threshold slider + mirrored number box. Live-update the fill, the
  // number and the condition text while dragging; persist on release.
  host.querySelectorAll("[data-rule-threshold]").forEach(sl => {
    const r = alertsState.rules.find(x => x.id === sl.dataset.ruleThreshold);
    if (!r) return;
    const num = host.querySelector(`[data-rule-threshold-num="${r.id}"]`);
    const cond = host.querySelector(`[data-rule-cond="${r.id}"]`);
    const live = (v) => {
      const min = +sl.min, max = +sl.max;
      sl.style.setProperty("--fill",
        Math.max(0, Math.min(100, ((v - min) / (max - min || 1)) * 100)) + "%");
      if (num) num.value = v;
      if (cond) cond.innerHTML = `Triggers when <b>${escHtml(r.metric)} ${ALERT_OP_LABEL[r.op] || r.op} ${v}</b>`;
    };
    sl.addEventListener("input", () => live(+sl.value));
    sl.addEventListener("change", () => { r.threshold = +sl.value; patchRule(r, { threshold: +sl.value }); });
    if (num) num.addEventListener("change", () => {
      const v = +num.value;
      if (isNaN(v)) { num.value = r.threshold; return; }
      sl.value = v; live(v); r.threshold = v; patchRule(r, { threshold: v });
    });
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
    // is obvious, saving with the box unchecked clears the window.
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
    // Carry the Armed toggle so saving an edit can't silently re-arm a
    // rule the user had disarmed (the payload's default is true).
    enabled: form.elements["enabled"] ? form.elements["enabled"].checked : true,
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
      // On edit, an empty secret means "leave the existing one alone",
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
