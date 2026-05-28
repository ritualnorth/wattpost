"""Settings → Backup & restore.

Single-button snapshot of the appliance's mutable state (SQLite DB +
config.yaml + web-password hash) into a downloadable tar.gz, and the
inverse restore endpoint that swaps it back into place and re-execs.

Scope is deliberately blunt, one tarball, all-or-nothing. A more
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

from litestar import Request, Response, delete, get, post
from litestar.datastructures import State
from litestar.exceptions import HTTPException, NotFoundException

log = logging.getLogger(__name__)

# Files that are NEVER backed up:
#   - *-wal / *-shm, SQLite's write-ahead log + shared-memory sidecars;
#     the online .backup() API consolidates them into the main file.
#   - *.bak, config rotation backups (we keep our own .bak when the
#     wizard mutates config; not useful to ship inside a snapshot).
#   - *.legacy.bak, one-shot v0.0.60 DB-relocation safety net.

# Conventional location for the web-password hash on both Pi and Docker
# installs. If the file isn't there (older install, or someone moved it)
# we just skip it, the daemon will regenerate one on next first-login
# flow.
WEB_PASSWORD_HASH_PATH = Path("/etc/wattpost/web-password.hash")
WEB_PASSWORD_PLAIN_PATH = Path("/etc/wattpost/web-password")


def _sqlite_snapshot_to(src_path: str, dest_path: str) -> None:
    """Online backup: copy a live SQLite DB into `dest_path` without
    blocking writers for longer than each page-copy step. Safe to run
    while the daemon is polling, the destination is a transactionally
    consistent copy of the source as of when .backup() finishes.

    Sync API on purpose, runs inside `asyncio.to_thread()` from the
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


def build_archive_bytes(db_path: Path, config_path: Path | None) -> bytes:
    """Build the backup tarball as a bytes payload. Reusable by the
    on-demand HTTP endpoint and the scheduled BackupService, same
    archive layout, same SQLite online-backup safety, same MANIFEST."""
    from .. import __version__
    ts = int(time.time())
    with tempfile.TemporaryDirectory(prefix="wattpost-backup-") as td:
        tmpdb = Path(td) / "data.sqlite"
        _sqlite_snapshot_to(str(db_path), str(tmpdb))
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
            tar.add(tmpdb, arcname="data.sqlite")
            if config_path and config_path.is_file():
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


@get("/api/system/backup")
async def export_backup(state: State) -> Response:
    """Stream a tar.gz of the daemon's mutable state. Layout inside:

      data.sqlite               , online-backup snapshot of the DB
      config/config.yaml        , current config (if present)
      config/web-password.hash  , hashed local-UI password (if set)
      MANIFEST                  , version + timestamp + path map

    The DB snapshot is taken via the sqlite3 online-backup API so it's
    safe to download mid-poll without locking writers."""
    db_path = _resolve_db_path(state)
    config_path = Path(state.get("config_path") or "/etc/wattpost/config.yaml")
    data = await asyncio.to_thread(build_archive_bytes, db_path, config_path)
    stamp = time.strftime("%Y-%m-%d-%H%M%S", time.localtime())
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
            detail="archive missing data.sqlite, not a WattPost backup",
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


# #297 mitigation 1, allowlist for top-level config.yaml keys when
# restoring from cloud. Anything not on this list is dropped silently
# (logged + listed in the restore summary). Defensive against a
# compromised cloud account uploading a backup with an arbitrary
# `mqtt_out:` block, an attacker `webhooks:` block, or any other new
# key designed to exfil data via a future config-reader.
#
# Source of truth: solar_monitor.config.Config dataclass fields.
# Keep in sync if a top-level key is added there.
_RESTORE_ALLOWED_TOPLEVEL = frozenset({
    "db_path", "transports", "devices", "exporters",
    "notification_transports", "alerts", "alerts_seeded",
    "quiet_hours", "forecast", "weather", "cloud", "gps",
    "location", "bank", "backup", "discovery", "local_telemetry",
    "history", "solar_pause", "smart_plugs", "mqtt_in",
})

