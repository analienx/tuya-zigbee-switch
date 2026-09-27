"""Optimized Python must never bypass the legacy live runner's assertions."""
from pathlib import Path
import subprocess
import sys


def test_optimized_runner_refuses_before_any_io():
    runner = Path(__file__).resolve().parents[1] / 'helper_scripts/bseed_targeted_z2m_ota.py'
    result = subprocess.run([sys.executable, '-O', str(runner), '--help'],
                            text=True, capture_output=True, timeout=10)
    assert result.returncode != 0
    assert 'requires Python without -O or PYTHONOPTIMIZE' in result.stderr
    assert 'usage:' not in result.stdout
