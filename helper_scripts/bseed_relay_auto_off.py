"""Automatic relay OFF before a flash and restore after acceptance (replaces "nothing plugged in").

With ``"auto_relay_off": true`` a ``flash``/``transition`` campaign:

1. reads the relay afresh and records its pre-flash state once, in the private
   workdir (``RELAY_AUTO_OFF.json``; a resumed campaign keeps the first record);
2. switches it OFF if it was ON and verifies OFF by a fresh read of the same IEEE;
3. lets the runner's normal PM meter preflight prove the outlet is unpowered
   (fresh converter-stamped power at or below ``max_reported_watts``), so no
   human load attestation is needed;
4. after a role change re-asserts OFF on the new firmware as soon as the device
   has rejoined (a stock-to-custom first boot has no saved relay state);
5. switches the relay back ON only after ``postflash_candidate``.

A failed or unverified update leaves the relay OFF: that is the safe state.
Only the target's own relay key is ever written; no group or broadcast command.
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


def enabled(profile):
    value = profile.get('auto_relay_off')
    if value is None or value is False:
        return False
    if value is not True:
        raise ValueError('auto_relay_off must be an explicit Boolean')
    return True


def validate(profile):
    """Profile shape for the automatic route; called from load_profile."""
    if not enabled(profile):
        return False
    if profile.get('require_pm') is not True or profile.get('non_pm') is True:
        raise ValueError('auto_relay_off needs a PM board: the meter must prove the outlet is unpowered')
    if profile.get('pm_preflash_load_proof') == 'physically_unloaded':
        raise ValueError('auto_relay_off replaces the physical unplugged proof; use the meter route')
    if profile.get('expect_relay') != 'OFF':
        raise ValueError('auto_relay_off requires expect_relay=OFF (the state the flash runs in)')
    if profile.get('manufacturer', '').startswith(STOCK_PREFIXES):
        # Stock converters never stamp a fresh PM sample, so the meter route
        # cannot prove zero watts there.
        raise ValueError('auto_relay_off needs custom PM firmware before the flash')
    return True


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
        """Fresh answer to an explicit GET; cached retained state never counts."""
        started = time.monotonic()
        self.client.publish(self.topic + '/get', json.dumps({key: ''}), qos=1).wait_for_publish(5)
        deadline = started + seconds
        while time.monotonic() < deadline:
            for at, data in reversed(self.messages):
                if at > started and data.get(key) in ('ON', 'OFF'):
                    return data[key]
            self.changed.wait(0.3)
            self.changed.clear()
        raise TimeoutError('No fresh %s answer from the target' % key)

    def set(self, key, value):
        self.client.publish(self.topic + '/set', json.dumps({key: value}), qos=1).wait_for_publish(5)
        time.sleep(1.5)
        observed = self.read(key)
        if observed != value:
            raise ValueError('Relay did not switch %s (read back %s)' % (value, observed))
        return observed

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()


def _write(profile, record):
    path = record_path(profile)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf8')


def switch_off_before_flash(profile, client=None):
    """Record the pre-flash state once, then leave the relay verified OFF."""
    path = record_path(profile)
    own = client is None
    client = client or RelayClient(profile)
    try:
        key = preflash_key(profile)
        if path.is_file():
            record = json.loads(path.read_text(encoding='utf8'))
            if (record.get('ieee'), record.get('device')) != (profile['ieee'], profile['device']):
                raise ValueError('Relay record belongs to a different target')
        else:
            record = {'device': profile['device'], 'ieee': profile['ieee'], 'key': key,
                      'preflash': client.read(key), 'recorded_at': now(), 'restored': False}
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf8') as handle:  # never overwrite the first truth
                handle.write(json.dumps(record, indent=2, sort_keys=True) + '\n')
        if client.read(key) != 'OFF':
            client.set(key, 'OFF')
            record['switched_off_at'] = now()
            _write(profile, record)
        print('RELAY_AUTO_OFF', json.dumps({'preflash': record['preflash'], 'now': 'OFF'}), flush=True)
        return record
    finally:
        if own:
            client.close()


def reassert_off_after_rejoin(profile, client=None):
    """A stock-to-custom first boot has no saved relay state: force OFF until acceptance."""
    if not record_path(profile).is_file():
        return None
    own = client is None
    client = client or RelayClient(profile)
    try:
        key = postflash_key(profile)
        if client.read(key) != 'OFF':
            client.set(key, 'OFF')
            print('RELAY_REASSERTED_OFF_AFTER_REJOIN', flush=True)
        return 'OFF'
    finally:
        if own:
            client.close()


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
