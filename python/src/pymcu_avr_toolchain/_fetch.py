# -----------------------------------------------------------------------------
# pymcu-avr-toolchain -- auto-download from GitHub Releases
# Copyright (C) 2026 Ivan Montiel Cardona and the PyMCU Project Authors
# SPDX-License-Identifier: GPL-3.0-or-later
# -----------------------------------------------------------------------------

"""
Download the AVR toolchain binary wheel from the pymcu-avr-toolchain GitHub
Release and extract its binaries into the shared PyMCU cache.

This module is invoked automatically by :func:`get_bin_dir` when the package
is installed from the PyPI sdist (no bundled binaries).  Users can also run it
explicitly::

    python -m pymcu_avr_toolchain fetch

Environment variables
---------------------
PYMCU_AVR_WHEEL_URL
    Override the wheel download URL (useful for air-gapped installs or testing).
PYMCU_SKIP_HASH_CHECK=1
    Skip SHA-256 verification (not normally needed; the wheel's integrity is
    guaranteed by HTTPS + GitHub's CDN).
"""

from __future__ import annotations

import os
import platform
import ssl
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

_REPO = "PyMCU/avr-gcc-build"
_RELEASE_BASE = f"https://github.com/{_REPO}/releases/download"

# Subdirectories inside the wheel that contain toolchain binaries.
# Python source files (.py, .dist-info) are intentionally excluded.
_TOOLCHAIN_SUBDIRS = {"bin", "lib", "avr", "libexec", "share", "include"}


def _wheel_platform_tag() -> str:
    """Return the wheel platform tag for the current machine."""
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        arch = "x86_64"
        manylinux_arch = "x86_64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
        manylinux_arch = "aarch64"
    else:
        raise RuntimeError(
            f"pymcu-avr-toolchain: unsupported architecture '{machine}'.\n"
            f"Download the toolchain manually from:\n"
            f"  https://github.com/{_REPO}/releases"
        )

    if sys.platform == "darwin":
        return "macosx_14_0_arm64"
    elif sys.platform.startswith("linux"):
        return f"manylinux_2_17_{manylinux_arch}"
    elif sys.platform == "win32":
        return "win_amd64"
    else:
        raise RuntimeError(
            f"pymcu-avr-toolchain: unsupported platform '{sys.platform}'.\n"
            f"Download the toolchain manually from:\n"
            f"  https://github.com/{_REPO}/releases"
        )


def _wheel_url(pkg_version: str) -> str:
    """Construct the GitHub Release URL for the current platform's binary wheel."""
    url = os.environ.get("PYMCU_AVR_WHEEL_URL")
    if url:
        return url
    tag = _wheel_platform_tag()
    wheel_name = f"pymcu_avr_toolchain-{pkg_version}-py3-none-{tag}.whl"
    return f"{_RELEASE_BASE}/v{pkg_version}/{wheel_name}"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _ssl_context() -> ssl.SSLContext:
    """
    Return an SSL context with working CA certificates.

    Python builds from python.org on macOS use a bundled OpenSSL that does not
    read the system keychain automatically.  We supplement the default context
    with known system CA bundle paths so HTTPS to GitHub works out of the box.
    """
    ctx = ssl.create_default_context()
    _CA_CANDIDATES = [
        os.environ.get("SSL_CERT_FILE", ""),
        "/etc/ssl/cert.pem",                        # macOS system bundle
        "/etc/ssl/certs/ca-certificates.crt",        # Debian/Ubuntu
        "/etc/pki/tls/certs/ca-bundle.crt",          # RHEL/CentOS/Fedora
        "/usr/local/etc/openssl/cert.pem",            # Homebrew OpenSSL
    ]
    for cafile in _CA_CANDIDATES:
        if cafile and os.path.isfile(cafile):
            try:
                ctx.load_verify_locations(cafile=cafile)
                return ctx
            except ssl.SSLError:
                continue
    # certifi as last resort (common transitive dependency)
    try:
        import certifi  # noqa: PLC0415
        ctx.load_verify_locations(cafile=certifi.where())
    except (ImportError, ssl.SSLError):
        pass
    return ctx


