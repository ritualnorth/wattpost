// WattPost UI, part 08b: device health, history and backup settings, system info and updates.
// Plain script (no modules): the numbered files in web/js/ are loaded in order by
// index.html and share one global scope, exactly as the old single app.js did.

// History & polling editor (#172). Pull current values, fill the form,
// wire Save and Reset buttons. Values apply live; the Save → PATCH
// also persists to config.yaml so a daemon restart doesn't revert.
async function refreshDeviceHealth() {
  let d;
  try { d = await api("/api/system/device-health"); }
  catch (e) { return; }
  const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
  const soc = d.soc || {}, disk = d.disk || {}, mem = d.memory || {}, la = d.loadavg || {};
  const fmtB = (b) => b == null ? "·"
    : (b >= 1e9 ? (b / 1e9).toFixed(1) + " GB" : (b / 1e6).toFixed(0) + " MB");
  set("#dh-temp", soc.temp_c != null ? soc.temp_c.toFixed(1) + " °C" : "n/a");
  let pw = "normal";
  if (soc.under_voltage_now)       pw = "⚠ under-voltage now";
  else if (soc.throttled_now)      pw = "⚠ throttling now";
  else if (soc.under_voltage_ever) pw = "under-voltage seen since boot";
  else if (soc.throttled_ever)     pw = "throttled since boot";
  else if (soc.temp_c == null)     pw = "n/a";
  set("#dh-throttle", pw);
  set("#dh-disk", disk.percent != null
    ? disk.percent.toFixed(0) + "% · " + fmtB(disk.free_bytes) + " free" : "·");
  set("#dh-mem", mem.percent != null
    ? mem.percent.toFixed(0) + "% · " + fmtB(mem.used_bytes) + " of " + fmtB(mem.total_bytes) : "·");
  set("#dh-load", la.load_1m != null
    ? la.load_1m.toFixed(2) + (la.cpu_count ? " / " + la.cpu_count + " cores" : "") : "·");
  const up = d.uptime_seconds;
  if (up != null) {
    const dd = Math.floor(up / 86400), hh = Math.floor((up % 86400) / 3600), mm = Math.floor((up % 3600) / 60);
    set("#dh-uptime", dd > 0 ? `${dd}d ${hh}h` : (hh > 0 ? `${hh}h ${mm}m` : `${mm}m`));
  }
}

// ── Host-health history chart ───────────────────────────────────────
// Reads /api/system/host-history (CPU temp + memory + disk over the last
// 24h, with under-voltage/throttle event flags) and renders it under the
// Device-health card. Same uPlot recipe as the energy chart.
let hostHealthChart = null;

async function refreshHostHealthChart() {
  const root = document.getElementById("dh-chart");
  if (!root) return;
  let d;
  try { d = await api("/api/system/host-history?hours=24"); }
  catch (e) { return; }
  drawHostHealthChart(root, d);
  renderHostHealthEvents(d);
}

function drawHostHealthChart(root, payload) {
  if (hostHealthChart) { hostHealthChart.destroy(); hostHealthChart = null; }
  const ts = (payload && payload.ts) || [];
  if (!ts.length) {
    root.innerHTML = `<div style="padding:.8rem;color:var(--text-3);font-size:.85rem;">No system-health samples yet &mdash; give it a few minutes of polling.</div>`;
    return;
  }
  const s = payload.series || {};
  const pal = chartPalette();
  const fmtC   = (v) => v == null ? "·" : v.toFixed(1) + " °C";
  const fmtPct = (v) => v == null ? "·" : v.toFixed(0) + " %";
  const opts = {
    width: Math.max(root.clientWidth, 320), height: 240,
    cursor: { drag: { x: true, y: false } },
    scales: { x: { time: true }, temp: { auto: true }, pct: { range: [0, 100], auto: false } },
    series: [
      {},
      { label: "CPU temp", stroke: "#ff8c42", width: 1.6, scale: "temp", value: (_u, v) => fmtC(v) },
      { label: "Memory",   stroke: "#5b9bd5", width: 1.4, scale: "pct",  value: (_u, v) => fmtPct(v) },
      { label: "Disk",     stroke: "#9aa3ad", width: 1.0, scale: "pct",  value: (_u, v) => fmtPct(v) },
    ],
    axes: [
      { stroke: pal.axis, grid: { stroke: pal.grid }, ticks: { stroke: pal.gridStrong }, space: 50, size: 36 },
      { stroke: pal.axis, grid: { stroke: pal.grid }, scale: "temp",
        values: (_u, sp) => sp.map(v => v == null ? "" : v.toFixed(0) + "°") },
      { stroke: pal.axis, scale: "pct", side: 1, grid: { show: false },
        values: (_u, sp) => sp.map(v => v == null ? "" : v.toFixed(0) + "%") },
    ],
    legend: { show: true },
    plugins: [valueTooltipPlugin()],
  };
  const cols = [ts, s.cpu_temp_c || [], s.mem_used_pct || [], s.disk_used_pct || []];
  try {
    hostHealthChart = new uPlot(opts, cols, root);
  } catch (e) {
    console.error("uPlot host-health failed:", e);
    root.innerHTML = `<div style="padding:.8rem;color:var(--red);font-size:.85rem;">Chart render failed: ${e.message}</div>`;
  }
}

