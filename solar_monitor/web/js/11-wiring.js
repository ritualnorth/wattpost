// WattPost UI, part 11: boot wiring: theme/kiosk toggles, settings handlers, kiosk share URL.
// Plain script (no modules): the numbered files in web/js/ are loaded in order by
// index.html and share one global scope, exactly as the old single app.js did.

// ---------- helpers used by the wiring below (declared here so they exist before it runs) ----------
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
    host.textContent = "(no log lines captured yet. Daemon just started)";
    return;
  }
  // Escape HTML, log lines may contain < > & from tracebacks etc.
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

// ---------- docs (markdown topics) ----------
// Tiny markdown renderer, handles the subset our bundled docs use:
// # ## ### headings, **bold**, *italic*, `inline code`, ```fences```,
// - / * / 1. lists, > blockquotes, --- rules, [text](url) links,
// pipe tables, and plain paragraphs. Escapes HTML in source text so
// the docs themselves can show <tags> safely.
function escHtml(s) {
  return String(s).replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
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

// ---------- wiring ----------
// Theme picker, System / Dark / Light.
document.querySelectorAll(".theme-opt").forEach(btn => {
  btn.addEventListener("click", () => applyTheme(btn.dataset.themePref));
});
applyTheme(themePref());  // paints meta-color + button selection state

// Kiosk default toggle + exit button. The default-on-this-device flag
// only triggers a redirect if the user landed without an explicit route
// in the URL, otherwise an inbound link to /#/history or /#/devices
// would be silently stomped on every refresh.
const kioskToggle = $("#kiosk-default-toggle");
if (kioskToggle) {
  kioskToggle.checked = kioskDefault();
  kioskToggle.addEventListener("change", () => {
    setKioskDefault(kioskToggle.checked);
  });
}
// Kiosk skin selector — PATCHes the appliance (travels to the wall display
// + cloud kiosk). Value reflects the appliance config once it has loaded.
const kioskSkinSelect = $("#kiosk-skin-select");
if (kioskSkinSelect) {
  kioskSkinSelect.value = activeSkinId();
  kioskSkinSelect.addEventListener("change", () => {
    setKioskSkin(kioskSkinSelect.value);
  });
}
// "Open the local display in kiosk" is now an APPLIANCE-side setting
// (#28), so it's meaningful from a cloud session too — setting it there
// PATCHes the appliance and the local wall display obeys. The cloud tab
// itself never auto-kiosks (the boot redirect is gated on !CLOUD_VIEW),
// so showing the toggle here is safe and is the whole point of the fix.
const kioskExitBtn = $("#kiosk-exit");
if (kioskExitBtn) {
  // Hide Exit Kiosk for two cases:
  //   - kiosk-token share URL (#150): visitor has no session at
  //     all, would land in the dashboard with no auth and see
  //     401s everywhere, bad UX.
  // The Exit button still renders on direct local access
  // (192.168.x.x / wattpost.local) where the van/cabin operator
  // genuinely wants to flip between SoC kiosk and the full UI.
  if (KIOSK_KEY_PARAM) {
    kioskExitBtn.hidden = true;
  }
  kioskExitBtn.addEventListener("click", () => {
    if (KIOSK_KEY_PARAM) {
      window.location.href = "/";
    } else {
      window.location.hash = "#/";
    }
  });
}
const restartBtn = $("#restart-daemon-btn");
if (restartBtn) {
  restartBtn.addEventListener("click", restartDaemon);
}
// Rotate web password, Settings → System → "Rotate web password".
// Generates a fresh ~16-char random password on the appliance and
// shows it once. Docker users specifically asked for this, they
// don't have wattpost-config TUI access on the host. Old hash is
// replaced atomically; existing sessions on OTHER browsers stay
// valid until they natural-expire (so you don't sign yourself out
// of the tab you're rotating from).
const rotatePwBtn = $("#rotate-pw-btn");
if (rotatePwBtn) {
  rotatePwBtn.addEventListener("click", async () => {
    if (!confirm(
      "Rotate the local web password?\n\n" +
      "You'll be shown the new password ONCE. Save it before " +
      "closing this dialog. Existing browser sessions stay valid " +
      "until they expire (30 days)."
    )) return;
    const out = document.getElementById("rotate-pw-result");
    rotatePwBtn.disabled = true;
    try {
      const r = await fetch("/api/system/web-password/rotate", {
        method: "POST", credentials: "include",
      });
      if (!r.ok) {
        const t = await r.text();
        throw new Error(`HTTP ${r.status}: ${t}`);
      }
      const j = await r.json();
      if (out) {
        out.hidden = false;
        out.innerHTML = `
          <div class="rotate-pw-label">New password. Save it now:</div>
          <code class="rotate-pw-code">${j.password.replace(/[<&>]/g, c => ({"<":"&lt;","&":"&amp;",">":"&gt;"}[c]))}</code>
          <button class="rotate-pw-copy" type="button">Copy</button>
          <div class="rotate-pw-foot">
            Also written to <code>/etc/wattpost/web-password</code>
            inside the container (host bind-mount on Docker installs).
          </div>`;
        out.querySelector(".rotate-pw-copy")?.addEventListener("click", (e) =>
          copyToClipboard(j.password, e.currentTarget));
      }
    } catch (e) {
      alert("Couldn't rotate the password:\n\n" + (e.message || e));
    } finally {
      rotatePwBtn.disabled = false;
    }
  });
}

// Kiosk display links (cloud #15). Mint a revocable, read-only kiosk
// token, shown once as a full /kiosk?token=… URL; list + revoke the
// existing ones. Lets off-grid wall displays in once login is mandatory.
// Copy-to-clipboard that works on the appliance's plain-http LAN origin.
// navigator.clipboard is gated to secure contexts (https / localhost), so on
// http://<lan-ip> it's undefined and a naive writeText silently no-ops — which
// is why the Copy buttons did nothing on the box (and people then hand-copied
// a long kiosk token, missed a character, and the link 302'd). Fall back to a
// hidden-textarea + execCommand('copy'), and give the button visual feedback.
function _legacyCopy(text) {
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (_) { return false; }
}
function copyToClipboard(text, btn) {
  const flash = (msg) => {
    if (!btn) return;
    const orig = btn.textContent;
    btn.textContent = msg;
    setTimeout(() => { btn.textContent = orig; }, 1600);
  };
  const ok = () => flash("Copied");
  const fail = () => flash("Press ⌘/Ctrl+C");
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(ok, () => { _legacyCopy(text) ? ok() : fail(); });
    return;
  }
  _legacyCopy(text) ? ok() : fail();
}
const _escHtmlMini = (s) => String(s).replace(/[<&>]/g, c => ({"<":"&lt;","&":"&amp;",">":"&gt;"}[c]));
async function loadKioskTokens() {
  const host = document.getElementById("kiosk-token-list");
  if (!host) return;
  try {
    const r = await fetch("/api/system/kiosk-tokens", { credentials: "include" });
    if (!r.ok) { host.innerHTML = ""; return; }
    const { tokens } = await r.json();
    if (!tokens || !tokens.length) {
      host.innerHTML = `<div class="kiosk-token-empty">No kiosk links yet.</div>`;
      return;
    }
    const _skinLabel = (s) => ({ halo: "Halo", ember: "Ember", command: "Command" }[s] || "");
    host.innerHTML = tokens.map(t => {
      const sk = _skinLabel((t.skin || "").toLowerCase());
      return `
      <div class="kiosk-token-row">
        <span class="kiosk-token-name">${_escHtmlMini(t.name || "Kiosk")}${sk ? ` <span class="kiosk-token-skin" style="color:var(--text-3)">· ${_escHtmlMini(sk)}</span>` : ""}</span>
        <button class="kiosk-token-revoke" type="button" data-id="${_escHtmlMini(t.id)}">Revoke</button>
      </div>`;
    }).join("");
    host.querySelectorAll(".kiosk-token-revoke").forEach(b =>
      b.addEventListener("click", async () => {
        if (!confirm("Revoke this kiosk link? The display using it will stop working.")) return;
        try {
          await fetch(`/api/system/kiosk-tokens/${encodeURIComponent(b.dataset.id)}`,
                      { method: "DELETE", credentials: "include" });
        } catch (_) {}
        loadKioskTokens();
      }));
  } catch (_) { host.innerHTML = ""; }
}
const kioskCreateBtn = $("#kiosk-create-btn");
if (kioskCreateBtn) {
  kioskCreateBtn.addEventListener("click", async () => {
    const name = prompt("Name this display (e.g. Cabin, Van screen):", "Kiosk");
    if (name === null) return;
    // Theme picked for THIS link (empty = appliance default). Bound to the
    // token so each wall display can run a different skin off the one box.
    const skin = document.getElementById("kiosk-skin")?.value || "";
    const skinLabel = { halo: "Halo", ember: "Ember", command: "Command" }[skin] || "Appliance default";
    const out = document.getElementById("kiosk-create-result");
    kioskCreateBtn.disabled = true;
    try {
      const r = await fetch("/api/system/kiosk-tokens", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, skin }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      const url = `${location.origin}${j.kiosk_path}`;
      if (out) {
        out.hidden = false;
        out.innerHTML = `
          <div class="rotate-pw-label">Kiosk link (${_escHtmlMini(skinLabel)}) — open it on the display. Save it now:</div>
          <code class="rotate-pw-code">${_escHtmlMini(url)}</code>
          <button class="rotate-pw-copy" type="button">Copy</button>
          <div class="rotate-pw-foot">Read-only, no login. Works on your local network only. Revoke any time below.</div>`;
        out.querySelector(".rotate-pw-copy")?.addEventListener("click", (e) =>
          copyToClipboard(url, e.currentTarget));
      }
      loadKioskTokens();
    } catch (e) {
      alert("Couldn't create the kiosk link:\n\n" + (e.message || e));
    } finally {
      kioskCreateBtn.disabled = false;
    }
  });
  loadKioskTokens();
}

