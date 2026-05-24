#!/usr/bin/env bash
# bundle-macos-toolchain.sh
#
# Assemble a self-contained AVR toolchain from Homebrew kegs and bundle
# all dylib dependencies so the resulting directory is portable — i.e. it
# works when moved outside the Homebrew prefix (e.g. into a pip wheel).
#
# Usage:
#   bundle-macos-toolchain.sh <brew_prefix> <output_dir>
#
# Examples:
#   bundle-macos-toolchain.sh /opt/homebrew  macos-arm64-toolchain-staged
#   bundle-macos-toolchain.sh /usr/local     macos-x64-toolchain-staged
#
# The script:
#   1. Copies bin/, lib/, libexec/, avr/ from the avr-gcc and avr-binutils
#      Homebrew kegs into <output_dir>.
#   2. Finds every Homebrew dylib referenced by each Mach-O binary.
#   3. Copies those dylibs into <output_dir>/lib/.
#   4. Patches each binary and each bundled dylib with install_name_tool so
#      all references become @rpath-relative (no absolute Homebrew paths).
#   5. Adds the correct @rpath to each binary so the dynamic linker can find
#      the bundled dylibs at runtime regardless of where the tree is installed.

set -euo pipefail

BREW_PREFIX="${1:?Usage: $0 <brew_prefix> <output_dir>}"
OUTPUT="${2:?Usage: $0 <brew_prefix> <output_dir>}"

# ── Locate Homebrew kegs ─────────────────────────────────────────────────────

GCC_KEG=""
for candidate in "$BREW_PREFIX/opt/avr-gcc" "$BREW_PREFIX/opt/avr-gcc@14" "$BREW_PREFIX/opt/avr-gcc@13"; do
    if [ -d "$candidate/bin" ]; then
        GCC_KEG="$candidate"
        break
    fi
done
[ -n "$GCC_KEG" ] || { echo "ERROR: avr-gcc keg not found under $BREW_PREFIX/opt/"; exit 1; }

BIN_KEG=""
for candidate in "$BREW_PREFIX/opt/avr-binutils"; do
    if [ -d "$candidate/bin" ]; then
        BIN_KEG="$candidate"
        break
    fi
done
[ -n "$BIN_KEG" ] || { echo "ERROR: avr-binutils keg not found under $BREW_PREFIX/opt/"; exit 1; }

echo "avr-gcc keg:      $GCC_KEG"
echo "avr-binutils keg: $BIN_KEG"
echo "Output:           $OUTPUT"

# ── Copy toolchain files ─────────────────────────────────────────────────────

mkdir -p "$OUTPUT/bin" "$OUTPUT/lib"

# GCC support files (lib/gcc/avr/*, libexec/gcc/avr/*)
[ -d "$GCC_KEG/lib" ]     && cp -rL "$GCC_KEG/lib"     "$OUTPUT/"
[ -d "$GCC_KEG/libexec" ] && cp -rL "$GCC_KEG/libexec" "$OUTPUT/"
[ -d "$GCC_KEG/share" ]   && cp -rL "$GCC_KEG/share"   "$OUTPUT/" 2>/dev/null || true

# Binaries from both kegs
for exe in "$GCC_KEG/bin/"* "$BIN_KEG/bin/"*; do
    [ -f "$exe" ] && cp -L "$exe" "$OUTPUT/bin/" 2>/dev/null || true
done

# AVR sysroot (device headers, crt*.o) from binutils keg
[ -d "$BIN_KEG/avr" ] && cp -rL "$BIN_KEG/avr" "$OUTPUT/"

# ── Dylib bundling ────────────────────────────────────────────────────────────
# avr-gcc and its cc1/cc1plus helpers are dynamically linked against Homebrew's
# libgmp, libmpfr, and libmpc. We copy those dylibs into lib/ and rewrite all
# absolute paths to @rpath-relative so the tree is self-contained.

LIB_OUT="$(cd "$OUTPUT/lib" && pwd)"
OUT_ABS="$(cd "$OUTPUT" && pwd)"

# Rewrite one Homebrew dylib reference inside a binary.
# Copies the dylib to LIB_OUT on first encounter and patches its install name.
patch_dylib_ref() {
    local binary="$1"
    local dylib_path="$2"
    local dylib_name
    dylib_name="$(basename "$dylib_path")"

    if [ ! -f "$LIB_OUT/$dylib_name" ]; then
        cp -L "$dylib_path" "$LIB_OUT/$dylib_name"
        install_name_tool -id "@rpath/$dylib_name" "$LIB_OUT/$dylib_name" 2>/dev/null || true
    fi
    install_name_tool -change "$dylib_path" "@rpath/$dylib_name" "$binary" 2>/dev/null || true
}

# Process one Mach-O binary: bundle its Homebrew deps and add an @rpath.
bundle_binary() {
    local binary="$1"
    local rpath_val="$2"

    # Collect Homebrew dylib dependencies
    while IFS= read -r dylib_path; do
        patch_dylib_ref "$binary" "$dylib_path"
    done < <(otool -L "$binary" 2>/dev/null | awk -v p="$BREW_PREFIX" '$1 ~ p {print $1}')

    # Add the rpath so the dynamic linker can find the bundled dylibs.
    # -add_rpath is a no-op if the rpath already exists.
    install_name_tool -add_rpath "$rpath_val" "$binary" 2>/dev/null || true
}

# bin/ executables — lib/ is one level up
for exe in "$OUT_ABS/bin/"*; do
    [ -f "$exe" ] || continue
    file "$exe" 2>/dev/null | grep -q "Mach-O" || continue
    bundle_binary "$exe" "@executable_path/../lib"
done

# libexec/ executables — lib/ depth varies; use @loader_path + relative path
while IFS= read -r exe; do
    exe_dir="$(dirname "$exe")"
    rel_to_lib="$(python3 -c "import os; print(os.path.relpath('$LIB_OUT', '$exe_dir'))")"
    bundle_binary "$exe" "@loader_path/$rel_to_lib"
done < <(find "$OUT_ABS/libexec" -type f 2>/dev/null | while read -r f; do
    file "$f" 2>/dev/null | grep -q "Mach-O" && echo "$f"
done)

# Fix cross-references INSIDE bundled dylibs (e.g. libmpfr → libgmp)
for dylib in "$LIB_OUT/"*.dylib; do
    [ -f "$dylib" ] || continue
    while IFS= read -r dep_path; do
        dep_name="$(basename "$dep_path")"
        [ -f "$LIB_OUT/$dep_name" ] && \
            install_name_tool -change "$dep_path" "@rpath/$dep_name" "$dylib" 2>/dev/null || true
    done < <(otool -L "$dylib" 2>/dev/null | awk -v p="$BREW_PREFIX" '$1 ~ p {print $1}')
done

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "Bundled dylibs:"
ls -1 "$LIB_OUT/"*.dylib 2>/dev/null | sed 's/^/  /' || echo "  (none)"
echo ""
echo "bin/ contents:"
ls -1 "$OUT_ABS/bin/" | grep avr | sed 's/^/  /'
echo ""
echo "Done."
