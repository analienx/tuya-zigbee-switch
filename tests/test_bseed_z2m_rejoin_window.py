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

    monkeypatch.setattr(module, 'mqtt', SimpleNamespace(
        Client=Client, CallbackAPIVersion=SimpleNamespace(VERSION2=2)))
    monkeypatch.setattr(module.threading, 'Event', Event)
    monkeypatch.setattr(module, 'read_node_descriptor', lambda *args: {'role': live_role})
    monkeypatch.setattr(module.time, 'monotonic', lambda counter=itertools.count(): next(counter))
    monkeypatch.setattr(module, 'start_deadline_timer',
                        lambda seconds, callback: SimpleNamespace(cancel=lambda: None))
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


def test_window_budget_bounds_each_window_and_the_whole_campaign():
    from bseed_z2m_rejoin_window import window_budget
    assert window_budget(120, None) == (120, 120)
    assert window_budget(180, 2100) == (180, 2100)
    for seconds, total in ((20, 600), (200, 600), (120, 60), (120, 2701)):
        with pytest.raises(ValueError):
            window_budget(seconds, total)


def test_only_a_device_new_to_the_network_counts_as_foreign_join():
    from bseed_z2m_rejoin_window import foreign_joiner
    known = {IEEE, '0xa4c138075cd16ed4'}
    joined = lambda ieee, kind='device_joined': {'type': kind, 'data': {'ieee_address': ieee}}
    assert foreign_joiner(joined('0x00000000deadbeef'), known, IEEE) == '0x00000000deadbeef'
    assert foreign_joiner(joined('0x00000000deadbeef', 'device_announce'), known, IEEE)
    assert foreign_joiner(joined(IEEE), known, IEEE) is None
    assert foreign_joiner(joined('0xa4c138075cd16ed4'), known, IEEE) is None
    assert foreign_joiner(joined('0x00000000deadbeef', 'device_leave'), known, IEEE) is None
    assert foreign_joiner('not an event', known, IEEE) is None


def _run_campaign(tmp_path, monkeypatch, *, total, on_open, live_role='EndDevice',
                  node=None, network_wide=False):
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
        opens = 0
        instance = None
        def __init__(self, *args, **kwargs): Client.instance = self
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
                Client.opens += 1
                on_open(Client.opens, lambda *event: pending.append(lambda: self.emit(*event)), inventory)
            return SimpleNamespace(wait_for_publish=lambda *args: None)

    monkeypatch.setattr(module, 'mqtt', SimpleNamespace(
        Client=Client, CallbackAPIVersion=SimpleNamespace(VERSION2=2)))
    monkeypatch.setattr(module.threading, 'Event', Event)
    clock = itertools.count()
    _run_campaign.clock = clock
    _run_campaign.requests_now = requests
    _run_campaign.client = Client
    _run_campaign.deadline = []
    monkeypatch.setattr(module, 'read_node_descriptor', node or (lambda *args: {'role': live_role}))
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(clock))
    def deadline_timer(seconds, callback):
        _run_campaign.deadline.append((seconds, callback))
        return SimpleNamespace(cancel=lambda: None)
    monkeypatch.setattr(module, 'start_deadline_timer', deadline_timer)
    monkeypatch.setattr(module, 'arguments', lambda: SimpleNamespace(
        target=target['friendly_name'], ieee=IEEE, confirm_ieee=IEEE,
        permit_via=None if network_wide else INVENTORY[1]['friendly_name'],
        network_wide=network_wide, from_role='Router' if network_wide else None,
        seconds=30, total_seconds=total,
        expect_role='EndDevice', expect_build='new-build', mqtt_config=str(config),
        broker='mock', campaign_lock=str(lock), output=str(output)))
    code = 0
    try:
        module.main()
    except SystemExit as error:
        code = error.code
    _run_campaign.requests = requests
    _run_campaign.requests_ref = requests
    return code, [r['time'] for r in requests], json.loads(output.read_text())


