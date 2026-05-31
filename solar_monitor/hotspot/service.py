"""Appliance-as-WiFi-AP via NetworkManager (Pillar 3, scaffold + manual).

Owns a single named `nmcli` connection profile in AP mode with NM's
shared IPv4 (built-in DHCP + NAT at 10.42.0.1/24). Bringing the AP up
is `nmcli connection up <name>`; tearing it down is `... down`. We
create/modify the profile from `HotspotCfg` on every activation so the
live SSID/band/channel/password always reflect config.

Lifecycle / contract (same spirit as TunnelService):
  - `start()` auto-brings-up the AP only when `cfg.enabled` AND
    nmcli is available. Otherwise it does nothing, the profile and
    manual /api/hotspot controls still work.
  - `stop()` deliberately leaves the AP UP. The AP lives in
    NetworkManager, not as a child of this process, so a daemon
    restart (or crash) must not knock a field user off the only
    network they can reach the box on. Turning it off is an explicit
    act (`deactivate()` / POST /api/hotspot/off).
  - Every nmcli failure is non-fatal: logged, surfaced via
    `status().last_error`, never raised into the scheduler.

Out of scope here (Phase 3b): auto-handoff (fall back to AP when no
known WiFi is in range) and a captive portal. This module only does
manual + boot-if-enabled bring-up.
"""
from __future__ import annotations

import asyncio
import logging
import shutil

from ..config import HotspotCfg

log = logging.getLogger(__name__)

# NM's shared-mode gateway. `ipv4.method shared` always hands the AP
# host 10.42.0.1/24 and runs a DHCP server for clients, so this is the
# address the dashboard answers on while the AP is up. Reported in
# status() so the UI can tell the user where to point their browser.
AP_GATEWAY = "10.42.0.1"

_NMCLI_TIMEOUT = 20.0  # seconds; nmcli connection up can take a beat


