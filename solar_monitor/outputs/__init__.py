"""Controllable outputs — anything a vendor adapter can flip.

A "ControllableOutput" is the on/off thing on a device: Renogy Rover
load terminal, JK BMS charge MOS, future MQTT relay, etc. The shape
is intentionally generic so the dashboard UI + (future) schedule
engine can treat them uniformly without knowing which vendor sits
behind each one.

See [[project-target-customer]] in user memory for why this matters:
load-output control is part of what makes WattPost more than a
read-only viewer for our two target personas.
"""
from .base import ControllableOutput, OutputAdapter, WriteResult  # noqa: F401
from .registry import discover_outputs_for_device, register_adapter  # noqa: F401
# Side-effect imports — each adapter module registers itself on import.
from . import renogy_rover  # noqa: F401
