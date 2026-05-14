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

# binfmt_misc is registered by docker/setup-qemu-action in CI (qemu 9.x
# from tonistiigi/binfmt:latest, which has the arm64 fixes Python 3.13
# needs). For local invocations without that step run, fall back to
# multiarch/qemu-user-static so the script still works on a dev box —
# tag the fallback so a green-line CI run isn't subject to the older
# qemu version.
if ! grep -q "qemu-aarch64" /proc/sys/fs/binfmt_misc/qemu-aarch64* 2>/dev/null; then
    echo "==> registering qemu binfmt handlers (host kernel — local fallback)"
    docker run --rm --privileged tonistiigi/binfmt:latest --install arm64,arm >/dev/null 2>&1 \
        || docker run --rm --privileged multiarch/qemu-user-static --reset -p yes >/dev/null 2>&1 \
        || true
fi

# pi-gen's build-docker.sh does its own host preflight: looks for
# `qemu-aarch64` on PATH. tonistiigi/binfmt puts its qemu under a
# different path so symlink the apt-shipped static binary as a
# fallback (it's still installed as a build-host dep).
if command -v qemu-aarch64-static >/dev/null; then
    sudo ln -sf "$(command -v qemu-aarch64-static)" /usr/bin/qemu-aarch64 2>/dev/null || true
fi
if command -v qemu-arm-static >/dev/null; then
    sudo ln -sf "$(command -v qemu-arm-static)" /usr/bin/qemu-arm 2>/dev/null || true
fi

# Defensive: rewrite the Signed-By path in stage0/debian.sources to the
# .gpg filename (present in every Debian release since at least
# bookworm). Pi-gen ships `.pgp` which only exists on Trixie+; the
# rewrite makes this wrapper robust if we ever switch RELEASE backwards
# again or pi-gen rolls forward off Trixie. No-op when both files exist.
sed -i 's|debian-archive-keyring\.pgp|debian-archive-keyring.gpg|g' \
    "${PIGEN_DIR}/stage0/00-configure-apt/files/debian.sources"

# Copy our stage into pi-gen's tree. Symlinking doesn't work in Docker
# mode — pi-gen's Dockerfile does `COPY . /pi-gen/` which can't follow
# a symlink whose target lives outside the build context, so when the
# build sequence reaches `stage-wattpost` it sees a dangling path and
# `realpath` aborts. Plain copy bakes the stage into the image.
rm -rf "${PIGEN_DIR}/${STAGE_NAME}"
cp -r  "${REPO_ROOT}/packaging/pi-gen/${STAGE_NAME}" "${PIGEN_DIR}/${STAGE_NAME}"

# Also stage the WattPost source tree inside pi-gen so it gets baked
# into the container image. The original 00-copy-source script relied
# on a $WATTPOST_SRC host path that's invisible to the Docker build;
# we now ship the source inside the stage and rsync from a known
# in-container location.
SRC_STAGE_DIR="${PIGEN_DIR}/${STAGE_NAME}/00-copy-source/wattpost-src"
echo "==> staging WattPost source from ${REPO_ROOT} → ${SRC_STAGE_DIR}"
rm -rf "${SRC_STAGE_DIR}"
mkdir -p "${SRC_STAGE_DIR}"
rsync -a \
      --exclude '.git/' \
      --exclude '.venv/' \
      --exclude '__pycache__/' \
      --exclude '*.egg-info/' \
      --exclude 'build/' \
      --exclude 'config.yaml' \
      --exclude 'solar-monitor.db*' \
      --exclude '.claude/' \
      "${REPO_ROOT}/" "${SRC_STAGE_DIR}/"
# Sanity: bail loudly if we didn't actually stage anything — the
# chroot-side install.sh chokes on an empty source tree with a less
# obvious "/tmp/wattpost-src not found" later.
if [ ! -f "${SRC_STAGE_DIR}/pyproject.toml" ]; then
    echo "ERROR: WattPost source staging failed —" >&2
    echo "  ${SRC_STAGE_DIR}/pyproject.toml is missing." >&2
    echo "  REPO_ROOT contents:" >&2
    ls -la "${REPO_ROOT}/" | head -20 >&2
    echo "  Staging dir contents:" >&2
    ls -la "${SRC_STAGE_DIR}/" | head -20 >&2
    exit 1
fi
echo "    staged $(du -sh "${SRC_STAGE_DIR}" | awk '{print $1}'), $(find "${SRC_STAGE_DIR}" -type f | wc -l) files"

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
