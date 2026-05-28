"""Ingest from external MQTT broker → virtual devices (#256).

See [[project_van_mode]] / [[project_target_customer]], third piece
of the sensor wave. Most Persona-A van builders already run Home
Assistant or have Shelly gear; this lets them surface every entity
their broker already knows about into the WattPost dashboard without
needing a BLE driver for each.
"""
from .service import MqttInService

__all__ = ["MqttInService"]
