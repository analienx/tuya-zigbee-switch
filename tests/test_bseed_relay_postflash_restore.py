"""Post-reboot recovery never sends OFF and never mistakes ON for continuity."""
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"helper_scripts"))
from bseed_relay_postflash_restore import restore_preserved_on


def profile(tmp_path, **overrides):
    p={
        "device":"FixturePM", "ieee":"0xa4c13800000000f1",
        "workdir":str(tmp_path), "sha256":"a"*64,
        "relay_state_policy":"preserve","auto_relay_off":False,
        "operator_connected_load_risk_accepted":True,
        "expect_relay":"ON",
        "postflash_build":"1.2.5-bseedcli27", "postflash_role":"EndDevice",
    }
    p.update(overrides)
    (tmp_path/"ACTIVE_LOCK.json").write_text(json.dumps({
        "device":p["device"],"ieee":p["ieee"],"sha256":p["sha256"],
        "phase":"ota_transfer_ok_postflash_unverified",
    }),encoding="utf8")
    return p


class Relay:
    def __init__(self,value):
        self.value=value
        self.calls=[]
    def read(self,key):
        self.calls.append(("GET",key))
        return self.value
    def set(self,key,value):
        self.calls.append(("SET",key,value))
        if key != "state_relay" or value != "ON":
            raise AssertionError("Forbidden relay command")
        self.value=value


def identity(p):
    return {"live_zdo_role":p["postflash_role"],
            "reported_build":p["postflash_build"]}


def test_reboot_cleared_on_recovered_only_with_identity_and_completed_lock(tmp_path):
    p=profile(tmp_path)
    relay=Relay("OFF")
    result=restore_preserved_on(p,"after_rejoin",client=relay,identity_checker=identity)
    assert relay.calls==[("GET","state_relay"),("SET","state_relay","ON"),
                         ("GET","state_relay")]
    assert result["recovery_on_command_sent"] is True
    assert result["hardware_acceptance"] is False
    assert result["mains_continuity_proven"] is False
    assert json.loads((tmp_path/"PRESERVED_ON_AFTER_REJOIN.json").read_text())==result


def test_already_on_uses_no_command(tmp_path):
    p=profile(tmp_path)
    relay=Relay("ON")
    result=restore_preserved_on(p,"after_rejoin",client=relay,identity_checker=identity)
    assert relay.calls==[("GET","state_relay"),("GET","state_relay")]
    assert result["recovery_on_command_sent"] is False


def test_previously_off_never_changes_state(tmp_path):
    p=profile(tmp_path,expect_relay="OFF")
    relay=Relay("ON")
    assert restore_preserved_on(p,"after_rejoin",client=relay,identity_checker=identity) is None
    assert relay.calls==[]


def test_stock_canary_owned_by_existing_exact_target_recovery(tmp_path):
    p=profile(tmp_path,stock_client_preserve_loaded=True)
    relay=Relay("OFF")
    assert restore_preserved_on(p,"after_rejoin",client=relay,identity_checker=identity) is None
    assert relay.calls==[]


@pytest.mark.parametrize("field,value",[
    ("sha256","b"*64),("ieee","0xa4c13800000000f2"),("phase","ota_running"),
])
def test_missing_completed_exact_campaign_evidence_blocks_all_writes(tmp_path,field,value):
    p=profile(tmp_path)
    lock_path=tmp_path/"ACTIVE_LOCK.json"
    lock=json.loads(lock_path.read_text())
    lock[field]=value
    lock_path.write_text(json.dumps(lock))
    relay=Relay("OFF")
    with pytest.raises(ValueError,match="foreign lock|completed OTA"):
        restore_preserved_on(p,"after_rejoin",client=relay,identity_checker=identity)
    assert relay.calls==[]


def test_unverified_firmware_identity_blocks_all_writes(tmp_path):
    p=profile(tmp_path)
    relay=Relay("OFF")
    def reject(_profile):
        raise ValueError("firmware build mismatch")
    with pytest.raises(ValueError,match="build mismatch"):
        restore_preserved_on(p,"after_rejoin",client=relay,identity_checker=reject)
    assert relay.calls==[]


def test_existing_negative_continuity_evidence_is_not_overwritten(tmp_path):
    p=profile(tmp_path)
    relay=Relay("OFF")
    first=restore_preserved_on(p,"after_rejoin",client=relay,identity_checker=identity)
    relay.calls.clear()
    second=restore_preserved_on(p,"after_rejoin",client=relay,identity_checker=identity)
    assert second==first
    assert relay.calls==[]
