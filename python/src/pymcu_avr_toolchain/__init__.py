# -----------------------------------------------------------------------------
# pymcu-avr-toolchain
# Copyright (C) 2026 Ivan Montiel Cardona and the PyMCU Project Authors
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This package distributes pre-built AVR GCC toolchain binaries.
# See NOTICE and LICENSES/ for full license information.
# -----------------------------------------------------------------------------

"""
pymcu-avr-toolchain — pre-built AVR GCC toolchain as a pip package.

Public API
----------
get_bin_dir() -> Path
    Return the path to the bin/ directory containing avr-gcc, avr-as,
    avr-objcopy, avr-g++, avr-gdb, avr-ld, etc.
    On first call, seeds the global cache (~/.pymcu/tools/) so that all
    pymcu projects on this machine share a single copy of the binaries.

get_tool(name: str) -> Path
    Return the path to a specific binary. Appends .exe on Windows.
    Raises FileNotFoundError if not present.

toolchain_version() -> str
    Return the GCC version string (e.g. "15.2.0").

manifest() -> dict
    Return the build manifest as a dict.

TOOLCHAIN_ROOT: Path
    The root directory of the installed package (parent of bin/).

Environment variables
---------------------
PYMCU_TOOLCHAIN_NO_SEEDING=1
    Skip seeding the global cache; return the bin/ inside site-packages
    directly. Useful in Docker layer builds where you want the binaries
    in a known path without touching the home directory.
PYMCU_TOOLS_DIR
    Override the root cache directory (default: ~/.pymcu/tools).
    Must be an absolute path.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import platform
import shutil
import sys
from pathlib import Path

_PKG_DIR = Path(__file__).parent
_VERSION_FILE = _PKG_DIR / "_manifest.json"

TOOLCHAIN_ROOT: Path = _PKG_DIR


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_bin_dir() -> Path:
    """
    Return the bin/ directory of the installed AVR toolchain.

    On first call, the binaries are seeded into the global cache at
    ~/.pymcu/tools/{platform}/pymcu-avr-toolchain/{version}/ using hard
    links (zero extra disk space when on the same filesystem) or file
    copies as fallback. Subsequent calls are instant.

    Set PYMCU_TOOLCHAIN_NO_SEEDING=1 to skip seeding and return the
    bin/ directory inside site-packages directly.
    """
    if os.environ.get("PYMCU_TOOLCHAIN_NO_SEEDING") == "1":
        bin_dir = _PKG_DIR / "bin"
        if not bin_dir.is_dir():
            raise RuntimeError(
                "pymcu-avr-toolchain: bin/ not found in package directory.\n"
                "This package was installed from an sdist (no binaries). "
                "Install the platform-specific wheel instead."
            )
        return bin_dir

    # Use the pip package version as cache key, not gcc_version from the manifest.
    # gcc_version detection fails in cross-build steps (e.g. packaging a macOS
    # Mach-O binary on an Ubuntu runner), producing garbage like "standard".
    try:
        from importlib.metadata import version as _pkg_ver  # noqa: PLC0415
        cache_key = _pkg_ver("pymcu-avr-toolchain")
    except Exception:
        cache_key = toolchain_version()

    cache_dir = _global_cache_dir() / "pymcu-avr-toolchain" / cache_key
    bin_dir = cache_dir / "bin"
    sentinel = cache_dir / ".seeded_from_wheel"

    if _cache_is_complete(cache_dir, bin_dir, sentinel, cache_key):
        return bin_dir

    _seed_cache(cache_dir, bin_dir, sentinel, cache_key)
    return bin_dir


def get_tool(name: str) -> Path:
    """Return the Path to a named binary inside the toolchain bin/."""
    if sys.platform == "win32" and not name.endswith(".exe"):
        name = name + ".exe"
    p = get_bin_dir() / name
    if not p.exists():
        raise FileNotFoundError(
            f"pymcu-avr-toolchain: '{name}' not found in {get_bin_dir()}"
        )
    return p


def toolchain_version() -> str:
    """Return the GCC version from the manifest, or the package version as fallback."""
    v = manifest().get("gcc_version", "unknown")
    if v and v != "unknown":
        return v
    try:
        from importlib.metadata import version as _pkg_version  # noqa: PLC0415
        return _pkg_version("pymcu-avr-toolchain")
    except Exception:
        return "unknown"


def manifest() -> dict:
    """Return the build manifest written at wheel-build time."""
    if not _VERSION_FILE.exists():
        return {}
    with _VERSION_FILE.open() as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _global_cache_dir() -> Path:
    env = os.environ.get("PYMCU_TOOLS_DIR")
    if env:
        base = Path(env).resolve()
    else:
        base = Path.home() / ".pymcu" / "tools"
    return base / _platform_key()


def _platform_key() -> str:
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        arch = "x86_64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = machine
    os_name = "linux" if sys.platform.startswith("linux") else sys.platform
    return f"{os_name}-{arch}"


def _cache_is_complete(
    cache_dir: Path, bin_dir: Path, sentinel: Path, version: str
) -> bool:
    """Return True if the global cache is up-to-date and fully seeded."""
    if not sentinel.exists() or not bin_dir.is_dir():
        return False
    if sentinel.read_text(encoding="utf-8").strip() != version:
        return False
    # If the wheel includes lib/ (full self-contained build), the cache must too.
    if (_PKG_DIR / "lib").is_dir() and not (cache_dir / "lib").is_dir():
        return False
    return True


def _seed_cache(cache_dir: Path, bin_dir: Path, sentinel: Path, cache_key: str) -> None:
    if not (_PKG_DIR / "bin").is_dir():
        raise RuntimeError(
            "pymcu-avr-toolchain: no binaries found in package.\n"
            "Install the platform-specific wheel (not the sdist)."
        )

    with _seed_lock(cache_dir):
        if _cache_is_complete(cache_dir, bin_dir, sentinel, cache_key):
            return

        # Seed all toolchain directories (bin/, lib/, avr/, libexec/, share/).
        # A self-contained avr-gcc needs lib/gcc/avr/<version>/device-specs/
        # alongside the binary; seeding only bin/ produces a non-functional copy.
        cache_dir.mkdir(parents=True, exist_ok=True)
        for item in _PKG_DIR.iterdir():
            if item.is_dir() and item.name != "__pycache__":
                dst = cache_dir / item.name
                dst.mkdir(parents=True, exist_ok=True)
                _hardlink_or_copy_tree(item, dst)

        if sys.platform != "win32":
            for entry in bin_dir.iterdir():
                if entry.is_file():
                    entry.chmod(entry.stat().st_mode | 0o111)

        sentinel.write_text(cache_key, encoding="utf-8")


def _hardlink_or_copy_tree(src: Path, dst: Path) -> None:
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(item, target)
            except (OSError, NotImplementedError):
                shutil.copy2(item, target)


@contextlib.contextmanager
def _seed_lock(cache_dir: Path):
    cache_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        yield
        return
    lock_path = cache_dir.parent / ".seed.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
