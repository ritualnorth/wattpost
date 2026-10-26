// WattPost UI, part 03: the "reactor" power-flow diagram.
// Plain script (no modules): the numbered files in web/js/ are loaded in order by
// index.html and share one global scope, exactly as the old single app.js did.

// ===================== Reactor power flow (#design) =====================
// The battery as a pulsing core: solar feeds in from one side, loads draw
// from the other; comet density/speed + ripple direction + core glow track
// live watts. SVG + SMIL only (no canvas) so it stays light on the Pi and on
// phone battery. Ported from the "Core" power-flow concept. Toggled
// against the classic flow via the button in the panel header
// (localStorage wp-flow-reactor; default on).
const _PF_NS = "http://www.w3.org/2000/svg";
let _pfUid = 0;
function _pfEl(tag, attrs, kids) {
  const e = document.createElementNS(_PF_NS, tag);
  if (attrs) for (const k in attrs) if (attrs[k] != null) e.setAttribute(k, String(attrs[k]));
  if (kids) for (const c of kids) if (c) e.appendChild(c);
  return e;
}
function _pfAnim(attr, dur, vals, begin, keyTimes) {
  const a = { attributeName: attr, dur, repeatCount: "indefinite", values: vals };
  if (begin) a.begin = begin;
  if (keyTimes) a.keyTimes = keyTimes;
  return _pfEl("animate", a);
}
function _pfArc(a, b, bow) {
  const dx = b.x - a.x, dy = b.y - a.y, l = Math.hypot(dx, dy) || 1;
  const nx = -dy / l, ny = dx / l;
  const c1x = a.x + dx * 0.32 + nx * bow, c1y = a.y + dy * 0.32 + ny * bow;
  const c2x = a.x + dx * 0.68 + nx * bow, c2y = a.y + dy * 0.68 + ny * bow;
  return `M ${a.x.toFixed(1)} ${a.y.toFixed(1)} C ${c1x.toFixed(1)} ${c1y.toFixed(1)} `
       + `${c2x.toFixed(1)} ${c2y.toFixed(1)} ${b.x.toFixed(1)} ${b.y.toFixed(1)}`;
}
function _pfFmtW(w) {
  w = Math.round(w);
  return Math.abs(w) >= 1000 ? (w / 1000).toFixed(1) + " kW" : w + " W";
}
// Inner markup of one of our ICONS (the bit between <svg>…</svg>), for
// embedding into the reactor's node discs.
function _pfIconInner(key) {
  const s = (typeof ICONS !== "undefined" && ICONS[key]) || "";
  return s.replace(/^[\s\S]*?<svg[^>]*>/, "").replace(/<\/svg>\s*$/, "");
}
function buildFlowReactor(model, opts) {
  const animate = !(opts && opts.animate === false);
  const uid = "pf" + (++_pfUid);
  const solar     = (model.sources || []).reduce((s, x) => s + Math.max(0, +x.power || 0), 0);
  const loadTotal = (model.loads   || []).reduce((s, x) => s + Math.max(0, +x.power || 0), 0);
  // The single input node sums ALL sources. Only call it "Solar" when the
  // input really is all PV — an AC charger / DC-DC feeding the battery is
  // charging input, not solar. Mixed input gets a neutral "Power in" label
  // (the breakdown below itemises Solar vs AC charger separately).
  const pvW = (model.sources || []).filter(x => (x.color || "pv") === "pv")
                .reduce((s, x) => s + Math.max(0, +x.power || 0), 0);
  const mixedIn = (solar - pvW) > 1;
  const inLabel = mixedIn ? "Power in" : "Solar";
  // Sun only when it really is all PV. Mixed input (solar + an AC charger /
  // DC-DC) gets a neutral bolt + colour so the node doesn't imply "solar"
  // — the breakdown underneath still itemises Solar vs AC separately.
  const inIcon  = mixedIn ? "bolt" : "sun";
  const inColor = mixedIn ? "var(--pf-grid, var(--accent, #58a6ff))" : "var(--pf-solar)";
  const battW = Math.round((opts && opts.battNetW) || model.batteryNetW || 0);
  const soc = model.bank ? Math.max(0, Math.min(100, +model.bank.soc || 0)) : 0;
  const DB = 5;
  const charging = battW > DB, discharging = battW < -DB;
  const critical = discharging && soc <= 12;
  const kind = charging ? "charge" : critical ? "crit" : discharging ? "discharge" : "hold";
  const cv = { charge: ["--pf-charge", "--pf-charge-2"], discharge: ["--pf-discharge", "--pf-discharge-2"],
               crit: ["--pf-critical", "--pf-critical-2"], hold: ["--pf-hold", "--pf-hold-2"] }[kind];
  const sc = `var(${cv[0]})`, sc2 = `var(${cv[1]})`;
  const stateLabel = charging ? "CHARGING" : discharging ? "DISCHARGING" : "HOLDING";

  const vertical = (window.innerWidth || 1000) < 640;
  // H tall enough that the ambient glow + widest ripple fit inside the viewBox
  // (no bleed past the panel). Vertical (phone) gets extra height + a smaller
  // core + tighter ripples so the Solar/Loads nodes and their labels clear the
  // core and don't crowd the panel edges.
  const W = vertical ? 380 : 960, H = vertical ? 560 : 440;
  const C = { x: W / 2, y: H * 0.50 };
  const R = vertical ? 96 : Math.min(H * 0.36, 130);
  const ripMax = vertical ? 48 : 80;
  const S = vertical ? { x: W / 2, y: 100 }     : { x: W * 0.15, y: H * 0.50 };
  const L = vertical ? { x: W / 2, y: H - 100 } : { x: W * 0.85, y: H * 0.50 };
  const maxFlow = Math.max(solar, loadTotal, Math.abs(battW), 120);
  const showIn = solar > 4, showOut = loadTotal > 4;
  const unit = (a, b) => { const dx = b.x - a.x, dy = b.y - a.y, l = Math.hypot(dx, dy) || 1; return { x: dx / l, y: dy / l }; };
  const INSET = 34;
  const uS = unit(S, C);
  const inA = _pfArc({ x: S.x + uS.x * INSET, y: S.y + uS.y * INSET }, { x: C.x - uS.x * (R + 6), y: C.y - uS.y * (R + 6) }, vertical ? 34 : 30);
  const uL = unit(C, L);
  const outA = _pfArc({ x: C.x + uL.x * (R + 6), y: C.y + uL.y * (R + 6) }, { x: L.x - uL.x * INSET, y: L.y - uL.y * INSET }, vertical ? -34 : 30);
  const rip = charging ? { from: R + 6, to: R + ripMax } : discharging ? { from: R + ripMax, to: R + 6 } : { from: R + 10, to: R + Math.min(50, ripMax) };
  const cometCount = (p) => Math.max(1, Math.min(6, Math.round(p / maxFlow * 5) + 1));
  const cometDur   = (p) => (1.9 - (p / maxFlow) * 0.9).toFixed(2);

  const svg = _pfEl("svg", { class: "pf-reactor-svg", viewBox: `0 0 ${W} ${H}`,
    preserveAspectRatio: "xMidYMid meet", role: "img",
    "aria-label": `Power flow: ${stateLabel}, ${battW} watts, battery ${soc.toFixed(0)} percent` });
  // Definite aspect-ratio so the browser computes the SVG's height from its
  // width instead of the viewBox's intrinsic size. Without this, Safari blows
  // the SVG up to a giant intrinsic size when it sits in a flex item (the
  // mobile app-shell makes <main> a flex child) — "loaded massive on iOS".
  svg.style.aspectRatio = `${W} / ${H}`;

  svg.appendChild(_pfEl("defs", null, [
    _pfEl("radialGradient", { id: `${uid}-amb` }, [
      _pfEl("stop", { offset: "0%",   "stop-color": sc2, "stop-opacity": "0.55" }),
      _pfEl("stop", { offset: "45%",  "stop-color": sc,  "stop-opacity": "0.28" }),
      _pfEl("stop", { offset: "100%", "stop-color": sc,  "stop-opacity": "0" }),
    ]),
    _pfEl("radialGradient", { id: `${uid}-disc` }, [
      _pfEl("stop", { offset: "0%",   "stop-color": "#fff", "stop-opacity": "0.95" }),
      _pfEl("stop", { offset: "42%",  "stop-color": sc2,    "stop-opacity": "0.92" }),
      _pfEl("stop", { offset: "100%", "stop-color": sc,     "stop-opacity": "0.62" }),
    ]),
  ]));

  const amb = _pfEl("circle", { cx: C.x, cy: C.y, r: R + 88, fill: `url(#${uid}-amb)` });
  if (animate) amb.appendChild(_pfAnim("opacity", "3.4s", "0.42;0.7;0.42"));
  svg.appendChild(amb);

  if (animate) for (let i = 0; i < 3; i++) {
    const c = _pfEl("circle", { cx: C.x, cy: C.y, fill: "none", stroke: sc, "stroke-width": "1.5" });
    const b = `-${(i * 1.06).toFixed(2)}s`;
    c.appendChild(_pfAnim("r", "3.2s", `${rip.from};${rip.to}`, b));
    c.appendChild(_pfAnim("opacity", "3.2s", "0.45;0", b));
    svg.appendChild(c);
  }
  svg.appendChild(_pfEl("circle", { cx: C.x, cy: C.y, r: R + 30, fill: "none",
    stroke: "var(--pf-orbit)", "stroke-width": "1", "stroke-dasharray": "2 8" }));

  function comets(path, n, dur, r, fillVar, fadeVals) {
    for (let i = 0; i < n; i++) {
      const b = `-${((i / n) * dur).toFixed(2)}s`;
      const dot = _pfEl("circle", { r, fill: fillVar, class: "pf-comet",
        style: `filter: drop-shadow(0 0 5px ${fillVar.replace(/-2\)$/, ")")})` });
      dot.appendChild(_pfEl("animateMotion", { dur: `${dur}s`, begin: b, repeatCount: "indefinite", path }));
      dot.appendChild(_pfAnim("opacity", `${dur}s`, fadeVals, b, "0;0.18;0.82;1"));
      svg.appendChild(dot);
    }
  }
  if (showIn) {
    svg.appendChild(_pfEl("path", { d: inA, fill: "none", stroke: "var(--pf-solar)", "stroke-width": "2", opacity: "0.16" }));
    if (animate) comets(inA, cometCount(solar), +cometDur(solar), "3.4", "var(--pf-solar-2)", "0;1;1;0.2");
  }
  if (showOut) {
    svg.appendChild(_pfEl("path", { d: outA, fill: "none", stroke: "var(--pf-load)", "stroke-width": "2", opacity: "0.16" }));
    if (animate) comets(outA, cometCount(loadTotal), +cometDur(loadTotal), "3.2", "var(--pf-load-2)", "0.2;1;1;0");
  }

  svg.appendChild(_pfEl("circle", { cx: C.x, cy: C.y, r: R, fill: "none", stroke: "var(--pf-ring-track)", "stroke-width": "9" }));
  svg.appendChild(_pfEl("circle", { cx: C.x, cy: C.y, r: R, fill: "none", stroke: sc, "stroke-width": "9",
    pathLength: "100", "stroke-dasharray": `${soc} 100`, "stroke-linecap": "round",
    transform: `rotate(-90 ${C.x} ${C.y})`, class: "pf-soc-arc",
    style: `filter: drop-shadow(0 0 8px ${sc}); transition: stroke-dasharray .9s cubic-bezier(.4,0,.2,1)` }));

  const core = _pfEl("circle", { cx: C.x, cy: C.y, r: R - 18, fill: `url(#${uid}-disc)`, opacity: "0.92" });
  if (animate) { core.appendChild(_pfAnim("r", "2.6s", `${R - 20};${R - 14};${R - 20}`));
                 core.appendChild(_pfAnim("opacity", "2.6s", "0.84;0.98;0.84")); }
  svg.appendChild(core);

  const t1 = _pfEl("text", { x: C.x, y: C.y - 6, "text-anchor": "middle", "font-size": (R * 0.5).toFixed(0),
    "font-weight": "700", fill: "#0a0d12", "fill-opacity": "0.92", class: "pf-num" });
  t1.textContent = soc.toFixed(0);
  const u = _pfEl("tspan", { "font-size": (R * 0.24).toFixed(0), dy: "-2" }); u.textContent = "%"; t1.appendChild(u);
  svg.appendChild(t1);
  const t2 = _pfEl("text", { x: C.x, y: C.y + R * 0.26, "text-anchor": "middle", "font-size": (R * 0.14).toFixed(0),
    "font-weight": "700", fill: "#0a0d12", "fill-opacity": "0.62", "letter-spacing": "0.08em" });
  t2.textContent = stateLabel; svg.appendChild(t2);
  const t3 = _pfEl("text", { x: C.x, y: C.y + R * 0.46, "text-anchor": "middle", "font-size": (R * 0.15).toFixed(0),
    "font-weight": "700", fill: "#0a0d12", "fill-opacity": "0.82", class: "pf-num" });
  t3.textContent = (battW > 0 ? "+" : "") + battW + " W"; svg.appendChild(t3);

  function node(pt, label, valW, colorVar, dim, iconKey, above) {
    const g = _pfEl("g", { class: "pf-node" + (dim ? " is-off" : "") });
    g.appendChild(_pfEl("circle", { cx: pt.x, cy: pt.y, r: "28", fill: "var(--pf-node-bg)", stroke: colorVar, "stroke-width": "1.6" }));
    // icon disc (reuse our ICONS; 24×24 scaled to ~22, centred in the disc)
    const ig = _pfEl("g", { transform: `translate(${(pt.x - 11).toFixed(1)} ${(pt.y - 11).toFixed(1)}) scale(0.92)`,
      fill: "none", "stroke-width": "1.6", "stroke-linecap": "round", "stroke-linejoin": "round",
      style: `color: ${colorVar}; stroke: ${colorVar}` });
    ig.innerHTML = _pfIconInner(iconKey);
    g.appendChild(ig);
    // Labels below the disc by default; above it when `above` (the top node on
    // the vertical/phone layout) so they sit away from the core, not over it.
    const ly = above ? -48 : 48, vy = above ? -68 : 67;
    const lab = _pfEl("text", { x: pt.x, y: pt.y + ly, "text-anchor": "middle", "font-size": "13",
      fill: "var(--text-3)", "font-weight": "600" }); lab.textContent = label; g.appendChild(lab);
    const val = _pfEl("text", { x: pt.x, y: pt.y + vy, "text-anchor": "middle", "font-size": "17",
      "font-weight": "700", fill: colorVar, class: "pf-num" }); val.textContent = _pfFmtW(valW); g.appendChild(val);
    return g;
  }
  svg.appendChild(node(S, inLabel, solar, inColor, !showIn, inIcon, vertical));
  svg.appendChild(node(L, "Loads", loadTotal, "var(--pf-load-2)", !showOut, "house", false));

  const stage = document.createElement("div");
  stage.className = "pf-reactor-stage";
  stage.appendChild(svg);
  return stage;
}

