"""Small read-mostly Home Assistant Zigbee2MQTT probe.

One CLI for the ad-hoc checks previously scattered across scratch scripts:
inventory snapshots, fresh device state reads, scoped join windows,
interview triggers, and Z2M log tailing. No secrets in git: MQTT
credentials are read at runtime from the HA configuration over SSH and
never printed.

Examples:
  python helper_scripts/ha_mqtt.py state --device WorkroomSocketCabinet
  python helper_scripts/ha_mqtt.py join --via WRSocketWindowLeft --seconds 120
  python helper_scripts/ha_mqtt.py interview --ieee 0xa4c138c5f07ee732
  python helper_scripts/ha_mqtt.py logs --pattern device_joined --since 10m
"""
import argparse
import json
import re
import subprocess
import sys

DEFAULT_SSH_HOST = 'ha'
DEFAULT_CONFIG = '/homeassistant/zigbee2mqtt/configuration.yaml'
DEFAULT_CONTAINER = 'app_45df7312_zigbee2mqtt'


def ssh(host, remote, timeout=60):
    proc = subprocess.run(['ssh', host, remote], capture_output=True,
                          text=True, timeout=timeout)
    if proc.returncode:
        raise RuntimeError('ssh failed: ' + (proc.stderr or proc.stdout)[:300])
    return proc.stdout


def mqtt_credentials(host, config=DEFAULT_CONFIG):
    cfg = ssh(host, 'cat %s' % config)
    block = re.search(r'mqtt:\s*\n((?:\s+\S.*\n)+)', cfg).group(1)
    user = re.search(r'user:\s*(\S+)', block).group(1)
    password = re.search(r'password:\s*(\S+)', block).group(1)
    server = re.search(r'server:\s*mqtt://([^:\s]+)', block)
    return user, password, server.group(1) if server else 'localhost'


def sub_command(user, password, broker, topic, count=1, wait=20):
    return ('mosquitto_sub -h %s -u %s -P %s -t %s -C %d -W %d'
            % (broker, user, password, topic, count, wait))


def pub_command(user, password, broker, topic, payload):
    return ("mosquitto_pub -h %s -u %s -P %s -t %s -m '%s' -q 1"
            % (broker, user, password, topic, payload))


def bridge_devices(host, user, password, broker):
    request = (sub_command(user, password, broker, 'zigbee2mqtt/bridge/devices')
               + ' & BPID=$!; '
               + pub_command(user, password, broker,
                             'zigbee2mqtt/bridge/request/devices', '{"t":"x"}')
               + '; wait $BPID')
    out = ssh(host, request, timeout=90)
    return json.loads(out.strip().splitlines()[-1])


def find_device(devices, identifier):
    hits = [d for d in devices
            if d.get('ieee_address') == identifier
            or d.get('friendly_name') == identifier]
    if len(hits) != 1:
        raise RuntimeError('identifier matched %d devices' % len(hits))
    return hits[0]


def summarize_device(device):
    keys = ('friendly_name', 'ieee_address', 'manufacturer', 'model_id',
            'type', 'interview_state', 'interview_completed',
            'software_build_id', 'date_code', 'network_address',
            'power_source')
    return {k: device.get(k) for k in keys}


def fresh_state(host, user, password, broker, device, get_key='state', count=2):
    topic = 'zigbee2mqtt/%s' % device
    combo = ('%s & BPID=$!; sleep 1; %s; wait $BPID'
             % (sub_command(user, password, broker, topic, count=count),
                pub_command(user, password, broker, topic + '/get',
                            json.dumps({get_key: ''}))))
    out = ssh(host, combo, timeout=90)
    states = []
    for line in out.strip().splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and 'update' in item:
            states.append(item)
    return states


def cmd_inventory(args):
    user, password, broker = mqtt_credentials(args.ssh_host, args.config)
    devices = bridge_devices(args.ssh_host, user, password, broker)
    if args.ieee or args.device:
        print(json.dumps(summarize_device(
            find_device(devices, args.ieee or args.device)), indent=1))
        return
    rows = []
    for dev in devices:
        if dev.get('type') == 'Coordinator':
            continue
        rows.append(summarize_device(dev))
    print(json.dumps(rows, indent=1))


def cmd_state(args):
    user, password, broker = mqtt_credentials(args.ssh_host, args.config)
    states = fresh_state(args.ssh_host, user, password, broker, args.device,
                         get_key=args.get_key, count=args.count)
    if not states:
        raise RuntimeError('no fresh state read arrived')
    last = states[-1]
    print(json.dumps({'relay': last.get('state_relay'),
                      'power': last.get('power'),
                      'voltage': last.get('voltage'),
                      'update': last.get('update')}, indent=1))


def cmd_join(args):
    user, password, broker = mqtt_credentials(args.ssh_host, args.config)
    payload = json.dumps({'time': args.seconds, 'device': args.via})
    ssh(args.ssh_host, pub_command(user, password, broker,
                                   'zigbee2mqtt/bridge/request/permit_join',
                                   payload))
    print('join window opened: %ss via %s' % (args.seconds, args.via))


def cmd_interview(args):
    user, password, broker = mqtt_credentials(args.ssh_host, args.config)
    ssh(args.ssh_host, pub_command(
        user, password, broker,
        'zigbee2mqtt/bridge/request/device/interview',
        json.dumps({'id': args.ieee})))
    print('interview requested for %s' % args.ieee)


def cmd_logs(args):
    out = ssh(args.ssh_host,
              "docker logs %s --since %s 2>&1 | grep -E '%s' | tail -%d"
              % (args.container, args.since, args.pattern, args.lines),
              timeout=90)
    print(out, end='')


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-host', default=DEFAULT_SSH_HOST)
    parser.add_argument('--config', default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest='command', required=True)
    inv = sub.add_parser('inventory', help='bridge device snapshot')
    inv.add_argument('--device')
    inv.add_argument('--ieee')
    inv.set_defaults(func=cmd_inventory)
    st = sub.add_parser('state', help='fresh device state read')
    st.add_argument('--device', required=True)
    st.add_argument('--get-key', default='state')
    st.add_argument('--count', type=int, default=2)
    st.set_defaults(func=cmd_state)
    jn = sub.add_parser('join', help='bounded router-scoped join window')
    jn.add_argument('--via', required=True)
    jn.add_argument('--seconds', type=int, default=120)
    jn.set_defaults(func=cmd_join)
    iv = sub.add_parser('interview', help='request device interview')
    iv.add_argument('--ieee', required=True)
    iv.set_defaults(func=cmd_interview)
    lg = sub.add_parser('logs', help='tail Z2M container logs by pattern')
    lg.add_argument('--pattern', required=True)
    lg.add_argument('--since', default='10m')
    lg.add_argument('--lines', type=int, default=15)
    lg.add_argument('--container', default=DEFAULT_CONTAINER)
    lg.set_defaults(func=cmd_logs)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except RuntimeError as error:
        print('ERROR: %s' % error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
