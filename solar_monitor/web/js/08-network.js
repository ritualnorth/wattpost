// WattPost UI, part 08: WiFi provisioning, hotspot, location/GPS, solar-pause and reset-to-defaults.
// Plain script (no modules): the numbered files in web/js/ are loaded in order by
// index.html and share one global scope, exactly as the old single app.js did.

// ---------- WiFi station provisioning (join a home network) ----------
// Scan for nearby WiFi + join one so a box in AP mode (or flashed without
// WiFi) can be put on the LAN from the dashboard. Backend:
// /api/network/{status,ipv4} → set a static IP (or DHCP) on the box's live
// connection. Distinct from joining a WiFi network: this pins the address of
// whatever the box is already on (ethernet / joined WiFi).
async function refreshNetworkPanel() {
  const blk = document.getElementById("settings-network-block");
  const list = document.getElementById("network-list");
  if (!blk || !list) return;
  let r;
  try { r = await api("/api/network/status"); }
  catch { return; }
  if (!r.supported) { blk.style.display = "none"; return; }   // Docker/dev
  list.innerHTML = "";
  const conns = r.connections || [];
  if (conns.length === 0) {
    list.innerHTML = '<p class="settings-foot">No active connections found.</p>';
    return;
  }
  for (const c of conns) list.appendChild(renderNetworkConn(c));
}

function renderNetworkConn(c) {
  const wrap = document.createElement("div");
  wrap.style.cssText = "border:1px solid var(--border,#333);border-radius:10px;" +
    "padding:.6rem .7rem;margin:.4rem 0";
  const isManual = c.method === "manual";
  const addr = (c.address || "").split("/")[0];
  const prefix = (c.address || "").split("/")[1] || "24";
  wrap.innerHTML =
    '<div style="display:flex;justify-content:space-between;align-items:center;gap:.5rem">' +
      `<div><strong>${escHtml(c.device || c.name)}</strong> ` +
      `<span class="settings-foot">${escHtml(c.type || "")}</span><br/>` +
      `<span class="settings-foot">${escHtml(c.address || "—")} &middot; ${isManual ? "Static" : "DHCP"}</span></div>` +
      '<button class="btn-action net-edit" type="button">Edit</button>' +
    "</div>" +
    '<div class="net-form" style="display:none;margin-top:.5rem">' +
      '<label class="hist-row"><span>Addressing</span><span class="hist-input">' +
        '<select class="net-method"><option value="auto">Automatic (DHCP)</option>' +
        '<option value="manual">Static IP</option></select></span></label>' +
      '<div class="net-static" style="display:none">' +
        '<label class="hist-row"><span>IP address</span><span class="hist-input hist-input--text"><input class="net-ip" type="text" inputmode="decimal" placeholder="192.168.1.50"/></span></label>' +
        '<label class="hist-row"><span>Subnet prefix</span><span class="hist-input hist-input--text"><input class="net-prefix" type="number" min="1" max="32" placeholder="24"/></span></label>' +
        '<label class="hist-row"><span>Gateway</span><span class="hist-input hist-input--text"><input class="net-gw" type="text" inputmode="decimal" placeholder="192.168.1.1"/></span></label>' +
        '<label class="hist-row"><span>DNS</span><span class="hist-input hist-input--text"><input class="net-dns" type="text" placeholder="1.1.1.1, 8.8.8.8"/></span></label>' +
      "</div>" +
      '<div class="hist-actions">' +
        '<button class="btn-action btn-action--primary net-save" type="button">Save</button>' +
        '<button class="btn-action net-cancel" type="button">Cancel</button>' +
      "</div>" +
    "</div>";
  const form = wrap.querySelector(".net-form");
  const methodSel = wrap.querySelector(".net-method");
  const staticBox = wrap.querySelector(".net-static");
  methodSel.value = isManual ? "manual" : "auto";
  staticBox.style.display = isManual ? "" : "none";
  if (isManual) {
    wrap.querySelector(".net-ip").value = addr;
    wrap.querySelector(".net-prefix").value = prefix;
    wrap.querySelector(".net-gw").value = c.gateway || "";
    wrap.querySelector(".net-dns").value = (c.dns || []).join(", ");
  }
  wrap.querySelector(".net-edit").onclick = () => {
    form.style.display = form.style.display === "none" ? "" : "none";
  };
  wrap.querySelector(".net-cancel").onclick = () => { form.style.display = "none"; };
  methodSel.onchange = () => {
    staticBox.style.display = methodSel.value === "manual" ? "" : "none";
  };
  wrap.querySelector(".net-save").onclick = () => saveNetworkConn(c.name, wrap);
  return wrap;
}