// Slim 3-item flow breakdown shown under the reactor — Solar / battery
// in-or-out / Loads. Replaces the old battery summary bar, which duplicated
// the SoC + state + watts the reactor core already shows.
function buildFlowLegend(model, battW) {
  const loadTotal = (model.loads   || []).reduce((s, x) => s + Math.max(0, +x.power || 0), 0);
  const wrap = document.createElement("div");
  wrap.className = "pf-legend";
  const item = (colorVar, label, w) => {
    const lg = document.createElement("span"); lg.className = "pf-lg";
    const sw = document.createElement("span"); sw.className = "pf-sw"; sw.style.background = colorVar;
    lg.appendChild(sw);
    lg.appendChild(document.createTextNode(" " + label + " "));
    const b = document.createElement("b"); b.textContent = _pfFmtW(w);
    lg.appendChild(b);
    return lg;
  };
  // Group inputs by their label so a non-solar source (e.g. an AC charger)
  // shows as its own line — "Solar 395 W · AC Charger 120 W" — instead of
  // being summed into "Solar". PV uses the solar colour; everything else
  // uses the grid/charger colour.
  const groups = new Map();
  (model.sources || []).forEach(x => {
    const w = Math.max(0, +x.power || 0);
    const label = x.label || "Solar";
    const g = groups.get(label) || { w: 0, color: x.color || "pv" };
    g.w += w; groups.set(label, g);
  });
  if (groups.size === 0) groups.set("Solar", { w: 0, color: "pv" });
  groups.forEach((g, label) => {
    const cv = g.color === "pv" ? "var(--pf-solar)" : "var(--pf-grid, var(--accent, #58a6ff))";
    wrap.appendChild(item(cv, label, g.w));
  });
  if (battW > 5)        wrap.appendChild(item("var(--pf-charge)", "Into battery", battW));
  else if (battW < -5)  wrap.appendChild(item("var(--pf-discharge)", "From battery", -battW));
  else                  wrap.appendChild(item("var(--text-3)", "Battery", 0));
  wrap.appendChild(item("var(--pf-load-2)", "Loads", loadTotal));
  return wrap;
}

