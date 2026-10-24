// WattPost UI, part 02: hero and the power-flow strip.
// Plain script (no modules): the numbered files in web/js/ are loaded in order by
// index.html and share one global scope, exactly as the old single app.js did.

// ---------- HERO ----------
// Position the leading-edge dot (head + halo) at the tip of the arc
// for the given pct. Math: 12 o'clock = pct 0; clockwise to pct 100.
function positionDonutHead(wrap, pct) {
  const r = Number(wrap.dataset.radius || 86);
  const cx = 100, cy = 100;
  const theta = (pct / 100) * 2 * Math.PI;
  const x = cx + r * Math.sin(theta);
  const y = cy - r * Math.cos(theta);
  wrap.querySelectorAll(".donut-head, .donut-head-glow").forEach(el => {
    el.setAttribute("cx", x.toFixed(2));
    el.setAttribute("cy", y.toFixed(2));
  });
}

function renderHero() {
  const bank = aggregateBank();
  if (!bank) {
    $("#bank-soc").textContent = "—";
    $("#donut-arc").setAttribute("stroke-dasharray", "0 100");
    return;
  }
  // SoC
  $("#bank-soc").textContent = bank.soc.toFixed(1);
  const arc = $("#donut-arc");
  const pct = Math.min(100, Math.max(0, bank.soc));
  arc.setAttribute("stroke-dasharray", `${pct} ${100 - pct}`);
  // Mirror the SoC paint onto the kiosk donut (lives in a different DOM
  // tree but uses the same class hooks).
  const kioskArc = document.querySelector(".kiosk-donut .donut-arc");
  if (kioskArc) kioskArc.setAttribute("stroke-dasharray", `${pct} ${100 - pct}`);
  const kioskSoc = $("#kiosk-soc");
  if (kioskSoc) kioskSoc.textContent = bank.soc.toFixed(1);
  // Hero card still hue-tints by SoC band for backwards-compat. Future:
  // drop these classes once the new donut palette covers it.
  const socCls = pct < 20 ? "soc-low" : pct < 50 ? "soc-mid" : "soc-high";
  const heroEl = document.querySelector(".hero-v2");
  if (heroEl) {
    heroEl.classList.remove("soc-low", "soc-mid", "soc-high");
    heroEl.classList.add(socCls);
  }

  // Net power
  const powerTile = $("#bank-power-tile");
  powerTile.classList.remove("charging", "discharging", "idle");
  let powerState;
  if (Math.abs(bank.netW) < 1) powerState = "idle";
  else if (bank.netW > 0) powerState = "charging";
  else powerState = "discharging";
  powerTile.classList.add(powerState);
  $("#bank-power").textContent = fmt.signed(bank.netW, 0);
  $("#bank-power-sub").textContent = {
    idle: "no flow",
    charging: `+${bank.sumI.toFixed(2)} A · charging`,
    discharging: `${bank.sumI.toFixed(2)} A · discharging`,
  }[powerState];

  // Donut wrapper state, drives ring gradient + leading-edge dot color
  // + flow-pill color. Four states:
  //   critical    SoC < 15 (urgent palette overrides flow direction)
  //   charging    netW > +1  AND SoC ≥ 15
  //   discharging netW < −1  AND SoC ≥ 15
  //   holding     |netW| ≤ 1 AND SoC ≥ 15 (genuinely idle)
  // Critical wins over flow so a draining-fast-at-12% bank reads RED,
  // not the calmer amber, regardless of direction.
  //
  // The 1 W threshold is tight on purpose. The old 5 W rule called a
  // 4 W discharge "holding" while the side label read "↑ 4 W out",
  // visually contradictory. Anything ≥1 W gets classified honestly
  // by direction so the donut + flow-pill + watts line tell the same
  // story.
  let donutState;
  if (pct < 15) donutState = "critical";
  else if (Math.abs(bank.netW) < 1) donutState = "holding";
  else if (bank.netW > 0) donutState = "charging";
  else donutState = "discharging";
  // Flow-direction modifier, drives the leading-edge dot + halo
  // colour independently of the arc colour. Only applied when the
  // bank has >1 W of net flow (the "holding" band keeps its neutral
  // blue head). The visible case this unlocks: critical SoC + net
  // charging shows a red arc with a GREEN pulsing head, telegraphing
  // recovery without losing the "we're low" message on the arc.
  let headFlow = null;
  if (bank.netW > 1)       headFlow = "donut-head-flow-up";
  else if (bank.netW < -1) headFlow = "donut-head-flow-down";
  document.querySelectorAll(".donut-state").forEach(el => {
    el.classList.remove("charging", "discharging", "holding", "critical", "idle",
                        "donut-head-flow-up", "donut-head-flow-down");
    el.classList.add(donutState);
    if (headFlow) el.classList.add(headFlow);
    positionDonutHead(el, pct);
  });
  // The flow pill colors still hook off the legacy charging/discharging/
  // idle classes, keep those applied alongside the new state so existing
  // .donut-state.charging .donut-flow rules still match.
  document.querySelectorAll(".donut-state").forEach(el => el.classList.add(powerState));
  const flowText = $("#donut-flow .donut-flow-text");
  if (flowText) {
    flowText.textContent = powerState === "idle"
      ? "Idle"
      : `${fmt.signed(bank.netW, 0)} W`;
  }
  const kioskFlowText = $("#kiosk-flow-text");
  if (kioskFlowText) {
    kioskFlowText.textContent = powerState === "idle"
      ? "Idle"
      : `${fmt.signed(bank.netW, 0)} W`;
  }

  // Remaining time
  const rem = computeRemaining(bank);
  $("#bank-time").textContent = rem.primary;
  $("#bank-time-sub").textContent = rem.secondary;
  // Runway feature tile: state-coloured SoC fill bar (matches the donut/flow).
  const timeTile = $("#bank-time-tile");
  if (timeTile) { timeTile.classList.remove("charging", "discharging", "idle"); timeTile.classList.add(powerState); }
  const timeBar = $("#bank-time-bar");
  if (timeBar) timeBar.style.width = Math.max(3, Math.min(100, +bank.soc || 0)) + "%";
  // The forecast-aware line is populated by refreshRuntimeForecast()
  // on its own cadence, render here just keeps the existing values.

  // Source-disagreement hint (#121). Only rendered when both a
  // shunt and one or more BMSes are present AND their SoC readings
  // differ by >5 pp. Single quiet line, not an alarm.
  const dis = $("#donut-disagreement");
  if (dis) {
    if (bank.disagreement) {
      const d = bank.disagreement;
      // Compact one-line format so the hint fits inside the donut on
      // narrow viewports. Active source first (that's what the donut
      // is showing), other source second. The "we picked the shunt
      // because…" explanation lives in the title tooltip.
      const activeLabel = d.showing === "shunt" ? "shunt" : "BMS";
      const otherLabel  = d.showing === "shunt" ? "BMS"   : "shunt";
      const activeSoc   = d.showing === "shunt" ? d.shuntSoc : d.bmsSoc;
      const otherSoc    = d.showing === "shunt" ? d.bmsSoc   : d.shuntSoc;
      dis.textContent = `${activeLabel} ${activeSoc.toFixed(0)}% · ${otherLabel} ${otherSoc.toFixed(0)}%`;
      dis.hidden = false;
      dis.title = `Showing ${activeLabel} reading. Your BMS and shunt ` +
        "disagree by more than 5%. WattPost shows the more reliable " +
        "source for SoC (shunt by default). Override in Settings → " +
        "Power source if you trust one over the other.";
    } else {
      dis.hidden = true;
      dis.textContent = "";
    }
  }

  // #293, Battery health one-liner under the donut. Aggregates the
  // worst current signal across the battery surfaces so the user
  // sees a single status without having to scroll to the Health
  // panel. Three colour bands match the Health panel itself.
  const healthEl = $("#donut-health");
  if (healthEl) {
    const h = _bankHealthBadge(bank);
    if (h) {
      healthEl.hidden = false;
      healthEl.textContent = h.text;
      healthEl.className = `donut-health ${h.band}`;
      healthEl.title = h.detail || "";
    } else {
      healthEl.hidden = true;
    }
  }
  // Other stats
  $("#bank-voltage").textContent = bank.meanV.toFixed(2);
  $("#bank-capacity").textContent = bank.totalCap.toFixed(0);
  $("#bank-remaining").textContent = bank.totalRem.toFixed(1);
  // Bank meta is a long string (e.g. "3× RBT100LFP12S-G1"), shrink to
  // text style so it fits the small grid cell on mobile.
  const bankMetaTile = $("#bank-meta").closest(".hero-stat-val");
  if (bankMetaTile) bankMetaTile.classList.add("is-text");
  // Two patterns:
  //   * Shunt-only install (#115 "no-BMS mode"), there are no
  //     declared packs, so the count would render "0× SmartShunt 500A"
  //     which reads as broken. Drop the count when packs=0 and just
  //     show the model.
  //   * Standard BMS install, "3× RBT100LFP12S-G1".
  const shortModel = (bank.model || "")
    .replace(/-G\d$/, "");
  $("#bank-meta").textContent = bank.packs > 0
    ? `${bank.packs}× ${shortModel}`
    : shortModel;
}