# Substring patterns matched (case-insensitive) against any dict key.
# Hit → the value is zeroed out so the operator re-enters it via the
# Settings UI. Substring-matching catches both `password` and
# `smtp_password`, both `token` and `bearer_token` / `api_token`,
# `secret` and `client_secret` etc. Defensive against a compromised
# cloud account uploading a backup with operator credentials.
_RESTORE_REDACT_SUBSTRINGS: tuple[str, ...] = (
    "password", "passwd", "secret", "token", "bearer", "api_key",
    "private_key", "hmac", "credential",
)


def _sanitize_restored_config(raw: dict, *, dropped: list, redacted: list) -> dict:
    """Apply #297 mitigation 1 to a parsed config dict. Returns a new
    dict containing ONLY allowlisted top-level keys, with credential
    fields recursively redacted (replaced with empty string so the
    schema still validates but the operator is forced to re-enter).

    Mutates the supplied `dropped` / `redacted` lists with breadcrumbs
    for the restore summary so operators know what to re-set."""
    out: dict = {}
    for k, v in raw.items():
        if k not in _RESTORE_ALLOWED_TOPLEVEL:
            dropped.append(k)
            continue
        out[k] = _redact_credentials(v, path=k, redacted=redacted)
    return out


def _redact_credentials(value: Any, *, path: str, redacted: list) -> Any:
    """Recurse the value and zero-out any key whose name looks like a
    credential. Lists are walked; non-dict/list values pass through."""
    if isinstance(value, dict):
        out_d: dict = {}
        for k, v in value.items():
            looks_secret = (
                isinstance(k, str)
                and any(s in k.lower() for s in _RESTORE_REDACT_SUBSTRINGS)
            )
            if looks_secret:
                # Only redact non-empty strings, preserves boolean
                # toggles and blank defaults so the user only sees
                # "re-enter this" for fields that were actually set.
                if isinstance(v, str) and v != "":
                    redacted.append(f"{path}.{k}")
                    out_d[k] = ""
                else:
                    out_d[k] = v
            else:
                out_d[k] = _redact_credentials(v, path=f"{path}.{k}", redacted=redacted)
        return out_d
    if isinstance(value, list):
        return [
            _redact_credentials(item, path=f"{path}[{i}]", redacted=redacted)
            for i, item in enumerate(value)
        ]
    return value


