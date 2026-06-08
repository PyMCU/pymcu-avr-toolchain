# -----------------------------------------------------------------------------
# pymcu-avr-toolchain -- auto-download from PlatformIO CDN
# Copyright (C) 2026 Ivan Montiel Cardona and the PyMCU Project Authors
# SPDX-License-Identifier: GPL-2.0-or-later
# -----------------------------------------------------------------------------

"""
Download the AVR toolchain tarball from the PlatformIO package registry and
stage the binaries into the shared PyMCU cache.

Invoked automatically by :func:`get_bin_dir` when no bundled binaries are
present (sdist / stub install).  Can also be called directly::

    python -m pymcu_avr_toolchain fetch

Environment variables
---------------------
PYMCU_AVR_TOOLCHAIN_URL
    Override the tarball download URL (for air-gapped installs or testing).
"""

from __future__ import annotations

import contextlib
import os
import platform
import ssl
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

_PIO_BASE = (
    "https://dl.registry.platformio.org/download/platformio/tool"
    "/toolchain-atmelavr/3.70300.220127"
)

# PlatformIO uses a single Windows tarball for x86, x64 and arm64 (WoA emulation).
# macOS arm64 uses the x86_64 tarball (runs via Rosetta 2).
_RELEASES: dict[str, str] = {
    "darwin-x86_64":  "toolchain-atmelavr-darwin_x86_64-3.70300.220127.tar.gz",
    "darwin-arm64":   "toolchain-atmelavr-darwin_x86_64-3.70300.220127.tar.gz",
    "linux-x86_64":   "toolchain-atmelavr-linux_x86_64-3.70300.220127.tar.gz",
    "linux-aarch64":  "toolchain-atmelavr-linux_aarch64-3.70300.220127.tar.gz",
    "win32-x86_64":   "toolchain-atmelavr-windows-3.70300.220127.tar.gz",
    "win32-arm64":    "toolchain-atmelavr-windows-3.70300.220127.tar.gz",
}


def _platform_key() -> str:
    machine = platform.machine().lower()
    arch = "x86_64" if machine in ("amd64", "x86_64") else "arm64" if machine in ("arm64", "aarch64") else machine
    os_name = sys.platform if sys.platform == "win32" else ("darwin" if sys.platform == "darwin" else "linux")
    return f"{os_name}-{arch}"


def _tarball_url() -> str:
    url = os.environ.get("PYMCU_AVR_TOOLCHAIN_URL")
    if url:
        return url
    key = _platform_key()
    filename = _RELEASES.get(key)
    if filename is None:
        raise RuntimeError(
            f"pymcu-avr-toolchain: no pre-built tarball for platform '{key}'.\n"
            f"Set PYMCU_AVR_TOOLCHAIN_URL to a custom tarball URL."
        )
    return f"{_PIO_BASE}/{filename}"


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    for cafile in [
        os.environ.get("SSL_CERT_FILE", ""),
        "/etc/ssl/cert.pem",
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt",
        "/usr/local/etc/openssl/cert.pem",
    ]:
        if cafile and os.path.isfile(cafile):
            try:
                ctx.load_verify_locations(cafile=cafile)
                return ctx
            except ssl.SSLError:
                continue
    try:
        import certifi  # noqa: PLC0415
        ctx.load_verify_locations(cafile=certifi.where())
    except (ImportError, ssl.SSLError):
        pass
    return ctx


def _download(url: str, dest: Path) -> None:
    ctx = _ssl_context()
    req = urllib.request.Request(url, headers={"User-Agent": "pymcu-avr-toolchain/1.0"})
    try:
        with urllib.request.urlopen(req, context=ctx) as resp, open(dest, "wb") as fh:
            total = int(resp.headers.get("Content-Length", 0) or 0)
            downloaded, next_report = 0, max(1 << 20, total // 20)
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
                downloaded += len(chunk)
                if total and downloaded >= next_report:
                    print(f"  ... {min(100, downloaded * 100 // total)}%", flush=True)
                    next_report += max(1 << 20, total // 20)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"pymcu-avr-toolchain: HTTP {exc.code} fetching toolchain.\n"
            f"URL: {url}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"pymcu-avr-toolchain: download failed — {exc}\nURL: {url}"
        ) from exc


def fetch_to_cache(
    cache_dir: Path,
    bin_dir: Path,
    sentinel: Path,
    cache_key: str,
    *,
    console=None,
) -> None:
    """Download the PlatformIO toolchain tarball and stage it into *cache_dir*."""
    def log(msg: str) -> None:
        if console is not None:
            console.print(msg)
        else:
            print(msg, flush=True)

    url = _tarball_url()
    log("[pymcu-avr-toolchain] No bundled toolchain found.")
    log("[pymcu-avr-toolchain] Downloading from PlatformIO registry:")
    log(f"  {url}")

    with tempfile.TemporaryDirectory(prefix="pymcu-avr-") as td:
        tmp = Path(td)
        archive = tmp / "toolchain.tar.gz"
        _download(url, archive)
        log("[pymcu-avr-toolchain] Extracting toolchain ...")
        cache_dir.mkdir(parents=True, exist_ok=True)
        # The PlatformIO tarball has no top-level wrapper directory —
        # contents are: bin/, avr/, lib/, libexec/, etc. at the root.
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(cache_dir)  # noqa: S202 — trusted PlatformIO CDN

    if not bin_dir.is_dir():
        raise RuntimeError(
            f"pymcu-avr-toolchain: bin/ not found after extraction in {cache_dir}"
        )

    if sys.platform != "win32":
        for search_dir in (bin_dir, cache_dir / "libexec"):
            if not search_dir.is_dir():
                continue
            for entry in search_dir.rglob("*"):
                if entry.is_file() and not entry.is_symlink():
                    with contextlib.suppress(OSError):
                        entry.chmod(entry.stat().st_mode | 0o111)

        # Create as/ld symlinks so avr-gcc 7.x finds them via COMPILER_PATH
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

        if sys.platform == "darwin":
            _codesign_darwin(cache_dir)

    sentinel.write_text(cache_key, encoding="utf-8")
    log(f"[pymcu-avr-toolchain] Toolchain ready at: {bin_dir}")


def _codesign_darwin(root: Path) -> None:
    """Ad-hoc codesign all Mach-O binaries and dylibs after staging on macOS.

    Binaries extracted from a tarball lose their original code signature (the
    file hash changes). macOS Sequoia+ kills any binary with an invalid
    signature. Replacing it with an ad-hoc signature ('-s -') is sufficient
    for local execution and requires no Apple Developer account.
    """
    import shutil, subprocess  # noqa: PLC0415
    codesign = shutil.which("codesign")
    if not codesign:
        return
    for search in (root / "bin", root / "lib", root / "libexec"):
        if not search.is_dir():
            continue
        for entry in search.rglob("*"):
            if not entry.is_file() or entry.is_symlink():
                continue
            with contextlib.suppress(Exception):
                subprocess.run(
                    [codesign, "--force", "-s", "-", str(entry)],
                    capture_output=True,
                )
