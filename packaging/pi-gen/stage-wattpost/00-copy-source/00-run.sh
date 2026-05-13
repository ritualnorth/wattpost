#!/bin/bash -e
# Stage the WattPost source tree into the chroot so 00-install/
# 00-run-chroot.sh can pip-install it. WATTPOST_SRC defaults to the
# repo root that contains this packaging/ directory.

: "${WATTPOST_SRC:?WATTPOST_SRC env var must point at the WattPost checkout}"

mkdir -p "${ROOTFS_DIR}/tmp/wattpost-src"
# rsync skips .git, __pycache__, the dev .venv, and any local databases
# so we don't bloat the chroot.
rsync -a --delete \
      --exclude '.git/' \
      --exclude '.venv/' \
      --exclude '__pycache__/' \
      --exclude '*.egg-info/' \
      --exclude 'config.yaml' \
      --exclude 'solar-monitor.db*' \
      "${WATTPOST_SRC}/" "${ROOTFS_DIR}/tmp/wattpost-src/"
