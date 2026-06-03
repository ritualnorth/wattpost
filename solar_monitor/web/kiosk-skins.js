/* WattPost kiosk skin engine.
 *
 * A SKIN renders the full-screen wall display from a KioskViewModel — a
 * stable, versioned snapshot the app emits every frame. Skins bind to the
 * view-model and the shared geometry helpers below, NEVER to app.js
 * internals, so a skin (including a community one) keeps working across
 * releases as long as KIOSK_VM_VERSION is unchanged.
 *
 * This file is deliberately self-contained and dependency-free so it can
 * also drive the skin playground (Phase 2): `WattPostKioskSkins.sampleVM()`
 * returns a representative view-model a skin author can develop against.
 *
 * Contract (KioskViewModel v1):
 *   version      number   — KIOSK_VM_VERSION it was built for
 *   siteName     string   — appliance label
 *   clock        string   — "14:32"
 *   isNight      boolean  — after local sunset (drives night skins/dim)
 *   soc          number   — state of charge %, 0..100
 *   state        string   — 'charging' | 'discharging' | 'holding' | 'critical'
 *   netW         number   — net battery power, + = charging
 *   battery      {v,a,ah,cap}        — volts, amps(+chg), remaining Ah, usable Ah
 *   timeToFullMin / timeToEmptyMin   — minutes, or null
 *   sources      [{id,label,role,watts}]   — role ∈ pv|grid|dc|ac|gen
 *   loads        [{id,label,role,watts}]
 *   todayKwh     number   — harvested today
 *   daySeries    number[] — today's production, normalised 0..1 (sparkline/arc)
 *   forecast     [{kwh}]  — next days
 *   weather      {desc,tempC}
 *   sun          {riseStr,setStr,progress}  — progress 0..1 through daylight
 *   bankLabel    string   — "3× RBT100LFP12S"
 *   cells        [{ah,frac}]   — per-pack capacity + fill 0..1 (Command bank tile)
 */
