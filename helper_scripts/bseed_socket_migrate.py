"""One-command BSEED socket migration: plan -> publish -> run -> release.

    set BSEED_SITE=<private site.json>        (or pass --site)
    python helper_scripts/bseed_socket_migrate.py plan    --device NAME --to client|router|same
    python helper_scripts/bseed_socket_migrate.py publish --device NAME
    python helper_scripts/bseed_socket_migrate.py run     --device NAME
    python helper_scripts/bseed_socket_migrate.py release --device NAME --authority "<owner's words>"
    python helper_scripts/bseed_socket_migrate.py queue   --device A --device B --to client [--release-authority "..."]
    python helper_scripts/bseed_socket_migrate.py queue   --target A=client --target B=router [--release-authority "..."]
    python helper_scripts/bseed_socket_migrate.py status  --device NAME

``plan`` is read-only on the network: it reads the live inventory and the
target's neighbour table, picks the route and the sealed candidate, writes the
private workdir (profile, image or header-only wrapper, template index) and
runs the same load_profile / version-policy / relay / recovery gates the
campaign runs, so every refusal surfaces before anything is published.

``run`` waits for the shared network lease, then runs the canonical campaign
(``transition`` for a role change, ``flash`` otherwise) with a durable log.
Historical stock conversion via relay-OFF is now refused. Exactly two
2026-11-22 stock Client canaries preserve the live ON relay without
a relay SET; they accept unknown stock PM freshness and never guarantee
mains continuity through reboot. Other devices remain unchanged.
A transfer that stops (``update_error`` / timeout) is reconciled with the
canonical ``reconcile-source`` and retried only while its OTA progress
strictly increases (the resume supervisor's gate), at most ``--max-resumes``.

``release`` retires a ``postflash_candidate`` lease through the private
mutation broker; it needs the owner's acceptance words. ``queue`` chains
plan/publish/run per device and releases each lease only with
``--release-authority`` (a standing acceptance for candidates that pass
postflash); otherwise it stops after the first candidate.

Routes (anything else is refused with the reason):
  stock PM  _TZ3000_b28wrpvx/TS011F Router -> custom PM Client (stock wrapper 54179)
  custom PM b28wrpvx/TS011F-BS-PM same role (native image, meter proof)
  custom PM Client <-> Router (FORCE wrapper on the source query type)
  custom non-PM o1jzcxou/TS011F-BS same role (only where the recovery gate admits it)
Private paths, images and profiles never enter the repository.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / 'helper_scripts' / 'bseed_ota_campaign.py'
WRAPPER_VERSION = 0xFFFFFFFF
STOCK_IMAGE_TYPE = 54179
STOCK_PM = ('_TZ3000_b28wrpvx', 'TS011F')
CUSTOM_PM = ('b28wrpvx', 'TS011F-BS-PM')
CUSTOM_NONPM = ('o1jzcxou', 'TS011F-BS')
ROLE = {'client': 'EndDevice', 'router': 'Router'}
VARIANT = {(CUSTOM_PM, 'EndDevice'): 'pm-client', (CUSTOM_PM, 'Router'): 'pm-router',
           (CUSTOM_NONPM, 'EndDevice'): 'nonpm-client', (CUSTOM_NONPM, 'Router'): 'nonpm-router'}
SOURCE_QUERY_TYPE = {(CUSTOM_PM, 'EndDevice'): 65024, (CUSTOM_PM, 'Router'): 43556}
STATE_FILE = 'MIGRATION.json'
FAILED_PHASES = ('update_error', 'update_timeout_or_unconfirmed')
PLAN_RADIO = threading.Lock()


class Refused(ValueError):
    """The socket cannot be migrated by this flow; the message says why."""


# ---------------------------------------------------------------- pure planning
def choose_route(live, to):
    """live: Z2M inventory row. to: client|router|same. Returns the route dict."""
    board = (live.get('manufacturer'), live.get('model_id'))
    source = live.get('type')
    if source not in ('Router', 'EndDevice'):
        raise Refused('Source role %r is not a socket role' % source)
    target = source if to == 'same' else ROLE.get(to)
    if target is None:
        raise Refused('--to must be client, router or same')
    if board == STOCK_PM:
        if source != 'Router' or target != 'EndDevice':
            raise Refused('Stock PM sockets convert only to a Client here; no reviewed stock-to-Router route yet')
        return dict(kind='stock_pm_to_client', board=CUSTOM_PM, source=source, target=target,
                    variant='pm-client', wrapper_type=STOCK_IMAGE_TYPE)
    if board == CUSTOM_PM:
        if source == target:
            return dict(kind='pm_same_role', board=board, source=source, target=target,
                        variant=VARIANT[(board, target)], wrapper_type=None)
        return dict(kind='pm_role_change', board=board, source=source, target=target,
                    variant=VARIANT[(board, target)], wrapper_type=SOURCE_QUERY_TYPE[(board, source)])
    if board == CUSTOM_NONPM:
        if source != target:
            raise Refused('Non-PM role changes are pinned to exact canaries; not automated')
        return dict(kind='nonpm_same_role', board=board, source=source, target=target,
                    variant=VARIANT[(board, target)], wrapper_type=None)
    raise Refused('Unsupported board %s/%s' % board)


def candidate_for(package, route):
    entries = [e for e in package['entries'] if e['variant'] == route['variant']]
    if len(entries) != 1:
        raise Refused('Candidate package has no unique %s entry' % route['variant'])
    return entries[0]


def wrap(native, image_type):
    """Header-only wrapper: outer image type + 0xFFFFFFFF; every other byte identical."""
    out = bytearray(native)
    struct.pack_into('<H', out, 12, image_type)
    struct.pack_into('<I', out, 14, WRAPPER_VERSION)
    if any(native[i] != out[i] for i in range(len(native)) if not 12 <= i < 18):
        raise AssertionError('wrapper changed bytes outside the OTA header fields')
    return bytes(out)


def choose_join_via(neighbors, devices, target_ieee):
    """Strongest Router neighbour (custom BSEED Routers win ties). Returns (name, lqi, children)."""
    by_ieee = {d.get('ieee_address'): d for d in devices}
    routers, children = [], []
    for entry in neighbors:
        d = by_ieee.get(entry.get('eui64')) or {}
        if entry.get('eui64') == target_ieee or not d.get('friendly_name'):
            continue
        if entry.get('relationship') == 1 and d.get('type') == 'EndDevice':
            children.append(d['friendly_name'])
        if d.get('type') == 'Router' and d.get('interview_state', 'SUCCESSFUL') == 'SUCCESSFUL':
            custom = 'bseed' in str(d.get('software_build_id') or '')
            routers.append((int(entry.get('lqi') or 0), custom, d['friendly_name']))
    if not routers:
        return None, None, sorted(children)  # network-wide rejoin needs none
    lqi, _custom, name = max(routers)
    return name, lqi, sorted(children)


def build_profile(site, live, route, candidate, *, image, sha256, native_image=None,
                  native_sha256=None, join_via=None, workdir, folder, preflash_file_version=None,
                   relay_baseline=None):
    from bseed_operator_load_policy import require_preserve_state_for_flash
    if relay_baseline is None:
        # Tests/explicit helper callers may provide a verified live relay value;
        # the production planner always passes a freshly read value below.
        relay_baseline = live.get('relay_state')
    if relay_baseline not in ('ON', 'OFF'):
        raise Refused('Cannot preserve relay: a fresh ON/OFF baseline is required')
    tag = sha256[:12]
    url_base = site['publish']['url_base'].rstrip('/') + '/' + folder
    role_change = route['source'] != route['target']
    p = {
        'device': live['friendly_name'], 'ieee': live['ieee_address'],
        'manufacturer': live['manufacturer'], 'model': live['model_id'],
        'preflash_role': route['source'], 'postflash_role': route['target'],
        'postflash_build': candidate['build'],
        'image': str(image), 'sha256': sha256,
        'url': url_base + '/firmware.ota?v=' + tag, 'index_url': url_base + '/index.json?v=' + tag,
        'index_output': str(Path(site['serve_root']) / ('index-%s.json' % folder)),
        'template_index': str(Path(workdir) / 'template_index.json'),
        'mqtt_config': site['mqtt_config'], 'broker': site['broker'], 'workdir': str(workdir),
        'network_lock_dir': site['network_lock_dir'], 'network_id': site['network_id'],
        'network_lock_shared': True, 'manufacturer_code': candidate['manufacturerCode'],
        'auto_relay_off': False, 'expect_relay': relay_baseline,
        'relay_state_policy': 'preserve',
        'operator_connected_load_risk_accepted': True,
        'force_test_transition': False,
        'preserve_device_state': True, 'block_bytes': 48, 'response_delay_ms': 1200,
        'request_timeout_ms': 600000, 'monitor_seconds': 14400, 'check_timeout_seconds': 120,
        'postota_settle_seconds': 30, 'observe_seconds': 90,
    }
    if route['wrapper_type'] is None:
        p.update(image_type=candidate['imageType'], file_version='0x%08X' % candidate['fileVersion'])
    else:
        p.update(image_type=route['wrapper_type'], file_version='0xffffffff',
                 native_image=str(native_image), native_sha256=native_sha256)
    if role_change:
        # Owner rule: rejoin network-wide, never scoped to one parent Router.
        from bseed_join_policy import DEFAULT_WINDOW_SECONDS, MAX_NETWORK_WIDE_SECONDS
        # Source recovery (resume) keeps its narrow mechanisms: scoped through
        # the strongest Router when there is one, else coordinator-only.
        p.update(join_mode='network-wide', join_seconds=DEFAULT_WINDOW_SECONDS,
                 join_total_seconds=MAX_NETWORK_WIDE_SECONDS, allow_join_all_fallback=False,
                 source_rejoin_strategy='scoped' if join_via else 'coordinator')
        if join_via:
            p['join_via'] = join_via  # informational: strongest Router neighbour at plan time
    pm = route['board'] == CUSTOM_PM
    if pm:
        p.update(require_pm=True, non_pm=False, pm_ssh_host=site['pm_ssh_host'],
                 pm_ssh_key=site['pm_ssh_key'], pm_max_writes=4, pm_observe_seconds=420,
                 pm_expected_idle=False)
    else:
        p.update(require_pm=False, non_pm=True, preflash_relay_physical_mode='follow_state')
    if route['kind'] == 'stock_pm_to_client':
        p.update(stock_client_conversion=True, pm_preflash_load_proof='meter',
                  relay_get_key='state', postflash_manufacturer=CUSTOM_PM[0],
                  postflash_model=CUSTOM_PM[1], pm_allow_configure=True)
        if live['friendly_name'] not in site.get('stock_loaded_preserve_devices', []):
            raise Refused('Stock-to-Client requires a pinned owner-authorized preserve-ON conversion; no relay-OFF fallback')
        if live['friendly_name'] in site.get('stock_loaded_preserve_devices', []):
            if relay_baseline != 'ON':
                raise Refused('Pinned stock conversion requires the already-ON relay baseline; never command ON during planning')
            # Two exact-IEEE operator-authorized loaded canaries only.
            # No relay command, no assertion that stock PM is independently fresh.
            p.update(stock_client_preserve_loaded=True, auto_relay_off=False,
                     expect_relay='ON', relay_state_policy='preserve',
                     pm_preflash_load_proof='meter', operator_waive_preflash_pm=True,
                     operator_connected_load_risk_accepted=True,
                      pm_expected_idle=False,
                      response_delay_ms=1800)
            from bseed_stock_client_loaded_canary import authorized as loaded_authorized
            loaded_authorized(p)
    else:
        p.update(preflash_build=live['software_build_id'], relay_get_key='state_relay')
        if pm:
            p.update(pm_preflash_load_proof='meter', pm_allow_configure=role_change)
    if route['kind'] == 'pm_role_change':
        p.update(force_test_transition=True, preflash_query_image_type=route['wrapper_type'])
        if preflash_file_version is not None:
            p['preflash_file_version'] = '0x%08X' % preflash_file_version
    device = (site.get('devices') or {}).get(live['friendly_name']) or {}
    if route['target'] == 'Router' and pm and not role_change:
        # Same-role PM Router flashes need private build-matrix evidence and
        # this device's settings baseline (site.json devices.<name>).
        for key in ('pm_router_matrix_evidence', 'pm_settings_baseline'):
            if device.get(key) or site.get(key):
                p[key] = device.get(key) or site.get(key)
    if device.get('parallel_ota_max', 1) != 1:
        from bseed_network_campaign_lock import MULTI_SLOT_MIN_DELAY_MS
        slots = device['parallel_ota_max']
        p.update(parallel_ota_max=slots,
                 concurrent_ota_ieees=device.get('concurrent_ota_ieees', []),
                 response_delay_ms=device.get('response_delay_ms', MULTI_SLOT_MIN_DELAY_MS.get(slots, 1800)),
                 source_rejoin_strategy='none', allow_join_all_fallback=False)
        if role_change:
            p['parallel_role_transition'] = True
        from bseed_network_campaign_lock import require_profile_authority
        require_profile_authority(p)
    require_preserve_state_for_flash(p)
    return p


def template_index(profile, transport, manufacturer_name):
    return [{'fileName': Path(profile['image']).name, 'fileVersion': int(profile['file_version'], 16),
             'fileSize': len(transport), 'url': profile['url'], 'imageType': int(profile['image_type']),
             'manufacturerCode': profile['manufacturer_code'],
             'sha512': hashlib.sha512(transport).hexdigest(), 'manufacturerName': [manufacturer_name]}]


def resume_allowed(history, resumes_done, max_resumes):
    """Same contract as the resume supervisor: progress must strictly increase."""
    from bseed_ota_resume_supervisor import progress_retry_gate
    gate = progress_retry_gate(history)
    return gate['allow_retry'] and resumes_done < max_resumes, gate


def release_request(lease, lock, profile, authority):
    """Mutation-broker request retiring an accepted candidate's lease, or Refused."""
    if not authority or len(authority.strip()) < 8:
        raise Refused("release needs the owner's acceptance words (--authority)")
    for name, record in (('network lease', lease), ('campaign lock', lock)):
        if (record.get('ieee'), record.get('phase')) != (profile['ieee'], 'postflash_candidate'):
            raise Refused('%s is not this device at postflash_candidate: %r' % (name, record.get('phase')))
    if lease.get('token') != lock.get('token'):
        raise Refused('network lease token differs from the campaign lock')
    relay = Path(profile['workdir']) / 'RELAY_AUTO_OFF.json'
    if relay.is_file() and json.loads(relay.read_text(encoding='utf8')).get('restored') is not True:
        raise Refused('relay auto-OFF record not restored yet')
    return {'schema': 1, 'operation': 'quarantine', 'target': str(lease['_path']),
            'reason': 'Retire completed %s %s -> %s lease (token %s); postflash_candidate; '
                      'this action does not start OTA' % (profile['device'], profile.get('preflash_build', 'stock'),
                                                         profile['postflash_build'], lock['token']),
            'authority': authority.strip(),
            'limits': {'max_files': 1, 'max_bytes': 65536, 'max_depth': 1}}


