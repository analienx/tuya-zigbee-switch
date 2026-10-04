import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper_scripts"))
import bseed_ota_resume_supervisor as sup
import bseed_source_rejoin_recovery as rejoin


def _profile(tmp_path):
    image = tmp_path / "image.ota"
    image.write_bytes(b"fixture")
    work = tmp_path / "work"
    return {
        "device": "BedroomSocketCabinetRight",
        "ieee": "0xa4c13824a7005afb",
        "manufacturer": "o1jzcxou",
        "model": "TS011F-BS",
        "preflash_role": "EndDevice",
        "preflash_build": "1.1.3-bseedc7",
        "preflash_relay_physical_mode": "follow_state",
        "postflash_role": "Router",
        "postflash_build": "1.1.3-bseedr10",
        "image": str(image),
        "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        "workdir": str(work),
        "non_pm": True,
        "force_test_transition": True,
    }


def test_launch_logged_never_uses_pipe(tmp_path, monkeypatch):
    seen = {}

    class FakePopen:
        pid = 4242
        def __init__(self, cmd, **kwargs):
            seen["cmd"] = cmd
            seen["kwargs"] = kwargs

    monkeypatch.setattr(sup.subprocess, "Popen", FakePopen)
    log = tmp_path / "transition.log"
    pid = sup.launch_logged(["python", "worker.py"], log)

    assert pid == 4242
    kwargs = seen["kwargs"]
    assert kwargs["stdout"] is not sup.subprocess.PIPE
    assert kwargs["stderr"] == sup.subprocess.STDOUT
    assert kwargs["stdin"] == sup.subprocess.DEVNULL
    assert kwargs["close_fds"] is True
    assert kwargs["stdout"].name == str(log)


def test_resume_reconciles_then_qualifies_then_launches(tmp_path, monkeypatch):
    cfg = _profile(tmp_path)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(cfg))
    work = Path(cfg["workdir"])
    work.mkdir()
    (work / "ACTIVE_LOCK.json").write_text(json.dumps({
        "phase": "update_error",
        "device": cfg["device"],
        "ieee": cfg["ieee"],
        "sha256": cfg["sha256"],
    }))

    monkeypatch.setattr(sup.campaign, "load_profile", lambda _p: dict(cfg))
    monkeypatch.setattr(sup, "process_alive", lambda _pid: False)

    calls = []

    def fake_run(cmd, log):
        calls.append(("run", cmd[cmd.index("--mode") + 1]))
        if cmd[cmd.index("--mode") + 1] == "reconcile-source":
            (work / "ACTIVE_LOCK.json").write_text(json.dumps({"phase": "source_unchanged_reconciled"}))
        return 0

    monkeypatch.setattr(sup, "run_logged", fake_run)
    monkeypatch.setattr(sup, "launch_logged",
                        lambda cmd, log: calls.append(("launch", cmd[cmd.index("--mode") + 1])) or 5555)

    result = sup.resume_transition(
        profile_path,
        cfg["ieee"],
        confirm_unloaded=True,
    )

    assert calls == [
        ("run", "reconcile-source"),
        ("run", "qualify"),
        ("launch", "transition"),
    ]
    assert result["pid"] == 5555
    assert result["launch_contract"]["stdout_pipe"] is False
    persisted = json.loads((work / sup.SUPERVISOR_FILE).read_text())
    assert persisted["state"] == "running"
    assert persisted["pid"] == 5555


