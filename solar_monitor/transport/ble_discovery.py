"""Always-on BLE discovery registry.

Every broadcast advertisement the daemon's advert scanners hear is
recorded here, classified by vendor, so the setup UI can show
"devices we can see nearby" without the user first configuring a
transport for them. This is what lets a Victron / BMS / sensor be
*offered* the moment it's in range instead of forcing the
add-a-connection-then-scan dance.

Pure in-memory + best-effort: a missing/garbled advert is skipped,
never raised. The per-type advert scanners (ble_*_advertise) call
record() from their detection callback — they already receive every
advertisement, they just filtered to their own vendor before; now
they also drop a classified row in here.
"""
from __future__ import annotations

import threading
import time
from typing import Any

# Manufacturer IDs that identify a supported broadcast vendor. Kept in
# sync with api/setup.py's on-demand scan classifier so live discovery
# and the manual scan badge devices identically.
_VICTRON_MFR = 0x02E1
_NORDIC_MFR  = 0x0059            # Mopeka tank sensors (disambiguated by hw-id byte)
_MOPEKA_HW   = {0x03, 0x05, 0x06, 0x08, 0x09}
_GOVEE_MFR   = 0xEC88
_RUUVI_MFR   = 0x0499

# mac(upper) -> {vendor, kind, name, rssi, mfr_id, last_seen}
_seen: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _classify(name: str | None, mfr: dict[int, bytes]) -> tuple[str, str] | None:
    """(vendor, kind) for a recognised broadcast device, else None.

    kind is a coarse hint for the UI ("battery", "sensor", "modbus_bt");
    the precise device type for Victron is only known once decoded with
    the key, so we keep it generic here."""
    if _VICTRON_MFR in mfr:
        return ("victron", "victron_instant_readout")
    if _NORDIC_MFR in mfr:
        payload = mfr.get(_NORDIC_MFR) or b""
        if payload and payload[0] in _MOPEKA_HW:
            return ("mopeka", "tank")
    if _GOVEE_MFR in mfr:
        return ("govee", "sensor")
    if _RUUVI_MFR in mfr:
        return ("ruuvi", "sensor")
    n = (name or "").lower()
    if n.startswith("bt-th") or "renogy" in n:
        return ("renogy", "modbus_bt")
    return None


def record(device: Any, ad_data: Any) -> None:
    """Upsert one advertisement into the registry. Called from the advert
    scanners' detection callbacks; cheap + best-effort."""
    try:
        mfr = getattr(ad_data, "manufacturer_data", None) or {}
        name = getattr(ad_data, "local_name", None) or getattr(device, "name", None)
        cls = _classify(name, mfr)
        if cls is None:
            return
        vendor, kind = cls
        mac = str(getattr(device, "address", "") or "").upper()
        if not mac:
            return
        with _lock:
            _seen[mac] = {
                "mac":       mac,
                "vendor":    vendor,
                "kind":      kind,
                "name":      name or None,
                "rssi":      getattr(ad_data, "rssi", None),
                "last_seen": time.time(),
            }
    except Exception:
        # Discovery is a convenience; never let it perturb the scan path.
        pass


def snapshot(max_age_s: float = 300.0) -> list[dict[str, Any]]:
    """Devices seen within the last `max_age_s` seconds, freshest first,
    each with `age_s`. Stale rows are pruned as we read."""
    now = time.time()
    with _lock:
        out = []
        for mac, row in list(_seen.items()):
            age = now - row["last_seen"]
            if age > max_age_s:
                del _seen[mac]
                continue
            r = dict(row)
            r["age_s"] = round(age, 1)
            out.append(r)
    out.sort(key=lambda r: r["last_seen"], reverse=True)
    return out