# ---------------------------------------------------------------- live I/O
def load_site(path):
    path = path or os.environ.get('BSEED_SITE')
    if not path:
        raise SystemExit('Set BSEED_SITE or pass --site (private site.json)')
    site = json.loads(Path(path).read_text(encoding='utf8'))
    if Path(path).resolve().is_relative_to(ROOT):
        raise SystemExit('site.json must be private, outside the repository')
    return site


def inventory(site):
    import paho.mqtt.client as mqtt
    import threading
    import yaml
    config = yaml.safe_load(Path(site['mqtt_config']).read_text(encoding='utf8'))['mqtt']
    base, got, done = config.get('base_topic', 'zigbee2mqtt'), {}, threading.Event()

    def on_message(_c, _u, m):
        got[m.topic.rsplit('/', 1)[-1]] = json.loads(m.payload)
        if {'devices', 'info'} <= set(got):
            done.set()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='bseed-migrate-inv-' + uuid.uuid4().hex[:8])
    client.username_pw_set(config.get('user', ''), config.get('password', ''))
    client.on_message = on_message
    client.connect(site['broker'], 1883, 10)
    client.subscribe([(base + '/bridge/devices', 1), (base + '/bridge/info', 1)])
    client.loop_start()
    try:
        if not done.wait(15):
            raise TimeoutError('Zigbee2MQTT bridge inventory not received')
    finally:
        client.loop_stop()
        client.disconnect()
    if (got['info'].get('coordinator') or {}).get('ieee_address') != site['network_id']:
        raise SystemExit('Live coordinator differs from site network_id')
    return got['devices']


