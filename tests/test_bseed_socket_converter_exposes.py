import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "helper_scripts" / "make_z2m_custom_converters.py"


def _render(*extra: str) -> str:
    return subprocess.check_output(
        [sys.executable, str(GEN), *extra, "device_db.yaml"],
        cwd=ROOT,
        text=True,
    )


def _definition(text: str, zigbee_model: str) -> str:
    marker = f'"{zigbee_model}"'
    start = text.index(marker)
    next_start = text.find("\n    {\n        zigbeeModel:", start + len(marker))
    return text[start:] if next_start == -1 else text[start:next_start]


def test_bseed_pm_outlet_does_not_expose_dimming_rate():
    for args in [(), ("--z2m-v1",)]:
        definition = _definition(_render(*args), "TS011F-BS-PM")
        assert "switch_level_move_rate" not in definition
        assert "switch_long_press_duration" in definition
        assert "switch_relay_mode" in definition


def test_non_outlet_switch_keeps_dimming_rate():
    for args in [(), ("--z2m-v1",)]:
        definition = _definition(_render(*args), "TS0004-MC")
        assert "level_move_rate" in definition
