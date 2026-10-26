// WattPost UI, part 07: routing and the settings panel.
// Plain script (no modules): the numbered files in web/js/ are loaded in order by
// index.html and share one global scope, exactly as the old single app.js did.

// ---------- routing ----------
// Hash-based. Default route = dashboard. Two forms:
//   #/                   → named routes (dashboard / history / devices / ...)
//   #/device/<label>     → per-device detail page (dispatched by device kind)
const VALID_ROUTES = new Set(["dashboard", "history", "devices", "setup", "settings", "kiosk", "docs"]);

// Routes that mutate config + therefore require an authed session.
// Anonymous LAN viewers (kiosks, family members on the WiFi) get
// dashboard / history / devices / docs / kiosk for free; the moment
// they hit Settings or Setup they're bounced to /login. Backend
// mutation endpoints stay gated regardless, this is a UX guard,
// not a security boundary on its own.
const AUTH_GATED_ROUTES = new Set(["settings", "setup"]);

// Cached auth state, populated by wireHeaderAuth's auth-status
// fetch, refreshed on logout. setRoute() reads this when gating
// AUTH_GATED_ROUTES; null = unknown (treat as anonymous to be safe).
let _authState = null;
function _setAuthState(authed, origin) {
  _authState = { authed, origin: origin || null };
}
window._setAuthState = _setAuthState;  // for wireHeaderAuth handoff

// First-run discovery banner. When the appliance is still on its
// auto-generated password (auth-status → first_run:true) and the
// viewer isn't authed, nudge them to claim it in-browser so they never
// have to fish the random password out of the MOTD / docker logs.
// Dismissible for the session.
let _firstRunBannerShown = false;
function showFirstRunBanner() {
  if (_firstRunBannerShown) return;
  try { if (sessionStorage.getItem("wp-firstrun-dismissed") === "1") return; } catch (_) {}
  _firstRunBannerShown = true;
  const bar = document.createElement("div");
  bar.className = "first-run-banner";
  bar.innerHTML =
    '<span class="frb-ico">' + (typeof _ico === "function" ? _ico("lock") : "") + '</span>' +
    '<span class="frb-text">Secure this appliance — set a password to protect Settings.</span>' +
    '<a class="frb-cta" href="/login?next=/%23/settings">Set password</a>' +
    '<button class="frb-x" type="button" aria-label="Dismiss">&times;</button>';
  bar.querySelector(".frb-x").addEventListener("click", () => {
    try { sessionStorage.setItem("wp-firstrun-dismissed", "1"); } catch (_) {}
    bar.remove();
  });
  document.body.insertBefore(bar, document.body.firstChild);
}

// Settings sub-routes (#328). Menu landing when sub is null; each
// sub renders only its own block group via CSS on body[data-route-sub].
const SETTINGS_SUBROUTES = new Set([
  "devices", "alerts", "display", "network", "integrations",
  "privacy", "backup", "advanced", "diagnostics", "about",
]);

// Section dropdown (replaces the old menu-landing drill-down). Entering
// Settings lands on DEFAULT_SETTINGS_SUB; the dropdown jumps between
// sections. Order + labels + icons live here.
// icon = key into _ICO (lucide line icons), rendered via _ico() at draw time.
const SETTINGS_SECTIONS = [
  { id: "devices",      label: "Add devices",        icon: "plug",    sub: "Pair Renogy, Victron and BMS gear" },
  { id: "alerts",       label: "Alerts",             icon: "bell",    sub: "Rules, transports, quiet hours" },
  { id: "display",      label: "Display",            icon: "monitor", sub: "Theme and kiosk display" },
  { id: "network",      label: "Network",            icon: "wifi",    sub: "WiFi, hotspot, IP address, firewall" },
  { id: "integrations", label: "Integrations",       icon: "link",    sub: "WattPost cloud, Solcast, weather, MQTT" },
  { id: "privacy",      label: "Privacy & sharing",  icon: "lock",    sub: "Web password, kiosk links, location, telemetry" },
  { id: "backup",       label: "Backup & restore",   icon: "drive",   sub: "Download, restore, schedule" },
  { id: "advanced",     label: "Advanced",           icon: "sliders", sub: "History and polling" },
  { id: "diagnostics",  label: "Diagnostics",        icon: "activity", sub: "Device health, logs, support bundle" },
  { id: "about",        label: "About",              icon: "info",    sub: "Status, updates, reset" },
];
const DEFAULT_SETTINGS_SUB = "devices";

