"""Fail-closed matrix contract; no Telink compiler needed for these unit tests."""
import hashlib
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'helper_scripts'))
from bseed_pm_variant_matrix import (verify_artifact, ROUTER, CLIENT, COMMON_TESTS,
                                      ROLE_TESTS)


def sample_artifact(tmp_path, role):
    directory=tmp_path/role['role']; directory.mkdir()
    data=b'P' * 14000
    (directory/'forward.ota').write_bytes(data)
    manifest={'sourceCommit':'abcdef', 'sourceDirty':False,
              'board':'OUTLET_BSEED_PM_TS011F',
              'canonicalConfig':'b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;',
              'swBuildId':role['build'], 'fileVersion':role['version'],
              'manufacturerCode':4417,'imageType':role['type'],
              'clientImageType':role['type'],'nvmMigrationsVersion':1,
              'artifacts':{'forward.ota':{'sha256':hashlib.sha256(data).hexdigest()}},
              'otaHeader':{'imageType':role['type'],
                           'fileVersion':role['version'],'totalImageSize':len(data)}}
    (directory/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
    return directory,manifest


@pytest.mark.parametrize('role',[ROUTER,CLIENT])
def test_valid_variant_identity_and_firmware_hash(role,tmp_path):
    path,_=sample_artifact(tmp_path,role)
    assert verify_artifact(path,role,'abcdef')['role']==role['role']


@pytest.mark.parametrize('field,value',[('sourceDirty',True),('sourceCommit','stale'),
        ('imageType',65024),('swBuildId','1.2.5-bseedv8u4'),('fileVersion',0x12053007)])
def test_router_candidate_fails_closed_on_wrong_manifest(tmp_path,field,value):
    directory,manifest=sample_artifact(tmp_path,ROUTER)
    manifest[field]=value
    (directory/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
    with pytest.raises(AssertionError): verify_artifact(directory,ROUTER,'abcdef')
