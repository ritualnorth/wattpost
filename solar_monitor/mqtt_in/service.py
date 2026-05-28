"""MQTT-IN: subscribe to user's broker, register virtual devices (#256).

Design:

* One background asyncio task owns the MQTT connection (auto-reconnect
  with backoff).
* Two parallel ingest paths:
    - HA-discovery: subscribe to `<prefix>/+/+/config` and friends,
      parse each config payload, and from then on subscribe to its
      `state_topic`. Each HA entity becomes one (label, metric) pair
      on a virtual device named after the HA `device.name`.
    - Manual `topics:` list: subscribe to each, route payloads via
      scalar or JSON-path extraction.
* A registry of `label → {snapshot dict, last_at}` is exposed via
  `current_snapshots()`. The scheduler merges this into each poll
  result so MQTT-IN devices appear on `/api/devices` and `/api/today`
  exactly like a BLE-decoded device.
* Stale virtual devices (no message within `stale_after_seconds`)
  fall out of `current_snapshots()` automatically, same end-user
  behaviour as a silent BLE sensor.

Out of scope here, deferred:
* Outbound publishes, that's the MQTT-OUT exporter already.
* Complex Jinja templates, only `{{ value }}` and
  `{{ value_json.X }}` (with dotted paths) are handled. Anything
  else logs a one-shot warning and skips the entity.
* Shelly gen1 ad-hoc topic patterns, those land in a follow-up
  when we have a real Shelly to test against.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import aiomqtt
import msgspec

from ..config import MqttInCfg, MqttInTopicCfg

log = logging.getLogger(__name__)


# Match a Jinja template of the form `{{ value_json.foo.bar }}` (or
# `{{ value_json["foo"]["bar"] }}`, the bracket form HA also accepts).
# Anything fancier (filters, conditionals) is rejected.
_VALUE_JSON_DOT = re.compile(
    r"^\s*{{\s*value_json((?:\.[\w]+|\[['\"][\w]+['\"]\])+)\s*}}\s*$",
)
_VALUE_RAW = re.compile(r"^\s*{{\s*value\s*}}\s*$")


def _parse_value_template(tmpl: str | None) -> tuple[str, str] | None:
    """Decode a HA `value_template` into ('raw', '') or ('json', 'a.b.c').

    Returns None when the template is empty (no transformation needed)
    OR when it's something we don't handle (caller logs and skips)."""
    if not tmpl:
        return ("raw", "")
    if _VALUE_RAW.match(tmpl):
        return ("raw", "")
    m = _VALUE_JSON_DOT.match(tmpl)
    if m:
        # Normalise both `.foo` and `["foo"]` segments to a dotted path.
        parts = re.findall(r"\.(\w+)|\[['\"](\w+)['\"]\]", m.group(1))
        path = ".".join(a or b for a, b in parts)
        return ("json", path)
    return None  # unsupported template shape


def _extract_json_path(payload: bytes, dotted: str) -> Any:
    """Walk a dotted JSON path through the payload. Returns None on
    any parse / lookup failure rather than throwing, MQTT bus is noisy
    and we'd otherwise bury the broker log in tracebacks."""
    try:
        obj: Any = json.loads(payload.decode("utf-8", errors="replace"))
    except Exception:
        return None
    for seg in dotted.split("."):
        if not seg:
            continue
        if isinstance(obj, dict) and seg in obj:
            obj = obj[seg]
        else:
            return None
    return obj


def _coerce_value(raw: Any) -> Any:
    """Best-effort number coercion. MQTT delivers strings; the
    dashboard cares about numerics for sparklines. Strings that aren't
    numeric pass through (e.g. "ON"/"OFF" for switches)."""
    if raw is None or isinstance(raw, (int, float, bool)):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    try:
        if "." in s or "e" in s.lower():
            return float(s)
        return int(s)
    except ValueError:
        return s