// Combined hero: the stat boxes that sit under the reactor when the hero +
// power-flow are merged into one section. Reads the values the hero already
// computed (one tick old at most — imperceptible) so we don't duplicate the
// formatting logic.
function buildCombinedStats() {
  const g = (id) => (document.getElementById(id)?.textContent || "·").trim();
  const row = document.createElement("div");
  row.className = "pf-statrow";
  const box = (k, valId, unit, sub, withBar, isText) => {
    const b = document.createElement("div"); b.className = "pf-statbox";
    // Text values (e.g. the battery model on the Bank box) aren't numbers —
    // they overflow the box at the big tabular size, so flag them for a
    // smaller, wrapping treatment.
    if (isText) b.classList.add("pf-statbox--text");
    const kk = document.createElement("div"); kk.className = "pf-statk"; kk.textContent = k; b.appendChild(kk);
    const vv = document.createElement("div"); vv.className = "pf-statv"; vv.textContent = g(valId);
    if (unit) { const u = document.createElement("span"); u.className = "pf-statu"; u.textContent = unit; vv.appendChild(u); }
    b.appendChild(vv);
    if (sub) { const ss = document.createElement("div"); ss.className = "pf-stats"; ss.textContent = sub; b.appendChild(ss); }
    if (withBar) {
      const bar = document.createElement("div"); bar.className = "pf-statbar";
      const tile = document.getElementById("bank-time-tile");
      if (tile) ["charging", "discharging", "idle"].forEach((c) => { if (tile.classList.contains(c)) bar.classList.add(c); });
      const i = document.createElement("i");
      const soc = parseFloat(g("bank-soc")) || 0;
      i.style.width = Math.max(3, Math.min(100, soc)) + "%";
      bar.appendChild(i); b.appendChild(bar);
    }
    return b;
  };
  row.appendChild(box("Remaining", "bank-time", "", g("bank-time-sub"), true));
  row.appendChild(box("Voltage", "bank-voltage", "V", ""));
  row.appendChild(box("Remaining", "bank-remaining", "Ah", "of " + g("bank-capacity") + " Ah usable"));
  row.appendChild(box("Bank", "bank-meta", "", "", false, true));
  return row;
}
function buildFlowSvgV2(model, opts) {
  const { showBattInSvg, battNetW } = opts;

  // Position sources across the top, loads (+ battery when shown)
  // across the bottom. When the battery node is in the SVG it takes
  // the left slot and loads pack to the right. Edge insets sized
  // for r=28 (was 22) so labels + ring fit cleanly.
  const srcPos = positionFlowNodes(model.sources.length, FLOW_SRC_Y, [80, FLOW_W - 80]);
  let loadPos, batPos = null;
  if (showBattInSvg) {
    batPos  = [90, FLOW_BOT_Y];
    loadPos = positionFlowNodes(model.loads.length, FLOW_BOT_Y, [200, FLOW_W - 80]);
  } else {
    loadPos = positionFlowNodes(model.loads.length, FLOW_BOT_Y, [80, FLOW_W - 80]);
  }

  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "flow-svg-v2");
  svg.setAttribute("viewBox", `0 0 ${FLOW_W} ${FLOW_H}`);
  // Removed aria-hidden, nodes are now interactive (drill-in clicks);
  // each <g class="flow-node-clickable"> carries an accessible label.
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Live power flow diagram");

  // Stable, scoped path IDs so multiple flow tiles (dashboard +
  // kiosk) don't collide their mpath / gradient references.
  const idPrefix = `fv2-${++FLOW_PATH_SEQ}`;

  // Gradient defs container, gradient stops are built per path as
  // we discover their source/dest colours.
  const defs = document.createElementNS(SVG_NS, "defs");
  svg.appendChild(defs);

  const activePaths = [];

  // Dominant source detection, whichever source/battery carries the
  // most W gets a soft pulse halo. Sources here are "power feeding
  // the bus": active sources OR (when battery is discharging)
  // the battery. Loads never dominate.
  let dominantId = null;
  let dominantMax = 0;
  model.sources.forEach((s) => {
    if (!s.silent && (s.power || 0) > dominantMax) {
      dominantMax = s.power;
      dominantId = `src-${s.label || s.color}`;
    }
  });
  if (showBattInSvg && battNetW < 0 && Math.abs(battNetW) > dominantMax) {
    dominantMax = Math.abs(battNetW);
    dominantId = "bat";
  }

  // Source → bus (each source).
  model.sources.forEach((s, i) => {
    const [x, y] = srcPos[i];
    const pathId = `${idPrefix}-s${i}`;
    const gradId = `${idPrefix}-gs${i}`;
    const active = (s.power || 0) >= 1 && !s.silent;
    const colorKey = colorKeyOf(s) || "pv";
    if (active) {
      addFlowGradient(defs, gradId, x, y + FLOW_NODE_R - 6,
                      FLOW_BUS_X, FLOW_BUS_Y, FLOW_COLOR[colorKey], FLOW_COLOR.neutral);
      addFlowPath(svg, pathId, x, y + FLOW_NODE_R - 6, FLOW_BUS_X, FLOW_BUS_Y,
                  true, `url(#${gradId})`);
      activePaths.push({ id: pathId, color: colorKey, w: s.power,
                         mx: (x + FLOW_BUS_X) / 2, my: (y + FLOW_BUS_Y) / 2 });
    } else {
      addFlowPath(svg, pathId, x, y + FLOW_NODE_R - 6, FLOW_BUS_X, FLOW_BUS_Y, false);
    }
  });

  // Battery <-> bus when shown. Direction encodes charge state.
  if (showBattInSvg) {
    const [bx, by] = batPos;
    const charging = battNetW > 0;
    const pathId = `${idPrefix}-bat`;
    const gradId = `${idPrefix}-gbat`;
    const colorKey = charging ? "batt" : "discharge";
    const x1 = charging ? FLOW_BUS_X : bx;
    const y1 = charging ? FLOW_BUS_Y : by - FLOW_NODE_R + 6;
    const x2 = charging ? bx : FLOW_BUS_X;
    const y2 = charging ? by - FLOW_NODE_R + 6 : FLOW_BUS_Y;
    addFlowGradient(defs, gradId, x1, y1, x2, y2,
                    charging ? FLOW_COLOR.neutral : FLOW_COLOR[colorKey],
                    charging ? FLOW_COLOR[colorKey] : FLOW_COLOR.neutral);
    addFlowPath(svg, pathId, x1, y1, x2, y2, true, `url(#${gradId})`);
    activePaths.push({ id: pathId, color: colorKey, w: Math.abs(battNetW),
                       mx: (x1 + x2) / 2, my: (y1 + y2) / 2 });
  }

  // Bus → load (each load).
  model.loads.forEach((l, i) => {
    const [x, y] = loadPos[i];
    const pathId = `${idPrefix}-l${i}`;
    const gradId = `${idPrefix}-gl${i}`;
    const active = (l.power || 0) >= 1;
    const colorKey = colorKeyOf(l) || "load";
    if (active) {
      addFlowGradient(defs, gradId, FLOW_BUS_X, FLOW_BUS_Y, x, y - FLOW_NODE_R + 6,
                      FLOW_COLOR.neutral, FLOW_COLOR[colorKey]);
      addFlowPath(svg, pathId, FLOW_BUS_X, FLOW_BUS_Y, x, y - FLOW_NODE_R + 6,
                  true, `url(#${gradId})`);
      activePaths.push({ id: pathId, color: colorKey, w: l.power,
                         mx: (FLOW_BUS_X + x) / 2, my: (FLOW_BUS_Y + y) / 2 });
    } else {
      addFlowPath(svg, pathId, FLOW_BUS_X, FLOW_BUS_Y, x, y - FLOW_NODE_R + 6, false);
    }
  });

  // Particles on each active path. Density + speed scale with W.
  activePaths.forEach(p => addFlowParticles(svg, p.id, p.color, p.w));

  // Mid-line W labels, only show for installs with 3+ active edges
  // (1-2 edges + node labels already convey the same info; cluttering
  // a simple 2-source layout makes things worse). Honest secondary
  // styling so they read as captions, not primary numbers.
  if (activePaths.length >= 3) {
    activePaths.forEach(p => addFlowSegmentLabel(svg, p.mx, p.my, p.w));
  }

  // Junction node, explicit centre marker at the bus. Pulses softly
  // when sources ≈ loads (battery isn't doing much); grows when the
  // battery is making up a shortfall to draw the eye to "we're
  // burning bank capacity right now". Hidden in the battery-only
  // edge case (no sources, no loads), that path returns earlier.
  const totalSrc = model.sources.reduce((a, s) => a + (s.power || 0), 0);
  const totalLoad = model.loads.reduce((a, l) => a + (l.power || 0), 0);
  const imbalance = Math.abs(totalSrc - totalLoad);
  const balanced = imbalance < Math.max(15, 0.05 * Math.max(totalSrc, totalLoad));
  addFlowJunction(svg, FLOW_BUS_X, FLOW_BUS_Y, balanced);

  // Nodes (rendered last so they sit on top of path ends).
  model.sources.forEach((s, i) => addFlowNode(svg, srcPos[i][0], srcPos[i][1], s, true, {
    dominant: dominantId === `src-${s.label || s.color}`,
    drillTo: drillTargetFor(s, "source"),
  }));
  if (showBattInSvg) {
    const [bx, by] = batPos;
    addFlowNode(svg, bx, by, {
      label: "Battery",
      color: battNetW > 0 ? "batt" : "discharge",
      icon: "battery",
      power: Math.abs(battNetW),
    }, false, {
      dominant: dominantId === "bat",
      drillTo:  drillTargetFor({ kind: "battery" }, "battery"),
      socPct:   model.bank ? model.bank.soc : null,
    });
  }
  model.loads.forEach((l, i) => addFlowNode(svg, loadPos[i][0], loadPos[i][1], l, false, {
    drillTo: drillTargetFor(l, "load"),
  }));

  // Single delegated click handler routes drill-in clicks. Keeps
  // per-node handlers out of the loop + survives DOM rebuilds.
  svg.addEventListener("click", _onFlowSvgClick);

  return svg;
}

