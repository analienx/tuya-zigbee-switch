"""Deprecated automatic relay-OFF must be unreachable even from direct helpers."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"helper_scripts"))
import bseed_relay_auto_off as auto
import bseed_operator_load_policy as policy
import bseed_ota_campaign as campaign


def profile():
    return {
        "device":"FixtureSocket","ieee":"0xa4c1380000000001",
        "auto_relay_off":True,"expect_relay":"OFF",
        "require_pm":True,"non_pm":False,"manufacturer":"b28wrpvx",
        "pm_preflash_load_proof":"meter",
    }


def test_automatic_off_is_never_a_valid_flash_profile():
    with pytest.raises(ValueError,match="disabled"):
        auto.validate(profile())
    assert auto.validate(dict(profile(),auto_relay_off=False)) is False
    assert auto.validate(dict(profile(),auto_relay_off=None)) is False
    with pytest.raises(ValueError,match="explicit Boolean"):
        auto.validate(dict(profile(),auto_relay_off="true"))


def test_switch_off_helper_fails_without_touching_client():
    class NeverCall:
        def __getattr__(self,key):
            raise AssertionError("Direct OFF helper must not read or write hardware")
    with pytest.raises(ValueError,match="prohibited"):
        auto.switch_off_before_flash(profile(),NeverCall())


def test_post_rejoin_off_helper_fails_without_touching_client():
    with pytest.raises(ValueError,match="prohibited"):
        auto.reassert_off_after_rejoin(profile(),object())


def test_underlying_mqtt_client_rejects_relay_off_before_publish():
    fake=object.__new__(auto.RelayClient)
    class NoPublish:
        def publish(self,*args,**kwargs):
            raise AssertionError("Relay-OFF must not publish MQTT")
    fake.client=NoPublish()
    fake.topic="zigbee2mqtt/FixtureSocket"
    with pytest.raises(ValueError,match="forbidden"):
        fake.set("state_relay","OFF")
    with pytest.raises(ValueError,match="forbidden"):
        fake.set("state","OFF")
    with pytest.raises(ValueError,match="forbidden"):
        fake.set("state_relay","toggle")
    with pytest.raises(ValueError,match="forbidden"):
        fake.set("relay_physical_mode","ON")


def test_only_explicit_connected_load_preserve_state_is_authorized():
    p=dict(profile(),auto_relay_off=False,expect_relay="ON",
           relay_state_policy="preserve",operator_connected_load_risk_accepted=True,
           relay_get_key="state_relay")
    assert policy.require_preserve_state_for_flash(p) is True
    for attr,val in [("auto_relay_off",True),("relay_state_policy","off"),
                     ("operator_connected_load_risk_accepted",False),
                     ("expect_relay","unknown"),("relay_get_key","other")]:
        with pytest.raises(ValueError):
            policy.require_preserve_state_for_flash(dict(p,**{attr:val}))


def test_campaign_cannot_activate_legacy_off_route(monkeypatch):
    calls=[]
    monkeypatch.setattr(campaign.subprocess,"call",lambda *a,**k:calls.append(a) or 0)
    with pytest.raises(ValueError,match="prohibited"):
        campaign.auto_relay_off_then_check(profile())
    assert calls==[]