def _stage_and_swap(
    tar_bytes: bytes, db_target: Path, config_target: Path,
) -> dict[str, Any]:
    """Extract each tar member into a sibling .new file alongside its
    eventual target, then rename atomically. Staging next to the
    target (rather than into a single shared tmpdir) matters because
    `/var/lib/wattpost` and `/etc/wattpost` are typically separate
    bind mounts in Docker installs, a cross-filesystem `os.replace`
    fails with EXDEV.

    Order: write all `.new` siblings first; then swap each over its
    target. If a write fails, no target is touched.

    Pairing-preserve: the live cloud block (bearer_token, sso_secret,
    tunnel_*, appliance_id) is read BEFORE the swap and re-injected
    into the restored YAML. Restoring a backup taken on a previous
    install would otherwise clobber the fresh pair's tokens with
    the dead appliance's, breaking heartbeat, SSO, and the CF
    tunnel route. Same-appliance rollback is unaffected (the
    preserved values match what was in the backup anyway).

    The web-password files are *not* overwritten when the target
    already exists, operator's current password on the fresh
    install wins over whatever was in the backup, for the same
    reason: shouldn't have to know the old password to use the
    fresh box.

    Returns a dict describing what was written."""
    import yaml as _yaml
    summary: dict[str, Any] = {"files": [], "preserved_pairing": False}

    # Read current cloud block + flag whether a password file is
    # already in place. Done up-front so we can apply both decisions
    # while reading the tar.
    preserved_cloud: dict | None = None
    if config_target.is_file():
        try:
            raw_cur = _yaml.safe_load(config_target.read_text()) or {}
            preserved_cloud = raw_cur.get("cloud")
        except Exception:
            log.exception("could not read current config for pairing-preserve")
    existing_pw_hash = WEB_PASSWORD_HASH_PATH.is_file()
    existing_pw_plain = WEB_PASSWORD_PLAIN_PATH.is_file()

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
                raw_bytes = f.read()
                # #297-1, ALWAYS sanitize the restored config.yaml
                # regardless of pairing-preserve path. Drop unknown
                # top-level keys + redact credential-shaped fields so
                # a compromised cloud account can't re-aim mqtt_out,
                # webhooks, or smuggle a future-key it didn't have at
                # backup time. Operator re-enters credentials via the
                # Settings UI on next sign-in.
                dropped: list = []
                redacted: list = []
                try:
                    raw_restored = _yaml.safe_load(raw_bytes) or {}
                    if not isinstance(raw_restored, dict):
                        raise ValueError("config.yaml is not a mapping")
                    raw_restored = _sanitize_restored_config(
                        raw_restored, dropped=dropped, redacted=redacted,
                    )
                    if preserved_cloud is not None:
                        raw_restored["cloud"] = preserved_cloud
                        summary["preserved_pairing"] = True
                    cfg_new.write_text(
                        _yaml.safe_dump(raw_restored, sort_keys=False)
                    )
                    if dropped:
                        log.warning(
                            "restore: dropped %d unknown top-level config keys: %s",
                            len(dropped), dropped,
                        )
                        summary["dropped_config_keys"] = dropped
                    if redacted:
                        log.warning(
                            "restore: redacted %d credential field(s) "
                            "from restored config: %s, operator must "
                            "re-enter via Settings",
                            len(redacted), redacted,
                        )
                        summary["redacted_credentials"] = redacted
                except Exception:
                    # Sanitizer / parse failed, fall back to wholesale
                    # replace so the operator at least gets their DB
                    # back. Pairing-preserve also lost. Logged loudly.
                    log.exception(
                        "config.yaml sanitize/restore failed; falling "
                        "back to as-is replace (pairing-preserve lost)"
                    )
                    cfg_new.write_bytes(raw_bytes)
                have_cfg = True
            elif member.name == "config/web-password.hash":
                # Preserve existing, operator's current password on the
                # fresh install shouldn't get clobbered by an old one
                # they may not remember.
                if existing_pw_hash:
                    continue
                # #297-2, on a true fresh install (no hash AND no
                # plaintext exist), DO NOT trust the restored hash.
                # A compromised cloud could supply an attacker-chosen
                # password. Instead leave the password files absent
                # and let the daemon's first-boot generator mint a
                # new random one on next start. Operator gets it via
                # `wattpost-config` / SSH MOTD as on any fresh install.
                if not existing_pw_plain:
                    summary["fresh_install_password_regen"] = True
                    log.warning(
                        "restore: fresh install detected, declining to "
                        "restore web-password.hash from backup; first-"
                        "boot password generator will mint a new one"
                    )
                    continue
                f = tar.extractfile(member)
                if f is None:
                    continue
                pw_hash_new.write_bytes(f.read())
                have_pw_hash = True
            elif member.name == "config/web-password":
                if existing_pw_plain:
                    continue
                if not existing_pw_hash:
                    # Same #297-2 rationale, don't trust restored
                    # plaintext on a true fresh install.
                    continue
                f = tar.extractfile(member)
                if f is None:
                    continue
                pw_plain_new.write_bytes(f.read())
                have_pw_plain = True

    # All staged successfully, swap each one into place.
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


