# pymcu-avr-toolchain

Pre-built **AVR-GCC toolchain** for the [PyMCU](https://github.com/begeistert/pymcu)
AVR (ATmega/ATtiny) backend.

The AVR backend compiles PyMCU's architecture-agnostic IR to AVR assembly
and then invokes three core tools to produce a flashable Intel HEX file:

```
avr-gcc   avr-as   avr-objcopy
```

(Full toolchain also includes `avr-g++`, `avr-ld`, `avr-ar`, `avr-objdump`,
`avr-size`, `avr-gdb`, and `avr-libc`.)

This package is the AVR counterpart of
[`pymcu-rp2040-toolchain`](https://github.com/begeistert/pymcu-rp2040-toolchain): a
platform-specific wheel whose only job is to ship those tools so that
`pip install pymcu-compiler[avr]` is fully self-contained — no separate
`avr-gcc` installation required on supported platforms.

The build script (`avr-gcc-build.sh`) is based on the work of
[Zak Kemble](https://github.com/ZakKemble/avr-gcc-build).

## License: GPL-3.0-or-later packaging + binaries

The bundled AVR-GCC toolchain is derived from **GCC** and **Binutils**, which
are licensed under **GPL-3.0-or-later** and **LGPL-3.0-or-later** respectively.
**avr-libc** is **BSD-2-Clause**. Because the wheel bundles GPL code, the
package as a whole is distributed under **GPL-3.0-or-later**.

- The GPL applies to the **toolchain binaries themselves**, not to the firmware
  your projects compile with them. Firmware produced by `avr-gcc` is your own
  work and carries no GPL obligation.
- **Contrast with RP2040:** `pymcu-rp2040-toolchain` bundles LLVM, which is
  Apache-2.0 WITH LLVM-exception (permissive, no copyleft). The isolation
  mechanism is the same — a separate optional package — but the license
  differs because the upstream compilers differ.

See `LICENSES/` and `NOTICE` for the full texts.

## Bundled versions

| Tool | Version |
|---|---|
| avr-gcc | 15.2.0 |
| avr-binutils | 2.45 |
| avr-gdb | 16.3 |
| avr-libc | latest |

## Installation

```bash
# Automatic — included when you install the AVR extra:
pip install pymcu-compiler[avr]

# Standalone:
pip install pymcu-avr-toolchain
```

Platform wheels are published for **Linux x86-64**, **Linux arm64**,
**macOS arm64**, and **Windows x86-64**. The wheels fit within PyPI's 100 MB
limit and are distributed directly on PyPI (no split like the RP2040 toolchain).

> **Linux arm64:** Built on GitHub's `ubuntu-24.04-arm` runner. The build
> script (`avr-gcc-build.sh`) was designed for Linux x64; the arm64 build is
> best-effort (`continue-on-error`) and may be absent from a release if the
> runner has issues. Fall back to `apt install gcc-avr binutils-avr avr-libc`
> in that case.

> **macOS Intel (x86-64):** The `osx-cross/avr` Homebrew tap does not provide
> x86-64 bottles for avr-gcc, so no Intel wheel is published. Intel Mac users
> can install via Homebrew (`brew tap osx-cross/avr && brew install avr-gcc`)
> and PyMCU detects the tools automatically via `PATH`.

## How the driver resolves tools

`AvrToolchain` (in `pymcu-avr`) checks these sources in order:

| Priority | Source |
|:---:|---|
| 1 | `pymcu_avr_toolchain.get_bin_dir()` — wheel bundle (seeded to cache on first call) |
| 2 | Shared cache `~/.pymcu/tools/<platform>/pymcu-avr-toolchain/<version>/bin/` |
| 3 | Common keg dirs (`/opt/homebrew/opt/avr-gcc/bin`, `/usr/bin`, …) |
| 4 | `PATH` |

A missing wheel never blocks a developer who already has `avr-gcc` installed.

## Inspecting installed tools

```bash
pymcu-avr-toolchain-info     # entry-point alias
python -m pymcu_avr_toolchain
```

Example output:

```
bin_dir: /Users/you/.pymcu/tools/darwin-arm64/pymcu-avr-toolchain/15.2.0/bin
manifest: {
  "gcc_version": "15.2.0",
  "as_version": "2.45",
  "platform": "darwin-arm64"
}
```

## For maintainers: publishing a new wheel

### Release process

1. Update `VER_GCC`, `VER_BINUTILS`, `VER_GDB` defaults in `avr-gcc-build.sh`
   and `version` in `python/pyproject.toml` to match the new GCC release.
2. Tag and push:
   ```bash
   git tag v15.2.0
   git push origin v15.2.0
   ```
3. The `build-wheels.yml` workflow fires automatically:
   - **Linux x64** — builds from source via `avr-gcc-build.sh` (~2 h);
     result is a fully static toolchain with no runtime dependencies.
   - **Linux arm64** — same script on `ubuntu-24.04-arm`; best-effort
     (`continue-on-error`), may be absent if the build fails.
   - **Windows x64** — installs pre-built MSYS2/MINGW64 packages
     (`mingw-w64-x86_64-avr-gcc`, `avr-libc`, `avr-binutils`).
   - **macOS arm64** — installs via Homebrew (`osx-cross/avr` tap) and
     bundles dylibs with rewritten `@rpath` for portability.
   - `collect-and-release` smoke-tests the Linux wheel and generates
     `SHA256SUMS`.
   - `publish-pypi` uploads all wheels + sdist to **public PyPI** via OIDC
     trusted publishing (no stored token required).
   - `publish-github-release` creates a GitHub Release and attaches all
     wheels and `SHA256SUMS` as downloadable assets.

### Required GitHub configuration

| Item | Where | Purpose |
|---|---|---|
| `release` environment | Repo → Settings → Environments | Gates OIDC publishing; add tag protection rule `v*` |

### Building a wheel locally (testing)

```bash
# Build the Linux toolchain from source:
bash avr-gcc-build.sh

# Build the wheel from the staged output:
AVRT_TOOLCHAIN_DIR=build/avr-gcc-15.2.0-x64-linux \
WHEEL_PLATFORM_TAG=manylinux_2_17_x86_64 \
uv build --wheel python/
```

Without `AVRT_TOOLCHAIN_DIR`, `hatch_build.py` looks for a pre-built
toolchain in `../output/avr-gcc-*-x64-linux` as a local fallback.

## Environment variables

| Variable | Effect |
|---|---|
| `AVRT_TOOLCHAIN_DIR` | Path to a staged AVR-GCC tree for `hatch_build.py` |
| `WHEEL_PLATFORM_TAG` | Override the wheel platform tag (e.g. `win_amd64`) |
| `PYMCU_TOOLS_DIR` | Override the `~/.pymcu/tools` cache root |
| `PYMCU_TOOLCHAIN_NO_SEEDING` | Set to `1` to skip seeding the cache; use the in-package `bin/` directly |

## Version history

| Package version | avr-gcc | avr-binutils |
|---|---|---|
| 15.2.0 | 15.2.0 | 2.45 |