def test_resume_orphaned_ota_running_uses_canonical_reconcile_before_retry(tmp_path, monkeypatch):
    cfg = _profile(tmp_path)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(cfg))
    work = Path(cfg["workdir"])
    work.mkdir()
    (work / "ACTIVE_LOCK.json").write_text(json.dumps({"phase": "ota_running"}))
    monkeypatch.setattr(sup.campaign, "load_profile", lambda _p: dict(cfg))
    monkeypatch.setattr(sup, "process_alive", lambda _pid: False)
    calls = []

    def fake_run(cmd, log):
        mode = cmd[cmd.index("--mode") + 1]
        calls.append(mode)
        if mode == "reconcile-source":
            (work / "ACTIVE_LOCK.json").write_text(json.dumps({"phase": "source_unchanged_reconciled"}))
        return 0

    monkeypatch.setattr(sup, "run_logged", fake_run)
    monkeypatch.setattr(sup, "launch_logged", lambda cmd, log: 7777)
    result = sup.resume_transition(
        profile_path, cfg["ieee"], confirm_unloaded=True,
        reconcile_wait_seconds=0, reconcile_retry_seconds=1,
    )
    assert calls == ["reconcile-source", "qualify"]
    assert result["pid"] == 7777


def test_reconcile_wait_is_bounded_and_never_launches_without_proof(tmp_path, monkeypatch):
    cfg = _profile(tmp_path)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(cfg))
    work = Path(cfg["workdir"])
    work.mkdir()
    (work / "ACTIVE_LOCK.json").write_text(json.dumps({"phase": "ota_running"}))
    monkeypatch.setattr(sup.campaign, "load_profile", lambda _p: dict(cfg))
    monkeypatch.setattr(sup, "process_alive", lambda _pid: False)
    monkeypatch.setattr(sup, "run_logged", lambda cmd, log: 1)
    with pytest.raises(RuntimeError, match="Source reconciliation did not become safe"):
        sup.resume_transition(
            profile_path, cfg["ieee"], confirm_unloaded=True,
            reconcile_wait_seconds=0, reconcile_retry_seconds=1,
        )


def test_resume_requires_nonpm_unloaded_confirmation(tmp_path, monkeypatch):
    cfg = _profile(tmp_path)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(cfg))
    monkeypatch.setattr(sup.campaign, "load_profile", lambda _p: dict(cfg))

    with pytest.raises(ValueError, match="load-unplugged"):
        sup.resume_transition(profile_path, cfg["ieee"],
                              confirm_unloaded=False)


def test_status_warns_stale_observer_is_not_transport_failure(tmp_path, monkeypatch):
    cfg = _profile(tmp_path)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(cfg))
    work = Path(cfg["workdir"])
    work.mkdir()
    (work / "ACTIVE_LOCK.json").write_text(json.dumps({"phase": "ota_running"}))
    (work / "LIVE_STATUS.json").write_text(json.dumps({"phase": "ota_running", "update": {"progress": 50.38}}))
    (work / "ota_bseed-ota-token.jsonl").write_text(json.dumps({"event": "ota_pending"}) + "\n")
    monkeypatch.setattr(sup.campaign, "load_profile", lambda _p: dict(cfg))

    result = sup.status(profile_path)
    assert result["campaign_lock"]["phase"] == "ota_running"
    assert result["live_status"]["update"]["progress"] == 50.38
    assert "NOT proof" in result["warning"]


def test_fresh_get_timeout_runs_scoped_source_rejoin_once_then_reconciles(tmp_path, monkeypatch):
    cfg = _profile(tmp_path)
    cfg["join_via"] = "BedroomSocketCabinetL"
    cfg["join_seconds"] = 120
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(cfg))
    work = Path(cfg["workdir"])
    work.mkdir()
    (work / "ACTIVE_LOCK.json").write_text(json.dumps({"phase": "ota_running"}))
    monkeypatch.setattr(sup.campaign, "load_profile", lambda _p: dict(cfg))
    monkeypatch.setattr(sup, "process_alive", lambda _pid: False)
    calls = []
    reconcile_count = 0

    def fake_run(cmd, log):
        nonlocal reconcile_count
        name = Path(cmd[2]).name
        if name == "bseed_source_rejoin_recovery.py":
            calls.append("source-rejoin")
            return 0
        mode = cmd[cmd.index("--mode") + 1]
        calls.append(mode)
        if mode == "reconcile-source":
            reconcile_count += 1
            if reconcile_count == 1:
                log.write_text(
                    "=== RUN ===\nTimeoutError: Fresh target GET response missing\n=== EXIT 1 ===\n"
                )
                return 1
            (work / "ACTIVE_LOCK.json").write_text(
                json.dumps({"phase": "source_unchanged_reconciled"})
            )
            return 0
        return 0

    monkeypatch.setattr(sup, "run_logged", fake_run)
    monkeypatch.setattr(sup, "launch_logged", lambda cmd, log: 8888)

    result = sup.resume_transition(
        profile_path, cfg["ieee"], confirm_unloaded=True,
        reconcile_wait_seconds=30, reconcile_retry_seconds=1,
    )

    assert calls == ["reconcile-source", "source-rejoin", "reconcile-source", "qualify"]
    assert result["pid"] == 8888


