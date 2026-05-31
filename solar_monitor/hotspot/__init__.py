"""Appliance-as-WiFi-AP (Pillar 3).

When `hotspot:` is configured, the appliance can turn its WiFi radio
into an access point via NetworkManager so a phone/laptop reaches the
dashboard with no existing network, the field-setup / off-grid story.

Strictly opt-in and non-fatal:
  - `enabled: true` auto-brings-up the AP on boot; otherwise the
    profile exists but is manual-only (/api/hotspot/{on,off}).
  - Needs `nmcli` (NetworkManager, default on Pi OS Bookworm).
    Missing → log once + skip, never breaks the local UI or polling.

Phase 3b (deferred): auto-handoff + captive portal.
"""
from .service import HotspotService  # noqa: F401
