"""Push anonymised discovery observations to the cloud.

Best-effort: failures here never affect the user-facing scan flow.
The cloud endpoint is bearer-authed with the appliance's existing
token (the same one the heartbeat uses), pairing is the only
gate, no separate credential to manage.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

import httpx


log = logging.getLogger(__name__)


def build_ble_fingerprint(rec: dict[str, Any]) -> dict[str, Any] | None:
    """Reduce a BLE scan result to its anonymised fingerprint, or
    return None if the device is recognised by an existing driver
    (we only ship the unknowns to the discovery pipeline, known
    devices are noise).

    Input is the per-device dict that `ble_scan` builds, with keys
    `address`, `name`, `rssi`, optional `vendor`, `protocol`,
    `manufacturer_data` (raw map, if available).
    """
    # Don't ship if we already recognised the vendor, those are
    # already covered by an existing driver and add no new info to
    # the unknown-device pipeline.
    if rec.get("vendor"):
        return None

    addr = (rec.get("address") or "").upper()
    if not addr or addr.count(":") < 2:
        return None
    # OUI only, never the full MAC. Vendors register OUIs publicly,
    # so this leaks at most "you have a Texas Instruments BLE chip".
    oui = ":".join(addr.split(":")[:3])

    name = rec.get("name") or None
    if name and len(name) > 40:
        name = name[:40]

    fp: dict[str, Any] = {
        "transport": "ble",
        "oui":   oui,
        "name":  name,
    }

    # Optional richer hints when the caller supplied them. The
    # ble_scan handler doesn't currently include raw manufacturer
    # data in its response shape, we pass it forward when the
    # caller does.
    mfr_first = rec.get("manufacturer_first_id")
    mfr_prefix = rec.get("manufacturer_prefix_hex")
    if mfr_first is not None:
        fp["mfr_id"] = mfr_first
    if mfr_prefix:
        fp["mfr_prefix_hex"] = mfr_prefix
    svc_uuids = rec.get("service_uuids")
    if svc_uuids:
        # Cap to keep payloads tiny, 8 UUIDs is more than any sane
        # device advertises.
        fp["service_uuids"] = list(svc_uuids)[:8]
    return fp


async def push_observations(
    endpoint: str,
    bearer_token: str,
    fingerprints: Iterable[dict[str, Any]],
    *,
    timeout: float = 8.0,
) -> bool:
    """POST a batch of fingerprints to the cloud's discovery endpoint.
    Returns True iff the cloud accepted the batch."""
    body = list(fingerprints)
    if not body:
        return True
    url = f"{endpoint.rstrip('/')}/api/internal/discovery"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type":  "application/json",
    }
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True,
        ) as client:
            r = await client.post(url, headers=headers, json={"items": body})
        if 200 <= r.status_code < 300:
            log.info("discovery: pushed %d fingerprints to cloud", len(body))
            return True
        log.warning(
            "discovery: cloud rejected push (HTTP %s): %s",
            r.status_code, r.text[:160],
        )
        return False
    except Exception as e:
        log.warning("discovery: push failed: %s", e)
        return False
