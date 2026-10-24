// WattPost UI, part 04: PV forecast, weather, 7-day outlook, today panel.
// Plain script (no modules): the numbered files in web/js/ are loaded in order by
// index.html and share one global scope, exactly as the old single app.js did.

// ---------- TOMORROW STRIP (PV forecast) ----------
//
// Forecast lives in /api/forecast/pv; we fetch on dashboard load and
// every ~5 min thereafter (poll cadence is hours, no point refreshing
// faster). When no forecast is configured or the cache is empty, the
// panel hides itself entirely so the dashboard isn't cluttered.

let forecastData = null;
let forecastLastFetched = 0;
const FORECAST_REFRESH_MS = 5 * 60 * 1000;

async function ensureForecast(force = false) {
  const now = Date.now();
  if (!force && forecastData && (now - forecastLastFetched) < FORECAST_REFRESH_MS) {
    return forecastData;
  }
  try {
    const f = await api("/api/forecast/pv");
    forecastData = (f?.points?.length) ? f : null;
    forecastLastFetched = now;
  } catch (e) { forecastData = null; }
  return forecastData;
}

// Today's intraday energy series (5-min buckets). Used by the kiosk
// skins' "harvested today" sparkline. Same throttled-cache shape as the
// forecast: the buckets are 5 min wide, so a 5 min refresh is plenty.
let energyTodayData = null;
let energyTodayLastFetched = 0;
const ENERGY_TODAY_REFRESH_MS = 5 * 60 * 1000;

async function ensureEnergyToday(force = false) {
  const now = Date.now();
  if (!force && energyTodayData && (now - energyTodayLastFetched) < ENERGY_TODAY_REFRESH_MS) {
    return energyTodayData;
  }
  try {
    const e = await api("/api/energy/today");
    energyTodayData = (e && e.series) ? e : null;
    energyTodayLastFetched = now;
  } catch (_) { /* keep last good cache */ }
  return energyTodayData;
}

// Average a numeric series down to at most `n` points, preserving shape.
// Keeps the kiosk sparkline path light when today has hundreds of buckets.
function downsampleSeries(arr, n) {
  if (arr.length <= n) return arr.slice();
  const out = [];
  for (let i = 0; i < n; i++) {
    const lo = Math.floor(i * arr.length / n);
    const hi = Math.max(lo + 1, Math.floor((i + 1) * arr.length / n));
    let sum = 0, cnt = 0;
    for (let j = lo; j < hi; j++) { sum += arr[j]; cnt++; }
    out.push(cnt ? sum / cnt : 0);
  }
  return out;
}

// Group forecast points into per-day buckets keyed by midnight epoch
// (local time). Each bucket gets { wh: total energy, peak: {ts, w},
// points: [...] }. Used by the 7-day outlook strip.
function bucketByDay(points) {
  const buckets = new Map();   // dayMid -> bucket
  for (const p of points || []) {
    const d = new Date((p.ts - 900) * 1000);   // slice mid-point
    d.setHours(0, 0, 0, 0);
    const dayMid = Math.floor(d.getTime() / 1000);
    let b = buckets.get(dayMid);
    if (!b) { b = { dayMid, wh: 0, peak: null, points: [] };
              buckets.set(dayMid, b); }
    const w = p.pv_w || 0;
    b.wh += w * 0.5;
    b.points.push({ ts: p.ts, w });
    if (!b.peak || w > b.peak.w) b.peak = { ts: p.ts, w };
  }
  return [...buckets.values()].sort((a, b) => a.dayMid - b.dayMid);
}

// Bucket forecast points by local calendar day; returns
// { todayWh, tomorrowWh, dayAfterWh, tomorrowPeak: {ts, w} | null,
//   tomorrowPoints: [{ts, w}], dayAfterPoints: [...] }
function summariseForecast(points) {
  const out = {
    todayWh: 0, tomorrowWh: 0, dayAfterWh: 0,
    todayPoints: [], todayPeak: null, todayRemainingWh: 0,
    tomorrowPeak: null, tomorrowPoints: [], dayAfterPoints: [],
  };
  if (!points?.length) return out;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const todayMid = today.getTime() / 1000;
  const dayS = 86400;
  const nowS = Math.floor(Date.now() / 1000);
  // Solcast's `period_end` is the END of a 30-min slice, so the
  // slice that ends at exactly 00:00 tomorrow is actually 23:30 of
  // *today*. Bucket by the slice mid-point (ts - 900s) to keep the
  // attribution honest at day boundaries.
  for (const p of points) {
    const ts = p.ts;
    const w = p.pv_w || 0;
    const wh = w * 0.5;
    const bucketTs = ts - 900;
    if (bucketTs >= todayMid && bucketTs < todayMid + dayS) {
      out.todayWh += wh;
      out.todayPoints.push({ ts, w });
      if (!out.todayPeak || w > out.todayPeak.w) out.todayPeak = { ts, w };
      if (ts >= nowS) out.todayRemainingWh += wh;
    } else if (bucketTs >= todayMid + dayS && bucketTs < todayMid + 2*dayS) {
      out.tomorrowWh += wh;
      out.tomorrowPoints.push({ ts, w });
      if (!out.tomorrowPeak || w > out.tomorrowPeak.w) {
        out.tomorrowPeak = { ts, w };
      }
    } else if (bucketTs >= todayMid + 2*dayS && bucketTs < todayMid + 3*dayS) {
      out.dayAfterWh += wh;
      out.dayAfterPoints.push({ ts, w });
    }
  }
  return out;
}