def test_window_reopens_until_the_target_rejoins(tmp_path, monkeypatch):
    def on_open(n, schedule, inventory):
        if n == 2:  # the socket's next attempt lands in the second window
            schedule('bridge/devices', inventory, False)
    code, times, evidence = _run_campaign(tmp_path, monkeypatch, total=200, on_open=on_open)
    assert code == 0 and times == [30, 30, 0]
    assert evidence['result'] == 'rejoin_candidate' and len(evidence['windows']) == 2
    assert evidence['total_seconds'] == 200 and evidence['foreign_join'] is None


def test_window_stops_at_total_and_closes_when_target_never_rejoins(tmp_path, monkeypatch):
    code, times, evidence = _run_campaign(tmp_path, monkeypatch, total=100,
                                          on_open=lambda *args: None)
    assert code == 2 and times[-1] == 0 and times.count(0) == 1 and len(times) >= 3
    assert all(0 < t <= 30 for t in times[:-1]) and evidence['result'] == 'unconfirmed'
    assert sum(times[:-1]) <= 100 and evidence['closed_by'] in ('campaign_end', 'deadline')


def test_unrelated_inventory_republish_does_not_end_the_campaign(tmp_path, monkeypatch):
    """WindowLeft 2026-11-18: another device's interview republished the inventory and the
    old loop closed joining after one failed live check, three times in one evening."""
    calls = []
    def node(*args):
        calls.append(args)
        if len(calls) == 1:
            raise TimeoutError('nodeDescRsp after 10000ms')
        return {'role': 'EndDevice'}
    def on_open(n, schedule, inventory):
        if n == 1:
            schedule('bridge/devices', inventory, False)   # someone else was interviewed
            schedule('bridge/devices', inventory, False)   # ignored for the rest of this window
        if n == 3:
            schedule('bridge/event', {'type': 'device_announce', 'data': {'ieee_address': IEEE}}, False)
    code, times, evidence = _run_campaign(tmp_path, monkeypatch, total=200, on_open=on_open, node=node)
    assert code == 0 and times == [30, 30, 30, 0] and len(calls) == 2
    assert evidence['result'] == 'rejoin_candidate'
    assert [r['reason'] for r in evidence['rejected_candidates']] == ['fresh_inventory']


def test_rejected_candidates_end_unconfirmed_at_the_total(tmp_path, monkeypatch):
    def on_open(n, schedule, inventory):
        schedule('bridge/event', {'type': 'device_announce', 'data': {'ieee_address': IEEE}}, False)
    code, times, evidence = _run_campaign(tmp_path, monkeypatch, total=100, on_open=on_open,
                                          live_role='Router')
    assert code == 2 and times[-1] == 0 and len(times) >= 3
    assert evidence['result'] == 'unconfirmed' and evidence['rejected_candidates']


def test_network_wide_window_has_no_router_and_a_tighter_budget(tmp_path, monkeypatch):
    from bseed_z2m_rejoin_window import window_budget, permit_payload, MAX_NETWORK_WIDE_SECONDS
    assert permit_payload(60, None, 't') == {'time': 60, 'transaction': 't'}
    assert permit_payload(60, 'R', 't')['device'] == 'R'
    assert window_budget(120, MAX_NETWORK_WIDE_SECONDS, True)[1] == 1200
    with pytest.raises(ValueError):
        window_budget(120, 1201, True)
    assert window_budget(120, 2100, False)[1] == 2100
    with pytest.raises(ValueError, match='no permit-via'):
        validate_recovery(IEEE, IEEE, 'KitchenSocketRight', INVENTORY, False, LOCK, True)
    with pytest.raises(ValueError, match='requires a permit-via'):
        validate_recovery(IEEE, IEEE, None, INVENTORY, False, LOCK)
    target, router = validate_recovery(IEEE, IEEE, None, INVENTORY, False, LOCK, True,
                                       'Router', 'EndDevice')
    assert target['ieee_address'] == IEEE and router is None
    # Network-wide is only for a role migration whose cached type is the source role.
    with pytest.raises(ValueError, match='only for a role migration'):
        validate_recovery(IEEE, IEEE, None, INVENTORY, False, LOCK, True)
    with pytest.raises(ValueError, match='only for a role migration'):
        validate_recovery(IEEE, IEEE, None, INVENTORY, False, LOCK, True, 'Router', 'Router')
    with pytest.raises(ValueError, match='Cached inventory type'):
        validate_recovery(IEEE, IEEE, None, INVENTORY, False, LOCK, True, 'EndDevice', 'Router')

    def on_open(n, schedule, inventory):
        schedule('bridge/event', {'type': 'device_announce', 'data': {'ieee_address': IEEE}}, False)
    code, times, evidence = _run_campaign(tmp_path, monkeypatch, total=300, on_open=on_open,
                                          network_wide=True)
    assert code == 0 and times == [30, 0] and evidence['network_wide'] is True
    assert all('device' not in r for r in _run_campaign.requests)


