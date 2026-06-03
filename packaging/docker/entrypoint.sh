#!/bin/sh
# WattPost appliance container entrypoint.
#
# Drops a minimal default config on the mounted /etc/wattpost volume
# the first time the container starts, so a `docker compose up -d`
# with empty volumes still gives the user a running daemon + dashboard
# to log into and configure devices via Settings.
set -eu

CFG_DIR=/etc/wattpost
CFG=${CFG_DIR}/config.yaml
DB_DIR=/var/lib/wattpost

mkdir -p "${CFG_DIR}" "${DB_DIR}"

if [ ! -f "${CFG}" ]; then
    cat > "${CFG}" <<'YAML'
# WattPost appliance — minimal default config.
# Edit via Settings → Devices in the dashboard, or directly here and
# `docker compose restart wattpost`.

# Top-level identity. Shown on the dashboard + cloud heartbeats.
label: "My WattPost"

# Database path inside the container. Mounted from host
# /var/lib/wattpost/ by the example compose so it survives image pulls.
db_path: /var/lib/wattpost/solar-monitor.db

# Polling cadence — every device gets touched on each tick.
poll_interval_seconds: 60

# Bluetooth transports. Empty by default — add real devices via the
# Settings UI's wizard once the daemon is up.
transports: []

# Vendor device list. Same — populated through the UI.
devices: []

# Alert rules — empty by default; configure via Settings → Alerts.
alerts: []

# Notification transports (ntfy, Discord, MQTT, email/SMTP, …).
notification_transports: []

# Exporters (MQTT push, Prometheus pull, …). Empty by default.
exporters: []
YAML
    echo "[wattpost-docker] wrote default config at ${CFG}"
fi

# Web port. Defaults to 80 so the bare host IP works out of the box —
# parity with the SD-card (Pi) image. Override with WATTPOST_PORT, e.g.
# when port 80 is already taken on a multi-app host. Host networking
# means this binds the host directly (container runs as root, so <1024
# is fine). The CMD intentionally omits --port so this is the only
# place the port is decided.
exec "$@" --port "${WATTPOST_PORT:-80}"