// ---------- CURRENT WEATHER (Open-Meteo) ----------
let weatherData = null;
let weatherLastFetched = 0;
const WEATHER_REFRESH_MS = 5 * 60 * 1000;

async function ensureWeather(force = false) {
  const now = Date.now();
  if (!force && weatherData && (now - weatherLastFetched) < WEATHER_REFRESH_MS) {
    return weatherData;
  }
  try {
    const w = await api("/api/weather/current");
    weatherData = (w && w.provider) ? w : null;
    weatherLastFetched = now;
  } catch (e) { weatherData = null; }
  return weatherData;
}

// WMO weather code → human label + minimal SVG icon. We pick from a
// small palette (clear, partly cloudy, cloud, fog, rain, snow,
// thunder) rather than the full WMO ladder, anything more fine-
// grained than that doesn't read at this size.
const WMO = {
  describe(code, isDay) {
    if (code == null) return "—";
    if (code === 0)                              return isDay === false ? "Clear night" : "Sunny";
    if (code === 1)                              return "Mostly clear";
    if (code === 2)                              return "Partly cloudy";
    if (code === 3)                              return "Overcast";
    if (code === 45 || code === 48)              return "Fog";
    if (code >= 51 && code <= 57)                return "Drizzle";
    if (code >= 61 && code <= 67)                return "Rain";
    if (code >= 71 && code <= 77)                return "Snow";
    if (code >= 80 && code <= 82)                return "Showers";
    if (code === 85 || code === 86)              return "Snow showers";
    if (code >= 95 && code <= 99)                return "Thunderstorm";
    return "—";
  },
  iconSvg(code, isDay) {
    if (code == null) return "";
    // Big-friendly inline icons. Stroke uses currentColor so the
    // panel theme controls tint.
    const sun = `<circle cx="12" cy="12" r="4"/>
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>`;
    const moon = `<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>`;
    const cloud = `<path d="M17 18a4 4 0 0 0 0-8 6 6 0 0 0-11.7 1.8A3.5 3.5 0 0 0 6.5 18z"/>`;
    const partly = `<path d="M16 17a4 4 0 0 0 0-8 6 6 0 0 0-11.5 1.8A3.5 3.5 0 0 0 5.5 17z"/>
      <circle cx="17" cy="6" r="2.4"/>
      <path d="M17 1.5v1.5M22 6h-1.5M19.5 2.5l-1.05 1.05"/>`;
    const rain = cloud + `<path d="M9 21l1-2M13 21l1-2M17 21l1-2"/>`;
    const snow = cloud + `<path d="M10 21l.5-1M14 21l.5-1M18 21l.5-1"/>`;
    const fog  = cloud + `<path d="M4 21h16M6 19h12"/>`;
    const storm = cloud + `<path d="M11 18l-2 3h3l-2 3"/>`;
    let inner;
    if (code === 0)                              inner = isDay === false ? moon : sun;
    else if (code === 1)                         inner = partly;
    else if (code === 2)                         inner = partly;
    else if (code === 3)                         inner = cloud;
    else if (code === 45 || code === 48)         inner = fog;
    else if (code >= 51 && code <= 67)           inner = rain;
    else if (code >= 71 && code <= 77)           inner = snow;
    else if (code >= 80 && code <= 86)           inner = rain;
    else if (code >= 95 && code <= 99)           inner = storm;
    else                                         inner = cloud;
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
  },
};

// Map a WMO weather_code + is_day flag to a panel-tint class.
// Kept narrow, six daytime moods + clear-night + partly-night.
// Everything else falls back to a neutral "cloudy" tint rather
// than guess; the tile is meant to whisper, not shout.
function weatherTintClass(code, isDay) {
  if (code == null) return "wx-bg-cloudy";
  if (code === 0) return isDay === false ? "wx-bg-clear-night" : "wx-bg-clear-day";
  if (code === 1 || code === 2) return isDay === false ? "wx-bg-partly-night" : "wx-bg-partly-day";
  if (code === 3) return "wx-bg-cloudy";
  if (code === 45 || code === 48) return "wx-bg-fog";
  if (code >= 51 && code <= 57) return "wx-bg-drizzle";
  if ((code >= 61 && code <= 67) || (code >= 80 && code <= 82)) return "wx-bg-rain";
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return "wx-bg-snow";
  if (code >= 95) return "wx-bg-thunder";
  return "wx-bg-cloudy";
}

