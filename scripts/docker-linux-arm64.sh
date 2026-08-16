#!/usr/bin/env bash
set -euo pipefail
apt-get update -qq
apt-get install -y -qq build-essential curl xz-utils bzip2 file wget >/dev/null
# Se compila en el disco del contenedor (sensible a mayusculas) y solo se saca
# el paquete final al directorio montado.
export AVR_BUILD_ROOT=/build
mkdir -p /build
bash /work/build-avr-linux-arm64.sh
cp /build/avr-toolchain-linux-aarch64.tar.xz /out/ 2>/dev/null || cp /build/*.tar.xz /out/ 2>/dev/null || true
ls -lh /out/