def test_non_link_reconcile_failure_does_not_open_source_rejoin(tmp_path, monkeypatch):
    cfg = _profile(tmp_path)
    cfg["join_via"] = "BedroomSocketCabinetL"
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(cfg))
    work = Path(cfg["workdir"])
    work.mkdir()
    (work / "ACTIVE_LOCK.json").write_text(json.dumps({"phase": "ota_running"}))
    monkeypatch.setattr(sup.campaign, "load_profile", lambda _p: dict(cfg))
    calls = []

    def fake_run(cmd, log):
        calls.append(Path(cmd[2]).name)
        log.write_text("=== RUN ===\nValueError: Campaign lock identity/hash mismatch\n=== EXIT 1 ===\n")
        return 1

    monkeypatch.setattr(sup, "run_logged", fake_run)
    with pytest.raises(RuntimeError, match="Source reconciliation did not become safe"):
        sup.reconcile_until_ready(
            profile_path, cfg["ieee"], work, work / "orchestration.log",
            wait_seconds=0, retry_seconds=1,
        )
    assert calls == ["bseed_ota_campaign.py"]


def test_failed_rejoin_keeps_lock_and_never_qualifies_or_launches(tmp_path, monkeypatch):
    cfg = _profile(tmp_path)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(cfg))
    work = Path(cfg["workdir"])
    work.mkdir()
    lock_path = work / "ACTIVE_LOCK.json"
    lock_path.write_text(json.dumps({"phase": "ota_running"}))
    original_lock = lock_path.read_bytes()
    monkeypatch.setattr(sup.campaign, "load_profile", lambda _p: dict(cfg))
    monkeypatch.setattr(sup, "process_alive", lambda _pid: False)
    calls = []

    def fake_run(cmd, log):
        name = Path(cmd[2]).name
        calls.append(name)
        if name == "bseed_ota_campaign.py":
            log.write_text(
                "=== RUN ===\nTimeoutError: Fresh target GET response missing\n=== EXIT 1 ===\n"
            )
        else:
            output = Path(cmd[cmd.index("--output") + 1])
            output.write_text(json.dumps({
                "result": "unconfirmed",
                "fatal_error": "JOIN_CLOSE_UNCONFIRMED:all",
                "attempts": [{"close_response": {"status": "error"}}],
            }))
        return 1

    monkeypatch.setattr(sup, "run_logged", fake_run)
    monkeypatch.setattr(sup, "launch_logged", lambda *args: pytest.fail("unsafe OTA launch"))
    with pytest.raises(RuntimeError, match="permit-join closure is unconfirmed") as error:
        sup.resume_transition(
            profile_path, cfg["ieee"], confirm_unloaded=True,
            reconcile_wait_seconds=0, join_strategy="all",
        )

    assert calls == ["bseed_ota_campaign.py", "bseed_source_rejoin_recovery.py"]
    assert lock_path.read_bytes() == original_lock
    evidence = next(work.glob("source_rejoin_*.json"))
    assert str(evidence) in str(error.value)
    assert not (work / sup.SUPERVISOR_FILE).exists()