@post(
    "/api/system/restore",
    status_code=202,
    # Generous body cap: appliances with a year+ of history can
    # produce snapshots well over Litestar's default 10 MB ceiling.
    request_max_body_size=2 * 1024 * 1024 * 1024,
)
async def import_backup(request: Request, state: State) -> dict[str, Any]:
    """Validate + apply a backup tarball uploaded as raw bytes (the JS
    sends `application/gzip` directly, no multipart wrapping, keeps
    the code on both sides simple).

    Order of operations:
      1. Read body into memory (capped at 2 GB, generous).
      2. Verify it's a valid tar with a passing-integrity SQLite inside.
      3. Atomically swap DB + config + password into place.
      4. Schedule a re-exec so the daemon comes back up against the new
         state, the response goes out first so the client sees 202
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
    # Same pattern as /api/system/restart, give the HTTP response 0.4s
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


# ---- Scheduled snapshots (Settings → Backup & restore → "Automatic")
#
# The on-demand endpoint above is for "give me the current state as a
# file right now"; these endpoints surface the local rotating snapshots
# the BackupService writes on a timer. Same archive format, same
# restore path, a scheduled .tar.gz is interchangeable with a manual
# download for restore purposes.

def _backup_service(state: State):
    svc = state.get("backup_service")
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail="backup service not running, set backup.enabled: true in config.yaml",
        )
    return svc


def _safe_snapshot_path(svc, name: str) -> Path:
    """Resolve `name` to a real file under the backup dir, rejecting
    any path traversal. We never accept absolute paths or names
    containing `/`, the only thing a client should send is the
    basename of an entry from the list endpoint."""
    if "/" in name or name.startswith(".") or not name.endswith(".tar.gz"):
        raise HTTPException(status_code=400, detail="invalid snapshot name")
    target = (svc.backup_dir / name).resolve()
    try:
        target.relative_to(svc.backup_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="snapshot escapes backup dir")
    if not target.is_file():
        raise NotFoundException(f"no such snapshot {name!r}")
    return target


@get("/api/system/backup/schedule")
async def backup_schedule(state: State) -> dict[str, Any]:
    """Status payload for the Settings UI: what the service is doing
    right now, plus the catalogue of local snapshots on disk."""
    from ..backup.service import list_auto_snapshots
    svc = state.get("backup_service")
    if svc is None:
        return {"enabled": False, "snapshots": []}
    snaps = list_auto_snapshots(svc.backup_dir)
    return {
        "enabled": svc.cfg.enabled,
        "interval_hours": svc.cfg.interval_hours,
        "keep_count": svc.cfg.keep_count,
        "dir": str(svc.backup_dir),
        "next_run_ts": svc.next_run_ts(),
        "last_run_ts": svc.last_run_ts,
        "last_run_path": str(svc.last_run_path) if svc.last_run_path else None,
        "last_run_error": svc.last_run_error,
        "cloud_upload_enabled": svc.cfg.cloud_upload,
        "last_cloud_upload_ok": svc.last_cloud_upload_ok,
        "last_cloud_upload_ts": svc.last_cloud_upload_ts,
        "last_cloud_upload_error": svc.last_cloud_upload_error,
        "snapshots": [
            {
                "name": p.name,
                "size_bytes": p.stat().st_size,
                "mtime_ts": int(p.stat().st_mtime),
            }
            for p in reversed(snaps)  # newest first for the UI
        ],
    }


@post("/api/system/backup/run-now", status_code=200)
async def backup_run_now(state: State) -> dict[str, Any]:
    """Trigger an immediate snapshot. Synchronous so the UI can show
    the new file in the listing right away, for a few-hundred-MB DB
    on a Pi this takes a couple of seconds, well within an HTTP
    request budget."""
    svc = _backup_service(state)
    try:
        out = await svc.snapshot_now()
    except Exception as e:
        log.exception("manual snapshot failed")
        raise HTTPException(status_code=500, detail=f"snapshot failed: {e}")
    return {
        "ok": True,
        "snapshot": out.name,
        "size_bytes": out.stat().st_size,
        "mtime_ts": int(out.stat().st_mtime),
    }


@get("/api/system/backup/file/{name:str}")
async def backup_download_one(name: str, state: State) -> Response:
    """Download a specific scheduled snapshot. The list endpoint hands
    out names; this serves them up as application/gzip downloads with
    the same Content-Disposition pattern as the on-demand backup."""
    svc = _backup_service(state)
    target = _safe_snapshot_path(svc, name)
    data = await asyncio.to_thread(target.read_bytes)
    return Response(
        content=data,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Content-Length": str(len(data)),
        },
    )


@delete("/api/system/backup/file/{name:str}", status_code=200)
async def backup_delete_one(name: str, state: State) -> dict[str, Any]:
    """Remove a single scheduled snapshot. Operator-driven cleanup;
    distinct from the service's automatic pruning by keep_count."""
    svc = _backup_service(state)
    target = _safe_snapshot_path(svc, name)
    await asyncio.to_thread(target.unlink)
    return {"ok": True, "deleted": name}


