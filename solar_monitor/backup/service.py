"""Scheduled local-snapshot loop. Writes a tar.gz under
`<dir>/wattpost-auto-YYYY-MM-DD-HHMMSS.tar.gz` every
`interval_hours`, prunes oldest beyond `keep_count`. Manual snapshots
written via `BackupService.snapshot_now()` follow the same naming +
counting rules.

The cloud-upload side (Pro tier) is implemented separately in a
follow-up commit; this module exposes a `cloud_uploader` hook that
the service calls after each successful local snapshot when the
config enables it. For phase 1 the hook is a no-op stub.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable

from ..api.backup import build_archive_bytes
from ..config import BackupCfg

log = logging.getLogger(__name__)

# Auto-snapshots have a distinct prefix from on-demand downloads so we
# can identify "things this service made" cleanly during pruning.
AUTO_PREFIX = "wattpost-auto-"
AUTO_SUFFIX = ".tar.gz"


def _resolve_backup_dir(cfg: BackupCfg, db_path: Path) -> Path:
    if cfg.dir:
        return Path(cfg.dir)
    # Default: sibling of the SQLite file. Keeps the on-disk layout
    # tidy for Pi installs (/var/lib/wattpost/{db,backups/}).
    return db_path.parent / "backups"


def list_auto_snapshots(backup_dir: Path) -> list[Path]:
    if not backup_dir.is_dir():
        return []
    return sorted(
        [p for p in backup_dir.iterdir()
         if p.is_file() and p.name.startswith(AUTO_PREFIX) and p.name.endswith(AUTO_SUFFIX)],
        key=lambda p: p.stat().st_mtime,
    )


# Optional Pro-tier hook. Phase 2 wires this to the cloud upload path.
CloudUploader = Callable[[Path], Awaitable[bool]]


class BackupService:
    def __init__(
        self,
        cfg: BackupCfg,
        db_path: Path,
        config_path: Path | None,
        cloud_uploader: CloudUploader | None = None,
    ) -> None:
        self.cfg = cfg
        self.db_path = db_path
        self.config_path = config_path
        self.cloud_uploader = cloud_uploader
        self.backup_dir = _resolve_backup_dir(cfg, db_path)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        # Surfaced via /api/system/backup/schedule for the Settings UI.
        self.last_run_ts: int | None = None
        self.last_run_path: Path | None = None
        self.last_run_error: str | None = None
        self.last_cloud_upload_ok: bool | None = None
        self.last_cloud_upload_ts: int | None = None
        self.last_cloud_upload_error: str | None = None

    async def start(self) -> None:
        if not self.cfg.enabled:
            log.info("backup service: disabled in config; not starting loop")
            return
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="backup-scheduler")
        log.info(
            "backup service started (every %d h, keep %d, dir=%s, cloud_upload=%s)",
            self.cfg.interval_hours, self.cfg.keep_count, self.backup_dir,
            self.cfg.cloud_upload,
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    def next_run_ts(self) -> int | None:
        """When the loop intends to fire next (UTC seconds). None if
        the service is disabled or hasn't computed yet."""
        if not self.cfg.enabled:
            return None
        snaps = list_auto_snapshots(self.backup_dir)
        anchor = int(snaps[-1].stat().st_mtime) if snaps else None
        if anchor is None:
            # First run hasn't happened yet; loop will fire at boot.
            return int(time.time())
        return anchor + self.cfg.interval_hours * 3600

    async def snapshot_now(self) -> Path:
        """Take a snapshot synchronously (well, async) and return its
        path. Used by the manual "Run now" button and by the scheduler
        loop. Throws on failure — caller handles."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        data = await asyncio.to_thread(
            build_archive_bytes, self.db_path, self.config_path,
        )
        stamp = time.strftime("%Y-%m-%d-%H%M%S", time.localtime())
        out_path = self.backup_dir / f"{AUTO_PREFIX}{stamp}{AUTO_SUFFIX}"
        await asyncio.to_thread(out_path.write_bytes, data)
        self.last_run_ts = int(time.time())
        self.last_run_path = out_path
        self.last_run_error = None
        log.info("backup: wrote %s (%d bytes)", out_path.name, len(data))
        # Prune after a successful write — we never delete to make room
        # for a snapshot that might fail, so the policy is "keep N most
        # recent that exist on disk after this run completed".
        self._prune()
        # Fire-and-await cloud upload if configured. We don't fail the
        # local snapshot if the cloud half blows up — local is the
        # source of truth, cloud is best-effort backup-of-backup.
        if self.cfg.cloud_upload and self.cloud_uploader is not None:
            try:
                ok = await self.cloud_uploader(out_path)
                self.last_cloud_upload_ok = ok
                self.last_cloud_upload_ts = int(time.time())
                if ok:
                    self.last_cloud_upload_error = None
                    log.info("backup: cloud upload ok (%s)", out_path.name)
                else:
                    self.last_cloud_upload_error = "uploader returned False"
                    log.warning("backup: cloud upload returned False (%s)", out_path.name)
            except Exception as e:
                self.last_cloud_upload_ok = False
                self.last_cloud_upload_ts = int(time.time())
                self.last_cloud_upload_error = str(e)
                log.warning("backup: cloud upload failed: %s", e)
        return out_path

    def _prune(self) -> None:
        snaps = list_auto_snapshots(self.backup_dir)
        excess = len(snaps) - self.cfg.keep_count
        for stale in snaps[:max(0, excess)]:
            try:
                stale.unlink()
                log.info("backup: pruned old snapshot %s", stale.name)
            except OSError as e:
                log.warning("backup: could not prune %s: %s", stale.name, e)

    async def _loop(self) -> None:
        # Boot-anchor: if the newest existing snapshot is older than
        # the interval, run immediately. Otherwise sleep until it
        # expires. Means a Pi that reboots every couple of days still
        # gets a weekly cadence without the loop resetting on every
        # boot (a naive sleep-then-snap would).
        while not self._stop.is_set():
            snaps = list_auto_snapshots(self.backup_dir)
            now = int(time.time())
            interval_s = max(60, self.cfg.interval_hours * 3600)
            if snaps:
                age = now - int(snaps[-1].stat().st_mtime)
                wait_s = max(0, interval_s - age)
            else:
                wait_s = 0  # never snapped here → run now

            if wait_s > 0:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=wait_s)
                    return
                except asyncio.TimeoutError:
                    pass

            try:
                await self.snapshot_now()
            except Exception as e:
                self.last_run_error = str(e)
                log.exception("backup: snapshot_now failed: %s", e)
                # Don't tight-loop on persistent failure — back off an
                # hour and try again. Beats spamming the log forever
                # when something's structurally wrong (disk full, etc).
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=3600)
                    return
                except asyncio.TimeoutError:
                    pass