def test_join_strategy_plan_is_generic_and_fail_closed():
    assert rejoin.resolve_join_plan(
        "none", router_available=False, allow_join_all_fallback=False
    ) == []
    assert rejoin.resolve_join_plan(
        "all", router_available=False, allow_join_all_fallback=False
    ) == ["all"]
    assert rejoin.resolve_join_plan(
        "scoped", router_available=True, allow_join_all_fallback=False
    ) == ["scoped"]
    assert rejoin.resolve_join_plan(
        "coordinator", router_available=False, allow_join_all_fallback=False
    ) == ["coordinator"]
    assert rejoin.resolve_join_plan(
        "auto", router_available=True, allow_join_all_fallback=False
    ) == ["scoped", "coordinator"]
    assert rejoin.resolve_join_plan(
        "auto", router_available=True, allow_join_all_fallback=True
    ) == ["scoped", "coordinator", "all"]
    assert rejoin.resolve_join_plan(
        "auto", router_available=False, allow_join_all_fallback=False
    ) == ["coordinator"]
    assert rejoin.resolve_join_plan(
        "auto", router_available=False, allow_join_all_fallback=True
    ) == ["coordinator", "all"]
    with pytest.raises(ValueError, match="no verified join_via"):
        rejoin.resolve_join_plan(
            "scoped", router_available=False, allow_join_all_fallback=True
        )


def test_join_all_payload_omits_router_and_scoped_payload_names_it():
    all_payload = rejoin.permit_payload(
        "all", seconds=120, transaction="tx-all", router_name=None
    )
    assert all_payload == {"time": 120, "transaction": "tx-all"}

    scoped_payload = rejoin.permit_payload(
        "scoped",
        seconds=120,
        transaction="tx-scoped",
        router_name="AnyVerifiedRouter",
    )
    assert scoped_payload == {
        "time": 120,
        "transaction": "tx-scoped",
        "device": "AnyVerifiedRouter",
    }

    coordinator_payload = rejoin.permit_payload(
        "coordinator", seconds=120, transaction="tx-coordinator", router_name=None
    )
    assert coordinator_payload == {
        "time": 120,
        "transaction": "tx-coordinator",
        "device": "coordinator",
    }

    assert rejoin.adapter_transport_error({
        "status": "error",
        "error": "SRSP - ZDO - mgmtPermitJoinReq after 6000ms",
    }) is True


def test_supervisor_source_rejoin_command_carries_policy(tmp_path):
    cmd = sup.source_rejoin_cmd(
        tmp_path / "profile.json",
        "0x0011223344556677",
        tmp_path / "evidence.json",
        join_strategy="auto",
        allow_join_all_fallback=True,
    )
    assert cmd[cmd.index("--join-strategy") + 1] == "auto"
    assert "--allow-join-all-fallback" in cmd


def test_resume_records_explicit_join_all_policy(tmp_path, monkeypatch):
    cfg = _profile(tmp_path)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(cfg))
    work = Path(cfg["workdir"])
    work.mkdir()
    (work / "ACTIVE_LOCK.json").write_text(
        json.dumps({"phase": "source_unchanged_reconciled"})
    )
    monkeypatch.setattr(sup.campaign, "load_profile", lambda _p: dict(cfg))
    monkeypatch.setattr(sup, "process_alive", lambda _pid: False)
    monkeypatch.setattr(sup, "run_logged", lambda cmd, log: 0)
    monkeypatch.setattr(sup, "launch_logged", lambda cmd, log: 9191)

    result = sup.resume_transition(
        profile_path,
        cfg["ieee"],
        confirm_unloaded=True,
        join_strategy="all",
        allow_join_all_fallback=False,
    )

    assert result["recovery_policy"]["join_strategy"] == "all"
    assert result["recovery_policy"]["allow_join_all_fallback"] is False
    persisted = json.loads((work / sup.SUPERVISOR_FILE).read_text())
    assert persisted["recovery_policy"]["join_strategy"] == "all"


