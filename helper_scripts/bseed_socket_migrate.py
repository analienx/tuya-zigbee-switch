"""One-command BSEED socket migration: plan -> publish -> run -> release.

    set BSEED_SITE=<private site.json>        (or pass --site)
    python helper_scripts/bseed_socket_migrate.py plan    --device NAME --to client|router|same
    python helper_scripts/bseed_socket_migrate.py publish --device NAME
    python helper_scripts/bseed_socket_migrate.py run     --device NAME
    python helper_scripts/bseed_socket_migrate.py release --device NAME --authority "<owner's words>"
    python helper_scripts/bseed_socket_migrate.py queue   --device A --device B --to client [--release-authority "..."]
    python helper_scripts/bseed_socket_migrate.py status  --device NAME

``plan`` is read-only on the network: it reads the live inventory and the
target's neighbour table, picks the route and the sealed candidate, writes the
private workdir (profile, image or header-only wrapper, template index) and
runs the same load_profile / version-policy / relay / recovery gates the
campaign runs, so every refusal surfaces before anything is published.

``run`` waits for the shared network lease, then runs the canonical campaign
(``transition`` for a role change, ``flash`` otherwise) with a durable log.
Automatic relay OFF is always on: the relay is switched OFF and verified
before the flash and switched back ON only after ``postflash_candidate``.
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
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

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
        raise Refused('No Router neighbour to scope the rejoin window to')
    lqi, _custom, name = max(routers)
    return name, lqi, sorted(children)


def build_profile(site, live, route, candidate, *, image, sha256, native_image=None,
                  native_sha256=None, join_via=None, workdir, folder, preflash_file_version=None):
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
        'auto_relay_off': True, 'expect_relay': 'OFF', 'force_test_transition': False,
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
        p.update(join_mode='network-wide', join_seconds=120, join_total_seconds=1200,
                 allow_join_all_fallback=False, source_rejoin_strategy='scoped')
        if join_via:
            p['join_via'] = join_via  # informational: strongest Router neighbour at plan time
    pm = route['board'] == CUSTOM_PM
    if pm:
        p.update(require_pm=True, non_pm=False, pm_ssh_host=site['pm_ssh_host'],
                 pm_ssh_key=site['pm_ssh_key'], pm_max_writes=4, pm_observe_seconds=420,
                 pm_expected_idle=True)
    else:
        p.update(require_pm=False, non_pm=True, preflash_relay_physical_mode='follow_state')
    if route['kind'] == 'stock_pm_to_client':
        p.update(stock_client_conversion=True, pm_preflash_load_proof='physically_unloaded',
                 relay_get_key='state', postflash_manufacturer=CUSTOM_PM[0],
                 postflash_model=CUSTOM_PM[1], pm_allow_configure=True)
    else:
        p.update(preflash_build=live['software_build_id'], relay_get_key='state_relay')
        if pm:
            p.update(pm_preflash_load_proof='meter', pm_allow_configure=role_change)
    if route['kind'] == 'pm_role_change':
        p.update(force_test_transition=True, preflash_query_image_type=route['wrapper_type'])
        if preflash_file_version is not None:
            p['preflash_file_version'] = '0x%08X' % preflash_file_version
    if route['target'] == 'Router' and pm and not role_change:
        # Same-role PM Router flashes need private build-matrix evidence and
        # this device's settings baseline (site.json devices.<name>).
        device = (site.get('devices') or {}).get(live['friendly_name']) or {}
        for key in ('pm_router_matrix_evidence', 'pm_settings_baseline'):
            if device.get(key) or site.get(key):
                p[key] = device.get(key) or site.get(key)
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
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='bseed-migrate-inv')
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
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='bseed-migrate-ver')
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
        neighbors = read_neighbor_table(site['mqtt_config'], site['broker'], live['ieee_address'],
                                        live['network_address'])
        try:
            join_via, lqi, children = choose_join_via(neighbors, devices, live['ieee_address'])
            print('STRONGEST_ROUTER_NEIGHBOUR', join_via, 'lqi', lqi)
        except Refused as error:
            print('NO_ROUTER_NEIGHBOUR', error)  # the network-wide rejoin does not need one
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
    if route['kind'] == 'pm_role_change':
        file_version = installed_file_version(site, args.device, 'state_relay')
    profile = build_profile(site, live, route, candidate, image=image, sha256=sha,
                            native_image=native_path, native_sha256=candidate['otaSha256'],
                            join_via=join_via, workdir=work, folder=folder,
                            preflash_file_version=file_version)
    manufacturer_name = live['manufacturer']
    (work / 'template_index.json').write_text(
        json.dumps(template_index(profile, transport, manufacturer_name), indent=2) + '\n', encoding='utf8')
    profile_path = work / 'profile.json'
    profile_path.write_text(json.dumps(profile, indent=2, sort_keys=True) + '\n', encoding='utf8')
    (Path(site['campaign_root']) / args.device / 'CURRENT').write_text(str(work), encoding='utf8')
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
    print('PLANNED', json.dumps(state, default=str))
    return 0


def cmd_publish(site, args):
    work = workdir_for(site, args.device)
    profile = json.loads((work / 'profile.json').read_text(encoding='utf8'))
    if campaign(work / 'profile.json', 'prepare'):
        raise SystemExit('prepare failed')
    folder = Path(profile['image']).stem
    remote = site['publish']['remote_dir'].rstrip('/') + '/' + folder
    alias = site['publish']['ssh_alias']
    for cmd in (['ssh', '-o', 'BatchMode=yes', alias, 'mkdir -p ' + remote],
                ['scp', '-q', '-o', 'BatchMode=yes', profile['image'], alias + ':' + remote + '/firmware.ota'],
                ['scp', '-q', '-o', 'BatchMode=yes', profile['index_output'], alias + ':' + remote + '/index.json']):
        if subprocess.call(cmd):
            raise SystemExit('publish step failed: ' + ' '.join(cmd[:2]))
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


def cmd_run(site, args):
    from bseed_ota_resume_supervisor import ota_progress_history
    work = workdir_for(site, args.device)
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


def cmd_release(site, args):
    work = workdir_for(site, args.device)
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


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def main(argv=None):
    sys.path.insert(0, str(ROOT / 'helper_scripts'))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--site', help='private site.json (default: $BSEED_SITE)')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('plan', 'publish', 'run', 'release', 'status', 'queue'):
        p = sub.add_parser(name)
        p.add_argument('--device', required=True, action='append' if name == 'queue' else 'store')
        if name in ('plan', 'queue'):
            p.add_argument('--to', choices=('client', 'router', 'same'), required=True)
            p.add_argument('--replan', action='store_true', help='rewrite an unflashed plan')
        if name in ('run', 'queue'):
            p.add_argument('--max-resumes', type=int, default=3)
            p.add_argument('--lease-wait-seconds', type=int, default=4 * 3600)
        if name == 'release':
            p.add_argument('--authority', required=True, help="owner's acceptance words")
        if name == 'queue':
            p.add_argument('--release-authority', help='standing acceptance: release each candidate lease')
    args = parser.parse_args(argv)
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