// Network security toggles (cloud #15, Phase B). Firewall + SSH; Pi only.
// Off-Pi the API reports supported:false — we still let the user set the
// config (it applies once running on a Pi) and surface that clearly.
async function loadNetsec() {
  if (!document.getElementById("settings-netsec-block")) return;
  try {
    const r = await fetch("/api/system/netsec", { credentials: "include" });
    if (!r.ok) return;
    const j = await r.json();
    const fw = document.getElementById("netsec-firewall-toggle");
    const ssh = document.getElementById("netsec-ssh-toggle");
    const uns = document.getElementById("netsec-unsupported");
    if (fw) fw.checked = !!(j.firewall && j.firewall.desired);
    if (ssh) ssh.checked = !!(j.ssh && j.ssh.desired);
    if (uns) uns.hidden = !!j.supported;
  } catch (_) {}
}
async function netsecSet(kind, enabled, el) {
  const msg = document.getElementById("netsec-msg");
  if (el) el.disabled = true;
  if (msg) msg.textContent = "";
  try {
    const r = await fetch(`/api/system/netsec/${kind}`, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
    if (msg) msg.textContent = j.supported ? "Applied." : "Saved — applies when running on a Pi.";
  } catch (e) {
    if (msg) msg.textContent = "Couldn't update: " + (e.message || e);
  } finally {
    if (el) el.disabled = false;
  }
}
const _nsFw = $("#netsec-firewall-toggle");
const _nsSsh = $("#netsec-ssh-toggle");
if (_nsFw) _nsFw.addEventListener("change", () => netsecSet("firewall", _nsFw.checked, _nsFw));
if (_nsSsh) _nsSsh.addEventListener("change", () => {
  if (_nsSsh.checked && !confirm("Enable SSH?\n\nThis starts the SSH server and opens port 22. You still need your own key or user (set in Raspberry Pi Imager) to log in — there's no default login.")) {
    _nsSsh.checked = false; return;
  }
  netsecSet("ssh", _nsSsh.checked, _nsSsh);
});
if (_nsFw || _nsSsh) loadNetsec();
// #292, Make the SoC donut click into the bank detail page.
// The bank device's label is the literal string "bank", set by
// the aggregator. Clicking the donut routes to /#/device/bank
// where buildBankDetail() renders per-pack mini-cards + summary
// stats. cursor:pointer + role=button + ARIA so it's discoverable.
(function wireDonutTap() {
  const wrap = document.getElementById("donut-wrap");
  if (!wrap) return;
  wrap.style.cursor = "pointer";
  wrap.setAttribute("role", "link");
  wrap.setAttribute("aria-label", "Open bank detail");
  wrap.tabIndex = 0;
  const go = () => { window.location.hash = "#/device/bank"; };
  wrap.addEventListener("click", go);
  wrap.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); }
  });
})();

