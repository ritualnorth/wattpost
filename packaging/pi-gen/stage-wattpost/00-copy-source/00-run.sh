#!/bin/bash -e
# Stage the WattPost source tree into the chroot so 01-install/
# 00-run-chroot.sh can pip-install it.
#
# The source was baked into this stage at host-side build setup
# (packaging/build-image.sh rsync-copies the repo into
# `wattpost-src/`). That keeps the source available inside the
# pi-gen Docker container without needing a host-volume mount or
# env-var-passed path.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${HERE}/wattpost-src"

if [ ! -d "${SRC}" ]; then
    echo "WattPost source not staged at ${SRC} — did build-image.sh run?" >&2
    exit 1
fi

mkdir -p "${ROOTFS_DIR}/tmp/wattpost-src"
rsync -a --delete \
      --exclude '__pycache__/' \
      --exclude '*.egg-info/' \
      "${SRC}/" "${ROOTFS_DIR}/tmp/wattpost-src/"