// Drill-in routing. Hash routes, using location.href would break on
// the cloud view, which only serves the SPA shell + its data API.
// Hash routes stay within the SPA and work everywhere.
//
// Battery → #292 dedicated detail page (bank aggregate). Sources go
// to the devices list so users can see the per-device live numbers.
// Load is the house demand aggregate (no per-device meaning) so the
// history page is the most useful drill.
function drillTargetFor(node, role) {
  if (role === "battery") return "#/device/bank";
  if (role === "source")  return "#/devices";
  if (role === "load")    return "#/history";
  return null;
}

function _onFlowSvgClick(e) {
  const node = e.target.closest("[data-flow-drill]");
  if (!node) return;
  const dest = node.getAttribute("data-flow-drill");
  if (!dest) return;
  // Hash navigation only, see drillTargetFor for why. Anything else is ignored
  // rather than assigned to location.href (a javascript: value would run).
  if (dest.startsWith("#")) location.hash = dest.slice(1);
}

function addFlowGradient(defs, id, x1, y1, x2, y2, fromColor, toColor) {
  // Linear gradient along the segment direction so the visual flow
  // (source colour fading into destination colour) tracks the
  // particle motion direction at a glance.
  const grad = document.createElementNS(SVG_NS, "linearGradient");
  grad.setAttribute("id", id);
  grad.setAttribute("gradientUnits", "userSpaceOnUse");
  grad.setAttribute("x1", x1);
  grad.setAttribute("y1", y1);
  grad.setAttribute("x2", x2);
  grad.setAttribute("y2", y2);
  const s0 = document.createElementNS(SVG_NS, "stop");
  s0.setAttribute("offset", "0%");
  s0.setAttribute("stop-color", fromColor);
  s0.setAttribute("stop-opacity", "0.85");
  const s1 = document.createElementNS(SVG_NS, "stop");
  s1.setAttribute("offset", "100%");
  s1.setAttribute("stop-color", toColor);
  s1.setAttribute("stop-opacity", "0.85");
  grad.appendChild(s0);
  grad.appendChild(s1);
  defs.appendChild(grad);
}

