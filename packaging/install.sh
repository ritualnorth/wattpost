#!/usr/bin/env bash
# WattPost install / upgrade script.
#
# Usage (as root, or via sudo):
#   curl -sSL https://wattpost.cloud/install.sh | sudo bash
#   # or, from a local checkout:
#   sudo ./packaging/install.sh
#
# Idempotent: re-running upgrades the venv + service in place without
# touching /etc/wattpost/config.yaml or the SQLite database.

set -euo pipefail

# ----- paths -----
APP_USER="wattpost"
APP_GROUP="wattpost"
APP_ROOT="/opt/wattpost"
APP_VENV="${APP_ROOT}/venv"
CONFIG_DIR="/etc/wattpost"
CONFIG_FILE="${CONFIG_DIR}/config.yaml"
STATE_DIR="/var/lib/wattpost"
SERVICE_DEST="/etc/systemd/system/wattpost.service"

# Default: install from the local checkout (the directory this script
# lives in). For a future remote-install path, override with
# WATTPOST_SOURCE=git+https://... or a wheel URL.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOURCE="${WATTPOST_SOURCE:-${REPO_ROOT}}"

# ----- preflight -----
if [[ $EUID -ne 0 ]]; then
    echo "install.sh must run as root (sudo $0)" >&2
    exit 1
fi

step() { echo -e "\n\033[1;36m==>\033[0m $*"; }
warn() { echo -e "\033[1;33mwarn:\033[0m $*" >&2; }

step "checking prerequisites"
if ! command -v python3 >/dev/null; then
    echo "python3 not found — apt install python3 first" >&2; exit 1
fi
PYVER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
if [[ "$(printf '%s\n' "3.11" "${PYVER}" | sort -V | head -1)" != "3.11" ]]; then
    echo "python ${PYVER} too old — need 3.11+" >&2; exit 1
fi
if ! command -v systemctl >/dev/null; then
    echo "systemd not detected (this is intended for Pi OS / Debian)." >&2; exit 1
fi
if ! systemctl is-active --quiet bluetooth 2>/dev/null; then
    warn "bluetooth service isn't active — make sure BlueZ is installed and running before first poll."
fi

# ----- user + dirs -----
step "creating user/group ${APP_USER}"
if ! getent group "${APP_GROUP}" >/dev/null; then
    groupadd --system "${APP_GROUP}"
fi
if ! id "${APP_USER}" >/dev/null 2>&1; then
    useradd --system --gid "${APP_GROUP}" \
            --home-dir "${APP_ROOT}" --shell /usr/sbin/nologin \
            "${APP_USER}"
fi
# bluetooth group: gives DBus access to BlueZ for BLE scans/connects.
# Ensure the group exists even when bluez isn't installed yet —
# the systemd unit declares `SupplementaryGroups=bluetooth` and
# misses-fatally with `216/GROUP` if the group is absent. Pi OS
# ships bluez (so the group is pre-created); plain Ubuntu Server
# does not. Group creation is cheap and harmless.
if ! getent group bluetooth >/dev/null; then
    groupadd --system bluetooth
fi
usermod -a -G bluetooth "${APP_USER}"

step "preparing ${APP_ROOT}, ${CONFIG_DIR}, ${STATE_DIR}"
mkdir -p "${APP_ROOT}" "${CONFIG_DIR}" "${STATE_DIR}"
chown -R "${APP_USER}:${APP_GROUP}" "${APP_ROOT}" "${STATE_DIR}"
chown "${APP_USER}:${APP_GROUP}" "${CONFIG_DIR}"

# ----- venv -----
step "installing venv at ${APP_VENV}"
if [[ ! -x "${APP_VENV}/bin/python" ]]; then
    python3 -m venv "${APP_VENV}"
fi
"${APP_VENV}/bin/pip" install --upgrade pip wheel >/dev/null
# A stale `solar_monitor.egg-info/` from a developer's earlier
# `pip install -e .` can break the wheel build with "Cannot update
# time stamp of directory". Real installs (curl|bash, fresh git clone,
# pi-gen rsync) never have one; this is a no-op there. Silently
# ignored if SOURCE is read-only — pip would already have failed in
# that case for other reasons.
find "${SOURCE}" -maxdepth 3 -name '*.egg-info' -type d \
    -exec rm -rf {} + 2>/dev/null || true
# --prefer-binary: when pi-gen runs this inside a qemu-emulated chroot,
# Python-C-extension compilation is glacial. All our deps publish
# aarch64 wheels on PyPI; this flag tells pip to grab those even when
# a newer sdist exists. Native installs are unaffected (wheel still
# chosen first by default).
#
# --force-reinstall --no-deps: pyproject.toml hardcodes version="0.0.1"
# (the package metadata never bumps even when solar_monitor/__init__.py
# does), so a plain `--upgrade` is a no-op when the upstream version
# string didn't change — pip says "0.0.1 already installed". Force-
# reinstall makes wattpost-update actually swap the venv contents.
# --no-deps so we don't reinstall the entire dep tree on every update
# (deps move on their own cadence; `pip install --upgrade` above
# already bumped them if needed).
"${APP_VENV}/bin/pip" install --prefer-binary --upgrade --force-reinstall --no-deps "${SOURCE}"

