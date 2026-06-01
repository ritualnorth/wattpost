"""Prometheus exporter (#14) — exposes the latest poll result as
Prometheus/OpenMetrics text for Grafana, via the standard pull model.

Unlike the MQTT exporter (push), Prometheus *scrapes*. So this exporter
holds the most recent poll result in memory and the `/metrics` route
(served by the web app when this exporter is configured) renders it on
demand. No outbound connections, no credentials — read-only telemetry,
the same data the LAN dashboard shows.

Config (config.yaml):

    exporters:
      - id: prom
        type: prometheus
        metric_prefix: wattpost   # optional, default "wattpost"

Point Prometheus at `http://<appliance>:<port>/metrics`; Grafana reads
from Prometheus. Every numeric per-device metric becomes a gauge
labelled by device, e.g. `wattpost_soc_pct{device="battery_0"}`.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from .base import Exporter
from .registry import register_exporter

log = logging.getLogger(__name__)

# Prometheus metric names must match [a-zA-Z_:][a-zA-Z0-9_:]*; label
# values are arbitrary UTF-8 but need backslash/quote/newline escaping.
_NAME_BAD = re.compile(r"[^a-zA-Z0-9_:]")


def _sanitize_name(s: str) -> str:
    name = _NAME_BAD.sub("_", str(s))
    # A leading digit is illegal in a metric/label-name segment.
    if name and name[0].isdigit():
        name = "_" + name
    return name or "_"


def _escape_label(s: str) -> str:
    return (str(s).replace("\\", "\\\\")
                  .replace('"', '\\"')
                  .replace("\n", "\\n"))


def render_openmetrics(
    result: dict[str, Any] | None,
    *,
    prefix: str = "wattpost",
    now: float | None = None,
) -> str:
    """Render a poll result as Prometheus text exposition format.

    Robust to a missing/empty result (returns just the liveness +
    staleness metrics) and to non-numeric metric values (skipped).
    Booleans render as 1/0. Samples are grouped by metric name so each
    carries exactly one `# TYPE` line, as the format requires.
    """
    prefix = _sanitize_name(prefix) or "wattpost"
    now = time.time() if now is None else now

    # Collect samples grouped by fully-qualified metric name so we emit
    # one HELP/TYPE header per metric, then all its device-labelled rows.
    grouped: dict[str, list[str]] = {}
    devices = (result or {}).get("devices") or {}
    if isinstance(devices, dict):
        for label, data in devices.items():
            if not isinstance(data, dict):
                continue
            dev = _escape_label(label)
            for key, val in data.items():
                # Skip internal/metadata fields (_vendor, _kind, _slave_id):
                # they're plumbing, not telemetry, and shouldn't be metrics.
                if isinstance(key, str) and key.startswith("_"):
                    continue
                if isinstance(val, bool):
                    num: float = 1.0 if val else 0.0
                elif isinstance(val, (int, float)):
                    num = float(val)
                else:
                    continue  # strings / None / nested — not a metric
                metric = f"{prefix}_{_sanitize_name(key)}"
                grouped.setdefault(metric, []).append(
                    f'{metric}{{device="{dev}"}} {num!r}'
                )

    lines: list[str] = []
    lines.append(f"# HELP {prefix}_up 1 if the exporter is serving metrics.")
    lines.append(f"# TYPE {prefix}_up gauge")
    lines.append(f"{prefix}_up 1")

    ts = (result or {}).get("timestamp")
    if isinstance(ts, (int, float)):
        lines.append(f"# HELP {prefix}_last_poll_timestamp_seconds Unix time of the last poll.")
        lines.append(f"# TYPE {prefix}_last_poll_timestamp_seconds gauge")
        lines.append(f"{prefix}_last_poll_timestamp_seconds {float(ts)!r}")
        lines.append(f"# HELP {prefix}_poll_age_seconds Seconds since the last poll at scrape time.")
        lines.append(f"# TYPE {prefix}_poll_age_seconds gauge")
        lines.append(f"{prefix}_poll_age_seconds {max(0.0, now - float(ts))!r}")

    for metric in sorted(grouped):
        lines.append(f"# TYPE {metric} gauge")
        lines.extend(grouped[metric])

    return "\n".join(lines) + "\n"


class PrometheusExporter(Exporter):
    """Caches the latest poll result; the `/metrics` route renders it."""

    def __init__(self, *, id: str = "prometheus", metric_prefix: str = "wattpost"):
        self.id = id
        self.metric_prefix = _sanitize_name(metric_prefix) or "wattpost"
        self._latest: dict[str, Any] | None = None

    async def start(self) -> None:
        log.info("[%s] prometheus exporter ready — scrape /metrics "
                 "(prefix=%s)", self.id, self.metric_prefix)

    async def stop(self) -> None:
        self._latest = None

    async def export(self, result: dict[str, Any]) -> None:
        # Pull model: just keep the freshest result. Returns instantly,
        # never blocks the scheduler.
        self._latest = result

    def render(self) -> str:
        return render_openmetrics(self._latest, prefix=self.metric_prefix)


@register_exporter("prometheus")
def _build(cfg: dict[str, Any]) -> PrometheusExporter:
    return PrometheusExporter(
        id=str(cfg.get("id") or "prometheus"),
        metric_prefix=str(cfg.get("metric_prefix") or "wattpost"),
    )
