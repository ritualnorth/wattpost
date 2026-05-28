"""Outbound tunnel to the WattPost cloud.

When the appliance is paired AND the cloud has issued a tunnel
token, this module runs `cloudflared` as a managed child process so
the local dashboard is reachable from anywhere at
`<slug>.wattpost.io`.

Strictly opt-in:
  - Tunnel disabled by default (no token → service doesn't start).
  - Failures are non-fatal, losing the tunnel never breaks the
    local UI or polling.
  - `cloudflared` binary must be on PATH; missing → log + skip.
"""
from .service import TunnelService  # noqa: F401
