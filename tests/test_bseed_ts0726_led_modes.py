"""The TS0726 five-state indicator UI must remain isolated from socket converters."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ts0726_led_modes_generator_is_scoped_and_regenerable():
    result = subprocess.run(
        [sys.executable, "helper_scripts/make_z2m_custom_converters.py", "device_db.yaml"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    output = result.stdout
    controller = output.split('model: "EC-GL86ZPCS31",', 1)[1].split("\n    {", 1)[0]
    for relay in ("left", "middle", "right"):
        expected = f'romasku.bseedTs0726IndicatorMode("relay_{relay}_indicator_mode", "relay_{relay}")'
        assert controller.count(expected) == 1
    assert output.count("romasku.bseedTs0726IndicatorMode(") == 3
    assert controller.count("romasku.relayIndicatorMode(") == 0
    assert '"Physical output": 3, "Binding status": 4' in output
    assert "electricityMeter(" not in controller
    assert "bseedSocketRelayOnOff(" not in controller
    assert "romasku.relayIndicatorMode(" in output  # Other devices retain their existing controls.
    persisted = (ROOT / "zigbee2mqtt/converters/switch_custom.js").read_text(encoding="utf8")
    assert persisted == output