// #264, "Where you are" location tile. Pulled from /api/location/status
// (LOCAL view, never gated by share-with-cloud). Cached + only re-painted
// when coordinates actually move so we don't churn Leaflet on every dashboard
// poll. Leaflet is defer-loaded; if it's not ready when this runs we just
// retry on the next frame.
let _locTileCache = null;     // last rendered {lat, lon, source}
let _locTileMap = null;
let _locTileMarker = null;
let _locTileFetching = false;
async function renderLocationTile() {
  const panel = document.getElementById("location-panel");
  if (!panel) return;
  if (_locTileFetching) return;       // single-flight; next dashboard poll
  _locTileFetching = true;
  let s;
  try { s = await api("/api/location/status"); }
  catch (_) { _locTileFetching = false; return; }
  _locTileFetching = false;
  const loc = s && s.current;
  if (!loc || loc.lat == null || loc.lon == null) {
    panel.hidden = true;
    return;
  }
  if (typeof L === "undefined") {
    // Leaflet still loading; try again on next dashboard frame.
    setTimeout(() => renderLocationTile(), 500);
    return;
  }
  panel.hidden = false;
  const sub = document.getElementById("location-sub");
  if (sub) {
    const src = loc.source === "gps" ? "GPS" : "static";
    const age = loc.fix_age_s != null ? ` · fix ${Math.round(loc.fix_age_s)}s` : "";
    sub.textContent = `${loc.lat.toFixed(4)}, ${loc.lon.toFixed(4)} (${src}${age})`;
  }
  // Skip the heavy re-paint if coords are basically unchanged. <0.0001°
  // (~11m) is well within GPS jitter, no point re-fitting the map.
  const moved = !_locTileCache
              || Math.abs(_locTileCache.lat - loc.lat) > 0.0001
              || Math.abs(_locTileCache.lon - loc.lon) > 0.0001;
  if (_locTileMap === null) {
    _locTileMap = L.map("location-map", {
      // Drop the in-map attribution bar (the "🇺🇦 Leaflet | © OSM ·
      // © CARTO" strip). It clutters small map cards and the Ukraine
      // flag isn't on-brand. License-compliance attribution still
      // lives in the panel footer (added below where the Location
      // card renders).
      zoomControl: true, scrollWheelZoom: false, attributionControl: false,
    });
    // Map / Satellite toggle, same wp-map-mode key as the cloud
    // surfaces so the user's preference is consistent across both.
    const mapLayer = L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
      subdomains: "abcd",
      maxZoom: 20,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &middot; &copy; <a href="https://carto.com/attributions">CARTO</a>',
    });
    const satLayer = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
      maxZoom: 19,
      attribution: 'Tiles &copy; <a href="https://www.esri.com">Esri</a>',
    });
    const savedMode = (localStorage.getItem("wp-map-mode") || "map").toLowerCase();
    (savedMode === "satellite" ? satLayer : mapLayer).addTo(_locTileMap);
    L.control.layers(
      { "Map": mapLayer, "Satellite": satLayer },
      null,
      { collapsed: true, position: "topright" },
    ).addTo(_locTileMap);
    _locTileMap.on("baselayerchange", (e) => {
      localStorage.setItem("wp-map-mode",
        e.layer === satLayer ? "satellite" : "map");
    });
  }
  if (moved) {
    _locTileMap.setView([loc.lat, loc.lon], 13);
    if (_locTileMarker) _locTileMap.removeLayer(_locTileMarker);
    _locTileMarker = L.marker([loc.lat, loc.lon]).addTo(_locTileMap);
    _locTileCache = { lat: loc.lat, lon: loc.lon, source: loc.source };
  }
  // Leaflet needs a size-invalidate after the panel unhides, otherwise
  // tiles render as a grey square until the user resizes.
  setTimeout(() => { try { _locTileMap.invalidateSize(); } catch (_) {} }, 100);
}