// Sign-out button, lives inside Settings → System (the only place
// where being signed in actually matters; mutations require a
// session, everything else on LAN is anonymous read-only). Hidden
// when the user isn't authed (no session to end). Sign-IN is
// triggered implicitly by tapping Settings or Setup, the SPA
// router (AUTH_GATED_ROUTES) bounces unauthed visitors to /login.
(function wireSignout() {
  const signoutBtn = document.getElementById("signout-btn");
  if (!signoutBtn) return;
  if (document.body.classList.contains("is-demo")) return;

  signoutBtn.addEventListener("click", async () => {
    try {
      await fetch("/api/logout", {
        method: "POST", credentials: "same-origin",
      });
    } catch (_) { /* network error. Still reload */ }
    // Land on the dashboard (read-only anonymous). If the user
    // re-opens Settings the auth gate will bounce them to /login.
    window.location.href = "/";
  });

  // Hide until we know the user is actually signed in. Avoids
  // showing a Sign-out button to an anonymous LAN viewer who's
  // never authed in the first place.
  signoutBtn.hidden = true;
  fetch("/api/system/auth-status", { credentials: "same-origin" })
    .then((r) => r.ok ? r.json() : null)
    .then((data) => {
      const authed = !!(data && data.authed);
      const origin = data && data.origin;
      if (typeof window._setAuthState === "function") window._setAuthState(authed, origin);
      if (data && data.first_run && !authed) showFirstRunBanner();
      if (authed) {
        signoutBtn.dataset.origin = origin || "local";
        signoutBtn.hidden = false;
      }
    })
    .catch(() => { /* leave hidden on network error */ });
})();
const diagRefreshBtn = $("#diag-refresh");
if (diagRefreshBtn) diagRefreshBtn.addEventListener("click", refreshDiagLog);

