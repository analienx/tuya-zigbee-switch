"""Offline validation of bounded, router-scoped postflash rejoin; never opens join."""
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'helper_scripts'))
from bseed_z2m_rejoin_window import validate_recovery

IEEE='0xa4c138241e3de538'
INVENTORY=[{'friendly_name':'KitchenSocketLeft','ieee_address':IEEE,'type':'Router','interview_completed':False},
           {'friendly_name':'KitchenSocketRight','ieee_address':'0xa4c138075cd16ed4','type':'Router','interview_completed':True}]
LOCK={'ieee':IEEE,'phase':'ota_transfer_ok_postflash_unverified'}

def test_exact_stale_target_and_distinct_online_router_allowed():
    target,router=validate_recovery(IEEE,IEEE,'KitchenSocketRight',INVENTORY,False,LOCK)
    assert target['ieee_address']==IEEE and router['friendly_name']=='KitchenSocketRight'

@pytest.mark.parametrize('confirmation,join,phase,via',[
    ('0xBAD',False,'ota_transfer_ok_postflash_unverified','KitchenSocketRight'),
    (IEEE,True,'ota_transfer_ok_postflash_unverified','KitchenSocketRight'),
    (IEEE,False,'update_error','KitchenSocketRight'),
    (IEEE,False,'ota_transfer_ok_postflash_unverified','KitchenSocketLeft'),
    (IEEE,False,'ota_transfer_ok_postflash_unverified','MissingRouter')])
def test_unsafe_recovery_state_is_blocked(confirmation,join,phase,via):
    with pytest.raises(ValueError):
        validate_recovery(IEEE,confirmation,via,INVENTORY,join,{'ieee':IEEE,'phase':phase})


@pytest.mark.parametrize('retained,live_role,expected', [
    (False, 'EndDevice', True),
    (True, 'EndDevice', False),
    (False, 'Router', False),
])
def test_stale_build_allows_interview_candidate_only_after_fresh_inventory_and_live_role(
        tmp_path, monkeypatch, retained, live_role, expected):
    import itertools
    import json
    from types import SimpleNamespace
    import bseed_z2m_rejoin_window as module

    config = tmp_path / 'mqtt.yaml'
    config.write_text('mqtt: {base_topic: test}')
    lock = tmp_path / 'lock.json'
    lock.write_text(json.dumps(LOCK))
    output = tmp_path / 'evidence.json'
    target = {**INVENTORY[0], 'software_build_id': 'old-build', 'network_address': 123}
    inventory = [target, INVENTORY[1]]
    requests, pending = [], []

    class Event:
        def set(self): pass
        def clear(self): pass
        def wait(self, *args):
            while pending:
                pending.pop(0)()
            return True

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
            self.emit('bridge/devices', inventory, True)
            self.emit('bridge/info', {'permit_join': False}, True)
        def publish(self, topic, payload, **kwargs):
            data = json.loads(payload)
            requests.append(data)
            self.emit('bridge/response/permit_join', {'status': 'ok', 'transaction': data['transaction']})
            if data['time']:
                pending.append(lambda: self.emit('bridge/devices', inventory, retained))
            return SimpleNamespace(wait_for_publish=lambda *args: None)

    monkeypatch.setattr(module.mqtt, 'Client', Client)
    monkeypatch.setattr(module.threading, 'Event', Event)
    monkeypatch.setattr(module, 'read_node_descriptor', lambda *args: {'role': live_role})
    monkeypatch.setattr(module.time, 'monotonic', lambda counter=itertools.count(): next(counter))
    monkeypatch.setattr(module, 'arguments', lambda: SimpleNamespace(
        target=target['friendly_name'], ieee=IEEE, confirm_ieee=IEEE,
        permit_via=INVENTORY[1]['friendly_name'], seconds=30, expect_role='EndDevice',
        expect_build='new-build', mqtt_config=str(config), broker='mock',
        campaign_lock=str(lock), output=str(output)))
    if expected:
        module.main()
    else:
        with pytest.raises(SystemExit) as error:
            module.main()
        assert error.value.code == 2
    evidence = json.loads(output.read_text())
    assert [r['time'] for r in requests] == [30, 0]
    assert (evidence['result'] == 'rejoin_candidate') == expected
    assert evidence['identity_verified'] is False
    assert evidence['inventory']['software_build_id'] == 'old-build'
