"""Historical BSEED relay-OFF helpers — DISABLED for every OTA campaign.

This module keeps read-only relay GET, evidence and exact-target ON restoration
for old campaign records. It refuses any OFF command, including direct calls.
Do not treat restored ON as uninterrupted mains continuity.
"""
import datetime as dt
import json
from pathlib import Path
import threading
import time

RECORD = 'RELAY_AUTO_OFF.json'
CUSTOM_RELAY_KEY = 'state_relay'
STOCK_PREFIXES = ('_TZ3000_', '_TZ3002_')
READ_SECONDS = 15
SETTLE_SECONDS = 3
RECORD_MAX_AGE_SECONDS = 900


def enabled(profile):
    value = profile.get('auto_relay_off')
    if value is None or value is False:
        return False
    if value is not True:
        raise ValueError('auto_relay_off must be an explicit Boolean')
    return True


def validate(profile):
    """Reject all auto-OFF profile shapes before OTA planning or runtime."""
    if not enabled(profile):
        return False
    raise ValueError('Automatic relay-OFF is disabled for all new BSEED OTA migrations')

def proof(profile):
    """'meter' when the runner's fresh PM sample proves zero watts, else 'relay_off'."""
    if (profile.get('require_pm') is True and profile.get('non_pm') is not True and
            profile.get('pm_preflash_load_proof') != 'physically_unloaded'):
        return 'meter'
    return 'relay_off'


def verified_off_record(profile, record_file=None, max_age=RECORD_MAX_AGE_SECONDS):
    """True for this target's fresh verified-OFF record; it replaces the human load attestation."""
    path = Path(record_file) if record_file else record_path(profile)
    if not path.is_file():
        return False
    record = json.loads(path.read_text(encoding='utf8'))
    if (record.get('device'), record.get('ieee')) != (profile['device'], profile['ieee']):
        return False
    stamp = record.get('verified_off_at')
    if not isinstance(stamp, str):
        return False
    age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(stamp)).total_seconds()
    return -60 <= age <= max_age


def preflash_key(profile):
    return profile.get('relay_get_key') or 'state'


def postflash_key(profile):
    return CUSTOM_RELAY_KEY


def record_path(profile):
    return Path(profile['workdir']) / RECORD


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


class RelayClient:
    """Minimal MQTT client: one target, its own topic, exact IEEE in every answer."""

    def __init__(self, profile):
        import paho.mqtt.client as mqtt
        import yaml
        config = yaml.safe_load(Path(profile['mqtt_config']).read_text(encoding='utf8'))['mqtt']
        self.topic = config.get('base_topic', 'zigbee2mqtt') + '/' + profile['device']
        self.ieee = profile['ieee']
        self.messages, self.changed = [], threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='bseed-relay-auto-off')
        self.client.username_pw_set(config.get('user', ''), config.get('password', ''))
        self.client.on_message = self._message
        self.client.connect(profile['broker'], 1883, 10)
        self.client.subscribe(self.topic, 1)
        self.client.loop_start()

    def _message(self, _client, _userdata, message):
        try:
            data = json.loads(message.payload)
        except ValueError:
            return
        if (data.get('device') or {}).get('ieeeAddr') == self.ieee:
            self.messages.append((time.monotonic(), data))
            self.changed.set()

    def read(self, key, seconds=READ_SECONDS):
        """Answer to an explicit GET, taken as the LAST value published in a short
        settle window after the first answer, so an unrelated report carrying the
        cached value just before the read's own answer cannot decide it."""
        started = time.monotonic()
        self.client.publish(self.topic + '/get', json.dumps({key: ''}), qos=1).wait_for_publish(5)
        deadline, first = started + seconds, None
        while time.monotonic() < deadline:
            answers = [(at, data[key]) for at, data in self.messages
                       if at > started and data.get(key) in ('ON', 'OFF')]
            if answers and first is None:
                first = answers[0][0]
            if first is not None and time.monotonic() >= first + SETTLE_SECONDS:
                return answers[-1][1]
            self.changed.wait(0.3)
            self.changed.clear()
        raise TimeoutError('No fresh %s answer from the target' % key)

    def set(self, key, value, attempts=3):
        """Only target-specific ON restoration is allowed; OFF is never sent."""
        if value != 'ON' or key not in ('state', 'state_relay'):
            raise ValueError('Relay-OFF command is forbidden during BSEED OTA')
        observed = None
        for _ in range(attempts):
            self.client.publish(self.topic + '/set', json.dumps({key: value}), qos=1).wait_for_publish(5)
            time.sleep(1.5)
            observed = self.read(key)
            if observed == value:
                return observed
        raise ValueError('Relay did not switch %s (read back %s)' % (value, observed))

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()


def _write(profile, record):
    path = record_path(profile)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf8')


def switch_off_before_flash(profile, client=None):
    """No BSEED OTA path may intentionally cut relay power."""
    raise ValueError('Automatic relay-OFF is prohibited by the BSEED migration policy')

def reassert_off_after_rejoin(profile, client=None):
    """Never force a post-reboot relay OFF."""
    raise ValueError('Relay-OFF after rejoin is prohibited by the BSEED migration policy')

def restore_after_acceptance(profile, client=None):
    """Return the relay to its pre-flash state; only after postflash_candidate."""
    path = record_path(profile)
    if not path.is_file():
        return None
    record = json.loads(path.read_text(encoding='utf8'))
    if record.get('restored'):
        return record
    if record.get('preflash') != 'ON':
        record.update(restored=True, restored_at=now())
        _write(profile, record)
        return record
    own = client is None
    client = client or RelayClient(profile)
    try:
        client.set(postflash_key(profile), 'ON')
        record.update(restored=True, restored_at=now())
        _write(profile, record)
        print('RELAY_RESTORED', 'ON', flush=True)
        return record
    finally:
        if own:
            client.close()