def test_network_wide_still_aborts_on_a_foreign_join(tmp_path, monkeypatch):
    def on_open(n, schedule, inventory):
        schedule('bridge/event', {'type': 'device_joined',
                                  'data': {'ieee_address': '0x00000000deadbeef'}}, False)
    code, times, evidence = _run_campaign(tmp_path, monkeypatch, total=300, on_open=on_open,
                                          network_wide=True)
    assert code == 2 and times == [30, 0] and evidence['result'] == 'foreign_join_aborted'


def test_foreign_join_aborts_and_closes_immediately(tmp_path, monkeypatch):
    def on_open(n, schedule, inventory):
        schedule('bridge/event', {'type': 'device_joined',
                                  'data': {'ieee_address': '0x00000000deadbeef'}}, False)
    code, times, evidence = _run_campaign(tmp_path, monkeypatch, total=600, on_open=on_open)
    assert code == 2 and times == [30, 0]
    assert evidence['foreign_join'] == '0x00000000deadbeef'
    assert evidence['result'] == 'foreign_join_aborted'


def test_window_lengths_never_pass_the_deadline():
    from bseed_z2m_rejoin_window import window_seconds_left
    assert window_seconds_left(30, 100, 0) == 30
    assert window_seconds_left(30, 100, 85) == 15
    assert window_seconds_left(30, 100, 99.5) == 0
    assert window_seconds_left(30, 100, 130) == 0


def test_last_window_is_shortened_to_the_deadline(tmp_path, monkeypatch):
    code, times, evidence = _run_campaign(tmp_path, monkeypatch, total=45,
                                          on_open=lambda *args: None, network_wide=True)
    opens = [w['seconds'] for w in evidence['windows']]
    assert code == 2 and times[-1] == 0 and opens[0] == 30
    assert all(0 < s <= 30 for s in opens) and sum(opens) <= 45
    (seconds, _callback), = _run_campaign.deadline
    assert 0 < seconds <= 45   # the timer covers the same single deadline


def test_deadline_closes_while_a_live_check_blocks(tmp_path, monkeypatch):
    seen = {}
    def node(*args):
        # The deadline timer fires while the descriptor read is still blocked.
        _seconds, callback = _run_campaign.deadline[0]
        callback()
        seen['closed_during_check'] = _run_campaign.requests_now[-1]['time'] == 0
        return {'role': 'Router'}
    def on_open(n, schedule, inventory):
        schedule('bridge/event', {'type': 'device_announce', 'data': {'ieee_address': IEEE}}, False)
    code, times, evidence = _run_campaign(tmp_path, monkeypatch, total=600, on_open=on_open,
                                          node=node, network_wide=True)
    assert seen['closed_during_check'] is True
    assert code == 2 and times == [30, 0] and evidence['closed_by'] == 'deadline'


def test_foreign_join_closes_while_a_live_check_blocks(tmp_path, monkeypatch):
    seen = {}
    def node(*args):
        _run_campaign.client.instance.emit('bridge/event', {
            'type': 'device_joined', 'data': {'ieee_address': '0x00000000deadbeef'}})
        seen['closed_during_check'] = _run_campaign.requests_now[-1]['time'] == 0
        return {'role': 'Router'}
    def on_open(n, schedule, inventory):
        schedule('bridge/event', {'type': 'device_announce', 'data': {'ieee_address': IEEE}}, False)
    code, times, evidence = _run_campaign(tmp_path, monkeypatch, total=600, on_open=on_open,
                                          node=node, network_wide=True)
    assert seen['closed_during_check'] is True
    assert code == 2 and times == [30, 0] and evidence['result'] == 'foreign_join_aborted'
    assert evidence['closed_by'] == 'foreign_join'