class HotspotService:
    def __init__(self, cfg: HotspotCfg) -> None:
        self.cfg = cfg
        self._last_error: str | None = None
        # What we last *intended*: True after a successful activate(),
        # False after deactivate(). status() still queries nmcli for
        # ground truth; this is only for logging/diagnostics.
        self._desired_up = False

    # ------------------------------------------------------------------
    # availability
    # ------------------------------------------------------------------
    @staticmethod
    def is_available(cfg: HotspotCfg) -> bool:
        """Can we drive an AP on this host at all? We require nmcli on
        PATH. The interface is checked at activation time (a missing
        radio surfaces as last_error rather than silently disabling
        manual control). Logs once when nmcli is absent."""
        if shutil.which("nmcli") is None:
            log.warning(
                "hotspot: nmcli not found on PATH, appliance-as-WiFi-AP "
                "unavailable. Install NetworkManager (default on Pi OS "
                "Bookworm) to use the '%s' hotspot.",
                cfg.ssid,
            )
            return False
        return True

    # ------------------------------------------------------------------
    # scheduler lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Boot hook. Only auto-brings-up the AP when the user opted in
        with `enabled: true`; manual control is always available."""
        if not self.is_available(self.cfg):
            return
        if not self.cfg.enabled:
            log.info("hotspot: configured, manual-only (enabled=false)")
            return
        log.info("hotspot: enabled, bringing up AP '%s'", self.cfg.ssid)
        await self.activate()

    async def stop(self) -> None:
        """Daemon shutdown. Intentionally a no-op for the AP itself, we
        leave NetworkManager holding the connection so a restart doesn't
        drop a user who reached us over the hotspot."""
        return

    # ------------------------------------------------------------------
    # manual control
    # ------------------------------------------------------------------
    async def activate(self) -> dict[str, str | bool]:
        """Create/refresh the AP profile from cfg, then bring it up.
        Returns {ok, error}. Never raises."""
        if not self.is_available(self.cfg):
            self._last_error = "nmcli not available"
            return {"ok": False, "error": self._last_error}

        pw = self.cfg.password or ""
        if pw and not (8 <= len(pw) <= 63):
            self._last_error = "password must be 8..63 chars (WPA2) or empty (open)"
            log.warning("hotspot: %s", self._last_error)
            return {"ok": False, "error": self._last_error}

        try:
            await self._ensure_profile()
            rc, _out, err = await self._nmcli(
                "connection", "up", self.cfg.connection_name
            )
            if rc != 0:
                self._last_error = err or f"nmcli up exited {rc}"
                log.warning("hotspot: bring-up failed: %s", self._last_error)
                return {"ok": False, "error": self._last_error}
        except Exception as e:  # non-fatal, surface and move on
            self._last_error = str(e)
            log.warning("hotspot: activate failed: %s", e)
            return {"ok": False, "error": self._last_error}

        self._desired_up = True
        self._last_error = None
        log.info(
            "hotspot: AP up — ssid=%s band=%s ch=%d iface=%s gw=%s",
            self.cfg.ssid, self.cfg.band, self.cfg.channel,
            self.cfg.interface, AP_GATEWAY,
        )
        return {"ok": True, "error": ""}

    async def deactivate(self) -> dict[str, str | bool]:
        """Bring the AP down. Idempotent: 'not active' is treated as
        success. Never raises."""
        if shutil.which("nmcli") is None:
            self._last_error = "nmcli not available"
            return {"ok": False, "error": self._last_error}
        try:
            rc, _out, err = await self._nmcli(
                "connection", "down", self.cfg.connection_name
            )
            # rc!=0 with 'not an active connection' just means it was
            # already down, which is the state the caller wanted.
            if rc != 0 and "not an active connection" not in (err or "").lower():
                self._last_error = err or f"nmcli down exited {rc}"
                log.warning("hotspot: bring-down failed: %s", self._last_error)
                return {"ok": False, "error": self._last_error}
        except Exception as e:
            self._last_error = str(e)
            log.warning("hotspot: deactivate failed: %s", e)
            return {"ok": False, "error": self._last_error}

        self._desired_up = False
        self._last_error = None
        log.info("hotspot: AP '%s' down", self.cfg.ssid)
        return {"ok": True, "error": ""}

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------
    async def status(self) -> dict:
        """Settings-panel view. Queries nmcli for ground-truth active
        state so it stays honest even if the AP was toggled outside us.
        Client count is best-effort (needs `iw`); None when unknown.

        Never raises: on a host without NetworkManager we report
        inactive + nmcli_available=false rather than blowing up the
        status endpoint."""
        nmcli_ok = shutil.which("nmcli") is not None
        active = await self._is_active() if nmcli_ok else False
        clients = await self._client_count() if active else 0
        return {
            "enabled":        self.cfg.enabled,
            "active":         active,
            "ssid":           self.cfg.ssid,
            "band":           self.cfg.band,
            "channel":        self.cfg.channel,
            "interface":      self.cfg.interface,
            "gateway":        AP_GATEWAY,
            "secured":        bool(self.cfg.password),
            "client_count":   clients,
            "last_error":     self._last_error,
            "nmcli_available": nmcli_ok,
        }

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    async def _ensure_profile(self) -> None:
        """Make the named connection match cfg. We delete any existing
        profile of the same name and re-add, so stale band/channel/psk
        from a previous config can't linger. Cheaper than diffing each
        nmcli property."""
        name = self.cfg.connection_name
        # Drop a prior profile (ignore 'unknown connection').
        await self._nmcli("connection", "delete", name)

        rc, _out, err = await self._nmcli(
            "connection", "add",
            "type", "wifi",
            "ifname", self.cfg.interface,
            "con-name", name,
            "autoconnect", "no",
            "ssid", self.cfg.ssid,
        )
        if rc != 0:
            raise RuntimeError(f"nmcli add: {err or rc}")

        mods = [
            "802-11-wireless.mode", "ap",
            "802-11-wireless.band", self.cfg.band,
            "802-11-wireless.channel", str(self.cfg.channel),
            "ipv4.method", "shared",
        ]
        if self.cfg.password:
            mods += [
                "wifi-sec.key-mgmt", "wpa-psk",
                "wifi-sec.psk", self.cfg.password,
            ]
        rc, _out, err = await self._nmcli("connection", "modify", name, *mods)
        if rc != 0:
            raise RuntimeError(f"nmcli modify: {err or rc}")

    async def _is_active(self) -> bool:
        rc, out, _err = await self._nmcli(
            "-t", "-f", "NAME", "connection", "show", "--active"
        )
        if rc != 0:
            return False
        names = {line.strip() for line in out.splitlines()}
        return self.cfg.connection_name in names

    async def _client_count(self) -> int | None:
        """Connected stations via `iw`. None if iw is absent."""
        if shutil.which("iw") is None:
            return None
        try:
            rc, out, _err = await self._run(
                "iw", "dev", self.cfg.interface, "station", "dump"
            )
            if rc != 0:
                return None
            return sum(1 for ln in out.splitlines() if ln.startswith("Station"))
        except Exception:
            return None

    async def _nmcli(self, *args: str) -> tuple[int, str, str]:
        return await self._run("nmcli", *args)

    @staticmethod
    async def _run(*cmd: str) -> tuple[int, str, str]:
        """Run a command, capture (rc, stdout, stderr). Times out so a
        wedged nmcli can't hang the event loop. A missing binary becomes
        a clean RuntimeError instead of a raw FileNotFoundError so every
        caller degrades the same way."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError) as e:
            raise RuntimeError(f"{cmd[0]} unavailable: {e}")
        try:
            out_b, err_b = await asyncio.wait_for(
                proc.communicate(), timeout=_NMCLI_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"{cmd[0]} timed out after {_NMCLI_TIMEOUT:.0f}s")
        return (
            proc.returncode if proc.returncode is not None else -1,
            out_b.decode(errors="replace"),
            err_b.decode(errors="replace").strip(),
        )