def installed_file_version(site, device, key):
    """OTA installed_version from a fresh read-only GET (needed for a FORCE route)."""
    import paho.mqtt.client as mqtt
    import threading
    import yaml
    config = yaml.safe_load(Path(site['mqtt_config']).read_text(encoding='utf8'))['mqtt']
    topic, seen = config.get('base_topic', 'zigbee2mqtt') + '/' + device, threading.Event()
    box = {}

    def on_message(_c, _u, m):
        box.update(json.loads(m.payload))
        seen.set()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='bseed-migrate-ver-' + uuid.uuid4().hex[:8])
    client.username_pw_set(config.get('user', ''), config.get('password', ''))
    client.on_message = on_message
    client.connect(site['broker'], 1883, 10)
    client.subscribe(topic, 1)
    client.loop_start()
    try:
        time.sleep(0.5)
        client.publish(topic + '/get', json.dumps({key: ''}), qos=1).wait_for_publish(5)
        seen.wait(15)
    finally:
        client.loop_stop()
        client.disconnect()
    return (box.get('update') or {}).get('installed_version')


def read_relay_baseline(site, live):
    """Read a fresh exact-device ON/OFF state. Never write a relay or read watts.

    Firmware source's cached inventory is not a physical relay baseline.
    A missed response blocks planning because 'preserve current state' would
    otherwise be impossible to verify.
    """
    import paho.mqtt.client as mqtt
    import threading
    import yaml
    config = yaml.safe_load(Path(site['mqtt_config']).read_text(encoding='utf8'))['mqtt']
    base = config.get('base_topic', 'zigbee2mqtt')
    name = live['friendly_name']
    source_stock = live.get('manufacturer', '').startswith(('_TZ3000_', '_TZ3002_'))
    key = 'state' if source_stock else 'state_relay'
    topic = base + '/' + name
    seen = threading.Event()
    results = []
    def on_message(_client, _userdata, msg):
        if msg.topic != topic or msg.retain:
            return
        try:
            value = json.loads(msg.payload)
        except (ValueError, UnicodeDecodeError):
            return
        if (value.get('device') or {}).get('ieeeAddr') != live['ieee_address']:
            return
        if value.get(key) in ('ON', 'OFF'):
            results.append(value[key])
            seen.set()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id='bseed-relay-baseline-' + uuid.uuid4().hex[:7])
    client.username_pw_set(config.get('user', ''), config.get('password', ''))
    client.on_message = on_message
    client.connect(site['broker'], 1883, 10)
    client.subscribe(topic, qos=1)
    client.loop_start()
    try:
        for _ in range(2):
            seen.clear()
            results.clear()
            client.publish(topic + '/get', json.dumps({key: ''}), qos=1).wait_for_publish(5)
            if seen.wait(9) and results:
                return results[-1]
    finally:
        client.loop_stop()
        client.disconnect()
    raise Refused('Exact socket relay state unavailable; cannot guarantee a preserve-state OTA plan')


