"""
Make the package importable when it is not installed.

test_toolchain_smoke.py exercises a real installation and needs the wheel in
place, but the unit tests only need the module. Falling back to src/ lets those
run in a bare checkout without pretending an install happened.
"""

import sys
from pathlib import Path

try:
    import pymcu_avr_toolchain  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
