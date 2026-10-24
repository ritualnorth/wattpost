// WattPost UI, part 09: integrations panel (forecast, weather, MQTT, cloud).
// Plain script (no modules): the numbered files in web/js/ are loaded in order by
// index.html and share one global scope, exactly as the old single app.js did.

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
  // One round-trip (#18). This used to fire four parallel requests
  // (forecast/weather/cloud/mqtt config) and, because it was a
  // Promise.all, one failure failed the entire panel.
  // /api/system/integrations folds all four into a single response.
  // Retry once on 429 with a short backoff, and on a hard failure keep
  // whatever we already had on screen instead of blanking the panel.
  let data = null;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      data = await api("/api/system/integrations");
      break;
    } catch (e) {
      const rateLimited = (e.message || "").startsWith("429");
      if (rateLimited && attempt === 0) {
        await new Promise(r => setTimeout(r, 1200));
        continue;
      }
      const haveCached = integrationsState.forecast || integrationsState.weather
        || integrationsState.cloud || integrationsState.mqtt;
      if (!haveCached) {
        host.innerHTML = `<div class="settings-empty">Could not load integrations: ${e.message}</div>`;
      }
      return;
    }
  }
  integrationsState.forecast   = data.forecast;
  integrationsState.weather    = data.weather;
  integrationsState.cloud      = data.cloud;
  integrationsState.mqtt       = data.mqtt;
  integrationsState.prometheus = data.prometheus;
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
  if (integrationsState.editing === "prometheus") {
    host.innerHTML = renderPrometheusForm(integrationsState.prometheus || {});
    wirePrometheusForm();
    return;
  }

  const forecastConfigured = fc.configured;
  const weatherConfigured  = wc.configured;
  const cloudConfigured    = (integrationsState.cloud || {}).configured;
  const mqttEnabled        = (integrationsState.mqtt || {}).enabled;
  const promEnabled        = (integrationsState.prometheus || {}).enabled;

  // One card shape for every integration, mirroring the alert-rule /
  // transport cards: icon chip + name + status tag in the header, a
  // prose description, and the Configure/Edit action in the footer.
  const intgCard = ({ id, name, ok, status, sub, action, attr }) => `
    <div class="ar-card${ok ? " is-on" : ""}" data-integration="${id}">
      <div class="ar-head">
        <div class="ar-ico">${integrationIcon(id)}</div>
        <div class="ar-head-body">
          <div class="ar-title">${name}</div>
          <div class="ar-desc ar-desc--prose">${sub}</div>
        </div>
      </div>
      <div class="ar-meta">
        <span class="ar-tag ar-tag--${ok ? "ok" : "warn"}">${status}</span>
      </div>
      <div class="ar-foot">
        <button class="ar-link" ${attr}>${action}</button>
      </div>
    </div>`;

  host.innerHTML =
    intgCard({
      id: "solcast", name: "Solcast PV forecast", ok: forecastConfigured,
      status: forecastConfigured ? "configured" : "not set up",
      sub: forecastConfigured
        ? `Polling every ${fc.poll_hours}h · resource ${fc.resource_id?.slice(0, 8) || "·"}…`
        : `Sign up at <a href="https://solcast.com/free-rooftop-solar-forecasting" target="_blank" rel="noopener">solcast.com</a> for a free hobbyist API key.`,
      action: forecastConfigured ? "Edit" : "Configure", attr: "data-edit-forecast",
    }) +
    intgCard({
      id: "openmeteo", name: "Open-Meteo weather", ok: weatherConfigured,
      status: weatherConfigured ? "configured" : "not set up",
      sub: weatherConfigured
        ? `Polling every ${wc.poll_minutes}m · ${wc.lat?.toFixed(3)}, ${wc.lon?.toFixed(3)}`
        : `Current conditions (temp, cloud, sunrise/sunset). No API key. Free public service.`,
      action: weatherConfigured ? "Edit" : "Configure", attr: "data-edit-weather",
    }) +
    intgCard({
      id: "cloud", name: "WattPost cloud", ok: cloudConfigured,
      status: cloudConfigured ? "paired" : "not paired",
      sub: cloudConfigured
        ? `${integrationsState.cloud.label || "·"}${integrationsState.cloud.appliance_id ? ` · #${integrationsState.cloud.appliance_id}` : ""} · heartbeat every ${integrationsState.cloud.heartbeat_minutes}m · ${integrationsState.cloud.telemetry_sync === false ? "history upload off" : `uploading history every ${integrationsState.cloud.telemetry_minutes ?? 5}m`}`
        : `Pair with your <a href="${(integrationsState.cloud?.endpoint || "https://wattpost.cloud")}" target="_blank" rel="noopener">wattpost.cloud</a> account for the multi-site dashboard + offline alerts.`,
      action: cloudConfigured ? "Edit" : "Pair", attr: "data-edit-cloud",
    }) +
    intgCard({
      id: "mqtt", name: "MQTT export", ok: mqttEnabled,
      status: mqttEnabled ? "enabled" : "not set up",
      sub: mqttEnabled
        ? `Publishing to <code>${integrationsState.mqtt.host}:${integrationsState.mqtt.port}</code> under <code>${integrationsState.mqtt.topic_prefix}/</code>${integrationsState.mqtt.ha_discovery ? " · HA discovery on" : ""}`
        : `Publish every poll snapshot to a local MQTT broker for Home Assistant, Node-RED, or your own subscribers. Local-LAN, no cloud.`,
      action: mqttEnabled ? "Edit" : "Configure", attr: "data-edit-mqtt",
    }) +
    intgCard({
      id: "prometheus", name: "Prometheus / Grafana", ok: promEnabled,
      status: promEnabled ? "enabled" : "not set up",
      sub: promEnabled
        ? `Serving metrics at <code>${location.origin}/metrics</code> · prefix <code>${(integrationsState.prometheus.metric_prefix) || "wattpost"}</code>`
        : `Expose a read-only <code>/metrics</code> endpoint for Grafana (via Prometheus). No credentials, local-LAN.`,
      action: promEnabled ? "Edit" : "Configure", attr: "data-edit-prometheus",
    });
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
  $("[data-edit-prometheus]")?.addEventListener("click", () => {
    integrationsState.editing = "prometheus";
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
    // Send "****" sentinel if blank, server preserves the existing one.
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

function renderPrometheusForm(pc) {
  const enabled = !!pc.enabled;
  const prefix = pc.metric_prefix || "wattpost";
  const url = `${location.origin}/metrics`;
  return `
    <form class="alerts-form" data-form="prometheus">
      <div class="alerts-form-grid">
        <label class="alerts-field-wide">Metric name prefix
          <input type="text" name="metric_prefix" value="${prefix}" placeholder="wattpost"/>
        </label>
      </div>
      <p class="settings-foot">
        Serves a read-only Prometheus endpoint at <code>${url}</code>.
        Point Prometheus (or Grafana Agent) at it, then add Prometheus as a
        Grafana data source. Each numeric per-device reading becomes a gauge,
        e.g. <code>${prefix}_soc_pct{device="battery_0"}</code>. No credentials —
        same read-only telemetry as the dashboard. Runs alongside MQTT.
        Changes apply on next daemon restart.
      </p>
      <div class="alerts-form-actions">
        <button type="submit" class="btn-action btn-action--primary">${enabled ? "Save" : "Enable"}</button>
        ${enabled
          ? `<button type="button" class="btn-action alerts-icon-btn--danger" data-disable-prometheus>Disable</button>`
          : ""}
        <button type="button" class="btn-action" data-cancel-prometheus>Cancel</button>
        <span class="alerts-form-status"></span>
      </div>
    </form>`;
}

function wirePrometheusForm() {
  const form = document.querySelector("form[data-form='prometheus']");
  if (!form) return;
  form.addEventListener("submit", (e) => { e.preventDefault(); savePrometheusConfig(form); });
  form.querySelector("[data-cancel-prometheus]")?.addEventListener("click", () => {
    integrationsState.editing = null;
    renderIntegrationsPanel();
  });
  form.querySelector("[data-disable-prometheus]")?.addEventListener("click", () => disablePrometheus());
}

async function savePrometheusConfig(form) {
  const status = form.querySelector(".alerts-form-status");
  status.textContent = "Saving…"; status.className = "alerts-form-status";
  try {
    const r = await fetch("/api/exporters/prometheus/config", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: true,
        metric_prefix: form.elements["metric_prefix"].value.trim() || "wattpost",
      }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `${r.status} ${r.statusText}`);
    }
    integrationsState.editing = null;
    await refreshIntegrationsPanel();
  } catch (e) { status.textContent = e.message; status.classList.add("err"); }
}

