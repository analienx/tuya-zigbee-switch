"""Regression tests for role-appropriate Zigbee2MQTT controls."""

from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "helper_scripts" / "make_z2m_custom_converters.py"
DB = ROOT / "device_db.yaml"
SOCKET_ONLY_CONTROLS = (
    "romasku.pressAction(", "romasku.switchMode(", "romasku.switchAction(",
    "romasku.relayMode(", "romasku.relayIndex(", "romasku.bindedMode(",
    "romasku.longPressDuration(", "romasku.levelMoveRate(", "genMultistateInput",
)


def _generate(z2m_v1: bool) -> str:
    args = [sys.executable, str(GENERATOR)]
    if z2m_v1:
        args.append("--z2m-v1")
    args.append(str(DB))
    return subprocess.check_output(args, cwd=ROOT, text=True)


def _block(output: str, zigbee_model: str) -> str:
    marker = f'            "{zigbee_model}",'
    start = output.index(marker)
    next_definition = output.find("\n    {\n        zigbeeModel:", start + len(marker))
    return output[start:] if next_definition == -1 else output[start:next_definition]


@pytest.mark.parametrize("z2m_v1", [False, True])
def test_outlets_hide_wall_switch_controls(z2m_v1: bool) -> None:
    output = _generate(z2m_v1)
    for model in ("TS011F-BS-PM", "TS011F-BS-PM-1", "TS011F-BS-PM-2"):
        block = _block(output, model)
        assert not any(control in block for control in SOCKET_ONLY_CONTROLS)
        assert "onOff({ endpointNames:" in block
    assert "electricityMeter()" in _block(output, "TS011F-BS-PM")


@pytest.mark.parametrize("z2m_v1", [False, True])
def test_wall_switches_keep_switch_controls(z2m_v1: bool) -> None:
    block = _block(_generate(z2m_v1), "TS0001-AVT")
    assert all(control in block for control in SOCKET_ONLY_CONTROLS)
