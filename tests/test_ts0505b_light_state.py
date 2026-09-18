import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FW = ROOT / "devices" / "ts0505b-mja6r5ix" / "firmware"
CORE = FW / "ts0505b_light_state.c"
HARNESS = ROOT / "tests" / "fixtures" / "ts0505b_light_state_harness.c"


def test_ts0505b_light_state_host_harness():
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        pytest.skip("No host C compiler; Linux CI executes this harness")

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / ("ts0505b-light.exe" if os.name == "nt" else "ts0505b-light")
        compiled = subprocess.run([
            cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
            "-I", str(FW), str(CORE), str(HARNESS), "-o", str(out),
        ], capture_output=True, text=True)
        assert compiled.returncode == 0, (
            f"compile failed\nSTDOUT:\n{compiled.stdout}\nSTDERR:\n{compiled.stderr}"
        )
        run = subprocess.run([str(out)], capture_output=True, text=True, timeout=10)
        assert run.returncode == 0, (
            f"harness failed\nSTDOUT:\n{run.stdout}\nSTDERR:\n{run.stderr}"
        )
        assert "light-state harness PASS" in run.stdout
