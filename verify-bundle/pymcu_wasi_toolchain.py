"""Prototype: PyMCU's AVR toolchain (avr-as / avr-ld / avr-objcopy) run as
wasm32-wasip1 modules under wasmtime-py, with no native toolchain binary."""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from wasmtime import Engine, ExitTrap, Linker, Module, Store, WasiConfig

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"

# Serialized modules are tied to the host OS, CPU and wasmtime build, so the
# cache can never travel inside the (py3-none-any) wheel.
import platform
from importlib.metadata import version as _pkg_version

CACHE = HERE / (
    f".cwasm-{platform.system().lower()}-{platform.machine()}-"
    f"wasmtime{_pkg_version('wasmtime')}"
)

TOOLS = {
    "avr-as": DIST / "avr-as.wasm",
    "avr-ld": DIST / "avr-ld.wasm",
    "avr-objcopy": DIST / "avr-objcopy.wasm",
}


@dataclass
class RunResult:
    tool: str
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    seconds: float


@dataclass
class Timing:
    load: dict[str, float] = field(default_factory=dict)
    run: list[RunResult] = field(default_factory=list)


class WasiToolchain:
    """Loads each tool once per process and re-instantiates it per invocation.

    A fresh Store + instance per run is required: these are single-shot CLI
    programs that call proc_exit, so their linear memory cannot be reused.
    Module compilation, which is the expensive part, is done once and can be
    persisted to disk with Module.serialize.
    """

    def __init__(self, use_cache: bool = True) -> None:
        self.engine = Engine()
        self.linker = Linker(self.engine)
        self.linker.define_wasi()
        self.modules: dict[str, Module] = {}
        self.load_times: dict[str, float] = {}
        CACHE.mkdir(exist_ok=True)
        for name, path in TOOLS.items():
            t0 = time.perf_counter()
            cached = CACHE / (path.stem + ".cwasm")
            if use_cache and cached.exists() and cached.stat().st_mtime >= path.stat().st_mtime:
                mod = Module.deserialize_file(self.engine, str(cached))
            else:
                mod = Module.from_file(self.engine, str(path))
                if use_cache:
                    cached.write_bytes(mod.serialize())
            self.modules[name] = mod
            self.load_times[name] = time.perf_counter() - t0

    def run(self, tool: str, argv: list[str], workdir: Path) -> RunResult:
        # The capture files live outside the preopened directory so they never
        # become link inputs, and are read only after the store is dropped:
        # on Windows a file cannot be deleted while a handle is still open.
        with tempfile.TemporaryDirectory(prefix="pymcu-wasi-") as tmp:
            out = Path(tmp) / "stdout"
            err = Path(tmp) / "stderr"

            cfg = WasiConfig()
            cfg.argv = [tool, *argv]
            cfg.preopen_dir(str(workdir), "/work")
            cfg.stdout_file = str(out)
            cfg.stderr_file = str(err)

            store = Store(self.engine)
            store.set_wasi(cfg)

            t0 = time.perf_counter()
            code = 0
            try:
                instance = self.linker.instantiate(store, self.modules[tool])
                instance.exports(store)["_start"](store)
            except ExitTrap as exc:
                code = exc.code
            elapsed = time.perf_counter() - t0
            del store

            def read(p: Path) -> str:
                if not p.exists():
                    return ""
                return p.read_bytes().decode("utf-8", errors="replace")

            res = RunResult(
                tool=tool,
                argv=argv,
                exit_code=code,
                stdout=read(out),
                stderr=read(err),
                seconds=elapsed,
            )

        if code != 0:
            raise RuntimeError(f"{tool} failed (exit {code}):\n{res.stderr or res.stdout}")
        return res


def build_hex(
    tc: WasiToolchain,
    asm: Path,
    ld_script: Path,
    libgcc: Path,
    libm: Path,
    workdir: Path,
    chip: str = "atmega328p",
    emulation: str = "avr5",
    data_origin: str = "0x800100",
) -> tuple[Path, Timing]:
    """Reproduces the driver's non-FFI AVR pipeline: as -> ld -> objcopy.

    The link argv mirrors what avr-gcc's collect2 passes to avr-ld for
    -mmcu=<chip> -mrelax -nostartfiles -nodefaultlibs -T <script>.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "lib" / "gcc").mkdir(parents=True, exist_ok=True)
    (workdir / "lib" / "avr").mkdir(parents=True, exist_ok=True)
    shutil.copy(asm, workdir / "firmware.asm")
    shutil.copy(ld_script, workdir / "_pymcu.ld")
    shutil.copy(libgcc, workdir / "lib" / "gcc" / "libgcc.a")
    shutil.copy(libm, workdir / "lib" / "avr" / "libm.a")

    timing = Timing(load=dict(tc.load_times))

    timing.run.append(tc.run("avr-as", [
        f"-mmcu={chip}", "-mno-skip-bug",
        "/work/firmware.asm", "-o", "/work/firmware.o",
    ], workdir))

    timing.run.append(tc.run("avr-ld", [
        f"-m{emulation}", "-Tdata", data_origin, "--relax",
        "-o", "/work/firmware.elf",
        "-L/work/lib/gcc", "-L/work/lib/avr",
        "/work/firmware.o", "-lm", "-lgcc",
        "-T", "/work/_pymcu.ld",
    ], workdir))

    timing.run.append(tc.run("avr-objcopy", [
        "-O", "ihex", "-R", ".eeprom",
        "/work/firmware.elf", "/work/firmware.hex",
    ], workdir))

    return workdir / "firmware.hex", timing


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if len(sys.argv) < 6:
        print(
            "usage: pymcu_wasi_toolchain.py <firmware.asm> <_pymcu.ld> "
            "<libgcc.a> <libm.a> <workdir> [chip] [emulation]",
            file=sys.stderr,
        )
        return 2
    asm, ld, libgcc, libm, work = (Path(a) for a in sys.argv[1:6])
    chip = sys.argv[6] if len(sys.argv) > 6 else "atmega328p"
    emu = sys.argv[7] if len(sys.argv) > 7 else "avr5"

    t0 = time.perf_counter()
    tc = WasiToolchain()
    load_total = time.perf_counter() - t0

    hex_path, timing = build_hex(tc, asm, ld, libgcc, libm, work, chip, emu)

    print(f"module load total : {load_total * 1000:8.1f} ms")
    for name, secs in timing.load.items():
        print(f"  {name:<12}    {secs * 1000:8.1f} ms")
    for r in timing.run:
        print(f"{r.tool:<18}: {r.seconds * 1000:8.1f} ms")
    total = sum(r.seconds for r in timing.run)
    print(f"{'pipeline total':<18}: {total * 1000:8.1f} ms")
    print(f"sha256 {hex_path.name}: {sha256(hex_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