async function saveNetworkConn(name, wrap) {
  const msg = document.getElementById("network-msg");
  const method = wrap.querySelector(".net-method").value;
  const body = { connection: name, method };
  if (method === "manual") {
    const ip = (wrap.querySelector(".net-ip").value || "").trim();
    if (!ip) { if (msg) { msg.style.color = "var(--red)"; msg.textContent = "Enter an IP address."; } return; }
    body.address = ip;
    const p = (wrap.querySelector(".net-prefix").value || "").trim(); if (p) body.prefix = parseInt(p, 10);
    const g = (wrap.querySelector(".net-gw").value || "").trim();     if (g) body.gateway = g;
    const d = (wrap.querySelector(".net-dns").value || "").trim();    if (d) body.dns = d;
  }
  if (msg) { msg.style.color = ""; msg.textContent = "Applying…"; }
  try {
    const r = await fetch(_withKiosk("/api/network/ipv4"), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${r.status}`); }
    const d = await r.json().catch(() => ({}));
    if (msg) { msg.style.color = ""; msg.textContent = d.message || "Applied. Reconnect at http://wattpost.local"; }
  } catch (e) {
    if (msg) { msg.style.color = "var(--red)"; msg.textContent = "Couldn't apply: " + (e.message || e); }
  }
}

// /api/network/wifi/{scan,join} → wattpost-helperd nmcli (the PSK is written
// to a 0600 NM keyfile host-side, never echoed back).
function wireWifiPanel() {
  if (!document.getElementById("settings-wifi-block")) return;
  const scanBtn = document.getElementById("wifi-scan-btn");
  if (scanBtn) scanBtn.onclick = scanWifiNetworks;
  const joinBtn = document.getElementById("wifi-join-btn");
  if (joinBtn) joinBtn.onclick = joinWifiNetwork;
  const cancelBtn = document.getElementById("wifi-join-cancel");
  if (cancelBtn) cancelBtn.onclick = () => {
    const f = document.getElementById("wifi-join-form");
    if (f) f.style.display = "none";
  };
  const staticToggle = document.getElementById("wifi-join-static-toggle");
  if (staticToggle) staticToggle.onchange = () => {
    const box = document.getElementById("wifi-join-static");
    if (box) box.style.display = staticToggle.checked ? "" : "none";
  };
}

async function scanWifiNetworks() {
  const msg = document.getElementById("wifi-scan-msg");
  const btn = document.getElementById("wifi-scan-btn");
  if (msg) { msg.style.color = ""; msg.textContent = "Scanning…"; }
  if (btn) btn.disabled = true;
  try {
    const r = await api("/api/network/wifi/scan");
    if (btn) btn.disabled = false;
    if (!r.supported) {
      // No host network control (Docker / dev) — hide the whole panel.
      const blk = document.getElementById("settings-wifi-block");
      if (blk) blk.style.display = "none";
      return;
    }
    if (r.hotspot_active) {
      // Single radio can't beacon the AP and scan at once — bounce it.
      await scanWifiViaApBounce();
      return;
    }
    if (r.error) {
      if (msg) { msg.style.color = "var(--red)"; msg.textContent = r.error; }
    } else if (msg) {
      const n = (r.networks || []).length;
      msg.textContent = `${n} network${n === 1 ? "" : "s"} found`;
    }
    renderWifiList(r.networks || []);
  } catch (e) {
    if (btn) btn.disabled = false;
    if (msg) { msg.style.color = "var(--red)"; msg.textContent = "Scan failed: " + (e.message || e); }
  }
}

// AP-mode scan: the box is beaconing its own hotspot, which we're probably
// connected through. Kick a background bounce (drop AP → scan → restore AP)
// and poll for the result; the AP blips so our own polls fail briefly while
// the device rejoins — tolerate that and keep trying.
async function scanWifiViaApBounce() {
  const msg = document.getElementById("wifi-scan-msg");
  const btn = document.getElementById("wifi-scan-btn");
  if (btn) btn.disabled = true;
  if (msg) {
    msg.style.color = "";
    msg.textContent = "Switching the hotspot off for a few seconds to scan — "
      + "your device should rejoin it automatically. Hold on…";
  }
  try {
    const r = await fetch(_withKiosk("/api/network/wifi/scan/ap-bounce"), { method: "POST" });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${r.status}`);
    }
  } catch (e) {
    if (btn) btn.disabled = false;
    if (msg) { msg.style.color = "var(--red)"; msg.textContent = "Couldn't start scan: " + (e.message || e); }
    return;
  }
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    await new Promise((res) => setTimeout(res, 3000));
    let s;
    try { s = await api("/api/network/wifi/scan/ap-bounce"); }
    catch { continue; }  // AP down / device reconnecting — keep polling
    if (s.state === "done" || s.state === "error") {
      if (btn) btn.disabled = false;
      if (s.state === "error") {
        if (msg) { msg.style.color = "var(--red)"; msg.textContent = s.error || "Scan failed."; }
      } else if (msg) {
        const n = (s.networks || []).length;
        msg.style.color = "";
        msg.textContent = `${n} network${n === 1 ? "" : "s"} found`;
      }
      renderWifiList(s.networks || []);
      return;
    }
  }
  if (btn) btn.disabled = false;
  if (msg) { msg.style.color = "var(--red)"; msg.textContent = "Scan timed out — try again once you're back on the hotspot."; }
}

