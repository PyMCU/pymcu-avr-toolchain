#!/usr/bin/env bash
set -euo pipefail
BINUTILS=${BINUTILS_VERSION:-2.42}
GCC=${GCC_VERSION:-14.2.0}
AVRLIBC=${AVRLIBC_VERSION:-2.2.0}

ARCH=$(uname -m)
BUILD_TRIPLE="${ARCH}-linux-gnu"
case "$ARCH" in
  x86_64) FILE_ARCH="x86-64" ;;
  *)      FILE_ARCH="$ARCH" ;;
esac

ROOT="${AVR_BUILD_ROOT:-/tmp/avrtc}"
PREFIX=$ROOT/toolchain-staged
export MAKEFLAGS="-j$(nproc)"
export CFLAGS="-O2"
export CXXFLAGS="-O2"
export LC_ALL=C

mkdir -p "$ROOT/src" "$ROOT/build"
cd "$ROOT/src"

echo "=== [1/6] descargando fuentes ($ARCH)"
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
      --disable-nls --disable-werror --disable-shared --enable-static --without-zstd \
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
      --disable-shared --with-dwarf2 \
      --with-double=32 --with-long-double=32 \
      MAKEINFO=missing
  make || { echo "reintentando en serie (carrera de libgcc)"; make -j1; }
  make install
  touch .done
fi

echo "=== [5/6] avr-libc"
mkdir -p "$ROOT/build/avr-libc" && cd "$ROOT/build/avr-libc"
if [ ! -f .done ]; then
  "$ROOT/src/avr-libc-$AVRLIBC/configure" --prefix="$PREFIX" --host=avr \
      --build="$BUILD_TRIPLE"
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
  file "$f" | grep -q ELF && strip --strip-unneeded "$f" 2>/dev/null || true
done
find "$PREFIX" -name "*.a" -exec strip -g {} \; 2>/dev/null || true
echo "  arbol: $(du -sh "$PREFIX" | awk '{print $1}')"

echo "=== [6/6] verificacion"
cd "$PREFIX"
for b in avr-gcc avr-as avr-objcopy; do
  file "bin/$b"
  file "bin/$b" | grep -q "$FILE_ARCH" || { echo "FALLO: $b no es $FILE_ARCH"; exit 1; }
  if ldd "bin/$b" | grep -vE "linux-vdso|ld-linux|libc\.so|libm\.so|libdl\.so|librt\.so|libpthread\.so|libgcc_s\.so|libstdc\+\+\.so|libz\.so|not a dynamic" | grep -q "=>"; then
    echo "FALLO: $b depende de bibliotecas ajenas:"; ldd "bin/$b"; exit 1
  fi
done
test -f avr/lib/libm.a && echo "libm.a OK"
./bin/avr-gcc -mmcu=atmega328p --print-libgcc-file-name

tar cJf "$ROOT/avr-toolchain-linux-$ARCH.tar.xz" -C "$(dirname "$PREFIX")" "$(basename "$PREFIX")"
echo "=== TOOLCHAIN NATIVO $ARCH CONSTRUIDO EN $PREFIX"
echo "    paquete: $ROOT/avr-toolchain-linux-$ARCH.tar.xz ($(du -h "$ROOT/avr-toolchain-linux-$ARCH.tar.xz" | awk '{print $1}'))"
