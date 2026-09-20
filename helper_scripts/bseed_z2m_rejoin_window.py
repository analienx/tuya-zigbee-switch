"""Bounded router-scoped permit-join for a specific UNVERIFIED OTA role transition.

No firmware transfer, relay command, reset, device removal or Zigbee restart.
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
from bseed_zdo_live import read_node_descriptor

ROOT = Path(__file__).resolve().parents[1]


def validate_recovery(target_ieee, confirm_ieee, router_name, inventory, permit_join, lock):
    if confirm_ieee != target_ieee: raise ValueError('Explicit target IEEE confirmation required')
    if permit_join is not False: raise ValueError('Joining already open; do not override another campaign')
    if lock.get('ieee') != target_ieee or lock.get('phase') not in ('ota_transfer_ok_postflash_unverified','update_ok'):
        raise ValueError('Target has no matching unfinished postflash campaign')
    target = [x for x in inventory if x.get('ieee_address') == target_ieee]
    router = [x for x in inventory if x.get('friendly_name') == router_name]
    if len(target)!=1 or len(router)!=1 or target[0].get('ieee_address')==router[0].get('ieee_address'):
        raise ValueError('Exact target or distinct permit-via router not found')
    if router[0].get('type') != 'Router' or router[0].get('interview_completed') is not True:
        raise ValueError('Scoped permit-via device is not a verified Router')
    return target[0], router[0]


def arguments():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('target','ieee','confirm-ieee','permit-via','mqtt-config','broker','campaign-lock','output'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--seconds',type=int,default=120)
    p.add_argument('--expect-build',required=True)
    p.add_argument('--expect-role',required=True,choices=['Router','EndDevice'])
    return p.parse_args()


def main():
    a=arguments()
    if not 30<=a.seconds<=180: raise ValueError('Scoped join window must be 30..180 seconds')
    output=Path(a.output).expanduser().resolve()
    if output.is_relative_to(ROOT) or output.exists(): raise ValueError('Evidence must be new and outside git')
    lock=json.loads(Path(a.campaign_lock).read_text(encoding='utf8'))
    config=yaml.safe_load(Path(a.mqtt_config).read_text(encoding='utf8'))['mqtt']
    base=config.get('base_topic','zigbee2mqtt');token='bseed-rejoin-'+uuid.uuid4().hex
    state={'info':None,'inventory':None,'response':{},'events':[],'target_state':None,'candidate':False}
    ready=threading.Event();wake=threading.Event()
    c=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id=token)
    c.username_pw_set(config.get('user',''),config.get('password',''))
    def on_connect(c,_u,_flags,reason,_properties):
        if reason.is_failure:return
        c.subscribe([(base+'/bridge/info',1),(base+'/bridge/devices',1),
                     (base+'/bridge/response/permit_join',1),(base+'/bridge/logging',0),
                     (base+'/'+a.target,1)]);ready.set()
    def on_message(_c,_u,m):
        try:data=json.loads(m.payload)
        except (UnicodeDecodeError,ValueError):return
        if m.topic==base+'/bridge/info' and isinstance(data,dict):state['info']=data
        elif m.topic==base+'/bridge/devices' and isinstance(data,list):
            state['inventory']=data
            target=next((x for x in data if x.get('ieee_address')==a.ieee),None)
            if target and target.get('interview_completed') is True and target.get('software_build_id')==a.expect_build:
                state['candidate']=True;wake.set()
        elif m.topic==base+'/bridge/response/permit_join' and isinstance(data,dict):
            state['response'][data.get('transaction')]=data;wake.set()
        elif m.topic==base+'/'+a.target and isinstance(data,dict) and not m.retain:
            state['target_state']={k:data.get(k) for k in ('device','state','state_relay','power','voltage','linkquality')}
            wake.set()
        elif m.topic==base+'/bridge/logging' and isinstance(data,dict):
            text=str(data.get('message',''))
            if a.ieee in text or a.target in text:
                event={'when':dt.datetime.now().astimezone().isoformat(),'message':text[:450]}
                if len(state['events'])<120:state['events'].append(event)
                print('TARGET_EVENT',text[:350],flush=True)
    c.on_connect=on_connect;c.on_message=on_message
    c.connect(a.broker,1883,10);c.loop_start()
    opened=False; open_response=None; close_response=None; outcome='unconfirmed'
    try:
        if not ready.wait(10):raise RuntimeError('MQTT subscription not ready')
        deadline=time.monotonic()+12
        while time.monotonic()<deadline and (state['info'] is None or state['inventory'] is None):
            wake.wait(0.4);wake.clear()
        if not isinstance(state['info'],dict) or not isinstance(state['inventory'],list):
            raise RuntimeError('Missing bridge info or inventory')
        target,router=validate_recovery(a.ieee,a.confirm_ieee,a.permit_via,state['inventory'],
                                         state['info'].get('permit_join'),lock)
        if target.get('friendly_name')!=a.target:raise RuntimeError('Target name/IEEE mismatch')
        print('RECOVERY_GATE',a.target,a.ieee,'via router',router['friendly_name'],flush=True)
        open_token=token+'-open';topic=base+'/bridge/request/permit_join'
        c.publish(topic,json.dumps({'time':a.seconds,'device':a.permit_via,'transaction':open_token}),qos=1).wait_for_publish(5)
        opened=True
        deadline=time.monotonic()+15
        while time.monotonic()<deadline and open_token not in state['response']:
            wake.wait(0.3);wake.clear()
        open_response=state['response'].get(open_token)
        if not open_response or open_response.get('status')!='ok':
            raise RuntimeError('Scoped permit-join not confirmed: '+repr(open_response))
        print('JOIN_WINDOW_OPEN',a.seconds,a.permit_via,flush=True)
        stop=time.monotonic()+a.seconds
        while time.monotonic()<stop and not state['candidate']:
            wake.wait(min(2,max(0.05,stop-time.monotonic())));wake.clear()
        outcome='postflash_candidate' if state['candidate'] else 'unconfirmed'
    finally:
        if opened:
            close_token=token+'-close';topic=base+'/bridge/request/permit_join'
            try:
                c.publish(topic,json.dumps({'time':0,'transaction':close_token}),qos=1).wait_for_publish(5)
                deadline=time.monotonic()+12
                while time.monotonic()<deadline and close_token not in state['response']:
                    wake.wait(0.3);wake.clear()
                close_response=state['response'].get(close_token)
                print('JOIN_WINDOW_CLOSE',close_response,flush=True)
            except Exception as error:print('CRITICAL_JOIN_CLOSE_FAILED',repr(error),flush=True)
        c.loop_stop();c.disconnect()
    target_now=next((x for x in (state['inventory'] or []) if x.get('ieee_address')==a.ieee),{})
    live_node=None;zdo_error=None
    if target_now.get('network_address') is not None:
        try:live_node=read_node_descriptor(a.mqtt_config,a.broker,a.ieee,target_now['network_address'])
        except (ValueError,TimeoutError,OSError) as error:zdo_error=repr(error)
    outcome='postflash_candidate' if (state['candidate'] and live_node and
            live_node.get('role')==a.expect_role) else 'unconfirmed'
    result={'observed_at':dt.datetime.now().astimezone().isoformat(),
            'target':a.target,'ieee':a.ieee,'join_via':a.permit_via,'window_seconds':a.seconds,
            'open_response':open_response,'close_response':close_response,
            'live_node_descriptor':live_node,'zdo_error':zdo_error,
            'result':outcome if close_response and close_response.get('status')=='ok' else 'unconfirmed',
            'inventory':{k:target_now.get(k) for k in ('friendly_name','ieee_address','type',
                'manufacturer','model_id','software_build_id','interview_completed')},
            'state':state['target_state'],'target_events':state['events']}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2,default=str),encoding='utf8')
    print('PRIVATE_JOIN_EVIDENCE',output,flush=True)
    print('RESULT',result['result'],json.dumps(result['inventory']),flush=True)
    if result['result']!='postflash_candidate':raise SystemExit(2)
    print('Role/build/physical relay/PM/parent/bindings require independent acceptance.',flush=True)


if __name__=='__main__':
    main()
