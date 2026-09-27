"""Campaign claims must match sealed payload role and a strictly newer identity."""
import copy
import hashlib
from pathlib import Path
import struct
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
from bseed_socket_version_policy import require_increasing
from bseed_ota_identity import IdentityError
from tests.bseed_image_fixture import image_for


def fixture(tmp_path, board='b28wrpvx', source_role='Router', target_role='EndDevice'):
    if board == 'b28wrpvx':
        types = {'Router': 43556, 'EndDevice': 65024}
        model = 'TS011F-BS-PM'
    elif board == 'o1jzcxou':
        types = {'Router': 43555, 'EndDevice': 65026}
        model = 'TS011F-BS'
    else:
        types = {'Router': 45577}
        model = 'TS0726-3-BS'
    old = dict(build='1.2.5-bseedold', version=100, type=types[source_role])
    new = dict(build='1.2.5-bseednew', version=101, type=types[target_role])
    blob = image_for(new)
    native = tmp_path / 'native.ota'; native.write_bytes(blob)
    wrapped = bytearray(blob); struct.pack_into('<H', wrapped, 12, types[source_role])
    image = tmp_path / 'offered.ota'; image.write_bytes(wrapped)
    registry = {typ: dict(board_key=board, role='router' if role == 'Router' else 'client', versions=[])
                for role, typ in types.items()}
    registry[old['type']]['versions'].append(dict(file_version=100, version_str=old['build'], sha512='a'*128))
    registry[new['type']]['versions'].append(dict(file_version=101, version_str=new['build'], sha512=hashlib.sha512(blob).hexdigest()))
    profile = dict(manufacturer=board, model=model, preflash_role=source_role, postflash_role=target_role,
        preflash_build=old['build'], postflash_build=new['build'], image=str(image), native_image=str(native),
        manufacturer_code=4417, image_type=types[source_role], file_version=101,
        sha256=hashlib.sha256(wrapped).hexdigest())
    return profile, registry


@pytest.mark.parametrize('board', ['b28wrpvx', 'o1jzcxou'])
@pytest.mark.parametrize('roles', [('Router', 'Router'), ('Router', 'EndDevice'), ('EndDevice', 'Router'), ('EndDevice', 'EndDevice')])
def test_valid_board_role_and_transport_are_independent(tmp_path, board, roles):
    p, registry = fixture(tmp_path, board, *roles)
    assert require_increasing(p, registry)['target_version'] == 101


@pytest.mark.parametrize('mutation', ['wrong_board', 'wrong_role', 'wrong_build', 'unsealed', 'equal', 'downgrade', 'wrapper_version', 'query_type', 'foreign_source', 'different_bytes'])
def test_campaign_rejects_wrong_or_nonincreasing_target(tmp_path, mutation):
    p, registry = fixture(tmp_path)
    if mutation == 'wrong_board': p['manufacturer'] = 'o1jzcxou'
    if mutation == 'wrong_role': p['postflash_role'] = 'Router'
    if mutation == 'wrong_build': p['postflash_build'] = '1.2.5-bseedother'
    if mutation == 'unsealed': registry[65024]['versions'][0]['sha512'] = None
    if mutation in ('equal', 'downgrade'):
        registry[43556]['versions'][0]['file_version'] = 101 if mutation == 'equal' else 102
    if mutation == 'foreign_source': p['preflash_build'] = 'foreign'
    if mutation == 'query_type': p['preflash_query_image_type'] = 65024
    if mutation == 'different_bytes': p['sha256'] = 'f'*64
    if mutation == 'wrapper_version':
        data = bytearray(Path(p['image']).read_bytes()); struct.pack_into('<I', data, 14, 102)
        Path(p['image']).write_bytes(data); p['file_version'] = 102; p['sha256'] = hashlib.sha256(data).hexdigest()
    with pytest.raises(IdentityError): require_increasing(p, registry)


def test_wrapper_cannot_change_native_payload(tmp_path):
    p, registry = fixture(tmp_path)
    data = bytearray(Path(p['image']).read_bytes()); data[-10] ^= 1
    Path(p['image']).write_bytes(data); p['sha256'] = hashlib.sha256(data).hexdigest()
    with pytest.raises(IdentityError, match='payload'): require_increasing(p, registry)


def test_sealed_router_payload_with_client_transport_type_is_not_a_client(tmp_path):
    p, registry = fixture(tmp_path)
    registry[65024]['versions'][0].update(payload_role='Router', payload_image_type=43556)
    with pytest.raises(IdentityError, match='not native firmware'): require_increasing(p, registry)


