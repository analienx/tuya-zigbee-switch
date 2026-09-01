"""Tests for the deterministic Z2M converter fingerprint generation.

Two built devices sharing a `zb_model` string would previously both emit a
bare `zigbeeModel`, making Z2M matching order-dependent and ambiguous. The
generator now detects the collision and pins such definitions with a
`fingerprint` on (manufacturerName, modelID).
"""

import subprocess
import sys
from pathlib import Path

import pytest

HELPER = Path("helper_scripts/make_z2m_custom_converters.py")

DB_TEMPLATE = """
colliding_a:
  human_name: Collision A
  output: relay
  device_type: router
  stock_model_name: {model}
  stock_manufacturer_name: mfg_a
  stock_converter_model: conv_a
  tuya_module: ZT3L
  mcu_family: Telink
  mcu: TLSR8258
  config_str: mfg_a;{model};LC4;SB1u;RC2;IC0;SB7u;RC3;ID7;SB4u;RD2;IB5;M;
  build: yes
colliding_b:
  human_name: Collision B
  output: relay
  device_type: router
  stock_model_name: {model}
  stock_manufacturer_name: mfg_b
  stock_converter_model: conv_b
  tuya_module: ZT3L
  mcu_family: Telink
  mcu: TLSR8258
  config_str: mfg_b;{model};LD4;SA1u;RB4;IC1;SC2u;RD2;IB5;M;
  build: yes
unique:
  human_name: Unique device
  output: relay
  device_type: router
  stock_model_name: UNIQUE-1
  stock_manufacturer_name: mfg_c
  stock_converter_model: conv_c
  tuya_module: ZT3L
  mcu_family: Telink
  mcu: TLSR8258
  config_str: mfg_c;UNIQUE-1;SB0u;RB1;M;
  build: yes
"""


@pytest.fixture()
def run_generator(tmp_path: Path):
    def _run(model: str) -> str:
        db_file = tmp_path / "device_db.yaml"
        db_file.write_text(DB_TEMPLATE.format(model=model))
        result = subprocess.run(
            [sys.executable, str(HELPER), str(db_file)],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    return _run


def test_colliding_models_use_fingerprints(run_generator) -> None:
    js = run_generator("TS0726-TEST")

    # Both colliding definitions are pinned to their own manufacturer.
    assert (
        '{ manufacturerName: "mfg_a", modelID: "TS0726-TEST" }' in js
    )
    assert (
        '{ manufacturerName: "mfg_b", modelID: "TS0726-TEST" }' in js
    )
    # No bare zigbeeModel may remain for the ambiguous model.
    assert '"TS0726-TEST"' not in js.split("fingerprint:")[0].split(
        "const definitions"
    )[-1]


def test_unique_model_keeps_zigbee_model(run_generator) -> None:
    js = run_generator("TS0726-TEST")

    # The non-colliding device keeps the classic zigbeeModel matching.
    assert 'zigbeeModel: [\n            "UNIQUE-1",' in js


def test_generation_is_reproducible(run_generator) -> None:
    assert run_generator("TS0726-TEST") == run_generator("TS0726-TEST")
