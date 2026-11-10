"""Reject the TC32 optimization combination that broke the r21 startup."""

from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('makefile', ['Makefile', 'client.mk'])
def test_unsafe_compact_optimization_is_rejected_before_building(makefile):
    command = ['make', '-s', '-C', str(ROOT / 'src/telink'), '-f', makefile, 'help']
    rejected = subprocess.run([*command, 'OPT_LEVEL=-Os'], capture_output=True, text=True)
    assert rejected.returncode != 0
    assert 'TC32 -Os requires -fno-jump-tables' in rejected.stderr

    for flags in ('-O2', '-Os -fno-jump-tables'):
        accepted = subprocess.run([*command, 'OPT_LEVEL=' + flags],
                                  capture_output=True, text=True)
        assert accepted.returncode == 0, accepted.stderr
