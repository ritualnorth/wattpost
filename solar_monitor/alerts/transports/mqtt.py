"""MQTT-publish alert transport, fires alert events onto a topic so
Home Assistant / Node-RED / Telegraf / a custom subscriber on the
local network picks them up. Critical for fully off-grid setups where
ntfy / Discord / SMTP all need internet.

Topic scheme (with the default prefix "wattpost/alerts"):
    wattpost/alerts                       all events, retained off
    wattpost/alerts/by-severity/<sev>     warn | alarm
    wattpost/alerts/by-rule/<rule_id>     per-rule channel

Payload is the same JSON the webhook transport sends so consumers can
write a single parser for both.
"""
from __future__ import annotations

import asyncio
import json
import logging

import aiomqtt

from ..base import AlertEvent, NotificationTransport
from ..registry import register_notification_transport

log = logging.getLogger(__name__)


class MqttAlertTransport(NotificationTransport):
    def __init__(
        self,
        id: str,
        host: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        client_id: str | None = None,
        topic_prefix: str = "wattpost/alerts",
        qos: int = 1,
        retain: bool = False,
    ) -> None:
        self.id = id
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.client_id = client_id or f"wattpost-alerts-{id}"
        self.topic_prefix = topic_prefix.rstrip("/")
        self.qos = qos
        self.retain = retain
        self._lock = asyncio.Lock()

    async def send(self, event: AlertEvent) -> None:
        # New connection per send. Alerts are rare; pooling adds
        # complexity (background-task lifecycle, reconnect logic) for no
        # meaningful latency win.
        payload_obj = {
            "rule_id":   event.rule_id,
            "name":      event.name,
            "severity":  event.severity,
            "metric":    event.metric,
            "op":        event.op,
            "value":     event.value,
            "threshold": event.threshold,
            "ts":        event.ts,
        }
        payload = json.dumps(payload_obj).encode("utf-8")
        topics = [
            self.topic_prefix,
            f"{self.topic_prefix}/by-severity/{event.severity}",
            f"{self.topic_prefix}/by-rule/{event.rule_id}",
        ]
        try:
            async with aiomqtt.Client(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                identifier=self.client_id,
            ) as client:
                for topic in topics:
                    await client.publish(
                        topic, payload, qos=self.qos, retain=self.retain,
                    )
        except Exception as e:
            log.warning("[%s] mqtt alert publish failed: %s", self.id, e)


@register_notification_transport("mqtt")
def _factory(cfg: dict) -> MqttAlertTransport:
    return MqttAlertTransport(
        id=cfg["id"],
        host=cfg["host"],
        port=int(cfg.get("port", 1883)),
        username=cfg.get("username"),
        password=cfg.get("password"),
        client_id=cfg.get("client_id"),
        topic_prefix=cfg.get("topic_prefix", "wattpost/alerts"),
        qos=int(cfg.get("qos", 1)),
        retain=bool(cfg.get("retain", False)),
    )
