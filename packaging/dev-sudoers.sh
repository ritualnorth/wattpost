#!/usr/bin/env bash
# Dev helper: grant the invoking user the same passwordless tailscale
# access that install.sh grants the production `wattpost` system user.
#
# Run when developing the daemon outside the systemd-installed layout —
# e.g. `python -m solar_monitor.cli serve` from your checkout — so that
# Settings → Network's Connect / Disconnect / Enable HTTPS buttons can
# call `sudo -n tailscale ...` without being prompted for a password.
#
# Usage:
#   sudo bash packaging/dev-sudoers.sh            # grants $SUDO_USER
#   sudo bash packaging/dev-sudoers.sh alice      # grants `alice`
#
# Idempotent — re-running just rewrites the same fragment.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "dev-sudoers.sh must run as root (sudo bash $0)" >&2
    exit 1
fi

# Caller supplied a username, else use the original invoking user
# (SUDO_USER, which is set by sudo to the logging-in account).
TARGET_USER="${1:-${SUDO_USER:-}}"
if [[ -z "$TARGET_USER" ]]; then
    echo "Could not determine target user — pass one explicitly:" >&2
    echo "  sudo bash $0 <username>" >&2
    exit 1
fi

if ! id "$TARGET_USER" >/dev/null 2>&1; then
    echo "User '$TARGET_USER' does not exist on this system." >&2
    exit 1
fi

TAILSCALE_BIN="$(command -v tailscale || true)"
if [[ -z "$TAILSCALE_BIN" ]]; then
    echo "tailscale is not installed. Install it first:" >&2
    echo "  curl -fsSL https://tailscale.com/install.sh | sh" >&2
    exit 1
fi

# Sanitise so the filename can't collide with the production fragment
# or contain anything visudo dislikes.
SAFE_USER="${TARGET_USER//[^A-Za-z0-9._-]/_}"
SUDOERS_FILE="/etc/sudoers.d/wattpost-tailscale-dev-${SAFE_USER}"

# Same three commands install.sh permits for the production user.
cat > "${SUDOERS_FILE}.tmp" <<SUDO
# WattPost dev: allow ${TARGET_USER} to manage Tailscale from the
# dashboard's Settings -> Network block while running the daemon from
# a development checkout.
${TARGET_USER} ALL=(root) NOPASSWD: ${TAILSCALE_BIN} up *, ${TAILSCALE_BIN} logout, ${TAILSCALE_BIN} serve *
SUDO

if ! visudo -cf "${SUDOERS_FILE}.tmp" >/dev/null; then
    echo "visudo rejected the generated fragment — refusing to install." >&2
    rm -f "${SUDOERS_FILE}.tmp"
    exit 1
fi

install -m 0440 "${SUDOERS_FILE}.tmp" "${SUDOERS_FILE}"
rm -f "${SUDOERS_FILE}.tmp"

echo "Installed ${SUDOERS_FILE} for user '${TARGET_USER}'."
echo "Sudo cache may need ${TARGET_USER} to run any \`sudo\` command once;"
echo "after that Settings -> Network's buttons will work without a prompt."
