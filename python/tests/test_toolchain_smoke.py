"""
Toolchain supply-chain smoke tests for pymcu-avr-toolchain.

These tests verify that the package is correctly installed on the current
platform: binaries exist, execute bits are set, device-specs resolve, and
the full compile → link → objcopy pipeline produces a valid HEX file.

Run with:
    pytest python/tests/test_toolchain_smoke.py -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Package availability guard
# ---------------------------------------------------------------------------

try:
    import pymcu_avr_toolchain as _tc

    BIN_DIR: Path = _tc.get_bin_dir()
    _HAS_TOOLCHAIN = True
    _TC_ERROR = ""
except Exception as exc:
    BIN_DIR = Path("/nonexistent")
    _HAS_TOOLCHAIN = False
    _TC_ERROR = str(exc)

pytestmark = pytest.mark.skipif(
    not _HAS_TOOLCHAIN,
    reason=f"pymcu-avr-toolchain not installed or get_bin_dir() failed: {_TC_ERROR}",
)

_IS_WIN = sys.platform == "win32"
_EXE = ".exe" if _IS_WIN else ""

_MINIMAL_C = "int add(int a, int b) { return a + b; }"

_MINIMAL_ASM = """\
.global main
main:
    nop
    ret
"""

_MINIMAL_LD = """\
ENTRY(main)
SECTIONS {
  .text 0x000000 : { *(.vectors) *(.text*) }
  .data 0x800100 : { *(.data*) *(.bss*) *(COMMON) }
}
"""


def _run(*args: str, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(a) for a in args],
        capture_output=True,
        timeout=60,
        env=env,
    )


def _gcc_env() -> dict:
    """Environment with BIN_DIR prepended to PATH (for avr-gcc tool lookup)."""
    env = os.environ.copy()
    bin_str = str(BIN_DIR)
    if bin_str not in env.get("PATH", "").split(os.pathsep):
        env["PATH"] = bin_str + os.pathsep + env.get("PATH", "")
    return env


# ---------------------------------------------------------------------------
# 1. Package API
# ---------------------------------------------------------------------------


class TestPackageAPI:
    def test_bin_dir_is_dir(self):
        assert BIN_DIR.is_dir(), f"bin/ missing: {BIN_DIR}"

    def test_toolchain_version(self):
        ver = _tc.toolchain_version()
        assert ver and ver != "unknown", f"unexpected version: {ver!r}"

    def test_manifest(self):
        m = _tc.manifest()
        assert isinstance(m, dict)

    @pytest.mark.parametrize("name", ["avr-gcc", "avr-as", "avr-objcopy"])
    def test_get_tool(self, name):
        p = _tc.get_tool(name)
        assert p.exists(), f"{name} path from get_tool() does not exist: {p}"


# ---------------------------------------------------------------------------
# 2. Binary presence and executability
# ---------------------------------------------------------------------------


class TestBinaryPresence:
    @pytest.mark.parametrize("name", ["avr-gcc", "avr-as", "avr-objcopy", "avr-ld"])
    def test_binary_exists(self, name):
        p = BIN_DIR / f"{name}{_EXE}"
        assert p.exists(), f"{name} not found in {BIN_DIR}"

    @pytest.mark.skipif(_IS_WIN, reason="execute bits are a POSIX concept")
    @pytest.mark.parametrize("name", ["avr-gcc", "avr-as", "avr-objcopy"])
    def test_binary_executable(self, name):
        p = BIN_DIR / name
        assert os.access(p, os.X_OK), (
            f"{name} is not executable — ZIP artifact upload may have stripped +x bits"
        )

    @pytest.mark.skipif(_IS_WIN, reason="execute bits are a POSIX concept")
    def test_libexec_executable(self):
        libexec = BIN_DIR.parent / "libexec"
        if not libexec.is_dir():
            pytest.skip("no libexec/ in this wheel build")
        # Only check ELF/Mach-O binaries (no extension or non-library extension).
        # Skip .la (libtool text metadata), .so/.dylib/.a (shared/static libs).
        _LIB_SUFFIXES = {".la", ".a", ".so", ".dylib"}
        non_exec = [
            str(f)
            for f in libexec.rglob("*")
            if (
                f.is_file()
                and not f.is_symlink()
                and f.suffix not in _LIB_SUFFIXES
                and not f.name.endswith(".so.0")  # versioned shared libs
                and not os.access(f, os.X_OK)
            )
        ]
        assert not non_exec, (
            "libexec/ binaries missing execute bit (cc1, collect2, etc.):\n"
            + "\n".join(non_exec[:10])
        )


# ---------------------------------------------------------------------------
# 3. Symlinks required by avr-gcc 15.x
# ---------------------------------------------------------------------------


class TestSymlinks:
    @pytest.mark.skipif(_IS_WIN, reason="symlinks not needed on Windows")
    @pytest.mark.parametrize("name", ["as", "ld"])
    def test_bin_symlink(self, name):
        sym = BIN_DIR / name
        assert sym.exists(), (
            f"bin/{name} symlink missing — avr-gcc 15.x falls back to system "
            f"/usr/bin/{name} (x86_64) without it"
        )

    @pytest.mark.skipif(_IS_WIN, reason="symlinks not needed on Windows")
    @pytest.mark.parametrize("name", ["as", "ld"])
    def test_avr_bin_symlink(self, name):
        avr_bin = BIN_DIR.parent / "avr" / "bin"
        if not avr_bin.is_dir():
            pytest.skip("no avr/bin/ in this wheel build")
        sym = avr_bin / name
        assert sym.exists(), (
            f"avr/bin/{name} symlink missing — avr-gcc 15.x COMPILER_PATH lookup fails"
        )


# ---------------------------------------------------------------------------
# 4. Tool execution
# ---------------------------------------------------------------------------


class TestToolExecution:
    def test_avr_gcc_version(self):
        r = _run(BIN_DIR / f"avr-gcc{_EXE}", "--version")
        assert r.returncode == 0, f"avr-gcc --version:\n{r.stderr.decode()}"
        assert "avr-gcc" in r.stdout.decode().lower()

    def test_avr_as_version(self):
        r = _run(BIN_DIR / f"avr-as{_EXE}", "--version")
        assert r.returncode == 0, f"avr-as --version:\n{r.stderr.decode()}"

    def test_device_specs_atmega328p(self):
        r = _run(
            BIN_DIR / f"avr-gcc{_EXE}",
            "-mmcu=atmega328p",
            "--print-libgcc-file-name",
        )
        assert r.returncode == 0, (
            "Device-specs lookup failed — glibc mismatch or missing device-specs?\n"
            + r.stderr.decode()
        )
        assert "libgcc" in r.stdout.decode(), f"unexpected output: {r.stdout.decode()!r}"

    def test_device_specs_attiny85(self):
        r = _run(
            BIN_DIR / f"avr-gcc{_EXE}",
            "-mmcu=attiny85",
            "--print-libgcc-file-name",
        )
        assert r.returncode == 0, (
            "attiny85 device-specs lookup failed:\n" + r.stderr.decode()
        )


# ---------------------------------------------------------------------------
# 5. Full pipeline: compile → assemble → link → HEX
# ---------------------------------------------------------------------------


class TestPipeline:
    def test_compile_c(self, tmp_path):
        c_file = tmp_path / "add.c"
        c_file.write_text(_MINIMAL_C)
        obj = tmp_path / "add.o"
        r = _run(
            BIN_DIR / f"avr-gcc{_EXE}",
            "-mmcu=atmega328p", "-Os", "-c",
            c_file, "-o", obj,
            env=_gcc_env(),
        )
        assert r.returncode == 0, f"compile failed:\n{r.stderr.decode()}"
        assert obj.exists()

    def test_full_pipeline(self, tmp_path):
        gcc = BIN_DIR / f"avr-gcc{_EXE}"
        avr_as = BIN_DIR / f"avr-as{_EXE}"
        avr_objcopy = BIN_DIR / f"avr-objcopy{_EXE}"
        env = _gcc_env()

        c_file = tmp_path / "add.c"
        c_file.write_text(_MINIMAL_C)
        asm_file = tmp_path / "main.s"
        asm_file.write_text(_MINIMAL_ASM)
        ld_file = tmp_path / "link.ld"
        ld_file.write_text(_MINIMAL_LD)

        # Assemble
        main_o = tmp_path / "main.o"
        r = _run(avr_as, "-mmcu=atmega328p", asm_file, "-o", main_o)
        assert r.returncode == 0, f"avr-as failed:\n{r.stderr.decode()}"

        # Compile C
        add_o = tmp_path / "add.o"
        r = _run(gcc, "-mmcu=atmega328p", "-Os", "-c", c_file, "-o", add_o, env=env)
        assert r.returncode == 0, f"avr-gcc compile failed:\n{r.stderr.decode()}"

        # Link
        elf = tmp_path / "firmware.elf"
        r = _run(
            gcc, "-mmcu=atmega328p",
            "-nostartfiles", "-nodefaultlibs",
            "-T", ld_file,
            main_o, add_o,
            "-lgcc",
            "-o", elf,
            env=env,
        )
        assert r.returncode == 0, f"link failed:\n{r.stderr.decode()}"

        # ELF → HEX
        hex_file = tmp_path / "firmware.hex"
        r = _run(avr_objcopy, "-O", "ihex", "-R", ".eeprom", elf, hex_file)
        assert r.returncode == 0, f"objcopy failed:\n{r.stderr.decode()}"
        assert ":00000001FF" in hex_file.read_text(), "HEX file missing EOF record"
