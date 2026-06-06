# pymcu-avr-toolchain

Pre-built **AVR-GCC toolchain** packaged as a pip-installable Python wheel for
the [PyMCU](https://github.com/begeistert/pymcu) AVR backend.

The build script (`avr-gcc-build.sh`) is based on the excellent work of
[Zak Kemble](https://github.com/ZakKemble/avr-gcc-build). This repo extends
it with a Python packaging layer so that `pip install pymcu-compiler[avr]`
is fully self-contained — no separate toolchain installation required on
supported platforms.

## Bundled tools and versions

| Tool | Version |
|---|---|
| avr-gcc | 15.2.0 |
| avr-binutils (as, ld, objcopy, objdump, …) | 2.45 |
| avr-gdb | 16.3 |
| avr-libc | latest |

## License

The Python packaging code is **MIT**. The bundled AVR-GCC toolchain is
derived from GCC and Binutils, which are licensed under **GPL-3.0-or-later**
and **LGPL-3.0-or-later** respectively. avr-libc is **BSD-2-Clause**.

See `LICENSES/` and `NOTICE` for the full texts. Because the binaries include
GPL code, the wheel itself is distributed under GPL-3.0-or-later — the same
terms as `pymcu-avr-toolchain` on PyPI.

> **Note on copyleft scope:** The GPL applies to the toolchain binaries
> themselves, not to the firmware your projects compile with them. Firmware
> produced by `avr-gcc` is your own work and carries no GPL obligation.

## Installation

```bash
# Automatic — included when you install the AVR extra:
pip install pymcu-compiler[avr]

# Standalone:
pip install pymcu-avr-toolchain
```

Platform wheels are published for **Linux x86-64**, **macOS arm64**, and
**Windows x86-64**.

> **macOS Intel (x86-64):** The `osx-cross/avr` Homebrew tap does not provide
> x86-64 bottles for avr-gcc. Intel Mac users can install via Homebrew
> (`brew tap osx-cross/avr && brew install avr-gcc`) and PyMCU will detect
> it automatically.

## Usage

```python
import pymcu_avr_toolchain

# Directory containing avr-gcc, avr-as, avr-objcopy, etc.
bin_dir = pymcu_avr_toolchain.get_bin_dir()

# Path to a specific binary (appends .exe on Windows)
avr_gcc = pymcu_avr_toolchain.get_tool("avr-gcc")

# Bundled GCC version
print(pymcu_avr_toolchain.toolchain_version())  # "15.2.0"
```

Or from the command line:

```bash
pymcu-avr-toolchain-info
python -m pymcu_avr_toolchain
```

## Environment variables

| Variable | Effect |
|---|---|
| `PYMCU_TOOLCHAIN_NO_SEEDING` | Set to `1` to skip seeding `~/.pymcu/tools/` and use the in-package `bin/` directly |
| `PYMCU_TOOLS_DIR` | Override the `~/.pymcu/tools/` cache root |

## For maintainers: publishing a new release

### Prerequisites

- PyPI trusted publisher configured for this repo (OIDC, no stored token)
- GitHub environment `release` with tag protection rule `v*`

### Release process

1. Update `VER_GCC`, `VER_BINUTILS`, `VER_GDB` defaults in `avr-gcc-build.sh`
   and `version` in `python/pyproject.toml` to match the new GCC version.
2. Tag and push:
   ```bash
   git tag v15.2.0
   git push origin v15.2.0
   ```
3. The `build-wheels.yml` workflow fires automatically:
   - **Linux x64** — builds from source via `avr-gcc-build.sh` (~2 h).
   - **Windows x64** — installs pre-built MSYS2/MINGW64 packages.
   - **macOS arm64** — installs via Homebrew (`osx-cross/avr` tap) and
     bundles dylibs with rewritten `@rpath` for portability.
   - `collect-and-release` smoke-tests the Linux wheel and generates
     `SHA256SUMS`.
   - `publish-pypi` uploads all wheels + sdist to **public PyPI** via OIDC
     trusted publishing (no stored token required).
   - `publish-github-release` creates a GitHub Release and attaches all
     wheels and `SHA256SUMS` as downloadable assets.

### Building locally

```bash
# Build the Linux toolchain (requires Docker or a Linux machine with build deps):
bash avr-gcc-build.sh

# Stage the output and build the wheel:
AVRT_TOOLCHAIN_DIR=build/avr-gcc-15.2.0-x64-linux \
WHEEL_PLATFORM_TAG=manylinux_2_17_x86_64 \
uv build --wheel python/
```

## Credits

- **[Zak Kemble](https://github.com/ZakKemble)** — original `avr-gcc-build.sh`
  build script and pre-built releases.
- **[PyMCU](https://github.com/begeistert/pymcu)** — Python packaging layer,
  CI pipeline, and PyPI distribution.