def test_preflight_recovery_accepts_only_exact_scoped_source():
    profile = {
        "device": "KitchenSocketLeft",
        "ieee": "0xa4c138241e3de538",
        "manufacturer": "b28wrpvx",
        "model": "TS011F-BS-PM",
        "preflash_role": "EndDevice",
        "preflash_build": "1.2.5-bseedcli12",
        "join_via": "KitchenSocketRight",
    }
    target = {
        "friendly_name": profile["device"],
        "ieee_address": profile["ieee"],
        "manufacturer": profile["manufacturer"],
        "model_id": profile["model"],
        "type": profile["preflash_role"],
        "software_build_id": profile["preflash_build"],
        "interview_state": "SUCCESSFUL",
    }
    rejoin.validate_preflight_recovery_state(
        target,
        profile,
        strategy="scoped",
        allow_global=False,
        campaign_lock_exists=False,
        network_lock_exists=False,
    )


@pytest.mark.parametrize(
    "overrides, match",
    [
        ({"strategy": "all"}, "limited to scoped"),
        ({"allow_global": True}, "never allows Join All"),
        ({"campaign_lock_exists": True}, "existing campaign lock"),
        ({"network_lock_exists": True}, "active network OTA lock"),
    ],
)
def test_preflight_recovery_rejects_broad_or_locked_paths(overrides, match):
    profile = {
        "device": "KitchenSocketLeft",
        "ieee": "0xa4c138241e3de538",
        "manufacturer": "b28wrpvx",
        "model": "TS011F-BS-PM",
        "preflash_role": "EndDevice",
        "preflash_build": "1.2.5-bseedcli12",
        "join_via": "KitchenSocketRight",
    }
    target = {
        "friendly_name": profile["device"],
        "ieee_address": profile["ieee"],
        "manufacturer": profile["manufacturer"],
        "model_id": profile["model"],
        "type": profile["preflash_role"],
        "software_build_id": profile["preflash_build"],
        "interview_completed": True,
    }
    kwargs = dict(
        strategy="scoped",
        allow_global=False,
        campaign_lock_exists=False,
        network_lock_exists=False,
    )
    kwargs.update(overrides)
    with pytest.raises(ValueError, match=match):
        rejoin.validate_preflight_recovery_state(target, profile, **kwargs)


def test_preflight_recovery_rejects_stale_cached_source_build():
    profile = {
        "device": "LivingRoomSocketHifiLeft",
        "ieee": "0xa4c138da1333dc70",
        "manufacturer": "b28wrpvx",
        "model": "TS011F-BS-PM",
        "preflash_role": "EndDevice",
        "preflash_build": "1.2.5-bseedcli6",
        "join_via": "LivingRoomSocketTableLeft",
    }
    target = {
        "friendly_name": profile["device"],
        "ieee_address": profile["ieee"],
        "manufacturer": profile["manufacturer"],
        "model_id": profile["model"],
        "type": "EndDevice",
        "software_build_id": "1.2.5-bseedcli12",
        "interview_completed": True,
    }
    with pytest.raises(ValueError, match="build differs"):
        rejoin.validate_preflight_recovery_state(
            target,
            profile,
            strategy="scoped",
            allow_global=False,
            campaign_lock_exists=False,
            network_lock_exists=False,
        )


def _same_role_profile(tmp_path):
    image = tmp_path / "same-role.ota"
    image.write_bytes(b"fixture")
    work = tmp_path / "same-role-work"
    return {
        "device": "LivingRoomSocketHifiLeft",
        "ieee": "0xa4c138da1333dc70",
        "manufacturer": "b28wrpvx",
        "model": "TS011F-BS-PM",
        "preflash_role": "EndDevice",
        "preflash_build": "1.2.5-bseedcli6",
        "postflash_role": "EndDevice",
        "postflash_build": "1.2.5-bseedcli14",
        "image": str(image),
        "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        "workdir": str(work),
        "require_pm": True,
        "non_pm": False,
    }


def _ota_progress_log(path, progress):
    rows = [
        {"event": "ota_request_sent", "value": {"transaction": path.stem}},
        {"event": "device_state", "value": {"update": {"state": "updating", "progress": progress}}},
        {"event": "ota_final", "value": {"phase": "update_error"}},
    ]
    path.write_text("\n".join(json.dumps(x) for x in rows) + "\n")


