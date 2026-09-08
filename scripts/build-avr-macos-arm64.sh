#!/usr/bin/env bash
set -euo pipefail
BINUTILS=${BINUTILS_VERSION:-2.42}
GCC=${GCC_VERSION:-14.2.0}
AVRLIBC=${AVRLIBC_VERSION:-2.2.0}

ROOT="${AVR_BUILD_ROOT:-/tmp/avrtc}"
PREFIX=$ROOT/toolchain-staged
export MAKEFLAGS="-j$(sysctl -n hw.ncpu)"
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

[ -d binutils-$BINUTILS ] || tar xf binutils-$BINUTILS.tar.xz
[ -d gcc-$GCC ]           || tar xf gcc-$GCC.tar.xz
[ -d avr-libc-$AVRLIBC ]  || tar xf avr-libc-$AVRLIBC.tar.bz2

# gcc/system.h includes <memory> AFTER it poisons the ctype macros via
# safe-ctype.h, and libstdc++ 16.2 made <memory> pull in <bits/locale_facets.h>
# transitively (memory -> unique_ptr.h -> ostream.h -> ios -> basic_ios.h). That
# header declares toupper(char_type*, const char_type*), so the one-argument
# poison macro fires on it and the whole ctype family fails to parse:
#
#   locale_facets.h:252: error: macro 'toupper' passed 2 arguments, but takes just 1
#   safe-ctype.h:146: note: macro 'toupper' defined here
#
# system.h line 197 already says "Include C++ standard headers before
# safe-ctype.h to avoid GCC poisoning the ctype macros", and does it for a dozen
# headers. <memory> is simply on the wrong side of that line. Upstream moved it
# in gcc 15, so this is a backport of their fix, not an invention.
#
# Chosen over pinning the MSYS2 host gcc, which only resets the timer (pacman has
# no real version pinning and the host keeps rolling), and over moving to gcc 15
# sources, which would change the shipped product: 14.2.0 paired with avr-libc
# 2.2.0 is the point of this release. This removes the coupling instead, so the
# build stops caring which libstdc++ the runner ships.
#
# DROP THIS when GCC_VERSION reaches 15 or later; the fix is already in those
# sources and the patch will stop applying, which is the failure we want.
# The guard tests the ORDER, not the presence. system.h already contains a
# `# include <memory>` further down, inside the #ifdef INCLUDE_MEMORY block that
# is the bug, so a presence check reports "already patched" and skips, failing
# the build identically but with a reassuring message.
if ! awk '/^# include <memory>$/ && !m { m = NR }
          /^#include "safe-ctype.h"/ && !s { s = NR }
          END { exit !(m && s && m < s) }' "$ROOT/src/gcc-$GCC/gcc/system.h"; then
  echo "  patching gcc/system.h: <memory> before safe-ctype.h (upstream gcc 15 fix)"
  patch -p1 -d "$ROOT/src/gcc-$GCC" <<'PATCH'
--- a/gcc/system.h
+++ b/gcc/system.h
@@ -222,6 +222,7 @@
 #ifdef INCLUDE_FUNCTIONAL
 # include <functional>
 #endif
+# include <memory>
 # include <cstring>
 # include <initializer_list>
 # include <new>
PATCH
fi

echo "=== [4/6] gcc"
export PATH="$PREFIX/bin:$PATH"
mkdir -p "$ROOT/build/gcc" && cd "$ROOT/build/gcc"
if [ ! -f .done ]; then
  "$ROOT/src/gcc-$GCC/configure" --target=avr --prefix="$PREFIX" \
      --enable-checking=release \
      --enable-languages=c,c++ --disable-nls --disable-libssp --disable-libada \
      --disable-shared --with-dwarf2 --with-system-zlib \
      --with-double=32 --with-long-double=32 \
      MAKEINFO=true
  make MAKEINFO=true || { echo "reintentando en serie (carrera de libgcc)"; make -j1 MAKEINFO=true; }
  make install MAKEINFO=true
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
rm -rf "$PREFIX/share/man" "$PREFIX/share/info" "$PREFIX/share/doc" \
       "$PREFIX/share/locale" "$PREFIX/lib/gcc/avr/$GCC/plugin" 2>/dev/null || true

KEEP_MULTILIBS="avr25 avr4 avr5 avr6"
for d in "$PREFIX/lib/gcc/avr/$GCC"/*/ "$PREFIX/avr/lib"/*/; do
  [ -d "$d" ] || continue
  n=$(basename "$d")
  case "$n" in
    include|include-fixed|install-tools|device-specs|plugin|ldscripts) continue ;;
  esac
  case " $KEEP_MULTILIBS " in *" $n "*) continue ;; esac
  rm -rf "$d"
done

find "$PREFIX/bin" "$PREFIX/libexec" -type f 2>/dev/null | while read -r f; do
  file "$f" | grep -q "Mach-O" && strip -S "$f" 2>/dev/null || true
done
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
COPYFILE_DISABLE=1 tar cJf "$ROOT/avr-toolchain-macos-arm64.tar.xz" -C "$(dirname "$PREFIX")" "$(basename "$PREFIX")"
echo "=== TOOLCHAIN NATIVO ARM64 CONSTRUIDO EN $PREFIX"
echo "    paquete: $ROOT/avr-toolchain-macos-arm64.tar.xz ($(du -h "$ROOT/avr-toolchain-macos-arm64.tar.xz" | awk '{print $1}'))"
