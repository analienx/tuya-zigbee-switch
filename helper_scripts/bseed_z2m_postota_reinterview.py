"""One identity-pinned same-role post-OTA re-interview; no reset, rejoin, relay set or OTA retry.

Run only after an exact-target OTA transport OK. A successful interview without a
fresh matching build is unconfirmed, NOT hardware acceptance.
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


def identity(device, ieee, name, manufacturer, model, role):
    if (device.get('ieee_address'), device.get('friendly_name'), device.get('manufacturer'),
            device.get('model_id'), device.get('type')) != (ieee, name, manufacturer, model, role):
        raise ValueError('Target IEEE/name/manufacturer/model/role mismatch')
    return {k: device.get(k) for k in ('ieee_address','friendly_name','manufacturer','model_id',
                                       'type','network_address','software_build_id','interview_state')}


def evaluate(before, after, response, expected_build):
    if not response or response.get('status') != 'ok': return 'interview_failed'
    if after is None: return 'target_disappeared'
    if after.get('network_address') != before.get('network_address'):
        return 'network_address_changed_requires_audit'
    if after.get('interview_state') != 'SUCCESSFUL': return 'interview_incomplete'
    if after.get('software_build_id') != expected_build: return 'build_mismatch_after_interview'
    return 'build_refreshed_postflash_unverified'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('device','ieee','confirm-ieee','manufacturer','model','expect-role',
                'expect-build','mqtt-config','broker','campaign-lock','output'):
        p.add_argument('--'+key,required=True)
    p.add_argument('--settle-seconds',type=int,default=10)
    p.add_argument('--timeout-seconds',type=int,default=160)
    a=p.parse_args()
    if a.ieee!=a.confirm_ieee or len(a.ieee)!=18 or not a.ieee.startswith('0x'):
        raise ValueError('Re-interview requires exact target IEEE confirmation')
    if a.expect_role not in ('Router','EndDevice') or not 0<=a.settle_seconds<=45 or not 30<=a.timeout_seconds<=180:
        raise ValueError('Invalid same-role/settling/interview bound')
    out=Path(a.output).expanduser().resolve();repo=Path(__file__).resolve().parents[1]
    if out.is_relative_to(repo) or out.exists(): raise ValueError('Require unused private evidence path')
    lock=json.loads(Path(a.campaign_lock).read_text(encoding='utf8'))
    if (lock.get('ieee'),lock.get('phase')) != (a.ieee,'ota_transfer_ok_postflash_unverified'):
        raise ValueError('Matching OTA transfer OK and unverified campaign lock required')
    config=yaml.safe_load(Path(a.mqtt_config).read_text(encoding='utf8'))['mqtt']
    base=config.get('base_topic','zigbee2mqtt');token='bseed-postota-interview-'+uuid.uuid4().hex
    values={'inventory':None,'bridge':None,'response':None,'events':[]}
    ready=threading.Event(); changed=threading.Event();sent=False
    client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id=token)
    client.username_pw_set(config.get('user',''),config.get('password',''))
    def connected(c,_u,_f,reason,_p):
        if not reason.is_failure:
            c.subscribe([(base+'/bridge/devices',1),(base+'/bridge/state',1),
                (base+'/bridge/response/device/interview',1),(base+'/bridge/event',1)])
            ready.set()
    def received(_c,_u,msg):
        try: data=json.loads(msg.payload)
        except (ValueError,UnicodeDecodeError):return
        if msg.topic==base+'/bridge/devices' and isinstance(data,list):
            values['inventory']=data;changed.set()
        elif msg.topic==base+'/bridge/state':
            values['bridge']=data.get('state') if isinstance(data,dict) else data;changed.set()
        elif msg.topic==base+'/bridge/response/device/interview' and data.get('transaction')==token:
            values['response']=data;changed.set()
        elif (msg.topic==base+'/bridge/event' and data.get('type')=='device_interview'
              and (data.get('data') or {}).get('ieee_address')==a.ieee):
            values['events'].append({'type':data.get('type'),'status':(data.get('data') or {}).get('status')});changed.set()
    client.on_connect=connected;client.on_message=received
    evidence={'at':dt.datetime.now().astimezone().isoformat(),'target':a.ieee,'token':token,
              'requested':False,'result':'unconfirmed','writes':0,'relay_commands':0}
    before=None;after=None
    client.connect(a.broker,1883,10);client.loop_start()
    try:
        if not ready.wait(10):raise TimeoutError('MQTT subscribe failed')
        deadline=time.monotonic()+12
        while time.monotonic()<deadline and (values['inventory'] is None or values['bridge'] is None):
            changed.wait(.3);changed.clear()
        if values['bridge']!='online':raise ValueError('Bridge offline')
        selection=[x for x in values['inventory'] if x.get('ieee_address')==a.ieee]
        if len(selection)!=1:raise ValueError('Target IEEE absent or duplicate')
        before=identity(selection[0],a.ieee,a.device,a.manufacturer,a.model,a.expect_role)
        evidence['before']=before
        if selection[0].get('interview_state')!='SUCCESSFUL':
            raise ValueError('Target not ready for post-OTA re-interview')
        time.sleep(a.settle_seconds)
        if values['bridge']!='online':raise ValueError('Bridge became offline')
        request={'id':a.ieee,'transaction':token}
        published=client.publish(base+'/bridge/request/device/interview',json.dumps(request),qos=1)
        published.wait_for_publish(5)
        if not published.is_published():raise TimeoutError('Interview MQTT publish not confirmed')
        evidence['requested']=True;print('TARGET_INTERVIEW_SENT',a.ieee,flush=True)
        deadline=time.monotonic()+a.timeout_seconds
        while time.monotonic()<deadline and values['response'] is None:
            changed.wait(.3);changed.clear()
        evidence['response']=values['response']
        if not values['response'] or values['response'].get('status')!='ok':
            raise RuntimeError('One target interview failed or timed out')
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            selection=[x for x in (values['inventory'] or []) if x.get('ieee_address')==a.ieee]
            if len(selection)==1 and selection[0].get('software_build_id')==a.expect_build:break
            changed.wait(.3);changed.clear()
        selection=[x for x in (values['inventory'] or []) if x.get('ieee_address')==a.ieee]
        if len(selection)!=1:raise ValueError('Target disappeared after interview')
        after=identity(selection[0],a.ieee,a.device,a.manufacturer,a.model,a.expect_role)
        evidence['after']=after
        evidence['result']=evaluate(before,after,values['response'],a.expect_build)
        print('POST_INTERVIEW',json.dumps({'build':after['software_build_id'],
              'result':evidence['result']}),flush=True)
    except (ValueError,RuntimeError,TimeoutError,OSError,KeyError) as error:
        evidence['error']=type(error).__name__+': '+str(error)
        print('POST_INTERVIEW_UNCONFIRMED',evidence['error'],flush=True)
    finally:
        client.loop_stop();client.disconnect()
        evidence['events']=values['events'][-5:]
        out.parent.mkdir(parents=True,exist_ok=True)
        with out.open('x',encoding='utf8') as handle:json.dump(evidence,handle,indent=2)
        print('PRIVATE_EVIDENCE',str(out),flush=True)
    if evidence['result']!='build_refreshed_postflash_unverified':raise SystemExit(2)


if __name__=='__main__':main()
