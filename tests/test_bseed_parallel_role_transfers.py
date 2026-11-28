"""Mixed-role transport admission and serialized rejoin; offline only."""
from pathlib import Path
import datetime as dt
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper_scripts"))
import bseed_network_campaign_lock as lock
import bseed_ota_autopilot as ap
import bseed_socket_migrate as mig

NET = "0x00124b0000000000"
A, B, C = ("0xa4c1380000000001", "0xa4c1380000000002", "0xa4c1380000000003")
SHA = "a" * 64
COMPLETE = "ota_transfer_ok_postflash_unverified"


def profile(tmp_path, ieee, source, target, peers):
    return dict(device="Socket" + ieee[-1], ieee=ieee, sha256=SHA,
                preflash_role=source, postflash_role=target,
                parallel_role_transition=source != target, force_test_transition=source != target,
                parallel_ota_max=3, concurrent_ota_ieees=list(peers),
                network_lock_shared=True, network_lock_dir=str(tmp_path / "locks"),
                network_id=NET, workdir=str(tmp_path / ieee), request_timeout_ms=300000,
                source_rejoin_strategy="none", allow_join_all_fallback=False,
                block_bytes=48, response_delay_ms=1800, relay_state_policy="preserve",
                expect_relay="ON", auto_relay_off=False)


def admit(p, phase="ota_running"):
    work = Path(p["workdir"])
    work.mkdir(exist_ok=True)
    path = lock.require_profile_authority(p)
    token = p["ieee"]
    lock.acquire(path, network_id=NET, token=token, device=p["device"], ieee=p["ieee"],
                 image_sha256=SHA, parallel_ota_max=3,
                 parallel_allowed_ieees=p["concurrent_ota_ieees"], work_lock_path=str(work / "ACTIVE_LOCK.json"))
    set_phase(p, phase)
    return path


def set_phase(p, phase):
    path = lock.require_profile_authority(p)
    token = p["ieee"]
    work = Path(p["workdir"])
    record = dict(device=p["device"], ieee=p["ieee"], sha256=SHA, token=token,
                  network_lock=str(path), phase=phase)
    if phase == COMPLETE:
        record["response"] = dict(status="ok", transaction=token, data=dict(id=p["ieee"]))
    (work / "ACTIVE_LOCK.json").write_text(json.dumps(record))
    lock.update(path, token, phase)
    (work / "LIVE_STATUS.json").write_text(json.dumps(dict(
        ieee=p["ieee"], token=token, phase="ota_running",
        observed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        update=dict(state="updating", progress=17))))


@pytest.mark.parametrize("source,target", [
    ("Router", "Router"), ("EndDevice", "EndDevice"),
    ("Router", "EndDevice"), ("EndDevice", "Router")])
def test_all_supported_roles_enter_pool(tmp_path, source, target):
    p = profile(tmp_path, A, source, target, [B, C])
    assert lock.require_profile_authority(p).name.endswith(A + ".json")
    assert ap.validate_policy(p) == []


def test_three_mixed_transfers_keep_all_campaigns_and_serialize_recovery(tmp_path):
    a = profile(tmp_path, A, "Router", "EndDevice", [B, C])
    b = profile(tmp_path, B, "EndDevice", "Router", [A, C])
    c = profile(tmp_path, C, "Router", "Router", [A, B])
    ap.validate_group([a, b, c])
    paths = [admit(p) for p in (a, b, c)]
    assert all(lock.read_lock(p)["phase"] == "ota_running" for p in paths)
    set_phase(b, COMPLETE)
    with pytest.raises(RuntimeError, match="peer transfers"):
        with lock.role_recovery_window(b):
            pytest.fail("Opened recovery while peer transports are active")
    set_phase(a, COMPLETE)
    set_phase(c, COMPLETE)
    with lock.role_recovery_window(b):
        with pytest.raises(RuntimeError, match="Another role recovery"):
            with lock.role_recovery_window(a):
                pytest.fail("Competing recovery window")
        d = profile(tmp_path, "0xa4c1380000000004", "Router", "Router", [A, B, C])
        with pytest.raises(RuntimeError, match="role recovery is in progress"):
            admit(d)
    with lock.role_recovery_window(a):
        pass
    assert all(lock.read_lock(p)["phase"] == COMPLETE for p in paths)


