"""Offline-only tests; never touch the user's Zigbee network."""
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'helper_scripts'))
from bseed_z2m_metadata_refresh import metadata_status,read_node_with_retries
IEEE='0xa4c138241e3de538'
REC={'ieee_address':IEEE,'friendly_name':'KitchenSocketLeft','network_address':23212,
     'type':'Router','interview_completed':True,'software_build_id':'1.2.5-bseedcli6'}
NODE={'role':'EndDevice','logical_type':2,'rx_on_when_idle':1}

def test_stale_router_metadata_requires_only_targeted_interview():
    device,correct=metadata_status([REC],IEEE,'EndDevice','1.2.5-bseedcli6',NODE)
    assert device['type']=='Router' and not correct
    assert metadata_status([{**REC,'type':'EndDevice'}],IEEE,'EndDevice','1.2.5-bseedcli6',NODE)[1]

def test_wrong_live_role_or_build_blocks_refresh():
    with pytest.raises(ValueError,match='Live ZDO'):metadata_status([REC],IEEE,'EndDevice','1.2.5-bseedcli6',{'role':'Router'})
    with pytest.raises(ValueError,match='custom build'):metadata_status([REC],IEEE,'EndDevice','other',NODE)
    with pytest.raises(ValueError,match='unique'):metadata_status([REC,REC],IEEE,'EndDevice','1.2.5-bseedcli6',NODE)

def test_bounded_live_zdo_retry():
    with patch('bseed_z2m_metadata_refresh.read_node_descriptor',side_effect=[TimeoutError(),NODE]) as read,patch('bseed_z2m_metadata_refresh.time.sleep'):
        assert read_node_with_retries('config','broker',IEEE,23212)==NODE
        assert read.call_count==2
    with pytest.raises(ValueError,match='Bounded'):read_node_with_retries('x','y',IEEE,23212,4)