# ---- Cloud-side backup mirror (phase 2)
#
# Proxies through to wattpost.cloud's /api/internal/backups/* so the
# Settings UI doesn't have to know cloud credentials. Returns 503
# when the appliance isn't paired or cloud upload is disabled, the
# UI shows an explanatory placeholder in that case.


def _cloud_creds(state: State) -> tuple[str, str]:
    cfg = state.get("config")
    if cfg is None or cfg.cloud is None or not cfg.cloud.bearer_token:
        raise HTTPException(
            status_code=503,
            detail="appliance is not paired to wattpost.cloud",
        )
    return cfg.cloud.endpoint.rstrip("/"), cfg.cloud.bearer_token


@get("/api/system/backup/cloud-list")
async def backup_cloud_list(state: State) -> dict[str, Any]:
    """List the appliance's cloud-side backups. Bare proxy through
    to /api/internal/backups/list on wattpost.cloud, auth handled
    by the appliance's bearer token.

    Maps a few upstream conditions to friendlier shapes so the UI
    doesn't surface raw cloud-side errors:
      - 404 → cloud is on an older build that doesn't have the
        endpoint yet. Returned as `{backups: [], not_yet_available: true}`.
      - 402 → Hobby-tier account. Returned with `tier_required: true`
        so the UI can show an upgrade CTA.
    """
    import httpx
    endpoint, token = _cloud_creds(state)
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{endpoint}/api/internal/backups/list",
            headers={"Authorization": f"Bearer {token}"},
        )
    if r.status_code == 404:
        return {"backups": [], "not_yet_available": True}
    if r.status_code == 402:
        return {"backups": [], "tier_required": True, "message": r.text[:300]}
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text[:300])
    return r.json()


