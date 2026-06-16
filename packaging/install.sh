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

# Atomic-swap layout (#36). The customer-visible path stays
# /opt/wattpost — what changes is that it's now a symlink to one
# of two slot directories under /opt/wattpost-slots/. Slot a is
# the bootstrap target; subsequent updates (#36 Slice 2) install
# into the inactive slot, run a health probe, then atomic-flip
# the /opt/wattpost symlink. wattpost-update + an OnFailure
# watchdog enable automated rollback (#36 Slice 3).
SLOTS_DIR=/opt/wattpost-slots
SLOT_A="${SLOTS_DIR}/a"
SLOT_B="${SLOTS_DIR}/b"

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

# BlueZ controller auto-power. The daemon polls BLE gear (Renogy, BMS, shunts)
# through bleak, which needs a *powered* controller — if it's powered off
# every poll fails with POWERED_OFF and the device shows OFFLINE. BlueZ does
# not reliably power the controller after a reboot or a `bluetooth` restart
# unless `[Policy] AutoEnable=true` is set, and it ships commented out. Make it
# explicit so a box always polls its solar gear across reboots. Pi/Debian only
# (skipped where BlueZ isn't installed, e.g. Docker — BLE no-ops there anyway).
BT_CONF=/etc/bluetooth/main.conf
if [ -f "${BT_CONF}" ]; then
    step "ensuring BlueZ AutoEnable=true (BLE polling survives reboots)"
    if grep -q '^\[Policy\]' "${BT_CONF}"; then
        if grep -qiE '^#?\s*AutoEnable' "${BT_CONF}"; then
            sed -i -E 's/^#?\s*AutoEnable.*/AutoEnable=true/I' "${BT_CONF}"
        else
            sed -i '/^\[Policy\]/a AutoEnable=true' "${BT_CONF}"
        fi
    else
        printf '\n[Policy]\nAutoEnable=true\n' >> "${BT_CONF}"
    fi
    # Power it on now too (best-effort; don't restart bluetooth.service — that
    # would briefly drop any in-flight BLE poll on a live re-run).
    command -v bluetoothctl >/dev/null 2>&1 && bluetoothctl power on >/dev/null 2>&1 || true
fi

# Serial access for a USB GPS (NMEA over /dev/ttyACM*/ttyUSB*, group
# 'dialout') and USB-RS485 charge controllers. Without this the daemon
# can't open the receiver / controller.
usermod -a -G dialout "${APP_USER}"

# Keep ModemManager's hands off the appliance's USB serial devices. MM
# probes ttyACM/ttyUSB devices as cellular modems and steals the port —
# the classic USB-GPS-on-Linux failure ("multiple access on port /
# returned no data", with /dev/ttyACM0 intermittently vanishing), and the
# same thing bites serial charge controllers. We DON'T mask MM (some
# off-grid rigs use a real USB cellular modem it should manage) — we just
# tell it to ignore the common GPS / USB-serial adapter chips. See
# docs/gps-and-location.md.
install -d /etc/udev/rules.d
cat > /etc/udev/rules.d/99-wattpost-gps.rules <<'UDEV'
# WattPost: exclude USB GPS receivers + serial adapters from ModemManager
# so the daemon can own the port. Add your own device with the same flag.
ATTRS{idVendor}=="1546", ENV{ID_MM_DEVICE_IGNORE}="1"                          # u-blox (VK-162 etc.)
ATTRS{idVendor}=="0403", ENV{ID_MM_DEVICE_IGNORE}="1"                          # FTDI (VE.Direct, RS485)
ATTRS{idVendor}=="067b", ATTRS{idProduct}=="2303", ENV{ID_MM_DEVICE_IGNORE}="1" # Prolific PL2303
ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", ENV{ID_MM_DEVICE_IGNORE}="1" # Silicon Labs CP210x
ATTRS{idVendor}=="1a86", ENV{ID_MM_DEVICE_IGNORE}="1"                          # CH340/CH341
ATTRS{idVendor}=="0e8d", ENV{ID_MM_DEVICE_IGNORE}="1"                          # MediaTek GPS
UDEV
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger 2>/dev/null || true

