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

# Identifying metadata fields that live on the latest snapshot but aren't
# numeric sensors — used to enrich the HA `device` block, not published as
# sensors.
_HA_DEVICE_META = {"model", "serial", "firmware_version", "device_id"}


def _ha_sensor_meta(metric: str) -> dict[str, str]:
    """Map a snake_case metric name → Home Assistant discovery hints
    (device_class, unit, state_class). We're conservative: only emit a
    device_class when it lines up with HA's enum, otherwise just set the
    unit so the value still renders correctly."""
    m = metric.lower()
    # SoC is a special case — HA classifies as "battery" with % unit.
    if m == "soc_pct":
        return {"device_class": "battery", "unit_of_measurement": "%",
                "state_class": "measurement"}
    if m.endswith("_v"):
        return {"device_class": "voltage", "unit_of_measurement": "V",
                "state_class": "measurement"}
    if m.endswith("_a"):
        return {"device_class": "current", "unit_of_measurement": "A",
                "state_class": "measurement"}
    if m.endswith("_w"):
        return {"device_class": "power", "unit_of_measurement": "W",
                "state_class": "measurement"}
    if m.endswith("_c"):
        return {"device_class": "temperature", "unit_of_measurement": "°C",
                "state_class": "measurement"}
    if m.endswith("_pct"):
        # Non-SoC percentages: no HA device_class fits, keep unit only.
        return {"unit_of_measurement": "%", "state_class": "measurement"}
    if m.endswith("_ah"):
        # Amp-hours — no HA class; total_increasing for counters, but our
        # _ah metrics are instantaneous (remaining/capacity), so measurement.
        return {"unit_of_measurement": "Ah", "state_class": "measurement"}
    if m.endswith("_wh"):
        # Watt-hours: energy. Both "_today_wh" (daily reset) and
        # "_total_wh" (lifetime cumulative) use total_increasing —
        # HA detects resets automatically and adjusts. Required for
        # HA's built-in Energy dashboard to pick these up as Solar
        # production / Battery in/out sources.
        return {"device_class": "energy", "unit_of_measurement": "Wh",
                "state_class": "total_increasing"}
    if m.endswith("_hz"):
        return {"device_class": "frequency", "unit_of_measurement": "Hz",
                "state_class": "measurement"}
    if m.endswith("_count"):
        return {"state_class": "measurement"}
    return {}


_UNIT_SUFFIXES = {"v", "a", "w", "c", "ah", "wh", "pct", "hz"}


def _ha_name(metric: str) -> str:
    """Pretty entity name. HA prepends the device name when grouped, so
    we just describe the metric — strip a trailing unit suffix so we
    don't get "Voltage V" (HA already shows the unit separately)."""
    parts = metric.split("_")
    if parts and parts[-1].lower() in _UNIT_SUFFIXES:
        parts = parts[:-1]
    return " ".join(parts).title() if parts else metric.replace("_", " ").title()


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
        ha_discovery: bool = False,
        ha_discovery_prefix: str = "homeassistant",
        ha_node_id: str = "solar_monitor",
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
        # Home Assistant MQTT discovery: when on, publish a
        # `<ha_prefix>/sensor/<node>/<label>_<metric>/config` payload the
        # first time we see a given (device, metric) pair, pointing HA at
        # the state topic we're already publishing.
        self.ha_discovery = ha_discovery
        self.ha_discovery_prefix = ha_discovery_prefix.rstrip("/")
        self.ha_node_id = ha_node_id

        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        # (device_label, metric) pairs whose discovery config we've already
        # published this connection. Cleared on reconnect so transient
        # broker outages don't strand HA without configs.
        self._ha_published: set[tuple[str, str]] = set()

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
                    # Re-publish HA discovery configs on each reconnect — the
                    # broker may have lost retained messages, or HA may have
                    # been reconfigured since last time.
                    self._ha_published.clear()
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
                    if self.ha_discovery and isinstance(v, (int, float)):
                        # Identity strings (model/serial/firmware) get
                        # rolled into the HA `device` block, not a sensor.
                        if k in _HA_DEVICE_META:
                            continue
                        await self._publish_ha_discovery(client, label, k, data)

    async def _publish_ha_discovery(
        self, client: aiomqtt.Client, label: str, metric: str, data: dict[str, Any]
    ) -> None:
        """Publish a Home Assistant MQTT-discovery config for one sensor.
        Runs at most once per (device, metric) per connection."""
        key = (label, metric)
        if key in self._ha_published:
            return
        self._ha_published.add(key)

        unique_id = f"{self.ha_node_id}_{label}_{metric}"
        # Same topic id slot so HA can clean up cleanly via an empty payload.
        config_topic = (
            f"{self.ha_discovery_prefix}/sensor/"
            f"{self.ha_node_id}/{label}_{metric}/config"
        )
        state_topic = f"{self.topic_prefix}/{label}/{metric}"
        device_block: dict[str, Any] = {
            "identifiers": [f"{self.ha_node_id}_{label}"],
            "name": label.replace("_", " ").title(),
            "manufacturer": "WattPost",
        }
        # Surface model / serial / firmware from the latest snapshot if
        # the driver exposed them — HA will group entities by device.
        model = data.get("model")
        if isinstance(model, str) and model:
            device_block["model"] = model
        serial = data.get("serial")
        if isinstance(serial, str) and serial:
            device_block["serial_number"] = serial
        fw = data.get("firmware_version")
        if isinstance(fw, str) and fw:
            device_block["sw_version"] = fw

        config: dict[str, Any] = {
            "name": _ha_name(metric),
            "state_topic": state_topic,
            "unique_id": unique_id,
            "object_id": f"{label}_{metric}",
            "value_template": "{{ value }}",
            "availability_topic": f"{self.topic_prefix}/_status",
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": device_block,
            **_ha_sensor_meta(metric),
        }
        await client.publish(
            config_topic,
            payload=json.dumps(config),
            qos=self.qos,
            retain=True,  # discovery configs are always retained — HA convention
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
      ha_discovery: default false — when true, publishes Home Assistant
        MQTT-discovery configs so HA auto-creates one sensor per metric
        on top of the existing per-metric topics.
      ha_discovery_prefix: default "homeassistant" (HA convention)
      ha_node_id: default "solar_monitor" — used in the unique_id and the
        HA device identifier so multiple WattPost units on the same broker
        don't collide.
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
        ha_discovery=bool(cfg.get("ha_discovery", False)),
        ha_discovery_prefix=cfg.get("ha_discovery_prefix", "homeassistant"),
        ha_node_id=cfg.get("ha_node_id", "solar_monitor"),
    )