function renderWifiList(networks) {
  const list = document.getElementById("wifi-network-list");
  if (!list) return;
  list.innerHTML = "";
  for (const n of networks) {
    const row = document.createElement("button");
    row.type = "button";
    row.style.cssText = "display:flex;justify-content:space-between;align-items:center;" +
      "width:100%;text-align:left;gap:.5rem;padding:.5rem .6rem;margin:.2rem 0;" +
      "border:1px solid var(--border,#333);border-radius:8px;background:transparent;" +
      "color:inherit;cursor:pointer;font:inherit";
    const meta = `${n.signal}% ${n.secure ? "🔒" : ""}${n.in_use ? " · connected" : ""}`;
    row.innerHTML =
      `<span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(n.ssid)}</span>` +
      `<span class="settings-foot" style="flex:none">${escHtml(meta)}</span>`;
    row.onclick = () => selectWifiNetwork(n);
    list.appendChild(row);
  }
}

function selectWifiNetwork(n) {
  const form  = document.getElementById("wifi-join-form");
  const ssidEl = document.getElementById("wifi-join-ssid");
  const pskEl  = document.getElementById("wifi-join-psk");
  if (ssidEl) ssidEl.value = n.ssid;
  if (pskEl) {
    pskEl.value = "";
    pskEl.disabled = !n.secure;
    pskEl.placeholder = n.secure ? "network password" : "open network — no password needed";
  }
  // Reset addressing to DHCP for each fresh selection.
  const staticToggle = document.getElementById("wifi-join-static-toggle");
  const staticBox = document.getElementById("wifi-join-static");
  if (staticToggle) staticToggle.checked = false;
  if (staticBox) staticBox.style.display = "none";
  if (form) form.style.display = "";
  if (n.secure && pskEl) pskEl.focus();
}

