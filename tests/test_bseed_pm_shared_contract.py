"""Shared PM protocol assertions applied to both build roles, never to stock Tuya.

Static assertions supplement (not replace) the two real Telink builds and live ZCL gate.
"""
from pathlib import Path
import re
import pytest

ROOT = Path(__file__).resolve().parents[1]
READS = {'ZCL_ATTR_ELEC_MEAS_RMS_VOLTAGE': ('0x0505','ZCL_DATA_TYPE_UINT16'),
         'ZCL_ATTR_ELEC_MEAS_RMS_CURRENT': ('0x0508','ZCL_DATA_TYPE_UINT16'),
         'ZCL_ATTR_ELEC_MEAS_ACTIVE_POWER': ('0x050B','ZCL_DATA_TYPE_INT16'),
         'ZCL_ATTR_METERING_CURRENT_SUMMATION_DELIVERED': ('0x0000','ZCL_DATA_TYPE_UINT48'),
         'ZCL_ATTR_METERING_DIVISOR': ('0x0302','ZCL_DATA_TYPE_UINT24')}


@pytest.mark.parametrize('role,makefile,build_script', [
    ('Router','src/telink/Makefile','make_scripts/build_bseed_ts011f_pm_v8.sh'),
    ('EndDevice','src/telink/client.mk','make_scripts/build_bseed_mains_client.sh')])
def test_both_roles_use_shared_pm_hal_and_meter(role,makefile,build_script):
    mk=(ROOT/makefile).read_text(encoding='utf8')
    script=(ROOT/build_script).read_text(encoding='utf8')
    hal=(ROOT/'src/telink/hal/zigbee_zcl.c').read_text(encoding='utf8')
    for callback in ('register_pm_electrical_attrs','register_pm_metering_attrs'):
        assert callback in hal, f'{role}: missing shared attribute registration'
    assert 'BSEED_PM_B28WRPVX=1' in script
    assert 'HLW8012_VOLTAGE_MULTIPLIER=161460' in script or 'VOLTAGE_MULTIPLIER=161460' in script
    assert 'HLW8012_CURRENT_MULTIPLIER=144679' in script or 'CURRENT_MULTIPLIER=144679' in script
    assert 'HLW8012_POWER_MULTIPLIER=16989' in script or 'POWER_MULTIPLIER=16989' in script
    if role=='Router':
        assert '-lzb_router' in mk and 'DEVICE_TYPE=router' in script
    else:
        assert '-lzb_ed' in mk and '-DBSEED_MAINS_CLIENT=1' in mk
        assert 'client.mk build' in script


@pytest.mark.parametrize('symbol,expected_type', [(symbol,kind) for symbol,(_id,kind) in READS.items()])
def test_pm_zcl_attribute_read_has_correct_wire_type(symbol,expected_type):
    source_name='metering_cluster.c' if symbol.startswith('ZCL_ATTR_METERING') else 'electrical_measurement_cluster.c'
    source=(ROOT/'src/zigbee'/source_name).read_text(encoding='utf8')
    constants=(ROOT/'src/zigbee/consts.h').read_text(encoding='utf8')
    assert re.search(rf'#define\s+{symbol}\s+{READS[symbol][0]}\b',constants)
    pattern=rf'SETUP_ATTR\(\d+,\s*{symbol},\s*{expected_type},\s*ATTR_READONLY'
    assert re.search(pattern,source), f'{symbol}: missing or incorrectly typed read attribute'


def test_both_role_meter_scaling_is_identical_in_shared_source():
    electrical=(ROOT/'src/zigbee/electrical_measurement_cluster.c').read_text(encoding='utf8')
    metering=(ROOT/'src/zigbee/metering_cluster.c').read_text(encoding='utf8')
    for attr,divisor in (('ac_voltage_divisor',100),('ac_current_divisor',1000),('ac_power_divisor',1)):
        assert re.search(rf'cluster->{attr}\s*=\s*{divisor};',electrical)
    assert re.search(r'cluster->divisor\s*=\s*1000;',metering)
    assert 'NV_ITEM_ENERGY_ACCUMULATION(cluster->endpoint)' in metering
    assert 'if (current_energy >= cluster->last_energy_value)' in metering


def test_router_candidate_is_new_image_not_relabelled_published_v8u4():
    build=(ROOT/'make_scripts/build_bseed_ts011f_pm_v8.sh').read_text(encoding='utf8')
    assert "SW_BUILD='1.2.5-bseedv8u4'" in build
    assert "FILE_VERSION_HEX='0x12053007'" in build
    assert 'BSEED_PM_ROUTER_CANDIDATE:-0' in build
    assert "SW_BUILD='1.2.5-bseedv8u5-rc3'" in build
    assert "FILE_VERSION_HEX='0x12053010'" in build
    assert 'BSEED_PM_ROUTER_CANDIDATE_OUTPUT' in build
