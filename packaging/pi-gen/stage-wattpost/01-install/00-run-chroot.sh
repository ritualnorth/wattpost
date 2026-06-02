#!/bin/bash -e
# Runs INSIDE the image's chroot during pi-gen build. Installs the
# WattPost daemon into /opt/wattpost, drops the systemd unit, and
# enables it so first boot comes up serving the dashboard.
#
# We invoke the same install.sh that hobbyists use for a manual install
# so there's a single source of truth for the install logic.

# /opt/wattpost-src is rsync'd in by the parent stage's
# 00-copy-source/00-run.sh. Using /opt (not /tmp) because pi-gen
# mounts a fresh tmpfs over /tmp on chroot entry.
INSTALL_DIR=/opt/wattpost-src

if [ ! -d "${INSTALL_DIR}/packaging" ]; then
    echo "WattPost source not staged at ${INSTALL_DIR}" >&2
    exit 1
fi

cd "${INSTALL_DIR}"
WATTPOST_SOURCE="${INSTALL_DIR}" bash packaging/install.sh

# The pi-gen image build doesn't have BlueZ running, so the install
# script's `systemctl restart wattpost` call would fail. Disable + don't
# start now; enable for first boot.
systemctl disable --now wattpost.service 2>/dev/null || true
systemctl enable wattpost.service

# Keep the source tree around at /opt/wattpost-src on the running
# image — this is the same tree the Update-now button's helper
# operates on (atomic-swap a new tarball into place + re-run
# install.sh). Don't rm it; that would break in-place upgrades.