function addFlowSegmentLabel(svg, x, y, w) {
  // Compact W label hovering near the midpoint of an active path.
  // Faded background pill so it stays legible over a gradient line.
  const bg = document.createElementNS(SVG_NS, "rect");
  const txt = `${Math.round(w)} W`;
  const charW = 6.2;
  const padX = 4, padY = 2;
  const wPx = txt.length * charW + padX * 2;
  const hPx = 14;
  bg.setAttribute("x", x - wPx / 2);
  bg.setAttribute("y", y - hPx / 2);
  bg.setAttribute("width", wPx);
  bg.setAttribute("height", hPx);
  bg.setAttribute("rx", 4);
  bg.setAttribute("class", "flow-segment-w-bg");
  svg.appendChild(bg);
  const lbl = document.createElementNS(SVG_NS, "text");
  lbl.setAttribute("x", x);
  lbl.setAttribute("y", y + 3.5);
  lbl.setAttribute("text-anchor", "middle");
  lbl.setAttribute("class", "flow-segment-w");
  lbl.textContent = txt;
  svg.appendChild(lbl);
}

function addFlowJunction(svg, x, y, balanced) {
  const dot = document.createElementNS(SVG_NS, "circle");
  dot.setAttribute("cx", x);
  dot.setAttribute("cy", y);
  dot.setAttribute("r", FLOW_JUNCTION_R);
  dot.setAttribute("class", `flow-junction${balanced ? " balanced" : ""}`);
  svg.appendChild(dot);
}

