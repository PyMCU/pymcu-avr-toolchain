"""Runs the PyMCU AVR pipeline (avr-as -> avr-ld -> avr-objcopy) entirely on
wasm32-wasip1 modules and checks every .hex against the sha256 the native
toolchain produces.

    python -m pip install wasmtime
    python verify.py

Exit code 0 means every case matched.
"""

from __future__ import annotations

import json
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pymcu_wasi_toolchain as T  # noqa: E402

T.DIST = HERE / "wasm"
T.TOOLS = {
    "avr-as": T.DIST / "avr-as.wasm",
    "avr-ld": T.DIST / "avr-ld.wasm",
    "avr-objcopy": T.DIST / "avr-objcopy.wasm",
}
T.CACHE = Path(tempfile.gettempdir()) / (
    f"pymcu-cwasm-{platform.system().lower()}-{platform.machine()}"
)


def main() -> int:
    import wasmtime

    print(f"host      : {platform.system()} {platform.machine()} "
          f"python {platform.python_version()}")
    print(f"wasmtime  : {wasmtime.__file__}")

    cases = json.loads((HERE / "manifest.json").read_text())
    work_root = Path(tempfile.mkdtemp(prefix="pymcu-verify-"))

    t0 = time.perf_counter()
    tc = T.WasiToolchain()
    load = time.perf_counter() - t0

    ok = bad = 0
    build_time = 0.0
    for case in cases:
        src = HERE / "cases" / case["name"]
        emu = case["emulation"]
        t1 = time.perf_counter()
        try:
            hex_path, _ = T.build_hex(
                tc,
                src / "firmware.asm",
                src / "_pymcu.ld",
                HERE / "sysroot" / emu / "libgcc.a",
                HERE / "sysroot" / emu / "libm.a",
                work_root / case["name"],
                case["chip"],
                emu,
            )
        except RuntimeError as exc:
            bad += 1
            print(f"FAIL  {case['name']}: {exc}")
            continue
        build_time += time.perf_counter() - t1
        got = T.sha256(hex_path)
        if got == case["sha256"]:
            ok += 1
        else:
            bad += 1
            print(f"DIFF  {case['name']}: esperado {case['sha256'][:16]} obtenido {got[:16]}")

    shutil.rmtree(work_root, ignore_errors=True)
    print()
    print(f"carga de modulos : {load * 1000:.0f} ms")
    print(f"{len(cases)} builds       : {build_time * 1000:.0f} ms")
    print(f"identicos={ok}  distintos/fallidos={bad}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
