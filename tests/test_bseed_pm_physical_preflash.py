"""Offline PM preflash load-proof alternative. GitHub-hosted CI only."""
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper_scripts"))
import bseed_ota_campaign as campaign
import bseed_targeted_z2m_ota as targeted
import bseed_ota_resume_supervisor as supervisor

PM = dict(manufacturer="b28wrpvx", model="TS011F-BS-PM",
          require_pm=True, non_pm=False, preflash_role="EndDevice",
          postflash_role="EndDevice")

@pytest.mark.parametrize("role", ["Router", "EndDevice"])
def test_physical_preflash_opt_in_works_for_both_pm_roles(role):
    data = dict(PM, preflash_role=role, postflash_role=role,
                pm_preflash_load_proof="physically_unloaded")
    assert campaign.validate_pm_mode(data) is True

def test_default_meter_route_preserved():
    assert campaign.validate_pm_mode(PM) is True
    assert campaign.validate_pm_mode(dict(PM, pm_preflash_load_proof="meter")) is True

@pytest.mark.parametrize("invalid", [
    dict(manufacturer="o1jzcxou", model="TS011F-BS", require_pm=False, non_pm=True),
    dict(manufacturer="_TZ3000_b28wrpvx", model="TS011F", require_pm=True),
    dict(manufacturer="iedhxgyi", model="TS0726-3-BS", require_pm=False),
    dict(manufacturer="b28wrpvx", model="TS011F-BS-PM", require_pm=False),
])
def test_physical_preflash_denied_for_other_board_or_stock(invalid):
    with pytest.raises(ValueError, match="only for exact custom PM"):
        campaign.validate_pm_mode(dict(PM, **invalid, pm_preflash_load_proof="physically_unloaded"))

@pytest.mark.parametrize("bad", [True, False, None, 1, "skip", "disabled"])
def test_invalid_policy_is_not_opt_out(bad):
    with pytest.raises(ValueError, match="meter or physically_unloaded"):
        campaign.validate_pm_mode(dict(PM, pm_preflash_load_proof=bad))

def test_physical_pm_flag_only_appended_to_flash_if_confirmed():
    profile = dict(PM, pm_preflash_load_proof="physically_unloaded",
                   device="Fixture", ieee="0x0000000000000001",
                   image="/private/image.ota", sha256="a"*64,
                   url="http://example.invalid/image.ota",
                   mqtt_config="/private/mqtt.yml", broker="localhost",
                   workdir="/private/campaign", manufacturer_code=4417,
                   image_type=65024, file_version=0x12053019,
                   index_url="http://example.invalid/index.json",
                   expect_relay="OFF", postflash_build="1.2.5-bseedcli14")
    for mode in ("preflight", "check"):
        cmd = campaign.runner_args(profile, mode)
        assert "--pm-preflash-physical-unloaded" in cmd
        assert "--confirm-load-unplugged" not in cmd
    cmd = campaign.runner_args(profile, "flash", confirm_unloaded=True)
    assert "--pm-preflash-physical-unloaded" in cmd
    assert "--confirm-load-unplugged" in cmd
    without = campaign.runner_args(profile, "flash", confirm_unloaded=False)
    assert "--confirm-load-unplugged" not in without

def test_meter_validation_still_requires_actual_power():
    with pytest.raises(AssertionError, match="Power missing"):
        targeted.validate_metering_preflight({}, non_pm=False,
            model="TS011F-BS-PM", manufacturer="b28wrpvx",
            role="EndDevice", max_reported_watts=1.0)
    assert targeted.validate_metering_preflight({"power": 0}, non_pm=False,
        model="TS011F-BS-PM", manufacturer="b28wrpvx",
        role="EndDevice", max_reported_watts=1.0) == 0

def test_supervisor_passes_load_attestation_to_flash_command():
    cmd = supervisor.campaign_cmd(Path("private.json"), "flash",
                                  confirm_ieee="0x0000000000000001",
                                  confirm_unloaded=True)
    assert "--confirm-load-unplugged" in cmd
    assert "--confirm-ieee" in cmd