def workdir_for(site, device):
    root = Path(site['campaign_root']) / device
    current = root / 'CURRENT'
    if not current.is_file():
        raise SystemExit('No plan for %s; run plan first' % device)
    return Path(current.read_text(encoding='utf8').strip())


def campaign(profile_path, mode, log=None, confirm_ieee=None):
    cmd = [sys.executable, '-u', str(CAMPAIGN), '--profile', str(profile_path), '--mode', mode]
    if confirm_ieee:
        cmd += ['--confirm-ieee', confirm_ieee]
    if log is None:
        return subprocess.call(cmd, stdin=subprocess.DEVNULL)
    with open(log, 'ab') as handle:  # durable file, never a pipe
        return subprocess.call(cmd, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf8'))
    except (OSError, ValueError):
        return None


def active_leases(site):
    return sorted(p for p in Path(site['network_lock_dir']).glob('bseed-zigbee-*.json'))


def cmd_plan(site, args):
    sys.path.insert(0, str(ROOT / 'helper_scripts'))
    with PLAN_RADIO:
        devices = inventory(site)
    rows = [d for d in devices if d.get('friendly_name') == args.device]
    if len(rows) != 1:
        raise Refused('Device %r not found exactly once in Zigbee2MQTT' % args.device)
    live = rows[0]
    if live.get('interview_state') != 'SUCCESSFUL':
        raise Refused('Source interview incomplete')
    route = choose_route(live, args.to)
    package_dir = Path(site['candidate_package'])
    package = json.loads((package_dir / 'CANDIDATE_PACKAGE.json').read_text(encoding='utf8'))
    candidate = candidate_for(package, route)
    if live.get('software_build_id') == candidate['build']:
        raise Refused('%s already runs %s' % (args.device, candidate['build']))
    native = (package_dir / route['variant'] / 'firmware.ota').read_bytes()
    if hashlib.sha256(native).hexdigest() != candidate['otaSha256']:
        raise Refused('Sealed candidate file hash differs from its package entry')
    source = live.get('software_build_id') or 'stock'
    label = '%s-to-%s' % ('stock' if route['kind'] == 'stock_pm_to_client' else source, candidate['build'])
    work = Path(site['campaign_root']) / args.device / label
    if (work / 'profile.json').exists() and not args.replan:
        raise Refused('Plan already exists: %s (use --replan only before any flash)' % work)
    if (work / 'ACTIVE_LOCK.json').exists():
        raise Refused('A campaign already ran in %s; resume it with run, never replan' % work)
    join_via, children = None, []
    if route['source'] != route['target']:
        from bseed_zdo_live import read_neighbor_table
        with PLAN_RADIO:
            neighbors = read_neighbor_table(site['mqtt_config'], site['broker'], live['ieee_address'],
                                            live['network_address'], require_complete=route['source'] == 'Router')
        known = {d.get('ieee_address') for d in devices}
        if (route['target'] == 'EndDevice' and
                not any(entry.get('eui64') in known for entry in neighbors or [])):
            # A Router becoming a Client orphans its children: an empty or
            # unreadable table cannot list them, so refuse rather than plan
            # blind. A Client source has no children to lose.
            raise Refused('Neighbour table empty or unreadable; retry plan')
        join_via, lqi, children = choose_join_via(neighbors, devices, live['ieee_address'])
        print('STRONGEST_ROUTER_NEIGHBOUR' if join_via else 'NO_ROUTER_NEIGHBOUR', join_via, 'lqi', lqi)
        if route['target'] == 'EndDevice' and children:
            print('CHILDREN_WILL_REPARENT', ', '.join(children))
    transport = native if route['wrapper_type'] is None else wrap(native, route['wrapper_type'])
    sha = hashlib.sha256(transport).hexdigest()
    work.mkdir(parents=True, exist_ok=True)
    native_path = None
    if route['wrapper_type'] is not None:
        native_path = work / ('native-%s.ota' % candidate['build'])
        native_path.write_bytes(native)
    folder = '%s-%s' % (args.device, label)
    image = Path(site['serve_root']) / (folder + '.ota')
    if image.exists() and hashlib.sha256(image.read_bytes()).hexdigest() != sha:
        raise Refused('A different image already uses %s' % image)
    image.write_bytes(transport)
    file_version = None
    with PLAN_RADIO:
        if route['kind'] == 'pm_role_change':
            file_version = installed_file_version(site, args.device, 'state_relay')
        relay_baseline = read_relay_baseline(site, live)
    profile = build_profile(site, live, route, candidate, image=image, sha256=sha,
                             native_image=native_path, native_sha256=candidate['otaSha256'],
                             join_via=join_via, workdir=work, folder=folder,
                             preflash_file_version=file_version,
                             relay_baseline=relay_baseline)
    manufacturer_name = live['manufacturer']
    (work / 'template_index.json').write_text(
        json.dumps(template_index(profile, transport, manufacturer_name), indent=2) + '\n', encoding='utf8')
    profile_path = work / 'profile.json'
    profile_path.write_text(json.dumps(profile, indent=2, sort_keys=True) + '\n', encoding='utf8')
    # The same gates the campaign runs: refuse now, not halfway through a run.
    import bseed_ota_campaign
    from bseed_socket_version_policy import require_increasing
    loaded = bseed_ota_campaign.load_profile(profile_path)
    policy = require_increasing(loaded)
    if loaded.get('non_pm') is True:
        from bseed_nonpm_recovery_gate import verify_recovery
        verify_recovery(loaded, confirm_unloaded=True)  # load is proven later by auto-OFF
    if loaded['postflash_role'] == 'Router' and loaded.get('require_pm') and not loaded.get('force_test_transition'):
        bseed_ota_campaign.verified_router_pm_candidate(loaded)
    state = {'device': args.device, 'route': route['kind'], 'workdir': str(work), 'planned_at': now(),
             'candidate': candidate['build'], 'image_sha256': sha, 'join_via': join_via,
             'children_reparent': children, 'policy': policy, 'resumes': 0}
    (work / STATE_FILE).write_text(json.dumps(state, indent=2, default=str) + '\n', encoding='utf8')
    (Path(site['campaign_root']) / args.device / 'CURRENT').write_text(str(work), encoding='utf8')
    print('PLANNED', json.dumps(state, default=str))
    return 0


def cmd_publish(site, args, *, work=None):
    work = work if work is not None else workdir_for(site, args.device)
    profile = json.loads((work / 'profile.json').read_text(encoding='utf8'))
    folder = Path(profile['image']).stem
    remote = site['publish']['remote_dir'].rstrip('/') + '/' + folder
    alias = site['publish']['ssh_alias']

    def step(cmd):
        if subprocess.call(cmd):
            raise SystemExit('publish step failed: ' + ' '.join(cmd[:2]))

    # prepare verifies the image as served over HTTP before it writes the
    # index, so the image goes up first and the index only after prepare.
    step(['ssh', '-o', 'BatchMode=yes', alias, 'mkdir -p ' + remote])
    step(['scp', '-q', '-o', 'BatchMode=yes', profile['image'], alias + ':' + remote + '/firmware.ota'])
    if campaign(work / 'profile.json', 'prepare'):
        raise SystemExit('prepare failed')
    step(['scp', '-q', '-o', 'BatchMode=yes', profile['index_output'], alias + ':' + remote + '/index.json'])
    import urllib.request
    with urllib.request.urlopen(profile['url'], timeout=30) as response:
        served = hashlib.sha256(response.read()).hexdigest()
    if served != profile['sha256']:
        raise SystemExit('Served image hash differs: ' + served)
    print('PUBLISHED', profile['url'])
    return 0


def wait_for_lease(site, timeout):
    deadline, announced = time.monotonic() + timeout, False
    while active_leases(site):
        if not announced:
            print('WAITING_FOR_NETWORK_LEASE', ', '.join(p.name for p in active_leases(site)), flush=True)
            announced = True
        if time.monotonic() > deadline:
            raise SystemExit('Network lease still held after %d s' % timeout)
        time.sleep(30)


def cmd_run(site, args, *, work=None):
    from bseed_ota_resume_supervisor import ota_progress_history
    work = work if work is not None else workdir_for(site, args.device)
    profile_path = work / 'profile.json'
    profile = json.loads(profile_path.read_text(encoding='utf8'))
    state = read_json(work / STATE_FILE) or {}
    mode = 'transition' if profile['preflash_role'] != profile['postflash_role'] else 'flash'
    lock = read_json(work / 'ACTIVE_LOCK.json') or {}
    if lock.get('phase') not in (None, 'source_unchanged_reconciled', *FAILED_PHASES):
        raise SystemExit('Campaign phase %r is not startable; see status' % lock.get('phase'))
    if lock.get('phase') in FAILED_PHASES:
        reconcile(work, profile, state)  # releases this campaign's own lease first
    while True:
        wait_for_lease(site, args.lease_wait_seconds)
        log = work / ('%s_%s.log' % (mode, dt.datetime.now().strftime('%Y%m%dT%H%M%S')))
        print('RUNNING', mode, profile['device'], 'log', log, flush=True)
        rc = campaign(profile_path, mode, log=log, confirm_ieee=profile['ieee'])
        lock = read_json(work / 'ACTIVE_LOCK.json') or {}
        if rc == 0:
            print('MIGRATION_CANDIDATE', profile['device'], lock.get('phase'), flush=True)
            return 0
        if lock.get('phase') not in FAILED_PHASES:
            print('STOPPED', profile['device'], 'phase', lock.get('phase'), 'exit', rc,
                  '- inspect', log, flush=True)
            return rc
        allowed, gate = resume_allowed(ota_progress_history(work, profile), state.get('resumes', 0),
                                       args.max_resumes)
        print('TRANSFER_STOPPED', json.dumps(gate), flush=True)
        if not allowed:
            print('NO_AUTOMATIC_RESUME: progress did not increase or resume budget spent', flush=True)
            return rc
        reconcile(work, profile, state)


def reconcile(work, profile, state):
    log = work / ('reconcile_%s.log' % dt.datetime.now().strftime('%Y%m%dT%H%M%S'))
    rc = campaign(work / 'profile.json', 'reconcile-source', log=log, confirm_ieee=profile['ieee'])
    if rc:
        raise SystemExit('reconcile-source failed (exit %d); see %s' % (rc, log))
    state['resumes'] = state.get('resumes', 0) + 1
    (work / STATE_FILE).write_text(json.dumps(state, indent=2, default=str) + '\n', encoding='utf8')
    print('RECONCILED_RESUMING', state['resumes'], flush=True)


def cmd_release(site, args, *, work=None):
    work = work if work is not None else workdir_for(site, args.device)
    profile = json.loads((work / 'profile.json').read_text(encoding='utf8'))
    lock = read_json(work / 'ACTIVE_LOCK.json') or {}
    leases = [p for p in active_leases(site) if (read_json(p) or {}).get('ieee') == profile['ieee']]
    if len(leases) != 1:
        raise Refused('No single network lease held by this device')
    lease = dict(read_json(leases[0]), _path=leases[0])
    request = release_request(lease, lock, profile, args.authority)
    broker = site['mutation_broker']
    stem = Path(site['network_lock_dir']) / ('release-%s-%s' % (args.device, lock['token'][-12:]))
    stem.with_suffix('.request.json').write_text(json.dumps(request, indent=1) + '\n', encoding='utf8')
    base = [sys.executable, broker['script'], '--policy', broker['policy']]
    plan = stem.with_suffix('.plan.json')
    if subprocess.call(base + ['plan', '--request', str(stem.with_suffix('.request.json')), '--out', str(plan)],
                       stdout=subprocess.DEVNULL):
        raise SystemExit('mutation broker refused the plan')
    plan_hash = json.loads(plan.read_text(encoding='utf8'))['plan_hash']
    if subprocess.call(base + ['execute', '--plan', str(plan), '--plan-hash', plan_hash]):
        raise SystemExit('mutation broker execution failed')
    if leases[0].exists():
        raise SystemExit('Lease still present after broker execution')
    print('LEASE_RELEASED', args.device)
    return 0


def cmd_status(site, args):
    work = workdir_for(site, args.device)
    print(json.dumps({'workdir': str(work), 'migration': read_json(work / STATE_FILE),
                      'lock_phase': (read_json(work / 'ACTIVE_LOCK.json') or {}).get('phase'),
                      'relay': read_json(work / 'RELAY_AUTO_OFF.json'),
                      'network_leases': [p.name for p in active_leases(site)]}, indent=2, default=str))
    return 0


def cmd_queue(site, args):
    if getattr(args, 'target', None):
        return cmd_mixed_queue(site, args)
    for device in args.device:
        one = argparse.Namespace(**dict(vars(args), device=device))
        root = Path(site['campaign_root']) / device / 'CURRENT'
        if not root.is_file():
            cmd_plan(site, one)
        cmd_publish(site, one)
        if cmd_run(site, one):
            raise SystemExit('Queue stopped at %s' % device)
        if not args.release_authority:
            print('QUEUE_PAUSED: %s is a candidate; release its lease to continue' % device)
            return 0
        cmd_release(site, argparse.Namespace(**dict(vars(one), authority=args.release_authority)))
    return 0


def parse_targets(values):
    targets = []
    seen = set()
    for value in values:
        device, _, role = value.rpartition('=')
        if (not device or device != device.strip() or device in ('.', '..') or
                any(char in device for char in '/\\:') or role not in {*ROLE, 'same'}):
            raise Refused('--target needs DEVICE=client, DEVICE=router or DEVICE=same')
        if device.casefold() in seen:
            raise Refused('Duplicate target: ' + device)
        seen.add(device.casefold())
        targets.append((device, role))
    return targets


def planned_target(site, args):
    from bseed_ota_campaign import load_profile, network_lock_path
    from bseed_operator_load_policy import require_preserve_state_for_flash

    current = Path(site['campaign_root']) / args.device / 'CURRENT'
    if not current.is_file() or args.replan:
        cmd_plan(site, args)
    work = workdir_for(site, args.device).resolve()
    path = work / 'profile.json'
    profile = load_profile(path)
    target_role = profile['preflash_role'] if args.to == 'same' else ROLE[args.to]
    if (profile['device'], profile['postflash_role']) != (args.device, target_role):
        raise Refused(f'{args.device}: current plan does not target {target_role}; replan before running')
    if (Path(profile['workdir']).resolve(), profile['network_id'], Path(profile['network_lock_dir']).resolve()) != (
            work, site['network_id'], Path(site['network_lock_dir']).resolve()):
        raise Refused(f'{args.device}: current plan has a different workdir or coordinator authority')
    require_preserve_state_for_flash(profile)
    network_lock_path(profile, required=True)
    lock_path = work / 'ACTIVE_LOCK.json'
    lock = json.loads(lock_path.read_text(encoding='utf8')) if lock_path.exists() else {}
    phase = lock.get('phase')
    finished = phase == 'postflash_candidate'
    if finished and (lock.get('device'), lock.get('ieee'), lock.get('sha256')) != (
            args.device, profile['ieee'], profile['sha256']):
        raise Refused(f'{args.device}: completed campaign does not match this plan')
    if not finished and phase not in (None, 'source_unchanged_reconciled', *FAILED_PHASES):
        raise Refused(f'{args.device}: campaign phase {phase!r} needs reconciliation')
    return args, work, profile, finished


def parallel_waves(targets):
    """Group adjacent opted-in campaigns with independently admitted firmware routes."""
    from bseed_ota_autopilot import validate_group, validate_policy

    wave = []
    for target in targets:
        profiles = [item[2] for item in (*wave, target)]
        if wave:
            try:
                for profile in profiles:
                    validate_policy(profile)
                validate_group(profiles)
            except ValueError:
                yield wave
                wave = []
        wave.append(target)
    if wave:
        yield wave


def check_parallel_topology(site, wave):
    from bseed_zdo_live import read_neighbor_table

    devices = {device['ieee_address'].lower(): device for device in inventory(site)}
    peers = {item[2]['ieee'].lower() for item in wave}
    for _, _, profile, _ in wave:
        ieee = profile['ieee'].lower()
        live = devices.get(ieee, {})
        if (live.get('friendly_name'), live.get('type')) != (profile['device'], profile['preflash_role']):
            raise Refused(f'{profile["device"]}: live identity or role changed before parallel OTA')
        if live['type'] == 'Router':
            neighbors = read_neighbor_table(site['mqtt_config'], site['broker'], profile['ieee'],
                                            live['network_address'], require_complete=True)
            if not neighbors:
                raise Refused(f'{profile["device"]}: live neighbor table unavailable')
            for neighbor in neighbors:
                child = (neighbor.get('eui64') or '').lower()
                if neighbor.get('relationship') == 1 and child in peers - {ieee}:
                    return f'{profile["device"]} lists another member as its child'
    return None


def check_role_transfer_topology(profile):
    """Check all potential peers again immediately before a role transfer."""
    peers = {str(ieee).lower() for ieee in profile.get('concurrent_ota_ieees', [])}
    rows = {d['ieee_address'].lower(): d for d in inventory(profile)}
    own = profile['ieee'].lower()
    if not peers | {own} <= rows.keys():
        raise Refused('Cannot verify every mixed-role peer in live inventory')
    wave = [(None, None, profile, False)]
    for ieee in sorted(peers):
        row = rows[ieee]
        wave.append((None, None, {'ieee': ieee, 'device': row['friendly_name'],
                                  'preflash_role': row['type']}, False))
    if reason := check_parallel_topology(profile, wave):
        raise Refused('Mixed-role topology conflict: ' + reason)


def cmd_mixed_queue(site, args):
    from bseed_ota_autopilot import run_many

    if args.device or args.to:
        raise Refused('Use --target for each socket or --device with one --to')
    if not 0 <= args.max_resumes <= 29:
        raise Refused('Mixed queue max-resumes must be 0..29')
    targets = [argparse.Namespace(**dict(vars(args), device=device, to=role))
               for device, role in parse_targets(args.target)]
    # Obtain a complete plan before publishing any member or starting an OTA.
    with ThreadPoolExecutor(max_workers=min(3, len(targets))) as pool:
        plans = list(pool.map(lambda one: planned_target(site, one), targets))
        ieees = {item[2]['ieee'].lower() for item in plans}
        pending = [item for item in plans if not item[3]]
        remote_folders = {Path(item[2]['image']).stem for item in pending}
        if len(ieees) != len(plans) or len(remote_folders) != len(pending):
            raise Refused('Mixed queue needs distinct IEEE addresses and publication folders')
        list(pool.map(lambda item: cmd_publish(site, item[0], work=item[1]), pending))

    # Only retire exact postflash candidates, with the same authority as the single-device flow.
    def release_wave(wave):
        held = {lease.get('ieee') for path in active_leases(site) if (lease := read_json(path))}
        for one, work, profile, _ in wave:
            if profile['ieee'] not in held:
                continue
            if not args.release_authority:
                print('QUEUE_PAUSED: %s is a candidate; release its lease to continue' % one.device)
                return True
            cmd_release(site, argparse.Namespace(**dict(vars(one), authority=args.release_authority)), work=work)
        return False

    if release_wave([item for item in plans if item[3]]):
        return 0
    for group in parallel_waves(pending):
        waves = [group]
        if len(group) > 1:
            wait_for_lease(site, args.lease_wait_seconds)
            if reason := check_parallel_topology(site, group):
                print('QUEUE_SERIAL', reason, flush=True)
                waves = [[item] for item in group]
        for wave in waves:
            if len(wave) == 1:
                one, work, _, _ = wave[0]
                result = cmd_run(site, one, work=work)
                if result:
                    return result
            else:
                results = run_many([item[1] / 'profile.json' for item in wave],
                                   [item[2]['ieee'] for item in wave],
                                   extra=['--max-attempts', str(args.max_resumes + 1), '--max-stagnant', '0'])
                print('QUEUE_WAVE', json.dumps(results), flush=True)
                if [(result['device'], result['state']) for result in results] != [
                        (item[0].device, 'installed') for item in wave]:
                    return 2
            for one, work, profile, _ in wave:
                lock = read_json(work / 'ACTIVE_LOCK.json') or {}
                if (lock.get('device'), lock.get('ieee'), lock.get('sha256'), lock.get('phase')) != (
                        one.device, profile['ieee'], profile['sha256'], 'postflash_candidate'):
                    raise Refused(f'{one.device}: no exact postflash candidate for this wave')
            if release_wave(wave):
                return 0
    return 0


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def main(argv=None):
    sys.path.insert(0, str(ROOT / 'helper_scripts'))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--site', help='private site.json (default: $BSEED_SITE)')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('plan', 'publish', 'run', 'release', 'status', 'queue'):
        p = sub.add_parser(name)
        p.add_argument('--device', required=name != 'queue', action='append' if name == 'queue' else 'store')
        if name in ('plan', 'queue'):
            p.add_argument('--to', choices=('client', 'router', 'same'), required=name == 'plan')
            p.add_argument('--replan', action='store_true', help='rewrite an unflashed plan')
        if name in ('run', 'queue'):
            p.add_argument('--max-resumes', type=int, default=3)
            p.add_argument('--lease-wait-seconds', type=int, default=4 * 3600)
        if name == 'release':
            p.add_argument('--authority', required=True, help="owner's acceptance words")
        if name == 'queue':
            p.add_argument('--target', action='append', metavar='DEVICE=ROLE',
                           help='mixed queue with an explicit client, router or same role per socket')
            p.add_argument('--release-authority', help='standing acceptance: release each candidate lease')
    args = parser.parse_args(argv)
    if args.command == 'queue' and not args.target and not (args.device and args.to):
        parser.error('queue requires --target DEVICE=ROLE or --device DEVICE with --to ROLE')
    site = load_site(args.site)
    handler = {'plan': cmd_plan, 'publish': cmd_publish, 'run': cmd_run, 'release': cmd_release,
               'status': cmd_status, 'queue': cmd_queue}[args.command]
    try:
        return handler(site, args)
    except ValueError as refusal:  # Refused and every gate's ValueError/IdentityError
        print('REFUSED', type(refusal).__name__, refusal, flush=True)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
