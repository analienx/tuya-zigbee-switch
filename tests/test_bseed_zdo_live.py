"""Offline tests: ZDO logical type overrides stale Zigbee2MQTT device type."""
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'helper_scripts'))
from bseed_zdo_live import parse_node_descriptor


def test_real_client_descriptor_parses_enddevice_and_always_on_radio():
    raw={'status':'ok','data':[0,{'logicalType':2,'nwkAddress':23212,
        'manufacturerCode':4417,'capabilities':{'rxOnWhenIdle':1}}]}
    result=parse_node_descriptor(raw,23212)
    assert result=={'role':'EndDevice','logical_type':2,'rx_on_when_idle':1,
                    'nwk_address':23212,'manufacturer_code':4417}

@pytest.mark.parametrize('response,expected_nwk',[
    ({'status':'error','error':'device unavailable'},23212),
    ({'status':'ok','data':[1,{'logicalType':2,'nwkAddress':23212}]},23212),
    ({'status':'ok','data':[0,{'logicalType':2,'nwkAddress':23212}]},12345),
    ({'status':'ok','data':[0,{'logicalType':99,'nwkAddress':23212}]},23212),
])
def test_invalid_zdo_result_is_not_role_evidence(response,expected_nwk):
    with pytest.raises(ValueError):parse_node_descriptor(response,expected_nwk)
