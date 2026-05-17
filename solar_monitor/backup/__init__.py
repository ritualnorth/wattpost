"""Scheduled local backup service + (Pro-tier) cloud upload.

The on-demand HTTP endpoints live in `solar_monitor.api.backup`; this
package owns the background loop that takes a snapshot every
`interval_hours`, prunes to `keep_count`, and (optionally) pushes the
snapshot to wattpost.cloud for Pro/Installer customers.

Boot-anchor scheduling: on start we read the newest existing snapshot's
mtime and either run immediately (if missing or older than the
interval) or schedule the next run for when the existing one expires.
That way a Pi that reboots every few days still gets weekly snapshots
without the loop drifting.
"""
from .service import BackupService

__all__ = ["BackupService"]
