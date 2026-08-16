#!/usr/bin/env bash
# Compila el toolchain AVR nativo para macOS arm64, igual que el workflow.
set -euo pipefail
BINUTILS=2.42
GCC=14.2.0
AVRLIBC=2.2.0

ROOT=/tmp/avrtc
PREFIX=$ROOT/toolchain-staged
export MAKEFLAGS="-j$(sysctl -n hw.ncpu)"
# Release: -O2 sin -g (por defecto autotools usa "-g -O2" y eso es la mayor
# parte de los 573 MB del arbol) y sin las comprobaciones internas de gcc.
export CFLAGS="-O2"
export CXXFLAGS="-O2"

mkdir -p "$ROOT/src" "$ROOT/build"
cd "$ROOT/src"

echo "=== [1/6] descargando fuentes"
[ -f binutils-$BINUTILS.tar.xz ] || curl -fLO "https://ftp.gnu.org/gnu/binutils/binutils-$BINUTILS.tar.xz"
[ -f gcc-$GCC.tar.xz ]           || curl -fLO "https://ftp.gnu.org/gnu/gcc/gcc-$GCC/gcc-$GCC.tar.xz"
TAG="${AVRLIBC//./_}"
[ -f avr-libc-$AVRLIBC.tar.bz2 ] || curl -fLO "https://github.com/avrdudes/avr-libc/releases/download/avr-libc-${TAG}-release/avr-libc-$AVRLIBC.tar.bz2"
[ -d binutils-$BINUTILS ] || tar xf binutils-$BINUTILS.tar.xz
[ -d gcc-$GCC ]           || tar xf gcc-$GCC.tar.xz
[ -d avr-libc-$AVRLIBC ]  || tar xf avr-libc-$AVRLIBC.tar.bz2

echo "=== [2/6] binutils"
mkdir -p "$ROOT/build/binutils" && cd "$ROOT/build/binutils"
if [ ! -f .done ]; then
  "$ROOT/src/binutils-$BINUTILS/configure" --target=avr --prefix="$PREFIX" \
      --disable-nls --disable-werror --disable-shared --enable-static --with-system-zlib --without-zstd \
      MAKEINFO=true
  make MAKEINFO=true
  make install MAKEINFO=true
  touch .done
fi

echo "=== [3/6] prerrequisitos de gcc (gmp/mpfr/mpc en el arbol, estaticos)"
cd "$ROOT/src/gcc-$GCC" && [ -d gmp ] || ./contrib/download_prerequisites

echo "=== [4/6] gcc"
export PATH="$PREFIX/bin:$PATH"
mkdir -p "$ROOT/build/gcc" && cd "$ROOT/build/gcc"
if [ ! -f .done ]; then
  "$ROOT/src/gcc-$GCC/configure" --target=avr --prefix="$PREFIX" \
      --enable-checking=release \
      --enable-languages=c,c++ --disable-nls --disable-libssp --disable-libada \
      --disable-shared --with-dwarf2 --with-system-zlib \
      --with-double=32 --with-long-double=32 \
      MAKEINFO=missing
  make
  make install
  touch .done
fi

echo "=== [5/6] avr-libc"
mkdir -p "$ROOT/build/avr-libc" && cd "$ROOT/build/avr-libc"
if [ ! -f .done ]; then
  "$ROOT/src/avr-libc-$AVRLIBC/configure" --prefix="$PREFIX" --host=avr \
      --build=aarch64-apple-darwin
  make
  make install
  touch .done
fi

echo "=== [5.5/6] recorte"
# Documentacion, manuales, locales y cabeceras de plugins: nada de esto lo usa
# PyMCU, que solo ensambla, enlaza y convierte a hex.
rm -rf "$PREFIX/share/man" "$PREFIX/share/info" "$PREFIX/share/doc" \
       "$PREFIX/share/locale" "$PREFIX/lib/gcc/avr/14.2.0/plugin" 2>/dev/null || true
echo "  arbol: $(du -sh "$PREFIX" | awk '{print $1}')"

echo "=== [6/6] verificacion"
cd "$PREFIX"
for b in avr-gcc avr-as avr-objcopy; do
  file "bin/$b"
  file "bin/$b" | grep -q arm64 || { echo "FALLO: $b no es arm64"; exit 1; }
  otool -L "bin/$b" | grep -q /opt/homebrew && { echo "FALLO: $b enlaza Homebrew"; exit 1; }
done
test -f avr/lib/libm.a && echo "libm.a OK"
./bin/avr-gcc -mmcu=atmega328p --print-libgcc-file-name
# OJO al empaquetar: usar tar (o cp -a). Algunos ejecutables son el MISMO
# fichero enlazado duro (avr-gcc y avr-gcc-14.2.0), y un cp -R los duplica.
tar cJf "$ROOT/avr-toolchain-macos-arm64.tar.xz" -C "$(dirname "$PREFIX")" "$(basename "$PREFIX")"
echo "=== TOOLCHAIN NATIVO ARM64 CONSTRUIDO EN $PREFIX"
echo "    paquete: $ROOT/avr-toolchain-macos-arm64.tar.xz ($(du -h "$ROOT/avr-toolchain-macos-arm64.tar.xz" | awk '{print $1}'))"
