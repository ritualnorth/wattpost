"""Push a freshly-written local backup tarball to wattpost.cloud.

Activated when `backup.cloud_upload: true` AND the appliance has a
populated cloud pairing. The cloud-side endpoint enforces the
Pro/Installer tier gate, a Hobby-tier upload returns 402 and we
record that as a (recoverable) failure on the BackupService so the
Settings UI can surface "tier required, upgrade here".

No retries here, if the upload fails the local snapshot still
exists, and the next scheduled run will try again. We avoid silent
re-uploads of the same file by short-circuiting if the cloud
already has a row whose filename + size match this one.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from .. import __version__

log = logging.getLogger(__name__)

# Conservative request timeout. Upload of a 50 MB DB over a decent
# home connection takes a few seconds; allow 5 minutes so a slow van
# or campsite link still completes.
UPLOAD_TIMEOUT_S = 300


def make_uploader(
    endpoint: str, bearer_token: str, keep_count: int,
) -> Callable[[Path], Awaitable[bool]]:
    """Returns an async callable suitable for BackupService's
    `cloud_uploader` hook. Closes over the cloud endpoint + bearer
    token so the service itself doesn't need to know how cloud auth
    works."""
    async def _upload(local_path: Path) -> bool:
        url = endpoint.rstrip("/") + "/api/internal/backups/upload"
        data = await asyncio.to_thread(local_path.read_bytes)
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/gzip",
            "Content-Length": str(len(data)),
            "X-WP-Backup-Filename": local_path.name,
            "X-WP-Backup-Keep": str(keep_count),
            "X-WP-Backup-Version": __version__,
        }
        # #297-3, sign the archive with the appliance ed25519 keypair
        # so restore-time verification can refuse a swapped tarball.
        # Best-effort: if signing fails (no keypair, sealed-file
        # broken, etc.) we still upload, old appliances without
        # keypairs and pre-Identity-v2 installs need the upload path
        # to keep working. Restore-side warning surfaces the absence.
        try:
            from . import signing as _sig
            sig = await asyncio.to_thread(_sig.sign_archive, data)
            headers["X-WP-Backup-Signature"]  = sig.sig_b64
            headers["X-WP-Backup-Pubkey-Fp"]  = sig.pubkey_fp
            headers["X-WP-Backup-Sig-Alg"]    = sig.alg
        except Exception as e:
            log.warning(
                "cloud backup: signing skipped (%s), uploading "
                "unsigned; restore from this row will warn", e,
            )
        async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT_S) as client:
            r = await client.post(url, content=data, headers=headers)
        if r.status_code == 402:
            log.warning(
                "cloud backup: rejected as Hobby tier, upgrade for off-site backups",
            )
            return False
        if r.status_code >= 300:
            log.warning(
                "cloud backup: upload failed %d: %s",
                r.status_code, r.text[:200],
            )
            return False
        try:
            payload = r.json()
            log.info(
                "cloud backup: uploaded id=%s, pruned %d older row(s) cloud-side",
                payload.get("id"), payload.get("pruned", 0),
            )
        except Exception:
            pass
        return True

    return _upload
