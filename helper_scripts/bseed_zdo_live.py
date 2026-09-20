"""Read-only Zigbee2MQTT 2.14-compatible ZDO node descriptor probe.

Never infer Zigbee node role from stale inventory after Router-to-EndDevice OTA.
"""
import json
from pathlib import Path
import threading
import time
import uuid
import paho.mqtt.client as mqtt
import yaml


def parse_node_descriptor(result, expected_network_address):
    if result.get('status') != 'ok': raise ValueError('ZDO request failed: '+str(result.get('error')))
    payload=result.get('data')
    if not isinstance(payload,list) or len(payload)<2 or payload[0]!=0 or not isinstance(payload[1],dict):
        raise ValueError('Missing successful ZDO node descriptor')
    node=payload[1]
    if node.get('nwkAddress') != expected_network_address:
        raise ValueError('ZDO response network address does not match target')
    role={0:'Coordinator',1:'Router',2:'EndDevice'}.get(node.get('logicalType'))
    if role is None:raise ValueError('Unknown Zigbee logicalType')
    return {'role':role,'logical_type':node['logicalType'],
            'rx_on_when_idle':(node.get('capabilities') or {}).get('rxOnWhenIdle'),
            'nwk_address':node['nwkAddress'],'manufacturer_code':node.get('manufacturerCode')}


def read_node_descriptor(mqtt_config, broker, ieee, network_address, timeout=20):
    """Exact IEEE and network address, transaction-matched ZDO node-descriptor read."""
    if not ieee.startswith('0x') or len(ieee)!=18:raise ValueError('Invalid target IEEE')
    nwk=int(network_address)
    config=yaml.safe_load(Path(mqtt_config).read_text(encoding='utf8'))['mqtt']
    base=config.get('base_topic','zigbee2mqtt');token='bseed-zdo-'+uuid.uuid4().hex
    client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id=token)
    client.username_pw_set(config.get('user',''),config.get('password',''))
    ready=threading.Event();answered=threading.Event();response={}
    def on_connect(c,_u,_f,reason,_p):
        if reason.is_failure:return
        c.subscribe(base+'/bridge/response/action',qos=1);ready.set()
    def on_message(_c,_u,message):
        if message.topic!=base+'/bridge/response/action':return
        try:body=json.loads(message.payload)
        except (ValueError,UnicodeDecodeError):return
        if body.get('transaction')==token:response.update(body);answered.set()
    client.on_connect=on_connect;client.on_message=on_message
    client.connect(broker,1883,10);client.loop_start()
    try:
        if not ready.wait(10):raise TimeoutError('MQTT ZDO subscription not ready')
        time.sleep(0.3)
        payload={'transaction':token,'action':'raw',
          'params':{'ieee_address':ieee,'network_address':nwk,
                    'profile_id':0,'cluster_key':2,'zdo_params':[nwk],
                    'disable_response':False}}
        sent=client.publish(base+'/bridge/request/action',json.dumps(payload),qos=1)
        sent.wait_for_publish(5)
        if not sent.is_published():raise TimeoutError('ZDO MQTT request was not published')
        if not answered.wait(timeout):raise TimeoutError('No transaction-matched ZDO node descriptor')
        return parse_node_descriptor(response,nwk)
    finally:
        client.loop_stop();client.disconnect()
