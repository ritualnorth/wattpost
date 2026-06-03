"""Auto-handoff (Pillar 3b) + first-boot onboarding (#27): raise the hotspot
when the appliance has no network, drop it when a real LAN returns.

First-boot onboarding makes a *never-networked* box raise the setup AP on its
own (the headless van/off-grid case — no monitor, no router), independent of
the auto_handoff opt-in. It latches off once the box has seen a LAN (the
`.networked` marker), so a home box that briefly loses WiFi never gets a
surprise AP.

LOCAL-ONLY. The trigger is `hotspot.auto_handoff` in the appliance's own
config — it works with no cloud subscription, which matters because the
off-grid user who needs this most is the least likely to be paying for
the cloud.

Single-radio reality: most Pis have one WiFi radio, so while our AP holds
it the appliance cannot also be a WiFi client — `lan_kind()` can't see a
known network until we let go. So when we've raised the AP and there's no
*ethernet*, we periodically drop it for a grace window to let
NetworkManager try known networks; if none join, the AP comes back. With
ethernet (or a second WiFi adapter) the handoff is clean and immediate.

The policy lives in `tick()` (one evaluation + at most one AP action) so
it's unit-testable by driving ticks with stubbed probes. The loop is just
`tick()` on an interval.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .service import HotspotService

log = logging.getLogger(__name__)

POLL_SECONDS = 30        # how often tick() runs
GRACE_CHECKS = 2         # consecutive offline ticks before raising the AP
RETRY_AFTER_POLLS = 10   # while AP up w/o ethernet, ticks before a probe-drop

# Marker written the first time the appliance sees a real LAN. Its presence
# means "this box has been networked at least once", which ends first-boot
# onboarding: a never-networked box raises the setup AP; a box that has been
# online and later drops offline is the auto_handoff path (opt-in). Lives in
# the state dir on the Pi image (writable by the wattpost user); a failed
# write elsewhere just means onboarding can't latch — harmless on a host with
# no AP radio anyway.
NETWORKED_MARKER = Path("/var/lib/wattpost/.networked")


class AutoHandoffMonitor:
    def __init__(self, service: HotspotService) -> None:
        self.service = service
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        # Only the monitor's own AP raises are managed here; a manually
        # raised AP (or `enabled: true`) is never touched.
        self._raised_by_monitor = False
        self._miss_streak = 0          # consecutive offline ticks
        self._polls_since_raise = 0    # for the single-radio probe-drop
        # Cached "has this box ever been networked?" — gates onboarding.
        self._networked = NETWORKED_MARKER.exists()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def should_run(self) -> bool:
        """Run the loop only when auto-handoff could fire: the local flag
        is set and we can drive an AP. Flag-off → no loop, no periodic
        nmcli polling."""
        if not HotspotService.is_available(self.service.cfg):
            return False
        if self.service.cfg.enabled:
            return False  # AP is always-on; nothing to hand off
        return (self.service.cfg.auto_handoff
                or self._onboarding_active(self.service.cfg))

    async def start(self) -> None:
        if not self.should_run():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="hotspot-handoff")
        log.info("hotspot: auto-handoff monitor started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:
                log.exception("hotspot: auto-handoff tick failed (non-fatal)")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=POLL_SECONDS)
                break
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------
    # policy — one evaluation, at most one AP action. Returns a short
    # outcome string (handy for logs + tests).
    # ------------------------------------------------------------------
    async def tick(self) -> str:
        cfg = self.service.cfg
        if cfg.enabled:
            return "skip:enabled"
        if not HotspotService.is_available(cfg):
            return "skip:unavailable"

        onboarding = self._onboarding_active(cfg)
        eff = cfg.auto_handoff or onboarding
        ap_up = await self.service._is_active()

        if not eff:
            # Not opted in (the local flag was turned off).
            # Tidy up only an AP *we* raised; leave manual APs alone.
            if self._raised_by_monitor and ap_up:
                await self.service.deactivate()
                log.info("hotspot: auto-handoff disabled — dropped fallback AP")
            self._reset()
            return "off"

        lan = await self.service.lan_kind()
        if lan is not None:
            self._mark_networked()

        if ap_up and self._raised_by_monitor:
            if lan is not None:
                # Real LAN is back (ethernet, or a second-radio wifi
                # client) — hand control back to it.
                await self.service.deactivate()
                self._reset()
                log.info("hotspot: LAN restored (%s) — dropped fallback AP", lan)
                return f"drop:{lan}"
            # No ethernet and our AP holds the (single) radio, so we
            # can't see a known wifi network from here. Periodically let
            # go to give NetworkManager a chance to rejoin one — UNLESS
            # we're onboarding: a never-networked box has no known WiFi to
            # find, so don't blip the setup AP a user is mid-setup on.
            if onboarding:
                return "hold:onboarding"
            self._polls_since_raise += 1
            if self._polls_since_raise >= RETRY_AFTER_POLLS:
                await self.service.deactivate()
                self._raised_by_monitor = False
                self._polls_since_raise = 0
                self._miss_streak = 0
                log.info("hotspot: probe-drop — testing for a known network")
                return "probe-drop"
            return "hold"

        if ap_up and not self._raised_by_monitor:
            return "skip:manual"   # human (or boot) raised it; don't touch

        # AP is down. Raise it once we've been offline for the grace
        # window (debounces a transient blip during a wifi roam).
        if lan is None:
            self._miss_streak += 1
            if self._miss_streak >= GRACE_CHECKS:
                res = await self.service.activate()
                if res.get("ok"):
                    self._raised_by_monitor = True
                    self._polls_since_raise = 0
                    self._miss_streak = 0
                    log.info("hotspot: no LAN for %d checks — raised fallback AP",
                             GRACE_CHECKS)
                    return "raise"
                log.warning("hotspot: auto-handoff wanted the AP up but "
                            "activate failed: %s", res.get("error"))
                return "raise-failed"
            return "wait"

        # We have LAN — nothing to do.
        self._miss_streak = 0
        return "ok"

    def _reset(self) -> None:
        self._miss_streak = 0
        self._polls_since_raise = 0
        self._raised_by_monitor = False

    # ------------------------------------------------------------------
    # first-boot onboarding
    # ------------------------------------------------------------------
    def _onboarding_active(self, cfg) -> bool:
        """True while a never-networked box should auto-raise the setup AP.
        Independent of auto_handoff; latches off once we've seen a LAN."""
        return bool(cfg.onboarding) and not self._networked

    def _mark_networked(self) -> None:
        """Record that we've seen a real LAN — ends onboarding for good."""
        if self._networked:
            return
        self._networked = True
        try:
            NETWORKED_MARKER.parent.mkdir(parents=True, exist_ok=True)
            NETWORKED_MARKER.touch()
        except OSError as e:
            log.debug("hotspot: couldn't write networked marker: %s", e)
        log.info("hotspot: first LAN seen — first-boot onboarding complete")