def test_progress_gate_requires_strict_increase(tmp_path):
    check = tmp_path / "ota_bseed-ota-check.jsonl"
    check.write_text(json.dumps({"event": "check_passed_no_flash", "value": {}}) + "\n")
    assert sup.ota_attempt_progress(check) is None

    one = [{"max_progress": 0.59}]
    assert sup.progress_retry_gate(one) == {
        "allow_retry": True,
        "previous_progress": 0.0,
        "current_progress": 0.59,
        "strictly_improved": True,
    }
    assert sup.progress_retry_gate(one + [{"max_progress": 1.2}])["allow_retry"] is True
    stalled = sup.progress_retry_gate(one + [{"max_progress": 0.59}])
    assert stalled["allow_retry"] is False
    assert stalled["strictly_improved"] is False


def test_same_role_autoresume_stops_when_progress_no_longer_increases(tmp_path, monkeypatch):
    cfg = _same_role_profile(tmp_path)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(cfg))
    work = Path(cfg["workdir"])
    work.mkdir()
    (work / "ACTIVE_LOCK.json").write_text(json.dumps({
        "phase": "update_error",
        "device": cfg["device"],
        "ieee": cfg["ieee"],
        "sha256": cfg["sha256"],
    }))
    _ota_progress_log(work / "ota_bseed-ota-attempt1.jsonl", 0.59)

    monkeypatch.setattr(sup.campaign, "load_profile", lambda _p: dict(cfg))
    monkeypatch.setattr(sup, "process_alive", lambda _pid: False)
    flash_count = 0

    def fake_run(cmd, log):
        nonlocal flash_count
        name = Path(cmd[2]).name
        if name == "bseed_ota_source_reconcile.py":
            assert "--defer-candidate-check" in cmd
            assert cmd[cmd.index("--observe-seconds") + 1] == "15"
            (work / "ACTIVE_LOCK.json").write_text(json.dumps({"phase": sup.READY_PHASE}))
            return 0
        mode = cmd[cmd.index("--mode") + 1]
        if mode in ("preflight", "check"):
            return 0
        if mode == "flash":
            flash_count += 1
            progress = 1.2
            _ota_progress_log(work / f"ota_bseed-ota-attempt{flash_count + 1}.jsonl", progress)
            (work / "ACTIVE_LOCK.json").write_text(json.dumps({"phase": "update_error"}))
            return 1
        raise AssertionError(mode)

    monkeypatch.setattr(sup, "run_logged", fake_run)
    result = sup.resume_same_role(
        profile_path,
        cfg["ieee"],
        confirm_unloaded=False,
        max_attempts=8,
        reconcile_wait_seconds=0,
        reconcile_retry_seconds=1,
    )

    assert flash_count == 2
    assert result["state"] == "progress_not_improved"
    assert result["retry_progress_gate"]["previous_progress"] == 1.2
    assert result["retry_progress_gate"]["current_progress"] == 1.2
    assert result["retry_progress_gate"]["allow_retry"] is False


def test_same_role_autoresume_does_not_retry_zero_progress_failure(tmp_path, monkeypatch):
    cfg = _same_role_profile(tmp_path)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(cfg))
    work = Path(cfg["workdir"])
    work.mkdir()
    (work / "ACTIVE_LOCK.json").write_text(json.dumps({"phase": "update_error"}))
    _ota_progress_log(work / "ota_bseed-ota-attempt1.jsonl", 0.0)

    monkeypatch.setattr(sup.campaign, "load_profile", lambda _p: dict(cfg))
    monkeypatch.setattr(sup, "process_alive", lambda _pid: False)
    monkeypatch.setattr(
        sup,
        "run_logged",
        lambda *args, **kwargs: pytest.fail("zero-progress failure must not auto-retry"),
    )

    result = sup.resume_same_role(
        profile_path,
        cfg["ieee"],
        confirm_unloaded=False,
        max_attempts=8,
    )
    assert result["state"] == "progress_not_improved"
    assert result["retry_progress_gate"]["current_progress"] == 0.0