# ----- config (only if not present — don't clobber the user's edits) -----
if [[ ! -f "${CONFIG_FILE}" ]]; then
    step "seeding ${CONFIG_FILE} from config.example.yaml"
    if [[ -f "${REPO_ROOT}/config.example.yaml" ]]; then
        cp "${REPO_ROOT}/config.example.yaml" "${CONFIG_FILE}"
    else
        cat > "${CONFIG_FILE}" <<'YAML'
# Edit via the Setup wizard at http://<this-pi>:8000/#/setup
# or by hand. See https://github.com/ritualnorth/offgrid-monitor
transports: []
devices: []
exporters: []
notification_transports: []
alerts: []
YAML
    fi
    chown "${APP_USER}:${APP_GROUP}" "${CONFIG_FILE}"
    chmod 0640 "${CONFIG_FILE}"
else
    step "keeping existing ${CONFIG_FILE}"
fi

# ----- local web UI password (opt-in) -----
# By default, the local dashboard accepts any LAN client — same trust
# model as Pi-hole, Solar Assistant, Home Assistant Yellow. Most
# off-grid setups have a single trusted network, and the cloud
# tunnel's auth is the strong gate for remote access. Security-
# conscious users (shared housing, corporate LAN, multi-tenant
# warehouses) opt in via `wattpost-config → Set local web password`,
# which writes /etc/wattpost/web-password.hash. The middleware
# (solar_monitor/web_auth.py) auto-detects the hash file's presence
# and starts enforcing.
#
# install.sh deliberately does NOT auto-generate. Auto-creating a
# password the user didn't ask for puts a "wattpost-7f3a2b" string in
# their notes/password-manager forever, even though they'll never
# need it — pure friction.

# ----- sudoers fragment for Tailscale -----
# Settings → Network needs to run `tailscale up / logout / serve` from
# the daemon (which runs as the wattpost system user). Without this,
# the Connect / Disconnect / HTTPS-serve buttons would all 500 with a
# permission error. Limited to the three exact subcommands, so the
# escalation surface stays tight.
step "granting wattpost user sudo access for tailscale + update helper"
SUDOERS_FILE="/etc/sudoers.d/wattpost"
cat > "${SUDOERS_FILE}.tmp" <<'SUDO'
# Allow the wattpost daemon to manage its Tailscale connection from
# the dashboard's Settings → Network block.
wattpost ALL=(root) NOPASSWD: /usr/bin/tailscale up *, /usr/bin/tailscale logout, /usr/bin/tailscale serve *
# Allow the wattpost daemon to fire the in-place upgrade helper from
# the dashboard's "Update now" button (and from wattpost-config). The
# helper itself is a fixed, trusted script — locked to no args so the
# daemon can't pass a malicious source URL.
wattpost ALL=(root) NOPASSWD: /usr/local/bin/wattpost-update
SUDO
# visudo -c validates syntax before we move it into /etc/sudoers.d/
if visudo -cf "${SUDOERS_FILE}.tmp" >/dev/null; then
    install -m 0440 "${SUDOERS_FILE}.tmp" "${SUDOERS_FILE}"
fi
rm -f "${SUDOERS_FILE}.tmp"
# Clean up the old filename so we don't end up with two sudoers
# entries for the same user after upgrade.
rm -f /etc/sudoers.d/wattpost-tailscale

# Install the update helper. Root-owned, world-readable, world-
# executable — sudoers takes care of who can actually run it.
step "installing wattpost-update helper"
if [ -f "${SCRIPT_DIR}/cli/wattpost-update" ]; then
    install -m 0755 -o root -g root \
        "${SCRIPT_DIR}/cli/wattpost-update" /usr/local/bin/wattpost-update
    # Pre-create the log file with permissions that let the wattpost
    # daemon read it back via /api/system/update/log. The helper runs
    # as root and writes to it; the daemon (group wattpost) reads.
    touch /var/log/wattpost-update.log
    chgrp wattpost /var/log/wattpost-update.log
    chmod 0644 /var/log/wattpost-update.log
fi

# ----- cloudflared (optional, for cloud tunnel) -----
# Install Cloudflare's cloudflared binary so the daemon can expose
# the local dashboard at <slug>.wattpost.io once it's paired to the
# cloud. Idempotent: skipped if already installed. Apt-managed (via
# Cloudflare's signed repo) so `apt upgrade` keeps it current.
#
# Skip entirely on architectures Cloudflare doesn't ship for —
# tunnel will simply stay off, appliance keeps working locally.
if ! command -v cloudflared >/dev/null; then
    step "installing cloudflared (for cloud tunnel — optional)"
    ARCH="$(dpkg --print-architecture 2>/dev/null || true)"
    case "${ARCH}" in
        amd64|arm64|armhf)
            if ! [ -f /usr/share/keyrings/cloudflare-main.gpg ]; then
                curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
                    | gpg --dearmor -o /usr/share/keyrings/cloudflare-main.gpg
            fi
            if ! [ -f /etc/apt/sources.list.d/cloudflared.list ]; then
                # Cloudflare ships `bookworm` + `any` distros only —
                # no `trixie` repo as of mid-2026. The package is a
                # statically-linked Go binary, so the bookworm package
                # works fine on a trixie host. Pin to bookworm to
                # avoid "Release file not found" on newer Pi OS.
                echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