step "preparing ${APP_ROOT}, ${CONFIG_DIR}, ${STATE_DIR}"
mkdir -p "${CONFIG_DIR}" "${STATE_DIR}"

# ---- atomic-swap slot layout (#36) ----
# Three states ${APP_ROOT} can be in:
#   1. missing — fresh install. Create slot a, symlink ${APP_ROOT} → a.
#   2. real directory — legacy pre-#36 install. Move it into slot a,
#      replace the directory with a symlink. One-way migration.
#   3. symlink — already in slot layout. No-op, just resolve the
#      target to know which slot we're installing into.
mkdir -p "${SLOTS_DIR}"
if [ -L "${APP_ROOT}" ]; then
    TARGET=$(readlink -f "${APP_ROOT}")
    if [ ! -d "${TARGET}" ]; then
        # Dangling symlink — slot dir was deleted out from under us.
        # Recover by recreating slot a and re-pointing.
        warn "${APP_ROOT} → ${TARGET} is dangling; recreating slot a"
        rm -f "${APP_ROOT}"
        mkdir -p "${SLOT_A}"
        ln -sfn "${SLOT_A}" "${APP_ROOT}"
    fi
elif [ -d "${APP_ROOT}" ]; then
    step "migrating legacy ${APP_ROOT}/ into slot layout"
    # Pause the service so the venv isn't in active use while we
    # rename its parent directory. The mv is atomic on the same FS;
    # the symlink replace below is also atomic via mv -T.
    systemctl stop wattpost.service 2>/dev/null || true
    if [ -d "${SLOT_A}" ]; then
        # Both legacy dir AND slot a exist — operator did something
        # weird (manual rsync etc.). Don't clobber the slot; bail
        # with instructions.
        echo "error: ${APP_ROOT} and ${SLOT_A} both exist." >&2
        echo "  Resolve by hand: either rm -rf ${SLOT_A} (if it's" >&2
        echo "  stale) or rm -rf ${APP_ROOT} (if the slot is canonical)." >&2
        exit 1
    fi
    mv "${APP_ROOT}" "${SLOT_A}"
    ln -s "${SLOT_A}" "${APP_ROOT}.new"
    mv -Tf "${APP_ROOT}.new" "${APP_ROOT}"
else
    # Fresh install — no prior layout to migrate.
    mkdir -p "${SLOT_A}"
    ln -sfn "${SLOT_A}" "${APP_ROOT}"
fi

