"""Bounded permit-join for a specific UNVERIFIED OTA role transition.

No firmware transfer, relay command, reset, device removal or Zigbee restart.

A role-changed socket retries joining on the firmware's own schedule: every
60-90 s after a clean failure, or only after its orphaned-steering rescue
reboot (minutes). One short window can miss every attempt, so the window is
re-opened the same way, `--seconds` at a time, until the target rejoins or
`--total-seconds` elapse. Any device not already in the inventory
joining meanwhile aborts the campaign and closes joining at once.

A fresh inventory or a join/announce event for the target is only a
candidate: it is checked at once with a live node descriptor, and a failed
check keeps the campaign going instead of ending it. Zigbee2MQTT republishes
the inventory whenever any device is interviewed, so ending on the first
republish closed windows early (WindowLeft, 2026-11-18, three times). After a
rejected inventory candidate, further inventory republishes are ignored until
the next window; the target's own join/announce events always count.

`--network-wide` opens joining on the coordinator and every Router for at
most MAX_NETWORK_WIDE_SECONDS in total, with the same foreign-join abort. It
is only for a role migration that needs a fresh association: `--from-role`
must name the pre-OTA role and differ from `--expect-role`; the cached
inventory type must be one of the two (Zigbee2MQTT may already have
re-interviewed the target in its new role). It is the campaign default for role changes (profile
`join_mode`); `--permit-via` is the explicit scoped opt-in.

One monotonic deadline bounds the campaign: it is fixed before the first
permit-join request, no window is opened past it, the last window is
shortened to what remains, and a remainder below MIN_WINDOW_SECONDS is not
opened at all. Closing never waits for device verification: a foreign join is
closed from the MQTT callback and the deadline by a timer, both independent of
a blocking live check. A target announce that arrives just after closing is
still checked. WindowLeft (stock -> cli26) never announced after its OTA; 70
min of scoped windows and a power cycle did nothing, and after an owner
network reset (10 presses) it joined 16 s into one network-wide window.
CabinetL (2026-11-18) likewise stayed silent through 15 min of scoped windows.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import threading
import time
import uuid
import paho.mqtt.client as mqtt
import yaml
from bseed_join_policy import (DEFAULT_WINDOW_SECONDS, MAX_NETWORK_WIDE_SECONDS,
                               MAX_SCOPED_TOTAL_SECONDS, MAX_WINDOW_SECONDS,
                               MIN_WINDOW_SECONDS, OPEN_SETTLE_SECONDS)
from bseed_zdo_live import read_node_descriptor

ROOT = Path(__file__).resolve().parents[1]
LATE_ANNOUNCE_GRACE_SECONDS = 5
JOIN_EVENTS = ('device_joined', 'device_interview', 'device_announce')


def window_budget(seconds, total_seconds, network_wide=False):
    """Validated per-window and whole-campaign join durations."""
    total = seconds if total_seconds is None else total_seconds
    limit = MAX_NETWORK_WIDE_SECONDS if network_wide else MAX_SCOPED_TOTAL_SECONDS
    if not MIN_WINDOW_SECONDS <= seconds <= MAX_WINDOW_SECONDS:
        raise ValueError('Join window must be %d..%d seconds' % (MIN_WINDOW_SECONDS, MAX_WINDOW_SECONDS))
    if not seconds <= total <= limit:
        raise ValueError('Total join time must be %d..%d seconds' % (seconds, limit))
    return seconds, total


def foreign_joiner(event, known_ieees, target_ieee):
    """IEEE of a device new to the network joining during our window, else None."""
    if not isinstance(event, dict) or event.get('type') not in JOIN_EVENTS:
        return None
    ieee = (event.get('data') or {}).get('ieee_address')
    if not ieee or ieee == target_ieee or ieee in known_ieees:
        return None
    return ieee


def target_join_event(event, target_ieee):
    """True for a join/announce/interview event of the target itself."""
    return (isinstance(event, dict) and event.get('type') in JOIN_EVENTS and
            (event.get('data') or {}).get('ieee_address') == target_ieee)


def window_seconds_left(window_seconds, deadline, now):
    """Length of the next window: never past the deadline; 0 means stop.

    A remainder shorter than MIN_WINDOW_SECONDS is not opened: it cannot
    catch a 60-90 s retry and its acknowledgement wait would outlast it."""
    remaining = int(deadline - now)
    if remaining < MIN_WINDOW_SECONDS:
        return 0
    return min(window_seconds, remaining)


def start_deadline_timer(seconds, callback):
    """Closes joining at the deadline even while the main loop is blocked."""
    timer = threading.Timer(max(0.0, seconds), callback)
    timer.daemon = True
    timer.start()
    return timer


def permit_payload(seconds, router_name, transaction):
    """Scoped request through one Router, or network-wide when router_name is None."""
    payload = {'time': seconds, 'transaction': transaction}
    if router_name is not None:
        payload['device'] = router_name
    return payload



def validated_stock_router_rejoin(profile, lock, target, ieee, from_role, expect_role):
    """Exact completed stock->custom Router canary: same Zigbee role, new NVM association.

    This is a rejoin exception ONLY. It authorizes no firmware retry, reset,
    device deletion, relay change or hardware acceptance.
    """
    if not isinstance(profile, dict) or not isinstance(lock, dict):
        return False
    response = lock.get('response') or {}
    data = response.get('data') or {}
    return (
        from_role == expect_role == 'Router' and
        profile.get('stock_router_conversion') is True and
        profile.get('preflash_role') == profile.get('postflash_role') == 'Router' and
        profile.get('ieee') == lock.get('ieee') == target.get('ieee_address') == ieee and
        profile.get('device') == target.get('friendly_name') == lock.get('device') and
        target.get('type') == 'Router' and
        target.get('manufacturer') == profile.get('manufacturer') == '_TZ3000_b28wrpvx' and
        target.get('model_id') == profile.get('model') == 'TS011F' and
        profile.get('postflash_manufacturer') == 'b28wrpvx' and
        profile.get('postflash_model') == 'TS011F-BS-PM' and
        str(profile.get('postflash_build') or '').startswith('1.2.5-bseedr') and
        profile.get('image_type') == 54179 and
        str(profile.get('file_version', '')).lower() == '0xffffffff' and
        profile.get('sha256') == lock.get('sha256') and
        profile.get('relay_state_policy') == 'preserve' and
        profile.get('auto_relay_off') is False and
        profile.get('expect_relay') == (lock.get('preflash_state') or {}).get('state') == 'OFF' and
        lock.get('phase') == 'ota_transfer_ok_postflash_unverified' and
        response.get('status') == 'ok' and
        response.get('transaction') == lock.get('token') and
        data.get('id') == ieee and
        (data.get('from') or {}).get('file_version') == 77 and
        (data.get('to') or {}).get('file_version') == 0xffffffff
    )


def validate_recovery(target_ieee, confirm_ieee, router_name, inventory, permit_join, lock,
                      network_wide=False, from_role=None, expect_role=None, stock_router_profile=None):
    if confirm_ieee != target_ieee: raise ValueError('Explicit target IEEE confirmation required')
    if permit_join is not False: raise ValueError('Joining already open; do not override another campaign')
    if lock.get('ieee') != target_ieee or lock.get('phase') not in ('ota_transfer_ok_postflash_unverified','update_ok'):
        raise ValueError('Target has no matching unfinished postflash campaign')
    target = [x for x in inventory if x.get('ieee_address') == target_ieee]
    if network_wide:
        if router_name is not None: raise ValueError('Network-wide join takes no permit-via Router')
        if len(target) != 1: raise ValueError('Exact target not found')
        # Only a role migration needs a fresh association; same-role updates
        # rejoin with their retained network key and never open the network.
        role_change = from_role in ('Router', 'EndDevice') and from_role != expect_role
        stock_router = validated_stock_router_rejoin(
            stock_router_profile, lock, target[0], target_ieee, from_role, expect_role)
        if not role_change and not stock_router:
            raise ValueError('Network-wide join is only for a role migration or exact completed stock Router canary')
        if target[0].get('type') not in (from_role, expect_role):
            raise ValueError('Cached inventory type is neither --from-role nor --expect-role')
        return target[0], None
    if router_name is None: raise ValueError('Scoped join requires a permit-via Router')
    router = [x for x in inventory if x.get('friendly_name') == router_name]
    if len(target)!=1 or len(router)!=1 or target[0].get('ieee_address')==router[0].get('ieee_address'):
        raise ValueError('Exact target or distinct permit-via router not found')
    if router[0].get('type') != 'Router' or router[0].get('interview_completed') is not True:
        raise ValueError('Scoped permit-via device is not a verified Router')
    return target[0], router[0]


def arguments():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('target','ieee','confirm-ieee','mqtt-config','broker','campaign-lock','output'):
        p.add_argument('--'+name,required=True)
    via=p.add_mutually_exclusive_group(required=True)
    via.add_argument('--permit-via',help='Router that alone permits joining (scoped)')
    via.add_argument('--network-wide',action='store_true',
                     help='Role migrations: coordinator and every Router, at most %d s in total'
                          % MAX_NETWORK_WIDE_SECONDS)
    p.add_argument('--seconds',type=int,default=DEFAULT_WINDOW_SECONDS)
    p.add_argument('--total-seconds',type=int,default=None,
                   help='re-open the window until this many seconds have passed')
    p.add_argument('--expect-build',required=True)
    p.add_argument('--expect-role',required=True,choices=['Router','EndDevice'])
    p.add_argument('--from-role',choices=['Router','EndDevice'],
                   help='Pre-OTA role; for stock Router rejoin use Router with --stock-router-profile')
    p.add_argument('--stock-router-profile',
                   help='Exact workdir/profile.json for a transport-complete stock Router->custom Router canary')
    return p.parse_args()


def main():
    a=arguments()
    network_wide=bool(getattr(a,'network_wide',False))
    permit_via=None if network_wide else a.permit_via
    window_seconds,total_seconds=window_budget(a.seconds,getattr(a,'total_seconds',None),network_wide)
    output=Path(a.output).expanduser().resolve()
    if output.is_relative_to(ROOT) or output.exists(): raise ValueError('Evidence must be new and outside git')
    lock_path=Path(a.campaign_lock).resolve()
    lock=json.loads(lock_path.read_text(encoding='utf8'))
    stock_profile=None
    if getattr(a, 'stock_router_profile', None):
        profile_path=Path(a.stock_router_profile).resolve()
        if not network_wide or profile_path != lock_path.with_name('profile.json'):
            raise ValueError('Stock Router rejoin requires exact profile beside campaign lock and --network-wide')
        stock_profile=json.loads(profile_path.read_text(encoding='utf8'))
    config=yaml.safe_load(Path(a.mqtt_config).read_text(encoding='utf8'))['mqtt']
    base=config.get('base_topic','zigbee2mqtt');token='bseed-rejoin-'+uuid.uuid4().hex
    state={'info':None,'inventory':None,'response':{},'events':[],'target_state':None,'candidate':None,
           'window_open':False,'fresh_inventory':False,'known':None,'foreign':None,'ignore_inventory':False,
           'opened':False,'closed':None,'close_tokens':[],'info_seq':0,
           'late_open_closes':set(),'open_sent':{}}
    ready=threading.Event();wake=threading.Event()
    gate=threading.RLock()  # serializes opening and closing; never held while verifying
    permit_topic=base+'/bridge/request/permit_join'
    def publish_close(suffix):
        with gate:
            close_token=token+suffix+'-'+str(len(state['close_tokens']))
            state['close_tokens'].append(close_token)
            c.publish(permit_topic,json.dumps({'time':0,'transaction':close_token}),qos=1)
    def close_now(reason):
        # Close joining once, immediately, from any thread. Never waits.
        with gate:
            if state['closed'] is not None:return
            state['closed']=reason;state['window_open']=False
            if not state['opened']:return
            publish_close('-close')
            print('JOIN_CLOSE_SENT',reason,flush=True)
    c=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id=token)
    c.username_pw_set(config.get('user',''),config.get('password',''))
    def on_connect(c,_u,_flags,reason,_properties):
        if reason.is_failure:return
        c.subscribe([(base+'/bridge/info',1),(base+'/bridge/devices',1),
                     (base+'/bridge/response/permit_join',1),(base+'/bridge/logging',0),
                     (base+'/bridge/event',1),(base+'/'+a.target,1)]);ready.set()
    def on_message(_c,_u,m):
        try:data=json.loads(m.payload)
        except (UnicodeDecodeError,ValueError):return
        if m.topic==base+'/bridge/info' and isinstance(data,dict):
            state['info']=data
            if not m.retain:state['info_seq']+=1;wake.set()
        elif m.topic==base+'/bridge/devices' and isinstance(data,list):
            state['inventory']=data
            target=next((x for x in data if x.get('ieee_address')==a.ieee),None)
            if state['window_open'] and not m.retain:state['fresh_inventory']=True
            if (state['window_open'] and not m.retain and not state['ignore_inventory'] and
                    target and target.get('friendly_name')==a.target and state['candidate'] is None):
                # Only a candidate for a live check. Cached Basic metadata
                # may still describe the pre-OTA firmware.
                state['candidate']='fresh_inventory';wake.set()
        elif m.topic==base+'/bridge/response/permit_join' and isinstance(data,dict):
            transaction=data.get('transaction')
            state['response'][transaction]=data;wake.set()
            # Bridge requests are handled asynchronously: an open answered
            # after our close may have taken effect after it. Close again.
            if (str(transaction).startswith(token+'-open-') and state['closed'] is not None and
                    transaction not in state['late_open_closes']):
                state['late_open_closes'].add(transaction)
                publish_close('-close-after-open')
        elif m.topic==base+'/bridge/event' and state['known'] is not None and not m.retain:
            joiner=foreign_joiner(data,state['known'],a.ieee)
            if joiner and state['foreign'] is None:
                state['foreign']=joiner;print('FOREIGN_JOIN_ABORT',joiner,flush=True)
                # This tool never removes devices; the operator must.
                print('FOREIGN_DEVICE_MUST_BE_REMOVED',joiner,
                      'via Zigbee2MQTT bridge/request/device/remove {"id": "%s"}'%joiner,flush=True)
                close_now('foreign_join');wake.set()
            elif target_join_event(data,a.ieee) and state['opened']:
                # Also just after closing: a join accepted in the last moment
                # of a window announces a little later.
                print('TARGET_JOIN_EVENT',data.get('type'),flush=True)
                state['candidate']='target_event';wake.set()
        elif m.topic==base+'/'+a.target and isinstance(data,dict) and not m.retain:
            state['target_state']={k:data.get(k) for k in ('device','state','state_relay','power','voltage','linkquality')}
            wake.set()
        elif m.topic==base+'/bridge/logging' and isinstance(data,dict):
            text=str(data.get('message',''))
            if a.ieee in text or a.target in text:
                event={'when':dt.datetime.now().astimezone().isoformat(),'message':text[:450]}
                if len(state['events'])<120:state['events'].append(event)
                print('TARGET_EVENT',text[:350],flush=True)
    def live_check():
        """Live node descriptor of the target's current inventory record, or (None, error)."""
        record=next((x for x in (state['inventory'] or []) if x.get('ieee_address')==a.ieee),{})
        if record.get('network_address') is None:return None,'no network address'
        try:return read_node_descriptor(a.mqtt_config,a.broker,a.ieee,record['network_address']),None
        except (ValueError,TimeoutError,OSError) as error:return None,repr(error)
    c.on_connect=on_connect;c.on_message=on_message
    c.connect(a.broker,1883,10);c.loop_start()
    open_response=None; close_response=None; outcome='unconfirmed'; windows=[]
    live_node=None; zdo_error=None; rejected=[]; timer=None; failure=None; closed_verified=False
    try:
        if not ready.wait(10):raise RuntimeError('MQTT subscription not ready')
        deadline=time.monotonic()+12
        while time.monotonic()<deadline and (state['info'] is None or state['inventory'] is None):
            wake.wait(0.4);wake.clear()
        if not isinstance(state['info'],dict) or not isinstance(state['inventory'],list):
            raise RuntimeError('Missing bridge info or inventory')
        target,router=validate_recovery(a.ieee,a.confirm_ieee,permit_via,state['inventory'],
                                         state['info'].get('permit_join'),lock,network_wide,
                                         getattr(a,'from_role',None),a.expect_role,stock_profile)
        if target.get('friendly_name')!=a.target:raise RuntimeError('Target name/IEEE mismatch')
        print('RECOVERY_GATE',a.target,a.ieee,'network-wide' if network_wide else
              'via router '+router['friendly_name'],flush=True)
        state['known']={x.get('ieee_address') for x in state['inventory']}
        campaign_stop=None
        verified=False
        while not verified and state['foreign'] is None and state['closed'] is None:
            with gate:
                # The callback sets foreign before it can take the gate.
                if state['closed'] is not None or state['foreign'] is not None:break
                sent_at=time.monotonic()
                if campaign_stop is None:
                    # The single deadline is fixed as the first request is
                    # sent, and its timer armed before that request.
                    campaign_stop=sent_at+total_seconds
                    timer=start_deadline_timer(total_seconds,lambda:close_now('deadline'))
                seconds=window_seconds_left(window_seconds,campaign_stop,sent_at)
                if seconds<=0:break
                open_token=token+'-open-'+str(len(windows))
                state['opened']=True;state['open_sent'][open_token]=sent_at
                c.publish(permit_topic,json.dumps(permit_payload(seconds,permit_via,open_token)),qos=1)
            deadline=time.monotonic()+15
            while (time.monotonic()<deadline and open_token not in state['response'] and
                   state['closed'] is None):
                wake.wait(0.3);wake.clear()
            response=state['response'].get(open_token)
            windows.append({'opened_at':dt.datetime.now().astimezone().isoformat(),
                            'seconds':seconds,'response':response})
            if open_response is None:open_response=response
            if state['closed'] is not None:break
            if not response or response.get('status')!='ok':
                failure='permit_join_unconfirmed: '+repr(response)
                print('PERMIT_JOIN_UNCONFIRMED',repr(response),flush=True)
                break
            with gate:
                if state['closed'] is not None:break
                state['window_open']=True;state['ignore_inventory']=False
            print('JOIN_WINDOW_OPEN',seconds,'network-wide' if network_wide else permit_via,
                  'window',len(windows),flush=True)
            # Measured from before the request, never from its acknowledgement.
            stop=min(sent_at+seconds,campaign_stop)
            while (time.monotonic()<stop and not verified and state['foreign'] is None and
                   state['closed'] is None):
                if state['candidate'] is not None:
                    reason=state['candidate']
                    live_node,zdo_error=live_check()
                    if live_node and live_node.get('role')==a.expect_role:
                        verified=True;break
                    rejected.append({'reason':reason,'zdo_error':zdo_error,
                                     'role':(live_node or {}).get('role')})
                    print('CANDIDATE_REJECTED',reason,zdo_error or (live_node or {}).get('role'),flush=True)
                    if reason=='fresh_inventory':state['ignore_inventory']=True
                    if state['candidate']==reason:state['candidate']=None  # keep a newer one
                    continue
                wake.wait(min(2,max(0.05,stop-time.monotonic())));wake.clear()
        # Joining closes before any further verification.
        close_now('verified' if verified else ('permit_join_unconfirmed' if failure else 'campaign_end'))
        if not verified and failure is None and state['foreign'] is None:
            # Brief grace for an announce of a join accepted at the very end.
            grace=time.monotonic()+LATE_ANNOUNCE_GRACE_SECONDS
            while (state['candidate'] is None and state['foreign'] is None and
                   time.monotonic()<grace):
                wake.wait(0.5);wake.clear()
        if not verified and state['candidate'] is not None and state['foreign'] is None:
            # A candidate that arrived as the total ran out still gets its live check.
            live_node,zdo_error=live_check()
            verified=bool(live_node and live_node.get('role')==a.expect_role)
        outcome='rejoin_candidate' if verified else 'unconfirmed'
    finally:
        if timer is not None:timer.cancel()
        try:
            close_now('exit')
            if state['close_tokens']:
                def close_ok():
                    return next((state['response'][t] for t in state['close_tokens']
                                 if (state['response'].get(t) or {}).get('status')=='ok'),None)
                def bridge_closed():
                    # Every open settled (answered, a late answer re-closes,
                    # or old enough that the bridge has handled it) and the
                    # bridge's latest state says joining is off. Zigbee2MQTT
                    # republishes bridge/info on every change, including a
                    # window expiring, so a no-op close needs no new info.
                    now=time.monotonic()
                    settled=all(t in state['response'] or now-sent>=OPEN_SETTLE_SECONDS
                                for t,sent in state['open_sent'].items())
                    return settled and state['info'].get('permit_join') is False
                for attempt in range(2):
                    deadline=time.monotonic()+12
                    while time.monotonic()<deadline and not (close_ok() and bridge_closed()):
                        wake.wait(0.3);wake.clear()
                    close_response=close_ok()
                    closed_verified=bool(close_response and bridge_closed())
                    if closed_verified or attempt:break
                    publish_close('-close-retry')  # missing, error or still open: once more
                print('JOIN_WINDOW_CLOSE',state['closed'],close_response,
                      'bridge permit_join off' if closed_verified else 'NOT VERIFIED CLOSED',flush=True)
        except Exception as error:print('CRITICAL_JOIN_CLOSE_FAILED',repr(error),flush=True)
        c.loop_stop();c.disconnect()
    target_now=next((x for x in (state['inventory'] or []) if x.get('ieee_address')==a.ieee),{})
    if state['foreign'] is not None:outcome='foreign_join_aborted'
    result={'observed_at':dt.datetime.now().astimezone().isoformat(),
            'target':a.target,'ieee':a.ieee,'join_via':'network-wide' if network_wide else permit_via,
            'network_wide':network_wide,'from_role':getattr(a,'from_role',None),
            'window_seconds':window_seconds,'closed_by':state['closed'],'failure':failure,
            'total_seconds':total_seconds,'windows':windows,'foreign_join':state['foreign'],
            'open_response':open_response,'close_response':close_response,
            'closed_verified':closed_verified,'close_requests':state['close_tokens'],
            'identity_verified':False,'expected_build':a.expect_build,
            'live_node_descriptor':live_node,'zdo_error':zdo_error,'rejected_candidates':rejected,
            'fresh_inventory_during_window':state['fresh_inventory'],
            'result':outcome if closed_verified else 'unconfirmed',
            'inventory':{k:target_now.get(k) for k in ('friendly_name','ieee_address','type',
                'manufacturer','model_id','software_build_id','interview_completed')},
            'state':state['target_state'],'target_events':state['events']}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2,default=str),encoding='utf8')
    print('PRIVATE_JOIN_EVIDENCE',output,flush=True)
    print('RESULT',result['result'],json.dumps(result['inventory']),flush=True)
    if result['result']!='rejoin_candidate':raise SystemExit(2)
    print('Role/build/physical relay/PM/parent/bindings require independent acceptance.',flush=True)


if __name__=='__main__':
    main()