https://pkg.cloudflare.com/cloudflared bookworm main" \
                    > /etc/apt/sources.list.d/cloudflared.list
            fi
            apt-get update -qq
            apt-get install -y cloudflared
            ;;
        *)
            warn "cloudflared not available for arch '${ARCH}' — cloud tunnel will be disabled."
            ;;
    esac
else
    step "cloudflared already installed ($(cloudflared --version 2>/dev/null | head -1))"
fi

# ----- version file (read by motd) -----
# Cheap source of truth that doesn't require invoking the venv. Read
# from the source's solar_monitor/__init__.py at install time.
INIT_VERSION="$(grep -E '^__version__' "${SOURCE}/solar_monitor/__init__.py" 2>/dev/null \
                | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
if [ -n "${INIT_VERSION}" ]; then
    echo "${INIT_VERSION}" > "${CONFIG_DIR}/version"
    chmod 0644 "${CONFIG_DIR}/version"
fi

# ----- web port -----
# Default to 80 on a Pi-style SD-card install (single-purpose appliance,
# users hit http://wattpost.local without typing a port) and 8000 on
# anything else (Docker hosts, manual installs on shared machines where
# port 80 is taken). Operator can force-pick by setting WATTPOST_PORT
# before running install.sh, or by editing /etc/wattpost/port.env and
# `systemctl restart wattpost` afterwards. The systemd unit reads this
# via EnvironmentFile= so a port change doesn't require a unit edit.
if [ -z "${WATTPOST_PORT:-}" ]; then
    # Pi OS image ships /etc/rpi-issue (and similar). If that's
    # missing, assume a shared host and default to 8000.
    if [ -f /etc/rpi-issue ] || [ -f /etc/rpi-eeprom-update-2025.05 ]; then
        WATTPOST_PORT=80
    else
        WATTPOST_PORT=8000
    fi
fi
step "web port: ${WATTPOST_PORT}"
echo "WATTPOST_PORT=${WATTPOST_PORT}" > "${CONFIG_DIR}/port.env"
chmod 0644 "${CONFIG_DIR}/port.env"

# ----- MOTD banner -----
# Drop our SSH login banner. /etc/update-motd.d/ is read on every login
# (when PAM motd is enabled, default on Debian/Pi OS). Numeric prefix
# orders us early so the WattPost block shows up at the top.
step "installing SSH login banner (/etc/update-motd.d/10-wattpost)"
if [ -d "${SCRIPT_DIR}/motd" ] && [ -d /etc/update-motd.d ]; then
    install -m 0755 "${SCRIPT_DIR}/motd/10-wattpost" /etc/update-motd.d/10-wattpost
fi

# ----- wattpost-config CLI -----
# The raspi-config-style menu for "I just SSH'd in, now what?" — wraps
# the most common admin actions (logs, restart, port change, pair
# status). Installed at /usr/local/bin so it's on root's $PATH without
# editing /etc/profile.
step "installing wattpost-config CLI"
if [ -f "${SCRIPT_DIR}/cli/wattpost-config" ]; then
    install -m 0755 "${SCRIPT_DIR}/cli/wattpost-config" /usr/local/bin/wattpost-config
fi

# ----- systemd unit -----
step "installing wattpost.service"
install -m 0644 "${SCRIPT_DIR}/systemd/wattpost.service" "${SERVICE_DEST}"
systemctl daemon-reload
systemctl enable wattpost.service >/dev/null
systemctl restart wattpost.service

# ----- summary -----
step "done"
IP=$(hostname -I | awk '{print $1}')
# Show port in the URL only when it's not the default :80 (which is
# implicit in any browser).
PORT_SUFFIX=""
if [ "${WATTPOST_PORT}" != "80" ]; then
    PORT_SUFFIX=":${WATTPOST_PORT}"
fi
cat <<EOF

WattPost is running. Open the dashboard:

    http://${IP:-<this-pi>}${PORT_SUFFIX}/

Useful commands:
    sudo systemctl status wattpost     # health
    journalctl -u wattpost -f           # live logs
    sudo systemctl restart wattpost     # apply config changes (the UI's
                                        # "Restart daemon" button does the
                                        # same via /api/system/restart)

Config file:       ${CONFIG_FILE}
Database:          ${STATE_DIR}/solar-monitor.db
App + venv:        ${APP_ROOT}/

EOF
