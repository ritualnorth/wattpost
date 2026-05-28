"""Ingest from external MQTT broker → virtual devices (#256).

Van builders typically already run Home Assistant or Shelly gear;
this surfaces every entity the broker knows about into the WattPost
dashboard without needing a BLE driver for each.
"""
from .service import MqttInService

__all__ = ["MqttInService"]
