"""ntfy.sh transport — POST plain text to a topic.

Free public broker at ntfy.sh; users can also self-host. We default to
the public server because that's the path-of-least-resistance for a
non-technical RV owner: install the ntfy app, pick a topic name, paste
it into config.yaml, done.
"""
from __future__ import annotations

import logging

import httpx

from ..base import AlertEvent, NotificationTransport
from ..registry import register_notification_transport

log = logging.getLogger(__name__)


_SEVERITY_PRIORITY = {"warn": "default", "alarm": "high"}
_SEVERITY_TAGS = {"warn": "warning", "alarm": "rotating_light"}


class NtfyTransport(NotificationTransport):
    def __init__(self, id: str, topic: str, server: str = "https://ntfy.sh") -> None:
        self.id = id
        self.topic = topic
        self.server = server.rstrip("/")
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
        url = f"{self.server}/{self.topic}"
        body = (
            f"{event.metric} = {event.value:.2f} "
            f"({event.op} threshold {event.threshold:.2f})"
        )
        try:
            # ntfy headers are sent as HTTP headers and must be ASCII —
            # no fancy bullets. Body can be UTF-8 freely. Explicit
            # Content-Type on the body helps the iOS app's push handler
            # render the notification reliably.
            title = f"WattPost - {event.name}".encode("ascii", "replace").decode("ascii")
            await self._client.post(
                url,
                content=body.encode("utf-8"),
                headers={
                    "Content-Type": "text/plain; charset=utf-8",
                    "Title": title,
                    "Priority": _SEVERITY_PRIORITY.get(event.severity, "default"),
                    "Tags": _SEVERITY_TAGS.get(event.severity, "warning"),
                },
            )
        except Exception as e:
            log.warning("[%s] ntfy publish failed: %s", self.id, e)


@register_notification_transport("ntfy")
def _factory(cfg: dict) -> NtfyTransport:
    return NtfyTransport(
        id=cfg["id"],
        topic=cfg["topic"],
        server=cfg.get("server", "https://ntfy.sh"),
    )