let FLOW_PATH_SEQ = 0;

function positionFlowNodes(n, y, [xMin, xMax]) {
  if (n === 0) return [];
  if (n === 1) return [[(xMin + xMax) / 2, y]];
  if (n === 2) return [[xMin + 30, y], [xMax - 30, y]];
  const step = (xMax - xMin) / (n - 1);
  return Array.from({length: n}, (_, i) => [xMin + i * step, y]);
}

function addFlowPath(svg, id, x1, y1, x2, y2, active, strokeOverride) {
  // Smooth bezier between two points. Control points are vertically
  // stretched so the curve enters/exits the nodes pointing up/down
  //, keeps source-to-bus arcs feeling like a real wiring diagram.
  const midY = (y1 + y2) / 2;
  const d = `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`;
  const el = document.createElementNS(SVG_NS, "path");
  el.setAttribute("id", id);
  el.setAttribute("d", d);
  el.setAttribute("class", `flow-conn-v2${active ? " active" : ""}`);
  // Optional stroke override, used to point at the per-segment
  // linearGradient so the line visually originates in its source
  // colour and arrives in its destination colour.
  if (strokeOverride) el.setAttribute("stroke", strokeOverride);
  svg.appendChild(el);
}

function addFlowParticles(svg, pathId, colorKey, w) {
  // Particle density + speed both scale with W.
  //   * Speed: log-curve so a trickle creeps, kW visibly rushes.
  //     Tighter range than v2 (0.45..1.8s) for more drama.
  //   * Count: 2 base, +1 at 250W, +2 at 1kW. Inverter-on or
  //     panels-in-full-sun become visually obvious without needing
  //     to read the number.
  const dur = Math.max(0.45, Math.min(1.8, 1.9 - Math.log10(Math.max(1, w)) * 0.45));
  const count = w >= 1000 ? 4 : (w >= 250 ? 3 : 2);
  for (let i = 0; i < count; i++) {
    const dot = document.createElementNS(SVG_NS, "circle");
    dot.setAttribute("r", "3.4");  // up from 3, matches bigger nodes
    dot.setAttribute("class", `flow-particle-v2 ${colorKey}`);
    const motion = document.createElementNS(SVG_NS, "animateMotion");
    motion.setAttribute("dur", `${dur.toFixed(2)}s`);
    motion.setAttribute("repeatCount", "indefinite");
    motion.setAttribute("begin", `${(i * dur / count).toFixed(2)}s`);
    const mpath = document.createElementNS(SVG_NS, "mpath");
    mpath.setAttributeNS("http://www.w3.org/1999/xlink", "href", `#${pathId}`);
    motion.appendChild(mpath);
    dot.appendChild(motion);
    svg.appendChild(dot);
  }
}

// Local-clock-based sun opacity. No sunrise/sunset API hit, we just
// want a visual hint that the dashboard knows it's night. Fades in
// 06-08, full 08-17, fades out 17-21, dim 21-06.
function sunTimeOpacity() {
  const h = new Date().getHours();
  if (h >= 8 && h < 17)  return 1;
  if (h >= 6 && h < 8)   return 0.45 + (h - 6) * 0.275;       // 0.45 → 1.0
  if (h >= 17 && h < 21) return 1 - (h - 17) * 0.21;          // 1.0 → 0.16
  return 0.3;
}