// Settings landing menu (cloud-style): a list of section rows, each
// linking to its focused #/settings/<sub> view.
function renderSettingsMenu() {
  const host = document.getElementById("settings-menu-list");
  if (!host) return;
  host.innerHTML = SETTINGS_SECTIONS.map(s => `
    <a class="set-menu-row" href="#/settings/${s.id}">
      <span class="set-menu-icon">${_ico(s.icon)}</span>
      <span class="set-menu-body">
        <span class="set-menu-title">${escHtml(s.label)}</span>
        <span class="set-menu-sub">${escHtml(s.sub || "")}</span>
      </span>
      <svg class="set-menu-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 6 15 12 9 18"/></svg>
    </a>`).join("");
}

function parseRoute() {
  const raw = (window.location.hash || "").replace(/^#\/?/, "").trim();
  const m = raw.match(/^device\/(.+)$/);
  if (m) return { name: "device", label: decodeURIComponent(m[1]) };
  // docs/<slug>, strip the slug into a separate field.
  const d = raw.match(/^docs\/(.+)$/);
  if (d) return { name: "docs", slug: d[1] };
  if (raw === "docs") return { name: "docs", slug: null };
  // settings or settings/<sub>
  const s = raw.match(/^settings\/(.+)$/);
  if (s) {
    const sub = SETTINGS_SUBROUTES.has(s[1]) ? s[1] : null;
    return { name: "settings", sub };
  }
  return { name: VALID_ROUTES.has(raw) ? raw : "dashboard" };
}
function currentRouteName() { return parseRoute().name; }

function setRoute(_unused) {
  const route = parseRoute();
  if (CLOUD_VIEW && (route.name === "setup" || route.name === "settings")) {
    window.location.hash = "#/";
    return;
  }
  // Auth gate: redirect to /login when an unauthed visitor tries to
  // open Settings or Setup. Demo mode skips (no auth at all). If we
  // haven't yet resolved the auth state (race with the bootstrap
  // /api/system/auth-status fetch), fall back to a quick synchronous
  // check via fetch, UX cost is small (one round-trip on first nav
  // to settings).
  if (AUTH_GATED_ROUTES.has(route.name)
      && !document.body.classList.contains("is-demo")) {
    if (_authState && !_authState.authed) {
      window.location.href = "/login?next=/%23/" + encodeURIComponent(route.name);
      return;
    }
    if (_authState === null) {
      // Defer the route render until we know. Bounce home meanwhile
      // so the user isn't staring at a partially-rendered Settings
      // pane that's about to be replaced.
      fetch("/api/system/auth-status", { credentials: "same-origin" })
        .then((r) => r.ok ? r.json() : null)
        .then((data) => {
          _setAuthState(!!(data && data.authed));
          if (!_authState.authed) {
            window.location.href = "/login?next=/%23/" + encodeURIComponent(route.name);
          } else {
            setRoute();  // re-enter now that we know
          }
        })
        .catch(() => {
          // Network error, assume unauthed and bounce.
          window.location.href = "/login?next=/%23/" + encodeURIComponent(route.name);
        });
      return;
    }
  }

  // Mirror current route onto <body> so route-conditional CSS (e.g.
  // hiding the help FAB on /docs) has a hook without needing JS.
  document.body.dataset.route = route.name;
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
    requestAnimationFrame(() => { refreshEnergyOverview(); refreshChart(); refreshHeatmap(); });
  }
  if (route.name === "settings") {
    // Cloud-style: #/settings shows a menu list; #/settings/<sub> opens a
    // focused section view (back link + heading + that section's cards).
    const menu = document.getElementById("settings-menu");
    const section = document.getElementById("settings-section");
    if (!route.sub) {
      delete document.body.dataset.routeSub;
      if (menu) menu.hidden = false;
      if (section) section.hidden = true;
      renderSettingsMenu();
      stopDiagTimer();
    } else {
      document.body.dataset.routeSub = route.sub;
      if (menu) menu.hidden = true;
      if (section) section.hidden = false;
      const meta = SETTINGS_SECTIONS.find(s => s.id === route.sub) || SETTINGS_SECTIONS[0];
      const t = document.getElementById("settings-section-title");
      const su = document.getElementById("settings-section-sub");
      if (t) t.textContent = meta.label;
      if (su) su.textContent = meta.sub || "";
      renderSettings();
      startDiagTimer();
    }
  } else {
    delete document.body.dataset.routeSub;
    stopDiagTimer();
  }
  if (route.name === "dashboard") refreshDriftSparkline();
  if (route.name === "device") renderDeviceDetail(route.label);
  if (route.name === "setup") onEnterSetup();
  if (route.name === "docs")  onEnterDocs(route.slug);
  if (route.name === "kiosk") onEnterKiosk();
  else if (document.body.classList.contains("kiosk-active")) onLeaveKiosk();
  // Reset scroll on route change. On desktop the document scrolls; on mobile
  // the app-shell makes <main> the scroll container, so reset that too.
  window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
  document.querySelector("main")?.scrollTo({ top: 0 });
}