async function disablePrometheus() {
  if (!confirm("Disable the Prometheus exporter? The /metrics endpoint will stop responding.")) return;
  try {
    const r = await fetch("/api/exporters/prometheus/config", {
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
        <label class="alerts-field-wide">
          <input type="checkbox" name="telemetry_sync" ${cc.telemetry_sync === false ? "" : "checked"}/>
          Upload history to the cloud so wattpost.cloud can show charts and live values
        </label>
        <label>Upload every (minutes)
          <input type="number" name="telemetry_minutes" value="${cc.telemetry_minutes ?? 5}" min="1" max="60"/>
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
          <button type="button" class="btn-action" data-retrust-cloud
                  title="Only if you rebuilt the cloud on purpose: accept its current command-signing key">Re-trust cloud key</button>
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
          click <strong>Pair an appliance</strong>, paste the 8-character code here, hit Pair.
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
  form.querySelector("[data-retrust-cloud]")?.addEventListener("click", () => retrustCloudKey(form));
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
    // See note above, hot-start makes the daemon live immediately.
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
    telemetry_sync: !!form.elements["telemetry_sync"]?.checked,
    telemetry_minutes: parseInt(form.elements["telemetry_minutes"]?.value || "5", 10),
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

async function retrustCloudKey(form) {
  const status = form.querySelector(".alerts-form-status");
  if (!confirm("Accept the cloud's CURRENT command-signing key?\n\nThis appliance only runs commands signed by the key it remembered when it paired. " +
               "Do this only if you rebuilt or moved the cloud on purpose. If you did not, something may be tampering with it.")) return;
  status.textContent = "Fetching the cloud's keys…"; status.className = "alerts-form-status";
  try {
    const r = await fetch("/api/cloud/signing-key/retrust", { method: "POST" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `${r.status} ${r.statusText}`);
    status.textContent = `✓ Pinned ${d.kids?.length || 0} key${(d.kids?.length || 0) === 1 ? "" : "s"}`;
    status.classList.add("ok");
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
  // Two distinct field sets, Solcast wants API creds, Open-Meteo
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
  // openmeteo, empty lat/lon means "inherit from weather block";
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
