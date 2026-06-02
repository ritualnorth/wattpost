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

echo "[stage-wattpost/00-copy-source] HERE=${HERE}"
echo "[stage-wattpost/00-copy-source] SRC=${SRC}"
echo "[stage-wattpost/00-copy-source] ROOTFS_DIR=${ROOTFS_DIR:-<unset>}"

if [ ! -d "${SRC}" ]; then
    echo "WattPost source not staged at ${SRC} — did build-image.sh run?" >&2
    echo "  HERE listing:" >&2
    ls -la "${HERE}/" >&2 || true
    exit 1
fi

echo "[stage-wattpost/00-copy-source] SRC contents (top level):"
ls -la "${SRC}/" | head -20

# IMPORTANT: stage the source under /opt, NOT /tmp. Pi-gen mounts a
# fresh tmpfs over /tmp when entering the chroot for *-chroot.sh
# scripts, so anything we drop in ROOTFS_DIR/tmp/ is invisible from
# inside the chroot (we hit this bug in pi-gen #15–#17). /opt is a
# plain directory with no special mount behaviour, so files survive
# the chroot transition.
DEST="${ROOTFS_DIR}/opt/wattpost-src"
mkdir -p "${DEST}"
rsync -a --delete \
      --exclude '__pycache__/' \
      --exclude '*.egg-info/' \
      "${SRC}/" "${DEST}/"

echo "[stage-wattpost/00-copy-source] DEST=${DEST} after rsync:"
ls -la "${DEST}/" | head -20
echo "[stage-wattpost/00-copy-source] DEST sanity: packaging/ dir present? $([ -d "${DEST}/packaging" ] && echo YES || echo NO)"
echo "[stage-wattpost/00-copy-source] DEST file count: $(find "${DEST}" -type f | wc -l)"
