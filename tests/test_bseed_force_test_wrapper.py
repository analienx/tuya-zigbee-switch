"""Force-test wrappers preserve exact sealed native payload bytes."""
import hashlib
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))

from bseed_force_test_wrapper import build_force_wrapper
from bseed_ota_identity import IdentityError, parse_ota_header
from tests.bseed_image_fixture import image_for


def fixture():
    candidate = dict(build='1.2.5-bseednew', version=101, type=65024)
    native = image_for(candidate)
    registry = {
        43556: dict(board_key='b28wrpvx', role='router', versions=[
            dict(file_version=101, version_str='1.2.5-bseedold', sha512='a' * 128)
        ]),
        65024: dict(board_key='b28wrpvx', role='client', versions=[
            dict(file_version=101, version_str=candidate['build'],
                 sha512=hashlib.sha512(native).hexdigest(),
                 payload_role='EndDevice', payload_image_type=65024)
        ]),
    }
    return native, registry, candidate


def test_force_wrapper_changes_only_outer_query_tuple():
    native, registry, candidate = fixture()
    wrapper, report = build_force_wrapper(
        native, board='b28wrpvx', source_role='Router',
        target_role='EndDevice', target_build=candidate['build'], registry=registry)
    outer = parse_ota_header(wrapper)
    inner = parse_ota_header(native)
    assert outer['image_type'] == 43556
    assert outer['file_version'] == 0xffffffff
    assert inner['image_type'] == 65024 and inner['file_version'] == 101
    assert wrapper[56:] == native[56:]
    assert report['nativeSha256'] == hashlib.sha256(native).hexdigest()
    assert report['wrapperSha256'] == hashlib.sha256(wrapper).hexdigest()
    assert report['forceTestOnly'] is True
    assert report['deploymentReady'] is False


def test_force_wrapper_rejects_unsealed_candidate():
    native, registry, candidate = fixture()
    registry[65024]['versions'][0]['sha512'] = None
    with pytest.raises(IdentityError, match='sealed'):
        build_force_wrapper(
            native, board='b28wrpvx', source_role='Router',
            target_role='EndDevice', target_build=candidate['build'], registry=registry)


def test_force_wrapper_rejects_wrong_target_role_or_same_role():
    native, registry, candidate = fixture()
    with pytest.raises(IdentityError, match='cross-role'):
        build_force_wrapper(
            native, board='b28wrpvx', source_role='EndDevice',
            target_role='EndDevice', target_build=candidate['build'], registry=registry)
    with pytest.raises(IdentityError, match='native image'):
        build_force_wrapper(
            native, board='b28wrpvx', source_role='EndDevice',
            target_role='Router', target_build='1.2.5-bseedold', registry=registry)


def test_force_wrapper_rejects_non_socket_board():
    native, registry, candidate = fixture()
    with pytest.raises(IdentityError, match='socket boards'):
        build_force_wrapper(
            native, board='iedhxgyi', source_role='Router',
            target_role='EndDevice', target_build=candidate['build'], registry=registry)
