# hatch_build.py
# Custom hatchling build hook: copies the pre-built AVR toolchain binaries
# into the wheel before packaging.
#
# The resulting wheel is tagged py3-none-<platform> — Python-version-agnostic
# but platform-specific (one wheel per OS/arch).
#
# Environment variables:
#   AVRT_TOOLCHAIN_DIR   Path to the pre-built toolchain directory (required).
#                        Must contain bin/avr-gcc, bin/avr-as, bin/avr-objcopy.
#                        Example: /path/to/avr-gcc-15.2.0-x64-linux
#   WHEEL_PLATFORM_TAG   Override the wheel platform tag.
#                        Example: manylinux_2_17_x86_64, win_amd64, macosx_14_0_arm64

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
from datetime import datetime, timezone
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_REQUIRED_BINS = ["avr-gcc", "avr-as", "avr-objcopy"]
_TOOLCHAIN_SUBDIRS = ["bin", "lib", "avr", "libexec", "share", "include"]


class CustomBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        root = Path(self.root)
        project_version = version
        toolchain_dir = _find_toolchain_dir(root)

        self.app.display_info(f"[hatch-hook] Using toolchain: {toolchain_dir}")

        bin_src = toolchain_dir / "bin"
        _validate_toolchain(bin_src)

        pkg_dir = root / "src" / "pymcu_avr_toolchain"

        # Remove any previously copied toolchain subdirectories.
        for sub in _TOOLCHAIN_SUBDIRS:
            d = pkg_dir / sub
            if d.exists():
                shutil.rmtree(d)

        # Copy the full toolchain installation (bin/, lib/, avr/, libexec/, share/).
        # A self-contained avr-gcc binary needs lib/gcc/avr/<version>/device-specs/
        # alongside it; copying only bin/ produces a binary that cannot resolve
        # chip-specific specs when seeded to a different location.
        for item in toolchain_dir.iterdir():
            if item.is_dir():
                shutil.copytree(str(item), str(pkg_dir / item.name))
        bin_dst = pkg_dir / "bin"
        self.app.display_info(f"[hatch-hook] Copied full toolchain from: {toolchain_dir}")

        manifest = _build_manifest(toolchain_dir, bin_dst, project_version)
        (pkg_dir / "_manifest.json").write_text(json.dumps(manifest, indent=2))
        self.app.display_info(f"[hatch-hook] Manifest: {manifest}")

        plat_tag = _get_wheel_platform_tag()
        build_data["pure_python"] = False
        build_data["tag"] = f"py3-none-{plat_tag}"
        self.app.display_info(f"[hatch-hook] Wheel tag: py3-none-{plat_tag}")

    def finalize(self, version: str, build_data: dict, artifact_path: str) -> None:
        pkg_dir = Path(self.root) / "src" / "pymcu_avr_toolchain"
        for sub in _TOOLCHAIN_SUBDIRS:
            d = pkg_dir / sub
            if d.exists():
                shutil.rmtree(d)
                self.app.display_info(f"[hatch-hook] Cleaned up: {d}")
        manifest = pkg_dir / "_manifest.json"
        if manifest.exists():
            manifest.unlink()


def _find_toolchain_dir(root: Path) -> Path:
    env = os.environ.get("AVRT_TOOLCHAIN_DIR")
    if env:
        d = Path(env).resolve()
        if d.is_dir():
            return d
        raise FileNotFoundError(
            f"AVRT_TOOLCHAIN_DIR points to a non-existent directory: {d}"
        )

    # Local fallback: look for any avr-gcc-*-x64-linux dir in ../output/
    output_dir = root.parent / "output"
    if output_dir.is_dir():
        candidates = sorted(output_dir.glob("avr-gcc-*-x64-linux"))
        if candidates:
            return candidates[-1]

    raise FileNotFoundError(
        "Pre-built AVR toolchain not found. Either:\n"
        "  1. Set AVRT_TOOLCHAIN_DIR=/path/to/avr-gcc-15.2.0-x64-linux\n"
        "  2. Run the Docker build first: docker run --rm -v $(pwd)/output:/output avrgccbuild\n"
        f"     (expected output in: {root.parent / 'output'})"
    )


def _validate_toolchain(bin_dir: Path) -> None:
    plat_tag = os.environ.get("WHEEL_PLATFORM_TAG", "")
    exe = ".exe" if sys.platform == "win32" or plat_tag.startswith("win") else ""
    missing = [b for b in _REQUIRED_BINS if not (bin_dir / (b + exe)).exists()]
    if missing:
        raise FileNotFoundError(
            f"Toolchain bin/ directory is missing required binaries: {missing}\n"
            f"Checked in: {bin_dir}"
        )


def _build_manifest(toolchain_dir: Path, bin_dir: Path, project_version: str) -> dict:
    plat_tag = os.environ.get("WHEEL_PLATFORM_TAG", "")
    exe = ".exe" if sys.platform == "win32" or plat_tag.startswith("win") else ""
    gcc_version = _read_tool_version(bin_dir / f"avr-gcc{exe}", r"(\d+\.\d+\.\d+)")
    as_version = _read_tool_version(bin_dir / f"avr-as{exe}", r"(\d+\.\d+)")
    gdb_path = bin_dir / f"avr-gdb{exe}"
    gdb_version = _read_tool_version(gdb_path, r"(\d+\.\d+)") if gdb_path.exists() else "n/a"

    # Fall back to the project version when binary detection fails (e.g. cross-build).
    if gcc_version == "unknown":
        gcc_version = project_version

    return {
        "gcc_version": gcc_version,
        "binutils_version": as_version,
        "gdb_version": gdb_version,
        "platform": toolchain_dir.name,
        "built_at": datetime.now(timezone.utc).isoformat(),
    }


def _read_tool_version(binary: Path, pattern: str) -> str:
    try:
        result = subprocess.run(
            [str(binary), "--version"],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout + result.stderr
        m = re.search(pattern, output)
        return m.group(1) if m else "unknown"
    except Exception:
        return "unknown"


def _get_wheel_platform_tag() -> str:
    override = os.environ.get("WHEEL_PLATFORM_TAG")
    if override:
        return override
    if sys.platform.startswith("linux"):
        arch = platform.machine().lower()
        return f"manylinux_2_17_{arch}"
    return sysconfig.get_platform().replace("-", "_").replace(".", "_")