function renderHostHealthEvents(payload) {
  const el = document.getElementById("dh-events");
  if (!el) return;
  const s = (payload && payload.series) || {};
  const count = (arr) => (arr || []).reduce((n, v) => n + (v ? 1 : 0), 0);
  const uv = count(s.under_voltage), th = count(s.throttled);
  if (!uv && !th) {
    el.innerHTML = `<span style="color:var(--green,#56d364)">No under-voltage or throttle events in the last 24h.</span>`;
    return;
  }
  const parts = [];
  if (uv) parts.push(`${uv} under-voltage`);
  if (th) parts.push(`${th} throttle`);
  el.innerHTML = `<span style="color:var(--amber,#f0b849)">⚠ ${parts.join(" · ")} event${(uv + th) > 1 ? "s" : ""} in the last 24h &mdash; check the power supply / cooling.</span>`;
}

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
      if (status) status.textContent = "Defaults loaded, click Save to apply.";
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
// idempotent, safe to call on every settings render.
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

// Anonymous install ping (#217). Off by default; mirrors the discovery
// telemetry consent pattern. No pairing required — the ping rides the
// daily update check (which hits the anonymous release manifest).
async function refreshLocalTelemetryToggle() {
  const btn = document.getElementById("localtele-toggle");
  const msg = document.getElementById("localtele-toggle-msg");
  if (!btn || !msg) return;
  let state;
  try {
    state = await api("/api/system/local-telemetry");
  } catch (_) {
    btn.hidden = true;
    msg.textContent = "·";
    return;
  }
  btn.hidden = false;
  btn.dataset.enabled = state.enabled ? "1" : "0";
  btn.textContent = state.enabled ? "Disable install ping" : "Enable install ping";
  msg.textContent = state.enabled
    ? "On. The daily update check includes an anonymous install ID."
    : "Off. No install ID leaves this appliance.";
  if (btn.dataset.wired === "1") return;
  btn.dataset.wired = "1";
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    const next = btn.dataset.enabled !== "1";
    try {
      const r = await fetch("/api/system/local-telemetry/toggle", {
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
      await refreshLocalTelemetryToggle();
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
    // Selective restore (#26): which parts to bring back. All ticked (the
    // default) = a full restore, and we omit the query for back-compat.
    const comps = [];
    if (document.getElementById("restore-comp-data")?.checked) comps.push("data");
    if (document.getElementById("restore-comp-config")?.checked) comps.push("config");
    if (document.getElementById("restore-comp-password")?.checked) comps.push("password");
    if (comps.length === 0) {
      if (msg) msg.textContent = "Tick at least one thing to restore.";
      input.value = "";
      return;
    }
    const _lbl = { data: "history", config: "configuration", password: "dashboard password" };
    if (!window.confirm(
      `Restore ${file.name} (${human})?\n\n` +
      `This will OVERWRITE: ${comps.map(c => _lbl[c]).join(", ")}, then ` +
      "restart the daemon. The previous config is saved as " +
      "config.yaml.restored.bak in case you need to back out."
    )) {
      input.value = "";
      return;
    }
    if (msg) msg.textContent = `Uploading ${human}…`;
    try {
      const qs = comps.length < 3 ? `?components=${comps.join(",")}` : "";
      const r = await fetch(_withKiosk("/api/system/restore" + qs), {
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
  const next = s.next_run_ts ? fmt.rel(s.next_run_ts) : "·";
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
  renderUpdateChannel(u.channel || "stable", isDocker);

  // "Latest available" row is ALWAYS visible, gives users a positive
  // confirmation either way (the previous behaviour silently hid the
  // row when up-to-date, which read as "data missing"). Two shapes:
  //   has_update + latest_version → "v0.1.34 available" + Release-notes link
  //   otherwise                   → "Up to date" (no link, no apply btn)
  // The version-known guard still matters for the transient first-boot
  // window where has_update=true but latest_version=null, we then
  // render "Checking…" instead of the misleading "v—".
  const row = $("#settings-update-row");
  if (row) row.hidden = false;
  const pendingUpdate = u.has_update && !!u.latest_version;
  const a = $("#settings-update-link");
  if (pendingUpdate) {
    set("#settings-update-latest", "v" + u.latest_version + " available");
    if (a) {
      // Always route to the in-app hash route, the appliance
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

  // Apply button: shown whenever there's a pending update AND the box
  // can apply it in-UI. Pi always can (slot-swap). Docker can iff the
  // wattpost-updater sidecar is configured (updater_available) — then
  // the button fires the sidecar's pull+recreate, no `docker compose
  // pull` needed. One-click on both, cloud or no cloud.
  const canApply = u.updater_available !== false;  // default true (older daemons)
  const applyBtn = $("#settings-update-apply");
  if (applyBtn) applyBtn.hidden = !(pendingUpdate && canApply);

  // The "docker compose pull…" hint only when a Docker box has a
  // pending update but NO sidecar to apply it for them.
  const dockerRow = $("#settings-update-docker-row");
  if (dockerRow) dockerRow.hidden = !(isDocker && pendingUpdate && !canApply);

  // Force-hide the in-flight progress row whenever refreshUpdateState
  // runs. The apply handler is the ONLY thing that should ever unhide
  // it, previously, the row could get stuck visible after a daemon
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

// Release-channel selector (#11). Reflects the channel the appliance is
// following and renders the pre-release warning. Stable shows no note;
// beta/edge warn. Edge has no per-commit Pi image, so on a Pi install we
// add a caveat that the apply path falls back to beta-equivalent.
function renderUpdateChannel(channel, isDocker) {
  const sel = document.getElementById("settings-channel-select");
  if (sel && sel.value !== channel) sel.value = channel;
  const noteRow = document.getElementById("settings-channel-note-row");
  const note = document.getElementById("settings-channel-note");
  if (!noteRow || !note) return;
  let text = "";
  if (channel === "beta") {
    text = "Beta — release candidates the moment they're cut, before "
         + "they've soaked. May be unstable. Great for helping test; "
         + "switch back to Stable any time.";
  } else if (channel === "edge") {
    text = "Edge — every commit to main, unfiltered. Expect breakage.";
    if (!isDocker) {
      text += " Edge images are Docker-only; on this Pi the update "
            + "check tracks edge but an in-place apply uses the latest "
            + "beta build.";
    }
  }
  if (text) {
    note.textContent = text;
    noteRow.hidden = false;
  } else {
    note.textContent = "";
    noteRow.hidden = true;
  }
}

document.getElementById("settings-channel-select")?.addEventListener("change", async (e) => {
  const sel = e.currentTarget;
  const channel = sel.value;
  const saving = document.getElementById("settings-channel-saving");
  sel.disabled = true;
  if (saving) saving.textContent = "saving…";
  try {
    const r = await fetch("/api/system/update/channel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ channel }),
    });
    if (!r.ok) {
      let detail = `HTTP ${r.status}`;
      try { const j = await r.json(); detail = j.detail || detail; } catch {}
      if (saving) saving.textContent = "failed: " + detail;
      // Re-sync the selector to the actual server state.
      await refreshUpdateState();
      return;
    }
    if (saving) saving.textContent = "saved";
    // The POST already fired a fresh manifest check server-side; pull
    // the new state so "Latest available" reflects the new channel.
    await refreshUpdateState();
    setTimeout(() => { if (saving) saving.textContent = ""; }, 2500);
  } catch (err) {
    if (saving) saving.textContent = "failed: " + (err.message || err);
    await refreshUpdateState();
  } finally {
    sel.disabled = false;
  }
});

// Alert banner expand/collapse, toggles the detail panel + the chevron
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
    // pressed/highlighted state after the action completes, users
    // see the colour stuck and assume the button is still busy.
    btn.blur();
  }
});

// Update-now: fire-and-poll. /api/system/update/apply backgrounds the
// helper and 202s immediately; we then poll /api/system/update/log
// every 2s to surface progress. install.sh restarts the daemon mid-
// flight so /api/system/update/log will briefly fail to connect, we
// tolerate that and resume polling once the daemon comes back up.
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
        // ~80s of unreachable daemon, give up auto-polling and let
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