def test_old_unresolved_transport_is_not_retired_or_ignored(tmp_path):
    a = profile(tmp_path, A, "Router", "EndDevice", [B])
    b = profile(tmp_path, B, "EndDevice", "Router", [A])
    pa = admit(a)
    set_phase(a, COMPLETE)
    pb = admit(b)
    status = Path(b["workdir"]) / "LIVE_STATUS.json"
    s = json.loads(status.read_text())
    s["update"]["progress"] = 100
    status.write_text(json.dumps(s))
    with pytest.raises(RuntimeError, match="peer transfers"):
        with lock.role_recovery_window(a):
            pytest.fail("An unresolved 100% transport is not a successful response")
    assert lock.read_lock(pa)["phase"] == COMPLETE
    assert lock.read_lock(pb)["phase"] == "ota_running"


def test_role_recovery_refuses_wrong_token(tmp_path):
    a = profile(tmp_path, A, "Router", "EndDevice", [])
    admit(a, COMPLETE)
    file = Path(a["workdir"]) / "ACTIVE_LOCK.json"
    record = json.loads(file.read_text())
    record["token"] = "other"
    file.write_text(json.dumps(record))
    with pytest.raises(RuntimeError, match="exact successful transfer"):
        with lock.role_recovery_window(a):
            pytest.fail("Foreign token accepted")


def test_unopted_and_unsafe_mixed_profiles_are_rejected(tmp_path):
    p = profile(tmp_path, A, "EndDevice", "Router", [B])
    for changes in (dict(parallel_role_transition=False), dict(parallel_ota_max=2),
                    dict(source_rejoin_strategy="scoped"), dict(allow_join_all_fallback=True)):
        with pytest.raises(ValueError):
            lock.require_profile_authority(dict(p, **changes))


def test_mixed_wave_groups_both_conversion_directions_and_same_role(tmp_path):
    ps = [profile(tmp_path, A, "Router", "EndDevice", [B, C]),
          profile(tmp_path, B, "EndDevice", "Router", [A, C]),
          profile(tmp_path, C, "Router", "Router", [A, B])]
    group = [(None, Path(p["workdir"]), p, False) for p in ps]
    assert list(mig.parallel_waves(group)) == [group]


def test_group_finalizes_router_first_after_all_transfer_workers(tmp_path, monkeypatch):
    a = profile(tmp_path, A, "Router", "EndDevice", [B])
    b = profile(tmp_path, B, "EndDevice", "Router", [A])
    profiles = {}
    for p in (a, b):
        w = Path(p["workdir"]); w.mkdir()
        profiles[w / "profile.json"] = p
        (w / ap.STATE_FILE).write_text(json.dumps(dict(state="transfer_complete", phase=COMPLETE)))
        (w / "ACTIVE_LOCK.json").write_text(json.dumps(dict(phase=COMPLETE)))
    monkeypatch.setattr(ap.sup.campaign, "load_profile", lambda path: profiles[path])
    monkeypatch.setattr(mig, "check_parallel_topology", lambda *args: None)
    launches, finalizes = [], []
    def launch(cmd, log):
        launches.append(cmd)
        return len(launches)
    def finish(cmd, log):
        assert len(launches) == 2
        assert cmd[cmd.index("--mode") + 1] == "transition-finalize"
        target = cmd[cmd.index("--confirm-ieee") + 1]
        finalizes.append(target)
        work = Path((a if target == A else b)["workdir"])
        (work / "ACTIVE_LOCK.json").write_text(json.dumps(dict(phase="postflash_candidate")))
        return 0
    monkeypatch.setattr(ap.sup, "run_logged", finish)
    result = ap.run_many(list(profiles), [A, B], extra=[], launch=launch, alive=lambda _: False, sleep=lambda _: None)
    assert finalizes == [B, A]
    assert [r["state"] for r in result] == ["installed", "installed"]