function renderWeather() {
  const panel = $("#weather-panel");
  if (!panel) return;
  ensureWeather().then(w => {
    if (!w) { panel.hidden = true; return; }
    panel.hidden = false;
    // Apply the weather-aware tint as a class so CSS handles the
    // gradient transition (no inline-style churn each refresh).
    panel.classList.forEach((c) => {
      if (c.startsWith("wx-bg-")) panel.classList.remove(c);
    });
    panel.classList.add(weatherTintClass(w.weather_code, w.is_day));
    $("#weather-icon").innerHTML = WMO.iconSvg(w.weather_code, w.is_day);
    $("#weather-temp").textContent = w.temperature_c == null ? "—" : Math.round(w.temperature_c);
    $("#weather-cond").textContent = WMO.describe(w.weather_code, w.is_day);
    $("#weather-cloud").textContent = w.cloud_cover_pct == null ? "—" : `${Math.round(w.cloud_cover_pct)} %`;
    $("#weather-wind").textContent  = w.wind_speed_ms == null ? "—"
      : `${w.wind_speed_ms.toFixed(1)} m/s${w.wind_direction_deg == null ? "" : ` · ${windCompass(w.wind_direction_deg)}`}`;
    $("#weather-humidity").textContent = w.humidity_pct == null ? "—" : `${Math.round(w.humidity_pct)} %`;
    $("#weather-sunrise").textContent = w.sunrise_ts
      ? new Date(w.sunrise_ts * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "—";
    $("#weather-sunset").textContent  = w.sunset_ts
      ? new Date(w.sunset_ts * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "—";
    const feels = w.feels_like_c != null ? ` · feels ${Math.round(w.feels_like_c)}°` : "";
    $("#weather-sub").textContent = `Open-Meteo${feels} · refreshed ${fmt.ago(w.fetched_at)}`;
    renderWeatherHourly(w.hourly);
  });
}

// "Next few hours" strip, Apple-Weather-style hourly preview at the
// bottom of the Right-now tile. Each cell: HH:00 label · tiny WMO
// icon · °C. We always render from the next upcoming hour (drop any
// slice whose timestamp is already in the past, provider may send
// the current hour as the first slice, which would duplicate the
// hero reading).
function renderWeatherHourly(hourly) {
  const host = $("#weather-hourly");
  if (!host) return;
  if (!Array.isArray(hourly) || hourly.length === 0) {
    host.hidden = true; host.innerHTML = ""; return;
  }
  const nowS = Math.floor(Date.now() / 1000);
  const cells = hourly
    .filter(h => h && h.ts > nowS - 1800)   // keep the current hour through ~30 min in
    .slice(0, 8)
    .map(h => {
      const hour = new Date(h.ts * 1000).getHours();
      const label = `${String(hour).padStart(2, "0")}:00`;
      const temp  = h.temperature_c == null ? "—" : `${Math.round(h.temperature_c)}°`;
      const icon  = WMO.iconSvg(h.weather_code, h.is_day);
      return `
        <div class="weather-hour">
          <span class="weather-hour-t">${label}</span>
          <span class="weather-hour-i" aria-hidden="true">${icon}</span>
          <span class="weather-hour-v">${temp}</span>
        </div>`;
    }).join("");
  if (!cells) { host.hidden = true; host.innerHTML = ""; return; }
  host.innerHTML = cells;
  host.hidden = false;
}

function windCompass(deg) {
  const dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
  return dirs[Math.round((deg % 360) / 22.5) % 16];
}

const FORECAST_EMPTY_DISMISS_KEY = "wattpost.forecast.empty.dismissed";

function renderTomorrowEmpty(show) {
  const empty = $("#tomorrow-empty");
  if (!empty) return;
  const dismissed = localStorage.getItem(FORECAST_EMPTY_DISMISS_KEY) === "1";
  empty.hidden = !show || dismissed;
  if (!empty.dataset.wired) {
    empty.dataset.wired = "1";
    $("#tomorrow-empty-dismiss")?.addEventListener("click", () => {
      localStorage.setItem(FORECAST_EMPTY_DISMISS_KEY, "1");
      empty.hidden = true;
    });
  }
}

async function refreshAccuracyLine() {
  const row = $("#forecast-accuracy");
  if (!row) return;
  try {
    const r = await api("/api/forecast/accuracy?day_offset=1");
    if (!r?.ok) { row.hidden = true; return; }
    const pred = (r.predicted_wh / 1000).toFixed(2);
    const got  = (r.actual_wh / 1000).toFixed(2);
    const acc  = r.accuracy_pct == null ? "—" : `${r.accuracy_pct.toFixed(0)} %`;
    // Tint: green if within ±10%, amber if 10-25% off, red beyond.
    const dev = Math.abs((r.accuracy_pct || 100) - 100);
    const cls = dev <= 10 ? "ok" : (dev <= 25 ? "warn" : "off");
    $("#forecast-accuracy-line").innerHTML = `
      predicted <strong>${pred} kWh</strong> ·
      actual <strong>${got} kWh</strong> ·
      <span class="acc-${cls}">${acc} of forecast</span>`;
    row.hidden = false;
  } catch (e) { row.hidden = true; }
}

// Tomorrow tile was folded into Today (see renderToday), the standalone
// renderTomorrow() / drawTomorrowSpark() entry points are gone. Anything
// the dashboard used to do for tomorrow lives inside renderToday now.

// ---------- 7-DAY OUTLOOK STRIP ----------
function renderWeek() {
  const panel = $("#week-panel");
  if (!panel) return;
  ensureForecast().then(f => {
    if (!f) { panel.hidden = true; return; }
    const buckets = bucketByDay(f.points);
    // Drop any buckets with no real energy, Solcast's window can
    // include an in-progress past day that gives 0 kWh. Cap at 5
    // days so the grid stays balanced even when Solcast returns the
    // full 7-day window; reads better at every viewport width.
    const days = buckets.filter(b => b.wh > 0).slice(0, 5);
    if (days.length === 0) { panel.hidden = true; return; }
    panel.hidden = false;
    // Title carries the actual day count so it never contradicts the
    // grid below. Sub-line stays as a pure freshness timestamp.
    $("#week-title").textContent = `${days.length}-day outlook`;
    $("#week-sub").textContent = `Refreshed ${fmt.ago(f.fetched_at)}`;
    drawWeekStrip(days);
  });
}

function drawWeekStrip(days) {
  const host = $("#week-strip");
  if (!host) return;
  // Common scale across all day cards so a quiet day visually reads
  // as quiet next to a sunny one, otherwise each card auto-fits its
  // own peak and the comparison loses meaning.
  const peakWAcrossWeek = Math.max(...days.flatMap(d => d.points.map(p => p.w)), 1);
  const todayMid = (() => { const d = new Date(); d.setHours(0,0,0,0); return Math.floor(d.getTime()/1000); })();
  host.innerHTML = days.map(d => {
    let label;
    if (d.dayMid === todayMid) label = "Today";
    else if (d.dayMid === todayMid + 86400) label = "Tomorrow";
    else label = new Date(d.dayMid * 1000).toLocaleDateString([], { weekday: "short" });
    const dateLabel = new Date(d.dayMid * 1000).toLocaleDateString([], { day: "numeric", month: "short" });
    const kwh = (d.wh / 1000).toFixed(1);
    const peakKw = d.peak ? (d.peak.w / 1000).toFixed(2) : "—";
    const peakAt = d.peak ? new Date(d.peak.ts * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "";
    // Today is the "you are here" anchor, matches the Today tile above.
    const isToday = d.dayMid === todayMid;
    return `
      <div class="week-card ${isToday ? "week-card--featured" : ""}">
        <div class="week-card-head">
          <span class="week-card-day">${label}</span>
          <span class="week-card-date">${dateLabel}</span>
        </div>
        <div class="week-card-spark">${weekSparkSvg(d.points, peakWAcrossWeek)}</div>
        <div class="week-card-num">${kwh} <span class="meta-k">kWh</span></div>
        <div class="week-card-foot">peak ${peakKw} kW${peakAt ? " · " + peakAt : ""}</div>
      </div>`;
  }).join("");
}

function weekSparkSvg(points, peakWAcrossWeek) {
  if (!points.length) return "";
  const W = 100, H = 32, padX = 2, padY = 2;
  const t0 = points[0].ts, tN = points[points.length - 1].ts;
  const span = Math.max(1, tN - t0);
  const pts = points.map(p => {
    const x = padX + ((p.ts - t0) / span) * (W - 2*padX);
    const y = H - padY - (p.w / peakWAcrossWeek) * (H - 2*padY);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const area = `M${padX},${H - padY} L ${pts} L ${W - padX},${H - padY} Z`;
  return `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" width="100%" height="${H}">
      <path d="${area}" fill="rgba(210,153,34,0.18)"/>
      <polyline points="${pts}" fill="none" stroke="#d29922" stroke-width="1.4" stroke-linejoin="round"/>
    </svg>`;
}

// ---------- TODAY PANEL ----------
//
// Headline tile: today's PV-so-far + a forecast curve for the rest of
// the day + sub-stats + one-line Tomorrow preview. After sunset (no PV
// expected today + tomorrow forecast available), the tile flips so
// Tomorrow becomes the headline and today demotes to a "final" line,
// the dashboard's "operational moment" stays unambiguous.
function renderToday() {
  const rover = devices.find(d => d.kind === "charge_controller");
  const l = rover?.latest || {};

  // Battery-monitor framing: a shunt-only system (a battery + a shunt,
  // no charging source) never "harvests" anything, so the PV-centric
  // "Harvested today / Peak" tile reads as a dead 0 Wh. When there's no
  // source device but there is a battery/shunt, reframe the tile around
  // battery throughput (used / charged / net) instead.
  const SOURCE_KINDS = new Set(["charge_controller", "ac_charger", "dcdc", "dcdc_xs", "dcdc_charger", "alternator"]);
  const batteryOnly = !devices.some(d => SOURCE_KINDS.has(d.kind))
    && devices.some(d => d.kind === "shunt" || d.kind === "smart_battery");

  // Headline kWh: prefer the server-side integrated total across ALL
  // sources (PV + AC charger + DC-DC) so a Victron-only install or
  // a multi-source install reads correctly. Fall back to the Renogy
  // MPPT's own daily counter for legacy clients that haven't pulled
  // v0.0.81's `sources_today_wh` field yet.
  const sourcesWh = (todayAggregate
    && typeof todayAggregate.sources_today_wh === "number"
    && todayAggregate.sources_today_wh > 0)
    ? todayAggregate.sources_today_wh
    : (l.energy_today_wh || 0);
  const pvActualStr  = fmt.wh(sourcesWh);
  const chargedStr   = (l.charging_ah_today ?? 0) + " Ah";
  const peakSoFarStr = fmt.num(l.max_charging_power_today_w, 0) + " W";
  const loadWh = (todayAggregate && typeof todayAggregate.load_today_wh === "number")
    ? todayAggregate.load_today_wh
    : l.consumption_today_wh;
  const loadStr = fmt.wh(loadWh);

  // Default to live actuals; sleep-mode block below may overwrite the
  // hero number with tomorrow's expected kWh.
  $("#today-pv").textContent      = pvActualStr;
  $("#today-charged").textContent = chargedStr;
  $("#today-peak").textContent    = peakSoFarStr;
  $("#today-load").textContent    = loadStr;

  // Stored today = bank net (in − out, signed). Matches the cloud
  // dashboard's headline answer to "did my system win today?".
  // Uses today_aggregate's bank_net_today_wh when available; falls
  // back to (sources − load) for older builds.
  const storedEl = $("#today-stored");
  if (storedEl) {
    let storedWh = null;
    if (todayAggregate && typeof todayAggregate.bank_net_today_wh === "number") {
      storedWh = todayAggregate.bank_net_today_wh;
    } else if (typeof sourcesWh === "number" && typeof loadWh === "number") {
      storedWh = sourcesWh - loadWh;
    }
    if (storedWh == null) {
      storedEl.textContent = "—";
    } else if (Math.abs(storedWh) < 5) {
      storedEl.textContent = "0 Wh";
    } else {
      const sign = storedWh > 0 ? "↑ " : "↓ ";
      storedEl.textContent = sign + fmt.wh(Math.abs(storedWh));
    }
  }

  // Battery-monitor reframe: lead with energy *used* today, drop the
  // PV-only cells (Peak), and relabel "Stored" → "Net". Reset back to
  // the PV framing otherwise, since the device set can change at runtime.
  const heroK = document.getElementById("today-hero-k");
  const peakCell = document.getElementById("today-cell-peak");
  const loadCell = document.getElementById("today-cell-load");
  const storedK = document.getElementById("today-stored-k");
  if (batteryOnly) {
    const dis = todayAggregate?.bank_discharged_today_wh;
    const chg = todayAggregate?.bank_charged_today_wh;
    if (heroK) heroK.textContent = "Used today";
    $("#today-pv").textContent = (typeof dis === "number") ? fmt.wh(dis) : "—";
    $("#today-charged").textContent = (typeof chg === "number") ? fmt.wh(chg) : "—";
    if (peakCell) peakCell.hidden = true;
    if (loadCell) loadCell.hidden = true;
    if (storedK) storedK.textContent = "Net";
  } else {
    if (heroK) heroK.textContent = "Harvested today";
    if (peakCell) peakCell.hidden = false;
    if (loadCell) loadCell.hidden = false;
    if (storedK) storedK.textContent = "Stored";
  }

  // Today's SoC envelope, answers "did the bank get critically low
  // overnight?" at a glance. Sourced from /api/today_soc_minmax
  // (fresh endpoint, cheap MIN/MAX over today's bank.soc_pct
  // samples). Render lazily so we don't block the rest of the tile;
  // the value lands on the next paint.
  const socEnvEl = $("#today-soc-env");
  if (socEnvEl) {
    fetch("/api/today/soc-envelope").then(r => r.ok ? r.json() : null).then(d => {
      if (!d || d.min_pct == null || d.max_pct == null) {
        socEnvEl.textContent = "—";
        return;
      }
      socEnvEl.textContent = `${d.min_pct.toFixed(1)} – ${d.max_pct.toFixed(1)}%`;
    }).catch(() => { socEnvEl.textContent = "—"; });
  }

  // Yesterday-accuracy line lives under this tile now, refresh on the
  // same cadence as the dashboard poll; the API call is cheap and the
  // result hides itself if no archive exists yet.
  refreshAccuracyLine();

  const panel    = $("#today-panel");
  const headline = $("#today-headline");
  const sub      = $("#today-sub");
  const foot     = $("#today-tomorrow");
  const footText = $("#today-tomorrow-text");
  const spark    = $("#today-spark");

  ensureForecast().then(f => {
    if (batteryOnly) {
      // No PV on this system — keep the battery framing, no solar
      // forecast headline and no "hook up Solcast" nag.
      if (headline) headline.textContent = "Today";
      if (sub) sub.textContent = "";
      if (foot) foot.hidden = true;
      if (spark) spark.innerHTML = "";
      panel?.classList.remove("today-panel--sleep");
      renderTomorrowEmpty(false);
      return;
    }
    if (!f) {
      // No forecast configured. Show only live actuals and surface the
      // gentle "hook up Solcast" CTA card unless the user dismissed it.
      if (headline) headline.textContent = "Today";
      if (sub)  sub.textContent = "";
      if (foot) foot.hidden = true;
      if (spark) spark.innerHTML = "";
      panel?.classList.remove("today-panel--sleep");
      renderTomorrowEmpty(true);
      return;
    }
    renderTomorrowEmpty(false);

    const s = summariseForecast(f.points);
    // Sleep mode: today has no meaningful PV left to come and the
    // forecast knows about tomorrow. < 50 Wh covers noisy zero-ish
    // slices around dusk so we don't oscillate.
    const sleep = s.todayRemainingWh < 50 && s.tomorrowWh > 0;
    panel?.classList.toggle("today-panel--sleep", sleep);

    // Provider name for the sub-line credit. Backend returns the
    // active provider in the forecast blob ("solcast" / "openmeteo").
    // Map to user-friendly text rather than echoing the raw key.
    const provLabel = f.provider === "openmeteo"
      ? "Open-Meteo"
      : f.provider === "solcast" ? "Solcast" : "Forecast";

    if (sleep) {
      if (headline) headline.textContent = "Tomorrow";
      const kEl = document.getElementById("today-hero-k");
      if (kEl) kEl.textContent = "Forecast tomorrow";
      $("#today-pv").textContent = `${(s.tomorrowWh / 1000).toFixed(1)} kWh`;
      if (sub) {
        sub.textContent = s.tomorrowPeak
          ? `Expected · peak ${(s.tomorrowPeak.w / 1000).toFixed(2)} kW at ${_fmtHm(s.tomorrowPeak.ts)} · ${provLabel}`
          : `Expected · ${provLabel}`;
      }
      // Footer becomes the final tally for today.
      if (foot) {
        foot.hidden = false;
        footText.textContent = `Today (final): PV ${pvActualStr} · Load ${loadStr}`;
      }
      drawTodaySpark(s.tomorrowPoints, null, spark, "Tomorrow");
    } else {
      if (headline) headline.textContent = "Today";
      const kEl = document.getElementById("today-hero-k");
      if (kEl) kEl.textContent = "Harvested today";
      if (sub) {
        if (s.todayWh > 0) {
          // s.todayWh is the provider's full-day forecast at fetch
          // time; s.todayRemainingWh is the from-this-moment-to-EOD
          // portion. Earlier UX rendered both as
          //   "Of X kWh expected · Y still to come"
          // which read as a contradiction once Y == X (e.g. the
          // forecast was refreshed at midnight + hasn't moved while
          // the day passed). Show the actual harvested figure
          // against the forecast instead, so users see the ratio
          // they care about: "are we on track today?".
          const expected  = (s.todayWh / 1000).toFixed(1);
          const harvestWh = (todayAggregate && typeof todayAggregate.sources_today_wh === "number")
            ? todayAggregate.sources_today_wh
            : null;
          if (harvestWh != null) {
            const harvestStr = harvestWh >= 1000
              ? `${(harvestWh / 1000).toFixed(1)} kWh`
              : `${harvestWh.toFixed(0)} Wh`;
            const pct = s.todayWh > 0
              ? Math.round((harvestWh / s.todayWh) * 100) : 0;
            sub.textContent = `Forecast ${expected} kWh · ${harvestStr} so far (${pct} %) · ${provLabel}`;
          } else {
            sub.textContent = `Forecast ${expected} kWh today · ${provLabel}`;
          }
        } else {
          sub.textContent = `${provLabel} · refreshed ${fmt.ago(f.fetched_at)}`;
        }
      }
      // Tomorrow preview footer, only shown when the forecast
      // window has tomorrow's data populated.
      if (s.tomorrowWh > 0 && foot) {
        foot.hidden = false;
        const peakStr = s.tomorrowPeak
          ? ` · peak ${(s.tomorrowPeak.w / 1000).toFixed(2)} kW at ${_fmtHm(s.tomorrowPeak.ts)}`
          : "";
        footText.textContent = `Tomorrow: ${(s.tomorrowWh / 1000).toFixed(1)} kWh expected${peakStr}`;
      } else if (foot) {
        foot.hidden = true;
      }
      drawTodaySpark(s.todayPoints, Math.floor(Date.now() / 1000), spark, "Today");
    }
  });
}

function _fmtHm(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// Catmull-Rom -> cubic bezier: turns the PV sample points into one flowing
// curve instead of jagged segments, so the day reads as the sun's arc.
// `pts` is an array of [x, y] pixel pairs.
function _spkSmooth(pts) {
  if (pts.length < 2) return pts.length ? `M${pts[0][0]},${pts[0][1]}` : "";
  let d = `M${pts[0][0].toFixed(2)},${pts[0][1].toFixed(2)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C${c1x.toFixed(2)},${c1y.toFixed(2)} ${c2x.toFixed(2)},${c2y.toFixed(2)} ${p2[0].toFixed(2)},${p2[1].toFixed(2)}`;
  }
  return d;
}
// Linear-interpolate the curve's y at a given x, so the sun disc can sit
// exactly on the arc at "now".
function _spkYAt(pts, x) {
  for (let i = 0; i < pts.length - 1; i++) {
    if (x >= pts[i][0] && x <= pts[i + 1][0]) {
      const t = (x - pts[i][0]) / ((pts[i + 1][0] - pts[i][0]) || 1);
      return pts[i][1] + t * (pts[i + 1][1] - pts[i][1]);
    }
  }
  return pts[pts.length - 1][1];
}
let _spkUid = 0;  // unique gradient/filter ids when >1 sparkline is on the page

// Today's sparkline, drawn as the sun's arc: a smooth gold curve from sunrise
// to sunset with a glowing fill under the energy actually harvested so far,
// the still-to-come forecast continuing as a faint dashed arc, and the sun
// itself riding the curve at "now". When nowTs is null (sleep mode showing
// tomorrow) the whole arc is bright with no sun marker.
function drawTodaySpark(points, nowTs, host, label) {
  host = host || $("#today-spark");
  if (!host || !points || !points.length) { if (host) host.innerHTML = ""; return; }
  const W = host.clientWidth || 600;
  const H = 104, padX = 10, padT = 22, padB = 16;
  const maxW = Math.max(...points.map(p => p.w), 1);
  const t0 = points[0].ts, tN = points[points.length - 1].ts;
  const span = Math.max(1, tN - t0);
  const xOf = (ts) => padX + ((ts - t0) / span) * (W - 2 * padX);
  const yOf = (w)  => H - padB - (w / maxW) * (H - padT - padB);
  const base = H - padB;
  const allPts = points.map(p => [xOf(p.ts), yOf(p.w)]);
  const g = "s" + (++_spkUid);
  // Faint top-left caption naming the day, so the arc is never mistaken for the
  // other one (today's harvest vs tomorrow's forecast share this component).
  const cap = label
    ? `<text x="${padX}" y="14" font-size="10.5" font-weight="700" letter-spacing="0.6" fill="var(--text-3)">${String(label).toUpperCase()}</text>`
    : "";
  const horizon = `<line x1="${padX}" y1="${(base + 0.5).toFixed(1)}" x2="${W - padX}" y2="${(base + 0.5).toFixed(1)}" stroke="rgba(125,133,144,0.25)" stroke-width="1"/>`;

  // No "now" → the whole day is still a forecast (the Tomorrow tile). Draw it
  // as a light dashed curve over a DIM fill: no bright apex (which reads as a
  // sun sitting on the arc) and no sun marker (there's no current time on a
  // future day).
  if (nowTs == null) {
    const line = _spkSmooth(allPts);
    const area = `${line} L${(W - padX).toFixed(2)},${base} L${padX},${base} Z`;
    host.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}">
      <defs>
        <linearGradient id="fill${g}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stop-color="rgba(210,153,34,0.14)"/>
          <stop offset="100%" stop-color="rgba(210,153,34,0)"/>
        </linearGradient>
      </defs>
      ${cap}
      <path d="${area}" fill="url(#fill${g})"/>
      <path d="${line}" fill="none" stroke="#d29922" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="2 5" opacity="0.75"/>
      ${horizon}
    </svg>`;
    return;
  }

  // Today: realized harvest up to "now" (solid + glowing fill), the rest of the
  // day dashed, and the sun riding the curve at the current time.
  const nowX = Math.min(Math.max(xOf(nowTs), padX), W - padX);
  const sunY = _spkYAt(allPts, nowX);
  const pastPts = points.filter(p => p.ts <= nowTs).map(p => [xOf(p.ts), yOf(p.w)]);
  const futPts  = points.filter(p => p.ts >= nowTs).map(p => [xOf(p.ts), yOf(p.w)]);
  pastPts.push([nowX, sunY]);
  futPts.unshift([nowX, sunY]);
  const pastLine = _spkSmooth(pastPts);
  const futLine  = _spkSmooth(futPts);
  const lastX = (pastPts[pastPts.length - 1] || [padX, base])[0];
  const realArea = `${pastLine} L${lastX.toFixed(2)},${base} L${padX},${base} Z`;

  host.innerHTML = `
  <svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}">
    <defs>
      <linearGradient id="fill${g}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%"   stop-color="rgba(245,180,40,0.42)"/>
        <stop offset="55%"  stop-color="rgba(210,153,34,0.16)"/>
        <stop offset="100%" stop-color="rgba(210,153,34,0)"/>
      </linearGradient>
      <radialGradient id="sun${g}" cx="50%" cy="50%" r="50%">
        <stop offset="0%"   stop-color="#fff4d6"/>
        <stop offset="45%"  stop-color="#ffd166"/>
        <stop offset="100%" stop-color="#f5a623"/>
      </radialGradient>
      <filter id="glow${g}" x="-80%" y="-80%" width="260%" height="260%">
        <feGaussianBlur stdDeviation="4.5" result="b"/>
        <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
      </filter>
    </defs>
    ${cap}
    <path d="${realArea}" fill="url(#fill${g})"/>
    <path d="${pastLine}" fill="none" stroke="#f0b429" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>
    ${futPts.length > 1 ? `<path d="${futLine}" fill="none" stroke="#d29922" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="2 5" opacity="0.5"/>` : ""}
    ${horizon}
    <g filter="url(#glow${g})">
      <circle cx="${nowX.toFixed(1)}" cy="${sunY.toFixed(1)}" r="9" fill="url(#sun${g})"/>
      <circle cx="${nowX.toFixed(1)}" cy="${sunY.toFixed(1)}" r="9" fill="none" stroke="rgba(255,244,214,0.9)" stroke-width="1"/>
    </g>
  </svg>`;
}
