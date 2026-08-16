# -----------------------------------------------------------------------------
# pymcu-avr-toolchain
# Copyright (C) 2026 Ivan Montiel Cardona and the PyMCU Project Authors
# SPDX-License-Identifier: GPL-2.0-or-later
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
                "pymcu-avr-toolchain: bin/ not found in package directory "
                "and PYMCU_TOOLCHAIN_NO_SEEDING=1 prevents automatic download.\n"
                "Either install the binary wheel from GitHub Releases or unset "
                "PYMCU_TOOLCHAIN_NO_SEEDING to allow auto-download."
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
    """Return the GCC version (always 7.3.0 for PlatformIO-sourced wheels)."""
    v = manifest().get("gcc_version", "")
    if v and v not in ("unknown", ""):
        return v
    return "7.3.0"


def manifest() -> dict:
    """Return the build manifest written at wheel-build time."""
    if not _VERSION_FILE.exists():
        return {}
    with _VERSION_FILE.open() as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Windows still gets a single upstream build covering every architecture:
# PlatformIO publishes only x86_64 there and ARM machines run it emulated (see
# the _RELEASES map in _fetch.py). Keying the cache on the interpreter's
# architecture stored the same 230 MB twice on a machine that ran both an ARM
# and an x86_64 Python -- which is exactly what a Windows on Arm run produced.
_SINGLE_BUILD_OS = ("win32",)


def _tools_root() -> Path:
    env = os.environ.get("PYMCU_TOOLS_DIR")
    if env:
        return Path(env).resolve()
    return Path.home() / ".pymcu" / "tools"


def _payload_key() -> str:
    """
    Cache key naming the binaries that get stored, not the interpreter asking.

    The directory says what is inside it: on Apple Silicon and Windows on Arm
    the bytes really are x86_64, so that is what the path says.
    """
    os_name, _, arch = _platform_key().partition("-")
    if os_name in _SINGLE_BUILD_OS:
        return f"{os_name}-x86_64"
    return f"{os_name}-{arch}"


def _legacy_cache_dirs() -> list[Path]:
    """Cache directories written by the interpreter-keyed scheme."""
    root, current = _tools_root(), _payload_key()
    return [root / _platform_key()] if _platform_key() != current else []


def _global_cache_dir() -> Path:
    return _tools_root() / _payload_key()


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


def _adopt_legacy_cache(cache_dir: Path, bin_dir: Path, sentinel: Path, cache_key: str) -> bool:
    """
    Move a cache seeded under the old interpreter-keyed path into the new one.

    A rename beats re-seeding: it costs nothing on the same filesystem and it
    leaves no second copy behind, which was the whole complaint. If anything
    goes wrong the caller just seeds normally -- the old directory is then left
    untouched for `pymcu toolchain clean` to remove.
    """
    for legacy_root in _legacy_cache_dirs():
        legacy = legacy_root / "pymcu-avr-toolchain" / cache_key
        if not _cache_is_complete(legacy, legacy / "bin", legacy / ".seeded_from_wheel", cache_key):
            continue
        try:
            cache_dir.parent.mkdir(parents=True, exist_ok=True)
            if cache_dir.exists():
                shutil.rmtree(cache_dir, ignore_errors=True)
            os.replace(legacy, cache_dir)
            with contextlib.suppress(OSError):
                legacy.parent.rmdir()          # only if now empty
                legacy_root.rmdir()
            return _cache_is_complete(cache_dir, bin_dir, sentinel, cache_key)
        except OSError:
            continue
    return False


def _prune_old_versions(versions_dir: Path, keep: int = 2) -> None:
    """
    Keep the newest *keep* toolchain versions and drop the rest.

    Nothing pruned these before, so every upgrade left its predecessor behind
    for good: a developer machine here had four versions of this toolchain
    alone, 931 MB. Two are kept rather than one so that a project pinned to the
    previous release keeps working after another project pulls a newer one.
    """
    if not versions_dir.is_dir():
        return
    entries = [d for d in versions_dir.iterdir() if d.is_dir()]
    if len(entries) <= keep:
        return
    for stale in sorted(entries, key=lambda d: d.stat().st_mtime, reverse=True)[keep:]:
        with contextlib.suppress(OSError):
            shutil.rmtree(stale)


def _seed_cache(cache_dir: Path, bin_dir: Path, sentinel: Path, cache_key: str) -> None:
    with _seed_lock(cache_dir):
        if _cache_is_complete(cache_dir, bin_dir, sentinel, cache_key):
            return

        if _adopt_legacy_cache(cache_dir, bin_dir, sentinel, cache_key):
            _prune_old_versions(cache_dir.parent)
            return

        if not (_PKG_DIR / "bin").is_dir():
            # No binaries bundled — sdist / stub install from PyPI.
            # Download the binary wheel from the GitHub Release automatically.
            from ._fetch import fetch_to_cache  # noqa: PLC0415
            fetch_to_cache(cache_dir, bin_dir, sentinel, cache_key)
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
            # Set execute permission on all binaries. GitHub Actions artifact upload
            # uses ZIP which strips Unix execute bits; we restore them here for
            # bin/ and libexec/ (cc1, collect2, etc.).
            for search_dir in (bin_dir, cache_dir / "libexec"):
                if not search_dir.is_dir():
                    continue
                for entry in search_dir.rglob("*"):
                    if entry.is_file() and not entry.is_symlink():
                        with contextlib.suppress(OSError):
                            entry.chmod(entry.stat().st_mode | 0o111)

        if sys.platform != "win32":
            # GCC 15.x calls 'as' and 'ld' (not 'avr-as'/'avr-ld') from COMPILER_PATH
            # then PATH. Create un-prefixed symlinks in bin/ (PATH fallback) and
            # avr/bin/ (COMPILER_PATH hit) so the right tools are found.
            avr_bin = cache_dir / "avr" / "bin"
            for sym_name, target in (("as", "avr-as"), ("ld", "avr-ld")):
                sym = bin_dir / sym_name
                if not sym.exists() and (bin_dir / target).exists():
                    with contextlib.suppress(OSError):
                        sym.symlink_to(target)
                if avr_bin.is_dir():
                    avr_sym = avr_bin / sym_name
                    if not avr_sym.exists() and (bin_dir / target).exists():
                        with contextlib.suppress(OSError):
                            avr_sym.symlink_to(f"../../bin/{target}")

        sentinel.write_text(cache_key, encoding="utf-8")
        _prune_old_versions(cache_dir.parent)


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
    import fcntl  # noqa: PLC0415 — POSIX only; not available on Windows
    lock_path = cache_dir.parent / ".seed.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
