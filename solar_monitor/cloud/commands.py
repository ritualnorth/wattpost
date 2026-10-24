"""Cloud command intake for CloudService: dispatch of signed commands, their
bookkeeping (replay ledger, tried-commands file) and status reports."""

from __future__ import annotations

from ..fsutil import atomic_write_text as _atomic_write_text
import asyncio
import logging
import re
from typing import Any

import httpx


log = logging.getLogger(__name__)


def _tried_cmds_path() -> str:
    """Where we persist already-attempted update/rollback cmd_ids, so a
    doomed op doesn't re-spawn on every daemon restart. Lives beside the
    DB (a ReadWritePaths dir for the sandboxed daemon)."""
    import os
    db = os.environ.get("WATTPOST_DB", "")
    base = os.path.dirname(db) if db else "/var/lib/wattpost"
    return os.path.join(base, "cloud_tried_cmds.json")


def _load_tried_cmds() -> set:
    import json
    try:
        with open(_tried_cmds_path()) as f:
            return {int(x) for x in json.load(f)}
    except Exception:
        return set()


def _mark_tried_cmd(cmd_id: int) -> None:
    import json
    import os
    try:
        s = _load_tried_cmds()
        s.add(int(cmd_id))
        s = set(sorted(s)[-200:])  # cap growth; old ids never recur
        p = _tried_cmds_path()
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(sorted(s), f)
        os.replace(tmp, p)
    except Exception:
        pass


_VERSION_RE = re.compile(r"^v?[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:[-+][0-9A-Za-z.\-]{1,16})?$")


def _bad_version_field(cmd: dict[str, Any]) -> str | None:
    """Why this command's version strings are unacceptable, or None."""
    import json as _json
    tv = cmd.get("target_version")
    if tv not in (None, "") and not _VERSION_RE.match(str(tv)):
        return "target_version is not a release version"
    raw = cmd.get("payload_json")
    if raw:
        try:
            payload = _json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            return "payload is not JSON"
        if not isinstance(payload, dict):
            return "payload is not an object"
        for k in ("version", "image"):
            v = payload.get(k)
            if k == "version" and v not in (None, "") and not _VERSION_RE.match(str(v)):
                return "payload version is not a release version"
            if k == "image" and v not in (None, ""):
                return "custom image references are not accepted"
    return None


class CommandsMixin:
    """Methods of CloudService; see service.py."""


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

        # A command is one-shot per daemon lifetime. The cloud re-sends any
        # command that hasn't reached a terminal state on EVERY heartbeat,
        # so without this an update/backup that doesn't self-terminate gets
        # re-dispatched every ~5 min — re-spawning wattpost-update and
        # re-PATCHing `applying`, which resets the cloud's reconcile
        # watchdog so it never times out (observed: an update wedged
        # "applying" for 20+ min in a tight loop). Dispatch each cmd_id at
        # most once per process; after a real update the daemon restarts
        # with a fresh set and the update no-op guard catches the success.
        if not hasattr(self, "_dispatched_cmds"):
            self._dispatched_cmds = set()
        if cmd_id in self._dispatched_cmds:
            log.debug("cloud command %d already dispatched this session; skipping", cmd_id)
            return
        # A signature says the cloud issued this command, not that its strings
        # are sane. Version strings end up in an updater request and in
        # subprocess arguments, so hold them to a release-version shape here
        # no matter who signed them.
        if kind in ("update", "rollback", "pin_image_tag"):
            bad = _bad_version_field(cmd)
            if bad:
                log.warning("cloud command %d (%s) refused: %s", cmd_id, kind, bad)
                await self._patch_command_status(cmd_id, "failed", error=f"refused: {bad}")
                return
        # Bound the set so a long-lived appliance taking routine commands
        # (rule sync, backups, disk cleanup) for weeks doesn't grow it
        # without limit. Keep the most recent ids; cmd_ids only increase, so
        # an evicted-then-re-sent old id is implausible.
        if len(self._dispatched_cmds) >= 500:
            self._dispatched_cmds = set(sorted(self._dispatched_cmds)[-250:])
        self._dispatched_cmds.add(cmd_id)

        # Cross-restart guard for the spawning kinds. The per-process set
        # above stops re-dispatch within ONE daemon lifetime, but
        # wattpost-update / wattpost-rollback RESTART the daemon — so each
        # fresh process would dispatch once more, re-PATCH `applying`, and
        # reset the cloud's 10-min watchdog. A doomed op (e.g. a rollback
        # with no previous slot, or an update that can't reach target on a
        # dev install) thus loops across restarts forever. Persist tried
        # cmd_ids: if we already attempted this one in a prior process and
        # still aren't at its target, give up (mark failed) instead of
        # re-spawning.
        if kind in ("update", "rollback", "pin_image_tag"):
            if cmd_id in _load_tried_cmds():
                tv = (cmd.get("target_version") or "").strip()
                from .. import __version__ as _running_v
                if tv and tv == _running_v:
                    await self._patch_command_status(cmd_id, "picked_up")
                    await self._patch_command_status(cmd_id, "success")
                else:
                    await self._patch_command_status(
                        cmd_id, "failed",
                        error="not re-attempting after a restart — the "
                              "previous attempt didn't reach the target "
                              "version (see the update log).")
                return
            _mark_tried_cmd(cmd_id)

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
        `cloud.appliance_id` are preserved by
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
        headers.update(self._signed_headers(b"", "GET", url))
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

        self._spawn(_delayed_exec(), name="cloud-restore-exec")


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
        _atomic_write_text(path, yaml.safe_dump(raw, sort_keys=False))


    def _reload_alerts_engine(self) -> None:
        """Hot-reload the alerts engine with the current config.alerts.
        Same call path Settings → Alerts uses on rule add/edit."""
        engine = getattr(self.scheduler, "_alerts", None)
        if engine is not None and hasattr(engine, "reload_rules"):
            engine.reload_rules(self._all_rules)


    def _ledger(self):
        from .command_verify import CommandLedger, default_ledger_path
        if getattr(self, "_cmd_ledger", None) is None:
            self._cmd_ledger = CommandLedger(default_ledger_path())
        return self._cmd_ledger


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
        if status in ("success", "failed"):
            # Terminal: remember it so a replay of this signed command is refused.
            self._ledger().record(cmd_id)
        # Pre-serialise: the signature covers the exact bytes sent.
        import json as _json
        body_bytes = _json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.cfg.bearer_token}",
            "Content-Type":  "application/json",
        }
        headers.update(self._signed_headers(body_bytes, "PATCH", url))
        try:
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=True,
            ) as client:
                r = await client.patch(url, content=body_bytes, headers=headers)
            if r.status_code >= 400:
                log.warning(
                    "cloud command status PATCH HTTP %s for cmd %d→%s: %s",
                    r.status_code, cmd_id, status, r.text[:200],
                )
        except Exception as e:
            log.warning("cloud command status PATCH failed (cmd %d→%s): %s",
                        cmd_id, status, e)
