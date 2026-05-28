"""Appliance-side hash-chained signed-audit log (Phase 8B, #310).

Each entry binds:
  occurred_at , UTC timestamp (microsecond precision)
  event_type  , short stable string ("login_failed", "password_changed", …)
  payload     , JSON-serialisable dict of event-specific fields
  prev_hash   , sha256 of the previous entry's `signed_repr`
  issuer_kid  , the appliance keypair fingerprint at signing time
  signed_repr , canonical JSON of all the above (exact bytes signed)
  signature   , ed25519 over signed_repr, by the appliance keypair

Canonical JSON matches the cloud's `signed_audit.py:_canonical_repr`
byte-for-byte. Keep the two in lockstep, drift = chain breaks at
ingest time. The fields, sort order, separators, and timestamp
format are all load-bearing.

Threat model:

  * Disk-only attacker who can read sealed keypair can also rewrite
    rows + re-sign, Phase 10 hardware key closes that. Until then,
    the chain's value is "tamper-EVIDENT" (cloud detects gaps /
    rewrites at sync time) rather than "tamper-PROOF".
  * Cloud compromise can't rewrite appliance entries that have
    already been ingested, cloud verifies the appliance signature
    on ingest and stores it as-is.

Not in this module:
  * Heartbeat sync wiring (lives in cloud/service.py for the
    pending-list shipping, cloud/api/heartbeat.py for the ingest).
  * Call-sites that actually log security events. Those land per-
    feature and reference write_event() here.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from .auth import keypair as _keypair

log = logging.getLogger(__name__)


_ISSUER = "appliance"


def _canonical_repr(
    *,
    issuer:        str,
    issuer_kid:    str,
    event_type:    str,
    event_payload: dict[str, Any],
    prev_hash:     str | None,
    occurred_at:   datetime,
) -> str:
    """EXACT byte string for sign + verify. Mirror of cloud
    signed_audit._canonical_repr, keep them in lockstep."""
    return json.dumps(
        {
            "issuer":        issuer,
            "issuer_kid":    issuer_kid,
            "event_type":    event_type,
            "event_payload": event_payload,
            "prev_hash":     prev_hash,
            "occurred_at":   occurred_at.replace(tzinfo=timezone.utc).isoformat(),
        },
        sort_keys=True, separators=(",", ":"),
        ensure_ascii=False,
    )


def _hash(signed_repr: str) -> str:
    return hashlib.sha256(signed_repr.encode("utf-8")).hexdigest()


async def _tail_signed_repr(db: aiosqlite.Connection) -> str | None:
    async with db.execute(
        "SELECT signed_repr FROM signed_audit_log ORDER BY id DESC LIMIT 1",
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else None


async def write_event(
    db: aiosqlite.Connection,
    *,
    event_type:    str,
    payload:       dict[str, Any] | None = None,
    occurred_at:   datetime | None = None,
) -> int:
    """Append a new appliance-signed audit entry. Returns the row id.

    Caller commits the transaction. Safe to call from anywhere with
    access to the store; works the same in setup-wizard / API /
    background paths."""
    kp = _keypair.load_or_create()
    payload = payload or {}
    occurred_at = occurred_at or datetime.now(timezone.utc)

    tail = await _tail_signed_repr(db)
    prev_hash = _hash(tail) if tail else None

    signed_repr = _canonical_repr(
        issuer=_ISSUER,
        issuer_kid=kp.fingerprint,
        event_type=event_type,
        event_payload=payload,
        prev_hash=prev_hash,
        occurred_at=occurred_at,
    )
    sig = kp.sign(signed_repr.encode("utf-8"))
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")

    cur = await db.execute(
        """INSERT INTO signed_audit_log
           (occurred_at, event_type, event_payload, prev_hash,
            signed_repr, signature_b64, issuer_kid)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            occurred_at.replace(tzinfo=timezone.utc).isoformat(),
            event_type,
            json.dumps(payload, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False),
            prev_hash,
            signed_repr,
            sig_b64,
            kp.fingerprint,
        ),
    )
    return cur.lastrowid


async def fetch_pending(
    db: aiosqlite.Connection, *, limit: int = 50,
) -> list[dict[str, Any]]:
    """Pull up to `limit` not-yet-uploaded rows for heartbeat
    inclusion. Returns the cloud-shaped dicts (issuer + issuer_kid +
    event_type + event_payload + prev_hash + occurred_at + signed_repr
    + signature_b64) so the cloud ingest path can persist them
    directly without re-deriving the canonical form."""
    async with db.execute(
        """SELECT id, occurred_at, event_type, event_payload, prev_hash,
                  signed_repr, signature_b64, issuer_kid
           FROM signed_audit_log
           WHERE uploaded_at IS NULL
           ORDER BY id ASC
           LIMIT ?""",
        (limit,),
    ) as cur:
        rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            payload = json.loads(r[3])
        except Exception:
            payload = {}
        out.append({
            "id":            r[0],
            "occurred_at":   r[1],
            "event_type":    r[2],
            "event_payload": payload,
            "prev_hash":     r[4],
            "signed_repr":   r[5],
            "signature_b64": r[6],
            "issuer_kid":    r[7],
            "issuer":        _ISSUER,
        })
    return out


async def mark_uploaded(
    db: aiosqlite.Connection, *, ids: list[int],
) -> None:
    """Flip `uploaded_at` to NOW for the given row ids. Called
    after the cloud has ACK'd ingestion in the heartbeat reply."""
    if not ids:
        return
    now = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join("?" for _ in ids)
    await db.execute(
        f"UPDATE signed_audit_log SET uploaded_at = ? "
        f"WHERE id IN ({placeholders}) AND uploaded_at IS NULL",
        [now, *ids],
    )
