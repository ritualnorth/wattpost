"""Daily poll of the release manifest.

The cloud serves a JSON manifest at
`<endpoint>/api/releases/latest` — `{version, released_at,
release_url}`. The appliance hits it every ~24h, compares to its
own `solar_monitor.__version__`, and stashes the result so the
API + Settings UI can surface "Update available" when newer.

Strictly check-only at this layer — applying an update is a
separate (not-yet-built) path. Failure to fetch is logged at WARNING
and ignored; the next attempt comes around 24h later.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .. import __version__ as APPLIANCE_VERSION

log = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 24 * 3600   # 1 day
DEFAULT_MANIFEST_URL   = "https://wattpost.cloud/api/releases/latest"
DEFAULT_BEACON_URL     = "https://wattpost.cloud/api/local_installs/beacon"
DEFAULT_CHANGELOG_URL  = "https://releases.wattpost.io/CHANGELOG.md"
USER_AGENT             = f"wattpost-appliance/{APPLIANCE_VERSION}"


@dataclass
class UpdateState:
    """What the UI / API read out. Updated in-place by the checker."""
    current_version:    str       = APPLIANCE_VERSION
    latest_version:     str | None = None
    latest_released_at: str | None = None
    release_url:        str | None = None
    last_checked_at:    int | None = None     # unix seconds
    last_error:         str | None = None     # last fetch failure if any
    # Cached upstream CHANGELOG.md text — refreshed each successful
    # manifest poll. Lets the dashboard show "what's in 0.0.3" while
    # the appliance is still on 0.0.2 (bundled docs only know the
    # versions <= the running release). Not included in as_dict() —
    # served separately via /api/releases/changelog because it can be
    # several KB and the update-state endpoint is polled frequently.
    release_notes_md:   str | None = None

    @property
    def has_update(self) -> bool:
        if not self.latest_version or not self.current_version:
            return False
        # Lexicographic compare works for our 0.x.y → 0.x.y+1 cadence;
        # when we hit double-digit minor/patch we'll need semver.
        return self.latest_version != self.current_version and \
               _semver_tuple(self.latest_version) > _semver_tuple(self.current_version)

    def as_dict(self) -> dict[str, Any]:
        return {
            "current_version":    self.current_version,
            "latest_version":     self.latest_version,
            "latest_released_at": self.latest_released_at,
            "release_url":        self.release_url,
            "last_checked_at":    self.last_checked_at,
            "last_error":         self.last_error,
            "has_update":         self.has_update,
        }


def _semver_tuple(v: str) -> tuple:
    """Cheap version comparison — splits on dots, ints where possible,
    strings as fallback. Handles "0.0.2" vs "0.0.10" correctly which
    is the main reason we don't just `<`-compare the raw string."""
    parts = []
    for p in v.lstrip("v").split("."):
        try:    parts.append((0, int(p)))
        except ValueError:
            parts.append((1, p))    # any non-int sorts AFTER ints
    return tuple(parts)


class UpdateChecker:
    def __init__(
        self,
        manifest_url: str | None = None,
        changelog_url: str | None = None,
        beacon_url: str | None = None,
        install_id: str | None = None,
        telemetry_enabled: bool = True,
    ) -> None:
        self.manifest_url      = manifest_url  or DEFAULT_MANIFEST_URL
        self.changelog_url     = changelog_url or DEFAULT_CHANGELOG_URL
        self.beacon_url        = beacon_url    or DEFAULT_BEACON_URL
        self.install_id        = install_id
        self.telemetry_enabled = telemetry_enabled
        self.state = UpdateState()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="update-checker")
        log.info("update checker started (manifest=%s, every %dh)",
                 self.manifest_url, CHECK_INTERVAL_SECONDS // 3600)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def check_once(self) -> None:
        """Fetch the manifest + cache the upstream CHANGELOG. Exposed
        for the "check now" button in Settings UI so the user doesn't
        have to wait 24h."""
        try:
            async with httpx.AsyncClient(
                timeout=15.0, follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                r = await client.get(self.manifest_url)
                r.raise_for_status()
                body = r.json()
                self.state.latest_version     = body.get("version")
                self.state.latest_released_at = body.get("released_at")
                self.state.release_url        = body.get("release_url")
                self.state.last_checked_at    = int(time.time())
                self.state.last_error         = None

                # Refresh cached release notes so the dashboard can
                # preview a not-yet-installed version's changelog
                # entry. Independent failure path — the manifest poll
                # is the source of truth for has_update, the
                # changelog is best-effort decoration.
                try:
                    cl = await client.get(self.changelog_url)
                    cl.raise_for_status()
                    self.state.release_notes_md = cl.text
                except Exception as e:
                    log.info("changelog fetch failed (keeping prior "
                             "cache + local fallback): %s", e)

                # Fire the anonymous install beacon (#217) — separate
                # URL so the Cloudflare-cached /api/releases/latest
                # endpoint stays fast. install_id is the only ID we
                # send; suppressed entirely when local_telemetry is
                # opted out. Failure is non-fatal — we don't want a
                # missing beacon to mask a successful version check.
                if self.telemetry_enabled and self.install_id:
                    try:
                        import os as _os
                        method = "docker" if _os.environ.get("WATTPOST_DEPLOYMENT") == "docker" else "pi"
                        await client.post(
                            self.beacon_url,
                            json={
                                "install_id":     self.install_id,
                                "version":        APPLIANCE_VERSION,
                                "install_method": method,
                            },
                        )
                    except Exception as e:
                        log.info("local-install beacon failed (non-fatal): %s", e)
        except Exception as e:
            self.state.last_checked_at = int(time.time())
            self.state.last_error      = str(e)[:200]
            log.warning("update check failed: %s", e)

    async def _loop(self) -> None:
        # First check immediately so a fresh daemon doesn't wait 24h
        # before knowing what version it should be.
        await self.check_once()
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=CHECK_INTERVAL_SECONDS,
                )
                return
            except asyncio.TimeoutError:
                pass
            await self.check_once()
