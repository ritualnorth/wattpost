// WattPost UI, part 13: setup wizard.
// Plain script (no modules): the numbered files in web/js/ are loaded in order by
// index.html and share one global scope, exactly as the old single app.js did.

// ---------- setup wizard ----------
// Two REST endpoints carry it: /api/setup/transports + /api/setup/probe +
// /api/setup/add_device. UI is intentionally bare, no modals, no router,
// just an inline state machine that walks transport → scan → add per row.
let wizState = { transport: null, scanResults: null, knownKeys: new Set() };

const KIND_LABEL = {
  smart_battery: "Smart battery",
  charge_controller: "Charge controller",
};

function wizKnownKey(transport, slaveId) { return `${transport}::${slaveId}`; }

// Always-on discovery: show what the appliance can already see over
// Bluetooth (Victron / sensors / Renogy BT) without the user scanning,
// so adding is a recognition step, not a hunt. Informational + nudges
// to "Find my devices" (the existing add flow handles the actual pairing
// incl. the Victron key). Best-effort: a fetch failure just renders
// nothing.
async function wizRenderNearby() {
  const host = document.getElementById("wiz-nearby");
  if (!host) return;
  let devs = [];
  try { const r = await api("/api/setup/discovered"); devs = (r && r.devices) || []; }
  catch (_) { host.innerHTML = ""; return; }
  if (!devs.length) { host.innerHTML = ""; return; }
  const LABEL = { victron: "Victron", renogy: "Renogy BT", mopeka: "Mopeka tank sensor",
                  govee: "Govee sensor", ruuvi: "RuuviTag" };
  const rows = devs.map(d => {
    const v = LABEL[d.vendor] || d.vendor || "device";
    const nm = d.name ? ` · ${escHtml(d.name)}` : "";
    const rssi = d.rssi != null ? `${d.rssi} dBm` : "";
    return `<div class="wiz-nearby-row" style="display:flex;justify-content:space-between;gap:.6rem;padding:.35rem 0;border-top:1px solid var(--border)">
      <span><strong>${escHtml(v)}</strong>${nm}</span>
      <span class="settings-foot ut-nowrap">${escHtml(rssi)}</span></div>`;
  }).join("");
  host.innerHTML = `<div class="wiz-nearby-card" style="margin:0 0 1rem;padding:.8rem 1rem;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--r-md)">
    <div style="font-weight:600;display:flex;align-items:center;gap:.4rem">
      <svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.55a11 11 0 0 1 14 0M8.5 16.05a6 6 0 0 1 7 0M2 8.82a15 15 0 0 1 20 0"/><line x1="12" y1="20" x2="12" y2="20"/></svg>
      Seen nearby
    </div>
    <p class="settings-foot" style="margin:.3rem 0 .5rem">WattPost can already see these over Bluetooth. Tap <strong>Find my devices</strong> below to add one.</p>
    ${rows}
  </div>`;
}

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
        <p><strong>Let's find your gear.</strong></p>
        <p>Plug in your gear, then scan. WattPost checks Bluetooth for your devices — Renogy dongles (BT-2), and Victron / JK BMS / sensors that broadcast on their own. Nothing to choose; just scan.</p>
        <div id="wiz-nearby"></div>
        <div class="wiz-controls" style="flex-wrap:wrap;gap:.5rem">
          <button id="wiz-find-dongle-btn" class="btn-action btn-action--primary">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
            <span>Find my devices</span>
          </button>
          <span id="wiz-find-status" class="wiz-status"></span>
        </div>
        <div id="wiz-find-results" class="wiz-results"></div>
        <details class="wiz-advanced" style="margin-top:.8rem">
          <summary class="ut-link-ish">Wired (USB-RS485) or advanced setup</summary>
          <p class="settings-foot ut-my-sm">
            A USB-to-RS485 dongle (~£10, FTDI / CH340) on the Pi, with Cat5 to your charger's RJ45 comms port. That port is RS-485, NOT Ethernet — it doesn't go in the Pi's network jack.
          </p>
          <div class="wiz-controls">
            <button id="wiz-find-usb-btn" class="btn-action">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="2.5" r="1.5"/><path d="M12 4v18"/><path d="M8 9h8"/><path d="M8 9l-2 4 2 4h8l2-4-2-4"/></svg>
              <span>Scan USB ports</span>
            </button>
          </div>
        </details>
      </div>`;
      document.getElementById("wiz-find-dongle-btn")?.addEventListener("click", wizFindDongle);
      document.getElementById("wiz-find-usb-btn")?.addEventListener("click", wizFindUsb);
      wizRenderNearby();
      return;
    }
    host.innerHTML = '<div id="wiz-nearby"></div>' + transports.map(t => {
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
        <button class="btn-action btn-action--primary" id="wiz-find-dongle-btn">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
          <span>Find more devices</span>
        </button>
        <span id="wiz-find-status" class="wiz-status"></span>
        <div id="wiz-find-results" class="wiz-results"></div>
        <details class="wiz-advanced ut-mt-lg">
          <summary class="ut-link-ish">Wired (USB-RS485) or advanced setup</summary>
          <p class="settings-foot ut-my-sm">
            Bluetooth and USB-RS485 run side by side — e.g. a Renogy BT-2 for the MPPT and a USB dongle for a wired BMS.
          </p>
          <div class="wiz-controls">
            <button id="wiz-find-usb-btn" class="btn-action">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="2.5" r="1.5"/><path d="M12 4v18"/><path d="M8 9h8"/><path d="M8 9l-2 4 2 4h8l2-4-2-4"/></svg>
              <span>Scan USB ports</span>
            </button>
          </div>
        </details>
      </div>`;
    // Wire the collapsible "Add another" panel, same two scan
    // buttons reuse wizFindDongle / wizFindUsb so the BLE+USB
    // mixed-install path is identical regardless of whether the
    // user is adding their first or fifth transport.
    document.getElementById("wiz-add-another-btn")?.addEventListener("click", () => {
      const panel = document.getElementById("wiz-add-another-panel");
      if (panel) panel.hidden = !panel.hidden;
    });
    document.getElementById("wiz-find-dongle-btn")?.addEventListener("click", wizFindDongle);
    document.getElementById("wiz-find-usb-btn")?.addEventListener("click", wizFindUsb);
    // No `disabled` on offline rows, the scan endpoint auto-reopens
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
    // Auto-select when there's only one transport, there's no
    // meaningful "pick" to make, so making the user tap a row before
    // Scan works is friction with no upside. The previous fix told
    // users to "Pick a transport above first" but the wizard didn't
    // make obvious that the row was tappable; this short-circuits
    // that whole UX trap.
    if (transports.length === 1 && !wizState.transport) {
      selectTransport(transports[0].id);
    }
    wizRenderNearby();
  } catch (e) {
    host.innerHTML = `<div class="wiz-empty">Could not load transports: ${e.message}</div>`;
  }
}

