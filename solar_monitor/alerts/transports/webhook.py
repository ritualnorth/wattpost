"""Generic HTTP webhook transport — POST/PUT a JSON payload to any URL.

Escape hatch for users plugging into their own systems (Zapier, n8n,
Home Assistant webhook, IFTTT, a homemade Lambda…). Payload shape is
intentionally flat and stable so consumers can match against it without
schema chasing.
"""
from __future__ import annotations

import logging

import httpx

from ..base import AlertEvent, NotificationTransport
from ..registry import register_notification_transport

log = logging.getLogger(__name__)


class WebhookTransport(NotificationTransport):
    def __init__(
        self,
        id: str,
        url: str,
        method: str = "POST",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.id = id
        self.url = url
        self.method = method.upper()
        self.headers = headers or {}
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
        payload = {
            "rule_id":   event.rule_id,
            "name":      event.name,
            "severity":  event.severity,
            "metric":    event.metric,
            "op":        event.op,
            "value":     event.value,
            "threshold": event.threshold,
            "ts":        event.ts,
        }
        try:
            await self._client.request(
                self.method, self.url, json=payload, headers=self.headers,
            )
        except Exception as e:
            log.warning("[%s] webhook %s %s failed: %s",
                        self.id, self.method, self.url, e)


@register_notification_transport("webhook")
def _factory(cfg: dict) -> WebhookTransport:
    return WebhookTransport(
        id=cfg["id"],
        url=cfg["url"],
        method=cfg.get("method", "POST"),
        headers=cfg.get("headers"),
    )
