import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper_scripts"))
import bseed_ota_resume_supervisor as sup


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
        accept_risk=True,
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
        profile_path, cfg["ieee"], confirm_unloaded=True, accept_risk=True,
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
            profile_path, cfg["ieee"], confirm_unloaded=True, accept_risk=True,
            reconcile_wait_seconds=0, reconcile_retry_seconds=1,
        )


def test_resume_requires_nonpm_risk_and_unloaded_confirmation(tmp_path, monkeypatch):
    cfg = _profile(tmp_path)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(cfg))
    monkeypatch.setattr(sup.campaign, "load_profile", lambda _p: dict(cfg))

    with pytest.raises(ValueError, match="load-unplugged"):
        sup.resume_transition(profile_path, cfg["ieee"],
                              confirm_unloaded=False, accept_risk=True)
    with pytest.raises(ValueError, match="nonrecoverable-risk"):
        sup.resume_transition(profile_path, cfg["ieee"],
                              confirm_unloaded=True, accept_risk=False)


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
