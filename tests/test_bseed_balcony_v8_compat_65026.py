"""Exact legacy-v8 bootloader image-type wrapper, without any general relaxation."""
from pathlib import Path
import json,sys,copy
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"helper_scripts"))
from bseed_nonpm_relay_policy import is_exact_balcony_router_candidate,verify_relay_policy_profile
from bseed_nonpm_recovery_gate import verify_recovery
PROFILE=Path(r"C:\Workspace\scratch\bseed-canary-20261031\next-dual-bedroom-pair-20261115\BedroomSocketBalcony\compat-65026\profile.json")
def base():
    return json.loads(PROFILE.read_text(encoding="utf8"))
def test_exact_wrapper_is_a_same_role_no_swire_canary():
    p=base()
    assert is_exact_balcony_router_candidate(p)
    assert verify_relay_policy_profile(p)
    with pytest.raises(ValueError,match="Physical load"):
        verify_recovery(p,confirm_unloaded=False)
    record=verify_recovery(p,confirm_unloaded=True)
    assert record["recovery_available"] is False
@pytest.mark.parametrize("name,value",[
("device","Wrong"),("ieee","0xa4c138ac993cc246"),
("sha256","0"*64),("image_type",43555),("preflash_query_image_type",43555),
("native_sha256","0"*64),("file_version","0x1102301e"),
("parallel_ota_max",1),("concurrent_ota_ieees",["0xa4c1387b13e9436d"]),
("source_rejoin_strategy","auto"),("block_bytes",64),
("response_delay_ms",1200),("force_test_transition",True),
("preflash_build","1.1.3-bseedr17"),("postflash_build","1.1.3-bseedr19"),
("preflash_role","EndDevice"),
])
def test_unrelated_profile_rejected(name,value):
    p=base();p[name]=value
    assert not is_exact_balcony_router_candidate(p)
    with pytest.raises(ValueError):
        verify_recovery(p,confirm_unloaded=True)
def test_tampering_private_image_is_rejected_by_hash_guard(tmp_path):
    p=base()
    orig=Path(p["image"]).read_bytes()
    changed=bytearray(orig);changed[22]^=1
    f=tmp_path/"firmware.ota";f.write_bytes(changed)
    p["image"]=str(f)
    assert not is_exact_balcony_router_candidate(p)
