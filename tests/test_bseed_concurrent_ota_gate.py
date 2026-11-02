"""Parallel OTA is allowed only for explicitly allowlisted IEEE addresses."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))

from bseed_targeted_z2m_ota import unexpected_active_updates  # noqa: E402

TARGET = '0xa4c138241e3de538'
GLEDOPTO = '0xa4c13850cfcdb3a4'
INVENTORY = [
    {'friendly_name': 'KitchenSocketLeft', 'ieee_address': TARGET},
    {'friendly_name': 'LivingRoomCircleLightDimmer', 'ieee_address': GLEDOPTO},
    {'friendly_name': 'Other', 'ieee_address': '0xa4c138000000ffff'},
]


def test_no_allowlist_blocks_any_active_update():
    assert unexpected_active_updates(['LivingRoomCircleLightDimmer'], INVENTORY, [], TARGET) == [
        'LivingRoomCircleLightDimmer']


def test_allowlisted_device_may_update_in_parallel():
    assert unexpected_active_updates(['LivingRoomCircleLightDimmer'], INVENTORY, [GLEDOPTO], TARGET) == []


def test_non_allowlisted_device_still_blocks():
    assert unexpected_active_updates(
        ['LivingRoomCircleLightDimmer', 'Other'], INVENTORY, [GLEDOPTO], TARGET) == ['Other']


def test_unresolvable_name_always_blocks():
    assert unexpected_active_updates(['Ghost'], INVENTORY, [GLEDOPTO], TARGET) == ['Ghost']


def test_target_cannot_be_exempted():
    with pytest.raises(ValueError):
        unexpected_active_updates([], INVENTORY, [TARGET], TARGET)