window.addEventListener("hashchange", () => setRoute(currentRouteName()));

// White-label branding, applied once at page load. Cached via the
// cloud heartbeat into the appliance kv table; /api/branding hands
// it back here. When unset (Hobby/Pro accounts, or no cloud pair),
// stays on the default WattPost mark. Re-renders on next page load
// after a heartbeat picks up changes (no live hot-swap needed,
// branding updates are rare).
(async function applyBranding() {
  let b;
  try {
    const r = await fetch("/api/branding");
    if (!r.ok) return;
    b = await r.json();
  } catch (_) { return; }
  if (!b || (!b.brand_name && !b.brand_logo_url)) return;
  if (b.brand_name) {
    const title = document.getElementById("app-brand-title");
    if (title) title.textContent = b.brand_name;
    document.title = b.brand_name;
  }
  if (b.brand_logo_url) {
    const def = document.getElementById("app-brand-logo-default");
    const cus = document.getElementById("app-brand-logo-custom");
    if (def && cus) {
      cus.src = b.brand_logo_url;
      cus.alt = b.brand_name || "Logo";
      cus.hidden = false;
      def.hidden = true;
    }
  }
})();

// Set the Daemon row based on whatever poll data we have. Pulled out
// of renderSettings() so applySnapshot can call it too, otherwise the
// Settings tab opens before the first snapshot, shows "starting…",
// and never refreshes until the user navigates away and back.
function renderDaemonStatus() {
  const el = document.getElementById("settings-daemon");
  if (!el) return;
  if (lastRun) {
    el.textContent = lastRun.errors_count === 0 ? "running, healthy" : "running, errors";
  } else {
    el.textContent = "starting…";
  }
}

// ---------- settings panel ----------
function renderSettings() {
  if (lastRun) {
    $("#settings-last-poll").textContent =
      `${lastRun.elapsed_ms} ms · ${fmt.ago(lastRun.ts)}`;
    $("#settings-errors").textContent = String(lastRun.errors_count);
  }
  // Daemon status implied from the same source as the header pill.
  // First-paint race: if Settings opens before the first poll has
  // landed, lastRun is null. Show "starting…" rather than the legacy
  // "no data" which read as a fault to users. applySnapshot() also
  // calls renderDaemonStatus() when the first poll arrives so the
  // value updates without a tab switch.
  renderDaemonStatus();
  // MQTT export now has its own row in Settings → Integrations; the old
  // "see config.yaml" placeholder was removed when that UI shipped.
  refreshAlertsPanel();
  refreshSystemInfo();
  refreshIntegrationsPanel();
  wireBackupRestore();
  refreshBackupSchedule();
  wireBackupRunNow();
  refreshDiscoveryToggle();
  refreshLocalTelemetryToggle();  // #217, anonymous install ping (off by default)
  refreshHistorySettings();  // #172, editable poll interval + retention
  refreshDeviceHealth();     // SoC temp / throttle / disk / mem / load / uptime
  refreshHostHealthChart();  // host-health history chart (CPU temp / mem / disk + events)
  refreshSolarPauseSettings();  // #163, solar-aware charger pause
  refreshLocationPanel();    // #263/#264, share-with-cloud toggle
  refreshGpsControl();       // #125, USB GPS enable/disable
  refreshHotspotPanel();     // Pillar 3, appliance-as-WiFi-AP
  wireWifiPanel();           // join a home WiFi from the dashboard (scan + connect)
  refreshNetworkPanel();     // set a static IP / DHCP on the live connection
  wireResetToDefaults();     // #138, Danger zone
}

// Hotspot panel (Pillar 3). Reads /api/hotspot/status for live AP
// state, pre-fills the form, and wires Save (PUT config) + the manual
// on/off buttons. Password field stays blank on load so we never echo
// the stored passphrase; a blank Save keeps the existing one.