def _download(url: str, dest: Path) -> None:
    """Download *url* to *dest* with a simple progress indicator."""
    ctx = _ssl_context()
    req = urllib.request.Request(url, headers={"User-Agent": "pymcu-avr-toolchain/1.0"})
    try:
        with urllib.request.urlopen(req, context=ctx) as resp:
            total = int(resp.headers.get("Content-Length", 0) or 0)
            downloaded = 0
            report_every = max(1 << 20, total // 20)  # ~5% steps
            next_report = report_every
            with open(dest, "wb") as fh:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if total and downloaded >= next_report:
                        pct = min(100, downloaded * 100 // total)
                        _log(f"  ... {pct}%")
                        next_report += report_every
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"pymcu-avr-toolchain: HTTP {exc.code} downloading wheel.\n"
            f"URL: {url}\n"
            f"Check that release v{_pkg_version_from_env()} exists on:\n"
            f"  https://github.com/{_REPO}/releases"
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"pymcu-avr-toolchain: download failed.\n"
            f"URL: {url}\n"
            f"Error: {exc}"
        ) from exc


def _pkg_version_from_env() -> str:
    try:
        from importlib.metadata import version as _v
        return _v("pymcu-avr-toolchain")
    except Exception:
        return "unknown"


def fetch_to_cache(
    cache_dir: Path,
    bin_dir: Path,
    sentinel: Path,
    cache_key: str,
    *,
    console=None,
) -> None:
    """
    Download the binary wheel for the current platform and extract its toolchain
    directories into *cache_dir*.  Writes *sentinel* on success.

    Parameters
    ----------
    cache_dir:
        Root of the versioned cache directory (e.g. ``~/.pymcu/tools/darwin-arm64/
        pymcu-avr-toolchain/15.2.0.post3``).
    bin_dir:
        ``<cache_dir>/bin`` — returned by :func:`get_bin_dir` after seeding.
    sentinel:
        Path to the ``<cache_dir>/.seeded_from_wheel`` marker file.
    cache_key:
        The pip package version string (used as the cache directory name and
        stored in the sentinel so that upgrades invalidate the cache).
    """
    def log(msg: str) -> None:
        if console is not None:
            console.print(msg)
        else:
            _log(msg)

    url = _wheel_url(cache_key)
    log(f"[pymcu-avr-toolchain] No bundled toolchain found.")
    log(f"[pymcu-avr-toolchain] Downloading binary wheel from GitHub Releases:")
    log(f"  {url}")

    with tempfile.TemporaryDirectory(prefix="pymcu-avr-") as td:
        tmp = Path(td)
        wheel_path = tmp / "toolchain.whl"

        _download(url, wheel_path)
        log("[pymcu-avr-toolchain] Extracting toolchain binaries ...")

        cache_dir.mkdir(parents=True, exist_ok=True)

        # A wheel is a zip file.  Extract only the toolchain subdirectories
        # (bin/, lib/, avr/, etc.) from the pymcu_avr_toolchain/ package root.
        pkg_prefix = "pymcu_avr_toolchain/"
        with zipfile.ZipFile(wheel_path) as zf:
            for member in zf.namelist():
                if not member.startswith(pkg_prefix):
                    continue
                rel = member[len(pkg_prefix):]
                parts = rel.split("/")
                if not parts or parts[0] not in _TOOLCHAIN_SUBDIRS:
                    continue
                dest = cache_dir / rel
                if member.endswith("/"):
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(member))

    if not bin_dir.is_dir():
        raise RuntimeError(
            f"pymcu-avr-toolchain: bin/ directory not found after extraction.\n"
            f"The downloaded wheel may not contain AVR toolchain binaries.\n"
            f"Expected platform wheel for: {_wheel_platform_tag()}"
        )

    if sys.platform != "win32":
        for entry in bin_dir.iterdir():
            if entry.is_file():
                entry.chmod(entry.stat().st_mode | 0o111)

    sentinel.write_text(cache_key, encoding="utf-8")
    log(f"[pymcu-avr-toolchain] Toolchain ready at: {bin_dir}")