# Migrate the legacy /opt/wattpost-src into the active slot. Prior
# to #36, wattpost-update downloaded source into a sibling dir;
# now it lives under the slot so an inactive-slot install has its
# own source tree.
ACTIVE_SLOT=$(readlink -f "${APP_ROOT}")
LEGACY_SRC=/opt/wattpost-src
if [ -d "${LEGACY_SRC}" ] && [ ! -e "${ACTIVE_SLOT}/src" ]; then
    step "migrating ${LEGACY_SRC}/ → ${ACTIVE_SLOT}/src/"
    mv "${LEGACY_SRC}" "${ACTIVE_SLOT}/src"
    # If SOURCE was pointing at the legacy path (the pi-gen chroot
    # invokes us with WATTPOST_SOURCE=/opt/wattpost-src, see
    # packaging/pi-gen/stage-wattpost/01-install/00-run-chroot.sh),
    # update SOURCE now — otherwise the pip install below will fail
    # with "Hint: It looks like a path. File '/opt/wattpost-src'
    # does not exist." Every tagged SD-image build since v0.1.32
    # was failing here.
    if [ "${SOURCE}" = "${LEGACY_SRC}" ]; then
        SOURCE="${ACTIVE_SLOT}/src"
        step "rewriting SOURCE → ${SOURCE} (pi-gen path migrated)"
    fi
    # v0.1.45 patched SOURCE after the move but missed SCRIPT_DIR /
    # REPO_ROOT (both captured at line 38-39 from BASH_SOURCE before
    # the slot dance). When the chroot ran us out of /opt/wattpost-src,
    # SCRIPT_DIR was /opt/wattpost-src/packaging; after the mv that
    # path is gone, so the `install -m 0644 "${SCRIPT_DIR}/systemd/
    # wattpost.service"` ~250 lines down errors with "cannot stat".
    # Every tagged SD-image build since v0.1.45 failed silently here
    # (the earlier wattpost-rollback + wattpost-config install lines
    # have `if [ -f ]` guards so they no-op'd; wattpost.service had
    # no guard and crashed the build). Re-derive both vars when the
    # captured path is under LEGACY_SRC.
    case "${SCRIPT_DIR}" in
        "${LEGACY_SRC}"/*|"${LEGACY_SRC}")
            SCRIPT_DIR="${ACTIVE_SLOT}/src/packaging"
            REPO_ROOT="${ACTIVE_SLOT}/src"
            step "rewriting SCRIPT_DIR → ${SCRIPT_DIR} (pi-gen path migrated)"
            ;;
    esac
fi

chown -R "${APP_USER}:${APP_GROUP}" "${SLOTS_DIR}" "${STATE_DIR}"
chown "${APP_USER}:${APP_GROUP}" "${CONFIG_DIR}"

# Hotspot captive portal (Pillar 3b): NetworkManager reads dnsmasq
# drop-ins for shared (AP) connections from here. Let the wattpost user
# manage its catch-all drop-in so the captive portal can arm/disarm with
# the AP, without giving the daemon broader root. Best-effort: skip
# cleanly on hosts without NetworkManager (e.g. Docker installs), where
# captive simply stays a no-op.
NM_DNSMASQ_DIR=/etc/NetworkManager/dnsmasq-shared.d
if [[ -d /etc/NetworkManager ]]; then
    mkdir -p "${NM_DNSMASQ_DIR}"
    chgrp "${APP_GROUP}" "${NM_DNSMASQ_DIR}" 2>/dev/null || true
    chmod 0775 "${NM_DNSMASQ_DIR}" 2>/dev/null || true

    # Authorise the wattpost user to drive NetworkManager via polkit — needed
    # for the daemon's nmcli to add + activate the hotspot AP connection
    # (without it: "Insufficient privileges"). The dnsmasq drop-in perms above
    # aren't enough; the connection add/activate is gated by polkit, not files.
    if [ -f "${SCRIPT_DIR}/polkit/50-wattpost-networkmanager.rules" ] && [ -d /etc/polkit-1/rules.d ]; then
        install -m 0644 -o root -g root \
            "${SCRIPT_DIR}/polkit/50-wattpost-networkmanager.rules" \
            /etc/polkit-1/rules.d/50-wattpost-networkmanager.rules
        systemctl try-reload-or-restart polkit 2>/dev/null || true
    fi

    # `iw` powers the hotspot's connected-client count (status.client_count).
    # Pi OS Bookworm doesn't ship it by default, so without this the count is
    # permanently null on real Pis even though it works in the Docker image
    # (which apt-installs iw). The AP itself works regardless. Best-effort.
    if ! command -v iw >/dev/null 2>&1; then
        apt-get install -y iw || warn "couldn't install iw — hotspot client_count will be unavailable"
    fi
fi

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
# Two steps, because pyproject.toml hardcodes version="0.0.1" (the package
# metadata never bumps even when solar_monitor/__init__.py does):
#
# 1. Resolve + install the dependency tree (uvicorn/litestar/bleak/...).
#    On a FRESH install — the pi-gen image build, or a new manual install —
#    this is the ONLY step that pulls the deps into the venv. Without it the
#    daemon crash-loops on "ModuleNotFoundError: No module named 'uvicorn'"
#    (the line 220 `pip install --upgrade pip wheel` only touches pip+wheel,
#    NOT the app's deps). Idempotent: on an update, satisfied deps are
#    skipped and only newer ones are pulled.
"${APP_VENV}/bin/pip" install --prefer-binary --upgrade "${SOURCE}"
# 2. Force-reinstall the package code only. Because the version string is
#    frozen at 0.0.1, step 1 sees "already installed" and won't swap the
#    code on an update; this forces the new code in without re-touching the
#    (now-correct) dep tree — keeping wattpost-update fast.
"${APP_VENV}/bin/pip" install --prefer-binary --force-reinstall --no-deps "${SOURCE}"

# ----- config (only if not present — don't clobber the user's edits) -----
if [[ ! -f "${CONFIG_FILE}" ]]; then
    step "seeding ${CONFIG_FILE} from config.example.yaml"
    if [[ -f "${REPO_ROOT}/config.example.yaml" ]]; then
        cp "${REPO_ROOT}/config.example.yaml" "${CONFIG_FILE}"
    else
        cat > "${CONFIG_FILE}" <<'YAML'
# Edit via the Setup wizard at http://<this-pi>:8000/#/setup
# or by hand. See https://github.com/ritualnorth/wattpost
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

# ----- privileged ops: no sudo grants (#33) -----
# The wattpost daemon no longer escalates via sudo. The update / rollback
# helpers and the firewall/SSH helper are invoked by the root helper daemon
# (wattpost-helperd) over a group-restricted socket, and the daemon's unit is
# fully sandboxed (NoNewPrivileges), so sudo wouldn't work for it anyway.
# Remove any sudoers grants shipped by earlier versions — the auto-updater
# re-runs install.sh, so this is the migration path for existing appliances.
# (The helper *scripts* below are still installed; only the grants go.)
step "removing legacy sudo grants (privileged ops moved to the helper, #33)"
rm -f /etc/sudoers.d/wattpost
rm -f /etc/sudoers.d/wattpost-netctl
rm -f /etc/sudoers.d/wattpost-tailscale

# Pre-create the root-written, group-readable log files the daemon reads back
# via /api/system/update/log + /api/system/rollback/log. The helpers (root)
# write them; the daemon (group wattpost) reads. The helper *binaries* + units
# themselves are installed by the shared privileged-sync library below.
step "preparing update/rollback log files"
for _wp_log in /var/log/wattpost-update.log /var/log/wattpost-rollback.log; do
    touch "${_wp_log}"
    chgrp wattpost "${_wp_log}"
    chmod 0644 "${_wp_log}"
done

# ----- privileged host surface (#33): single source of truth -----
# Install the root-owned helpers that live OUTSIDE the slot — the privileged
# helper daemon + its socket/service units, the network-control helper, the
# tmpfiles entry, the rollback watchdog, and the admin CLIs (update, rollback,
# config) — via the shared library. wattpost-update sources the SAME library
# after a slot swap, so a fresh install and an in-place update can never drift.
# That drift is exactly what used to leave updated appliances on a stale
# wattpost-helperd ("unknown action: net_status").
#
# A small root service reached over a group-restricted Unix socket performs
# the fixed allow-list of privileged ops (firewall/SSH toggles, update,
# rollback, captive-portal DNS drop-in) so the main daemon needs no sudo and
# can run fully sandboxed.
if [ -f "${SCRIPT_DIR}/lib/wattpost-privileged-sync.sh" ]; then
    step "installing privileged helpers (daemon, units, CLIs)"
    # shellcheck source=/dev/null
    . "${SCRIPT_DIR}/lib/wattpost-privileged-sync.sh"
    wp_sync_privileged "${SCRIPT_DIR}"
fi

# ----- host network hardening: firewall + SSH control (cloud #15, Phase B) -----
# A root-owned, fixed-verb helper reconciles the inbound nftables firewall
# and sshd to the configured state (web.firewall_enabled / web.ssh_enabled).
# The unprivileged daemon drives it through wattpost-helperd over a socket
# (#33) — no sudo. The daemon applies this on every boot (cli.cmd_serve ->
# netsec.reconcile), so this step just puts the helper script + nft in place;
# the service restart at the end of install brings the ruleset up. Pi image
# only — on Docker/dev the daemon no-ops (netsec.is_supported() is False).
if [ -f "${SCRIPT_DIR}/sbin/wattpost-netctl" ]; then
    # wattpost-netctl itself is installed by wp_sync_privileged above; this
    # block just ensures its nftables backend is present and warns about the
    # SSH-off default before the daemon restart closes port 22.
    step "configuring host network control (firewall backend + SSH guard)"

    # nftables provides `nft`, the firewall backend the helper drives. The
    # daemon re-applies the ruleset every boot, so we need the binary, not
    # nftables.service persistence. Best-effort: a no-op firewall (helper
    # skips it) is harmless until nft lands.
    if ! command -v nft >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
        apt-get update -qq || true
        apt-get install -y nftables || warn "couldn't install nftables — firewall stays a no-op until it's present"
    fi

    # No sudoers grant (#33): the daemon reaches this helper through
    # wattpost-helperd, which runs it as root directly. Any legacy
    # /etc/sudoers.d/wattpost-netctl grant is removed in the sudo-grants
    # cleanup step earlier in this script.

    # Lockout guard. SSH defaults OFF, so the daemon will disable sshd and
    # close port 22 when it restarts at the end of this install. If you're
    # installing over SSH and need it, enable SSH first or you'll be cut off.
    if [ -n "${SSH_CONNECTION:-}" ]; then
        warn "you're connected over SSH, and SSH is OFF by default — the firewall will close port 22 when the daemon restarts."
        warn "to keep SSH: set web.ssh_enabled: true in ${CONFIG_FILE} before this finishes, or re-enable later from the local dashboard (Settings) / console."
    fi
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

# wattpost-config (the raspi-config-style "I just SSH'd in, now what?" admin
# menu) is installed by wp_sync_privileged above, alongside the other CLIs.

# ----- Pi 5 active-cooler fan curve -----
# Enable temperature-based control of the official FAN-connector fan so the
# appliance cools itself with zero user setup. Without this a Pi 5 + cooler
# can ship with no automatic fan control (fan never spins, SoC runs hot).
# Values are millidegrees-C : PWM speed (0-255), matching Raspberry Pi's own
# defaults. Idempotent (guarded by the marker) and a no-op on non-Pi hosts /
# Docker (no config.txt). Takes effect on the next boot.
for FAN_CFG in /boot/firmware/config.txt /boot/config.txt; do
    if [ -f "${FAN_CFG}" ] && ! grep -q 'wattpost-fan-curve' "${FAN_CFG}"; then
        step "enabling Pi 5 fan curve in ${FAN_CFG}"
        cat >> "${FAN_CFG}" <<'FANEOF'

# wattpost-fan-curve: Pi 5 active-cooler temperature control (auto, variable speed)
dtparam=fan_temp0=50000,fan_temp0_hyst=5000,fan_temp0_speed=75
dtparam=fan_temp1=60000,fan_temp1_hyst=5000,fan_temp1_speed=125
dtparam=fan_temp2=67500,fan_temp2_hyst=5000,fan_temp2_speed=175
dtparam=fan_temp3=75000,fan_temp3_hyst=5000,fan_temp3_speed=250
FANEOF
        break
    fi
done

# ----- systemd unit -----
step "installing wattpost.service"
install -m 0644 "${SCRIPT_DIR}/systemd/wattpost.service" "${SERVICE_DEST}"
systemctl daemon-reload
systemctl enable wattpost.service >/dev/null
systemctl restart wattpost.service

# ----- mDNS hostname -----
# Advertise the box as `wattpost.local` so it's reachable by name on the LAN
# without knowing its IP — and on its own hotspot too, since avahi answers on
# every interface, so a phone joined to the AP resolves wattpost.local ->
# 10.42.0.1. Sets avahi's advertised name only; the system hostname is left
# alone. Pi-style installs only (Docker hosts are reached by their own host
# address, no mDNS needed).
step "configuring mDNS (wattpost.local)"
if ! command -v avahi-daemon >/dev/null 2>&1; then
    apt-get install -y avahi-daemon || warn "couldn't install avahi-daemon — wattpost.local won't resolve"
fi
if [ -f /etc/avahi/avahi-daemon.conf ]; then
    if grep -qE '^#*host-name=' /etc/avahi/avahi-daemon.conf; then
        sed -i 's/^#*host-name=.*/host-name=wattpost/' /etc/avahi/avahi-daemon.conf
    else
        sed -i '/^\[server\]/a host-name=wattpost' /etc/avahi/avahi-daemon.conf
    fi
    systemctl enable avahi-daemon >/dev/null 2>&1 || true
    systemctl restart avahi-daemon || warn "avahi restart failed — wattpost.local may not resolve until reboot"
