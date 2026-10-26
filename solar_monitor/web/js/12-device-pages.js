// WattPost UI, part 12: per-device page, device settings, outputs, output schedules.
// Plain script (no modules): the numbered files in web/js/ are loaded in order by
// index.html and share one global scope, exactly as the old single app.js did.

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
  else if (dev.kind === "bank") inner = buildBankDetail(dev);
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
  // #297-4, escape every DB-derived string before it lands in
  // innerHTML. display_name + label come from user-edited rename
  // input; vendor/kind/model from device drivers; all become tainted
  // if a malicious backup restores a poisoned DB row. Defense in
  // depth alongside #297-1 (config sanitiser), even if a row slips
  // through, rendering can't execute attacker JS.
  host.innerHTML = `
    <div class="dev-detail-head">
      <div class="dev-detail-crumb">
        <a href="#/devices">← Devices</a>
        <span class="dev-detail-title">
          <span class="dev-detail-icon">${ICONS[KIND_ICON[dev.kind] || "unknown"]}</span>
          <span data-dev-display-name>${escHtml(shown)}</span>
          <button class="dev-rename-btn" data-dev-rename="${escHtml(dev.label)}"
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
          ${escHtml(dev.vendor)} · ${escHtml(dev.kind)}${dev.slave_id != null ? " · slave " + Number(dev.slave_id) : ""}${fw ? " · fw " + escHtml(fw) : ""}${dev.latest?.model ? " · " + escHtml(dev.latest.model) : ""}${renamed ? ` · label <code>${escHtml(dev.label)}</code>` : ""}
        </span>
      </div>
      <div class="dev-detail-nav">
        ${prev ? `<a class="btn-action" href="#/device/${encodeURIComponent(prev.label)}">← ${escHtml(dispName(prev))}</a>` : ""}
        ${next ? `<a class="btn-action" href="#/device/${encodeURIComponent(next.label)}">${escHtml(dispName(next))} →</a>` : ""}
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
  // Renders nothing if the device has none, Rovers get a Load panel,
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
      <h3 class="ut-mb-sm">Settings</h3>
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
    ? `Last command: <strong>${escHtml(lastCmd.action)}</strong> · ${fmt.ago(lastCmd.at)} · by ${escHtml(lastCmd.by)} · ${escHtml(lastCmd.result)}`
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
          ${escHtml(o.name)}
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
// (M T W T F S S, Monday is bit 0, matches `datetime.weekday()`).

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
  // Each output channel as a column, many Blue Smart IP65 chargers
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
        <span class="meta-k ut-dim">${fmt.num(v, 1)} V · ${fmt.num(i, 1)} A</span>
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
      <div class="chgstats-ribbon-wrap ut-mt-lg">
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

  // Ribbon, single horizontal bar of colored state segments over 24h.
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
      // Optimistic update, patch the in-memory devices entry so the
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

// #292, Battery detail page. Tap the SoC donut on the dashboard
// and you land here. Three sections stacked:
//   1. Bank summary stats (SoC, V, A, W, capacity, source)
//   2. Per-pack mini-card grid, each pack is a tap target into
//      its own smart_battery detail page for cells/temps/firmware
//   3. Standard history chart for any bank metric
//
// Reuses the rest of the renderDeviceDetail chrome (title, prev/next,
// chart wiring) so the bank view feels native vs. a one-off page.
function buildBankDetail(dev) {
  const l = dev.latest || {};
  const packs = devices.filter((d) => d.kind === "smart_battery");

  const summaryCell = (k, v) =>
    `<div class="bank-sum-cell"><span class="meta-k">${escHtml(k)}</span><strong class="bank-sum-v">${v}</strong></div>`;

  const stats = [
    ["State of charge", l.soc_pct != null ? `${fmt.num(l.soc_pct, 1)}%` : "·"],
    ["Voltage",         l.voltage_v != null ? `${fmt.num(l.voltage_v, 2)} V` : "·"],
    ["Current",         l.current_a != null ? `${fmt.signed(l.current_a, 2)} A` : "·"],
    ["Power",           l.power_w != null ? `${fmt.signed(l.power_w, 0)} W` : "·"],
    ["Capacity",        l.capacity_ah != null ? `${fmt.num(l.capacity_ah, 0)} Ah` : "·"],
    ["Source",          l.source ? escHtml(l.source) : "·"],
  ];

  const driftMv = l.worst_pack_drift_v != null ? Math.round(l.worst_pack_drift_v * 1000) : null;
  const driftLine = driftMv != null
    ? `<span class="panel-sub">Worst pack drift: <strong>${driftMv} mV</strong></span>`
    : "";

  const summary = `
    <section class="panel">
      <div class="panel-header"><h2>Bank summary</h2>${driftLine}</div>
      <div class="bank-sum-grid">
        ${stats.map(([k, v]) => summaryCell(k, v)).join("")}
      </div>
    </section>
  `;

  const packsBlock = packs.length === 0 ? "" : `
    <section class="panel">
      <div class="panel-header">
        <h2>Packs (${packs.length})</h2>
        <span class="panel-sub">Tap a pack for cells, temps, firmware</span>
      </div>
      <div class="pack-grid">
        ${packs.map((p) => _renderPackMiniCard(p)).join("")}
      </div>
    </section>
  `;

  return summary + packsBlock + buildHistoryBlock(dev, "soc_pct");
}

function _renderPackMiniCard(pack) {
  const l = pack.latest || {};
  // BMSes don't always report soc_pct directly; rely on what the
  // driver surfaces. Voltage is universal, show it as the primary.
  const v = l.voltage_v;
  const a = l.current_a;
  const drift = l.cell_drift_v != null ? Math.round(l.cell_drift_v * 1000) : null;
  // Average pack temp if temperature_0_c .. temperature_N_c exist.
  const temps = Object.entries(l)
    .filter(([k, v]) => /^temperature_\d+_c$/.test(k) && typeof v === "number")
    .map(([, v]) => v);
  const tAvg = temps.length ? temps.reduce((a, b) => a + b, 0) / temps.length : null;

  // Cell drift colour: <30mV green, 30-80 amber, >80 red. LFP packs
  // should stay well under 30mV at rest.
  let driftCls = "pack-drift-ok";
  if (drift != null) {
    if (drift > 80) driftCls = "pack-drift-bad";
    else if (drift > 30) driftCls = "pack-drift-warn";
  }

  return `
    <a class="pack-card" href="#/device/${encodeURIComponent(pack.label)}">
      <div class="pack-card-head">
        <span class="pack-card-name">${escHtml(dispName(pack))}</span>
        <span class="pack-card-soc">${l.soc_pct != null ? fmt.num(l.soc_pct, 0) + "%" : "·"}</span>
      </div>
      <div class="pack-card-meta">
        <span>${v != null ? fmt.num(v, 2) + " V" : "·"}</span>
        <span>${a != null ? fmt.signed(a, 1) + " A" : "·"}</span>
        ${tAvg != null ? `<span>${fmt.num(tAvg, 0)} °C</span>` : ""}
      </div>
      ${drift != null
        ? `<div class="pack-card-drift ${driftCls}">cell drift ${drift} mV</div>`
        : `<div class="pack-card-drift pack-drift-na">cell drift unavailable</div>`}
    </a>
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
  // #297-4 defense-in-depth, escape driver-supplied metric keys
  // even though they're code-defined today, in case a restored DB
  // surfaces an attacker-supplied row through latest.* somehow.
  select.innerHTML = keys.map(k => `<option value="${escHtml(k)}">${escHtml(prettyKey(k))}</option>`).join("");
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
      // Per-window breakdown table, only shown on the device detail page
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
