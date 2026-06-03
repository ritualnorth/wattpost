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

  // =====================================================================
  //  EMBER — cozy van / night-native. Warm 'hearth' palette, a day-arc
  //  with the sun's position, big human runtime. The differentiator.
  // =====================================================================
  register({
    id: 'ember', name: 'Ember', night: true,
    render: function (root, vm) { root.innerHTML = emberSVG(vm); },
  });

  // Quadratic-bezier day arc: P0(250,300) C(640,-40) P1(1030,300). A true
  // semicircle would peak off-screen at midday; this shallow arc keeps the
  // sun on screen all day. t = sun progress (0 sunrise … 1 sunset).
  function bezXY(t) {
    var mt = 1 - t;
    return {
      x: mt * mt * 250 + 2 * mt * t * 640 + t * t * 1030,
      y: mt * mt * 300 + 2 * mt * t * (-40) + t * t * 300,
    };
  }

  function emberSVG(vm) {
    var solar = pick(vm.sources, 'pv') || { watts: 0 };
    var load = totalWatts(vm.loads);
    var batt = vm.battery || {};
    var p = (vm.sun && typeof vm.sun.progress === 'number') ? H.clamp(vm.sun.progress, 0, 1) : null;
    var sun = p != null ? bezXY(p) : null;
    var discharging = vm.state === 'discharging';
    var runMin = discharging ? vm.timeToEmptyMin : vm.timeToFullMin;
    var runWord = discharging ? 'left' : 'to full';
    var fillW = (44 * H.clamp(vm.soc, 0, 100) / 100).toFixed(0);
    return '' +
'<svg viewBox="0 0 1280 800" preserveAspectRatio="xMidYMid meet" class="wp-skin wp-ember">' +
  '<defs>' +
    '<linearGradient id="kEmA" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#7a4a1f"/><stop offset=".5" stop-color="#f0b860"/><stop offset="1" stop-color="#e8743b"/></linearGradient>' +
    '<radialGradient id="kEmS" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#ffe3a3"/><stop offset="1" stop-color="#f0a13c"/></radialGradient>' +
    '<radialGradient id="kEmBg" cx=".5" cy=".26" r=".95"><stop offset="0" stop-color="#2a1d14"/><stop offset=".48" stop-color="#1a1310"/><stop offset="1" stop-color="#0d0907"/></radialGradient>' +
    '<filter id="kEmG" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="9"/></filter>' +
  '</defs>' +
  '<rect width="1280" height="800" fill="url(#kEmBg)"/>' +
  '<text x="56" y="62" font-size="24" font-weight="600" class="k-dim">' + H.esc(vm.siteName || 'WattPost') + '</text>' +
  '<text x="1224" y="62" font-size="24" text-anchor="end" class="k-dim k-num">' + H.esc(vm.clock || '') + '</text>' +
  '<path d="M250,300 Q640,-40 1030,300" fill="none" stroke="#2c2118" stroke-width="10" stroke-linecap="round"/>' +
  (vm.sun ? '<text x="250" y="338" font-size="20" text-anchor="middle" class="k-dim k-num">' + H.esc(vm.sun.riseStr || '') + '</text><text x="1030" y="338" font-size="20" text-anchor="middle" class="k-num" fill="#e8946a">' + H.esc(vm.sun.setStr || '') + '</text>' : '') +
  (sun ? '<g transform="translate(' + sun.x.toFixed(0) + ',' + sun.y.toFixed(0) + ')"><circle r="34" fill="#f0a13c" filter="url(#kEmG)" opacity=".5"/><circle r="19" fill="url(#kEmS)"/></g>' : '') +
  '<text x="640" y="206" font-size="22" text-anchor="middle" fill="#f0b860" font-weight="700" class="k-num">' + fmtKwh(vm.todayKwh) + ' harvested today</text>' +
  '<text x="640" y="430" text-anchor="middle" font-size="150" font-weight="800" letter-spacing="-3" fill="#f6d79b" class="k-num">' + Math.round(vm.soc) + '<tspan font-size="60" fill="#9a8163" font-weight="600" dx="2">%</tspan></text>' +
  '<text x="640" y="474" text-anchor="middle" font-size="20" fill="#e8946a" class="k-label">' + H.esc(vm.stateLabel || vm.state) + '</text>' +
  '<text x="640" y="540" text-anchor="middle" font-size="40" font-weight="700" class="k-num">' + (runMin != null ? '≈ ' + H.fmtDur(runMin) + ' ' + runWord : H.fmtW(vm.netW)) + '</text>' +
  '<text x="640" y="572" text-anchor="middle" font-size="22" class="k-dim k-num">' + H.fmtW(vm.netW) + ' · ' + (+batt.v || 0).toFixed(1) + ' V · ' + Math.round(batt.ah || 0) + ' Ah of ' + Math.round(batt.cap || 0) + '</text>' +
  '<g transform="translate(0,672)">' +
    '<g transform="translate(300,0)"><circle r="34" fill="#241a10" stroke="#3a2c12" stroke-width="2"/><circle r="9" fill="none" stroke="#caa05a" stroke-width="2.5"/><text x="0" y="62" text-anchor="middle" font-size="22" font-weight="700" fill="#caa05a" class="k-num">' + Math.round(solar.watts) + ' W</text><text x="0" y="84" text-anchor="middle" font-size="13" fill="#9a8163" class="k-label">Solar</text></g>' +
    '<g transform="translate(640,0)"><rect x="-40" y="-26" width="80" height="52" rx="9" fill="#1d1510" stroke="#5a4326" stroke-width="2"/><rect x="40" y="-9" width="6" height="18" rx="2" fill="#5a4326"/><rect x="-34" y="-20" width="' + fillW + '" height="40" rx="4" fill="#f0b860" opacity=".85"/><text x="0" y="62" text-anchor="middle" font-size="13" fill="#9a8163" class="k-label">Battery</text></g>' +
    '<g transform="translate(980,0)"><circle r="34" fill="#241a10" stroke="#3a2c12" stroke-width="2"/>' + houseGlyph('#f3e7d6') + '<text x="0" y="62" text-anchor="middle" font-size="22" font-weight="700" class="k-num">' + Math.round(load) + ' W</text><text x="0" y="84" text-anchor="middle" font-size="13" fill="#9a8163" class="k-label">Loads</text></g>' +
    (solar.watts > 2 ? '<path class="k-flow" d="M334,0 L600,0" stroke="#caa05a" stroke-width="4"/>' : '') +
    (load > 2 ? '<path class="k-flow" d="M680,0 L946,0" stroke="#f0b860" stroke-width="6"/>' : '') +
  '</g>' +
'</svg>';
  }

  // =====================================================================
  //  COMMAND — rich command-centre. Branching flow + a tile band. For the
  //  workshop / cabin / boat owner who wants everything at a glance.
  // =====================================================================
  register({
    id: 'command', name: 'Command',
    render: function (root, vm) { root.innerHTML = commandSVG(vm); },
  });

  function cmdTile(x, w, label, body) {
    return '<g transform="translate(' + x + ',470)"><rect width="' + w + '" height="270" rx="16" fill="#0d121a" stroke="#1b2430" stroke-width="1.5"/>' +
      '<text x="22" y="40" font-size="14" class="k-label">' + label + '</text>' + body + '</g>';
  }

  function commandSVG(vm) {
    var solar = pick(vm.sources, 'pv') || { watts: 0 };
    var shore = pick(vm.sources, 'grid') || pick(vm.sources, 'dc') || { watts: 0, label: 'Shore' };
    var load = totalWatts(vm.loads);
    var maxFlow = Math.max(solar.watts, shore.watts, load, 600);
    var sColor = H.stateColor(vm.state);
    var batt = vm.battery || {};
    var solarOn = solar.watts > 2, shoreOn = shore.watts > 2, loadOn = load > 2;
    var remFrac = batt.cap ? H.clamp((batt.ah || 0) / batt.cap, 0, 1) : 0;
    var ttf = vm.timeToFullMin != null ? ' · ' + H.fmtDur(vm.timeToFullMin) + ' to full'
            : (vm.timeToEmptyMin != null ? ' · ' + H.fmtDur(vm.timeToEmptyMin) + ' left' : '');
    // day sparkline from daySeries (0..1)
    var ds = vm.daySeries || [];
    var spark = ds.length > 1 ? ds.map(function (v, i) {
      return (22 + 176 * i / (ds.length - 1)).toFixed(0) + ',' + (210 - 70 * H.clamp(v, 0, 1)).toFixed(0);
    }).join(' ') : '';
    // forecast bars
    var fc = (vm.forecast || []).slice(0, 7);
    var fcMax = fc.reduce(function (m, f) { return Math.max(m, f.kwh || 0); }, 1);
    var fcBars = fc.map(function (f, i) {
      var h = 70 * H.clamp((f.kwh || 0) / fcMax, 0, 1);
      return '<rect x="' + (i * 26) + '" y="' + (90 - h).toFixed(0) + '" width="18" height="' + h.toFixed(0) + '" rx="3" fill="' + H.color('pv') + '"/>';
    }).join('');
    // bank cells
    var cells = (vm.cells || []).slice(0, 3).map(function (c, i) {
      return '<g transform="translate(0,' + (i * 46) + ')"><rect width="152" height="34" rx="6" fill="#10202a" stroke="#1d3a44" stroke-width="1.5"/><rect x="6" y="6" width="' + (118 * H.clamp(c.frac, 0, 1)).toFixed(0) + '" height="22" rx="3" fill="#56d364" opacity=".85"/><text x="142" y="23" font-size="14" text-anchor="end" fill="#cdd6e2" font-weight="800" class="k-num">' + Math.round(c.ah) + 'Ah</text></g>';
    }).join('');
    return '' +
'<svg viewBox="0 0 1280 800" preserveAspectRatio="xMidYMid meet" class="wp-skin wp-command">' +
  '<defs><linearGradient id="kCmA" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="' + sColor + '" stop-opacity=".75"/><stop offset="1" stop-color="' + sColor + '"/></linearGradient>' +
  '<filter id="kCmG" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="6"/></filter></defs>' +
  '<text x="56" y="60" font-size="24" font-weight="700" class="k-num">' + H.esc(vm.siteName || 'WattPost') + '</text>' +
  '<text x="1224" y="60" font-size="22" text-anchor="end" class="k-dim k-num">' + H.esc(vm.clock || '') + ' · ' + Math.round(solar.watts + shore.watts) + ' W in · ' + Math.round(load) + ' W out</text>' +
  flowPath('M250,200 C420,210 460,260 540,268', H.color('pv'), H.strokeFor(solar.watts, maxFlow), solarOn) +
  (shoreOn ? flowPath('M250,360 C420,350 460,300 540,288', H.color('grid'), H.strokeFor(shore.watts, maxFlow), true) : '<path d="M250,360 C420,350 460,300 540,288" fill="none" stroke="#16202c" stroke-width="4"/>') +
  flowPath('M735,272 C860,272 900,272 1010,272', '#cdd6e2', H.strokeFor(load, maxFlow), loadOn, true) +
  '<g transform="translate(196,200)" opacity="' + (solarOn ? 1 : .5) + '"><circle r="50" fill="#120f06" stroke="#3a2c12" stroke-width="2"/>' + sunGlyph(H.color('pv')) + '<text x="0" y="78" text-anchor="middle" font-size="26" font-weight="700" fill="' + H.color('pv') + '" class="k-num">' + Math.round(solar.watts) + ' W</text><text x="0" y="100" text-anchor="middle" font-size="14" class="k-label">' + H.esc(solar.label || 'Solar') + '</text></g>' +
  '<g transform="translate(196,360)" opacity="' + (shoreOn ? 1 : .55) + '"><circle r="44" fill="#0c1015" stroke="#1b2532" stroke-width="2"/>' + boltGlyph(shoreOn ? H.color('grid') : '#41506a') + '<text x="0" y="70" text-anchor="middle" font-size="24" font-weight="700" fill="' + (shoreOn ? H.color('grid') : '#5a6678') + '" class="k-num">' + Math.round(shore.watts) + ' W</text><text x="0" y="92" text-anchor="middle" font-size="14" class="k-label">' + H.esc(shore.label || 'Shore') + '</text></g>' +
  '<g transform="translate(640,272)"><circle r="92" fill="none" stroke="#161c26" stroke-width="15"/>' +
    '<circle r="92" fill="none" stroke="url(#kCmA)" stroke-width="15" stroke-linecap="round" pathLength="100" stroke-dasharray="' + H.arcDash(vm.soc) + '" transform="rotate(-90)" filter="url(#kCmG)" opacity=".5"/>' +
    '<circle r="92" fill="none" stroke="url(#kCmA)" stroke-width="15" stroke-linecap="round" pathLength="100" stroke-dasharray="' + H.arcDash(vm.soc) + '" transform="rotate(-90)"/>' +
    '<text x="0" y="2" text-anchor="middle" font-size="58" font-weight="800" class="k-num">' + Math.round(vm.soc) + '<tspan font-size="26" fill="#76828f" dx="1">%</tspan></text>' +
    '<text x="0" y="34" text-anchor="middle" font-size="20" font-weight="700" fill="' + sColor + '" class="k-num">' + H.fmtW(vm.netW) + '</text>' +
    '<text x="0" y="138" text-anchor="middle" font-size="14" class="k-label" fill="' + sColor + '">' + H.esc(vm.stateLabel || vm.state) + ttf + '</text></g>' +
  '<g transform="translate(1064,272)"><circle r="50" fill="#0e1219" stroke="#2a3340" stroke-width="2"/>' + houseGlyph('#cdd6e2') + '<text x="0" y="78" text-anchor="middle" font-size="26" font-weight="700" class="k-num">' + Math.round(load) + ' W</text><text x="0" y="100" text-anchor="middle" font-size="14" class="k-label">Loads</text></g>' +
  cmdTile(56, 220, 'Battery', '<text x="22" y="104" font-size="46" font-weight="800" class="k-num">' + (+batt.v || 0).toFixed(1) + '<tspan font-size="22" fill="#76828f" dx="3">V</tspan></text><text x="22" y="152" font-size="34" font-weight="700" fill="' + sColor + '" class="k-num">' + ((+batt.a || 0) >= 0 ? '+' : '') + (+batt.a || 0).toFixed(1) + '<tspan font-size="18" fill="#76828f" dx="3">A</tspan></text>') +
  cmdTile(296, 220, 'Remaining', '<text x="22" y="104" font-size="46" font-weight="800" class="k-num">' + Math.round(batt.ah || 0) + '<tspan font-size="22" fill="#76828f" dx="3">Ah</tspan></text><text x="22" y="138" font-size="18" class="k-dim">of ' + Math.round(batt.cap || 0) + ' Ah</text><rect x="22" y="160" width="176" height="14" rx="7" fill="#161c26"/><rect x="22" y="160" width="' + (176 * remFrac).toFixed(0) + '" height="14" rx="7" fill="#56d364"/>') +
  cmdTile(536, 220, 'Bank', '<g transform="translate(34,66)">' + cells + '</g><text x="22" y="244" font-size="15" class="k-dim">' + H.esc(vm.bankLabel || '') + '</text>') +
  cmdTile(776, 220, 'Harvested today', '<text x="22" y="104" font-size="46" font-weight="800" fill="' + H.color('pv') + '" class="k-num">' + (vm.todayKwh == null ? '·' : (Math.round(vm.todayKwh * 10) / 10)) + '<tspan font-size="22" fill="#76828f" dx="3">kWh</tspan></text>' + (spark ? '<polyline points="' + spark + '" fill="none" stroke="' + H.color('pv') + '" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>' : '') + '<line x1="22" y1="214" x2="198" y2="214" stroke="#1b2430" stroke-width="1.5"/>') +
  cmdTile(1016, 208, 'Forecast', '<g transform="translate(22,120)">' + fcBars + '</g>' + (vm.weather ? '<text x="22" y="250" font-size="17" class="k-dim">' + H.esc(vm.weather.desc || '') + ' · ' + Math.round(vm.weather.tempC) + ' °C</text>' : '')) +
'</svg>';
  }

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
