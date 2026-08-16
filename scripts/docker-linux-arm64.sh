#!/usr/bin/env bash
set -euo pipefail

GCC_VERSION="${AVRT_GCC_VERSION:-14.2.0}"

apt-get update -qq
apt-get install -y -qq build-essential curl xz-utils bzip2 file wget python3 >/dev/null

export AVR_BUILD_ROOT=/build
mkdir -p /build
bash /work/scripts/build-avr-linux.sh
cp /build/avr-toolchain-linux-$(uname -m).tar.xz /out/

echo "=== wheel"
curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
export PATH="/root/.local/bin:$PATH"
cp -a /work/python /build/python
cd /build/python
AVRT_TOOLCHAIN_DIR=/build/toolchain-staged \
WHEEL_PLATFORM_TAG=${WHEEL_PLATFORM_TAG:-manylinux_2_17_aarch64} \
AVRT_GCC_VERSION="$GCC_VERSION" \
  uv build --wheel -o /out

ls -lh /out/
