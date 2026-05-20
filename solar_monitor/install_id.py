"""Anonymous per-install identifier.

The appliance generates a single random UUID at first boot and
persists it. It rides along on the daily update-check beacon
(see solar_monitor/update/checker.py) so the cloud can count
distinct unpaired installs + show version drift across the
fleet without anybody having to pair into the SaaS tier.

What this identifier is:
  * A random UUID v4 — no derivation from hardware, no MAC,
    no email, no IP.
  * Persistent across daemon restarts (saved to disk).
  * Reset on `wattpost-config → Reset to defaults` or by
    deleting the file by hand.

What it is NOT:
  * Linked to a customer record (paired installs use the
    bearer token instead; this beacon is for the un-paired
    population only).
  * Privacy-affecting beyond what the update poll already
    leaks (the appliance has to fetch /api/releases/latest
    over HTTPS no matter what — the install_id just lets the
    server deduplicate distinct callers instead of counting
    raw requests).

Customers can opt out via `local_telemetry: off` in
config.yaml — the update poll still fires (we need it for the
"Update available" badge) but the install_id query param is
suppressed.

Disk location: `/var/lib/wattpost/install-id` on the Pi /
host installs (writable, survives daemon upgrades); falls
back to a process-local UUID if the file can't be written
(Docker installs without a persistent volume, read-only FS,
etc.). The fallback means we still see the install for the
lifetime of the container, just not across restarts.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PATH = "/var/lib/wattpost/install-id"

# UUID v4 hex form with dashes — strict regex so a corrupted /
# tampered file doesn't poison the beacon with arbitrary input.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def load_or_create(path: str = DEFAULT_PATH) -> str:
    """Return the install_id for this appliance. Reads an existing
    file if present + well-formed; otherwise generates a new UUID
    and tries to persist it. On unwritable filesystems falls back
    to a process-local UUID and logs the reason."""
    p = Path(path)
    try:
        if p.is_file():
            existing = p.read_text(encoding="utf-8").strip().lower()
            if _UUID_RE.match(existing):
                return existing
            log.warning("install-id at %s is malformed (%r) — regenerating", path, existing[:64])
    except Exception as e:
        log.warning("install-id read failed at %s: %s — regenerating", path, e)

    new = str(uuid.uuid4())
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # Write then rename so concurrent reads never see a half-
        # written file. Permissions 0o644 — readable by anyone who
        # can already read /var/lib/wattpost/, which the daemon
        # user owns by default.
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(new + "\n", encoding="utf-8")
        os.replace(tmp, p)
    except Exception as e:
        # Container with no writable state volume, read-only root,
        # etc. — fall through with the in-memory ID. The cloud will
        # see the install for the lifetime of this process; that's
        # better than no data and the fallback path doesn't need
        # any new operator-visible error.
        log.info("install-id persist failed (%s) — using process-local UUID", e)
    return new
