"""Read-only PM Router/Client audit. Never interprets cached values as physical acceptance."""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import time
from bseed_pm_provision import (Bridge, CLUSTER_IDS, ROOT, SCALES, missing_reports,
                                pm_snapshot, read_db, select_target)
from bseed_zdo_live import read_node_descriptor
import yaml

# This contract is intentionally *not* shared with Client's firmware constants.
# Router's legacy scaling attributes differ and can be absent in the Z2M cache.
ROUTER_REQUIRED = {'haElectricalMeasurement': ('acCurrentMultiplier','acCurrentDivisor'),
                   'seMetering': ('multiplier','divisor')}
SETTING_CLUSTERS = {'1': ('genOnOff','genOnOffSwitchCfg','manuSpecificTuya3',
                          'manuSpecificTuya_3','57345'),
                    '2': ('genOnOff',)}
TRANSIENT_ATTRIBUTES = {'onOff','onTime','offWaitTime'}


def settings_snapshot(record):
    """Capture only known settings; never compare live relay state as configuration."""
    output={}
    for endpoint, clusters in SETTING_CLUSTERS.items():
        source=record.get('endpoints',{}).get(endpoint,{}).get('clusters',{})
        for name in clusters:
            attrs=source.get(name,{}).get('attributes',{})
            filtered={k:v for k,v in attrs.items() if k not in TRANSIENT_ATTRIBUTES}
            if filtered:output[f'{endpoint}/{name}']=filtered
    return output


def settings_drift(baseline, current):
    if not isinstance(baseline, dict):raise ValueError('Baseline must be a settings dictionary')
    return {k:{'previous':value,'current':current.get(k,'MISSING')}
            for k,value in baseline.items() if current.get(k)!=value}


def check_persisted(record, ieee, build, role, coordinator):
    if role not in ('Router','EndDevice'):raise ValueError('Unsupported Zigbee role')
    if record.get('ieeeAddr')!=ieee or record.get('swBuildId')!=build or record.get('type')!=role:
        raise ValueError('Persisted identity, role or build mismatch')
    ep=record.get('endpoints',{}).get('1',{})
    if '2' not in record.get('endpoints',{}) or not ep:raise ValueError('Missing PM or relay endpoint')
    attrs={key:ep.get('clusters',{}).get(key,{}).get('attributes',{}) for key in CLUSTER_IDS}
    required=(SCALES if role=='EndDevice' else ROUTER_REQUIRED)
    for cluster,names in required.items():
        for name in names:
            value=attrs[cluster].get(name)
            if type(value)!=int or value<=0:raise ValueError('Unverified scale '+cluster+'.'+name)
            if role=='EndDevice' and value!=SCALES[cluster][name]:
                raise ValueError('Client scale mismatch '+cluster+'.'+name)
    bindings={r.get('cluster') for r in ep.get('binds',[]) if
        r.get('type')=='endpoint' and r.get('deviceIeeeAddress')==coordinator and
        r.get('endpointID')==1}
    if not set(CLUSTER_IDS.values()).issubset(bindings):
        raise ValueError('Missing PM coordinator bindings')
    return attrs


def assess_samples(samples, attrs, role):
    """Check passive MQTT plausibility; a packet alone is NOT a raw ZCL report."""
    if len(samples)<2 or samples[-1][0]-samples[0][0]<8:
        raise ValueError('Insufficient separated non-retained MQTT samples')
    last=samples[-1][1]
    for name in ('voltage','current','power','energy'):
        value=last.get(name)
        if type(value) not in (float,int) or not math.isfinite(value) or value<0:
            raise ValueError('Invalid/absent MQTT '+name)
    if not 180<=last['voltage']<=260 or last['current']>32 or last['power']>8000:
        raise ValueError('Unscaled or implausible MQTT electrical values')
    meter=attrs['seMetering'];elec=attrs['haElectricalMeasurement']
    energy_scale=meter.get('multiplier',0)/meter.get('divisor',1)
    if energy_scale<=0:raise ValueError('Unknown energy scaling')
    expected=meter.get('currentSummDelivered')
    if type(expected)==int and abs(last['energy']-expected*energy_scale)>0.1:
        raise ValueError('Raw-to-MQTT energy discrepancy; reread device scale')
    if role=='EndDevice':
        raw_voltage=elec.get('rmsVoltage')
        if type(raw_voltage)==int and abs(last['voltage']-raw_voltage/100)>10:
            raise ValueError('Client raw voltage scaling discrepancy')
    # Router voltage multiplier/divisor are absent in legacy cached records.
    # A plausible voltage is NOT independent proof of Router raw scaling.
    previous=[d.get('energy') for _,d in samples]
    if any(type(v) not in (float,int) for v in previous):
        raise ValueError('Missing cumulative energy in passive samples')
    if any(b+0.02<a for a,b in zip(previous,previous[1:])):
        raise ValueError('Energy decreased in passive MQTT')
    return {'messages':len(samples),'span_seconds':round(samples[-1][0]-samples[0][0],1),
            'latest_metrics':{k:last[k] for k in ('power','current','voltage','energy')},
            'router_voltage_scale_independently_verified':False if role=='Router' else True,
            'raw_zcl_report_proven':False}


