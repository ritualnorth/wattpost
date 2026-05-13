"""Pushover transport — POST one message to api.pushover.net.

Pushover is a paid one-time app ($5) popular with the Home Assistant /
maker crowd. Better mobile UX than ntfy for users who don't want a
second push app, and survives quiet hours when severity = alarm.

Two secrets the user must paste into config.yaml:
  app_token  — created at https://pushover.net/apps/build (one per
               appliance fleet is fine; they're free for personal use)
  user_key   — displayed at the top of https://pushover.net dashboard
               after signing up
"""
from __future__ import annotations

import logging

import httpx

from ..base import AlertEvent, NotificationTransport
from ..registry import register_notification_transport

log = logging.getLogger(__name__)

PUSHOVER_API = "https://api.pushover.net/1/messages.json"

# warn = 0 (default delivery, respects quiet hours).
# alarm = 1 (high priority — bypasses the user's quiet hours and
#            shows a louder notification on iOS/Android).
_SEVERITY_PRIORITY = {"warn": 0, "alarm": 1}


class PushoverTransport(NotificationTransport):
    def __init__(
        self,
        id: str,
        app_token: str,
        user_key: str,
        device: str | None = None,
    ) -> None:
        self.id = id
        self.app_token = app_token
        self.user_key = user_key
        self.device = device
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=10.0)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def send(self, event: AlertEvent) -> None:
        if self._client is None:
            return
        data = {
            "token": self.app_token,
            "user": self.user_key,
            "title": f"WattPost · {event.name}",
            "message": (
                f"{event.metric} = {event.value:.2f} "
                f"({event.op} {event.threshold:.2f})"
            ),
            "priority": _SEVERITY_PRIORITY.get(event.severity, 0),
            "timestamp": event.ts,
        }
        if self.device:
            # Restrict delivery to one of the user's registered devices
            # — useful when the same user_key fans out to phone + tablet
            # and they only want alerts on the phone.
            data["device"] = self.device
        try:
            r = await self._client.post(PUSHOVER_API, data=data)
            # Pushover returns 200 + {"status": 1} on success; anything
            # else carries a human-readable error in `errors`.
            if r.status_code != 200:
                log.warning(
                    "[%s] pushover HTTP %s: %s", self.id, r.status_code, r.text[:200]
                )
                return
            body = r.json()
            if body.get("status") != 1:
                log.warning(
                    "[%s] pushover rejected: %s",
                    self.id, body.get("errors") or body,
                )
        except Exception as e:
            log.warning("[%s] pushover send failed: %s", self.id, e)


@register_notification_transport("pushover")
def _factory(cfg: dict) -> PushoverTransport:
    for required in ("app_token", "user_key"):
        if not cfg.get(required):
            raise ValueError(
                f"pushover transport {cfg.get('id')!r}: missing {required}"
            )
    return PushoverTransport(
        id=cfg["id"],
        app_token=cfg["app_token"],
        user_key=cfg["user_key"],
        device=cfg.get("device"),
    )
