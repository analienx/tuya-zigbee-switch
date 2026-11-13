"""The converter recognises BSEED PM builds by family, not by a per-release list.

That is only safe while every build of the family uses the same scales, so this
test pins the firmware's scale constants to the converter's fixed contract. A
firmware change to the scales fails here: give such a build a new family.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
from bseed_converter_release import family_accepts, pm_build_family, REQUIRED_PM_BUILDS  # noqa: E402

CONVERTERS = [ROOT / 'zigbee2mqtt' / 'converters' / 'switch_custom.js',
              ROOT / 'helper_scripts' / 'templates' / 'switch_custom.js.jinja']


def firmware_assignments(path):
    text = (ROOT / path).read_text(encoding='utf-8')
    return {name: int(value) for name, value in re.findall(r'cluster->(\w+)\s*=\s*(\d+);', text)}


def test_firmware_scales_match_the_converter_contract():
    meas = firmware_assignments('src/zigbee/electrical_measurement_cluster.c')
    assert {key: meas[key] for key in ('ac_voltage_multiplier', 'ac_voltage_divisor', 'ac_current_multiplier',
                                       'ac_current_divisor', 'ac_power_multiplier', 'ac_power_divisor')} == {
        'ac_voltage_multiplier': 1, 'ac_voltage_divisor': 100, 'ac_current_multiplier': 1,
        'ac_current_divisor': 1000, 'ac_power_multiplier': 1, 'ac_power_divisor': 1}
    metering = firmware_assignments('src/zigbee/metering_cluster.c')
    assert (metering['multiplier'], metering['divisor']) == (1, 1000)
    for path in CONVERTERS:
        text = path.read_text(encoding='utf-8')
        assert ('acVoltageMultiplier: 1, acVoltageDivisor: 100,\n            acCurrentMultiplier: 1, '
                'acCurrentDivisor: 1000, acPowerMultiplier: 1, acPowerDivisor: 1') in text, path
        assert 'seMetering: {multiplier: 1, divisor: 1000}' in text, path


@pytest.mark.parametrize('path', CONVERTERS, ids=lambda p: p.name)
def test_family_rule_replaces_the_release_list(path):
    text = path.read_text(encoding='utf-8')
    assert 'knownBuilds' not in text
    family = pm_build_family(text)
    for build in REQUIRED_PM_BUILDS + ('1.2.5-bseedr20', '1.2.5-bseedr21', '1.2.5-bseedr22',
                                       '1.2.5-bseedcli25', '1.2.5-bseedr23', '1.2.5-bseedcli99'):
        assert family_accepts(family, build), build
    for build in ('1.2.5-bseedr6', '1.2.5-bseedcli5', '1.2.5-bseedr10', '1.2.5-bseedcli7',
                  '1.2.5-bseedv8u4', '1.2.5-bseedr22x', '1.2.6-bseedr23', '1.1.2-anx1', '', None):
        assert not family_accepts(family, build), build