function addFlowNode(svg, x, y, t, isSource, extras) {
  const color = colorKeyOf(t);
  // "Idle" = explicit power=0 reading; subtly dimmed (still
  // visible, the user added it to the config). "Silent" = device
  // hasn't reported recently; visibly dimmer because the data is
  // genuinely stale.
  const silent = t.silent && (t.power || 0) < 1;
  const idle = !silent && (t.power || 0) < 1;
  const tone = silent ? "silent" : color;
  const dominant = !!(extras && extras.dominant);
  const drillTo  = extras && extras.drillTo;
  const socPct   = extras && extras.socPct;

  // Wrap everything in a clickable group when a drill target exists.
  // The SVG's delegated click handler reads data-flow-drill from the
  // group. role=button + tabindex makes the node keyboard-focusable
  // for the keyboard-only crowd (kiosk installs without touch).
  const group = document.createElementNS(SVG_NS, "g");
  group.setAttribute("class",
    `flow-node-group ${tone}` +
    (dominant ? " dominant" : "") +
    (idle ? " idle" : "") +
    (drillTo ? " flow-node-clickable" : "")
  );
  if (drillTo) {
    group.setAttribute("data-flow-drill", drillTo);
    group.setAttribute("role", "button");
    group.setAttribute("tabindex", "0");
    group.setAttribute("aria-label", `${t.label || color} node, opens detail`);
  }

  // Watt label, above for sources, below for loads/battery. Padded
  // a few extra pixels to keep the bigger node ring + label spacing
  // proportional.
  const lbl = document.createElementNS(SVG_NS, "text");
  lbl.setAttribute("x", x);
  lbl.setAttribute("y", isSource ? y - FLOW_NODE_R - 14 : y + FLOW_NODE_R + 18);
  lbl.setAttribute("class", `flow-node-w ${silent ? "silent" : ""}`);
  lbl.setAttribute("text-anchor", "middle");
  lbl.textContent = `${Math.round(t.power || 0)} W`;
  group.appendChild(lbl);

  // SoC arc, only on the battery node. Drawn as a circle with
  // stroke-dasharray to subtend just the right fraction. Rotated
  // -90° so 0% starts at the top (matches the hero SoC donut).
  if (typeof socPct === "number") {
    const r = FLOW_BATT_SOC_R;
    const circ = 2 * Math.PI * r;
    const filled = circ * Math.max(0, Math.min(100, socPct)) / 100;
    const arc = document.createElementNS(SVG_NS, "circle");
    arc.setAttribute("cx", x);
    arc.setAttribute("cy", y);
    arc.setAttribute("r", r);
    arc.setAttribute("class", `flow-batt-soc-ring ${tone}`);
    arc.setAttribute("transform", `rotate(-90 ${x} ${y})`);
    arc.setAttribute("stroke-dasharray", `${filled.toFixed(2)} ${(circ - filled).toFixed(2)}`);
    group.appendChild(arc);
  }

  // Ring (single stroked circle). Battery node also gets a tinted
  // fill driven by tone, pink-wash when discharging, green-wash
  // when charging, so direction reads at a glance even before the
  // user looks at the arrow / SoC ring / card below.
  const ring = document.createElementNS(SVG_NS, "circle");
  ring.setAttribute("cx", x);
  ring.setAttribute("cy", y);
  ring.setAttribute("r", FLOW_NODE_R);
  const isBattery = typeof socPct === "number";
  ring.setAttribute("class",
    `flow-node-ring ${tone}${isBattery ? " flow-node-ring-batt" : ""}`);
  group.appendChild(ring);

  // Direction arrow, battery node only. ↓ for charging (energy
  // INTO the bank), ↑ for discharging (energy OUT). Tone-down from
  // v0.1.90's first cut: chip sits just OUTSIDE the ring (not
  // overlapping the SoC arc) and shrank from r=9 to r=7 so it reads
  // as a badge rather than a competing element. Fill-wash dropped
  // entirely, the ring colour + arrow already say everything;
  // pink-on-pink-on-pink was colour soup.
  if (isBattery && (t.power || 0) >= 1) {
    const charging = tone === "batt";
    // 45° upper-right, just past the SoC arc (r = FLOW_BATT_SOC_R).
    // sqrt(2)/2 ≈ 0.707 for clean 45° placement.
    const off = (FLOW_BATT_SOC_R + 4) * 0.707;
    const ax = x + off;
    const ay = y - off;
    const chip = document.createElementNS(SVG_NS, "circle");
    chip.setAttribute("cx", ax);
    chip.setAttribute("cy", ay);
    chip.setAttribute("r", 7);
    chip.setAttribute("class", `flow-node-dir-chip ${tone}`);
    group.appendChild(chip);
    // Triangle, points down (charging, going INTO bank) or up
    // (discharging, going OUT). Sized for r=7 chip.
    const arrow = document.createElementNS(SVG_NS, "path");
    const d = charging
      ? `M ${ax - 3} ${ay - 2} L ${ax + 3} ${ay - 2} L ${ax} ${ay + 3} Z`
      : `M ${ax - 3} ${ay + 2} L ${ax + 3} ${ay + 2} L ${ax} ${ay - 3} Z`;
    arrow.setAttribute("d", d);
    arrow.setAttribute("class", `flow-node-dir-arrow ${tone}`);
    group.appendChild(arrow);
  }

  // Icon (inline SVG so the path data stays in one place: ICONS).
  const iconKey = t.icon || COLOR_TO_ICON[color] || "unknown";
  const iconSvg = ICONS[iconKey] || ICONS.unknown;
  const ig = document.createElementNS(SVG_NS, "g");
  const iconHalf = FLOW_NODE_ICON / 2;
  ig.setAttribute("transform", `translate(${x - iconHalf} ${y - iconHalf})`);
  ig.setAttribute("class", `flow-node-icon ${tone}`);
  // Time-of-day fade applies only to the sun (PV) icon, at night
  // the panels aren't doing anything anyway, dimming the icon makes
  // that visually honest without changing the underlying node state.
  if (iconKey === "sun") {
    ig.setAttribute("opacity", String(sunTimeOpacity().toFixed(2)));
  }
  const inner = iconSvg.replace(/<\/?svg[^>]*>/g, "");
  const innerSvg = document.createElementNS(SVG_NS, "svg");
  innerSvg.setAttribute("viewBox", "0 0 24 24");
  innerSvg.setAttribute("width", String(FLOW_NODE_ICON));
  innerSvg.setAttribute("height", String(FLOW_NODE_ICON));
  innerSvg.innerHTML = inner;
  ig.appendChild(innerSvg);
  group.appendChild(ig);

  svg.appendChild(group);
}

