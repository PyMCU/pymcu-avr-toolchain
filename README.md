# pymcu-avr-toolchain

Pre-built **AVR-GCC toolchain** for the [PyMCU](https://github.com/PyMCU/pymcu)
AVR (ATmega/ATtiny) backend.

The AVR backend compiles PyMCU's architecture-agnostic IR to AVR assembly
and then invokes three core tools to produce a flashable Intel HEX file:

```
avr-gcc   avr-as   avr-objcopy
```

(Full toolchain also includes `avr-g++`, `avr-ld`, `avr-ar`, `avr-objdump`,
`avr-size`, `avr-gdb`, and `avr-libc`.)

This package is the AVR counterpart of
[`pymcu-arm-toolchain`](https://github.com/PyMCU/pymcu-arm-toolchain): a
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
- **Contrast with ARM:** `pymcu-arm-toolchain` bundles LLVM, which is
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
pip install pymcu-avr-toolchain
```

`avr-gcc` + `avr-libc` together exceed PyPI's 100 MB per-file ceiling
(~150 MB per platform). The distribution is therefore split:

| Channel | What it contains |
|---|---|
| **PyPI** (`pip install pymcu-avr-toolchain`) | Lightweight stub (~14 KB) |
| **[GitHub Releases](https://github.com/PyMCU/avr-gcc-build/releases)** | Binary wheels with the full toolchain (~150 MB each) |

**The binary wheel is downloaded automatically.** The first call to
`get_bin_dir()` (or any PyMCU build that needs the toolchain) downloads the
correct wheel for your platform from GitHub Releases and extracts it to the
shared cache at `~/.pymcu/tools/`. Subsequent calls are instant.

```python
import pymcu_avr_toolchain
bin_dir = pymcu_avr_toolchain.get_bin_dir()   # downloads on first call
```

For CI or air-gapped environments, set `PYMCU_AVR_WHEEL_URL` to override
the download URL, or install the binary wheel directly:

```bash
# Linux x86-64
pip install https://github.com/PyMCU/avr-gcc-build/releases/download/v15.2.0.post5/pymcu_avr_toolchain-15.2.0.post5-py3-none-manylinux_2_17_x86_64.whl

# Linux arm64 (best-effort build)
pip install https://github.com/PyMCU/avr-gcc-build/releases/download/v15.2.0.post5/pymcu_avr_toolchain-15.2.0.post5-py3-none-manylinux_2_17_aarch64.whl

# macOS Apple Silicon
pip install https://github.com/PyMCU/avr-gcc-build/releases/download/v15.2.0.post5/pymcu_avr_toolchain-15.2.0.post5-py3-none-macosx_14_0_arm64.whl

# Any other host, Windows included: pymcu-avr-toolchain-wasi, one py3-none-any
# wheel that runs the toolchain under WASM. This package ships native wheels
# only for the three platforms above.
pip install pymcu-avr-toolchain-wasi
```

### System toolchain (alternative)

If you already have `avr-gcc` installed, the `AvrToolchain` driver finds
the tools automatically via `PATH` — no package needed:

```bash
# macOS
brew tap osx-cross/avr && brew install avr-gcc

# Debian/Ubuntu
apt install gcc-avr binutils-avr avr-libc
```

> **Linux arm64:** Built on GitHub's `ubuntu-24.04-arm` runner. The build
> script was designed for Linux x64; the arm64 build is best-effort
> (`continue-on-error`) and may be absent from a release if the runner fails.

> **macOS Intel (x86-64):** The `osx-cross/avr` tap does not provide x86-64
> bottles for avr-gcc, so no Intel wheel is published.

## How the driver resolves tools

`AvrToolchain` (in `pymcu-avr`) checks these sources in order:

| Priority | Source |
|:---:|---|
| 1 | `pymcu_avr_toolchain.get_bin_dir()` — wheel bundle or auto-downloaded cache |
| 2 | Shared cache `~/.pymcu/tools/<platform>/pymcu-avr-toolchain/<version>/bin/` |
| 3 | Common keg dirs (`/opt/homebrew/opt/avr-gcc/bin`, `/usr/bin`, …) |
| 4 | `PATH` |

A missing wheel never blocks a developer who already has `avr-gcc` installed.

## Inspecting installed tools

```bash
pymcu-avr-toolchain-info     # entry-point
python -m pymcu_avr_toolchain
```

## For maintainers: publishing a new wheel

### Release process

> [!IMPORTANT]
> **The local branch is `master`; the remote branch is `main`.** Push with
> `git push <remote> master:main`. A plain `git push <remote> main` fails with
> `src refspec main does not match any`, and the danger is what comes next: if
> you chain a `workflow_dispatch` after it with `;` instead of `&&`, the dispatch
> runs anyway and builds the commit you did NOT push. The run looks healthy and
> tests the wrong tree. The same mismatch makes `git log origin/main..main` print
> nothing and report zero unpushed commits when there are many, because the
> command errors and the error is easy to discard. Both of those have happened.
>
> Also check which remote you are pushing to: `origin` is the archived
> `avr-gcc-build`. The live repo is `PyMCU/pymcu-avr-toolchain`.

1. Update `VER_GCC`, `VER_BINUTILS`, `VER_GDB` in `avr-gcc-build.sh`
   and `version` in `python/pyproject.toml`.
2. Dry run first, on `main`, with no tag:
   ```bash
   git push live master:main
   gh workflow run build-wheels.yml --repo PyMCU/pymcu-avr-toolchain \
       --ref main -f cache_bust="$(date +%s)"
   ```
   `publish-pypi` is gated on `startsWith(github.ref, 'refs/tags/v')`, so a
   dispatch cannot publish. Bust the cache when a previous attempt left a
   half-built tree that would otherwise survive into the next one.
3. Only once all three builds are green, tag and push:
   ```bash
   git tag v15.2.0
   git push live v15.2.0
   ```
4. The `build-wheels.yml` workflow then:
   - Builds one binary wheel per platform: Linux x64, Linux arm64, macOS arm64.
     Those three are the whole of this package's native support. Every other
     host, Windows included, uses `pymcu-avr-toolchain-wasi`, a single
     `py3-none-any` wheel that runs the toolchain under WASM.
   - Publishes the **binary wheels to PyPI** via `pypa/gh-action-pypi-publish`,
     using OIDC trusted publishing (no stored token), and attaches the same
     wheels to a GitHub Release.
   - There is **no sdist**. That matters: if one platform's wheel is missing from
     a release, pip does not fail for that platform's users. It walks back to the
     newest version that does have a compatible wheel and installs it silently,
     so those users keep the previous toolchain with nothing naming it. Never
     publish a partial matrix. `check-consistency` will not catch this, because it
     compares wheels within one release; what stops it is that `publish-pypi`
     needs `smoke-test` and `check-consistency`, and both need every build job.

### Required GitHub configuration

| Item | Where | Purpose |
|---|---|---|
| `release` environment | Repo → Settings → Environments | Gates OIDC publishing; add tag protection rule `v*` |

### Building a wheel locally

```bash
AVRT_TOOLCHAIN_DIR=build/avr-gcc-15.2.0-x64-linux \
WHEEL_PLATFORM_TAG=manylinux_2_17_x86_64 \
uv build --wheel python/
```

## Environment variables

| Variable | Effect |
|---|---|
| `AVRT_TOOLCHAIN_DIR` | Path to a staged AVR-GCC tree for `hatch_build.py` |
| `AVRT_GCC_VERSION` | Inject GCC version string for cross-build CI steps |
| `WHEEL_PLATFORM_TAG` | Override the wheel platform tag (e.g. `manylinux_2_17_x86_64`) |
| `PYMCU_AVR_WHEEL_URL` | Override the binary wheel download URL (air-gapped installs) |
| `PYMCU_TOOLS_DIR` | Override the `~/.pymcu/tools` cache root |
| `PYMCU_TOOLCHAIN_NO_SEEDING` | Set to `1` to use the in-package `bin/` directly without seeding the cache |

## Version history

| Package version | avr-gcc | Notes |
|---|---|---|
| 15.2.0.post5 | 15.2.0 | Fix project URLs; auto-download on first use |
| 15.2.0 | 15.2.0 | Initial release |
