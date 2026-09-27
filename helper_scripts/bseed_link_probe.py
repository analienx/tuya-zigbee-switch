"""Bounded relay read through the separately installed BSEED Z2M extension.

No relay writes, interviews, joining, retries or OTA requests. Output is private.
"""
import argparse
import json
from pathlib import Path
import threading
import time

import paho.mqtt.client as mqtt
import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('mqtt-config', 'broker', 'device', 'ieee', 'endpoint', 'cluster',
                 'attribute', 'request-id', 'timeout-seconds', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    if (int(args.endpoint), args.cluster, args.attribute, int(args.timeout_seconds)) != (2, 'genOnOff', 'onOff', 12):
        raise ValueError('Only bounded endpoint-2 relay reads are allowed')
    output = Path(args.output).resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise ValueError('An unused private output outside the repository is required')
    config = yaml.safe_load(Path(args.mqtt_config).read_text(encoding='utf8'))['mqtt']
    base = config.get('base_topic', 'zigbee2mqtt')
    response_topic = base + '/bridge/response/bseed/link_probe'
    ready, answered = threading.Event(), threading.Event()
    response = {}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=args.request_id)
    client.username_pw_set(config.get('user', ''), config.get('password', ''))

    def on_connect(c, _userdata, _flags, reason, _properties):
        if not reason.is_failure:
            c.subscribe(response_topic, qos=1)

    def on_subscribe(_c, _userdata, _mid, reasons, _properties):
        if reasons and all(not code.is_failure for code in reasons):
            ready.set()

    def on_message(_c, _userdata, message):
        if message.retain or message.topic != response_topic:
            return
        try:
            value = json.loads(message.payload)
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(value, dict) and value.get('request_id') == args.request_id:
            response.update(value)
            answered.set()

    client.on_connect, client.on_subscribe, client.on_message = on_connect, on_subscribe, on_message
    started = time.monotonic()
    try:
        client.connect(args.broker, 1883, 10)
        client.loop_start()
        if not ready.wait(3):
            raise TimeoutError('Probe response subscription unavailable')
        payload = dict(request_id=args.request_id, device=args.device, ieee=args.ieee,
                       endpoint=2, cluster=args.cluster, attribute=args.attribute,
                       timeout_seconds=12, issued_at=time.time())
        sent = client.publish(base + '/bridge/request/bseed/link_probe', json.dumps(payload), qos=1, retain=False)
        sent.wait_for_publish(2)
        if not sent.is_published() or not answered.wait(max(0, 16 - (time.monotonic() - started))):
            raise TimeoutError('No correlated backend read response')
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf8') as handle:
            json.dump(response, handle, indent=2)
        if response.get('passed') is not True:
            raise RuntimeError('Backend read failed: ' + str(response.get('errors')))
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == '__main__':
    main()
