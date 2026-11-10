"""Failed PM retention terminalization must preserve failure and validate identity."""
import copy
import datetime as dt
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper_scripts"))
from bseed_failed_postflash_close import FAILURE, validate
from bseed_network_campaign_lock import (
    FAILED_POSTFLASH_PHASE, acquire, read_lock,
    release_accepted, release_failed_postflash, require_profile_authority, update,
)

NOW = dt.datetime.fromisoformat("2026-11-05T21:17:00+00:00")


def cases():
    profile = dict(device="PMSocketUnderTest",
                   ieee="0xa4c138cd00000001", sha256="d"*64,
                   file_version="0x12053021", preflash_role="Router",
                   postflash_role="Router", require_pm=True,
                   postflash_build="1.2.5-bseedr19",
                   model="TS011F-BS-PM", manufacturer="b28wrpvx",
                   network_id="0x00124b000000abcd")
    token = "txn-one"
    work = dict(phase="ota_transfer_ok_postflash_unverified",
                device=profile["device"], ieee=profile["ieee"], sha256=profile["sha256"],
                token=token, completed="2026-11-04T15:07:46+00:00",
                response={"status": "ok", "transaction": token, "data": {
                    "id": profile["ieee"],
                    "to": {"software_build_id": profile["postflash_build"],
                           "file_version": 302329889}}})
    network = dict(phase=work["phase"], token=token, device=profile["device"],
                   ieee=profile["ieee"], image_sha256=profile["sha256"],
                   network_id=profile["network_id"])
    post = dict(result="unconfirmed", issues=[FAILURE], errors=[], bridge="online",
                observed_at="2026-11-05T21:15:00+00:00",
                fresh_mqtt_state_observed=True, zdo_error=None,
                live_node_descriptor={"role": "Router"}, target_ieee=profile["ieee"],
                expected_build=profile["postflash_build"],
                inventory={"ieee_address": profile["ieee"],
                           "software_build_id": profile["postflash_build"],
                           "type": "Router", "interview_completed": True},
                pm_readiness={"issues": []},
                same_role_retention={"issues": [FAILURE]})
    live = dict(ieeeAddr=profile["ieee"], swBuildId=profile["postflash_build"],
                type="Router", modelId=profile["model"], manufName=profile["manufacturer"],
                interviewCompleted=True, lastSeen=int(
                    dt.datetime.fromisoformat("2026-11-05T21:16:30+00:00").timestamp()*1000))
    return [profile, work, network, post, live]


def test_only_negative_retention_terminalizes_without_success():
    report = validate(*cases(), now=NOW)
    assert report["result"] == FAILED_POSTFLASH_PHASE
    assert report["hardware_acceptance"] is False
    assert report["pm_energy_retention_accepted"] is False
    assert report["new_ota_authorized"] is False


@pytest.mark.parametrize("mutate", [
    lambda c: c[1].update(phase="ota_running"),
    lambda c: c[2].update(token="different"),
    lambda c: c[2].update(network_id="different"),
    lambda c: c[1].update(sha256="c"*64),
    lambda c: c[1]["response"].update(status="error"),
    lambda c: c[3].update(result="postflash_candidate"),
    lambda c: c[3].update(issues=[]),
    lambda c: c[3]["same_role_retention"].update(issues=[]),
    lambda c: c[3]["pm_readiness"].update(issues=["bad"]),
    lambda c: c[3]["inventory"].update(software_build_id="wrong"),
    lambda c: c[3]["live_node_descriptor"].update(role="EndDevice"),
    lambda c: c[3].update(fresh_mqtt_state_observed=False),
    lambda c: c[3].update(observed_at="2026-11-05T18:15:00+00:00"),
    lambda c: c[4].update(swBuildId="different"),
    lambda c: c[4].update(type="EndDevice"),
    lambda c: c[4].update(lastSeen=1),
])
def test_fail_closed_for_invalid_historical_or_live_evidence(mutate):
    values = copy.deepcopy(cases())
    mutate(values)
    with pytest.raises((ValueError, KeyError, TypeError)):
        validate(*values, now=NOW)


def test_release_only_from_explicit_failed_phase(tmp_path):
    data = dict(workdir=str(tmp_path/"other-work"),
                network_lock_dir=str(tmp_path/"shared-network"),
                network_id="zigbee-net", network_lock_shared=True)
    path = require_profile_authority(data)
    acquire(path, network_id="zigbee-net", token="owner",
            device="Socket", ieee="0x00124b0000000001", image_sha256="c"*64)
    update(path, "owner", "ota_transfer_ok_postflash_unverified")
    with pytest.raises(RuntimeError):
        release_failed_postflash(path, "owner")
    assert path.exists()
    update(path, "owner", FAILED_POSTFLASH_PHASE,
           hardware_acceptance=False, failure="retained")
    with pytest.raises(RuntimeError):
        release_accepted(path, "owner")
    assert read_lock(path)["hardware_acceptance"] is False
    assert release_failed_postflash(path, "owner") is True
    assert not path.exists()
