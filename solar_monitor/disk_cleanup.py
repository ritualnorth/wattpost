"""Non-destructive disk housekeeping for the cloud-orchestrated
``disk_cleanup`` command (#279).

Bundles operations that customers would otherwise have to SSH in for:

  1. Force-prune local snapshots beyond ``backup.keep_count``, same
     pruner the scheduled backup loop uses, just run on demand.
  2. ``journalctl --vacuum-size=500M`` to cap systemd journal growth
     on Pi installs (no-op on Docker; the container doesn't carry
     its own journald).
  3. ``apt-get clean`` + ``apt-get autoremove -y`` to drop cached
     ``.deb`` packages and orphaned dependencies on Pi installs.

Explicitly NOT doing here:

  * ``apt upgrade``, can wedge a remote Pi (kernel + glibc bumps),
    see ``[[security-patches-surface]]`` for the right safety chain
    we'd need first.
  * ``rm -rf /var/log/*``, would nuke non-journald logs we may
    care about during incident triage.
  * Docker image prune, needs docker-socket access the wattpost
    container doesn't have. Will route through the updater sidecar
    in a follow-up; for now Docker installs only get snapshot
    pruning here.

Returns a structured report ``{ "ops": [...], "freed_bytes": int,
"errors": [...] }`` so the caller can stuff it into the command's
completion message + the user can see what actually happened.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Cap on subprocess runtime per op. journal vacuum + apt clean usually
# finish in seconds; autoremove is the long pole, 180s is the
# pragmatic ceiling, beyond which we assume something's wrong.
_OP_TIMEOUT_S = 180


async def _run(cmd: list[str]) -> tuple[int, str]:
    """Run cmd with a hard timeout. Returns (exit_code, combined-output-tail)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=_OP_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return 124, f"timeout after {_OP_TIMEOUT_S}s"
        return proc.returncode or 0, (out_b or b"").decode("utf-8", "replace")[-400:]
    except FileNotFoundError:
        return 127, f"{cmd[0]} not found"
    except Exception as e:
        return 1, f"{type(e).__name__}: {e}"


def _disk_free(path: str = "/") -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def _prune_snapshots(scheduler) -> dict[str, Any]:
    backup_svc = getattr(scheduler, "backup_service", None)
    if backup_svc is None:
        return {"op": "snapshot_prune", "ok": True, "note": "no backup service configured"}
    try:
        # Reuses the same retention-aware pruner the scheduled backup
        # loop calls. Honours backup.keep_count from config.yaml.
        backup_svc._prune()
        return {"op": "snapshot_prune", "ok": True, "note": "pruned to keep_count"}
    except Exception as e:
        log.exception("disk_cleanup: snapshot prune failed")
        return {"op": "snapshot_prune", "ok": False, "error": f"{type(e).__name__}: {e}"}


async def _journal_vacuum(size_cap: str = "500M") -> dict[str, Any]:
    rc, out = await _run(["journalctl", f"--vacuum-size={size_cap}"])
    return {
        "op": "journal_vacuum",
        "ok": rc == 0,
        "note": f"capped journal to {size_cap}" if rc == 0 else out,
    }


async def _apt_clean() -> dict[str, Any]:
    # apt-get clean = drop cached .deb files in /var/cache/apt/archives.
    # Safe; just clears the cache, doesn't touch installed packages.
    rc, out = await _run(["apt-get", "clean"])
    return {
        "op": "apt_clean",
        "ok": rc == 0,
        "note": "package cache cleared" if rc == 0 else out,
    }


async def _apt_autoremove() -> dict[str, Any]:
    # apt-get autoremove = remove packages installed as dependencies
    # of something now uninstalled. Common after `apt remove pkg`.
    # `-y` because we're non-interactive.
    rc, out = await _run(["apt-get", "autoremove", "-y"])
    return {
        "op": "apt_autoremove",
        "ok": rc == 0,
        "note": "orphaned deps removed" if rc == 0 else out,
    }


async def run(scheduler) -> dict[str, Any]:
    """Run all applicable ops. Caller supplies the live scheduler
    (CloudService.scheduler) so we can reach backup_service for the
    snapshot prune. Returns a report, caller decides success/failure
    framing based on `errors`."""
    is_docker = os.environ.get("WATTPOST_DEPLOYMENT") == "docker"
    before = _disk_free("/")
    ops: list[dict[str, Any]] = []

    # 1. Snapshot prune (works on both Pi + Docker, snapshots dir is
    #    bind-mounted into the container on Docker installs).
    if scheduler is not None:
        ops.append(_prune_snapshots(scheduler))

    # 2-4. systemd / apt only on Pi installs. Container has neither
    #      systemd nor an apt history; these would be confusing 127s.
    if not is_docker:
        ops.append(await _journal_vacuum())
        ops.append(await _apt_clean())
        ops.append(await _apt_autoremove())

    after = _disk_free("/")
    freed = max(0, after - before)
    errors = [o for o in ops if not o.get("ok", False)]
    return {
        "ops":          ops,
        "freed_bytes":  freed,
        "before_free":  before,
        "after_free":   after,
        "errors":       errors,
        "deployment":   "docker" if is_docker else "pi",
    }
