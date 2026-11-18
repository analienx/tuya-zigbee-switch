"""Mandatory live check: the DEPLOYED Zigbee2MQTT converter must recognise the PM target build.

A converter that does not recognise the installed BSEED PM build decodes its
metering with generic scaling and never stamps fresh PM samples, so PM
provisioning and postflash can never pass (2026-11-18: WRSocketWindowRightR
reached cli26 while the deployed converter still listed builds only up to
cli25/r22). The check reads the live file over the campaign's PM SSH access
before any firmware write, and again before provisioning and postflash, and
keeps a private copy in the workdir for the postflash verifier.

Converters from the family-rule release onward accept every future
``1.2.5-bseed(r|cli)N`` build, so the converter only needs redeploying when the
firmware's PM scale contract changes (which must leave that family).
"""
import hashlib
import json
from pathlib import Path

from bseed_z2m_state_refresh import converter_accepts_build

DEFAULT_CONVERTER_PATH = '/config/zigbee2mqtt/external_converters/switch_custom.js'
COPY_NAME = 'DEPLOYED_CONVERTER.js'
RECORD_NAME = 'DEPLOYED_CONVERTER.json'
GATED_MODES = ('preflight', 'check', 'flash', 'transition', 'rejoin', 'provision-pm', 'postflash')


def fetch_deployed_converter(host, username, key, path):
    """Read the live converter text over host-key-verified SFTP (read-only)."""
    import paramiko
    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys(str(Path.home() / '.ssh' / 'known_hosts'))
    ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
    ssh.connect(host, username=username, key_filename=str(key), timeout=7,
                auth_timeout=7, banner_timeout=7)
    try:
        sftp = ssh.open_sftp()
        try:
            with sftp.open(path, 'r') as handle:
                return handle.read().decode('utf8')
        finally:
            sftp.close()
    finally:
        ssh.close()


def required(profile, mode):
    return profile.get('require_pm') is True and mode in GATED_MODES


def require_deployed_converter(profile):
    """Fail closed unless the live converter applies the fixed PM scales to postflash_build."""
    build = profile.get('postflash_build')
    if not profile.get('pm_ssh_host') or not profile.get('pm_ssh_key'):
        raise ValueError('PM campaign requires pm_ssh_host and pm_ssh_key to verify the deployed converter')
    path = profile.get('z2m_converter_path') or DEFAULT_CONVERTER_PATH
    try:
        text = fetch_deployed_converter(profile['pm_ssh_host'], profile.get('pm_ssh_user', 'root'),
                                        profile['pm_ssh_key'], path)
    except Exception as error:  # noqa: BLE001 - any read failure must stop the campaign
        raise ValueError('Cannot read the deployed Zigbee2MQTT converter: %r' % (error,)) from error
    accepted = converter_accepts_build(text, build)
    work = Path(profile['workdir'])
    work.mkdir(parents=True, exist_ok=True)
    (work / COPY_NAME).write_text(text, encoding='utf8')
    record = {'path': path, 'sha256': hashlib.sha256(text.encode('utf8')).hexdigest(),
              'build': build, 'accepted': accepted}
    (work / RECORD_NAME).write_text(json.dumps(record, indent=2) + '\n', encoding='utf8')
    if not accepted:
        raise ValueError('Deployed Zigbee2MQTT converter does not recognise %s: deploy the CI-built '
                         'converter (z2m-converter-canary artifact) and restart Zigbee2MQTT first' % build)
    print('DEPLOYED_CONVERTER_OK', json.dumps(record, sort_keys=True), flush=True)
    return record


def converter_copy(profile):
    """The postflash verifier's --converter: explicit profile value, else the gate's fresh copy."""
    if profile.get('deployed_converter'):
        return str(profile['deployed_converter'])
    copy = Path(profile['workdir']) / COPY_NAME
    return str(copy) if profile.get('require_pm') is True and copy.is_file() else None
