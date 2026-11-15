"""Exact no-backup risk admission for the non-PM Bedroom client pilot."""
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"helper_scripts"))
from bseed_nonpm_recovery_gate import verify_recovery

BASE={
"device":"BedroomSocketCabinetRight","ieee":"0xa4c13824a7005afb",
"manufacturer":"o1jzcxou","model":"TS011F-BS",
"non_pm":True,"require_pm":False,
"preflash_role":"EndDevice","postflash_role":"EndDevice",
"preflash_build":"1.1.3-bseedc16","postflash_build":"1.1.3-bseedc17",
"sha256":"3ae273707a657b79a8bd2b7f6f77794d57547ba3a52247627b4a7c335f932b08",
"parallel_ota_max":2,"concurrent_ota_ieees":["0xa4c138ac993cc245"],
"source_rejoin_strategy":"none","force_test_transition":False,
"image_type":65026,"file_version":"0x1102301e",
"block_bytes":32,"response_delay_ms":1800,
"preflash_relay_physical_mode":"follow_state","relay_get_key":"state_relay",
"expect_relay":"OFF",
}
def test_exact_dual_canary_requires_explicit_unloaded_signal():
    with pytest.raises(ValueError,match="Physical load"):
        verify_recovery(BASE,confirm_unloaded=False)
    result=verify_recovery(BASE,confirm_unloaded=True)
    assert result["recovery_available"] is False
    assert result["readback_verified"] is False
    assert result["operator_risk_accepted"] is True
    assert result["hardware_acceptance"] is False

@pytest.mark.parametrize("key,bad",[
("device","AnotherBedroom"),("ieee","0xa4c13824a7005abc"),
("preflash_build","1.1.3-bseedc15"),("postflash_build","1.1.3-bseedc18"),
("sha256","0"*64),("parallel_ota_max",1),
("concurrent_ota_ieees",["0xa4c1387b13e9436d"]),
("source_rejoin_strategy","auto"),("force_test_transition",True),
("image_type",43555),("file_version","0x1102301d"),
("block_bytes",64),("response_delay_ms",1200),
("preflash_relay_physical_mode","legacy_unreported_v8_router"),
("expect_relay","ON"),("relay_get_key","state"),
("require_pm",True),("non_pm",False),
("postflash_role","Router"),
])
def test_every_mismatched_parameter_is_rejected(key,bad):
    changed=dict(BASE);changed[key]=bad
    with pytest.raises((ValueError,TypeError)):
        verify_recovery(changed,confirm_unloaded=True)
