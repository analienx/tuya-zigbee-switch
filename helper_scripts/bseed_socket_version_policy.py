"""Fail-closed release version checks for the verified BSEED TS011F boards.

Numeric fileVersion must rise across BOTH Zigbee roles of the SAME board.
This is a release policy; stock-facing 0xffffffff conversion wrappers are
transport identities and must not be mistaken for a custom release number.
"""

BOARDS = {
    'b28wrpvx': ('TS011F-BS-PM', {
        '1.2.5-bseedv8u4': 0x12053007,
        '1.2.5-bseedcli6': 0x1205300C,
        '1.2.5-bseedv8u5-rc1': 0x1205300D,
        '1.2.5-bseedv8u5-rc2': 0x1205300E,
        '1.2.5-bseedcli7': 0x1205300F,
        '1.2.5-bseedv8u5-rc3': 0x12053010,
        '1.2.5-bseedcli8': 0x12053011,
    }),
    'o1jzcxou': ('TS011F-BS', {
        '1.1.3-bseedv8': 0x11023001,
        '1.1.2-bseedcli4': 0x1102300F,
        '1.1.2-bseedcli5-rc1': 0x11023010,
        '1.1.2-bseedcli6': 0x11023011,
        '1.1.3-bseedv9': 0x11023012,
        '1.1.2-bseedcli7': 0x11023013,
        '1.1.3-bseedv10': 0x11023014,
    }),
}

CANDIDATES = {
    '1.2.5-bseedv8u5-rc2', '1.2.5-bseedcli7',
    '1.1.2-bseedcli5-rc1', '1.1.2-bseedcli6', '1.1.3-bseedv9',
    '1.2.5-bseedv8u5-rc3', '1.2.5-bseedcli8',
    '1.1.2-bseedcli7', '1.1.3-bseedv10',
}


def require_increasing(profile):
    """Reject reused, downgraded, mislabelled or unknown custom OTA versions."""
    board = profile.get('manufacturer')
    if board not in BOARDS:
        return False  # Do not impose BSEED rules on unrelated firmware.
    model, builds = BOARDS[board]
    if profile.get('model') != model:
        raise ValueError('BSEED board/model mismatch')
    build = profile.get('postflash_build')
    version = int(str(profile['file_version']), 0)
    if build not in builds:
        if version in builds.values():
            raise ValueError('Known OTA version reused with unknown build identity')
        raise ValueError('Unregistered BSEED firmware build/version: register and review before OTA')
    if version != builds[build]:
        raise ValueError('OTA file version differs from registered firmware build')
    previous_build = profile.get('preflash_build')
    if previous_build not in builds:
        raise ValueError('Missing or unknown preflash build: monotonicity unproven')
    if version <= builds[previous_build]:
        raise ValueError('OTA file version must increase across socket roles')
    role = profile.get('postflash_role')
    expected_role = 'EndDevice' if 'cli' in build else 'Router'
    if role is not None and role != expected_role:
        raise ValueError('Firmware build does not match intended Zigbee role')
    if build in CANDIDATES:
        older = {name: number for name, number in builds.items()
                 if name != build and number < version}
        if not older:
            raise ValueError('Candidate version has no prior board history')
    return True
