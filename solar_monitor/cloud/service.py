"""Background heartbeat poster.

Reads the latest bank snapshot from the scheduler's `last_result`
plus today's energy aggregates from the scheduler, packages them
into a small JSON payload, and POSTs to `<endpoint>/api/heartbeat`
with the bearer token.

Failures are swallowed, losing internet must not break the local
dashboard. Each failure is logged at WARNING for diagnostics.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..config import CloudCfg
from . import request_sign as _request_sign
from .commands import CommandsMixin, _bad_version_field  # noqa: F401  (_bad_version_field re-exported for callers/tests)
from .identity import IdentityMixin
from .payload import PayloadMixin, _bank_extras  # noqa: F401  (re-exported)

log = logging.getLogger(__name__)


# Upper bound on commands honoured from a single heartbeat reply.
MAX_COMMANDS_PER_HEARTBEAT = 20




class CloudService(IdentityMixin, PayloadMixin, CommandsMixin):
    def __init__(self, config_or_cfg, scheduler) -> None:
        """Accepts either a Config (preferred) or a bare CloudCfg
        (legacy callers, Settings save still passes one in). When
        given a Config we hold a reference to the parent so reading
        `self.cfg` always reflects the current `config.cloud`, even
        after Settings save rebinds the parent's `.cloud` attribute
        to a freshly-built CloudCfg. Otherwise a heartbeat firing
        after a user clicked Save in Settings → Cloud could mutate
        a stale CloudCfg, persist its (outdated) state back to
        config.yaml (#148).
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
        # Per-cmd-id snapshot cache: if a Docker update dispatch fails at
        # the watchtower / swap step, the cloud watchdog keeps the cmd in
        # `applying` and the daemon re-dispatches on the next heartbeat.
        # Without this cache each retry takes a *new* pre-update snapshot,
        # cluttering the cloud backup list with near-duplicates. Cache
        # lives in-memory only; a daemon restart legitimately invalidates
        # it (the snapshot file might be gone) so re-snapshot is correct.
        self._update_snapshots: dict[int, str] = {}
        # Strong refs to fire-and-forget tasks. asyncio holds only a WEAK
        # ref to a running task, so an unawaited create_task() result can be
        # GC'd and silently cancelled mid-flight — a dropped command
        # dispatch or, worst case, the restore re-exec never firing. Keep a
        # ref until the task finishes (discarded via done-callback).
        self._bg_tasks: set[asyncio.Task] = set()

    def _spawn(self, coro, *, name: str | None = None) -> asyncio.Task:
        """Fire-and-forget a coroutine while retaining a strong reference
        so it can't be garbage-collected before it completes."""
        t = asyncio.create_task(coro, name=name)
        self._bg_tasks.add(t)
        t.add_done_callback(self._bg_tasks.discard)
        return t

    @property
    def cfg(self) -> CloudCfg:
        """Always returns the live CloudCfg the rest of the daemon
        reads from. When constructed from a Config (the normal
        scheduler path), this resolves via the parent so a Settings
        save that did `config.cloud = new_c` is visible immediately."""
        if self._config is not None:
            return self._config.cloud
        return self._direct_cfg

    def _cfg_get(self, name: str, default):
        """Read a cloud setting without assuming the cloud config exists:
        /api/cloud/unpair sets `config.cloud = None` while the background
        loops keep running."""
        c = self.cfg
        return getattr(c, name, default) if c is not None else default

    # Top-level Config.alerts (NOT a CloudCfg attribute, getattr
    # against self.cfg silently returns [] and every alert-related
    # cloud feature no-ops). All rule-unification paths (#261) read
    # / write through this accessor so the same mistake can't recur.
    @property
    def _all_rules(self) -> list:
        return (self._config.alerts if self._config is not None else None) or []

    @_all_rules.setter
    def _all_rules(self, val: list) -> None:
        if self._config is not None:
            self._config.alerts = val

    async def start(self) -> None:
        if not self.cfg.bearer_token:
            log.info("cloud: no bearer_token configured, skipping heartbeat loop")
            return
        self._stop.clear()
        # Identity v2 (#303): register/confirm the appliance's ed25519
        # keypair with the cloud BEFORE the first heartbeat. Heartbeats
        # are signed and the cloud rejects any whose fingerprint it
        # doesn't yet know ("heartbeat signature fingerprint unknown" →
        # 401), so a beat sent before registration completes fails and
        # the box shows offline until the next 5-min cycle / a restart.
        # That raced on a fresh pair: this await closes the window so a
        # freshly-paired box goes online on its very first beat. Bounded
        # so a cloud/network hiccup can't wedge heartbeat startup — on a
        # timeout we start anyway (a later run / restart re-registers).
        # Idempotent: on an already-registered box it's a fast status GET.
        try:
            await asyncio.wait_for(self._identity_v2_upgrade_bg(), timeout=20)
        except asyncio.TimeoutError:
            log.warning("identity v2: registration slow (>20s); "
                        "starting heartbeat loop anyway")
        except Exception:
            log.exception("identity v2: registration raised; "
                          "starting heartbeat loop anyway")
        self._task = asyncio.create_task(self._loop(), name="cloud-heartbeat")
        self._sync_task = None
        # Always running; `telemetry_sync` is checked live by the loop so the
        # Settings toggle takes effect without a restart.
        from .rollup_sync import RollupSync
        from .views import ViewPublisher
        self._sync = RollupSync(
            store=self.scheduler.store,
            endpoint=lambda: self._cfg_get("endpoint", ""),
            bearer=lambda: self._cfg_get("bearer_token", ""),
            sign=self._signed_headers,
            backfill_hours=lambda: self._cfg_get("telemetry_backfill_hours", 24),
            views=ViewPublisher(self.scheduler.store, self.scheduler),
            # No cloud config (unpaired live) means uploads are off, not a crash.
            enabled=lambda: bool(self._cfg_get("telemetry_sync", False)
                                 and self._cfg_get("bearer_token", "")),
            identity=lambda: (
                f"{self._cfg_get('appliance_id', None)}|"
                f"{str(self._cfg_get('endpoint', '')).rstrip('/').lower()}"
                if self.cfg is not None else ""),
        )
        self._sync_task = asyncio.create_task(
            self._sync.loop(self._stop,
                            lambda: max(1, self._cfg_get("telemetry_minutes", 5)) * 60),
            name="cloud-rollup-sync")
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
        t = getattr(self, "_sync_task", None)
        if t is not None:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
            self._sync_task = None

    def _signed_headers(self, body: bytes, method: str, url: str) -> dict[str, str]:
        """ed25519 request-signature headers (scheme v2) for one request.

        The method and request target (path + query) are inside the signed
        bytes, so a captured signed body can't be replayed at another
        endpoint, and a leaked bearer token alone can't act as this
        appliance. See request_sign.py. Best effort: a keypair load failure
        returns no headers and the cloud refuses the request."""
        return _request_sign.signed_headers(method, url, body)

    async def heartbeat_once(self) -> bool:
        """Build + send one heartbeat. Returns True on 2xx, False on
        anything else. Used by the loop and also exposed for the
        Settings UI's "Send heartbeat now" button.

        Also dispatches any commands the cloud handed back. Dispatch
        happens AFTER the heartbeat returns 2xx, so a flaky network
        round-trip doesn't half-execute a command. Each command's
        status transitions are PATCHed back to the cloud as the
        appliance progresses through pick-up → apply → terminal."""
        if self.cfg is None:
            return False              # unpaired while the loop was running
        payload = await self._build_payload()
        url = f"{self.cfg.endpoint.rstrip('/')}/api/heartbeat"
        # Pre-serialize the body so the bytes we sign are the EXACT
        # bytes the cloud will hash. httpx's json= kw would also
        # serialize but with its own separator policy; we sign +
        # send the same string to guarantee a match.
        import json as _json
        body_bytes = _json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.cfg.bearer_token}",
            "Content-Type":  "application/json",
        }
        headers.update(self._signed_headers(body_bytes, "POST", url))
        try:
            # follow_redirects=True so an appliance still pointing at
            # an older hostname (e.g. https://wattpost.io after we
            # moved the API to wattpost.cloud) succeeds via the 308
            # rather than silently 308-ing into a no-op. POST → POST
            # is method-preserving under 308 by spec.
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=True,
            ) as client:
                r = await client.post(url, content=body_bytes, headers=headers)
        except Exception as e:
            log.warning("cloud heartbeat failed: %s", e)
            return False
        if r.status_code >= 400:
            log.warning("cloud heartbeat HTTP %s: %s", r.status_code, r.text[:200])
            return False

        # Phase 8B (#310), flip uploaded_at on any audit rows the
        # cloud confirmed it ingested, so they drop out of the next
        # heartbeat's pending list. Best-effort: if this fails we'll
        # just re-ship them next time (idempotent on the cloud side
        # too, duplicate signed_repr would fail the UNIQUE
        # constraint on the cloud SignedAuditLogEntry.signed_repr
        # path, which currently doesn't exist; cloud uses prev_hash
        # chain check instead, also idempotent in effect).
        try:
            body = r.json()
            acked = body.get("audit_acked_ids") or []
            if isinstance(acked, list) and acked:
                try:
                    from .. import signed_audit as _sa
                    store = self.scheduler.store
                    if store is not None and store._db is not None:
                        await _sa.mark_uploaded(
                            store._db, ids=[int(i) for i in acked if isinstance(i, int)],
                        )
                        await store._db.commit()
                except Exception:
                    log.exception("signed_audit: mark_uploaded failed")
        except Exception:
            pass

        # Dispatch any commands the cloud queued for us. Best-effort
        #, failures dispatching one command shouldn't stop the
        # heartbeat from being considered successful, since the
        # heartbeat write itself already succeeded.
        try:
            body = r.json()
            commands = body.get("commands") or []
            # #299, verify cloud-signed commands BEFORE dispatching.
            # Reject paths refuse to fire the command (it stays in the
            # cloud queue marked queued; cloud admins can investigate).
            # Strict: unsigned, stale or already-completed commands are
            # refused (see command_verify).
            from . import command_verify as _cverify
            from .. import signed_audit as _sa
            if not isinstance(commands, list):
                commands = []
            if len(commands) > MAX_COMMANDS_PER_HEARTBEAT:
                # Each unverifiable command costs a signed-audit write (and
                # possibly a JWKS fetch); a hostile reply must not be able to
                # make us do that thousands of times per heartbeat.
                log.warning("cloud sent %d commands in one heartbeat; only the "
                            "first %d are considered", len(commands),
                            MAX_COMMANDS_PER_HEARTBEAT)
                commands = commands[:MAX_COMMANDS_PER_HEARTBEAT]
            jwks_refresh = {"done": False}      # at most one forced refetch per heartbeat
            for cmd in commands:
                if not isinstance(cmd, dict):
                    continue
                try:
                    # The id a command is verified against is THIS
                    # appliance's own (from config), never the one the
                    # command claims: otherwise a command legitimately
                    # signed for another appliance would verify here.
                    # Unpaired (no id) means refuse everything. Parsing
                    # happens INSIDE the try so one malformed command
                    # rejects just itself, not the batch.
                    own_id = self.cfg.appliance_id
                    if own_id is None:
                        log.warning("cloud command %s refused: no appliance_id configured",
                                    cmd.get("id"))
                        ok = False
                    else:
                        claimed = cmd.get("appliance_id")
                        if claimed is not None and str(claimed) != str(own_id):
                            log.warning("cloud command %s claims appliance_id=%s, "
                                        "this appliance is %s", cmd.get("id"), claimed, own_id)
                        ok = await _cverify.verify_command(
                            cmd, appliance_id=int(own_id), refresh_state=jwks_refresh)
                    if ok and self._ledger().seen(int(cmd.get("id") or 0)):
                        log.warning("command %s already ran; ignoring replay", cmd.get("id"))
                        ok = False
                except Exception:
                    log.exception("command verify raised, treating as untrusted")
                    ok = False
                if not ok:
                    # Record the rejection in our signed-audit chain
                    # (Phase 8B) so an attacker can't quietly probe by
                    # feeding malformed commands without leaving a
                    # trace.
                    try:
                        store = self.scheduler.store
                        if store is not None and store._db is not None:
                            await _sa.write_event(store._db,
                                event_type="cloud_command_rejected",
                                payload={
                                    "cmd_id":   cmd.get("id"),
                                    "kind":     cmd.get("kind"),
                                    "reason":   "signature verify failed",
                                },
                            )
                            await store._db.commit()
                    except Exception:
                        pass
                    continue
                # Spawn as a task so a long-running command (e.g. an
                # update that takes 30s) doesn't block the next
                # scheduled heartbeat. The dispatcher does its own
                # serialization within a single command type.
                self._spawn(self._dispatch_command(cmd), name="cloud-cmd-dispatch")
            # Cache the owner's white-label branding (Installer tier)
            # so the local dashboard can render the custom brand
            # without a separate round-trip per page load. Stored in
            # the kv table (the same one the forecast service uses)
            # under key `cloud.branding`. Hobby/Pro accounts → empty
            # dict, which clears any previously-cached brand.
            self._cache_branding(body.get("branding") or {})
            # #17 — backup-upload management lives in the cloud dashboard
            # now. Honour the cloud-delivered toggle by mirroring it onto
            # the live backup config, so the backup service uploads (or
            # stops) with no local control. Absent field = older cloud;
            # leave the local setting untouched.
            self._apply_cloud_backups_flag(body.get("cloud_backups_enabled"))
            # #22 — cloud-controlled backup schedule. Apply interval +
            # retention onto the live backup config and nudge the loop so
            # the change takes effect now, not after the current sleep.
            if self._apply_cloud_backup_schedule(
                body.get("backup_interval_hours"),
                body.get("backup_keep_count"),
            ):
                bk_svc = getattr(self.scheduler, "backup_service", None)
                if bk_svc is not None and hasattr(bk_svc, "reschedule"):
                    bk_svc.reschedule()
            # #11 — cloud-controlled update channel. Cloud wins when set:
            # it re-asserts on every heartbeat. Absent = the owner hasn't
            # chosen one on the dashboard, so the appliance's own local
            # selector stands. On a change, kick an immediate manifest
            # re-check so the dashboard reflects the new channel's latest.
            if self._apply_cloud_update_channel(body.get("update_channel")):
                updater = getattr(self.scheduler, "_updater", None)
                if updater is not None:
                    self._spawn(updater.check_once(), name="cloud-update-check")
        except Exception as e:
            log.warning("cloud heartbeat: failed to parse response body: %s", e)
        # Promote the pending alerts-uploaded cursor now that the
        # heartbeat write succeeded. On failure we leave it alone so
        # the next heartbeat re-ships the same events (cloud dedupes
        # via UNIQUE constraint).
        pending = getattr(self, "_alerts_pending_ts", None)
        if pending is not None:
            self._alerts_uploaded_ts = pending
            self._alerts_pending_ts = None
        return True

    def _apply_cloud_backups_flag(self, enabled) -> None:
        """Mirror the cloud's per-appliance backup-upload toggle (#17)
        onto the live backup config. In-memory only — the cloud is the
        source of truth and re-asserts it on every heartbeat. No-op when
        the field is absent (older cloud) or there's no backup config."""
        if enabled is None or self._config is None:
            return
        bk = getattr(self._config, "backup", None)
        if bk is None:
            return
        want = bool(enabled)
        if bk.cloud_upload != want:
            bk.cloud_upload = want
            log.info("cloud backups %s by the cloud dashboard",
                     "enabled" if want else "disabled")

    def _apply_cloud_backup_schedule(self, interval_hours, keep_count) -> bool:
        """Apply the cloud's backup schedule (#22) onto the live backup
        config. None = the cloud hasn't overridden it, so leave the
        local value alone. Returns True if anything changed (so the
        caller can nudge the backup loop). In-memory only; the cloud
        re-asserts on every heartbeat."""
        if self._config is None:
            return False
        bk = getattr(self._config, "backup", None)
        if bk is None:
            return False
        changed = False
        # Coerce defensively — these come from the cloud heartbeat
        # response; a non-numeric value would otherwise raise out of the
        # response handler and skip the settings applied after this call.
        if interval_hours is not None:
            try:
                iv = int(interval_hours)
            except (TypeError, ValueError):
                iv = 0
            if iv >= 1 and bk.interval_hours != iv:
                bk.interval_hours = iv
                changed = True
        if keep_count is not None:
            try:
                kc = int(keep_count)
            except (TypeError, ValueError):
                kc = 0
            if kc >= 1 and bk.keep_count != kc:
                bk.keep_count = kc
                changed = True
        if changed:
            log.info("backup schedule updated by cloud: every %sh, keep %s",
                     bk.interval_hours, bk.keep_count)
        return changed

    def _apply_cloud_update_channel(self, channel) -> bool:
        """Mirror the cloud's per-appliance update-channel choice (#11)
        onto the live update checker. Cloud wins when set: it re-asserts
        on every heartbeat. None = the cloud hasn't overridden it (older
        cloud, or the owner hasn't picked one), so leave the appliance's
        local selector alone. Returns True if the channel changed (so the
        caller can kick a re-check). In-memory only; the cloud is the
        source of truth for paired appliances, exactly like the backup
        toggle/schedule above."""
        if channel is None:
            return False
        updater = getattr(self.scheduler, "_updater", None)
        if updater is None:
            return False
        from ..update.checker import normalize_channel
        want = normalize_channel(channel)
        if updater.channel == want:
            return False
        updater.set_channel(want)
        log.info("update channel set to %s by the cloud dashboard", want)
        return True

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
            self._spawn(store.kv_set("cloud.branding", payload), name="cloud-branding")
        except Exception as e:
            log.debug("cloud heartbeat: failed to cache branding: %s", e)

    async def _loop(self) -> None:
        # First heartbeat immediately so the cloud's online pill flips
        # within seconds of the daemon coming up, not after the first
        # full poll_minutes window.
        try:
            await self.heartbeat_once()
        except Exception as e:
            log.warning("initial cloud heartbeat failed: %s", e)
        while not self._stop.is_set():
            # Re-read each cycle: the config can change (or vanish, after
            # /api/cloud/unpair) while this task runs.
            period_s = max(1, self._cfg_get("heartbeat_minutes", 5)) * 60
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=period_s)
                return
            except asyncio.TimeoutError:
                pass
            if self.cfg is None or not self.cfg.bearer_token:
                continue              # unpaired: idle until stopped
            try:
                await self.heartbeat_once()
            except Exception as e:
                log.warning("cloud heartbeat failed: %s", e)