(function (global) {
  'use strict';

  var KIOSK_VM_VERSION = 1;

  // ---- shared palette: energy role -> colour (the research-backed map) ----
  var ROLE_COLOR = {
    pv:   '#f0c849',   // solar — amber/gold
    grid: '#58a6ff',   // shore / AC mains — blue
    dc:   '#56d3c2',   // DC-DC / alternator — teal
    ac:   '#cdd6e2',   // AC loads — neutral
    gen:  '#d29922',   // generator — amber-orange
    load: '#cdd6e2',
    batt: '#56d364',   // battery — green
  };
  var STATE_COLOR = {
    charging: '#56d364', discharging: '#f0c849',
    holding: '#58a6ff', critical: '#ff7b72',
  };

  // ---- geometry + formatting helpers (skins share these) ----
  var H = {
    color: function (role) { return ROLE_COLOR[role] || ROLE_COLOR.load; },
    stateColor: function (s) { return STATE_COLOR[s] || STATE_COLOR.holding; },
    clamp: function (n, lo, hi) { return Math.max(lo, Math.min(hi, n)); },
    // SoC arc as a pathLength=100 dasharray
    arcDash: function (pct) { return H.clamp(pct, 0, 100).toFixed(1) + ' 100'; },
    // map watts -> stroke width for a flow line (visually legible band)
    strokeFor: function (watts, max) {
      var w = Math.abs(watts || 0);
      var m = Math.max(50, max || 1000);
      return (3 + 9 * Math.min(1, w / m)).toFixed(1);
    },
    fmtW: function (w) {
      if (w == null) return '·';
      var v = Math.round(w);
      return (v > 0 ? '+' : '') + v + ' W';
    },
    fmtDur: function (min) {
      if (min == null || !isFinite(min)) return '—';
      var h = Math.floor(min / 60), m = Math.round(min % 60);
      return h > 0 ? (h + 'h ' + m + 'm') : (m + 'm');
    },
    esc: function (s) {
      return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
      });
    },
  };

  // ---- skin registry ----
  var skins = {};
  function register(skin) { skins[skin.id] = skin; }
  function get(id) { return skins[id] || skins.halo; }
  function list() {
    return Object.keys(skins).map(function (k) {
      return { id: skins[k].id, name: skins[k].name, night: !!skins[k].night };
    });
  }

  // =====================================================================
  //  HALO — minimal, SoC ring as hero, flow nodes around it. The default.
  // =====================================================================
  register({
    id: 'halo', name: 'Halo',
    render: function (root, vm) { root.innerHTML = haloSVG(vm); },
  });

  function haloSVG(vm) {
    var solar = pick(vm.sources, 'pv') || pick(vm.sources, 'gen') || { watts: 0 };
    var shore = pick(vm.sources, 'grid') || pick(vm.sources, 'dc') || { watts: 0, label: 'Shore' };
    var load = totalWatts(vm.loads);
    var maxFlow = Math.max(solar.watts, shore.watts, load, 600);
    var sColor = H.stateColor(vm.state);
    var solarOn = solar.watts > 2, shoreOn = shore.watts > 2, loadOn = load > 2;
    var ttf = vm.state === 'discharging'
      ? { k: 'TIME TO EMPTY', v: H.fmtDur(vm.timeToEmptyMin) }
      : { k: 'TIME TO FULL', v: H.fmtDur(vm.timeToFullMin) };

    return '' +
'<svg viewBox="0 0 1280 800" preserveAspectRatio="xMidYMid meet" class="wp-skin wp-halo">' +
  '<defs>' +
    '<linearGradient id="kHaloArc" x1="0" y1="0" x2="1" y2="1">' +
      '<stop offset="0" stop-color="' + sColor + '" stop-opacity=".75"/><stop offset="1" stop-color="' + sColor + '"/>' +
    '</linearGradient>' +
    '<filter id="kHaloSoft" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="3"/></filter>' +
  '</defs>' +
  '<text x="56" y="64" font-size="26" font-weight="600" class="k-dim">' + H.esc(vm.siteName || 'WattPost') + '</text>' +
  '<text x="1224" y="64" font-size="26" text-anchor="end" class="k-dim">' + H.esc(vm.clock || '') + '</text>' +
  // solar flow
  flowPath('M300,250 C430,330 470,330 560,360', H.color('pv'), H.strokeFor(solar.watts, maxFlow), solarOn) +
  // shore flow (idle -> faint static)
  (shoreOn ? flowPath('M980,250 C850,330 810,330 720,360', H.color('grid'), H.strokeFor(shore.watts, maxFlow), true)
           : '<path d="M980,250 C850,330 810,330 720,360" fill="none" stroke="#16202c" stroke-width="5"/>') +
  // ring -> loads
  flowPath('M640,536 C640,560 640,566 640,584', '#cdd6e2', H.strokeFor(load, maxFlow), loadOn, true) +
  // solar node
  '<g transform="translate(248,210)" opacity="' + (solarOn ? 1 : .45) + '">' +
    '<circle r="44" fill="#120f06" stroke="#3a2c12" stroke-width="2"/>' + sunGlyph(H.color('pv')) +
    '<text x="0" y="74" text-anchor="middle" font-size="30" font-weight="700" fill="' + H.color('pv') + '" class="k-num">' + Math.round(solar.watts) + ' W</text>' +
    '<text x="0" y="98" text-anchor="middle" font-size="16" class="k-label">' + H.esc(solar.label || 'Solar') + '</text>' +
  '</g>' +
  // shore node
  '<g transform="translate(1032,210)" opacity="' + (shoreOn ? 1 : .5) + '">' +
    '<circle r="44" fill="#0c1015" stroke="#1b2532" stroke-width="2"/>' + boltGlyph(shoreOn ? H.color('grid') : '#41506a') +
    '<text x="0" y="74" text-anchor="middle" font-size="30" font-weight="700" fill="' + (shoreOn ? H.color('grid') : '#5a6678') + '" class="k-num">' + Math.round(shore.watts) + ' W</text>' +
    '<text x="0" y="98" text-anchor="middle" font-size="16" class="k-label" fill="' + (shoreOn ? '' : '#5a6678') + '">' + H.esc(shore.label || 'Shore') + '</text>' +
  '</g>' +
  // loads node
  '<g transform="translate(640,628)">' +
    '<circle r="42" fill="#0e1219" stroke="#2a3340" stroke-width="2"/>' + houseGlyph('#cdd6e2') +
    '<text x="62" y="-4" text-anchor="start" font-size="30" font-weight="700" class="k-num">' + Math.round(load) + ' W</text>' +
    '<text x="62" y="20" text-anchor="start" font-size="16" class="k-label">Loads</text>' +
  '</g>' +
  // hero ring
  '<g transform="translate(640,380)">' +
    '<circle r="150" fill="none" stroke="#161c26" stroke-width="20"/>' +
    '<circle r="150" fill="none" stroke="url(#kHaloArc)" stroke-width="20" stroke-linecap="round" pathLength="100" stroke-dasharray="' + H.arcDash(vm.soc) + '" transform="rotate(-90)" filter="url(#kHaloSoft)" opacity=".55"/>' +
    '<circle r="150" fill="none" stroke="url(#kHaloArc)" stroke-width="20" stroke-linecap="round" pathLength="100" stroke-dasharray="' + H.arcDash(vm.soc) + '" transform="rotate(-90)"/>' +
    '<text x="0" y="18" text-anchor="middle" font-size="120" font-weight="800" letter-spacing="-2" class="k-num">' + Math.round(vm.soc) + '<tspan font-size="48" font-weight="600" dx="2" class="k-dim">%</tspan></text>' +
    '<text x="0" y="62" text-anchor="middle" font-size="20" font-weight="600" letter-spacing=".5" style="text-transform:uppercase" fill="' + sColor + '">' + H.esc(vm.stateLabel || vm.state) + '</text>' +
    '<text x="0" y="104" text-anchor="middle" font-size="34" font-weight="700" fill="' + sColor + '" class="k-num">' + H.fmtW(vm.netW) + '</text>' +
  '</g>' +
  // bottom glance
  '<g class="k-num" font-size="24">' +
    '<text x="56" y="730" font-size="34" font-weight="700">' + ttf.v + '</text>' +
    '<text x="56" y="760" class="k-dim" font-weight="600">' + ttf.k + '</text>' +
    '<text x="640" y="730" text-anchor="middle" font-size="34" font-weight="700" fill="' + H.color('pv') + '">' + fmtKwh(vm.todayKwh) + '</text>' +
    '<text x="640" y="760" text-anchor="middle" class="k-dim" font-weight="600">HARVESTED TODAY</text>' +
    '<text x="1224" y="730" text-anchor="end" font-size="34" font-weight="700">' + Math.round(vm.battery && vm.battery.ah || 0) + ' Ah</text>' +
    '<text x="1224" y="760" text-anchor="end" class="k-dim" font-weight="600">' + H.esc(vm.bankLabel || 'BANK') + '</text>' +
  '</g>' +
'</svg>';
  }

  // ---- small SVG building blocks reused by skins ----
  function flowPath(d, color, width, active, reverse) {
    var base = '<path d="' + d + '" fill="none" stroke="' + dim(color) + '" stroke-width="' + (parseFloat(width) + 2).toFixed(1) + '"/>';
    if (!active) return base;
    return base + '<path d="' + d + '" fill="none" stroke="' + color + '" stroke-width="' + width +
      '" stroke-linecap="round" class="k-flow' + (reverse ? ' k-rev' : '') + '"/>';
  }
  function sunGlyph(c) {
    return '<circle cx="0" cy="-2" r="11" fill="none" stroke="' + c + '" stroke-width="3"/>' +
      '<g stroke="' + c + '" stroke-width="3" stroke-linecap="round">' +
      '<line x1="0" y1="-22" x2="0" y2="-16"/><line x1="0" y1="12" x2="0" y2="18"/>' +
      '<line x1="-20" y1="-2" x2="-14" y2="-2"/><line x1="14" y1="-2" x2="20" y2="-2"/></g>';
  }
  function boltGlyph(c) { return '<path d="M-3,-20 L-12,2 L-1,2 L-4,20 L13,-4 L1,-4 Z" fill="none" stroke="' + c + '" stroke-width="3" stroke-linejoin="round"/>'; }
  function houseGlyph(c) {
    return '<path d="M-16,8 L-16,-2 A16,16 0 0 1 16,-2 L16,8 Z" fill="none" stroke="' + c + '" stroke-width="3" stroke-linejoin="round"/>' +
      '<line x1="-20" y1="8" x2="20" y2="8" stroke="' + c + '" stroke-width="3" stroke-linecap="round"/>';
  }
  function dim(hex) { return '#1d2530'; }
  function pick(arr, role) { return (arr || []).filter(function (s) { return s.role === role; }).sort(function (a, b) { return b.watts - a.watts; })[0]; }
  function totalWatts(arr) { return (arr || []).reduce(function (t, x) { return t + (x.watts || 0); }, 0); }
  function fmtKwh(k) { return (k == null ? '·' : (Math.round(k * 10) / 10) + ' kWh'); }

  // ---- sample view-model (playground fixture + harness) ----
  function sampleVM() {
    return {
      version: KIOSK_VM_VERSION, siteName: 'Garage Stack', clock: '14:32', isNight: false,
      soc: 76, state: 'charging', stateLabel: 'Charging', netW: 424,
      battery: { v: 13.7, a: 31.0, ah: 228, cap: 300 },
      timeToFullMin: 160, timeToEmptyMin: null,
      sources: [{ id: 'pv', label: 'Solar', role: 'pv', watts: 612 },
                { id: 'shore', label: 'Shore', role: 'grid', watts: 0 }],
      loads: [{ id: 'dc', label: 'Loads', role: 'load', watts: 188 }],
      todayKwh: 4.1, daySeries: [0, .1, .3, .6, .9, 1, .85, .5, .2],
      forecast: [{ kwh: 3.2 }, { kwh: 5.6 }, { kwh: 6.0 }, { kwh: 2.4 }, { kwh: 4.1 }, { kwh: 5.8 }, { kwh: 5.0 }],
      weather: { desc: 'Clear', tempC: 18 },
      sun: { riseStr: '06:14', setStr: '20:02', progress: 0.6 },
      bankLabel: '3× RBT100LFP12S',
      cells: [{ ah: 100, frac: .9 }, { ah: 100, frac: .9 }, { ah: 100, frac: .7 }],
    };
  }

  global.WattPostKioskSkins = {
    VERSION: KIOSK_VM_VERSION,
    register: register, get: get, list: list, helpers: H, sampleVM: sampleVM,
  };
})(window);