@post("/api/system/backup/cloud-toggle", status_code=200)
async def backup_cloud_toggle(request: Request, state: State) -> dict[str, Any]:
    """Flip `backup.cloud_upload` in the running config and persist
    to disk. Re-wires the BackupService's uploader hook in-process
    so no daemon restart is needed. Body: `{enabled: bool}`.

    Server-side defence in depth: when enabling, pre-flight a call
    to the cloud so a Hobby-tier or unpaired user can't sneak past
    the UI gate by curl-ing this endpoint. The UI is also expected
    to greys-out the toggle, but the endpoint must reject too.
    """
    import shutil as _shutil
    import yaml as _yaml
    import httpx as _httpx
    body = await request.json()
    enabled = bool(body.get("enabled")) if isinstance(body, dict) else False

    if enabled:
        # Pre-flight against the cloud. Same path the UI hits when
        # rendering, `tier_required` for Hobby accounts,
        # `not_yet_available` if the cloud is on an older build.
        try:
            endpoint, token = _cloud_creds(state)
        except HTTPException as e:
            raise HTTPException(
                status_code=e.status_code,
                detail="Pair the appliance to wattpost.cloud before enabling cloud backups.",
            )
        async with _httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{endpoint}/api/internal/backups/list",
                headers={"Authorization": f"Bearer {token}"},
            )
        if r.status_code == 402:
            raise HTTPException(
                status_code=402,
                detail=(
                    "Cloud backups require Pro or Installer tier. "
                    "Upgrade at https://wattpost.cloud/app/account to enable."
                ),
            )
        if r.status_code == 404:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Cloud account is on an older build that doesn't "
                    "accept backup uploads yet. Try again shortly."
                ),
            )
        if r.status_code >= 400:
            raise HTTPException(
                status_code=r.status_code,
                detail=f"Cloud rejected pre-flight: {r.text[:200]}",
            )

    config_path = state.get("config_path")
    if not config_path:
        raise HTTPException(status_code=500, detail="config_path unset")
    path = Path(config_path)

    # Mutate on-disk YAML, same pattern as alerts_admin._save_config.
    raw = _yaml.safe_load(path.read_text()) or {}
    backup_block = raw.get("backup") or {}
    backup_block["cloud_upload"] = enabled
    raw["backup"] = backup_block
    bak = path.with_suffix(path.suffix + ".bak")
    _shutil.copy2(path, bak)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_yaml.safe_dump(raw, sort_keys=False))
    tmp.replace(path)

    # Update the live Config object so subsequent restarts read the
    # right value, and re-wire the running BackupService's uploader.
    config = state.get("config")
    svc = state.get("backup_service")
    if config is not None and config.backup is not None:
        config.backup.cloud_upload = enabled
    if svc is not None:
        svc.cfg.cloud_upload = enabled
        if enabled and config is not None and config.cloud is not None and config.cloud.bearer_token:
            from ..backup.cloud_uploader import make_uploader
            svc.cloud_uploader = make_uploader(
                config.cloud.endpoint,
                config.cloud.bearer_token,
                svc.cfg.cloud_keep_count,
            )
        else:
            svc.cloud_uploader = None

    return {"ok": True, "cloud_upload": enabled}


@get("/api/system/discovery", status_code=200)
async def discovery_state(state: State) -> dict[str, Any]:
    """Read whether anonymous discovery telemetry (#129) is enabled.
    Lives next to backup_cloud_toggle in the file because it follows
    the exact same config-mutate-and-persist pattern; the surfaced
    UI lives on the Settings page in the same area as backups."""
    config = state.get("config")
    enabled = False
    if config is not None and getattr(config, "discovery", None) is not None:
        enabled = bool(config.discovery.enabled)
    paired = bool(
        config is not None
        and getattr(config, "cloud", None) is not None
        and config.cloud.bearer_token
        and config.cloud.endpoint
    )
    return {"enabled": enabled, "paired": paired}


@post("/api/system/discovery/toggle", status_code=200)
async def discovery_toggle(request: Request, state: State) -> dict[str, Any]:
    """Flip `discovery.enabled` in the running config and persist.
    Body: `{enabled: bool}`.

    No cloud preflight, the appliance just won't push when disabled,
    and the cloud endpoint is the same bearer-token surface anyone
    else hits. The user-facing setting is purely local consent.
    """
    import shutil as _shutil
    import yaml as _yaml
    body = await request.json()
    enabled = bool(body.get("enabled")) if isinstance(body, dict) else False

    config_path = state.get("config_path")
    if not config_path:
        raise HTTPException(status_code=500, detail="config_path unset")
    path = Path(config_path)

    raw = _yaml.safe_load(path.read_text()) or {}
    block = raw.get("discovery") or {}
    block["enabled"] = enabled
    raw["discovery"] = block
    bak = path.with_suffix(path.suffix + ".bak")
    _shutil.copy2(path, bak)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_yaml.safe_dump(raw, sort_keys=False))
    tmp.replace(path)

    config = state.get("config")
    if config is not None:
        from ..config import DiscoveryCfg
        if config.discovery is None:
            config.discovery = DiscoveryCfg(enabled=enabled)
        else:
            config.discovery.enabled = enabled

    return {"ok": True, "enabled": enabled}


