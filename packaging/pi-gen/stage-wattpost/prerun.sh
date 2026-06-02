#!/bin/bash -e
# Pi-gen stage entry. Copies the stage3 root as our base (Pi OS Lite
# with networking + raspi-config), then our 00-install step bolts the
# WattPost daemon + systemd unit on top.
if [ ! -d "${ROOTFS_DIR}" ]; then
    copy_previous
fi
