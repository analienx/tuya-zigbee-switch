"""Refresh stale Zigbee2MQTT role metadata after a cross-role OTA, without removing a device."""
import argparse, datetime as dt, json, threading, time, uuid
from pathlib import Path
import paho.mqtt.client as mqtt
import yaml
from bseed_zdo_live import read_node_descriptor

def metadata_status(inventory, ieee, expected_role, expected_build, live):
    matches=[d for d in (inventory or []) if d.get('ieee_address')==ieee]
    if len(matches)!=1: raise ValueError('Target IEEE not unique in live inventory')
    device=matches[0]
    if not live or live.get('role')!=expected_role: raise ValueError('Live ZDO role does not match intended firmware')
    if device.get('software_build_id')!=expected_build or device.get('interview_completed') is not True:
        raise ValueError('Expected custom build/interview has not been verified')
    return device, device.get('type')==expected_role

def read_node_with_retries(config, broker, ieee, nwk, attempts=3):
    if not 1<=attempts<=3: raise ValueError('Bounded ZDO attempts required')
    for attempt in range(attempts):
        try: return read_node_descriptor(config,broker,ieee,nwk)
        except (TimeoutError,OSError,ValueError):
            if attempt+1>=attempts: raise
            time.sleep(3*(attempt+1))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for field in ('device','ieee','confirm-ieee','expect-role','expect-build','mqtt-config','broker','output'):p.add_argument('--'+field,required=True)
    args=p.parse_args();root=Path(__file__).resolve().parents[1]
    output=Path(args.output).expanduser().resolve()
    if output.is_relative_to(root) or output.exists():raise ValueError('Use fresh private evidence outside repository')
    if args.confirm_ieee!=args.ieee or len(args.ieee)!=18:raise ValueError('Exact IEEE confirmation required')
    config=yaml.safe_load(Path(args.mqtt_config).read_text(encoding='utf8'))['mqtt']
    base=config.get('base_topic','zigbee2mqtt');token='bseed-metadata-'+uuid.uuid4().hex
    state={'inventory':None,'bridge':None,'response':None};changed=threading.Event();ready=threading.Event()
    client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id=token)
    client.username_pw_set(config.get('user',''),config.get('password',''))
    def on_connect(c,_u,_f,reason,_p):
        if not reason.is_failure:
            c.subscribe([(base+'/bridge/devices',1),(base+'/bridge/state',1),
                         (base+'/bridge/response/device/interview',1)]);ready.set()
    def on_message(_c,_u,m):
        try:d=json.loads(m.payload)
        except (ValueError,UnicodeDecodeError):return
        if m.topic==base+'/bridge/devices' and isinstance(d,list):state['inventory']=d;changed.set()
        elif m.topic==base+'/bridge/state':state['bridge']=d.get('state') if isinstance(d,dict) else d;changed.set()
        elif m.topic==base+'/bridge/response/device/interview' and d.get('transaction')==token:
            state['response']=d;changed.set()
    client.on_connect=on_connect;client.on_message=on_message
    client.connect(args.broker,1883,10);client.loop_start()
    before=None;after=None;live=None;result='unconfirmed';reason=None
    try:
        if not ready.wait(10):raise TimeoutError('MQTT did not subscribe')
        deadline=time.monotonic()+12
        while time.monotonic()<deadline and (state['inventory'] is None or state['bridge'] is None):
            changed.wait(.3);changed.clear()
        if state['bridge']!='online':raise RuntimeError('Zigbee2MQTT bridge not online')
        selected=[d for d in state['inventory'] if d.get('ieee_address')==args.ieee]
        if len(selected)!=1 or selected[0].get('friendly_name')!=args.device:
            raise ValueError('Exact target name/IEEE mismatch')
        live=read_node_with_retries(args.mqtt_config,args.broker,args.ieee,selected[0]['network_address'])
        before,correct=metadata_status(state['inventory'],args.ieee,args.expect_role,args.expect_build,live)
        if not correct:
            client.publish(base+'/bridge/request/device/interview',
                json.dumps({'id':args.ieee,'transaction':token}),qos=1).wait_for_publish(5)
            deadline=time.monotonic()+150
            while time.monotonic()<deadline and state['response'] is None:
                changed.wait(.3);changed.clear()
            if not state['response'] or state['response'].get('status')!='ok':
                raise RuntimeError('Targeted interview failed or timed out: '+repr(state['response']))
            deadline=time.monotonic()+12
            while time.monotonic()<deadline:
                observed=state['inventory'] or []
                match=next((d for d in observed if d.get('ieee_address')==args.ieee),{})
                if match.get('type')==args.expect_role:break
                changed.wait(.3);changed.clear()
        live_after=read_node_with_retries(args.mqtt_config,args.broker,args.ieee,before['network_address'])
        after,correct=metadata_status(state['inventory'],args.ieee,args.expect_role,args.expect_build,live_after)
        if not correct:raise RuntimeError('Interview succeeded but cached role remains stale; do not remove device automatically')
        if before['ieee_address']!=after['ieee_address'] or before['network_address']!=after['network_address']:
            raise RuntimeError('IEEE or NWK address changed during metadata refresh')
        result='metadata_refreshed' if before.get('type')!=after.get('type') else 'metadata_already_correct'
    except (ValueError,RuntimeError,TimeoutError,OSError,KeyError) as error:
        reason=repr(error)
    finally:
        client.loop_stop();client.disconnect()
        evidence={'at':dt.datetime.now().astimezone().isoformat(),'ieee':args.ieee,'device':args.device,
            'result':result,'error':reason,'live_zdo_before':live,'inventory_role_before':before.get('type') if before else None,
            'inventory_role_after':after.get('type') if after else None,'interview_response':state['response'],
            'automatic_remove_or_factory_reset':False}
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(evidence,indent=2,default=str),encoding='utf8')
        print(json.dumps(evidence,indent=2,default=str),flush=True)
    if reason:raise SystemExit(2)

if __name__=='__main__':main()
