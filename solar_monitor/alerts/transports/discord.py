"""Discord webhook transport, POSTs a rich embed to a channel webhook.

Users create a channel webhook in Discord, paste the URL into config.yaml
under `notification_transports[].url`. No bot setup required.
"""
from __future__ import annotations

import logging
import time

import httpx

from ..base import AlertEvent, NotificationTransport
from ..registry import register_notification_transport

log = logging.getLogger(__name__)


_SEVERITY_COLOR = {
    "warn":  0xD29922,  # amber
    "alarm": 0xF85149,  # red
}


class DiscordWebhookTransport(NotificationTransport):
    def __init__(self, id: str, url: str, username: str = "WattPost") -> None:
        self.id = id
        self.url = url
        self.username = username
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
        from ..base import humanise_metric, humanise_op, fmt_value
        metric_label = humanise_metric(event.metric)
        embed = {
            "title": event.name,
            "description": (
                f"**{metric_label}** is "
                f"**{fmt_value(event.metric, event.value)}**  "
                f"(threshold {humanise_op(event.op)} "
                f"{fmt_value(event.metric, event.threshold)})"
            ),
            "color": _SEVERITY_COLOR.get(event.severity, 0xD29922),
            "footer": {"text": f"WattPost · {event.severity}"},
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(event.ts)
            ),
        }
        try:
            await self._client.post(
                self.url,
                json={"username": self.username, "embeds": [embed]},
            )
        except Exception as e:
            log.warning("[%s] discord webhook failed: %s", self.id, e)


@register_notification_transport("discord_webhook")
def _factory(cfg: dict) -> DiscordWebhookTransport:
    return DiscordWebhookTransport(
        id=cfg["id"],
        url=cfg["url"],
        username=cfg.get("username", "WattPost"),
    )
