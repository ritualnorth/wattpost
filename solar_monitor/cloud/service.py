"""Background heartbeat poster.

Reads the latest bank snapshot from the scheduler's `last_result`
plus today's energy aggregates from the scheduler, packages them
into a small JSON payload, and POSTs to `<endpoint>/api/heartbeat`
with the bearer token.

Failures are swallowed — losing internet must not break the local
dashboard. Each failure is logged at WARNING for diagnostics.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..config import CloudCfg

log = logging.getLogger(__name__)


class CloudService:
    def __init__(self, config_or_cfg, scheduler) -> None:
        """Accepts either a Config (preferred) or a bare CloudCfg
        (legacy callers — Settings save still passes one in). When
        given a Config we hold a reference to the parent so reading
        `self.cfg` always reflects the current `config.cloud` — even
        after Settings save rebinds the parent's `.cloud` attribute
        to a freshly-built CloudCfg. Otherwise a heartbeat firing
        after a user clicked Save in Settings → Cloud could mutate
        a stale CloudCfg, persist its (outdated) state back to
        config.yaml, and quietly drift the in-memory SSO secret away
        from what /sso reads on the request path (#148).
        """
        from ..config import Config as _Config
        if isinstance(config_or_cfg, _Config):
            self._config = config_or_cfg
            self._direct_cfg = None
        else:
            self._config = None
            self._direct_cfg = config_or_cfg
        self.scheduler = scheduler
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    @property
    def cfg(self) -> CloudCfg:
        """Always returns the live CloudCfg the rest of the daemon
        reads from. When constructed from a Config (the normal
        scheduler path), this resolves via the parent so a Settings
        save that did `config.cloud = new_c` is visible immediately."""
        if self._config is not None:
            return self._config.cloud
        return self._direct_cfg

    async def start(self) -> None:
        if not self.cfg.bearer_token:
            log.info("cloud: no bearer_token configured — skipping heartbeat loop")
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="cloud-heartbeat")
        log.info("cloud heartbeat service started (endpoint=%s, every %dm)",
                 self.cfg.endpoint, self.cfg.heartbeat_minutes)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def heartbeat_once(self) -> bool:
        """Build + send one heartbeat. Returns True on 2xx, False on
        anything else. Used by the loop and also exposed for the
        Settings UI's "Send heartbeat now" button.

        Also dispatches any commands the cloud handed back. Dispatch
        happens AFTER the heartbeat returns 2xx — so a flaky network
        round-trip doesn't half-execute a command. Each command's
        status transitions are PATCHed back to the cloud as the
        appliance progresses through pick-up → apply → terminal."""
        payload = await self._build_payload()
        url = f"{self.cfg.endpoint.rstrip('/')}/api/heartbeat"
        headers = {
            "Authorization": f"Bearer {self.cfg.bearer_token}",
            "Content-Type":  "application/json",
        }
        try:
            # follow_redirects=True so an appliance still pointing at
            # an older hostname (e.g. https://wattpost.io after we
            # moved the API to wattpost.cloud) succeeds via the 308
            # rather than silently 308-ing into a no-op. POST → POST
            # is method-preserving under 308 by spec.
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=True,
            ) as client:
                r = await client.post(url, json=payload, headers=headers)
        except Exception as e:
            log.warning("cloud heartbeat failed: %s", e)
            return False
        if r.status_code >= 400:
            log.warning("cloud heartbeat HTTP %s: %s", r.status_code, r.text[:200])
            return False

        # Dispatch any commands the cloud queued for us. Best-effort
        # — failures dispatching one command shouldn't stop the
        # heartbeat from being considered successful, since the
        # heartbeat write itself already succeeded.
        try:
            body = r.json()
            commands = body.get("commands") or []
            for cmd in commands:
                # Spawn as a task so a long-running command (e.g. an
                # update that takes 30s) doesn't block the next
                # scheduled heartbeat. The dispatcher does its own
                # serialization within a single command type.
                asyncio.create_task(self._dispatch_command(cmd))
            # Cache the owner's white-label branding (Installer tier)
            # so the local dashboard can render the custom brand
            # without a separate round-trip per page load. Stored in
            # the kv table (the same one the forecast service uses)
            # under key `cloud.branding`. Hobby/Pro accounts → empty
            # dict, which clears any previously-cached brand.
            self._cache_branding(body.get("branding") or {})
            # SSO secret distribution (#137). Cloud always echoes this
            # back; if our local copy is empty (legacy pair, or first
            # heartbeat post-update), persist it to config.yaml so the
            # /sso endpoint can verify cloud-signed redirect tokens.
            self._maybe_persist_sso_secret(body.get("sso_secret"))
        except Exception as e:
            log.warning("cloud heartbeat: failed to parse response body: %s", e)
        return True

    def _maybe_persist_sso_secret(self, sso_secret: str | None) -> None:
        """Save the cloud-issued SSO HMAC key if we don't already have
        one. Idempotent — if our copy matches, no-op. If it differs
        (cloud rotated), trust the cloud and update. Persistence goes
        through the same config.yaml write path the pair flow uses,
        so the appliance survives restarts."""
        if not sso_secret or not isinstance(sso_secret, str):
            return
        if self.cfg.sso_secret == sso_secret:
            return
        log.info("cloud heartbeat: caching SSO secret (was empty=%s)",
                 not self.cfg.sso_secret)
        try:
            # Mutate the live config struct in place AND persist to
            # config.yaml so the new secret survives daemon restart.
            self.cfg.sso_secret = sso_secret
            # Delegate the file write to the cloud_admin helper so the
            # YAML round-trip stays consistent across all writers.
            from ..api import cloud_admin as _ca
            cfg_path = getattr(self.scheduler, "config_path", None)
            _ca.persist_cloud_cfg(self.cfg, config_path=cfg_path)
        except Exception:
            log.exception("cloud heartbeat: failed to persist sso_secret")

    def _cache_branding(self, branding: dict[str, Any]) -> None:
        """Persist the {brand_name, brand_support_email, brand_logo_url}
        triple in the appliance's kv table. The /api/branding endpoint
        reads it back for the dashboard. Schema-less / additive so a
        future white-label field doesn't need a migration."""
        try:
            store = self.scheduler.store
            import json
            payload = json.dumps({
                k: branding.get(k) or None
                for k in ("brand_name", "brand_support_email", "brand_logo_url")
            })
            # The store has a kv_set helper that the forecast service
            # already uses; same write path.
            asyncio.create_task(store.kv_set("cloud.branding", payload))
        except Exception as e:
            log.debug("cloud heartbeat: failed to cache branding: %s", e)

    async def _dispatch_command(self, cmd: dict[str, Any]) -> None:
        """Apply a single cloud-queued command. Reports status
        transitions back to /api/heartbeat/command/{id} as it goes.

        Handled kinds:
          update      — run wattpost-update (Pi installs only)
          backup_now  — snapshot + upload to cloud (#165)

        Unknown kinds get marked failed with a clear error message
        so they don't sit forever as 'queued' on the dashboard."""
        cmd_id = cmd.get("id")
        kind   = cmd.get("kind")
        if not isinstance(cmd_id, int):
            log.warning("cloud command missing id: %r", cmd)
            return

        if kind == "backup_now":
            await self._dispatch_backup_now(cmd_id)
            return

        if kind != "update":
            await self._patch_command_status(
                cmd_id, "failed",
                error=f"appliance doesn't handle kind={kind!r}",
            )
            return

        # Docker installs can't run wattpost-update — that helper
        # only exists on Pi installs (where it's bundled by pi-gen).
        # Fail fast and visibly rather than letting the user think
        # we're "applying…" for an action we can't take.
        import os
        if os.environ.get("WATTPOST_DEPLOYMENT") == "docker":
            await self._patch_command_status(
                cmd_id, "failed",
                error="cloud-triggered updates are not supported on "
                      "Docker installs — run `docker compose pull && "
                      "docker compose up -d` on the host instead",
            )
            return

        await self._patch_command_status(cmd_id, "picked_up")
        await self._patch_command_status(cmd_id, "applying")
        # Invoke wattpost-update detached — it'll restart this
        # daemon mid-flight, so we have no way to await it OR to
        # PATCH the terminal status from here. The cloud auto-
        # reconciles: when the next heartbeat arrives with a newer
        # `version` field, the server marks any `applying` update
        # commands as success. A 10-minute server-side watchdog
        # marks the rest as failed if no heartbeat lands.
        try:
            await asyncio.create_subprocess_exec(
                "wattpost-update",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            log.info("cloud update: wattpost-update spawned for cmd %d", cmd_id)
        except Exception as e:
            log.exception("cloud update: failed to spawn wattpost-update")
            await self._patch_command_status(
                cmd_id, "failed",
                error=f"failed to start updater: {type(e).__name__}: {e}",
            )

    async def _dispatch_backup_now(self, cmd_id: int) -> None:
        """Cloud-triggered immediate snapshot (#165).

        Runs the same code path as the scheduled weekly snapshot: write
        to the local backup_dir AND, if a cloud uploader is configured,
        push the archive to the cloud's appliance_backups table so the
        owner can rescue from it later via /app/site/{id}.

        If `cloud_upload` is OFF in config (Hobby tier user with cloud
        paired but no cloud-backup retention) we still take the LOCAL
        snapshot — clicking "Take backup now" from the cloud UI should
        never be a silent no-op. The user gets a fresh local snapshot
        either way; the cloud-side ApplianceBackup row only appears if
        the uploader was wired at startup.
        """
        backup_svc = getattr(self.scheduler, "backup_service", None)
        if backup_svc is None:
            await self._patch_command_status(
                cmd_id, "failed",
                error="backup service not running on this appliance",
            )
            return
        await self._patch_command_status(cmd_id, "picked_up")
        await self._patch_command_status(cmd_id, "applying")
        try:
            out_path = await backup_svc.snapshot_now()
        except Exception as e:
            log.exception("cloud backup_now: snapshot failed for cmd %d", cmd_id)
            await self._patch_command_status(
                cmd_id, "failed",
                error=f"snapshot failed: {type(e).__name__}: {e}",
            )
            return
        # Did the cloud-upload arm succeed? snapshot_now() stores the
        # result on the service for the local Settings UI; we surface
        # it here too so the dashboard can render "snapshot stored on
        # cloud" vs "local-only — enable cloud backups in Settings".
        if (backup_svc.cfg.cloud_upload
                and backup_svc.cloud_uploader is not None
                and backup_svc.last_cloud_upload_ok is False):
            err = backup_svc.last_cloud_upload_error or "unknown error"
            await self._patch_command_status(
                cmd_id, "failed",
                error=f"local snapshot ok ({out_path.name}) but "
                      f"cloud upload failed: {err}",
            )
            return
        log.info("cloud backup_now: cmd %d wrote %s", cmd_id, out_path.name)
        await self._patch_command_status(cmd_id, "success")

    async def _patch_command_status(
        self, cmd_id: int, status: str, *, error: str | None = None,
    ) -> None:
        """PATCH /api/heartbeat/command/{id} to report a status
        transition. Best-effort — failures here are logged but
        don't cascade (a half-reported command on the dashboard
        is preferable to crashing the heartbeat path)."""
        url = (f"{self.cfg.endpoint.rstrip('/')}/api/heartbeat/"
               f"command/{cmd_id}")
        body: dict[str, Any] = {"status": status}
        if error:
            body["error"] = error
        headers = {
            "Authorization": f"Bearer {self.cfg.bearer_token}",
            "Content-Type":  "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=True,
            ) as client:
                r = await client.patch(url, json=body, headers=headers)
            if r.status_code >= 400:
                log.warning(
                    "cloud command status PATCH HTTP %s for cmd %d→%s: %s",
                    r.status_code, cmd_id, status, r.text[:200],
                )
        except Exception as e:
            log.warning("cloud command status PATCH failed (cmd %d→%s): %s",
                        cmd_id, status, e)

    async def _build_payload(self) -> dict[str, Any]:
        """Pull SoC + net power from the store's `bank` pseudo-device.
        Defensive about every step — a half-built snapshot during
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
        # Keep this concise — the cloud caps extras at 2 KiB.
        extras: dict[str, Any] = {}
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
        # Kiosk share-token (Option C of the kiosk security model).
        # The cloud dashboard's "Kiosk" button reads this and builds
        # the share URL `<slug>.wattpost.cloud/kiosk?key=<token>`.
        # Safe to ship in extras: it IS the public bearer for the
        # share URL — anyone with the URL has it anyway. Stays out
        # of the audit log + isn't sensitive like the bearer_token.
        if self.cfg.kiosk_token:
            extras["kiosk_token"] = self.cfg.kiosk_token
        try:
            alert_count = len([
                r for r in (getattr(self.scheduler._alerts, "rules", []) or [])
                if r.id in getattr(self.scheduler._alerts, "_last_fired", {})
            ])
            extras["alert_count"] = alert_count
        except Exception:
            pass
        # Today's energy aggregates — surface on the cloud card so the
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
                # Round to whole Wh — the cloud renders in kWh anyway.
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
                # Today's SoC envelope — answers "did the bank get
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

                # Time to empty (discharging) or time to full (charging),
                # in minutes. Uses the same rolling-hour load average as
                # the runtime-forecast endpoint, so it's the same number
                # the local dashboard would show — keeps cloud + local
                # consistent. Skipped entirely when the bank is idle
                # (-5 .. +5 W) or capacity is unknown.
                try:
                    bank_state = latest_for_extras.get("bank") or {}
                    cap_ah  = bank_state.get("capacity_ah")
                    voltage = bank_state.get("voltage_v") or 12.8
                    soc_now = bank_state.get("soc_pct")
                    if (isinstance(cap_ah, (int, float)) and cap_ah > 0
                            and isinstance(soc_now, (int, float))):
                        bank_wh = float(cap_ah) * float(voltage)
                        avg_w = await store.rolling_load_avg(3600)
                        if avg_w is not None:
                            if avg_w < -5:  # discharging
                                # 10% reserve — don't predict past LFP minimum
                                usable_wh = bank_wh * max(0.0, float(soc_now) - 10.0) / 100.0
                                if usable_wh > 0:
                                    extras["time_to_empty_min"] = int(usable_wh / abs(avg_w) * 60)
                            elif avg_w > 5:  # charging
                                empty_wh = bank_wh * (1.0 - float(soc_now) / 100.0)
                                if empty_wh > 0:
                                    extras["time_to_full_min"] = int(empty_wh / float(avg_w) * 60)
                except Exception:
                    log.exception("cloud heartbeat: time-to-empty/full failed")

                # Charger state pill — pick the first device that reports
                # one (typically the MPPT or AC charger). On a mixed
                # install we just surface "any active charger's mode",
                # which is the headline customer-facing answer ("bulk?
                # absorption? float?"). 16-char cap stays cheap.
                try:
                    for _label, dev in latest_for_extras.items():
                        st = dev.get("charging_state") if isinstance(dev, dict) else None
                        if st:
                            extras["charger_state"] = str(st).lower()[:16]
                            break
                except Exception:
                    pass
        except Exception:
            log.warning("cloud heartbeat: today_aggregate read failed",
                        exc_info=True)

        # Weather snapshot + PV forecast totals. Both live in the
        # local SQLite kv table (the same cache the dashboard reads
        # from), so this is a cheap read — no third-party calls per
        # heartbeat. Keep the field set tight (~5 fields, ~100 bytes)
        # so the 2 KiB extras cap isn't a concern.
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

        return {
            "soc_pct": soc_pct,
            "net_w":   net_w,
            "extras":  extras,
        }

    async def _loop(self) -> None:
        # First heartbeat immediately so the cloud's online pill flips
        # within seconds of the daemon coming up, not after the first
        # full poll_minutes window.
        try:
            await self.heartbeat_once()
        except Exception as e:
            log.warning("initial cloud heartbeat failed: %s", e)
        period_s = max(1, self.cfg.heartbeat_minutes) * 60
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=period_s)
                return
            except asyncio.TimeoutError:
                pass
            try:
                await self.heartbeat_once()
            except Exception as e:
                log.warning("cloud heartbeat failed: %s", e)
