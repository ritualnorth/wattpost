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
import time
from typing import Any

import httpx

from ..config import CloudCfg

log = logging.getLogger(__name__)


class CloudService:
    def __init__(self, config_or_cfg, scheduler) -> None:
        """Accepts either a Config (preferred) or a bare CloudCfg
        (legacy callers, Settings save still passes one in). When
        given a Config we hold a reference to the parent so reading
        `self.cfg` always reflects the current `config.cloud`, even
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
        # Per-cmd-id snapshot cache: if a Docker update dispatch fails at
        # the watchtower / swap step, the cloud watchdog keeps the cmd in
        # `applying` and the daemon re-dispatches on the next heartbeat.
        # Without this cache each retry takes a *new* pre-update snapshot,
        # cluttering the cloud backup list with near-duplicates. Cache
        # lives in-memory only; a daemon restart legitimately invalidates
        # it (the snapshot file might be gone) so re-snapshot is correct.
        self._update_snapshots: dict[int, str] = {}

    @property
    def cfg(self) -> CloudCfg:
        """Always returns the live CloudCfg the rest of the daemon
        reads from. When constructed from a Config (the normal
        scheduler path), this resolves via the parent so a Settings
        save that did `config.cloud = new_c` is visible immediately."""
        if self._config is not None:
            return self._config.cloud
        return self._direct_cfg

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
        # Identity v2 (#303), fire-and-forget keypair upgrade check.
        # If the appliance has an ed25519 keypair on disk but the cloud
        # doesn't know about it (or no keypair at all), generate +
        # upload. Idempotent; safe to run on every boot. Doesn't
        # block heartbeat startup, failure here logs and moves on,
        # since v1 bearer-token auth still works during the transition.
        asyncio.create_task(self._identity_v2_upgrade_bg(),
                            name="identity-v2-upgrade")
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

    async def _identity_v2_upgrade_bg(self) -> None:
        """Identity v2 (#303), generate ed25519 keypair if missing,
        register public key with cloud if cloud doesn't have it yet.

        Idempotent and best-effort. Failures here log + move on,
        v1 bearer-token auth still works during the transition, so
        a cloud or network blip doesn't block heartbeats.

        Phase 1 just registers the keypair. Phases 2+ will start
        actually signing things with it.
        """
        try:
            from ..auth import load_or_create as _kp_load
            from ..auth.keypair import fingerprint as _kp_fp_disk
        except ImportError as e:
            log.debug("identity v2 module not available: %s, skipping", e)
            return

        try:
            keypair = _kp_load()
        except Exception:
            log.exception("identity v2: keypair load/generate failed")
            return

        endpoint = self.cfg.endpoint.rstrip("/")
        status_url = f"{endpoint}/api/internal/identity/v2/status"
        upgrade_url = f"{endpoint}/api/internal/identity/v2/upgrade"
        headers = {
            "Authorization": f"Bearer {self.cfg.bearer_token}",
            "Content-Type":  "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                # Check current state first. If the cloud already knows
                # this fingerprint, there's nothing to do.
                r = await client.get(status_url, headers=headers)
                if r.status_code == 200:
                    body = r.json()
                    if body.get("fingerprint") == keypair.fingerprint:
                        log.info("identity v2: already registered (fingerprint=%s)",
                                 keypair.fingerprint)
                        # Phase 3 (#305): /status now also returns OIDC
                        # client config, capture it so existing v2
                        # appliances pick up LAN OIDC on boot without
                        # having to re-trigger /upgrade.
                        if all(body.get(k) for k in (
                            "oidc_client_id", "oidc_redirect_uri",
                            "jwks_url", "oidc_discovery_url",
                        )):
                            try:
                                from ..auth import oidc_config as _oidc_cfg
                                _oidc_cfg.save(
                                    client_id=body["oidc_client_id"],
                                    redirect_uri=body["oidc_redirect_uri"],
                                    jwks_url=body["jwks_url"],
                                    discovery_url=body["oidc_discovery_url"],
                                )
                            except Exception:
                                log.exception(
                                    "identity v2: failed to persist OIDC "
                                    "config from /status response",
                                )
                        # Phase 6B (#308), ensure mTLS leaf cert is
                        # current. Idempotent; returns immediately if
                        # cert is fresh. Until Phase 6B-B wires the
                        # heartbeat to use it, the cert just sits
                        # ready on disk.
                        try:
                            from . import mtls_client as _mtls
                            await _mtls.ensure_cert(
                                endpoint=endpoint,
                                bearer_token=self.cfg.bearer_token,
                            )
                        except Exception:
                            log.exception(
                                "identity v2 phase 6B: mTLS cert "
                                "issuance failed (non-fatal, heartbeat "
                                "still uses bearer)"
                            )
                        return
                elif r.status_code == 404:
                    # Cloud doesn't have the endpoint yet (older cloud
                    # deploy than appliance). Skip silently; we'll try
                    # again on next boot.
                    log.debug("identity v2: cloud endpoint 404, older deploy?")
                    return

                # Upload our public key. Cloud will either register
                # (first time) or rotate (different fingerprint than
                # what's stored).
                payload = {
                    "public_key_b64": keypair.public_key_b64(),
                    "fingerprint":    keypair.fingerprint,
                }
                r = await client.post(upgrade_url, json=payload, headers=headers)
                if r.status_code >= 400:
                    log.warning("identity v2 upgrade HTTP %s: %s",
                                r.status_code, r.text[:200])
                    return
                body = r.json()
                log.info("identity v2 upgrade %s: appliance keypair "
                         "registered with cloud (fingerprint=%s)",
                         body.get("result"), keypair.fingerprint)

                # Phase 3 (#305): persist OIDC client config the cloud
                # handed back. We capture all four fields atomically so
                # downstream code can assume "if oidc_config.load() is
                # not None, all fields are present and consistent".
                # Older clouds (pre-Phase 2) won't return these fields
                #, skip silently in that case so we don't churn the
                # disk on partial upgrade.
                if all(body.get(k) for k in (
                    "oidc_client_id", "oidc_redirect_uri",
                    "jwks_url", "oidc_discovery_url",
                )):
                    try:
                        from ..auth import oidc_config as _oidc_cfg
                        _oidc_cfg.save(
                            client_id=body["oidc_client_id"],
                            redirect_uri=body["oidc_redirect_uri"],
                            jwks_url=body["jwks_url"],
                            discovery_url=body["oidc_discovery_url"],
                        )
                    except Exception:
                        # Non-fatal, Phase 3 OIDC client is additive;
                        # legacy cookie auth still works without it.
                        log.exception(
                            "identity v2 phase 3: failed to persist OIDC "
                            "config; LAN OIDC login will fall back to "
                            "the legacy password flow",
                        )

                # Phase 6B (#308), issue mTLS leaf cert. Runs on
                # the SAME upgrade-bg task path so a newly-upgraded
                # appliance has a cert by the end of its first boot.
                try:
                    from . import mtls_client as _mtls
                    await _mtls.ensure_cert(
                        endpoint=endpoint,
                        bearer_token=self.cfg.bearer_token,
                    )
                except Exception:
                    log.exception(
                        "identity v2 phase 6B: mTLS cert issuance "
                        "failed (non-fatal, heartbeat still uses bearer)"
                    )
        except Exception as e:
            log.warning("identity v2: cloud round-trip failed: %s, "
                        "will retry on next boot", e)

    async def heartbeat_once(self) -> bool:
        """Build + send one heartbeat. Returns True on 2xx, False on
        anything else. Used by the loop and also exposed for the
        Settings UI's "Send heartbeat now" button.

        Also dispatches any commands the cloud handed back. Dispatch
        happens AFTER the heartbeat returns 2xx, so a flaky network
        round-trip doesn't half-execute a command. Each command's
        status transitions are PATCHed back to the cloud as the
        appliance progresses through pick-up → apply → terminal."""
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
        # Phase 6B alt-C (#308), sign the heartbeat with the Phase 1
        # ed25519 keypair so a leaked bearer alone is useless.
        # Signed form: "v1\n<ts>\n<body>", version tag pinned so a
        # future scheme change can be rejected, ts fresh-checked
        # cloud-side (5min window) to cap replay attacks. The cloud
        # grandfathers heartbeats without these headers during the
        # rollout. Best-effort: keypair load failure ships unsigned
        # (older Identity-v1 boxes never have a keypair).
        try:
            from datetime import datetime, timezone
            from ..auth import keypair as _kp
            import base64 as _b64
            kp = _kp.load_or_create()
            ts = datetime.now(timezone.utc).isoformat()
            signed = b"v1\n" + ts.encode("ascii") + b"\n" + body_bytes
            sig = kp.sign(signed)
            headers["X-WP-Heartbeat-Ts"]  = ts
            headers["X-WP-Heartbeat-Fp"]  = kp.fingerprint
            headers["X-WP-Heartbeat-Sig"] = (
                _b64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")
            )
        except Exception:
            log.exception("heartbeat sign skipped, uploading bearer-only")
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
            # Grandfather paths (unsigned cmd) log + dispatch with a
            # warning so transitional appliances don't brick.
            from . import command_verify as _cverify
            from .. import signed_audit as _sa
            for cmd in commands:
                # Each cmd carries its own appliance_id so the
                # verifier doesn't need an out-of-band hint.
                appliance_id = int(cmd.get("appliance_id") or 0)
                try:
                    ok = await _cverify.verify_command(cmd, appliance_id=appliance_id)
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
        # Promote the pending alerts-uploaded cursor now that the
        # heartbeat write succeeded. On failure we leave it alone so
        # the next heartbeat re-ships the same events (cloud dedupes
        # via UNIQUE constraint).
        pending = getattr(self, "_alerts_pending_ts", None)
        if pending is not None:
            self._alerts_uploaded_ts = pending
            self._alerts_pending_ts = None
        return True

    def _maybe_persist_sso_secret(self, sso_secret: str | None) -> None:
        """Save the cloud-issued SSO HMAC key if we don't already have
        one. Idempotent, if our copy matches, no-op. If it differs
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
          update     , run wattpost-update (Pi installs only)
          backup_now , snapshot + upload to cloud (#165)

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

        if kind == "restore_from_cloud":
            await self._dispatch_restore_from_cloud(cmd_id, cmd)
            return

        # #261 slice 2, bidirectional rules sync. Cloud edits to a
        # local rule arrive as set_local_rule / delete_local_rule
        # commands carrying the rule spec in `payload_json`.
        if kind == "set_local_rule":
            await self._dispatch_set_local_rule(cmd_id, cmd)
            return
        if kind == "delete_local_rule":
            await self._dispatch_delete_local_rule(cmd_id, cmd)
            return

        # #270 auto-rollback. Cloud queues these when the update
        # watchdog times out an `applying` update. Per install_method:
        #   pin_image_tag , Docker: tell wattpost-updater to pull a
        #                    specific image tag instead of :latest.
        #   rollback      , Pi: spawn /usr/local/bin/wattpost-rollback
        #                    which swings the slot symlink.
        if kind == "pin_image_tag":
            await self._dispatch_pin_image_tag(cmd_id, cmd)
            return
        if kind == "rollback":
            await self._dispatch_rollback(cmd_id, cmd)
            return

        # #279 Phase 1, non-destructive housekeeping. Prunes local
        # snapshots, vacuums journal, clears apt cache. Run on demand
        # from the cloud "Free up disk" button or auto-queued by the
        # watchdog when host_health.disk.percent ≥ 90.
        if kind == "disk_cleanup":
            await self._dispatch_disk_cleanup(cmd_id)
            return

        if kind != "update":
            await self._patch_command_status(
                cmd_id, "failed",
                error=f"appliance doesn't handle kind={kind!r}",
            )
            return

        # Defense-in-depth against the duplicate-cmd race ([[watchdog
        # fix in #283]]): if we're picking up an `update` cmd whose
        # target_version is already what we're running, treat it as a
        # success without actually firing watchtower / wattpost-update.
        # Stops sibling cmds from queuing a spurious rollback when the
        # appliance already reached the target via another path.
        target = (cmd.get("target_version") or "").strip()
        from .. import __version__ as _running
        if target and target == _running:
            log.info("cloud update: cmd %d already at target v%s, "
                     "no-op", cmd_id, target)
            await self._patch_command_status(cmd_id, "picked_up")
            await self._patch_command_status(cmd_id, "success")
            return

        # Docker installs go via a Watchtower sidecar (#265). The
        # daemon takes a snapshot first (gives the user a rollback
        # path if the new image is bad, Pi has slot-based atomic
        # swap, Docker doesn't), then POSTs the watchtower HTTP API
        # to pull + restart. Container restart kills this process
        # mid-flight, so we don't await; cloud reconciles via the
        # version-bump heartbeat (same path as Pi).
        import os
        if os.environ.get("WATTPOST_DEPLOYMENT") == "docker":
            wt_url   = (os.environ.get("WATCHTOWER_URL")   or "").strip()
            wt_token = (os.environ.get("WATCHTOWER_TOKEN") or "").strip()
            if not (wt_url and wt_token):
                await self._patch_command_status(
                    cmd_id, "failed",
                    error="cloud-triggered updates need the watchtower "
                          "sidecar. Add it to docker-compose.yml (see "
                          "https://wattpost.io/docs/docker-update) and "
                          "set WATCHTOWER_URL + WATCHTOWER_TOKEN on the "
                          "wattpost service.",
                )
                return

            await self._patch_command_status(cmd_id, "picked_up")
            # Snapshot first, best-effort. If backup service isn't
            # configured we log and continue: blocking updates on
            # backup wedge would be worse than letting the update
            # proceed with no rollback safety net (the user gets the
            # warning surface on the cloud detail page anyway).
            # On retry within the same cmd, reuse the snapshot from
            # the first attempt, otherwise each watchtower-call
            # failure would multiply pre-update snapshots in the
            # cloud backup list.
            backup_svc = getattr(self.scheduler, "backup_service", None)
            snapshot_name = self._update_snapshots.get(cmd_id)
            if snapshot_name:
                log.info("cloud update: reusing pre-update snapshot %s "
                         "from earlier attempt for cmd %d",
                         snapshot_name, cmd_id)
            elif backup_svc is not None:
                try:
                    out_path = await backup_svc.snapshot_now()
                    snapshot_name = out_path.name if out_path else None
                    if snapshot_name:
                        self._update_snapshots[cmd_id] = snapshot_name
                    log.info("cloud update: pre-update snapshot %s for cmd %d",
                             snapshot_name, cmd_id)
                except Exception:
                    log.exception("cloud update: pre-update snapshot "
                                  "failed; proceeding with update anyway "
                                  "for cmd %d", cmd_id)

            await self._patch_command_status(cmd_id, "applying")
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.post(
                        f"{wt_url.rstrip('/')}/v1/update",
                        headers={"Authorization": f"Bearer {wt_token}"},
                    )
                if r.status_code >= 400:
                    await self._patch_command_status(
                        cmd_id, "failed",
                        error=f"watchtower returned HTTP {r.status_code}: "
                              f"{r.text[:200]}",
                    )
                    self._update_snapshots.pop(cmd_id, None)
                    return
                log.info("cloud update: watchtower fired for cmd %d "
                         "(snapshot=%s)", cmd_id, snapshot_name or "skipped")
            except Exception as e:
                log.exception("cloud update: watchtower call failed")
                await self._patch_command_status(
                    cmd_id, "failed",
                    error=f"could not reach watchtower at {wt_url}: "
                          f"{type(e).__name__}: {e}",
                )
                self._update_snapshots.pop(cmd_id, None)
            return

        await self._patch_command_status(cmd_id, "picked_up")
        await self._patch_command_status(cmd_id, "applying")
        # Invoke wattpost-update detached, it'll restart this
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

    async def _dispatch_pin_image_tag(
        self, cmd_id: int, cmd: dict[str, Any],
    ) -> None:
        """#270, Docker auto-rollback. Cloud queues this when the
        update watchdog times out a failed update; we pin the wattpost
        container back to the previous image tag via the wattpost-
        updater sidecar. Same Bearer auth + HTTP shape as the regular
        update path; the difference is the `?version=` query param
        which the updater interprets as "use this tag instead of
        whatever's in compose's image: line"."""
        import json as _json
        import os
        if os.environ.get("WATTPOST_DEPLOYMENT") != "docker":
            await self._patch_command_status(
                cmd_id, "failed",
                error="pin_image_tag is Docker-only, Pi rollbacks use "
                      "kind=rollback (wattpost-rollback slot revert)",
            )
            return
        wt_url   = (os.environ.get("WATCHTOWER_URL")   or "").strip()
        wt_token = (os.environ.get("WATCHTOWER_TOKEN") or "").strip()
        if not (wt_url and wt_token):
            await self._patch_command_status(
                cmd_id, "failed",
                error="WATCHTOWER_URL/TOKEN not configured, sidecar "
                      "missing from compose; can't auto-rollback",
            )
            return

        # Cloud sends the version string; updater needs the full image
        # ref. Constructing it here lets the cloud stay agnostic of
        # the GHCR path, and lets us honour an explicit `image` in
        # payload_json for future cross-registry support.
        raw = cmd.get("payload_json") or "{}"
        try:
            payload = _json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            payload = {}
        version = (payload.get("version")
                   or cmd.get("target_version") or "").lstrip("v")
        if not version:
            await self._patch_command_status(
                cmd_id, "failed",
                error="pin_image_tag has no target version",
            )
            return

        await self._patch_command_status(cmd_id, "picked_up")
        await self._patch_command_status(cmd_id, "applying")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"{wt_url.rstrip('/')}/v1/update",
                    params={"version": version},
                    headers={"Authorization": f"Bearer {wt_token}"},
                )
            if r.status_code >= 400:
                await self._patch_command_status(
                    cmd_id, "failed",
                    error=f"updater returned HTTP {r.status_code}: "
                          f"{r.text[:200]}",
                )
                return
            log.info("cloud rollback: updater fired for cmd %d (pin v%s)",
                     cmd_id, version)
        except Exception as e:
            log.exception("cloud rollback: updater call failed")
            await self._patch_command_status(
                cmd_id, "failed",
                error=f"could not reach updater at {wt_url}: "
                      f"{type(e).__name__}: {e}",
            )

    async def _dispatch_rollback(
        self, cmd_id: int, cmd: dict[str, Any],
    ) -> None:
        """#270, Pi auto-rollback. Spawns the existing wattpost-
        rollback helper that the OnFailure watchdog (#221) uses.
        Detached + best-effort: the helper restarts the daemon at the
        end, so the terminal status reconciles via the next heartbeat
        coming from the previous-slot's binary."""
        import os
        if os.environ.get("WATTPOST_DEPLOYMENT") == "docker":
            await self._patch_command_status(
                cmd_id, "failed",
                error="kind=rollback is Pi-only, Docker uses "
                      "kind=pin_image_tag (sidecar tag-pin)",
            )
            return
        if not os.path.exists("/usr/local/bin/wattpost-rollback"):
            await self._patch_command_status(
                cmd_id, "failed",
                error="wattpost-rollback helper not found, reinstall to fix",
            )
            return
        await self._patch_command_status(cmd_id, "picked_up")
        await self._patch_command_status(cmd_id, "applying")
        try:
            await asyncio.create_subprocess_exec(
                "/usr/bin/setsid", "sudo", "-n",
                "/usr/local/bin/wattpost-rollback",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            log.info("cloud rollback: wattpost-rollback spawned for cmd %d", cmd_id)
        except Exception as e:
            log.exception("cloud rollback: failed to spawn wattpost-rollback")
            await self._patch_command_status(
                cmd_id, "failed",
                error=f"failed to start rollback: {type(e).__name__}: {e}",
            )

    async def _dispatch_disk_cleanup(self, cmd_id: int) -> None:
        """Cloud-triggered disk housekeeping (#279). Non-destructive:
        snapshot prune to retention, journal vacuum, apt clean +
        autoremove (Pi only). Reports freed bytes + per-op result via
        the command's completion message."""
        await self._patch_command_status(cmd_id, "picked_up")
        await self._patch_command_status(cmd_id, "applying")
        try:
            from .. import disk_cleanup as _dc
            report = await _dc.run(self.scheduler)
        except Exception as e:
            log.exception("cloud disk_cleanup: dispatch failed")
            await self._patch_command_status(
                cmd_id, "failed",
                error=f"disk_cleanup failed: {type(e).__name__}: {e}",
            )
            return
        freed_mb = report["freed_bytes"] / (1024 * 1024)
        op_names = ", ".join(o["op"] for o in report["ops"] if o.get("ok"))
        msg = f"freed {freed_mb:.1f} MB · ran: {op_names or 'no ops'}"
        # Per-op errors are downgraded to a warning in the message rather
        # than failing the whole cmd, if snapshot_prune worked but
        # apt_autoremove timed out, the user still got value.
        if report["errors"]:
            err_names = ", ".join(
                f"{o['op']}: {o.get('error') or o.get('note', '')}"
                for o in report["errors"]
            )
            msg += f" · partial errors → {err_names}"
        log.info("cloud disk_cleanup cmd %d: %s", cmd_id, msg)
        await self._patch_command_status(cmd_id, "success", error=msg)

    async def _dispatch_backup_now(self, cmd_id: int) -> None:
        """Cloud-triggered immediate snapshot (#165).

        Runs the same code path as the scheduled weekly snapshot: write
        to the local backup_dir AND, if a cloud uploader is configured,
        push the archive to the cloud's appliance_backups table so the
        owner can rescue from it later via /app/site/{id}.

        If `cloud_upload` is OFF in config (Hobby tier user with cloud
        paired but no cloud-backup retention) we still take the LOCAL
        snapshot, clicking "Take backup now" from the cloud UI should
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
        # cloud" vs "local-only, enable cloud backups in Settings".
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

    async def _dispatch_restore_from_cloud(
        self, cmd_id: int, cmd: dict[str, Any],
    ) -> None:
        """Cloud-triggered restore from a specific cloud-stored backup
        (#166). Downloads the archive via the cloud's bearer-authed
        internal endpoint, applies it through the same `_stage_and_swap`
        path the appliance's local Settings → Restore button uses, then
        re-execs the daemon so the new SQLite + config are loaded fresh.

        Critical UX note: the live `cloud.bearer_token`,
        `cloud.sso_secret`, and `cloud.tunnel_token` are preserved by
        `_stage_and_swap` (#146 phase-2 fix), pairing survives the
        restore. Without that the appliance would come back online
        un-paired and the user would have to re-pair to see it on the
        dashboard.

        Status is PATCHed to "success" BEFORE the re-exec, otherwise
        the daemon-restart kills the python process before the report
        is on the wire and the command sits forever as "applying".
        """
        backup_id = cmd.get("target_backup_id")
        if not isinstance(backup_id, int):
            await self._patch_command_status(
                cmd_id, "failed",
                error="restore_from_cloud missing target_backup_id",
            )
            return
        await self._patch_command_status(cmd_id, "picked_up")
        await self._patch_command_status(cmd_id, "applying")

        # Fetch the archive bytes.
        url = (f"{self.cfg.endpoint.rstrip('/')}/api/internal/backups/"
               f"{backup_id}/download")
        headers = {"Authorization": f"Bearer {self.cfg.bearer_token}"}
        try:
            async with httpx.AsyncClient(
                timeout=300, follow_redirects=True,
            ) as client:
                r = await client.get(url, headers=headers)
        except Exception as e:
            await self._patch_command_status(
                cmd_id, "failed",
                error=f"download failed: {type(e).__name__}: {e}",
            )
            return
        if r.status_code >= 400:
            await self._patch_command_status(
                cmd_id, "failed",
                error=f"cloud returned HTTP {r.status_code}: {r.text[:200]}",
            )
            return

        body = r.content
        # #297-3, verify the appliance-keypair signature BEFORE we
        # touch _verify_archive (which spends real CPU on the SQLite
        # integrity check). The signature is on the response headers
        # echoed back from cloud's appliance_backups row.
        sig_b64 = r.headers.get("X-WP-Backup-Signature") \
            or r.headers.get("x-wp-backup-signature")
        sig_fp = r.headers.get("X-WP-Backup-Pubkey-Fp") \
            or r.headers.get("x-wp-backup-pubkey-fp")
        sig_alg = r.headers.get("X-WP-Backup-Sig-Alg") \
            or r.headers.get("x-wp-backup-sig-alg")
        try:
            from ..backup import signing as _sig
            await asyncio.to_thread(
                _sig.verify_archive, body,
                sig_b64=sig_b64, pubkey_fp=sig_fp, alg=sig_alg,
            )
            if sig_b64:
                log.info(
                    "cloud restore: backup %s signature verified "
                    "(fp=%s...)", backup_id, sig_fp[:8] if sig_fp else "?",
                )
            else:
                # Grandfather path, pre-0.1.99 backup with no sig.
                # Warn loudly + proceed (alternative would lock users
                # out of their own pre-rollout history).
                log.warning(
                    "cloud restore: backup %s has NO signature "
                    "(pre-0.1.99 row); proceeding with #297-1 config "
                    "sanitiser + #297-2 fresh-install pw regen as the "
                    "only defences", backup_id,
                )
        except _sig.BackupSigError as e:
            await self._patch_command_status(
                cmd_id, "failed",
                error=f"backup signature verification failed: {e}, "
                      "refusing restore. If this is a legitimate "
                      "backup from a previous keypair, contact "
                      "support@wattpost.io.",
            )
            return

        # Re-use the appliance's restore plumbing, same code path as
        # the user-initiated restore endpoint.
        try:
            from ..api.backup import _stage_and_swap, _verify_archive
            await asyncio.to_thread(_verify_archive, body)
        except Exception as e:
            await self._patch_command_status(
                cmd_id, "failed",
                error=f"archive failed verification: {e}",
            )
            return

        # Resolve where to write the new DB + config. The DB path
        # lives on the open Store (mirrors how api/backup.py resolves
        # it for the user-driven restore); the config path was pinned
        # onto the scheduler by build_app at startup.
        from pathlib import Path
        import os, sys
        store = getattr(self.scheduler, "store", None)
        store_path = (
            getattr(store, "_path", None) or getattr(store, "path", None)
            if store is not None else None
        )
        db_target = Path(
            store_path
            or os.environ.get("WATTPOST_DB_PATH")
            or "solar-monitor.db"
        )
        config_target = Path(
            getattr(self.scheduler, "config_path", None)
            or os.environ.get("WATTPOST_CONFIG_PATH")
            or "/etc/wattpost/config.yaml"
        )
        try:
            await asyncio.to_thread(
                _stage_and_swap, body, db_target, config_target,
            )
        except Exception as e:
            await self._patch_command_status(
                cmd_id, "failed",
                error=f"apply failed: {type(e).__name__}: {e}",
            )
            return

        log.info(
            "cloud restore_from_cloud: cmd %d applied backup %d, re-execing",
            cmd_id, backup_id,
        )
        # Phase 8B (#310), record the restore in the signed audit
        # log BEFORE re-exec, otherwise the in-flight write would die
        # with the process image. Best-effort.
        try:
            from .. import signed_audit as _sa
            store = self.scheduler.store
            if store is not None and store._db is not None:
                await _sa.write_event(store._db,
                    event_type="restore_applied",
                    payload={"cmd_id": cmd_id, "backup_id": backup_id},
                )
                await store._db.commit()
        except Exception:
            log.exception("signed_audit: restore_applied write failed")
        # Report success BEFORE re-exec, once execv runs the process
        # image is replaced and any in-flight PATCH dies with it.
        await self._patch_command_status(cmd_id, "success")

        async def _delayed_exec() -> None:
            await asyncio.sleep(0.5)
            try:
                await self.scheduler.stop()
            except Exception:
                log.exception("scheduler stop failed before restore re-exec")
            os.execv(sys.executable, [sys.executable] + sys.argv)

        asyncio.create_task(_delayed_exec())

    async def _dispatch_set_local_rule(self, cmd_id: int, cmd: dict[str, Any]) -> None:
        """#261 slice 2, apply a cloud-edited rule to the local engine.

        Payload mirrors the appliance's /api/alerts/rules POST shape
        (id, name, metric, op, threshold, severity, cooldown_seconds,
        transports). We upsert into config.alerts in-place, atomic-
        write config.yaml, and reload the engine. No daemon restart.
        """
        import json as _json
        await self._patch_command_status(cmd_id, "picked_up")
        try:
            raw = cmd.get("payload_json") or "{}"
            payload = _json.loads(raw) if isinstance(raw, str) else dict(raw)
            rid     = str(payload.get("id") or "")
            if not rid:
                raise ValueError("missing rule id")
            from ..config import AlertRuleCfg
            new_rule = AlertRuleCfg(
                id               = rid,
                name             = str(payload.get("name") or rid),
                metric           = str(payload.get("metric") or ""),
                op               = str(payload.get("op") or "lt"),
                threshold        = float(payload.get("threshold") or 0),
                severity         = str(payload.get("severity") or "warn"),
                cooldown_seconds = int(payload.get("cooldown_seconds") or 1800),
                transports       = list(payload.get("transports") or []),
            )
            # In-place replace or append, via _all_rules accessor,
            # writing to self.cfg.alerts silently no-ops (CloudCfg has
            # no .alerts attribute, that lives on top-level Config).
            rules = list(self._all_rules)
            rules = [new_rule if r.id == rid else r for r in rules]
            if not any(r.id == rid for r in rules):
                rules.append(new_rule)
            self._all_rules = rules
            await self._persist_alerts_to_yaml()
            self._reload_alerts_engine()
            await self._patch_command_status(cmd_id, "success")
            log.info("set_local_rule applied: %s", rid)
        except Exception as e:
            log.exception("set_local_rule failed")
            await self._patch_command_status(
                cmd_id, "failed",
                error=f"{type(e).__name__}: {e}",
            )

    async def _dispatch_delete_local_rule(self, cmd_id: int, cmd: dict[str, Any]) -> None:
        """#261 slice 2, remove a rule the cloud deleted. Idempotent:
        if it's already gone (e.g. user also deleted on the appliance)
        we still report success."""
        import json as _json
        await self._patch_command_status(cmd_id, "picked_up")
        try:
            raw = cmd.get("payload_json") or "{}"
            payload = _json.loads(raw) if isinstance(raw, str) else dict(raw)
            rid     = str(payload.get("id") or "")
            if not rid:
                raise ValueError("missing rule id")
            rules = list(self._all_rules)
            before = len(rules)
            self._all_rules = [r for r in rules if r.id != rid]
            await self._persist_alerts_to_yaml()
            self._reload_alerts_engine()
            await self._patch_command_status(cmd_id, "success")
            log.info("delete_local_rule applied: %s (was %d, now %d)",
                     rid, before, len(self._all_rules))
        except Exception as e:
            log.exception("delete_local_rule failed")
            await self._patch_command_status(
                cmd_id, "failed",
                error=f"{type(e).__name__}: {e}",
            )

    async def _persist_alerts_to_yaml(self) -> None:
        """Atomic-write config.yaml's `alerts:` section. Mirrors the
        pattern in api/alerts_admin._save_config, same backup +
        tmp+replace dance so a crash mid-write doesn't truncate.

        Resolves the config path via the scheduler, `build_app` stashes
        it on the scheduler so background services (us, BackupService)
        can mutate config.yaml without needing Litestar state."""
        import shutil
        from pathlib import Path
        import yaml
        config_path = getattr(self.scheduler, "config_path", None)
        if not config_path:
            raise RuntimeError("scheduler has no config_path; "
                               "set in build_app() per #148 pattern")
        path = Path(config_path)
        raw = yaml.safe_load(path.read_text()) or {}
        raw["alerts"] = [
            {
                "id":               r.id,
                "name":             r.name,
                "metric":           r.metric,
                "op":               r.op,
                "threshold":        r.threshold,
                "severity":         r.severity,
                "cooldown_seconds": r.cooldown_seconds,
                "transports":       list(r.transports or []),
            }
            for r in self._all_rules
        ]
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(yaml.safe_dump(raw, sort_keys=False))
        tmp.replace(path)

    def _reload_alerts_engine(self) -> None:
        """Hot-reload the alerts engine with the current config.alerts.
        Same call path Settings → Alerts uses on rule add/edit."""
        engine = getattr(self.scheduler, "_alerts", None)
        if engine is not None and hasattr(engine, "reload_rules"):
            engine.reload_rules(self._all_rules)

    async def _patch_command_status(
        self, cmd_id: int, status: str, *, error: str | None = None,
    ) -> None:
        """PATCH /api/heartbeat/command/{id} to report a status
        transition. Best-effort, failures here are logged but
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
        # Keep this concise, the cloud caps extras at 2 KiB.
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
        # Kiosk share-token (Option C of the kiosk security model).
        # The cloud dashboard's "Kiosk" button reads this and builds
        # the share URL `<slug>.wattpost.cloud/kiosk?key=<token>`.
        # Safe to ship in extras: it IS the public bearer for the
        # share URL, anyone with the URL has it anyway. Stays out
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

        # Phase 8B (#310), appliance-signed audit log entries waiting
        # to sync to cloud. Cloud verifies signature + hash chain +
        # ingests; ack list comes back in the heartbeat response so
        # we can flip uploaded_at. Cap at 50 per heartbeat to keep
        # extras under the 2KiB ceiling on busy-event days.
        try:
            from .. import signed_audit as _sa
            store = self.scheduler.store
            if store is not None and store._db is not None:
                pending = await _sa.fetch_pending(store._db, limit=50)
                if pending:
                    extras["signed_audit_pending"] = pending
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
            rules = list(self._all_rules)
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
                events = engine.recent_events_since(since_ts, limit=20)
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