async function joinWifiNetwork() {
  const msg  = document.getElementById("wifi-join-msg");
  const ssid = (document.getElementById("wifi-join-ssid")?.value || "").trim();
  const psk  = document.getElementById("wifi-join-psk")?.value || "";
  if (!ssid) { if (msg) { msg.style.color = "var(--red)"; msg.textContent = "Pick a network first."; } return; }
  const body = { ssid };
  if (psk) body.password = psk;
  // Static IP (optional). Helper validates the addresses + returns a
  // clean 400 on a bad one, so we only require the IP itself here.
  if (document.getElementById("wifi-join-static-toggle")?.checked) {
    const ip      = (document.getElementById("wifi-join-ip")?.value || "").trim();
    const prefix  = (document.getElementById("wifi-join-prefix")?.value || "").trim();
    const gateway = (document.getElementById("wifi-join-gateway")?.value || "").trim();
    const dns     = (document.getElementById("wifi-join-dns")?.value || "").trim();
    if (!ip) { if (msg) { msg.style.color = "var(--red)"; msg.textContent = "Enter a static IP address, or switch off Static IP."; } return; }
    body.static_ip = ip;
    if (prefix)  body.prefix = parseInt(prefix, 10);
    if (gateway) body.gateway = gateway;
    if (dns)     body.dns = dns;
  }
  if (msg) { msg.style.color = ""; msg.textContent = `Connecting to "${ssid}"…`; }
  const btn = document.getElementById("wifi-join-btn");
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(_withKiosk("/api/network/wifi/join"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (btn) btn.disabled = false;
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${r.status}`);
    }
    if (msg) { msg.style.color = ""; msg.textContent = `Connected to "${ssid}". This box is now on your LAN.`; }
    const pskEl = document.getElementById("wifi-join-psk");
    if (pskEl) pskEl.value = "";
  } catch (e) {
    if (btn) btn.disabled = false;
    if (msg) { msg.style.color = "var(--red)"; msg.textContent = "Couldn't connect: " + (e.message || e); }
  }
}

async function refreshHotspotPanel() {
  const block = document.getElementById("settings-hotspot-block");
  if (!block) return;
  const statusEl = document.getElementById("hotspot-status-v");
  const msgEl    = document.getElementById("hotspot-msg");
  let s;
  try { s = await api("/api/hotspot/status"); }
  catch (e) {
    if (statusEl) statusEl.textContent = "Couldn't load: " + (e.message || e);
    return;
  }

  if (statusEl) {
    if (!s.nmcli_available) {
      statusEl.textContent = "Unavailable — NetworkManager (nmcli) not found on host";
    } else if (!s.configured) {
      statusEl.textContent = "Off · not configured";
    } else if (s.active) {
      const clients = (s.client_count == null) ? "" : ` · ${s.client_count} client${s.client_count === 1 ? "" : "s"}`;
      statusEl.textContent = `On · "${s.ssid}" → http://${s.gateway}${clients}`;
    } else {
      statusEl.textContent = `Off · "${s.ssid}" ready`;
    }
  }

  // Pre-fill form from current config (only when a block exists).
  const ssidEl = document.getElementById("hotspot-ssid");
  const enEl   = document.getElementById("hotspot-enabled");
  const ahEl   = document.getElementById("hotspot-auto-handoff");
  const cpEl   = document.getElementById("hotspot-captive");
  if (s.configured) {
    if (ssidEl && document.activeElement !== ssidEl) ssidEl.value = s.ssid || "";
    if (enEl) enEl.checked = !!s.enabled;
    if (ahEl) ahEl.checked = !!s.auto_handoff;
    if (cpEl) cpEl.checked = !!s.captive_portal;
  }

  const unavailable = !s.nmcli_available;
  for (const id of ["hotspot-save", "hotspot-on", "hotspot-off"]) {
    const b = document.getElementById(id);
    if (b) b.disabled = unavailable;
  }

  const saveBtn = document.getElementById("hotspot-save");
  if (saveBtn) saveBtn.onclick = saveHotspotSettings;
  const onBtn = document.getElementById("hotspot-on");
  if (onBtn) onBtn.onclick = () => hotspotPower(true);
  const offBtn = document.getElementById("hotspot-off");
  if (offBtn) offBtn.onclick = () => hotspotPower(false);
  if (msgEl && !msgEl.dataset.sticky) msgEl.textContent = "";
}

