"""Bounded, identity-pinned PM ZCL read diagnostics; never changes a device.

An MQTT message after a request does NOT independently prove a ZCL read reply.
Store evidence outside the public repository. Never use this for fixture acceptance.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import time
import uuid
import yaml
from bseed_pm_provision import Bridge, ROOT, select_target

READ_ALLOWLIST = {
    'haElectricalMeasurement': frozenset({'activePower','rmsCurrent','rmsVoltage',
        'acVoltageMultiplier','acVoltageDivisor','acCurrentMultiplier','acCurrentDivisor'}),
    'seMetering': frozenset({'currentSummDelivered','multiplier','divisor'}),
}


def validated_read(cluster, attribute):
    if cluster not in READ_ALLOWLIST or attribute not in READ_ALLOWLIST[cluster]:
        raise ValueError('Only predefined, non-mutating PM attribute reads are allowed')
    return {'read': {'cluster': cluster, 'attributes': [attribute]}}


def classify(errors, states):
    if any('UNSUPPORTED_ATTRIBUTE' in e for e in errors):return 'unsupported_attribute'
    if errors:return 'zcl_error'
    if states:return 'mqtt_observed_after_read_response_unproven'
    return 'no_read_response_proven'


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('device','ieee','expect-role','expect-build','mqtt-config','broker',
                'cluster','attribute','output'):p.add_argument('--'+key,required=True)
    p.add_argument('--seconds',type=int,default=14)
    a=p.parse_args(argv)
    if a.expect_role not in ('Router','EndDevice') or not 12<=a.seconds<=30:
        raise ValueError('Explicit role and bounded 12..30 second observation required')
    if len(a.ieee)!=18 or not a.ieee.startswith('0x'):
        raise ValueError('Exact IEEE required')
    payload=validated_read(a.cluster,a.attribute)
    output=Path(a.output).expanduser().resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise ValueError('Private unused output outside repository required')
    config=yaml.safe_load(Path(a.mqtt_config).read_text(encoding='utf8'))['mqtt']
    bridge=Bridge(config,a.broker,a.device)
    evidence={'at':dt.datetime.now().astimezone().isoformat(), 'device':a.device,
              'ieee':a.ieee,'expected_role':a.expect_role,'expected_build':a.expect_build,
              'cluster':a.cluster,'attribute':a.attribute,'writes':0,
              'physical_acceptance':False,'zcl_read_reply_independently_proven':False,
              'result':'unconfirmed','issues':[]}
    started=False
    try:
        bridge.start();started=True
        if bridge.state!='online':raise ValueError('Z2M offline')
        device=select_target(bridge.inventory,a.ieee,a.device,a.expect_role,a.expect_build)
        if device.get('model_id')!='TS011F-BS-PM':
            raise ValueError('Unexpected device model')
        bridge.states.clear();bridge.errors.clear()
        msg=bridge.client.publish(bridge.base+'/'+a.device+'/set',
                                  json.dumps(payload),qos=1)
        msg.wait_for_publish(5)
        if not msg.is_published():raise TimeoutError('Read request not published')
        end=time.monotonic()+a.seconds
        while time.monotonic()<end:
            if bridge.errors:break
            time.sleep(min(.2,max(0,end-time.monotonic())))
        evidence['result']=classify(bridge.errors,bridge.states)
        evidence['mqtt_samples_after_request']=len(bridge.states)
        evidence['target_errors']=bridge.errors[-5:]
    except Exception as error:
        evidence['issues'].append(type(error).__name__+': '+str(error)[:220])
    finally:
        if started:bridge.stop()
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(evidence,indent=2),encoding='utf8')
        print('PRIVATE_EVIDENCE',output,'RESULT',evidence['result'],
              'ISSUES',evidence['issues'],'ERRORS',evidence.get('target_errors'),
              'MQTT_MESSAGES',evidence.get('mqtt_samples_after_request'),flush=True)
    if evidence['result']!='mqtt_observed_after_read_response_unproven':
        raise SystemExit(2)


if __name__=='__main__':main()
