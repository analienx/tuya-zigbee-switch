"""Offline gate for non-PM Client flash recovery evidence; NEVER writes hardware.

The record is an operator attestation plus locally verified file integrity,
NOT a cryptographic proof that a programmer can recover a dead socket.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METHOD = 'TLSR8258-SWire'


def _private_file(value, label):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError(label + ': absolute private file path required')
    path = Path(value).resolve()
    if path.is_relative_to(ROOT) or not path.is_file():
        raise ValueError(label + ': private file missing or inside repo')
    return path


def verify_recovery(profile, *, confirm_unloaded=False, accept_nonrecoverable_ota=False):
    """Reject absent, foreign, untested or unverified board-specific recovery."""
    if profile.get('non_pm') is not True:
        raise ValueError('This recovery gate is strictly for non-PM Clients')
    if (profile.get('manufacturer'), profile.get('model'),
            profile.get('preflash_role'), profile.get('postflash_role')) != (
            'o1jzcxou', 'TS011F-BS', 'EndDevice', 'EndDevice'):
        raise ValueError('Non-PM recovery identity/role mismatch')
    if profile.get('block_bytes') != 32:
        raise ValueError('Unproven non-PM transfer size: pin 32 bytes')
    if confirm_unloaded is not True:
        raise ValueError('Physical load not explicitly confirmed disconnected for THIS flash')
    if accept_nonrecoverable_ota:
        # This opt-in is deliberately locked to the current non-PM canary and
        # one reviewed image. Not transferable to PM, other clients or releases.
        if (profile.get('device'), profile.get('ieee'), profile.get('preflash_build'),
                profile.get('postflash_build'), profile.get('sha256')) != (
                'BedroomSocketCabinetRight', '0xa4c13824a7005afb',
                '1.1.2-bseedcli4', '1.1.2-bseedcli5-rc1',
                '92894009f687976a60a535170581d8ff8daf06b7cc07bb175775ae7b751330dd'):
            raise ValueError('Non-invasive risk acceptance applies only to the signed-off Bedroom non-PM canary')
        if profile.get('require_pm') is not False or profile.get('relay_get_key') != 'state_relay':
            raise ValueError('Non-invasive path refuses PM or unverified relay endpoints')
        if profile.get('expect_relay') != 'OFF' or profile.get('preflash_relay_physical_mode') != 'follow_state':
            raise ValueError('Non-invasive path requires pinned relay OFF and follow_state')
        return {'ieee': profile['ieee'], 'method': 'non-invasive single OTA canary',
                'recovery_available': False, 'warning': 'No guaranteed OTA or physical recovery if boot fails'}
    evidence = _private_file(profile.get('recovery_evidence'), 'Recovery evidence')
    record = json.loads(evidence.read_text(encoding='utf8'))
    if record.get('schema') != 1 or record.get('target_ieee') != profile['ieee']:
        raise ValueError('Recovery record schema/IEEE mismatch')
    if record.get('board') != 'OUTLET_BSEED_TS011F' or record.get('chip') != 'TLSR8258':
        raise ValueError('Recovery board/chip mismatch')
    if record.get('programming_method') != METHOD:
        raise ValueError('Non-PM Client requires tested TLSR8258 SWire recovery')
    if record.get('candidate_sha256') != profile['sha256']:
        raise ValueError('Recovery evidence belongs to a different candidate')
    for field in ('board_inspected', 'mains_isolation_verified',
                  'low_voltage_programming_verified', 'programmer_readback_tested',
                  'backup_restore_procedure_reviewed'):
        if record.get(field) is not True:
            raise ValueError('Recovery operator attestation missing: ' + field)
    backups = record.get('readback_files')
    if not isinstance(backups, list) or len(backups) != 2:
        raise ValueError('Two independent complete flash readbacks are required')
    first, second = [_private_file(item, 'Flash readback') for item in backups]
    if first == second:
        raise ValueError('Readback paths must be independent')
    size = record.get('flash_capacity_bytes')
    if type(size) is not int or size not in (512 * 1024, 1024 * 1024):
        raise ValueError('Exact-board flash capacity must be confirmed and recorded')
    if first.stat().st_size != size or second.stat().st_size != size:
        raise ValueError('Full-flash readback length does not match capacity')
    expected = record.get('backup_sha256')
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError('Recovery readback hash missing')
    image = first.read_bytes()
    for backup in (first, second):
        actual = hashlib.sha256(backup.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError('Two full readbacks must match the recorded SHA-256')
    if not any(image[offset+8:offset+12] == b'KNLT'
               for offset in (0, 0x8000, 0x20000, 0x40000)):
        raise ValueError('Full flash readback has no recognizable Telink boot slot')
    build = profile.get('preflash_build')
    if not isinstance(build, str) or not build or build.encode() not in image:
        raise ValueError('Readback does not contain the pinned installed Client build')
    if b'o1jzcxou;TS011F-BS;' not in image:
        raise ValueError('Readback does not contain the expected non-PM board config')
    return {'ieee': profile['ieee'], 'method': METHOD,
            'readback_sha256': expected, 'flash_capacity_bytes': size,
            'warning': 'File/hash validation plus operator attestation; boot recovery is NOT guaranteed'}
