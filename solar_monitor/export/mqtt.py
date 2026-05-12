"""MQTT exporter.

Publishes every poll result as MQTT messages on a configurable topic tree:

    <prefix>/<device_label>/state    full device snapshot as JSON (retained)
    <prefix>/<device_label>/<metric> single value as JSON-encoded scalar (retained)
    <prefix>/_status                 daemon heartbeat / availability

LWT (Last Will & Testament) flips `_status` to `offline` if the daemon dies.

Decoupled from the scheduler via an internal queue: `export()` returns
instantly, and a background task drains the queue to the broker. If the
broker is unreachable, items are dropped (we're a real-time monitor, not a
durable bus). aiomqtt handles reconnection.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiomqtt

from .base import Exporter
from .registry import register_exporter

log = logging.getLogger(__name__)

_META_KEYS = {"_vendor", "_kind", "_label", "_slave_id", "_errors"}

# Internal keys preserved on the device snapshot payload but not used as
# per-metric topics (those would be redundant or noisy).
_TOPIC_SKIP = _META_KEYS


class MqttExporter(Exporter):
    def __init__(
        self,
        id: str,
        host: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        client_id: str = "solar-monitor",
        topic_prefix: str = "solar",
        qos: int = 0,
        retain: bool = True,
        publish_per_metric: bool = True,
    ) -> None:
        self.id = id
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.client_id = client_id
        self.topic_prefix = topic_prefix.rstrip("/")
        self.qos = qos
        self.retain = retain
        self.publish_per_metric = publish_per_metric

        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    # ---- Exporter API ----

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name=f"mqtt-exporter-{self.id}")
        log.info("[%s] mqtt exporter started → %s:%d (prefix=%s)",
                 self.id, self.host, self.port, self.topic_prefix)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=5)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def export(self, result: dict[str, Any]) -> None:
        # Drop if queue is full — we're a live monitor, not a buffered log.
        try:
            self._queue.put_nowait(result)
        except asyncio.QueueFull:
            log.warning("[%s] mqtt queue full; dropping poll result", self.id)

    # ---- internals ----

    async def _run(self) -> None:
        """Connect with auto-reconnect; drain queue."""
        # aiomqtt context handles connect/disconnect + per-instance state.
        # We loop on the outer level so a failed connection retries.
        backoff = 1.0
        while not self._stop.is_set():
            try:
                will = aiomqtt.Will(
                    topic=f"{self.topic_prefix}/_status",
                    payload="offline",
                    qos=self.qos,
                    retain=True,
                )
                async with aiomqtt.Client(
                    hostname=self.host,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    identifier=self.client_id,
                    will=will,
                ) as client:
                    log.info("[%s] connected to %s:%d", self.id, self.host, self.port)
                    backoff = 1.0
                    await client.publish(
                        f"{self.topic_prefix}/_status",
                        payload="online",
                        qos=self.qos,
                        retain=True,
                    )
                    await self._drain(client)
                # On graceful loop exit (stop), publish offline and bail.
                async with aiomqtt.Client(
                    hostname=self.host,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    identifier=f"{self.client_id}-shutdown",
                ) as client:
                    await client.publish(
                        f"{self.topic_prefix}/_status",
                        payload="offline",
                        qos=self.qos,
                        retain=True,
                    )
                return
            except aiomqtt.MqttError as e:
                log.warning("[%s] mqtt connection error: %s; retrying in %.1fs",
                            self.id, e, backoff)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                    return  # stop requested mid-backoff
                except asyncio.TimeoutError:
                    backoff = min(backoff * 2, 60.0)
            except Exception:
                log.exception("[%s] unexpected mqtt error", self.id)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=5)
                    return
                except asyncio.TimeoutError:
                    pass

    async def _drain(self, client: aiomqtt.Client) -> None:
        """Publish every queued result until stop or connection error."""
        while not self._stop.is_set():
            # Wake for either a queue item or the stop signal.
            item_task = asyncio.create_task(self._queue.get())
            stop_task = asyncio.create_task(self._stop.wait())
            done, pending = await asyncio.wait(
                {item_task, stop_task}, return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            if stop_task in done:
                # Even on shutdown, flush anything already queued.
                while not self._queue.empty():
                    await self._publish_result(client, self._queue.get_nowait())
                return
            await self._publish_result(client, item_task.result())

    async def _publish_result(self, client: aiomqtt.Client, result: dict[str, Any]) -> None:
        timestamp = result.get("timestamp")
        for label, data in (result.get("devices") or {}).items():
            if not data:
                continue

            # Full device snapshot — keep `_vendor`/`_kind` for downstream routing.
            snapshot = dict(data)
            snapshot["_ts"] = timestamp
            await client.publish(
                f"{self.topic_prefix}/{label}/state",
                payload=json.dumps(snapshot, default=str),
                qos=self.qos,
                retain=self.retain,
            )

            if not self.publish_per_metric:
                continue
            for k, v in data.items():
                if k in _TOPIC_SKIP:
                    continue
                # Only publish JSON-serialisable scalars per topic.
                if isinstance(v, bool) or v is None:
                    continue
                if isinstance(v, (int, float, str)):
                    await client.publish(
                        f"{self.topic_prefix}/{label}/{k}",
                        payload=json.dumps(v, default=str),
                        qos=self.qos,
                        retain=self.retain,
                    )


@register_exporter("mqtt")
def _factory(cfg: dict) -> MqttExporter:
    """Build an MqttExporter from a YAML config dict.

    Expected fields:
      id: stable id
      type: "mqtt"
      host: broker hostname / ip
      port: broker port (default 1883)
      username: optional
      password: optional
      client_id: default "solar-monitor"
      topic_prefix: default "solar"
      qos: default 0
      retain: default true
      publish_per_metric: default true
    """
    return MqttExporter(
        id=cfg["id"],
        host=cfg["host"],
        port=int(cfg.get("port", 1883)),
        username=cfg.get("username"),
        password=cfg.get("password"),
        client_id=cfg.get("client_id", "solar-monitor"),
        topic_prefix=cfg.get("topic_prefix", "solar"),
        qos=int(cfg.get("qos", 0)),
        retain=bool(cfg.get("retain", True)),
        publish_per_metric=bool(cfg.get("publish_per_metric", True)),
    )
