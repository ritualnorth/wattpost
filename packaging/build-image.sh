#!/usr/bin/env bash
# Build a WattPost SD image via pi-gen.
#
# Requirements on the build host (Debian / Ubuntu x86_64 or arm64):
#   sudo apt install -y git quilt parted qemu-user-static debootstrap zerofree \
#       zip dosfstools libcap2-bin grep rsync xz-utils file kmod bc gpg pigz
#
# Usage:
#   ./packaging/build-image.sh
#
# Produces ./build/deploy/wattpost-YYYY-MM-DD-wattpost-lite.img.xz
# in ~1–2 hours.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ROOT="${REPO_ROOT}/build"
PIGEN_DIR="${BUILD_ROOT}/pi-gen"
STAGE_NAME="stage-wattpost"

mkdir -p "${BUILD_ROOT}"

if [ ! -d "${PIGEN_DIR}" ]; then
    echo "==> cloning pi-gen"
    git clone --depth 1 --branch arm64 https://github.com/RPi-Distro/pi-gen "${PIGEN_DIR}"
fi

# Ubuntu workaround: pi-gen's `depends` file lists both
# qemu-user-binfmt and qemu-user-static. On Debian/Raspbian these
# coexist; on Ubuntu they Conflict (both register the same binfmt
# handlers — apt refuses to install both). qemu-user-static alone
# provides the static qemu binaries pi-gen actually uses, plus
# auto-registers binfmt via update-binfmts in its postinst.
#
# pi-gen's depends file uses either `package` or `package:command`
# per line; match both forms when stripping. Also print what we did
# so future failures are easier to diagnose from the CI log.
if [ -f "${PIGEN_DIR}/depends" ]; then
    echo "==> pi-gen depends BEFORE strip:"
    grep -nE '^(qemu|binfmt)' "${PIGEN_DIR}/depends" || true
    sed -i -E '/^qemu-user-binfmt(:|$)/d' "${PIGEN_DIR}/depends"
    echo "==> pi-gen depends AFTER strip:"
    grep -nE '^(qemu|binfmt)' "${PIGEN_DIR}/depends" || echo "  (no qemu/binfmt lines)"
fi

# Link our stage into pi-gen's stages dir.
ln -snf "${REPO_ROOT}/packaging/pi-gen/${STAGE_NAME}" "${PIGEN_DIR}/${STAGE_NAME}"

# Skip the desktop stages (3, 4, 5) and use lite (stage 2) as the base
# our stage builds on. SKIP_IMAGES on stages we don't ship.
cat > "${PIGEN_DIR}/config" <<EOF
IMG_NAME=wattpost
RELEASE=bookworm
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

echo "==> running pi-gen build (~1–2 hours)"
cd "${PIGEN_DIR}"
sudo -E ./build.sh

echo
echo "==> done"
ls -la "${PIGEN_DIR}/deploy/" 2>/dev/null || true
