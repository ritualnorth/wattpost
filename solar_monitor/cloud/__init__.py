"""Appliance-side cloud (wattpost.io) integration.

When enabled, the daemon periodically pushes a heartbeat to the
configured endpoint so the cloud's multi-site dashboard sees the
appliance as online and can alert the owner if the heartbeat stops.

Strictly additive — the appliance keeps working with no cloud,
no internet, no account.
"""
from .service import CloudService  # noqa: F401
