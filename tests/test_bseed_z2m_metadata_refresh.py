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


@pytest.mark.parametrize('initial_build,initial_complete,final_build,final_complete,role,retained,expected', [
    ('old-build', False, 'new-build', True, 'EndDevice', False, True),
    ('old-build', True, 'new-build', True, 'EndDevice', False, True),
    ('old-build', False, 'old-build', True, 'EndDevice', False, False),
    ('old-build', False, 'new-build', False, 'EndDevice', False, False),
    ('old-build', False, 'new-build', True, 'Router', False, False),
    ('old-build', False, 'new-build', True, 'EndDevice', True, False),
])
def test_main_repairs_stale_build_but_requires_fresh_final_identity(
        tmp_path, monkeypatch, initial_build, initial_complete,
        final_build, final_complete, role, retained, expected):
    import itertools
    import json
    from types import SimpleNamespace
    import bseed_z2m_metadata_refresh as module

    config = tmp_path / 'mqtt.yaml'
    config.write_text('mqtt: {base_topic: test}')
    output = tmp_path / 'evidence.json'
    requests = []
    initial = {**REC, 'software_build_id': initial_build, 'interview_completed': initial_complete}
    final = {**REC, 'software_build_id': final_build, 'interview_completed': final_complete, 'type': 'EndDevice'}

    class Client:
        def __init__(self, *args, **kwargs): pass
        def username_pw_set(self, *args): pass
        def connect(self, *args): pass
        def subscribe(self, *args): pass
        def disconnect(self): pass
        def loop_stop(self): pass
        def emit(self, topic, data, retain=False):
            self.on_message(self, None, SimpleNamespace(topic='test/' + topic,
                payload=json.dumps(data).encode(), retain=retain))
        def loop_start(self):
            self.on_connect(self, None, None, SimpleNamespace(is_failure=False), None)
            self.emit('bridge/devices', [initial], True)
            self.emit('bridge/state', {'state': 'online'}, True)
        def publish(self, topic, payload, **kwargs):
            requests.append((topic, json.loads(payload)))
            transaction = requests[-1][1]['transaction']
            self.emit('bridge/response/device/interview',
                      {'status': 'ok', 'transaction': transaction, 'data': {'id': IEEE}})
            self.emit('bridge/devices', [final], retained)
            return SimpleNamespace(wait_for_publish=lambda *args: None)

    monkeypatch.setattr(module, 'mqtt', SimpleNamespace(
        Client=Client, CallbackAPIVersion=SimpleNamespace(VERSION2=2)))
    monkeypatch.setattr(module, 'read_node_with_retries', lambda *args: {'role': role})
    monkeypatch.setattr(module.time, 'monotonic', lambda counter=itertools.count(step=20): next(counter))
    monkeypatch.setattr(sys, 'argv', ['metadata', '--device', REC['friendly_name'],
        '--ieee', IEEE, '--confirm-ieee', IEEE, '--expect-role', 'EndDevice',
        '--expect-build', 'new-build', '--mqtt-config', str(config), '--broker', 'mock',
        '--output', str(output)])
    if expected:
        module.main()
    else:
        with pytest.raises(SystemExit) as error:
            module.main()
        assert error.value.code == 2
    evidence = json.loads(output.read_text())
    assert len(requests) == (0 if role == 'Router' else 1)
    assert (evidence['error'] is None) == expected
    if expected:
        assert requests[0][1]['id'] == IEEE
        assert evidence['after']['software_build_id'] == 'new-build'
        assert evidence['interview_ok'] and evidence['fresh_inventory_observed']
