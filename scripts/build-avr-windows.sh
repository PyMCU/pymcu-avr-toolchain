#!/usr/bin/env bash
set -euo pipefail
BINUTILS=${BINUTILS_VERSION:-2.42}
GCC=${GCC_VERSION:-14.2.0}
AVRLIBC=${AVRLIBC_VERSION:-2.2.0}

ROOT="${AVR_BUILD_ROOT:-/c/a}"
PREFIX=$ROOT/toolchain-staged
export MAKEFLAGS="-j$(nproc)"
export CFLAGS="-O2"
export CXXFLAGS="-O2"
export LDFLAGS="-static -static-libgcc -static-libstdc++"
export LC_ALL=C

mkdir -p "$ROOT/src" "$ROOT/build"
cd "$ROOT/src"

echo "=== [1/6] sources"
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

echo "=== [3/6] gcc prerequisites"
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
  make || { echo "retrying serially (libgcc race)"; make -j1; }
  make install
  touch .done
fi

echo "=== [5/6] avr-libc"
mkdir -p "$ROOT/build/avr-libc" && cd "$ROOT/build/avr-libc"
if [ ! -f .done ]; then
  "$ROOT/src/avr-libc-$AVRLIBC/configure" --prefix="$PREFIX" --host=avr \
      --build=x86_64-w64-mingw32
  make
  make install
  touch .done
fi

echo "=== [5.5/6] trim"
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

find "$PREFIX/bin" "$PREFIX/libexec" -name "*.exe" 2>/dev/null | while read -r f; do
  strip --strip-unneeded "$f" 2>/dev/null || true
done
find "$PREFIX" -name "*.a" -exec strip -g {} \; 2>/dev/null || true

echo "=== [6/6] verification"
cd "$PREFIX"
test -f "libexec/gcc/avr/$GCC/cc1.exe" || { echo "FAIL: cc1.exe missing"; exit 1; }
test -d "lib/gcc/avr/$GCC/device-specs" || { echo "FAIL: device-specs missing"; exit 1; }
test -f avr/lib/libm.a || { echo "FAIL: libm.a missing"; exit 1; }

for b in avr-gcc avr-as avr-objcopy; do
  test -f "bin/$b.exe" || { echo "FAIL: bin/$b.exe missing"; exit 1; }
  file "bin/$b.exe"
  file "bin/$b.exe" | grep -q "PE32+" || { echo "FAIL: $b is not a 64-bit PE"; exit 1; }
  leaked=$(ldd "bin/$b.exe" | grep -iE "mingw64|msys|ucrt64" || true)
  if [ -n "$leaked" ]; then
    echo "FAIL: $b depends on DLLs outside the tree:"; echo "$leaked"; exit 1
  fi
done

./bin/avr-gcc.exe -mmcu=atmega328p --print-libgcc-file-name
echo "int main(void){return 0;}" > "$ROOT/t.c"
./bin/avr-gcc.exe -mmcu=atmega328p -Os -o "$ROOT/t.elf" "$ROOT/t.c"
./bin/avr-objcopy.exe -O ihex "$ROOT/t.elf" "$ROOT/t.hex"
test -s "$ROOT/t.hex" || { echo "FAIL: empty hex"; exit 1; }

echo "  tree: $(du -sh "$PREFIX" | awk '{print $1}')"
echo "=== NATIVE WINDOWS TOOLCHAIN BUILT AT $PREFIX"
