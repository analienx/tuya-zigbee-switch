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
C17_BUILD = '1.1.3-bseedc17'
C17_SHA256 = '3ae273707a657b79a8bd2b7f6f77794d57547ba3a52247627b4a7c335f932b08'
R10_BUILD = '1.1.3-bseedr10'
R10_SHA256 = 'c2bb21dee350fd375586029eefb03f85791b0941bd386882a0b8653bb15bdb96'


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
    if profile.get('block_bytes') != 32:
        raise ValueError('Unproven non-PM transfer size: pin 32 bytes')
    if confirm_unloaded is not True:
        raise ValueError('Physical load not explicitly confirmed disconnected for THIS flash')
    if (profile.get('device'), profile.get('ieee')) == (BEDROOM_DEVICE, BEDROOM_IEEE):
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
                (C9_BUILD, C16_BUILD, C16_SHA256),
                (C16_BUILD, C17_BUILD, C17_SHA256)))
        consolidated_transition = (transition and profile.get('force_test_transition') is True and (
            (roles == ('EndDevice', 'Router') and profile.get('preflash_build') == C7_BUILD and
             profile.get('postflash_build') == R10_BUILD and profile.get('native_sha256') == R10_SHA256) or
            (roles == ('Router', 'EndDevice') and profile.get('preflash_build') == R10_BUILD and
             profile.get('postflash_build') == C7_BUILD and profile.get('native_sha256') == C7_SHA256)))
        c17_dual_operator_canary = (
            not transition and roles == ('EndDevice', 'EndDevice') and
            (profile.get('preflash_build'), profile.get('postflash_build'),
             profile.get('sha256')) == (C16_BUILD, C17_BUILD, C17_SHA256))
        if c17_dual_operator_canary:
            # The prior c9->c16 no-SWire exception is NOT transferable.
            # Authorization for this exact image is separately limited to the
            # user's Nov 15 opt-in two-device pilot, with unloaded-device
            # confirmation still mandatory. Never imply verified recovery.
            if not (
                    profile.get('parallel_ota_max') == 2 and
                    profile.get('concurrent_ota_ieees') == ['0xa4c138ac993cc245'] and
                    profile.get('source_rejoin_strategy') == 'none' and
                    profile.get('force_test_transition') is False and
                    profile.get('image_type') == 65026 and
                    str(profile.get('file_version')).lower() == '0x1102301e' and
                    profile.get('block_bytes') == 32 and
                    type(profile.get('response_delay_ms')) is int and
                    profile.get('response_delay_ms') >= 1800 and
                    profile.get('preflash_relay_physical_mode') == 'follow_state' and
                    profile.get('expect_relay') == 'OFF'):
                raise ValueError('c16-to-c17 irreversible-risk exception is pinned to the exact dual-bedroom pilot')
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
        return {'ieee': profile['ieee'],
                'method': ('non-invasive exact c16-to-c17 dual OTA canary'
                           if c17_dual_operator_canary else
                           'non-invasive single OTA canary' if legacy
                           else 'non-invasive exact Bedroom canary'),
                'recovery_available': False,
                'readback_verified': False,
                'operator_risk_accepted': bool(c17_dual_operator_canary),
                'hardware_acceptance': False,
                'warning': ('No verified SWire image or tested chip recovery; a failed OTA can make the socket unrecoverable without wired programming'
                            if c17_dual_operator_canary else 'No verified SWire backup')}
    from bseed_nonpm_relay_policy import is_exact_balcony_router_candidate
    if (not profile.get('recovery_evidence')
            and is_exact_balcony_router_candidate(profile)):
        from bseed_nonpm_relay_policy import (
            BALCONY_LEGACY_POLICY, verify_relay_policy_profile,
        )
        if not (roles == ('Router', 'Router') and not transition and
                profile.get('preflash_relay_physical_mode') == BALCONY_LEGACY_POLICY):
            raise ValueError('Balcony non-invasive authorization is exact Router v8 to r18 only')
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
    # Operator-authorized, one-time exact canary: after explicit direction on
    # 2026-11-14 to initiate the two workroom flashes despite the disclosed
    # missing SWire readback, accept irreversible brick risk ONLY for this
    # precise already-custom, same-role Router r18 -> r19 pair.
    # This is not a claim that a programmer has ever read/recovered this chip.
    if (not transition and roles == ('Router', 'Router') and
            not profile.get('recovery_evidence') and
            (profile.get('device'), profile.get('ieee'),
             profile.get('preflash_build'), profile.get('postflash_build'),
             profile.get('sha256')) == (
                'WRSocketEntrance', '0xa4c1387b13e9436d',
                '1.1.3-bseedr18', '1.1.3-bseedr19',
                '3a85623ab51e535b38384e41bedc13cf961721e36b62bb462419ce1b318b5f71') and
            profile.get('parallel_ota_max') == 2 and
            profile.get('concurrent_ota_ieees') == ['0xa4c138c5f07ee732'] and
            profile.get('source_rejoin_strategy') == 'none' and
            profile.get('block_bytes') == 32 and
            profile.get('response_delay_ms', 0) >= 1800 and
            profile.get('preflash_relay_physical_mode') == 'follow_state' and
            profile.get('expect_relay') == 'OFF' and
            profile.get('relay_get_key') == 'state_relay' and
            profile.get('require_pm') is False and
            profile.get('force_test_transition') is False):
        return {
            'ieee': profile['ieee'],
            'method': 'explicit same-role r18-to-r19 non-invasive operator risk canary',
            'recovery_available': False,
            'readback_verified': False,
            'operator_risk_accepted': True,
            'hardware_acceptance': False,
            'warning': 'No full chip readback. Loss of socket recoverability is possible.',
        }
    if transition and roles == ('EndDevice', 'Router') and (
            profile.get('balcony_cli2_to_r18_transition') is True):
        from bseed_balcony_cli2_to_r18_transition import exact_transition
        # This is an explicit irreversible-loss operator waiver for the
        # corrected cli2->r18 source/target only, NOT SWire backup evidence.
        # Physically unloaded target was already required above.
        return exact_transition(profile)
    if (not transition and roles == ('EndDevice', 'EndDevice') and
            profile.get('balcony_legacy_cli2_to_c17') is True):
        # Exact same-role client upgrade on previously user-approved/unloaded
        # Balcony. Do not make up SWire recovery or two full-chip backups.
        from bseed_balcony_cli2_to_c17 import verify_exact
        proof = verify_exact(profile)
        return {
            **proof,
            'method': 'one-device legacy cli2 to sealed c17 Client canary',
            'recovery_available': False, 'readback_verified': False,
            'operator_risk_accepted': True, 'hardware_acceptance': False,
            'warning': 'No independently verified full-chip backup. A failed OTA may require wired recovery.',
        }
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