fi

# ----- zram swap (low-RAM headroom) -----
# Compressed RAM swap so a memory spike can't OOM-kill the daemon on a
# memory-tight board. It's the difference between viable and not on a 512 MB
# Pi Zero 2 W, and harmless on bigger Pis: zram only consumes RAM as pages are
# actually swapped to it, so on a box with spare RAM it sits idle. zstd
# compresses cold pages by roughly 2-3x, giving a small board real effective
# headroom without a disk swapfile (no SD-card wear). Pi-only; a Docker host
# manages its own swap.
step "configuring zram swap"
if ! dpkg -s zram-tools >/dev/null 2>&1; then
    apt-get install -y zram-tools || warn "couldn't install zram-tools — no swap cushion on low-RAM Pis"
fi
if dpkg -s zram-tools >/dev/null 2>&1; then
    zcfg=/etc/default/zramswap
    touch "$zcfg"
    if grep -qE '^#*ALGO=' "$zcfg"; then sed -i 's/^#*ALGO=.*/ALGO=zstd/' "$zcfg"; else echo 'ALGO=zstd' >> "$zcfg"; fi
    if grep -qE '^#*PERCENT=' "$zcfg"; then sed -i 's/^#*PERCENT=.*/PERCENT=50/' "$zcfg"; else echo 'PERCENT=50' >> "$zcfg"; fi
    systemctl enable zramswap.service >/dev/null 2>&1 || true
    # The restart fails harmlessly during the pi-gen image build (no running
    # systemd in the chroot); it's enabled above, so it comes up on first boot.
    systemctl restart zramswap.service 2>/dev/null || warn "zram swap will start on next boot"