// One-liner battery health badge (#293). Pure, takes the bank
// model + returns {band, text, detail} or null when there's no
// reading worth surfacing. Worst-of-signals wins: cell spread
// > SoC critical > SoC low > healthy.
function _bankHealthBadge(bank) {
  if (!bank) return null;
  const spread = bank.cell_spread;
  if (typeof spread === "number") {
    if (spread >= 0.20) return { band: "alarm", text: "cells drifted",  detail: `Cell spread ${spread.toFixed(2)} V. Open Cell balance to investigate.` };
    if (spread >= 0.10) return { band: "warn",  text: "cells drifting", detail: `Cell spread ${spread.toFixed(2)} V. Watch the Cell balance panel.` };
  }
  if (typeof bank.soc === "number" && bank.soc <= 15) {
    return { band: "alarm", text: "SoC critical", detail: "Bank below 15%. Deep discharge shortens lifespan." };
  }
  if (typeof bank.soc === "number" && bank.soc <= 30) {
    return { band: "warn",  text: "SoC low",      detail: "Bank below 30%. Recharge soon for longest lifespan." };
  }
  if (typeof bank.soc === "number") {
    return { band: "good",  text: "healthy",      detail: "Cells balanced, SoC in range." };
  }
  return null;
}

// ---------- POWER FLOW ----------
function buildFlowModel() {
  // Frame-cache: applySnapshot resets _frame.flowModel every tick.
  // The dashboard renderFlow and the kiosk renderFlow both call us in
  // the same frame; without the cache we'd recompute identical state
  // and risk picking up a different staleness window on the boundary.
  if (_frame.flowModel !== null) return _frame.flowModel;
  const sources = [];
  const loads   = [];
  let batteryNetW = 0;

  for (const dev of devices) {
    const mapping = FLOW_MAPPING[dev.kind];
    if (!mapping) continue;
    const l = dev.latest || {};

    // Silent-device check (#171). Two cases collapse into one
    // "treat as silent" branch:
    //
    //  1. Stale broadcast, Victron BLE drivers stamp
    //     `advertisement_age_s` on every poll; > 60 s means we
    //     haven't decoded a fresh broadcast (charger switched off
    //     at the wall, dongle out of range, etc.).
    //
    //  2. Explicitly idle, the device IS still broadcasting fresh
    //     adverts but the payload reports it's not producing. Most
    //     common on Victron AC chargers in standby: the radio keeps
    //     blasting the LAST V/A readings (e.g. 13.7 V · 15.00 A ·
    //     206 W) forever even though `charging_state == "off"`.
    //     The fresh-advert age would otherwise fool the silent
    //     check and the tile would pretend 206 W is flowing.
    //
    // Both cases: power forced to 0, silent flag set, sub-label
    // explains which kind of silent it is.
    const ageS = (typeof l.advertisement_age_s === "number") ? l.advertisement_age_s : null;
    const isStale = ageS != null && ageS > 60;
    // Generic poll-freshness check, covers vendors that DON'T stamp
    // their own advertisement_age_s (Renogy and JK BMS today). Every
    // /api/devices entry carries `_updated_at` (Unix seconds) of the
    // last successful poll. If that's older than ~90 s, the transport
    // has been failing to reach the device and what we're rendering
    // is a snapshot from however long ago. Without this check, an
    // offline Renogy charge controller silently shows its last-known
    // V/A/W as if they were live, same lie as the old v0.1.15 AC
    // charger bug, just one vendor over.
    const STALE_POLL_SECONDS = 90;
    const updatedAt = +(l._updated_at) || +(dev.last_seen) || null;
    // Frame-stamped "now" so the staleness floor matches what
    // aggregateBank used for the same frame (#162).
    const nowSec = _frame.nowSec || (Date.now() / 1000);
    const pollAgeS = (updatedAt && nowSec > updatedAt) ? (nowSec - updatedAt) : null;
    const isPollStale = pollAgeS != null && pollAgeS > STALE_POLL_SECONDS;
    // Idle-state strings that mean "not actively producing watts".
    // Victron's ChargerState enum: OFF / LOW_POWER / FAULT are all
    // "not charging right now"; everything else (BULK, ABS, FLOAT,
    // STORAGE, EQUALIZE, INVERTING, POWER_SUPPLY) means active.
    const IDLE_CHARGE_STATES = new Set(["off", "low_power", "fault"]);
    const isExplicitlyIdle = (
      typeof l.charging_state === "string"
      && IDLE_CHARGE_STATES.has(l.charging_state.toLowerCase())
    );
    const isSilent = isStale || isExplicitlyIdle || isPollStale;
    const silentSub = (() => {
      if (!isSilent) return null;
      if (isPollStale && !isStale && !isExplicitlyIdle) {
        // No fresh poll for ≥90s. Cover Renogy / JK BMS / any vendor
        // that doesn't stamp advertisement_age_s itself. Wording mirrors
        // the Victron stale messages so the dashboard reads consistently.
        if (pollAgeS < 5400)  return `Stale, last poll ${Math.round(pollAgeS / 60)} min ago`;
        if (pollAgeS < 86000) return `Stale, last poll ${(pollAgeS / 3600).toFixed(1)} h ago`;
        return "Stale, no poll since restart";
      }
      if (isExplicitlyIdle && !isStale) {
        // Fresh broadcast says "I'm off", be specific rather than
        // pretending we haven't heard from it.
        if (l.charging_state === "off")       return "Off";
        if (l.charging_state === "low_power") return "Standby (low power)";
        if (l.charging_state === "fault")     return "Fault, not producing";
        return "Idle";
      }
      if (ageS < 90)     return `Silent, last heard ${Math.round(ageS)} s ago`;
      if (ageS < 5400)   return `Silent, last heard ${Math.round(ageS / 60)} min ago`;
      if (ageS < 86000)  return `Silent, last heard ${(ageS / 3600).toFixed(1)} h ago`;
      return "Silent, no broadcast since restart";
    })();

    // Battery power is deliberately not accumulated per device here: a shunt
    // and the BMS packs measure the same current, so summing every
    // battery-mapped device double-counted it on any rig with both (~2x "Into
    // battery"). The bank's net power comes from aggregateBank() below, which
    // picks one authoritative source (shunt, else BMS packs, else inverter)
    // and skips stale devices.
    // Sources: ALWAYS render configured sources, even when they're at 0 W.
    // A user who's set up an MPPT wants to see it on the dashboard at all
    // times, "0 W idle" is informative; hiding the tile makes the system
    // look misconfigured.
    //
    // Solar-pause overlay (#163): when the daemon's solar-pause rule
    // has just told this charger to go off (or is keeping it off), the
    // tile's sub-label says so explicitly instead of the bare "idle".
    // Without this hint the user sees an inverted-by-default charger
    // tile with no obvious reason.
    const acPausedByRule = (() => {
      if (!solarPauseStatus || dev.kind !== "ac_charger") return false;
      const sp = solarPauseStatus;
      if (!sp.charger_on === false) {
        // sp.charger_on is the most recent observed state; we use a
        // pause indicator when the rule's last decision was force_off
        // OR when it explicitly says the charger is currently paused
        // and SoC sits above the recover threshold.
        if (sp.decision === "force_off") return true;
        if (!sp.charger_on && sp.applied !== true && /paused/i.test(sp.reason || "")) return true;
      }
      return false;
    })();
    for (const s of mapping.sources || []) {
      if (s.onlyIf && !s.onlyIf(l)) continue;
      const w = isSilent ? 0 : (+l[s.metric] || 0);
      const subParts = isSilent ? [] : [
        typeof l[s.vMetric] === "number" ? `${l[s.vMetric].toFixed(1)} V` : null,
        typeof l[s.aMetric] === "number" ? `${l[s.aMetric].toFixed(2)} A` : null,
      ].filter(Boolean);
      const subFromState = silentSub
             || (w > 0 ? subParts.join(" · ")
                       : (subParts.length ? `${subParts.join(" · ")} · idle` : "idle"));
      sources.push({
        id: `${dev.label}.${s.id}`,
        label: s.label,
        device: dev.label,
        color: s.color,
        icon: s.icon || COLOR_TO_ICON[s.color],
        power: w,
        active: w > 1,
        silent: isSilent,
        // The solar-pause overlay only overrides the sub-label when
        // the charger is genuinely off (silent / idle). If the
        // charger is actively producing despite the rule saying
        // "force_off", keep showing the real V/A so the user can
        // tell the write hasn't landed yet.
        sub: (acPausedByRule && (isSilent || w <= 1))
             ? "Paused · solar covering"
             : subFromState,
      });
    }
    // Loads: keep the onlyIf filter, the Rover's load output really is
    // off for most users, and showing "DC Load: 0 W idle" would be clutter,
    // not signal. Inferred bus-loads are still surfaced separately below.
    for (const lo of mapping.loads || []) {
      if (lo.onlyIf && !lo.onlyIf(l)) continue;
      const w = isSilent ? 0 : (+l[lo.metric] || 0);
      if (w <= 0 && !lo.onlyIf && !isSilent) continue;
      loads.push({
        id: `${dev.label}.${lo.id}`,
        label: lo.label,
        device: dev.label,
        color: lo.color,
        icon: lo.icon || COLOR_TO_ICON[lo.color],
        power: w,
        active: w > 1,
        silent: isSilent,
        sub: [
          typeof l[lo.vMetric] === "number" ? `${l[lo.vMetric].toFixed(1)} V` : null,
          typeof l[lo.aMetric] === "number" ? `${l[lo.aMetric].toFixed(2)} A` : null,
        ].filter(Boolean).join(" · "),
      });
    }
  }

  const bank = aggregateBank();
  batteryNetW = bank ? bank.netW : 0;

  // Energy-balance inference: anything the bank is gaining/losing that we
  // can't account for from visible sources/loads is an unmeasured load
  // (heater wired to a busbar, fridge on a separate fuse, etc.) or an
  // unmeasured source (the Victron charger we don't have a driver for).
  //
  //   bank.netW = sources_in - loads_out
  //   loads_out = visible_loads + inferred
  //   ⇒ inferred = sources_in - bank.netW - visible_loads
  //
  // Positive  ⇒ an unmeasured LOAD of that magnitude.
  // Negative  ⇒ an unmeasured SOURCE of that magnitude.
  // Noise floor: a ±1 W imbalance is essentially sampling skew between
  // the MPPT poll and the battery poll, plus integer rounding. Anything
  // above this is genuine system load (or an unmeasured source).
  //
  // We always surface the inferred figure now, not just above some 10 W
  // threshold, otherwise the dashboard reads as "PV 71 W → battery 66 W →
  // load 0 W" and users (correctly) think the maths is broken. Showing
  // the small 5 W difference as load, with an "estimated" sub-label,
  // makes the totals reconcile and tells the truth: load is computed
  // from energy balance unless a real load meter is wired in.
  const INFERRED_NOISE_W = 1;
  if (bank) {
    const visibleSourcesW = sources.reduce((a, s) => a + s.power, 0);
    const visibleLoadsW   = loads.reduce((a, l) => a + l.power, 0);
    const inferred = visibleSourcesW - bank.netW - visibleLoadsW;
    const visibleLoadsActive = loads.length > 0;

    if (inferred > INFERRED_NOISE_W) {
      loads.push({
        id: visibleLoadsActive ? "_bus_load" : "_load",
        label: visibleLoadsActive ? "Bus loads" : "Load",
        color: "dc",
        icon: "house",
        power: inferred,
        active: inferred > 50,
        // Only flag as "inferred" (dashed border + asterisk) when it's
        // a secondary tile alongside something we measured directly.
        inferred: visibleLoadsActive,
        sub: "estimated",
      });
    } else if (inferred < -INFERRED_NOISE_W) {
      // Before falling back to a phantom "Other source" tile,
      // check whether we have exactly one silent source already.
      // That's the common Victron BLE case: the charger went
      // quiet on BLE but is still physically pushing watts that
      // the bank is measuring.  Attributing the gap back to the
      // silent device is more honest than rendering a separate
      // "Other source" alongside, the user sees one tile, knows
      // why it's marked estimated, and the maths still balances.
      const silentSources = sources.filter((s) => s.silent && (+s.power || 0) === 0);
      if (silentSources.length === 1) {
        const silent = silentSources[0];
        silent.power    = -inferred;
        silent.active   = -inferred > 50;
        silent.inferred = true;  // dashed border + asterisk
        // Keep the "Silent, last heard …" sub-label so the user
        // still sees why the value is estimated rather than measured.
        silent.sub = silent.sub
          ? `${silent.sub} · estimated from bank`
          : "estimated from bank";
      } else {
        const visibleSourcesActive = sources.length > 0;
        sources.push({
          id: visibleSourcesActive ? "_bus_source" : "_source",
          label: visibleSourcesActive ? "Other source" : "Source",
          color: "grid",
          icon: "feed",
          power: -inferred,
          active: -inferred > 50,
          inferred: visibleSourcesActive,
          sub: "estimated",
        });
      }
    } else if (!visibleLoadsActive) {
      // True idle: |inferred| ≤ noise floor and nothing measured.
      // Placeholder tile keeps the strip symmetric with the always-
      // visible Sources column.
      loads.push({
        id: "_load_idle",
        label: "Load",
        color: "dc",
        icon: "house",
        power: 0,
        active: false,
        sub: "estimated · idle",
      });
    }
  }

  const model = { sources, loads, batteryNetW, bank };
  _frame.flowModel = model;
  return model;
}

