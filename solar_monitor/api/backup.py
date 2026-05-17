"""Settings → Backup & restore.

Single-button snapshot of the appliance's mutable state (SQLite DB +
config.yaml + web-password hash) into a downloadable tar.gz, and the
inverse restore endpoint that swaps it back into place and re-execs.

Scope is deliberately blunt — one tarball, all-or-nothing. A more
granular "settings only" restore can come later if customers ask for
it; for now the goal is "if my SD card dies, give me a one-click way
back to where I was".
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

from litestar import Request, Response, get, post
from litestar.datastructures import State
from litestar.exceptions import HTTPException

log = logging.getLogger(__name__)

# Files that are NEVER backed up:
#   - *-wal / *-shm — SQLite's write-ahead log + shared-memory sidecars;
#     the online .backup() API consolidates them into the main file.
#   - *.bak — config rotation backups (we keep our own .bak when the
#     wizard mutates config; not useful to ship inside a snapshot).
#   - *.legacy.bak — one-shot v0.0.60 DB-relocation safety net.

# Conventional location for the web-password hash on both Pi and Docker
# installs. If the file isn't there (older install, or someone moved it)
# we just skip it — the daemon will regenerate one on next first-login
# flow.
WEB_PASSWORD_HASH_PATH = Path("/etc/wattpost/web-password.hash")
WEB_PASSWORD_PLAIN_PATH = Path("/etc/wattpost/web-password")


def _sqlite_snapshot_to(src_path: str, dest_path: str) -> None:
    """Online backup: copy a live SQLite DB into `dest_path` without
    blocking writers for longer than each page-copy step. Safe to run
    while the daemon is polling — the destination is a transactionally
    consistent copy of the source as of when .backup() finishes.

    Sync API on purpose — runs inside `asyncio.to_thread()` from the
    handler. aiosqlite's backup wrapper is fine too, but the stdlib
    sqlite3 connection is simpler and avoids holding the writer
    connection open longer than necessary.
    """
    src = sqlite3.connect(src_path)
    try:
        dst = sqlite3.connect(dest_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _resolve_db_path(state: State) -> Path:
    """The actual on-disk DB file the daemon is using. We pull it via
    the open Store rather than re-reading config so we honour any
    CLI-override the operator passed at start-up."""
    store = state["store"]
    p = getattr(store, "_path", None) or getattr(store, "path", None)
    if not p:
        raise HTTPException(status_code=500, detail="cannot resolve DB path from store")
    return Path(p)


@get("/api/system/backup")
async def export_backup(state: State) -> Response:
    """Stream a tar.gz of the daemon's mutable state. Layout inside:

      data.sqlite                — online-backup snapshot of the DB
      config/config.yaml         — current config (if present)
      config/web-password.hash   — hashed local-UI password (if set)
      MANIFEST                   — version + timestamp + path map

    The DB snapshot is taken via the sqlite3 online-backup API so it's
    safe to download mid-poll without locking writers."""
    from .. import __version__

    db_path = _resolve_db_path(state)
    config_path = Path(state.get("config_path") or "/etc/wattpost/config.yaml")
    ts = int(time.time())

    def _build_archive() -> bytes:
        # Snapshot DB into a temp file first, then stream the temp into
        # the tarball. Building the tarball in memory keeps the code
        # simple — typical DB is a few hundred MB at worst, well within
        # the appliance's RAM budget (Pi 4 / 8GB).
        with tempfile.TemporaryDirectory(prefix="wattpost-backup-") as td:
            tmpdb = Path(td) / "data.sqlite"
            _sqlite_snapshot_to(str(db_path), str(tmpdb))

            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
                tar.add(tmpdb, arcname="data.sqlite")
                if config_path.is_file():
                    tar.add(config_path, arcname="config/config.yaml")
                if WEB_PASSWORD_HASH_PATH.is_file():
                    tar.add(WEB_PASSWORD_HASH_PATH, arcname="config/web-password.hash")
                if WEB_PASSWORD_PLAIN_PATH.is_file():
                    tar.add(WEB_PASSWORD_PLAIN_PATH, arcname="config/web-password")
                manifest = (
                    f"wattpost_version={__version__}\n"
                    f"created_ts={ts}\n"
                    f"db_path_at_capture={db_path}\n"
                    f"config_path_at_capture={config_path}\n"
                ).encode()
                info = tarfile.TarInfo("MANIFEST")
                info.size = len(manifest)
                info.mtime = ts
                tar.addfile(info, io.BytesIO(manifest))
            return buf.getvalue()

    data = await asyncio.to_thread(_build_archive)
    stamp = time.strftime("%Y-%m-%d-%H%M%S", time.localtime(ts))
    fname = f"wattpost-backup-{stamp}.tar.gz"
    return Response(
        content=data,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Content-Length": str(len(data)),
        },
    )


def _verify_archive(tar_bytes: bytes) -> dict[str, Any]:
    """Pre-flight: open the tar, sanity-check the DB file inside, parse
    the manifest. Raises on anything that would make a swap unsafe.
    Returns a dict of what we found, useful for the response payload."""
    buf = io.BytesIO(tar_bytes)
    try:
        tar = tarfile.open(fileobj=buf, mode="r:gz")
    except tarfile.TarError as e:
        raise HTTPException(status_code=400, detail=f"not a valid tar.gz: {e}")

    names = set(tar.getnames())
    if "data.sqlite" not in names:
        tar.close()
        raise HTTPException(
            status_code=400,
            detail="archive missing data.sqlite — not a WattPost backup",
        )

    # Stage the DB to disk and run integrity_check before accepting it.
    # SQLite will happily open a truncated/corrupt file and surface
    # errors only when you touch a damaged page; the integrity_check
    # PRAGMA forces a full scan so we fail loudly here rather than after
    # the swap when there's nothing to roll back to.
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        tmp_db_path = Path(f.name)
    try:
        member = tar.getmember("data.sqlite")
        extracted = tar.extractfile(member)
        if extracted is None:
            raise HTTPException(status_code=400, detail="archive's data.sqlite is empty")
        tmp_db_path.write_bytes(extracted.read())
        conn = sqlite3.connect(str(tmp_db_path))
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                raise HTTPException(
                    status_code=400,
                    detail=f"DB integrity_check failed: {row[0] if row else 'no result'}",
                )
            schema_version = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
        size = tmp_db_path.stat().st_size
    finally:
        try:
            tmp_db_path.unlink()
        except OSError:
            pass
        tar.close()

    manifest: dict[str, str] = {}
    buf.seek(0)
    tar2 = tarfile.open(fileobj=buf, mode="r:gz")
    try:
        if "MANIFEST" in names:
            m = tar2.extractfile(tar2.getmember("MANIFEST"))
            if m is not None:
                for line in m.read().decode(errors="replace").splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        manifest[k.strip()] = v.strip()
    finally:
        tar2.close()

    return {
        "db_size_bytes": size,
        "db_schema_version": schema_version,
        "has_config": "config/config.yaml" in names,
        "has_web_password": "config/web-password.hash" in names,
        "manifest": manifest,
    }


def _stage_and_swap(
    tar_bytes: bytes, db_target: Path, config_target: Path,
) -> dict[str, Any]:
    """Extract each tar member into a sibling .new file alongside its
    eventual target, then rename atomically. Staging next to the
    target (rather than into a single shared tmpdir) matters because
    `/var/lib/wattpost` and `/etc/wattpost` are typically separate
    bind mounts in Docker installs — a cross-filesystem `os.replace`
    fails with EXDEV.

    Order: write all `.new` siblings first; then swap each over its
    target. If a write fails, no target is touched.

    Returns a dict describing what was written."""
    summary: dict[str, Any] = {"files": []}

    db_new = db_target.with_name(db_target.name + ".restoring")
    cfg_new = config_target.with_name(config_target.name + ".restoring")
    pw_hash_new = WEB_PASSWORD_HASH_PATH.with_name(WEB_PASSWORD_HASH_PATH.name + ".restoring")
    pw_plain_new = WEB_PASSWORD_PLAIN_PATH.with_name(WEB_PASSWORD_PLAIN_PATH.name + ".restoring")

    # Make sure parent dirs exist (fresh install case).
    db_target.parent.mkdir(parents=True, exist_ok=True)
    config_target.parent.mkdir(parents=True, exist_ok=True)
    WEB_PASSWORD_HASH_PATH.parent.mkdir(parents=True, exist_ok=True)

    have_cfg = False
    have_pw_hash = False
    have_pw_plain = False

    buf = io.BytesIO(tar_bytes)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.name == "data.sqlite":
                f = tar.extractfile(member)
                if f is None:
                    continue
                db_new.write_bytes(f.read())
            elif member.name == "config/config.yaml":
                f = tar.extractfile(member)
                if f is None:
                    continue
                cfg_new.write_bytes(f.read())
                have_cfg = True
            elif member.name == "config/web-password.hash":
                f = tar.extractfile(member)
                if f is None:
                    continue
                pw_hash_new.write_bytes(f.read())
                have_pw_hash = True
            elif member.name == "config/web-password":
                f = tar.extractfile(member)
                if f is None:
                    continue
                pw_plain_new.write_bytes(f.read())
                have_pw_plain = True

    # All staged successfully — swap each one into place.
    try:
        if db_new.is_file():
            # Drop stale WAL/SHM so the new DB doesn't adopt them.
            for suffix in ("-wal", "-shm"):
                stale = db_target.with_name(db_target.name + suffix)
                try:
                    stale.unlink()
                except FileNotFoundError:
                    pass
            os.replace(db_new, db_target)
            summary["files"].append(str(db_target))

        if have_cfg:
            # Keep a .restored.bak of the OLD config so a bad restore
            # is reversible. Named distinctly from the wizard's auto
            # .bak so an operator can tell why it exists.
            if config_target.is_file():
                try:
                    shutil.copy2(config_target, config_target.with_suffix(
                        config_target.suffix + ".restored.bak"
                    ))
                except OSError:
                    log.exception("could not stash pre-restore config backup")
            os.replace(cfg_new, config_target)
            summary["files"].append(str(config_target))

        if have_pw_hash:
            os.replace(pw_hash_new, WEB_PASSWORD_HASH_PATH)
            summary["files"].append(str(WEB_PASSWORD_HASH_PATH))

        if have_pw_plain:
            os.replace(pw_plain_new, WEB_PASSWORD_PLAIN_PATH)
            summary["files"].append(str(WEB_PASSWORD_PLAIN_PATH))
    finally:
        # If something blew up mid-swap, clean up any leftover .restoring
        # files. The originals are untouched (atomic rename is all-or-
        # nothing) so the daemon keeps running on the old state.
        for stale in (db_new, cfg_new, pw_hash_new, pw_plain_new):
            try:
                stale.unlink()
            except FileNotFoundError:
                pass

    return summary


@post("/api/system/restore", status_code=202)
async def import_backup(request: Request, state: State) -> dict[str, Any]:
    """Validate + apply a backup tarball uploaded as raw bytes (the JS
    sends `application/gzip` directly — no multipart wrapping, keeps
    the code on both sides simple).

    Order of operations:
      1. Read body into memory (capped at 2 GB, generous).
      2. Verify it's a valid tar with a passing-integrity SQLite inside.
      3. Atomically swap DB + config + password into place.
      4. Schedule a re-exec so the daemon comes back up against the new
         state — the response goes out first so the client sees 202
         and starts polling /api/health.
    """
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty body")
    if len(body) > 2 * 1024 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="archive too large (>2 GB)")

    verdict = await asyncio.to_thread(_verify_archive, body)

    db_target = _resolve_db_path(state)
    config_target = Path(state.get("config_path") or "/etc/wattpost/config.yaml")

    summary = await asyncio.to_thread(
        _stage_and_swap, body, db_target, config_target,
    )

    # Re-exec the daemon so it picks up the new DB and config cleanly.
    # Same pattern as /api/system/restart — give the HTTP response 0.4s
    # to flush before exec replaces the process image.
    scheduler = state.get("scheduler")

    async def _delayed_exec() -> None:
        await asyncio.sleep(0.4)
        try:
            if scheduler is not None:
                await scheduler.stop()
        except Exception:
            log.exception("scheduler stop failed before restart")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    asyncio.create_task(_delayed_exec())

    return {
        "ok": True,
        "message": "restore staged, daemon restarting",
        "applied": summary,
        "verdict": verdict,
    }