@get("/api/system/local-telemetry", status_code=200)
async def local_telemetry_state(state: State) -> dict[str, Any]:
    """Read whether the anonymous install-count beacon (#217) is enabled.
    Off by default. No pairing required — the beacon rides the daily
    update-check poll, which hits the anonymous release manifest."""
    config = state.get("config")
    enabled = False
    if config is not None and getattr(config, "local_telemetry", None) is not None:
        enabled = bool(config.local_telemetry.enabled)
    return {"enabled": enabled}


@post("/api/system/local-telemetry/toggle", status_code=200)
async def local_telemetry_toggle(request: Request, state: State) -> dict[str, Any]:
    """Flip `local_telemetry.enabled` in the running config and persist.
    Body: `{enabled: bool}`. The daily update check runs either way;
    this only controls whether an anonymous install_id rides along.
    Updates the live UpdateChecker so it takes effect without a restart."""
    import shutil as _shutil
    import yaml as _yaml
    body = await request.json()
    enabled = bool(body.get("enabled")) if isinstance(body, dict) else False

    config_path = state.get("config_path")
    if not config_path:
        raise HTTPException(status_code=500, detail="config_path unset")
    path = Path(config_path)

    raw = _yaml.safe_load(path.read_text()) or {}
    block = raw.get("local_telemetry") or {}
    block["enabled"] = enabled
    raw["local_telemetry"] = block
    bak = path.with_suffix(path.suffix + ".bak")
    _shutil.copy2(path, bak)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_yaml.safe_dump(raw, sort_keys=False))
    tmp.replace(path)

    config = state.get("config")
    if config is not None:
        from ..config import LocalTelemetryCfg
        if config.local_telemetry is None:
            config.local_telemetry = LocalTelemetryCfg(enabled=enabled)
        else:
            config.local_telemetry.enabled = enabled

    # Apply live so the running beacon honours the change immediately
    # (UpdateChecker reads self.telemetry_enabled at send time).
    scheduler = state.get("scheduler")
    updater = getattr(scheduler, "_updater", None) if scheduler is not None else None
    if updater is not None:
        updater.telemetry_enabled = enabled

    return {"ok": True, "enabled": enabled}


@post("/api/system/backup/cloud-restore/{backup_id:int}", status_code=202)
async def backup_cloud_restore(backup_id: int, state: State) -> dict[str, Any]:
    """Download a specific cloud backup and feed it through the local
    restore path. End-to-end "rebuild from cloud" flow without the
    operator having to download then re-upload by hand."""
    import httpx
    endpoint, token = _cloud_creds(state)
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.get(
            f"{endpoint}/api/internal/backups/{backup_id}/download",
            headers={"Authorization": f"Bearer {token}"},
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text[:300])
    body = r.content
    verdict = await asyncio.to_thread(_verify_archive, body)
    db_target = _resolve_db_path(state)
    config_target = Path(state.get("config_path") or "/etc/wattpost/config.yaml")
    summary = await asyncio.to_thread(
        _stage_and_swap, body, db_target, config_target,
    )
    scheduler = state.get("scheduler")

    async def _delayed_exec() -> None:
        await asyncio.sleep(0.4)
        try:
            if scheduler is not None:
                await scheduler.stop()
        except Exception:
            log.exception("scheduler stop failed before cloud-restore exec")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    asyncio.create_task(_delayed_exec())
    return {
        "ok": True,
        "message": "cloud restore staged, daemon restarting",
        "applied": summary,
        "verdict": verdict,
    }