// ---------- kiosk share URL (Settings → Kiosk share URL) ----------
// Surfaces the per-appliance kiosk_token as a copy-paste-able URL
// + lets the user rotate it (revoke a leaked URL with one click).

// Status pill legend popover, click the pill to open, click outside or
// the close button to dismiss.
// Both the desktop nav pill (#status) and the mobile top-bar pill
// (#status-mobile) open the same status-guide popover.
const statusEls = [$("#status"), $("#status-mobile")].filter(Boolean);
const legendEl = $("#status-legend");
if (statusEls.length && legendEl) {
  const close = () => { legendEl.hidden = true; };
  const open  = () => { legendEl.hidden = false; };
  statusEls.forEach((el) => el.addEventListener("click", (e) => {
    e.stopPropagation();
    legendEl.hidden ? open() : close();
  }));
  legendEl.querySelector(".status-legend-close")?.addEventListener("click", close);
  document.addEventListener("click", (e) => {
    const onAPill = statusEls.some((el) => el === e.target || el.contains(e.target));
    if (!legendEl.hidden && !legendEl.contains(e.target) && !onAPill) {
      close();
    }
  });
  legendEl.querySelector(".status-legend-link")?.addEventListener("click", close);
}
// If the URL path is /kiosk (real server route, hits anonymously even
// when local-auth is on), flip the SPA into kiosk mode by setting the
// hash before initial setRoute runs. Bookmarkable, shareable, and the
// auth middleware whitelists this exact path.
//
// /kiosk/<mode> variant — pin a wall display to a persona (van /
// cabin / marine / home / kiosk) so the layout is tailored. The mode
// lands on <body data-mode="..."> for CSS to react. Unknown modes
// fall back to the standard kiosk view rather than 404'ing.
const _KIOSK_MODES = ["home", "van", "cabin", "marine", "kiosk"];
const _kioskPathMatch = window.location.pathname.match(/^\/kiosk(?:\/([a-z]+))?\/?$/);
if (_kioskPathMatch) {
  const _requestedMode = _kioskPathMatch[1] || "";
  if (_requestedMode && _KIOSK_MODES.includes(_requestedMode)) {
    document.body.dataset.mode = _requestedMode;
  }
  window.location.hash = "#/kiosk";
}
// If the appliance is set to default-to-kiosk and the URL has no explicit
// hash, redirect before the initial setRoute runs (instant via the cached
// flag; loadKioskConfig() below corrects it from the appliance config and
// re-routes if needed). Skipped on the cloud view — only the local
// wall display should auto-kiosk.
else if (
  !CLOUD_VIEW &&
  kioskDefault() &&
  (!window.location.hash || window.location.hash === "#" || window.location.hash === "#/")
) {
  window.location.hash = "#/kiosk";
}

// Pull the appliance-side kiosk config (default + skin) so it reflects what
// was set from anywhere — incl. a cloud session — not just this browser.
loadKioskConfig();

$("#sel-device").addEventListener("change", () => onDeviceChanged());
$("#sel-metric").addEventListener("change", refreshChart);
// Compare-packs toggle: persist the preference and re-render. The
// checkbox is greyed out when the eligibility conditions don't hold
// (need a smart_battery selected + >=2 packs), but we still update
// state so flipping back to an eligible device remembers the choice.
$("#chart-compare-packs")?.addEventListener("change", (e) => {
  compareMode = !!e.target.checked;
  localStorage.setItem("compareMode", compareMode ? "1" : "0");
  refreshChart();
});
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

// Range buttons above the Energy chart, single delegated click
// listener (the buttons are in static HTML so the listener can be
// installed once, no rebinding needed when refreshEnergyOverview
// toggles is-active classes).
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".energy-ranges .erange");
  if (!btn) return;
  const range = btn.dataset.range;
  if (!range) return;
  refreshEnergyOverview(range);
});
