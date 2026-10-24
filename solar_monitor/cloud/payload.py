"""Builds the heartbeat payload (battery, energy, extras) for CloudService."""

from __future__ import annotations

import logging
from typing import Any


log = logging.getLogger(__name__)


def _bank_extras(bank: dict) -> dict:
    """Bank-level readings the heartbeat carries for the cloud's public API
    (the Home Assistant integration's voltage / current / remaining-Ah /
    temperature sensors). Only numeric values that exist are copied, so a bank
    without a shunt simply omits them."""
    out: dict = {}
    for key in ("voltage_v", "current_a", "remaining_ah", "temperature_c"):
        v = bank.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf"):
            out[key] = round(float(v), 3)
    cap, soc = bank.get("capacity_ah"), bank.get("soc_pct")
    if ("remaining_ah" not in out and isinstance(cap, (int, float)) and cap > 0
            and isinstance(soc, (int, float))):
        out["remaining_ah"] = round(float(cap) * float(soc) / 100.0, 2)
    return out


class PayloadMixin:
    """Methods of CloudService; see service.py."""


    async def _build_payload(self) -> dict[str, Any]:
        """Pull SoC + net power from the store's `bank` pseudo-device.
        Defensive about every step, a half-built snapshot during
        startup should not crash the heartbeat task.

        Why the store and not scheduler.last_result: `last_result` is
        the raw poll output (real devices: battery_0, rover_mppt etc).
        The aggregate "bank" pseudo-device is computed *inside*
        record_poll() and lives in the `latest` table; the heartbeat
        was previously looking for it in last_result and finding
        nothing → soc_pct + net_w shipped as nulls.
        """
        import time
        soc_pct = None
        net_w = None
        try:
            store = getattr(self.scheduler, "store", None)
            if store is not None:
                latest = await store.get_latest()
                bank = latest.get("bank") or {}
                soc_pct = bank.get("soc_pct")
                net_w   = bank.get("power_w")
        except Exception:
            log.exception("cloud heartbeat: could not read bank state")

        # Free-form extras for the cloud dashboard to render later.
        # Keep this concise: the cloud caps extras at 8 KiB and the
        # byte-budget trim at the end of this method sheds the biggest
        # non-essential keys if we run over, so anything bulky added
        # here is best-effort and may be dropped on a busy beat.
        extras: dict[str, Any] = {}
        # BLE adapter health (#244). Surfaces "wedged" Realtek dongles
        # to the cloud so users see "Bluetooth dongle not responding"
        # rather than every Victron device independently going silent.
        try:
            from ..transport.ble_victron_advertise import adapter_health
            extras["ble_adapter_state"] = adapter_health()
        except Exception:
            pass
        try:
            from .. import __version__
            extras["version"] = __version__
        except Exception:
            pass
        # Tell the cloud whether we're a Pi or Docker install. Used
        # by the dashboard to hide the cloud-triggered Update button
        # on Docker installs (where wattpost-update isn't bundled).
        # WATTPOST_DEPLOYMENT is set to 'docker' by docker-compose.yml;
        # the pi-gen image leaves it unset, which the cloud reads as
        # 'pi'.
        import os as _os
        extras["deployment"] = "docker" if _os.environ.get("WATTPOST_DEPLOYMENT") == "docker" else "pi"
        # #267, host-health snapshot (disk, memory, load, uptime,
        # hostname, LAN IP). Cheap stdlib reads; the cloud renders
        # this as a Device-health card on /app/site/{id} and uses it
        # for fleet-view "disk almost full" warnings.
        try:
            from .. import host_health as _hh
            extras["host_health"] = _hh.snapshot()
        except Exception:
            log.exception("cloud heartbeat: host_health snapshot failed")
        # #263/#264, location (lat/lon) gated by LocationCfg.
        # OPT-IN by default; returns None unless the customer has
        # explicitly set share_with_cloud to "approx" or "precise".
        # Approx mode rounds on this side BEFORE transmission so the
        # cloud never sees precise coords from an approx user.
        try:
            from .. import location as _loc
            loc_payload = _loc.location_for_cloud(self.scheduler, self._config)
            if loc_payload is not None:
                extras["location"] = loc_payload
        except Exception:
            log.exception("cloud heartbeat: location resolution failed")
        # Active kiosk skin so the cloud can preview what a shared kiosk
        # link will display ("this share shows the Command skin") without
        # asking the appliance. Cheap (~20 bytes); defaults
        # to halo when unset.
        try:
            _kcfg = getattr(self._config, "kiosk", None)
            if _kcfg is not None and getattr(_kcfg, "skin", None):
                extras["kiosk_skin"] = _kcfg.skin
        except Exception:
            pass
        try:
            alert_count = len([
                r for r in (getattr(self.scheduler._alerts, "rules", []) or [])
                if r.id in getattr(self.scheduler._alerts, "_last_fired", {})
            ])
            extras["alert_count"] = alert_count
        except Exception:
            pass

        # Phase 8B (#310), appliance-signed audit log entries waiting
        # to sync to cloud. Cloud verifies signature + hash chain +
        # ingests; ack list comes back in the heartbeat response so we
        # can flip uploaded_at. The old fixed limit=50 was ~7.3 KB and
        # blew the cloud's 8 KiB extras cap on a box with a backlog —
        # 413'ing every heartbeat so the box never came online. Row
        # size varies with event_payload, so a fixed *count* isn't
        # enough; byte-bound the batch instead. It ships what fits and
        # drains the backlog over successive beats. The _build_payload
        # byte-budget trim below is the backstop.
        try:
            from .. import signed_audit as _sa
            import json as _json
            store = self.scheduler.store
            if store is not None and store._db is not None:
                pending = await _sa.fetch_pending(store._db, limit=50)
                if pending:
                    _budget, _size, _kept = 3500, 0, []
                    for _e in pending:
                        _s = len(_json.dumps(_e, separators=(",", ":")).encode("utf-8"))
                        if _kept and _size + _s > _budget:
                            break
                        _kept.append(_e)
                        _size += _s
                    extras["signed_audit_pending"] = _kept
        except Exception:
            log.exception("signed_audit: fetch_pending failed")

        # Local alert rules snapshot (#261 unification slice 1).
        # Surfaces this appliance's currently-configured rules in the
        # cloud Rules UI as read-only rows with a "Runs locally" chip.
        # Editing them from the cloud (slice 2) writes back via the
        # appliance_commands queue; for now this is just the read path.
        # Schema mirrors AlertRulePayload so cloud can render without
        # a translation layer.
        try:
            engine = getattr(self.scheduler, "_alerts", None)
            last_fired = getattr(engine, "_last_fired", {}) if engine else {}
            # Cap at source: a box with hundreds of rules would otherwise
            # make this the biggest extras key and get it shed by the
            # byte-budget trim every beat, flapping the cloud Rules UI.
            rules = list(self._all_rules)[:50]
            if rules:
                extras["local_alert_rules"] = [
                    {
                        "id":               r.id,
                        "name":             r.name,
                        "metric":           r.metric,
                        "op":               r.op,
                        "threshold":        r.threshold,
                        "severity":         r.severity,
                        "cooldown_seconds": r.cooldown_seconds,
                        "transports":       list(r.transports or []),
                        "last_fired_ts":    int(last_fired.get(r.id, 0)) or None,
                    }
                    for r in rules
                ]
        except Exception:
            log.exception("cloud heartbeat: local_alert_rules collection failed")

        # #252 slice 1, ship today's energy totals + the last 24h of
        # hourly buckets so the cloud Energy page can render the same
        # multi-series chart we ship locally PLUS accumulate week /
        # month / year history. Two payloads:
        #
        #   energy_today       , totals + self-powered breakdown for
        #                          the current local calendar day.
        #                          ~150 bytes. Today-tile food.
        #   energy_hourly_24h  , parallel arrays: ts + per-series.
        #                          24 hourly buckets, ~600 bytes.
        #                          Cloud accumulates these into a
        #                          per-site history table.
        #
        # Both lifted from compute_energy() (the helper extracted from
        # the existing /api/energy/today endpoint).
        try:
            from ..api.energy import compute_energy
            store = getattr(self.scheduler, "store", None)
            if store is not None:
                # Local-day window (default args).
                today = await compute_energy(store)
                extras["energy_today"] = {
                    "totals":       today.get("totals", {}),
                    "self_powered": today.get("self_powered", {}),
                }
                # Last 24 hours at hourly resolution.
                now_ts = int(time.time())
                hourly = await compute_energy(
                    store,
                    since=now_ts - 24 * 3600,
                    until=now_ts,
                    bucket=3600,
                )
                s = hourly.get("series", {})
                ts_list = s.get("ts") or []
                if ts_list:
                    def _r(v: float | None) -> float | None:
                        return None if v is None else round(float(v), 1)
                    extras["energy_hourly_24h"] = {
                        "ts":        [int(t) for t in ts_list],
                        "solar_w":   [_r(v) for v in (s.get("solar_w")   or [])],
                        "charger_w": [_r(v) for v in (s.get("charger_w") or [])],
                        "bank_w":    [_r(v) for v in (s.get("bank_w")    or [])],
                        "soc_pct":   [_r(v) for v in (s.get("soc_pct")   or [])],
                    }
        except Exception:
            log.exception("cloud heartbeat: energy aggregation failed")

        # Cloud alerts inbox (#206): ship recent events from the engine's
        # ring buffer so the cloud can render a per-account feed across
        # every site. Cap to last 20 since the previous successful
        # heartbeat; dedup is cloud-side via UNIQUE(appliance_id,
        # rule_id, ts) so retransmits on a flaky link are a no-op.
        try:
            engine = getattr(self.scheduler, "_alerts", None)
            since_ts = int(getattr(self, "_alerts_uploaded_ts", 0))
            if engine is not None and hasattr(engine, "recent_events_since"):
                # Belt-and-suspenders [:20]: don't rely solely on the
                # engine honouring `limit` (a fresh process has
                # _alerts_uploaded_ts unset → since_ts=0 → full ring buffer).
                events = (engine.recent_events_since(since_ts, limit=20) or [])[:20]
                if events:
                    # Map rule_id → transports so cloud knows which
                    # events the user wants fanned out via cloud
                    # channels (web push + native push + email). When
                    # the rule's transports include "cloud" (#259),
                    # the cloud's existing notification fan-out fires
                    # on ingest using the user's notification prefs.
                    rule_transports = {
                        r.id: list(r.transports or [])
                        for r in self._all_rules
                    }
                    extras["recent_alerts"] = [
                        {
                            "rule_id":    e.rule_id,
                            "name":       e.name,
                            "severity":   e.severity,
                            "metric":     e.metric,
                            "value":      e.value,
                            "threshold":  e.threshold,
                            "op":         e.op,
                            "ts":         e.ts,
                            "transports": rule_transports.get(e.rule_id, []),
                        }
                        for e in events
                    ]
                    # Will be promoted to _alerts_uploaded_ts on
                    # successful POST response (see below).
                    self._alerts_pending_ts = max(e.ts for e in events)
        except Exception:
            log.exception("cloud heartbeat: recent_alerts collection failed")
        # Today's energy aggregates, surface on the cloud card so the
        # user can see "RV: 1.4 kWh in, 0.6 kWh out today" without
        # opening the local site. One DB read per heartbeat (~5 min)
        # is cheap; failure to read is non-fatal.
        try:
            store = getattr(self.scheduler, "store", None)
            if store is not None:
                now = int(time.time())
                local = time.localtime(now)
                midnight = int(time.mktime(
                    (local.tm_year, local.tm_mon, local.tm_mday,
                     0, 0, 0, 0, 0, -1)
                ))
                tot = await store.today_aggregate(midnight, now)
                # Round to whole Wh, the cloud renders in kWh anyway.
                # `sources_today_wh` = PV + AC charger + DC-DC (the
                # headline "Today in" the cloud shows). Keep
                # `pv_today_wh` for backwards compat with older cloud
                # builds. The per-source breakdown lets the card show
                # "1.7 PV + 0.9 AC" rather than a single lump, which
                # is the difference between "great solar day" and
                # "mostly grid-fed". `bank_net_today_wh` powers the
                # "Stored today" headline the user actually cares
                # about (in − out, signed: positive = bank gained,
                # negative = bank depleted today).
                extras["pv_today_wh"]         = int(tot.get("pv_today_wh") or 0)
                extras["ac_charger_today_wh"] = int(tot.get("ac_charger_today_wh") or 0)
                extras["dcdc_today_wh"]       = int(tot.get("dcdc_today_wh") or 0)
                extras["sources_today_wh"]    = int(tot.get("sources_today_wh") or 0)
                extras["load_today_wh"]       = int(tot.get("load_today_wh") or 0)
                extras["bank_net_today_wh"]   = int(tot.get("bank_net_today_wh") or 0)
                # Today's SoC envelope, answers "did the bank get
                # critically low overnight?" at a glance, without
                # opening History. One cheap SELECT per heartbeat.
                try:
                    soc_lo, soc_hi = await store.bank_soc_minmax(midnight, now)
                    if soc_lo is not None:
                        extras["soc_min_today_pct"] = round(soc_lo, 1)
                    if soc_hi is not None:
                        extras["soc_max_today_pct"] = round(soc_hi, 1)
                except Exception:
                    log.exception("cloud heartbeat: soc minmax failed")

                # Re-fetch latest for the next two blocks (it was only
                # in scope for the earlier soc_pct / net_w extraction).
                latest_for_extras = await store.get_latest()

                # Instantaneous power split for the cloud hero's flow
                # diagram. Sources = solar (pv_power_w) + AC charger
                # (output_1_power_w) + DC-DC (output_power_w), summed
                # across devices (same kinds the energy totals use).
                #
                # Load is the ENERGY-BALANCE identity the local dashboard
                # uses — load = max(0, sources − net_w) — NOT the charge
                # controller's load terminal (load_power_w), which is
                # usually 0 because household loads sit on a busbar the
                # controller can't meter. (net_w >0 charging, <0
                # discharging, so a discharging bank adds to the load.)
                try:
                    pv_w = 0.0
                    sources_w = 0.0
                    for _dm in latest_for_extras.values():
                        if not isinstance(_dm, dict):
                            continue
                        _pv = _dm.get("pv_power_w")
                        _ac = _dm.get("output_1_power_w")
                        _dc = _dm.get("output_power_w")
                        if isinstance(_pv, (int, float)):
                            pv_w += float(_pv)
                            sources_w += float(_pv)
                        if isinstance(_ac, (int, float)):
                            sources_w += float(_ac)
                        if isinstance(_dc, (int, float)):
                            sources_w += float(_dc)
                    extras["pv_w"] = round(pv_w)
                    extras["sources_w"] = round(sources_w)
                    if isinstance(net_w, (int, float)):
                        extras["load_w"] = round(max(0.0, sources_w - float(net_w)))
                except Exception:
                    log.debug("cloud heartbeat: pv/load aggregate failed", exc_info=True)

                # Time to empty (discharging) or time to full (charging),
                # in minutes. Uses the same rolling-hour load average as
                # the runtime-forecast endpoint, so it's the same number
                # the local dashboard would show, keeps cloud + local
                # consistent. Skipped entirely when the bank is idle
                # (-5 .. +5 W) or capacity is unknown.
                try:
                    bank_state = latest_for_extras.get("bank") or {}
                    extras.update(_bank_extras(bank_state))
                    cap_ah  = bank_state.get("capacity_ah")
                    voltage = bank_state.get("voltage_v") or 12.8
                    soc_now = bank_state.get("soc_pct")
                    if (isinstance(cap_ah, (int, float)) and cap_ah > 0
                            and isinstance(soc_now, (int, float))):
                        bank_wh = float(cap_ah) * float(voltage)
                        avg_w = await store.rolling_load_avg(3600)
                        if avg_w is not None:
                            if avg_w < -5:  # discharging
                                # 10% reserve, don't predict past LFP minimum
                                usable_wh = bank_wh * max(0.0, float(soc_now) - 10.0) / 100.0
                                if usable_wh > 0:
                                    extras["time_to_empty_min"] = int(usable_wh / abs(avg_w) * 60)
                            elif avg_w > 5:  # charging
                                empty_wh = bank_wh * (1.0 - float(soc_now) / 100.0)
                                if empty_wh > 0:
                                    extras["time_to_full_min"] = int(empty_wh / float(avg_w) * 60)
                except Exception:
                    log.exception("cloud heartbeat: time-to-empty/full failed")

                # Charger state pill, surface the BANK-LEVEL stage,
                # not whichever device's label happened to sort first
                # in get_latest(). On a multi-charger install (MPPT +
                # AC charger + DC-DC) different chargers can be in
                # different stages at the same instant, MPPT may
                # have hit absorb voltage while AC charger is still
                # in bulk, etc.
                #
                # Pick the most-active stage across every online
                # charger. The pill is meant to answer the user's
                # mental-model question "is my bank charging hard
                # right now, or just maintaining?", so if ANY
                # charger is in bulk, the answer is "bulk".
                #
                # Skip silent devices (≥10 min since last broadcast):
                # a stale "bulk" from a dead BLE radio would otherwise
                # poison the aggregate. Same 10-min threshold the
                # devices snapshot uses for the online flag.
                STAGE_PRIORITY = (
                    "bulk", "mppt", "absorption", "equalize",
                    "float", "storage", "low_power", "off", "fault",
                )
                try:
                    now_ts = int(time.time())
                    stages: list[str] = []
                    for _label, dev in latest_for_extras.items():
                        if not isinstance(dev, dict):
                            continue
                        last_seen = int(dev.get("_last_seen") or 0)
                        if (now_ts - last_seen) >= 600:
                            continue
                        st = dev.get("charging_state")
                        if st:
                            stages.append(str(st).lower())
                    if stages:
                        def _rank(s: str) -> int:
                            try:
                                return STAGE_PRIORITY.index(s)
                            except ValueError:
                                return len(STAGE_PRIORITY)  # unknowns last
                        winner = min(stages, key=_rank)
                        extras["charger_state"] = winner[:16]
                except Exception:
                    pass

                # Per-device snapshot for the mobile per-site dashboard
                # (#238). One concise row per device, name, vendor,
                # kind, online flag, headline value. Capped at 8
                # devices and ~400 bytes total to stay inside the
                # 2 KiB extras budget. Headline value differs by kind:
                # battery → SoC%, charger → power, shunt → current,
                # etc.  Falls back to the first metric we can render.
                try:
                    devs_payload: list[dict[str, Any]] = []
                    now_ts = int(time.time())
                    for label, dev in latest_for_extras.items():
                        if not isinstance(dev, dict):
                            continue
                        kind = str(dev.get("_kind") or "").lower()
                        vendor = str(dev.get("_vendor") or "")
                        last_seen = int(dev.get("_last_seen") or 0)
                        # "Online" if the device last reported within
                        # 3× the poll cadence, generous enough to
                        # avoid false offlines on BLE re-scan, tight
                        # enough that a truly-silent device shows.
                        online = (now_ts - last_seen) < 600
                        headline: dict[str, Any] | None = None
                        # Order matters, first match wins.  Battery
                        # SoC trumps everything; chargers report power;
                        # shunts pick current.  Falls through to bus
                        # voltage as a last resort.
                        if "soc_pct" in dev and isinstance(dev["soc_pct"], (int, float)):
                            headline = {"k": "SoC", "v": round(float(dev["soc_pct"]), 1), "u": "%"}
                        elif "pv_power_w" in dev and isinstance(dev["pv_power_w"], (int, float)):
                            headline = {"k": "PV", "v": round(float(dev["pv_power_w"])), "u": "W"}
                        elif "output_power_w" in dev and isinstance(dev["output_power_w"], (int, float)):
                            headline = {"k": "Power", "v": round(float(dev["output_power_w"])), "u": "W"}
                        elif "ac_input_power_w" in dev and isinstance(dev["ac_input_power_w"], (int, float)):
                            headline = {"k": "AC in", "v": round(float(dev["ac_input_power_w"])), "u": "W"}
                        elif "current_a" in dev and isinstance(dev["current_a"], (int, float)):
                            headline = {"k": "Current", "v": round(float(dev["current_a"]), 1), "u": "A"}
                        elif "battery_voltage_v" in dev and isinstance(dev["battery_voltage_v"], (int, float)):
                            headline = {"k": "Voltage", "v": round(float(dev["battery_voltage_v"]), 2), "u": "V"}
                        devs_payload.append({
                            "name":   str(label)[:32],
                            "kind":   kind[:16],
                            "vendor": vendor[:16],
                            "online": online,
                            "h":      headline,
                        })
                    if devs_payload:
                        extras["devices"] = devs_payload[:8]
                except Exception:
                    log.exception("cloud heartbeat: devices snapshot failed")
        except Exception:
            log.warning("cloud heartbeat: today_aggregate read failed",
                        exc_info=True)

        # Weather snapshot + PV forecast totals. Both live in the
        # local SQLite kv table (the same cache the dashboard reads
        # from), so this is a cheap read, no third-party calls per
        # heartbeat. Keep the field set tight (~5 fields, ~100 bytes)
        # so the 8 KiB extras cap isn't a concern.
        try:
            store = getattr(self.scheduler, "store", None)
            if store is not None:
                wx = await store.kv_get("weather:current")
                if wx is not None:
                    import json as _json
                    w = _json.loads(wx[0])
                    if w.get("temperature_c") is not None:
                        extras["weather_temp_c"] = round(float(w["temperature_c"]), 1)
                    if w.get("weather_code") is not None:
                        # Raw WMO code; cloud renders the label so we
                        # don't need to ship a 30-entry lookup table
                        # in every heartbeat.
                        extras["weather_code"] = int(w["weather_code"])
                    if w.get("sunset_ts") is not None:
                        extras["sunset_unix"] = int(w["sunset_ts"])
        except Exception:
            log.warning("cloud heartbeat: weather snapshot read failed",
                        exc_info=True)
        try:
            store = getattr(self.scheduler, "store", None)
            if store is not None:
                fc = await store.kv_get("forecast:pv")
                if fc is not None:
                    import json as _json
                    f = _json.loads(fc[0])
                    pts = f.get("points") or []
                    # Sum Wh expected for today (local) + tomorrow.
                    # Solcast points are 30-min periods in W; Wh =
                    # W × 0.5h per point. Round to whole Wh.
                    now_ts = int(time.time())
                    local_now = time.localtime(now_ts)
                    tom = int(time.mktime((
                        local_now.tm_year, local_now.tm_mon, local_now.tm_mday + 1,
                        0, 0, 0, 0, 0, -1,
                    )))
                    day_after = tom + 86400
                    today_wh = 0.0
                    tomorrow_wh = 0.0
                    for p in pts:
                        ts = int(p.get("ts") or 0)
                        w_val = float(p.get("pv_w") or 0)
                        wh = w_val * 0.5
                        if now_ts <= ts < tom:
                            today_wh += wh
                        elif tom <= ts < day_after:
                            tomorrow_wh += wh
                    if today_wh > 0:
                        extras["forecast_today_wh"] = int(today_wh)
                    if tomorrow_wh > 0:
                        extras["forecast_tomorrow_wh"] = int(tomorrow_wh)
        except Exception:
            log.warning("cloud heartbeat: forecast snapshot read failed",
                        exc_info=True)

        # Keep extras under the cloud's cap. The core fields (soc_pct,
        # net_w) plus the small identity extras MUST get through so the
        # box registers online. The bulky render-only enrichment is best-effort:
        # shed the single biggest non-essential key, repeatedly, until
        # we're under budget. Size-based (not a fixed key order) so it
        # adapts to whatever is heavy on a given box — on a busy rig the
        # signed-audit backlog dominated, on others it might be energy
        # history or device snapshots. Without this a busy multi-device
        # install overflows and the cloud 413s EVERY heartbeat — the box
        # then looks permanently offline. Each shed key is re-sent next beat, so enrichment
        # converges once the transient pressure passes. Budget sits a
        # margin under the cloud's _EXTRAS_MAX_BYTES (8 KiB).
        import json as _json
        _EXTRAS_BUDGET = 7800
        # Never shed these: small, and load-bearing for identity.
        _EXTRAS_KEEP = {
            "version", "deployment", "kiosk_skin", "alert_count",
        }

        def _extras_bytes() -> int:
            return len(_json.dumps(extras, separators=(",", ":")).encode("utf-8"))

        def _ksize(_k: str) -> int:
            return len(_json.dumps({_k: extras[_k]}, separators=(",", ":")).encode("utf-8"))

        shed: list[str] = []
        while _extras_bytes() > _EXTRAS_BUDGET:
            cands = [k for k in extras if k not in _EXTRAS_KEEP]
            if not cands:
                break  # everything left is mandatory; let the cloud judge
            biggest = max(cands, key=_ksize)
            extras.pop(biggest, None)
            shed.append(biggest)
            if biggest == "recent_alerts":
                # Never actually uploaded — clear the pending marker so
                # heartbeat_once's post-2xx promotion doesn't advance the
                # cursor past them (see _alerts_pending_ts there). They
                # ship on the next heartbeat instead.
                self._alerts_pending_ts = None
        if shed:
            log.warning("cloud heartbeat: extras over budget, shed %s (now %dB)",
                        shed, _extras_bytes())

        return {
            "soc_pct": soc_pct,
            "net_w":   net_w,
            "extras":  extras,
        }