async function saveHotspotSettings() {
  const msgEl = document.getElementById("hotspot-msg");
  const ssid  = (document.getElementById("hotspot-ssid")?.value || "").trim();
  const pw    = document.getElementById("hotspot-password")?.value || "";
  const enabled = !!document.getElementById("hotspot-enabled")?.checked;
  const auto_handoff = !!document.getElementById("hotspot-auto-handoff")?.checked;
  const captive_portal = !!document.getElementById("hotspot-captive")?.checked;
  const body = { ssid, enabled, auto_handoff, captive_portal };
  // Only send password when the user typed one — blank keeps current.
  if (pw.length) body.password = pw;
  if (msgEl) { msgEl.textContent = "Saving…"; msgEl.style.color = ""; msgEl.dataset.sticky = "1"; }
  try {
    const r = await fetch(_withKiosk("/api/hotspot/config"), {
      method: "PUT",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const detail = await r.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${r.status}`);
    }
    if (msgEl) { msgEl.style.color = ""; msgEl.textContent = "Saved."; }
    const pwEl = document.getElementById("hotspot-password");
    if (pwEl) pwEl.value = "";
    // Config hot-reloads; give the new scheduler a beat, then refresh.
    setTimeout(() => { delete msgEl?.dataset.sticky; refreshHotspotPanel(); }, 1500);
  } catch (e) {
    if (msgEl) { msgEl.style.color = "var(--red)"; msgEl.textContent = "Save failed: " + (e.message || e); delete msgEl.dataset.sticky; }
  }
}

async function hotspotPower(on) {
  const msgEl = document.getElementById("hotspot-msg");
  if (msgEl) { msgEl.textContent = on ? "Turning on…" : "Turning off…"; msgEl.style.color = ""; msgEl.dataset.sticky = "1"; }
  try {
    const r = await fetch(_withKiosk(on ? "/api/hotspot/on" : "/api/hotspot/off"), { method: "POST" });
    if (!r.ok) {
      const detail = await r.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${r.status}`);
    }
    if (msgEl) { msgEl.style.color = ""; msgEl.textContent = on ? "Hotspot on." : "Hotspot off."; }
    delete msgEl?.dataset.sticky;
    refreshHotspotPanel();
  } catch (e) {
    if (msgEl) { msgEl.style.color = "var(--red)"; msgEl.textContent = (on ? "Turn on failed: " : "Turn off failed: ") + (e.message || e); delete msgEl.dataset.sticky; }
  }
}

// While a GPS is acquiring, re-poll the location panel every few seconds
// so the user can watch satellites climb + the fix land, without manually
// reopening Settings. Self-stops once a fix arrives or the panel is hidden.
let _locReacquireTimer = null;
function _scheduleLocationReacquirePoll() {
  if (_locReacquireTimer) return;        // already polling
  _locReacquireTimer = setInterval(() => {
    const el = document.getElementById("settings-location-current-v");
    if (!el || !el.offsetParent) { _stopLocationReacquirePoll(); return; }  // panel hidden
    refreshLocationPanel();
  }, 5000);
}
function _stopLocationReacquirePoll() {
  if (_locReacquireTimer) { clearInterval(_locReacquireTimer); _locReacquireTimer = null; }
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
    _stopLocationReacquirePoll();        // got a fix — stop watching
  } else {
    // No usable location yet — but a configured GPS may simply be
    // acquiring. Tell the user WHICH, rather than a flat "no source"
    // that reads as "your GPS isn't detected" when it actually is.
    let g = null;
    try { g = await api("/api/gps"); } catch (_) {}
    if (g && g.configured) {
      const n = g.satellites_in_view || 0;
      const snr = g.best_snr_dbhz;
      if (g.acquiring || n > 0) {
        const weak = snr != null && snr < 25;
        currentEl.textContent =
          `GPS connected — acquiring a fix: ${n} satellite${n === 1 ? "" : "s"} in view`
          + (snr != null ? ` (best signal ${snr} dB-Hz${weak ? ", weak" : ""})` : "")
          + ". " + (weak
              ? "Signal is weak — give the antenna a clearer view of the sky (a window or outside)."
              : "Hang tight — a cold start can take a minute.");
      } else {
        currentEl.textContent =
          `GPS connected (${g.port || "serial"}) — searching, no satellites in view yet. `
          + "The antenna needs a clear view of the sky: a window, or run it outside.";
      }
      _scheduleLocationReacquirePoll();  // watch it lock in real time
    } else {
      currentEl.textContent = "No location source yet — enable a USB GPS receiver below, or set a static forecast lat/lon.";
      _stopLocationReacquirePoll();
    }
  }
  // Pre-select the current mode. The share level is a preference, so it's
  // always settable — it simply takes effect once a location source comes
  // online. (Disabling it when there's no fix just confused people.)
  const mode = (s.share_with_cloud || "off").toLowerCase();
  for (const r of radios) {
    r.checked = (r.value === mode);
    r.disabled = false;
    r.onchange = () => updateLocationShare(r.value);
  }
  if (msgEl) {
    msgEl.textContent = s.current
      ? ""
      : "Set your preference now — it applies as soon as a location source is configured.";
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
        ? "Off, cloud will receive no location data from the next heartbeat."
        : (mode === "approx"
            ? "Approximate, coordinates rounded to ~10km before transmission."
            : "Precise, exact lat/lon will be shared on the next heartbeat.");
    }
  } catch (e) {
    if (msgEl) { msgEl.style.color = "var(--red)"; msgEl.textContent = "Save failed: " + e.message; }
  }
}