class MqttInService:
    """Background MQTT subscriber that exposes a snapshot of latest
    state. See module docstring for the full design.

    Lifecycle: instantiate → start() (fire-and-forget background task)
    → current_snapshots() in the poll loop → stop() on daemon shutdown.
    """

    def __init__(self, cfg: MqttInCfg) -> None:
        self.cfg = cfg
        # Per-label: { snapshot_dict, last_at }. snapshot_dict already
        # has the `_vendor`/`_kind`/`_label` meta keys baked in.
        self._devices: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        # Last-known broker state. Surfaced via status() for the
        # Settings panel. Mirrors the wording the exporter uses.
        self._state: str = "stopped"
        self._last_error: str | None = None
        # HA-discovery: map a state_topic → (label, metric, value_kind, path)
        # so the receive loop can route a state message without
        # re-parsing the original config payload each time.
        self._ha_routes: dict[str, tuple[str, str, str, str]] = {}
        # Unsupported HA entities we've already warned about (config
        # topic). Stops the log from filling up with the same N lines
        # every reconnect cycle.
        self._ha_warned: set[str] = set()
        # Manual mapping routes, built from cfg.topics. Same shape
        # as _ha_routes for code symmetry.
        self._manual_routes: dict[str, tuple[str, str, str, str]] = {}
        for t in cfg.topics:
            self._add_manual_route(t)

    # ---- lifecycle ----

    async def start(self) -> None:
        if not self.cfg.enabled:
            log.info("mqtt_in: disabled in config; not starting")
            return
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="mqtt-in")
        log.info("mqtt_in started → %s:%d (ha_discovery=%s, manual=%d)",
                 self.cfg.host, self.cfg.port, self.cfg.ha_discovery,
                 len(self.cfg.topics))

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
        self._state = "stopped"

    # ---- public API ----

    def status(self) -> dict[str, Any]:
        """Settings panel reads this. Numbers are estimates, not
        load-bearing, meant for human eyeballing."""
        return {
            "enabled":      self.cfg.enabled,
            "state":        self._state,
            "host":         self.cfg.host,
            "port":         self.cfg.port,
            "last_error":   self._last_error,
            "device_count": len(self._devices),
            "ha_routes":    len(self._ha_routes),
            "manual_routes": len(self._manual_routes),
        }

    def current_snapshots(self) -> dict[str, dict[str, Any]]:
        """Return latest snapshot per virtual device, dropping stale
        ones. Called from the scheduler's poll loop and merged into
        result["devices"]. Synchronous + lock-free read of a dict
        Python's GIL makes safe-enough for our use (writes go through
        the background task, this read happens once per poll tick)."""
        cutoff = time.time() - max(1, self.cfg.stale_after_seconds)
        out: dict[str, dict[str, Any]] = {}
        for label, rec in self._devices.items():
            if rec.get("last_at", 0) < cutoff:
                continue
            out[label] = dict(rec["snapshot"])
        return out

    # ---- HA-discovery + manual mapping ----

    def _add_manual_route(self, t: MqttInTopicCfg) -> None:
        kind = t.value_type if t.value_type in ("scalar", "json") else "scalar"
        path = t.json_path if kind == "json" else ""
        # We store vendor/kind on the route so the snapshot inherits
        # them when this is the first message for the label.
        # Pack into the same 4-tuple shape as HA routes for the
        # receive loop's dispatcher.
        self._manual_routes[t.topic] = (t.label, t.metric, kind, path)
        # Pre-allocate the device with vendor/kind hints; the snapshot
        # gets a metric key when the first message arrives.
        self._devices.setdefault(t.label, {
            "snapshot": {
                "_vendor": t.vendor, "_kind": t.kind,
                "_label": t.label, "_slave_id": None,
            },
            "last_at": 0.0,
        })

    def _ha_config_topic(self) -> str:
        return f"{self.cfg.ha_discovery_prefix}/+/+/config"

    def _ha_config_topic_with_node(self) -> str:
        # Some HA integrations publish under
        # `<prefix>/<component>/<node_id>/<object_id>/config` (e.g.
        # ESPHome). Subscribe to that pattern too.
        return f"{self.cfg.ha_discovery_prefix}/+/+/+/config"

    def _handle_ha_config(self, topic: str, payload: bytes) -> None:
        """Parse an HA-discovery config payload and add a routing
        entry. Tolerant of broken / unsupported configs, log once
        and skip."""
        if not payload:
            # HA convention: empty payload means "delete this entity".
            self._ha_routes = {
                k: v for k, v in self._ha_routes.items()
                if v[0] != topic
            }
            return
        try:
            cfg = json.loads(payload.decode("utf-8", errors="replace"))
        except Exception:
            return
        if not isinstance(cfg, dict):
            return
        state_topic = cfg.get("state_topic") or cfg.get("stat_t")
        if not state_topic or not isinstance(state_topic, str):
            return
        tmpl = cfg.get("value_template") or cfg.get("val_tpl")
        parsed = _parse_value_template(tmpl)
        if parsed is None:
            if topic not in self._ha_warned:
                log.info(
                    "mqtt_in: skipping HA entity at %s, unsupported "
                    "value_template %r (only `value` / `value_json.X` "
                    "are handled in this release)",
                    topic, tmpl,
                )
                self._ha_warned.add(topic)
            return
        kind_v, path = parsed
        # Group entities by HA device.identifier (or device.name) into
        # one virtual device row. Falls back to object_id when the
        # config didn't include a device block (some integrations
        # don't).
        dev = cfg.get("device") or {}
        ids = dev.get("identifiers")
        if isinstance(ids, list) and ids:
            label_seed = str(ids[0])
        elif dev.get("name"):
            label_seed = str(dev["name"])
        else:
            label_seed = cfg.get("unique_id") or cfg.get("object_id") or topic
        # MQTT topics + dashboard labels are case-sensitive but HA
        # device names can have spaces/punctuation. Normalise to a
        # snake-ish slug so the snapshot key matches existing
        # appliance row conventions.
        label = re.sub(r"[^a-zA-Z0-9_]+", "_", label_seed).strip("_") or "mqtt_device"
        # Metric name: prefer object_id, fall back to entity name.
        metric_seed = cfg.get("object_id") or cfg.get("name") or "value"
        metric = re.sub(r"[^a-zA-Z0-9_]+", "_", str(metric_seed)).strip("_") or "value"
        self._ha_routes[state_topic] = (label, metric, kind_v, path)
        # Initialise the device row so it shows up even before the
        # first state message lands (with `_errors` so the UI can
        # render "waiting for first message"; cleared on first
        # successful payload).
        dev_row = self._devices.setdefault(label, {
            "snapshot": {
                "_vendor": "mqtt", "_kind": "ha_entity",
                "_label": label, "_slave_id": None,
            },
            "last_at": 0.0,
        })
        # Carry HA device meta forward for display (model / sw version).
        snap = dev_row["snapshot"]
        if dev.get("model") and "model" not in snap:
            snap["model"] = str(dev["model"])
        if dev.get("manufacturer") and "manufacturer" not in snap:
            snap["manufacturer"] = str(dev["manufacturer"])
        if dev.get("sw_version") and "firmware_version" not in snap:
            snap["firmware_version"] = str(dev["sw_version"])

    def _handle_state_message(
        self, route: tuple[str, str, str, str], payload: bytes,
    ) -> None:
        """Apply a state message via either route table. Updates the
        device's snapshot dict in place + bumps last_at."""
        label, metric, kind_v, path = route
        if kind_v == "json":
            raw = _extract_json_path(payload, path)
        else:
            raw = payload.decode("utf-8", errors="replace") if payload else None
        value = _coerce_value(raw)
        if value is None:
            return  # silently drop empty / unparseable
        dev = self._devices.setdefault(label, {
            "snapshot": {
                "_vendor": "mqtt", "_kind": "sensor",
                "_label": label, "_slave_id": None,
            },
            "last_at": 0.0,
        })
        dev["snapshot"][metric] = value
        dev["last_at"] = time.time()

    # ---- background task ----

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._state = "connecting"
                async with aiomqtt.Client(
                    hostname=self.cfg.host,
                    port=self.cfg.port,
                    username=self.cfg.username or None,
                    password=self.cfg.password or None,
                    identifier=self.cfg.client_id,
                ) as client:
                    self._state = "connected"
                    self._last_error = None
                    backoff = 1.0
                    log.info("mqtt_in connected to %s:%d",
                             self.cfg.host, self.cfg.port)

                    if self.cfg.ha_discovery:
                        await client.subscribe(self._ha_config_topic())
                        await client.subscribe(self._ha_config_topic_with_node())
                    for t in self._manual_routes:
                        await client.subscribe(t)

                    # Re-subscribe any state topics we'd already
                    # learned from previous reconnect's discovery
                    # (HA broker may have retained the configs).
                    for st in list(self._ha_routes):
                        try:
                            await client.subscribe(st)
                        except Exception:
                            log.debug("mqtt_in: re-subscribe %s failed", st)

                    async for msg in client.messages:
                        if self._stop.is_set():
                            break
                        topic = str(msg.topic)
                        payload = msg.payload or b""
                        # HA discovery config landed? Parse + register
                        # the state topic, then subscribe to it.
                        if (
                            self.cfg.ha_discovery
                            and topic.startswith(self.cfg.ha_discovery_prefix + "/")
                            and topic.endswith("/config")
                        ):
                            self._handle_ha_config(topic, payload)
                            # Subscribe to any new state_topic
                            # introduced by this config.
                            for st in list(self._ha_routes):
                                try:
                                    await client.subscribe(st)
                                except Exception:
                                    pass
                            continue
                        # Otherwise it's a state message, route via
                        # whichever table claims it.
                        route = self._ha_routes.get(topic) or self._manual_routes.get(topic)
                        if route is None:
                            # Could be a wildcard manual match, walk
                            # the manual routes once. Skipped for
                            # first release; the YAML expects exact
                            # topic strings.
                            continue
                        try:
                            self._handle_state_message(route, payload)
                        except Exception:
                            log.exception("mqtt_in: handler failed for %s", topic)
                self._state = "disconnected"
            except aiomqtt.MqttError as e:
                self._state = "reconnecting"
                self._last_error = str(e)
                log.warning("mqtt_in: connection error: %s; retrying in %.1fs",
                            e, backoff)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                    break
                except asyncio.TimeoutError:
                    backoff = min(backoff * 2, 60.0)
            except Exception:
                self._state = "reconnecting"
                log.exception("mqtt_in: unexpected error")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=5)
                    break
                except asyncio.TimeoutError:
                    pass
        self._state = "stopped"