// Combined hero (#design): merge the donut hero into the reactor section.
// Default on; the in-panel toggle flips it (wp-combined). Set the body class
// at load so the hero is hidden via CSS without a flash.
try { document.body.classList.toggle("combined-flow", localStorage.getItem("wp-combined") !== "0"); } catch (_) {}

function renderFlow(targetHost) {
  // v2 Power-flow tile: SVG with animated particles along curves,
  // icon-only nodes at the perimeter, watts as labels *outside* the
  // nodes, and a separate battery card below the diagram. Modelled
  // on the Powerwall-Dashboard / Tesla-app aesthetic. The kiosk view
  // passes its own host so we can mount a second copy inside the
  // kiosk layout, the SVG scales with the host width.
  const host = targetHost || $("#flow");
  const sub  = host === $("#flow") ? $("#flow-sub") : null;
  const cap  = host === $("#flow") ? document.getElementById("flow-cap") : null;
  host.innerHTML = "";
  host.classList.remove("flow--idle");

  const model = buildFlowModel();
  if (!model.bank && model.sources.length === 0 && model.loads.length === 0) {
    host.innerHTML = `<div class="flow-empty">No active devices yet.</div>`;
    if (sub) sub.textContent = "";
    if (cap) cap.textContent = "";
    return;
  }

  const totalSourceW = model.sources.reduce((a, s) => a + s.power, 0);
  const totalLoadW   = model.loads.reduce((a, l) => a + l.power, 0);
  const hasSources = model.sources.length > 0;
  const hasLoads   = model.loads.length > 0;
  const battNetW   = Math.round(model.batteryNetW || 0);

  // Always render the battery as a node in the SVG when a bank
  // exists. Off-grid users want their battery visible at all times,
  // the "uncluttered diagram" intent of hiding it below 10 W made
  // the flow diagram lie about which devices were in the system and
  // people genuinely couldn't find their bank on the dashboard. The
  // battery is the heart of every off-grid install; it should never
  // disappear from the picture.
  const showBattInSvg = !!model.bank;

  // Battery-only topology, no sources, no loads (e.g. bank shunt + dumb packs).
  if (!hasSources && !hasLoads) {
    host.classList.add("flow--idle");
    if (model.bank) host.appendChild(buildBatCard(model.bank, battNetW));
    if (sub) sub.textContent = "no sources or loads configured";
    if (cap) cap.textContent = "";
    return;
  }

  const reactorOn = localStorage.getItem("wp-flow-reactor") !== "0";
  if (reactorOn) {
    host.appendChild(buildFlowReactor(model, { battNetW }));
    // 3-item breakdown instead of the battery bar — the reactor core already
    // shows SoC + state + watts, so the old bar was a duplicate.
    host.appendChild(buildFlowLegend(model, battNetW));
    // Combined hero: when merged (default), the donut hero is hidden (CSS) and
    // the leftover stats sit here under the reactor. Hidden localStorage
    // `wp-combined=0` fallback splits them back if ever needed.
    if (document.body.classList.contains("combined-flow")) host.appendChild(buildCombinedStats());
  } else {
    host.appendChild(buildFlowSvgV2(model, { showBattInSvg, battNetW }));
    if (model.bank) host.appendChild(buildBatCard(model.bank, battNetW));
  }

  const parts = [];
  if (hasSources) parts.push(`${model.sources.length} source${model.sources.length === 1 ? "" : "s"} · ${totalSourceW.toFixed(0)} W in`);
  if (hasLoads)   parts.push(`${model.loads.length} load${model.loads.length === 1 ? "" : "s"} · ${totalLoadW.toFixed(0)} W out`);
  if (sub) sub.textContent = parts.join(" · ") || "system idle";
  if (cap) cap.textContent = flowCaption(model, totalSourceW, totalLoadW);
}