// GPS receiver control (#125). Lives inside the Location settings block.
// When a receiver is enabled we show live acquisition status + a Disable
// button; when it's off we scan /api/setup/usb_scan for NMEA receivers and
// offer a one-tap Enable. Enabling writes the `gps:` block via the same
// hot-reloading config path the wizard uses — no daemon restart, no YAML.
async function refreshGpsControl() {
  const body = document.getElementById("settings-gps-body");
  if (!body) return;
  let cfg = null;
  try { cfg = await api("/api/gps/config"); } catch (_) { body.textContent = "Couldn't load GPS status."; return; }
  if (cfg && cfg.configured) {
    let g = null;
    try { g = await api("/api/gps"); } catch (_) {}
    let status = "starting…";
    if (g && g.configured) {
      if (g.has_fix) status = "fix acquired";
      else if (g.acquiring || (g.satellites_in_view || 0) > 0) status = `acquiring (${g.satellites_in_view || 0} sat in view)`;
      else status = "searching — needs a clear view of the sky";
    }
    body.innerHTML = `<div class="settings-row ut-center-gap">
      <span>Enabled on <code>${_escHtmlMini(cfg.port)}</code> · ${_escHtmlMini(status)}</span>
      <button class="btn-action" id="gps-disable" type="button">Disable</button></div>`;
    const db = document.getElementById("gps-disable");
    if (db) db.onclick = () => setGps(false);
    return;
  }
  body.textContent = "Scanning for a USB GPS…";
  let adapters = [];
  try { const r = await api("/api/setup/usb_scan"); adapters = (r.adapters || []).filter(a => a.protocol === "nmea_gps"); } catch (_) {}
  if (!adapters.length) {
    body.innerHTML = `No USB GPS detected. Plug one in (e.g. a u-blox VK-162) and it appears here within a few seconds. <a href="#" id="gps-rescan">Rescan</a>.`;
    const rs = document.getElementById("gps-rescan");
    if (rs) rs.onclick = (e) => { e.preventDefault(); refreshGpsControl(); };
    return;
  }
  body.innerHTML = adapters.map(a => `<div class="settings-row ut-center-gap">
      <span>${_escHtmlMini(a.product || a.port)} <code>${_escHtmlMini(a.port)}</code></span>
      <button class="btn-action btn-action--primary" type="button" data-gps-enable="${_escHtmlMini(a.port)}">Enable</button></div>`).join("");
  body.querySelectorAll("[data-gps-enable]").forEach(b => b.onclick = () => setGps(true, b.dataset.gpsEnable));
}

async function setGps(enabled, port) {
  const body = document.getElementById("settings-gps-body");
  if (body) body.textContent = enabled ? "Enabling…" : "Disabling…";
  try {
    const r = await fetch(_withKiosk("/api/gps/config"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(enabled ? { enabled: true, port: port, baudrate: 9600 } : { enabled: false }),
    });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${r.status}`); }
  } catch (e) {
    if (body) body.innerHTML = `<span style="color:var(--red)">Couldn't ${enabled ? "enable" : "disable"} GPS: ${_escHtmlMini(e.message || String(e))}</span>`;
    return;
  }
  // Hot-reload swaps the scheduler asynchronously; give it a beat before re-reading.
  await new Promise(r => setTimeout(r, 1800));
  await refreshGpsControl();
  if (typeof refreshLocationPanel === "function") refreshLocationPanel();
}

// Solar-pause panel (#163). Pulls /api/outputs/solar_pause for the live
// rule state + the controller's most recent decision, fills the form,
// and wires the Save button. Outputs come from /api/outputs filtered to
// charge-output kinds so the dropdown only shows things the user can
// reasonably auto-pause (not, say, a Renogy DC load output).
async function refreshSolarPauseSettings() {
  const block = document.getElementById("settings-solar-pause-block");
  if (!block || block.hidden) return;   // hidden from prod until ready (#163)
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
    // Sensible targets: smart plugs (the recommended path, they cut
    // mains upstream of the AC charger and work regardless of charger
    // vendor) plus any future native AC-charger writes. We deliberately
    // skip the Renogy Rover's DC load output here, it's not the right
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