def replace_transport(profile, *, image_type, version=None):
    blob = bytearray(Path(profile['image']).read_bytes())
    struct.pack_into('<H', blob, 12, image_type)
    profile['image_type'] = image_type
    if version is not None:
        struct.pack_into('<I', blob, 14, version)
        profile['file_version'] = version
    Path(profile['image']).write_bytes(blob)
    profile['sha256'] = hashlib.sha256(blob).hexdigest()


@pytest.mark.parametrize('board', ['b28wrpvx', 'o1jzcxou'])
def test_stock_wrapper_can_only_convert_into_native_router(tmp_path, board):
    p, registry = fixture(tmp_path, board, 'Router', 'Router')
    p.update(manufacturer='_TZ3000_' + board, model='TS011F')
    replace_transport(p, image_type=54179, version=0xffffffff)
    assert require_increasing(p, registry)['source'] == 'stock'
    p['preflash_role'] = 'EndDevice'
    with pytest.raises(IdentityError, match='stock conversion'):
        require_increasing(p, registry)


def test_explicit_query_override_cannot_cross_board_types(tmp_path):
    p, registry = fixture(tmp_path)
    p['preflash_query_image_type'] = 65026
    replace_transport(p, image_type=65026)
    with pytest.raises(IdentityError, match='different board'):
        require_increasing(p, registry)


def test_same_board_historical_query_override_does_not_change_payload_role(tmp_path):
    p, registry = fixture(tmp_path, source_role='EndDevice', target_role='EndDevice')
    p['preflash_query_image_type'] = 43556
    replace_transport(p, image_type=43556)
    assert require_increasing(p, registry)['target_role'] == 'EndDevice'


def test_shared_build_identity_requires_explicit_payload_role(tmp_path):
    p, registry = fixture(tmp_path)
    registry[43556]['versions'].append(copy.deepcopy(registry[65024]['versions'][0]))
    with pytest.raises(IdentityError, match='ambiguous wrapper/native'):
        require_increasing(p, registry)
    registry[65024]['versions'][0].update(payload_role='EndDevice', payload_image_type=65024)
    assert require_increasing(p, registry)['target_role'] == 'EndDevice'


def test_ts0726_router_update_requires_sealed_increasing_identity(tmp_path):
    p, registry = fixture(tmp_path, 'iedhxgyi', 'Router', 'Router')
    verdict = require_increasing(p, registry)
    assert (verdict['board'], verdict['target_version']) == ('iedhxgyi', 101)


@pytest.mark.parametrize('mutation', ['client_target', 'client_source', 'wrong_model', 'wrong_prefix',
    'equal', 'downgrade', 'query_type', 'unsealed', 'foreign_source'])
def test_ts0726_rejects_client_wrong_model_or_nonincreasing(tmp_path, mutation):
    p, registry = fixture(tmp_path, 'iedhxgyi', 'Router', 'Router')
    if mutation == 'client_target': p['postflash_role'] = 'EndDevice'
    if mutation == 'client_source': p['preflash_role'] = 'EndDevice'
    if mutation == 'wrong_model': p['model'] = 'TS011F'
    if mutation == 'wrong_prefix': p.update(manufacturer='_TZ3000_iedhxgyi', model='TS0726')
    if mutation in ('equal', 'downgrade'):
        registry[45577]['versions'][0]['file_version'] = 101 if mutation == 'equal' else 102
    if mutation == 'query_type': p['preflash_query_image_type'] = 43556
    if mutation == 'unsealed': registry[45577]['versions'][1]['sha512'] = None
    if mutation == 'foreign_source': p['preflash_build'] = 'foreign'
    with pytest.raises(IdentityError): require_increasing(p, registry)


def test_ts0726_stock_wrapper_can_only_convert_into_native_router(tmp_path):
    p, registry = fixture(tmp_path, 'iedhxgyi', 'Router', 'Router')
    p.update(manufacturer='_TZ3002_iedhxgyi', model='TS0726')
    replace_transport(p, image_type=54179, version=0xffffffff)
    assert require_increasing(p, registry)['source'] == 'stock'
    p['preflash_role'] = 'EndDevice'
    with pytest.raises(IdentityError, match='missing or unsupported'):
        require_increasing(p, registry)


def test_policy_is_wired_before_index_and_runtime_operations():
    campaign = (ROOT / 'helper_scripts/bseed_ota_campaign.py').read_text()
    runner = (ROOT / 'helper_scripts/bseed_targeted_z2m_ota.py').read_text()
    assert campaign.index('require_increasing(profile)') < campaign.index('destination.write_text')
    assert runner.index('require_increasing(campaign)') < runner.index('verify_image(args)', runner.index('def main'))
    assert "if args.mode in ('check', 'flash'):" in runner
    assert "'--campaign-profile'" in campaign