async function wizScan() {
  const btn = $("#wiz-scan-btn");
  const status = $("#wiz-scan-status");
  const host = $("#wiz-scan-results");
  if (!wizState.transport) {
    // Don't silently no-op, the previous version did, which left
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

  // Live state, devices found so far, used both to render
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
          // Final summary, fall through to the "finished" block.
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
    // A save's hot-reload kills the scan stream, that's expected,
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
    // text in, it'd flash on screen for milliseconds before the
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
            <span class="wiz-row-model">${escHtml(r.model || '·')}</span>
          </div>
          <div class="wiz-row-meta">
            <span class="wiz-tag">${escHtml(r.vendor || 'unknown vendor')}</span>
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
    // Key first — that's what people fix when readings stay blank (wrong
    // Instant Readout key). MAC is the rare secondary edit.
    promptLabel = "Encryption key for " + id + " — 32 hex chars from "
      + "VictronConnect → the device → Product info → Show (leave blank to keep):";
    promptDefault = "";
    field = "encryption_key";
    second = {
      label: "BLE MAC (only if the device itself changed; leave blank to keep):",
      field: "address",
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
    // Nothing to update, let them know rather than silently no-op.
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
    // stream, don't treat that as a real error." Cleared after the
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
      // #297-4, data.label is user-supplied; escape before innerHTML.
      row.querySelector(".wiz-row-action").innerHTML = `<span class="wiz-saved">Saved · ${escHtml(data.label)}</span>`;
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
      // Best-effort, failures here are silent.
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

// "Find my dongle", scans BLE for ~8 s and surfaces every advertising
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

  // Build the "recently disappeared" panel first if any, this is
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
      // from the payload header, SmartShunt/SmartSolar/Orion-Tr etc.)
      // makes the badge richer than just "Victron".
      const dc = d.victron_device_class || "Victron device";
      const dcRaw = d.victron_device_class || "";
      hintHtml = `<span class="wiz-vendor-hint wiz-vendor-hint--victron">${escHtml(dc)}</span>`;
      // Stash device_class on the Pair button so the Save handler can
      // pass it to the backend, needed to pick the right device_kind
      // when auto-creating the device row (#159-adjacent fix).
      actionHtml = `<button class="btn-action btn-action--primary" data-pair-victron="${escHtml(d.address)}" data-victron-class="${escHtml(dcRaw)}">Pair Victron</button>`;
      // The key form is rendered hidden and revealed when the user
      // taps "Pair Victron", keeps the scan-results card compact.
      keyFormHtml = `
        <div class="wiz-victron-key" data-victron-key-for="${escHtml(d.address)}" hidden style="margin-top:.6rem;padding:.7rem;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--r-md)">
          <p class="settings-foot ut-mb-sm">
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
    } else if (vendor === "mopeka") {
      // Mopeka tank sensor, plaintext, no key dance. Single-tap pair.
      hintHtml = `<span class="wiz-vendor-hint wiz-vendor-hint--victron">Mopeka tank sensor</span>`;
      actionHtml = `<button class="btn-action btn-action--primary" data-pair-mopeka="${escHtml(d.address)}">Pair Mopeka</button>`;
    } else if (vendor === "govee") {
      hintHtml = `<span class="wiz-vendor-hint wiz-vendor-hint--victron">Govee thermometer</span>`;
      actionHtml = `<button class="btn-action btn-action--primary" data-pair-govee="${escHtml(d.address)}">Pair Govee</button>`;
    } else if (vendor === "ruuvi") {
      hintHtml = `<span class="wiz-vendor-hint wiz-vendor-hint--victron">RuuviTag</span>`;
      actionHtml = `<button class="btn-action btn-action--primary" data-pair-ruuvi="${escHtml(d.address)}">Pair Ruuvi</button>`;
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

  // Renogy / fallback path, unchanged.
  list.querySelectorAll("[data-use-mac]").forEach(b => {
    b.addEventListener("click", () => wizAddTransportFromMac(b.dataset.useMac, b.dataset.useName));
  });
  // Mopeka pairing, single-tap, no key prompt.
  list.querySelectorAll("[data-pair-mopeka]").forEach(b => {
    b.addEventListener("click", () => {
      b.disabled = true;
      b.textContent = "Saving…";
      wizAddTransportFromMopeka(b.dataset.pairMopeka, b);
    });
  });
  // Govee + Ruuvi, same single-tap, different transport type.
  list.querySelectorAll("[data-pair-govee]").forEach(b => {
    b.addEventListener("click", () => {
      b.disabled = true;
      b.textContent = "Saving…";
      wizAddPassiveBleTransport("ble_govee_advertise", b.dataset.pairGovee, "Pair Govee", b);
    });
  });
  list.querySelectorAll("[data-pair-ruuvi]").forEach(b => {
    b.addEventListener("click", () => {
      b.disabled = true;
      b.textContent = "Saving…";
      wizAddPassiveBleTransport("ble_ruuvi_advertise", b.dataset.pairRuuvi, "Pair Ruuvi", b);
    });
  });
  // Victron pairing, show + wire the key-entry form.
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
  // Format "30 seconds ago" / "3 min ago", a generic relative
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
          the other host (Devices → Delete) or stop its
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

// Generic single-tap MAC-only passive BLE transport pair flow. Used
// by Govee + Ruuvi; Mopeka kept its own function for symmetry with
// the Victron pairing UX text it borrows.
async function wizAddPassiveBleTransport(type, mac, restoreLabel, button) {
  let res;
  try {
    const r = await fetch("/api/setup/transports/add", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({type, address: mac}),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    res = await r.json();
  } catch (e) {
    if (button) {
      button.disabled = false;
      button.textContent = restoreLabel;
    }
    alert("Couldn't add transport: " + (e.message || String(e)));
    return;
  }
  if (button) button.textContent = `Added · ${res.label || res.id}`;
  await new Promise(r => setTimeout(r, 1200));
  await wizLoadTransports();
}

async function wizAddTransportFromMopeka(mac, button) {
  let res;
  try {
    const r = await fetch("/api/setup/transports/add", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        type: "ble_mopeka_advertise",
        address: mac,
      }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    res = await r.json();
  } catch (e) {
    if (button) {
      button.disabled = false;
      button.textContent = "Pair Mopeka";
    }
    alert("Couldn't add Mopeka: " + (e.message || String(e)));
    return;
  }
  if (button) button.textContent = `Added · ${res.label || res.id}`;
  await new Promise(r => setTimeout(r, 1200));
  await wizLoadTransports();
}

// USB-RS485 path, same shape as wizFindDongle/wizAddTransportFromMac
// but driven by /api/setup/usb_scan + the serial_modbus transport type.
// Customers who skip the BT-2 (or replace it with a wired dongle for
// reliability, sub-ms round-trips, no BLE timeouts, proper FC06 acks)
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
    //   modbus_rtu, silent serial, hint Modbus (the dominant case).
    //   nmea_gps  , emitted GPS sentences during the sniff window.
    //   ve_direct , Victron VE.Direct text frames at 19200 baud.
    //   unknown   , port opens but bytes don't match anything we know.
    //   busy      , port held by another process (probably us).
    const proto = a.protocol || "unknown";
    let badge = "";
    let action = `<button class="btn-action btn-action--primary"
                data-use-port="${escHtml(a.port)}"
                data-use-label="${escHtml(a.product || a.port)}">Use as Modbus</button>`;
    if (proto === "ve_direct") {
      badge = `<span class="wiz-vendor-hint wiz-vendor-hint--victron">Victron VE.Direct</span>`;
      // Wire the VE.Direct path directly, only sensible action for
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
      // Wire the GPS path directly: a port emitting NMEA only makes
      // sense as the location receiver. Enabling it writes the `gps:`
      // block and hot-reloads (location stays opt-in — this is the
      // explicit opt-in action).
      action = `<button class="btn-action btn-action--primary"
                  data-use-gps-port="${escHtml(a.port)}"
                  data-use-gps-label="${escHtml(a.product || a.port)}">
                  Use as GPS
                </button>`;
    } else if (proto === "busy") {
      badge = `<span class="wiz-vendor-hint wiz-vendor-hint--warn">port busy</span>`;
      action = `<button class="btn-action" disabled
                  title="Port couldn't be opened. Already in use by another process (often the daemon itself if you've already added this transport)">
                  port busy
                </button>`;
    } else if (proto === "unknown") {
      badge = `<span class="wiz-vendor-hint wiz-vendor-hint--warn">unknown output</span>`;
      // Still offer Modbus, user may know what it is even if we don't.
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
  list.querySelectorAll("[data-use-gps-port]").forEach(b => {
    b.addEventListener("click", () => wizEnableGpsFromPort(
      b.dataset.useGpsPort, b.dataset.useGpsLabel,
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

// GPS enable flow (#125). The USB-scan sniff already confirmed this
// port is emitting NMEA. Write the `gps:` block via /api/gps/config;
// the daemon hot-reloads and the receiver starts acquiring. Location
// sharing stays whatever the privacy toggle says — this only turns on
// the local receiver.
async function wizEnableGpsFromPort(port, label) {
  const stat = document.getElementById("wiz-find-status");
  stat.textContent = `Enabling GPS on ${port}…`;
  try {
    const r = await fetch("/api/gps/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: true, port: port, baudrate: 9600 }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
  } catch (e) {
    stat.textContent = "";
    alert("Couldn't enable GPS: " + (e.message || String(e)));
    return;
  }
  stat.textContent = `GPS enabled on ${port}. Acquiring a fix — give the antenna a clear view of the sky.`;
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