def arguments():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('device','ieee','expect-role','expect-build','mqtt-config','broker',
                'ssh-host','ssh-key','output'):p.add_argument('--'+key,required=True)
    p.add_argument('--ssh-user',default='root')
    p.add_argument('--database',default='/config/zigbee2mqtt/database.db')
    p.add_argument('--observe-seconds',type=int,default=85)
    p.add_argument('--baseline',help='Private prior settings-snapshot JSON for persistence comparison')
    return p.parse_args()


def main():
    a=arguments()
    if a.expect_role not in ('Router','EndDevice') or not 60<=a.observe_seconds<=420:
        raise ValueError('Explicit valid role and bounded observation required')
    if not a.ieee.startswith('0x') or len(a.ieee)!=18:raise ValueError('Exact IEEE required')
    output=Path(a.output).expanduser().resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise ValueError('Private unused evidence path outside repo required')
    config=yaml.safe_load(Path(a.mqtt_config).read_text(encoding='utf8'))['mqtt']
    bridge=Bridge(config,a.broker,a.device)
    evidence={'at':dt.datetime.now().astimezone().isoformat(),'device':a.device,
              'ieee':a.ieee,'expected_role':a.expect_role,'expected_build':a.expect_build,
              'result':'unconfirmed','issues':[],'writes':0,'physical_acceptance':False}
    started=False
    try:
        bridge.start();started=True
        if bridge.state!='online':raise ValueError('Z2M bridge not online')
        device=select_target(bridge.inventory,a.ieee,a.device,a.expect_role,a.expect_build)
        if (device.get('model_id')!='TS011F-BS-PM' or
            (device.get('definition') or {}).get('model')!='TS011F_plug_1_2'):
            raise ValueError('Unexpected PM board/converter model')
        coordinator=bridge.info.get('coordinator',{}).get('ieee_address')
        if not coordinator:raise ValueError('Coordinator identity absent')
        node=read_node_descriptor(a.mqtt_config,a.broker,a.ieee,device['network_address'])
        if node.get('role')!=a.expect_role:raise ValueError('Live ZDO role mismatch')
        db=read_db(a.ssh_host,a.ssh_user,a.ssh_key,a.database,a.ieee)
        evidence['cached_pm']=pm_snapshot(db)
        attrs=check_persisted(db,a.ieee,a.expect_build,a.expect_role,coordinator)
        evidence['missing_reporting']=[r[1] for r in missing_reports(device)]
        evidence['settings_snapshot']=settings_snapshot(db)
        if a.baseline:
            old=json.loads(Path(a.baseline).read_text(encoding='utf8'))
            if isinstance(old,dict) and 'settings_snapshot' in old:
                if old.get('ieee') != a.ieee:
                    raise ValueError('Settings baseline belongs to another IEEE')
                old=old['settings_snapshot']
            evidence['settings_drift']=settings_drift(old,evidence['settings_snapshot'])
            if evidence['settings_drift']:raise ValueError('Settings drift relative to private baseline')
        else:evidence['settings_persistence']='no preflash baseline: not verified'
        end=time.monotonic()+a.observe_seconds
        while time.monotonic()<end:
            if bridge.errors:break
            time.sleep(min(.5,max(0,end-time.monotonic())))
        evidence['mqtt']=assess_samples(bridge.states,attrs,a.expect_role)
        if evidence['missing_reporting']:
            raise ValueError('Out-of-policy PM reporting: '+','.join(evidence['missing_reporting']))
        if bridge.errors:raise ValueError('Target-related Zigbee2MQTT errors during window')
        evidence['result']='read_only_audit_candidate'
    except Exception as error:evidence['issues'].append(type(error).__name__+': '+str(error)[:260])
    finally:
        if started:bridge.stop()
        evidence['target_errors']=bridge.errors[-12:]
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(evidence,indent=2,default=str),encoding='utf8')
        print('PRIVATE_EVIDENCE',str(output),'RESULT',evidence['result'],
              'ISSUES',evidence['issues'],'REPORT_GAPS',evidence.get('missing_reporting'),
              'MESSAGES',evidence.get('mqtt',{}).get('messages'),flush=True)
    if evidence['result']=='unconfirmed':raise SystemExit(2)

if __name__=='__main__':main()