fi

# ----- wall-display kiosk launcher + unit (#8) -----
# Install the tiny launcher + systemd unit for driving an attached HDMI /
# touchscreen panel. The unit is ENABLED but self-gates — it exits 0 unless
# kiosk.display_enabled is set AND a panel is present — so a headless box is
# unaffected. We deliberately do NOT apt-install the heavy display stack
# (cage + seatd + chromium, hundreds of MB) here: most appliances are
# headless and would never use it. Enabling the panel installs the stack as
# an opt-in step (see docs/kiosk-display.md). Pi-only; harmless if dirs exist.
if [ -d /etc/systemd/system ] && [ -f "${SCRIPT_DIR}/cli/wattpost-kiosk-display" ]; then
    step "installing wall-display kiosk launcher + unit"
    install -m 0755 "${SCRIPT_DIR}/cli/wattpost-kiosk-display" \
        /usr/local/bin/wattpost-kiosk-display 2>/dev/null \
        || warn "kiosk-display launcher install failed (wall display unavailable)"
    if [ -f "${SCRIPT_DIR}/systemd/wattpost-kiosk-display.service" ]; then
        install -m 0644 "${SCRIPT_DIR}/systemd/wattpost-kiosk-display.service" \
            /etc/systemd/system/wattpost-kiosk-display.service
        systemctl daemon-reload
        # Safe to enable everywhere: the launcher exits 0 on a box with the
        # feature off or no panel, so systemd never restarts it there.
        systemctl enable wattpost-kiosk-display.service >/dev/null 2>&1 || true
    fi
fi

# ----- prefer IPv4 for outbound -----
# Many home routers hand out a global IPv6 address via SLAAC but don't
# actually route IPv6 to the internet. RFC 6724 then makes glibc prefer
# IPv6, so the daemon's outbound calls (cloud pairing, heartbeat, update
# checks via httpx) try the dead IPv6 path and time out — pairing fails
# with a bare "could not reach https://wattpost.cloud:". curl masks it
# with Happy-Eyeballs IPv4 fallback; httpx has none. Pin IPv4 ahead of
# IPv6 in the resolver so dual-stack hosts use the working path. v6-only
# destinations still go over v6 (this changes preference, not capability).
# Idempotent.
step "preferring IPv4 for outbound (broken-IPv6 networks)"
if ! grep -q '^precedence ::ffff:0:0/96' /etc/gai.conf 2>/dev/null; then
    echo 'precedence ::ffff:0:0/96  100' >> /etc/gai.conf
fi

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

    http://wattpost.local${PORT_SUFFIX}/        (works on any device on this network)
    http://${IP:-<this-pi>}${PORT_SUFFIX}/        (by IP, if .local doesn't resolve)

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
