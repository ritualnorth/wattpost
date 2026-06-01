"""Settings UI editor for the MQTT exporter, closes the last
yaml-only gap on the appliance side.

The exporter system supports a list of arbitrary types, but in practice
99% of users have at most one MQTT exporter. The UI assumes that single
exporter; advanced users can still maintain multiple in config.yaml by
hand (the find-first-mqtt logic just leaves the others alone).
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

import msgspec
import yaml
from litestar import get, post, put
from litestar.datastructures import State
from litestar.exceptions import HTTPException

from ..config import Config

log = logging.getLogger(__name__)


class MqttExporterPayload(msgspec.Struct, kw_only=True):
    enabled: bool = True
    host: str | None = None
    port: int = 1883
    username: str | None = None
    password: str | None = None
    client_id: str = "solar-monitor"
    topic_prefix: str = "solar"
    qos: int = 0
    retain: bool = True
    publish_per_metric: bool = True
    ha_discovery: bool = False
    ha_discovery_prefix: str = "homeassistant"
    ha_node_id: str = "solar_monitor"


def _save_config(config_path: str, mutator) -> None:
    path = Path(config_path)
    raw = yaml.safe_load(path.read_text()) or {}
    raw = mutator(raw)
    if raw is None:
        raise RuntimeError("config mutator returned None")
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(raw, sort_keys=False))
    tmp.replace(path)


def _first_mqtt(exporters: list[dict]) -> dict | None:
    for e in exporters:
        if e.get("type") == "mqtt":
            return e
    return None


def mqtt_config_view(config: Config) -> dict[str, Any]:
    """Masked view of the MQTT exporter config. `enabled` flag tracks
    whether the exporter exists at all. Pure config→dict so the
    aggregate /api/system/integrations endpoint can reuse it (#18)."""
    mqtt = _first_mqtt(config.exporters)
    if mqtt is None:
        return {
            "enabled": False, "host": "", "port": 1883,
            "username": "", "client_id": "solar-monitor",
            "topic_prefix": "solar", "qos": 0, "retain": True,
            "publish_per_metric": True, "ha_discovery": False,
            "ha_discovery_prefix": "homeassistant", "ha_node_id": "solar_monitor",
        }
    return {
        "enabled":             True,
        "host":                mqtt.get("host", ""),
        "port":                int(mqtt.get("port", 1883)),
        "username":            mqtt.get("username", "") or "",
        "password":            "****" if mqtt.get("password") else "",
        "client_id":           mqtt.get("client_id", "solar-monitor"),
        "topic_prefix":        mqtt.get("topic_prefix", "solar"),
        "qos":                 int(mqtt.get("qos", 0)),
        "retain":              bool(mqtt.get("retain", True)),
        "publish_per_metric":  bool(mqtt.get("publish_per_metric", True)),
        "ha_discovery":        bool(mqtt.get("ha_discovery", False)),
        "ha_discovery_prefix": mqtt.get("ha_discovery_prefix", "homeassistant"),
        "ha_node_id":          mqtt.get("ha_node_id", "solar_monitor"),
    }


@get("/api/exporters/mqtt/config")
async def get_mqtt_config(state: State) -> dict[str, Any]:
    return mqtt_config_view(state["config"])


@put("/api/exporters/mqtt/config")
async def update_mqtt_config(
    data: MqttExporterPayload, state: State,
) -> dict[str, Any]:
    config: Config = state["config"]
    config_path: str = state.get("config_path", "config.yaml")

    if not data.enabled:
        # Remove the first MQTT exporter, if any.
        before = len(config.exporters)
        config.exporters = [e for e in config.exporters if e.get("type") != "mqtt"]
        def _mutate(raw):
            raw["exporters"] = [
                e for e in (raw.get("exporters") or [])
                if e.get("type") != "mqtt"
            ]
            return raw
        _save_config(config_path, _mutate)
        log.info("MQTT exporter removed (%d → %d)", before, len(config.exporters))
        return {"ok": True, "enabled": False, "restart_required": True}

    if not data.host:
        raise HTTPException(status_code=400, detail="host is required when enabled")
    if data.port < 1 or data.port > 65535:
        raise HTTPException(status_code=400, detail="port must be 1..65535")
    if data.qos not in (0, 1, 2):
        raise HTTPException(status_code=400, detail="qos must be 0, 1, or 2")

    # Preserve existing password when UI sends the "****" sentinel
    # (form left blank). Same idiom as the alert-transport editor.
    existing = _first_mqtt(config.exporters) or {}
    password = data.password
    if password == "****" and existing.get("password"):
        password = existing["password"]

    new_entry: dict[str, Any] = {
        "id":                  existing.get("id", "mqtt_local"),
        "type":                "mqtt",
        "host":                data.host,
        "port":                data.port,
        "client_id":           data.client_id,
        "topic_prefix":        data.topic_prefix,
        "qos":                 data.qos,
        "retain":              data.retain,
        "publish_per_metric":  data.publish_per_metric,
        "ha_discovery":        data.ha_discovery,
        "ha_discovery_prefix": data.ha_discovery_prefix,
        "ha_node_id":          data.ha_node_id,
    }
    if data.username:
        new_entry["username"] = data.username
    if password:
        new_entry["password"] = password

    # Replace the first MQTT exporter or append a new one.
    replaced = False
    out: list[dict] = []
    for e in config.exporters:
        if not replaced and e.get("type") == "mqtt":
            out.append(new_entry); replaced = True
        else:
            out.append(e)
    if not replaced:
        out.append(new_entry)
    config.exporters = out

    def _mutate(raw):
        existing_list = raw.get("exporters") or []
        new_list: list[dict] = []
        replaced_in_yaml = False
        for e in existing_list:
            if not replaced_in_yaml and e.get("type") == "mqtt":
                new_list.append(new_entry); replaced_in_yaml = True
            else:
                new_list.append(e)
        if not replaced_in_yaml:
            new_list.append(new_entry)
        raw["exporters"] = new_list
        return raw
    _save_config(config_path, _mutate)
    log.info("MQTT exporter configured: %s:%d (topic=%s, ha=%s)",
             new_entry["host"], new_entry["port"],
             new_entry["topic_prefix"], new_entry["ha_discovery"])
    return {"ok": True, "enabled": True, "restart_required": True}


@post("/api/exporters/mqtt/test")
async def test_mqtt(
    data: MqttExporterPayload, state: State,
) -> dict[str, Any]:
    """One-shot connect to the broker with the supplied credentials,
    used by the Settings UI's Test button. Doesn't publish anything;
    just verifies host/port/auth are correct."""
    import aiomqtt
    if not data.host:
        raise HTTPException(status_code=400, detail="host is required")

    config: Config = state["config"]
    existing = _first_mqtt(config.exporters) or {}
    password = data.password
    if password == "****" and existing.get("password"):
        password = existing["password"]

    try:
        async with aiomqtt.Client(
            hostname=data.host,
            port=data.port,
            username=data.username or None,
            password=password or None,
            timeout=10.0,
            identifier=data.client_id,
        ):
            pass
    except aiomqtt.MqttError as e:
        raise HTTPException(
            status_code=502,
            detail=f"could not connect to {data.host}:{data.port}, {e}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"{data.host}:{data.port} unreachable: {e}",
        )
    return {"ok": True, "host": data.host, "port": data.port}
