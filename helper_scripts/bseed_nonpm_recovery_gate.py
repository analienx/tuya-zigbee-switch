"""Offline gate for non-PM Client flash recovery evidence; NEVER writes hardware.

The record is an operator attestation plus locally verified file integrity,
NOT a cryptographic proof that a programmer can recover a dead socket.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METHOD = 'TLSR8258-SWire'
BEDROOM_DEVICE = 'BedroomSocketCabinetRight'
BEDROOM_IEEE = '0xa4c13824a7005afb'
C7_BUILD = '1.1.3-bseedc7'
C7_SHA256 = 'f0a499ea9e351265cb47fa26717f00246450cecaf727adad3298552bb1d8a94f'
C9_BUILD = '1.1.3-bseedc9'
C9_SHA256 = 'e52701b83ea7528ed0e9b26cb9e4da0679d125c259c8141e62207c6460738fff'
# User-authorized 2026-11-03: explicit irreversible-loss risk accepted for this
# exact SAME-ROLE canary. This is NOT a recovery method or fleet approval.
# Preserve image digest, IEEE, board, role, 32B transfer, relay baseline and
# freshly unloaded confirmation; do not re-prompt for this exact grant.
C16_BUILD = '1.1.3-bseedc16'
C16_SHA256 = 'd4057825661eb4a6c943ca4f8a5b02d7c709acdb960e4102fd5a185e4f87e321'
R10_BUILD = '1.1.3-bseedr10'
R10_SHA256 = 'c2bb21dee350fd375586029eefb03f85791b0941bd386882a0b8653bb15bdb96'


# User-authorized 2026-11-18 ("Dont ask about accepting risks again and go
# ahead", answering the explicit question whether irreversible brick risk is
# accepted for WRSocketEntrance r19 -> r20 without SWire readbacks): ONE exact
# same-role Router OTA on this IEEE with the sealed 2a5b0d08 r20 image. It is
# not a recovery method, not fleet approval and not hardware acceptance. The
# load must still be proven off for this flash (human attestation or the
# campaign's fresh verified relay-OFF record).
ENTRANCE_R20 = {
    'device': 'WRSocketEntrance', 'ieee': '0xa4c1387b13e9436d',
    'manufacturer': 'o1jzcxou', 'model': 'TS011F-BS',
    'preflash_build': '1.1.3-bseedr19', 'postflash_build': '1.1.3-bseedr20',
    'sha256': 'c3d4f3076710e390c485f5b475aa3a6ca51f444ab8fbac8a32b510f2238653a6',
    'preflash_relay_physical_mode': 'follow_state', 'expect_relay': 'OFF',
    'relay_get_key': 'state_relay',
}


def is_exact_entrance_r20_operator_risk(profile, roles, transition):
    return (not transition and roles == ('Router', 'Router') and
            all(profile.get(key) == value for key, value in ENTRANCE_R20.items()) and
            profile.get('require_pm') is False and
            profile.get('force_test_transition') is False and
            not profile.get('parallel_ota_max') and not profile.get('concurrent_ota_ieees'))


# 20 November 2026: the owner reconfirmed the two-device parallel OTA
# experiment. Earlier fleet-wide irreversible-loss risk acknowledgement is
# narrowed here to these TWO exact non-PM firmware artifacts and target IEEEs.
# This is not a recovery mechanism or approval for another board or release.
# The campaign must obtain a NEW verified relay-OFF record before OTA.
PARALLEL_20261120_CANDIDATES = (
    {
        'device': 'WRSocketEntrance', 'ieee': '0xa4c1387b13e9436d',
        'preflash_role': 'Router', 'postflash_role': 'Router',
        'preflash_build': '1.1.3-bseedr19',
        'postflash_build': '1.1.3-bseedr21',
        'sha256': 'b9745a9282e8063005e75b00df8c76053bf04aaf033619f7ef06841255abfdcd',
        'concurrent_ota_ieees': ['0xa4c13824a7005afb'],
    },
    {
        'device': 'BedroomSocketCabinetRight',
        'ieee': '0xa4c13824a7005afb',
        'preflash_role': 'EndDevice', 'postflash_role': 'EndDevice',
        'preflash_build': '1.1.3-bseedc17',
        'postflash_build': '1.1.3-bseedc19',
        'sha256': 'a551668210ef91d9b43788e1a2dc281f691a6ca47b016b9b7f4a68cc1449ad63',
        'concurrent_ota_ieees': ['0xa4c1387b13e9436d'],
    },
)
PARALLEL_20261120_COMMON = {
    'manufacturer': 'o1jzcxou', 'model': 'TS011F-BS',
    'preflash_relay_physical_mode': 'follow_state',
    'relay_get_key': 'state_relay', 'expect_relay': 'OFF',
    'require_pm': False, 'non_pm': True,
    'force_test_transition': False,
    'parallel_ota_max': 2, 'network_lock_shared': True,
    'block_bytes': 32, 'response_delay_ms': 1800,
    'source_rejoin_strategy': 'none', 'allow_join_all_fallback': False,
    'auto_relay_off': True, 'owner_irreversible_risk_accepted': True,
}


def is_exact_parallel_owner_risk_20261120(profile, roles, transition):
    """ONLY reciprocal sealed no-readback canaries, never generic permission."""
    if transition or not all(profile.get(k) == v
                             for k, v in PARALLEL_20261120_COMMON.items()):
        return False
    return any(
        roles == (candidate['preflash_role'], candidate['postflash_role'])
        and all(profile.get(key) == value for key, value in candidate.items())
        for candidate in PARALLEL_20261120_CANDIDATES
    )


def _private_file(value, label):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError(label + ': absolute private file path required')
    path = Path(value).resolve()
    if path.is_relative_to(ROOT) or not path.is_file():
        raise ValueError(label + ': private file missing or inside repo')
    return path


def verify_recovery(profile, *, confirm_unloaded=False):
    """Verify a same-role non-PM Client or Router without relaxing recovery proof.

    The previously hard-coded EndDevice→EndDevice default unintentionally
    rejected genuine Router→Router upgrades before their device-specific
    readback and physical safety evidence could be validated.
    """
    roles = (profile.get('preflash_role'), profile.get('postflash_role'))
    if roles not in (('EndDevice', 'EndDevice'), ('Router', 'Router')):
        raise ValueError('Same-role non-PM flash role mismatch: requires unchanged Router or EndDevice role')
    return _verify_recovery(profile, confirm_unloaded=confirm_unloaded, roles=roles)


def verify_transition_recovery(profile, *, confirm_unloaded=False):
    roles = (profile.get('preflash_role'), profile.get('postflash_role'))
    if roles not in (('Router', 'EndDevice'), ('EndDevice', 'Router')):
        raise ValueError('Non-PM transition requires distinct supported source and target roles')
    return _verify_recovery(profile, confirm_unloaded=confirm_unloaded,
                            roles=roles, transition=True)


def _verify_recovery(profile, *, confirm_unloaded=False,
                     roles=('EndDevice', 'EndDevice'), transition=False):
    """Reject absent, foreign, untested or unverified board-specific recovery."""
    if profile.get('non_pm') is not True:
        raise ValueError('This recovery gate is strictly for non-PM Clients')
    if (profile.get('manufacturer'), profile.get('model'),
            profile.get('preflash_role'), profile.get('postflash_role')) != (
            'o1jzcxou', 'TS011F-BS', *roles):
        raise ValueError('Non-PM recovery identity/role mismatch')
    if profile.get('block_bytes') not in (32, 48) or isinstance(profile.get('block_bytes'), bool):
        raise ValueError('Unproven non-PM transfer size: pin 32 bytes, or 48 bytes with paced responses')
    from bseed_ota_block_policy import check_block_bytes
    check_block_bytes(profile, allowed=(32, 48))
    from bseed_operator_load_policy import connected_load_authorized
    if confirm_unloaded is not True and not connected_load_authorized(profile):
        raise ValueError('Non-PM OTA needs preserve-state owner risk authorization or explicit unloaded proof')
    if (not profile.get('recovery_evidence') and
            is_exact_parallel_owner_risk_20261120(profile, roles, transition)):
        # The canonical runner must independently verify fresh relay OFF before
        # treating auto_relay_off as load proof. This shape check alone does not.
        return {'ieee': profile['ieee'],
                'method': 'exact two-slot owner-risk canary (relay-OFF proof required)',
                'recovery_available': False, 'readback_verified': False,
                'operator_risk_accepted': True, 'hardware_acceptance': False}
    if (profile.get('device'), profile.get('ieee')) == (BEDROOM_DEVICE, BEDROOM_IEEE):
        # The exact Bedroom authorizations were approved at 32-byte blocks.
        if profile.get('block_bytes') != 32:
            raise ValueError('Exact Bedroom non-invasive authorization is pinned to 32-byte blocks')
        # Exact-device candidates only. Legacy rc1/rc2 remain pinned to their exact
        # hashes. The consolidated c7/r10 pair is allowed only on the same
        # Bedroom canary and only when the exact sealed native destination is
        # proven; FORCE wrapper payload equality is verified separately by the
        # canonical OTA runner before any bytes are submitted.
        legacy = (profile.get('preflash_build'), profile.get('postflash_build'), profile.get('sha256')) in (
            ('1.1.2-bseedcli4', '1.1.2-bseedcli5-rc1',
             '92894009f687976a60a535170581d8ff8daf06b7cc07bb175775ae7b751330dd'),
            ('1.1.2-bseedcli4', '1.1.2-bseedcli5-rc2',
             'e6fb2cca2a244a42ab5e8da166ed35ec438434220a46c89a37bc98086c326d1b'))
        consolidated_same = (not transition and roles == ('EndDevice', 'EndDevice') and
            (profile.get('preflash_build'), profile.get('postflash_build'),
             profile.get('sha256')) in (
                ('1.1.2-bseedcli4', C7_BUILD, C7_SHA256),
                (C7_BUILD, C9_BUILD, C9_SHA256),
                (C9_BUILD, C16_BUILD, C16_SHA256)))
        consolidated_transition = (transition and profile.get('force_test_transition') is True and (
            (roles == ('EndDevice', 'Router') and profile.get('preflash_build') == C7_BUILD and
             profile.get('postflash_build') == R10_BUILD and profile.get('native_sha256') == R10_SHA256) or
            (roles == ('Router', 'EndDevice') and profile.get('preflash_build') == R10_BUILD and
             profile.get('postflash_build') == C7_BUILD and profile.get('native_sha256') == C7_SHA256)))
        if not (legacy or consolidated_same or consolidated_transition):
            raise ValueError('Non-invasive path applies only to the exact signed-off Bedroom non-PM candidates')
        if profile.get('require_pm') is not False or profile.get('relay_get_key') != 'state_relay':
            raise ValueError('Non-invasive path refuses PM or unverified relay endpoints')
        if profile.get('preflash_relay_physical_mode') != 'follow_state':
            raise ValueError('Non-invasive path requires pinned follow_state relay policy')
        if legacy and profile.get('expect_relay') != 'OFF':
            raise ValueError('Legacy non-invasive canary requires pinned relay OFF')
        if not legacy and profile.get('expect_relay') not in ('ON', 'OFF'):
            raise ValueError('Consolidated non-PM canary requires an explicit relay baseline')
        return {'ieee': profile['ieee'], 'method': ('non-invasive single OTA canary' if legacy else 'non-invasive exact Bedroom canary'),
                'recovery_available': False}
    from bseed_nonpm_relay_policy import is_exact_balcony_router_candidate
    if (not profile.get('recovery_evidence')
            and is_exact_balcony_router_candidate(profile)):
        from bseed_nonpm_relay_policy import (
            BALCONY_LEGACY_POLICY, verify_relay_policy_profile,
        )
        if not (roles == ('Router', 'Router') and not transition and
                profile.get('preflash_relay_physical_mode') == BALCONY_LEGACY_POLICY):
            raise ValueError('Balcony non-invasive authorization is exact Router v8 to r18 only')
        if profile.get('block_bytes') != 32:
            raise ValueError('Exact Balcony non-invasive authorization is pinned to 32-byte blocks')
        verify_relay_policy_profile(profile)
        return {'ieee': profile['ieee'],
                'method': 'non-invasive exact Balcony Router v8-to-r18 canary',
                'recovery_available': False}
    from bseed_nonpm_relay_policy import (
        is_exact_entrance_router_candidate, exact_same_role_nonpm_router_admission,
    )
    if not profile.get('recovery_evidence') and is_exact_entrance_router_candidate(profile):
        if not (roles == ('Router', 'Router') and not transition and
                exact_same_role_nonpm_router_admission(profile) and
                profile.get('preflash_relay_physical_mode') == 'follow_state'):
            raise ValueError('Entrance non-invasive authorization requires exact Router v8 to r18')
        return {'ieee': profile['ieee'],
                'method': 'non-invasive exact Entrance Router v8-to-r18 canary',
                'recovery_available': False}
    if not profile.get('recovery_evidence') and is_exact_entrance_r20_operator_risk(profile, roles, transition):
        return {'ieee': profile['ieee'],
                'method': 'explicit same-role r19-to-r20 operator risk canary',
                'recovery_available': False, 'readback_verified': False,
                'operator_risk_accepted': True, 'hardware_acceptance': False,
                'warning': 'No full chip readback. Loss of socket recoverability is possible.'}
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