// ───────── v2 SVG flow diagram builders ─────────
//
// Layout: 400×260 viewBox. Sources arc across the top, the bus is a
// notional point at (200, 145), loads (and the battery node when
// shown) arc across the bottom. Smooth bezier connectors between
// each node and the bus. Particles travel along active paths via
// SMIL <animateMotion>; speed scales with W magnitude so heavy
// flows visibly stream faster.

const SVG_NS = "http://www.w3.org/2000/svg";
// v3 layout, grew viewBox + node radius to give the diagram more
// presence. Old 400×260 with r=22 felt cramped on mobile;
// 440×290 with r=28 reads as a hero, not a footer chart.
const FLOW_W = 440, FLOW_H = 290;
const FLOW_BUS_X = FLOW_W / 2, FLOW_BUS_Y = 160;
const FLOW_SRC_Y = 70, FLOW_BOT_Y = 240;
const FLOW_NODE_R = 28;            // main ring radius (was 22)
const FLOW_NODE_ICON = 28;         // inner icon edge length (was 22)
const FLOW_BATT_SOC_R = 33;        // SoC arc just outside the ring
const FLOW_JUNCTION_R = 5;         // explicit centre-node radius
// Colour stops keyed by colorKeyOf(), drive the gradient defs on
// each active path so a segment visibly originates in its source
// colour and arrives in its destination colour. Single source of
// truth; CSS still drives the static node ring colours.
const FLOW_COLOR = {
  pv:        "#f0c849",
  batt:      "#56d364",
  load:      "#58a6ff",
  grid:      "#b1b9c2",
  ac:        "#b1b9c2",
  dc:        "#d29922",
  amber:     "#f0c849",
  discharge: "#f06292",
  red:       "#ff7b72",
  neutral:   "#6c7689",
  silent:    "#6c7689",
};
