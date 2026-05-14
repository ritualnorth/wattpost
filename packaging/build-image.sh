#!/usr/bin/env bash
# Build a WattPost SD image via pi-gen.
#
# Uses pi-gen's Docker-mode build (build-docker.sh) — runs the whole
# build inside a Debian-based container so we don't have to fight the
# host-deps mismatch when running on Ubuntu (different qemu package
# split, missing Debian archive keys, etc). The container has it all
# baked in.
#
# Host requirements (Debian / Ubuntu, anything with Docker really):
#   docker  (CLI + dockerd, including buildx)
#   git
#   sudo    (build-docker.sh needs it for loopback mount + chroot)
#
# Usage:
#   ./packaging/build-image.sh
#
# Produces ./build/pi-gen/deploy/*.img.xz in ~1–2 hours.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ROOT="${REPO_ROOT}/build"
PIGEN_DIR="${BUILD_ROOT}/pi-gen"
STAGE_NAME="stage-wattpost"

mkdir -p "${BUILD_ROOT}"

if [ ! -d "${PIGEN_DIR}" ]; then
    echo "==> cloning pi-gen (arm64 branch — pi-gen still splits 32/64-bit by branch)"
    git clone --depth 1 --branch arm64 https://github.com/RPi-Distro/pi-gen "${PIGEN_DIR}"
fi

# Make sure binfmt_misc is registered for arm64 on the host. Pi-gen's
# Docker container does the heavy lifting, but the host kernel needs
# binfmt registrations so foreign binaries route through qemu inside
# the privileged container.
echo "==> registering qemu binfmt handlers (host kernel)"
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes >/dev/null 2>&1 || true

# pi-gen's build-docker.sh does its own host preflight: looks for
# `qemu-aarch64` on PATH. On Ubuntu the qemu-user-static package
# provides the `-static` suffix only, so symlink that satisfies the
# check without installing a conflicting non-static package.
sudo ln -sf /usr/bin/qemu-aarch64-static /usr/bin/qemu-aarch64 2>/dev/null || true
sudo ln -sf /usr/bin/qemu-arm-static     /usr/bin/qemu-arm     2>/dev/null || true

# Defensive: rewrite the Signed-By path in stage0/debian.sources to the
# .gpg filename (present in every Debian release since at least
# bookworm). Pi-gen ships `.pgp` which only exists on Trixie+; the
# rewrite makes this wrapper robust if we ever switch RELEASE backwards
# again or pi-gen rolls forward off Trixie. No-op when both files exist.
sed -i 's|debian-archive-keyring\.pgp|debian-archive-keyring.gpg|g' \
    "${PIGEN_DIR}/stage0/00-configure-apt/files/debian.sources"

# Link our stage into pi-gen's stages dir.
ln -snf "${REPO_ROOT}/packaging/pi-gen/${STAGE_NAME}" "${PIGEN_DIR}/${STAGE_NAME}"

# Skip the desktop stages (3, 4, 5) and use lite (stage 2) as the base
# our stage builds on. SKIP_IMAGES on stages we don't ship.
cat > "${PIGEN_DIR}/config" <<EOF
IMG_NAME=wattpost
RELEASE=trixie
DEPLOY_COMPRESSION=xz
LOCALE_DEFAULT=en_GB.UTF-8
TIMEZONE_DEFAULT=Europe/London
TARGET_HOSTNAME=wattpost
FIRST_USER_NAME=wattpost
FIRST_USER_PASS=wattpost   # user must change on first login — TODO swap for a prompt
DISABLE_FIRST_BOOT_USER_RENAME=1
ENABLE_SSH=1
STAGE_LIST="stage0 stage1 stage2 ${STAGE_NAME}"
EOF

# Pass the source path to the staged 00-copy-source script.
export WATTPOST_SRC="${REPO_ROOT}"

echo "==> running pi-gen build via Docker (~1–2 hours)"
cd "${PIGEN_DIR}"
# build-docker.sh builds (or pulls) a pre-baked pigen-builder image
# that has the right Debian keyrings + tools, then runs the actual
# build inside it. Stops + restarts cleanly between stages.
# CONTAINER_NAME pinned so re-runs reuse the same docker container
# (faster cached apt + debootstrap on re-builds).
sudo CONTAINER_NAME=pigen-builder -E ./build-docker.sh

echo
echo "==> done"
ls -la "${PIGEN_DIR}/deploy/" 2>/dev/null || true