function colorKeyOf(t) {
  // Map flow-model colour tag to a tone class shared with CSS.
  //
  // "discharge" was missing from the allowlist since v2, meant the
  // battery node fell through to "neutral" (grey ring + grey icon)
  // every time the bank was draining, even though the CSS author
  // shipped `.flow-node-ring.discharge { stroke: #f06292 }` expecting
  // it to fire. Card-below was pink, node was grey. Caught while
  // adding the v3 fill-tint + direction-arrow work, fixed in v0.1.90.
  const c = (t && t.color) || "";
  if (c === "pv" || c === "batt" || c === "load" || c === "grid"
      || c === "ac" || c === "dc" || c === "discharge") return c;
  return "neutral";
}

function buildBatCard(bank, battNetW) {
  // Battery card lives below the SVG. Slim, Tesla-app style:
  // icon-circle on the left, big SoC + state label in the middle,
  // signed flow on the right, thin horizontal fill bar underneath.
  const soc   = Math.max(0, Math.min(100, bank.soc || 0));
  const aNetW = Math.abs(battNetW);

  // State drives the icon-ring + fill colours. "Full" wins over a
  // tiny float trickle so users don't see amber discharging at 100 %.
  // The old "Resting" middle tier was dropped because it labelled a
  // 4 W discharge "Resting" while the side label still read "↑ 4 W
  // out", contradictory. Anything ≥1 W now reads honestly by
  // direction; <1 W (genuinely idle) reads as "Idle".
  let state, tone;
  if (soc < 15 && battNetW > 0)      { state = "Low · Charging"; tone = "red"; }
  else if (soc < 15)                 { state = "Low";            tone = "red"; }
  else if (soc >= 98 && aNetW < 50)  { state = "Full";           tone = "blue"; }
  else if (aNetW < 1)                { state = "Idle";           tone = "blue"; }
  else if (battNetW > 0)             { state = "Charging";       tone = "green"; }
  else                               { state = "Discharging";    tone = "discharge"; }

  let flowText, flowClass;
  if (aNetW < 1)         { flowText = "Idle";                                              flowClass = ""; }
  else if (battNetW > 0) { flowText = `↓ ${aNetW} W in`;                                   flowClass = "charging"; }
  else                   { flowText = `↑ ${aNetW} W out`;                                  flowClass = "discharging"; }

  const subParts = [];
  if (typeof bank.meanV === "number") subParts.push(`${bank.meanV.toFixed(2)} V`);
  if (typeof bank.sumI  === "number" && Math.abs(bank.sumI) >= 0.1) {
    subParts.push(`${bank.sumI >= 0 ? "+" : ""}${bank.sumI.toFixed(1)} A`);
  }

  const card = document.createElement("div");
  card.className = "bat-card";
  card.innerHTML = `
    <div class="bat-icon-wrap ${tone}">${ICONS.battery}</div>
    <div class="bat-headline">
      <div class="bat-soc">${soc.toFixed(0)}<span class="bat-soc-unit">%</span></div>
      <div class="bat-state">${state}</div>
    </div>
    <div class="bat-power ${flowClass}">
      <div>${flowText}</div>
      <div class="bat-power-sub">${subParts.join(" · ")}</div>
    </div>
    <div class="bat-fill"><div class="bat-fill-inner ${tone}" style="width:${soc.toFixed(0)}%"></div></div>
  `;
  return card;
}

function flowCaption(model, totalSourceW, totalLoadW) {
  if (!model.bank) return "";
  const battW = Math.round(model.batteryNetW || 0);
  const sourcesActive = totalSourceW >= 1;
  const loadsActive   = totalLoadW   >= 1;
  const soc = model.bank.soc || 0;
  // "Effectively full", MPPTs go into float around 98-100%. We use
  // 98 so we catch the "100.0 % shown, 99.6 actual" case too.
  const bankFull = soc >= 98;

  // Battery is meaningfully charging (sources > loads, excess into bank).
  if (battW >= 1) {
    if (sourcesActive && loadsActive) return `Sources covering load · charging battery at ${battW} W`;
    if (sourcesActive)                return `Charging battery at ${battW} W`;
    return "";
  }

  // Bank is full and the MPPT is loafing, pulling just enough sun
  // to cover the load (and a few W of bus maintenance). This is the
  // most common "wait, why is solar only 94 W when it's sunny?"
  // case, so we call it out before the generic discharge text.
  // Heuristic: bank full + sources active + the bank itself isn't
  // doing much (|netW| < 50 W). If the load suddenly spikes the
  // MPPT ramps up and we fall through to the normal captions below.
  if (bankFull && sourcesActive && Math.abs(battW) < 50) {
    if (loadsActive) return `Battery full · solar throttled to load demand (${Math.round(totalSourceW)} W, panels not maxed)`;
    return `Battery full · solar throttled (no demand)`;
  }

  // Battery is meaningfully discharging (sources < loads, bank fills gap).
  if (battW <= -1) {
    const out = Math.abs(battW);
    if (sourcesActive && loadsActive) return `Load is ${out} W more than sources · battery making up the difference`;
    if (loadsActive)                  return `Running off battery · ${out} W to load`;
    return `Battery draining at ${out} W`;
  }

  // Battery at rest.
  if (sourcesActive && loadsActive) return `Sources matched to load · battery resting`;
  if (sourcesActive)                return `Sources idle to a full battery · no load drawing`;
  if (loadsActive)                  return `Running off battery · no draw beyond idle`;
  return `System idle`;
}
