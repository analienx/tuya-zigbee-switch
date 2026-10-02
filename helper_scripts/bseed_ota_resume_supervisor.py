"""BSEED OTA resume/retry supervisor.

Best-practice launcher for long-running OTA transitions:
- never leaves a long-running child attached to an unread PIPE;
- reconciles a failed source-unchanged campaign before retry;
- refreshes qualification immediately before transition launch;
- redirects stdout/stderr to a durable file;
- persists PID/profile/log metadata for later status checks;
- never treats stale observer output as proof that Zigbee2MQTT OTA stopped.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HELPERS = ROOT / "helper_scripts"
if str(HELPERS) not in sys.path:
    sys.path.insert(0, str(HELPERS))

import bseed_ota_campaign as campaign

SUPERVISOR_FILE = "OTA_SUPERVISOR.json"
RECONCILABLE_PHASES = {"ota_running", "update_error", "update_timeout_or_unconfirmed"}
READY_PHASE = "source_unchanged_reconciled"
JOIN_STRATEGIES = ("none", "scoped", "coordinator", "all", "auto")


def now() -> str:
    return dt.datetime.now().astimezone().isoformat()


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf8")
    tmp.replace(path)


def process_alive(pid: int | str | None) -> bool:
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def file_age_seconds(path: Path) -> float | None:
    try:
        return max(0.0, dt.datetime.now().timestamp() - path.stat().st_mtime)
    except OSError:
        return None


def campaign_cmd(profile_path: Path, mode: str, *, confirm_ieee: str | None = None,
                 confirm_unloaded: bool = False, accept_risk: bool = False) -> list[str]:
    cmd = [sys.executable, "-u", str(HELPERS / "bseed_ota_campaign.py"),
           "--profile", str(profile_path), "--mode", mode]
    if confirm_ieee:
        cmd += ["--confirm-ieee", confirm_ieee]
    if confirm_unloaded:
        cmd.append("--confirm-load-unplugged")
    if accept_risk:
        cmd.append("--accept-nonrecoverable-ota-risk")
    return cmd


def run_logged(cmd: list[str], log_path: Path) -> int:
    """Run a bounded orchestration step with output redirected to disk, never PIPE."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf8", buffering=1) as handle:
        handle.write(f"\n=== {now()} RUN {json.dumps(cmd)} ===\n")
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        handle.write(f"=== {now()} EXIT {proc.returncode} ===\n")
        return int(proc.returncode)


def launch_logged(cmd: list[str], log_path: Path) -> int:
    """Launch long-running OTA with inherited file handles, never unread pipes."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf8", buffering=1)
    handle.write(f"\n=== {now()} LAUNCH {json.dumps(cmd)} ===\n")
    kwargs: dict[str, Any] = dict(
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        close_fds=True,
    )
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    finally:
        # The child inherited its own file handle. Parent must not keep one around.
        handle.close()
    return int(proc.pid)


def confirm_transition_started(work: Path, pid: int, previous_token: str | None, *,
                               timeout_seconds: float = 10.0) -> dict[str, Any]:
    """Require concrete campaign ownership after spawning the transition child.

    Popen returning a PID is not evidence that the OTA runner actually started.
    Success requires a fresh ota_running lock/token while the child remains alive.
    """
    deadline = time.monotonic() + timeout_seconds
    last_lock = None
    while time.monotonic() < deadline:
        last_lock = read_json(work / "ACTIVE_LOCK.json")
        if (last_lock and last_lock.get("phase") == "ota_running" and
                last_lock.get("token") and last_lock.get("token") != previous_token):
            return last_lock
        if not process_alive(pid):
            raise RuntimeError(
                f"Transition child PID {pid} exited before acquiring a fresh ota_running lock"
            )
        time.sleep(0.2)
    raise RuntimeError(
        "Transition child did not acquire a fresh ota_running lock within "
        f"{timeout_seconds:.1f}s; last_lock={last_lock!r}"
    )


def latest_ota_jsonl(work: Path) -> Path | None:
    files = sorted(work.glob("ota_bseed-ota-*.jsonl"),
                   key=lambda p: p.stat().st_mtime if p.exists() else 0,
                   reverse=True)
    return files[0] if files else None


def tail_jsonl(path: Path | None, limit: int = 8) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf8", errors="replace").splitlines()[-limit:]:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def status(profile_path: Path) -> dict[str, Any]:
    profile = campaign.load_profile(profile_path)
    work = Path(profile["workdir"])
    supervisor = read_json(work / SUPERVISOR_FILE)
    lock = read_json(work / "ACTIVE_LOCK.json")
    live = read_json(work / "LIVE_STATUS.json")
    ota_log = latest_ota_jsonl(work)
    pid = supervisor.get("pid") if supervisor else None
    return {
        "at": now(),
        "profile": str(profile_path),
        "device": profile["device"],
        "ieee": profile["ieee"],
        "supervisor": supervisor,
        "supervisor_process_alive": process_alive(pid if isinstance(pid, int) else None),
        "campaign_lock": lock,
        "live_status": live,
        "latest_ota_jsonl": str(ota_log) if ota_log else None,
        "latest_ota_jsonl_age_seconds": file_age_seconds(ota_log) if ota_log else None,
        "latest_ota_events": tail_jsonl(ota_log),
        "warning": (
            "Stale supervisor/JSONL output is observer evidence only; it is NOT proof "
            "that Zigbee2MQTT OTA transport stopped. Verify live device/update state "
            "before terminating or reconciling an ota_running campaign."
        ),
    }


def _last_logged_run(log_path: Path) -> str:
    try:
        text = log_path.read_text(encoding="utf8", errors="replace")
    except OSError:
        return ""
    # run_logged writes one RUN marker, body, then one EXIT marker. Slice from
    # the marker immediately preceding the final marker so error matching only
    # considers the most recent orchestration step and is format-tolerant.
    final = text.rfind("=== ")
    if final < 0:
        return text
    previous = text.rfind("=== ", 0, final)
    return text[previous if previous >= 0 else 0:]


def source_rejoin_cmd(profile_path: Path, confirm_ieee: str, output: Path, *,
                      join_strategy: str, allow_join_all_fallback: bool) -> list[str]:
    cmd = [
        sys.executable, "-u", str(HELPERS / "bseed_source_rejoin_recovery.py"),
        "--profile", str(profile_path),
        "--confirm-ieee", confirm_ieee,
        "--output", str(output),
        "--join-strategy", join_strategy,
    ]
    if allow_join_all_fallback:
        cmd.append("--allow-join-all-fallback")
    return cmd


def reconcile_until_ready(profile_path: Path, confirm_ieee: str, work: Path,
                          orchestration_log: Path, *, wait_seconds: int,
                          retry_seconds: int, join_strategy: str = "auto",
                          allow_join_all_fallback: bool = False) -> str | None:
    """Use canonical reconciliation until an orphaned/failed source is proven ready.

    Every attempt is read-only with respect to firmware. The canonical helper
    itself refuses while OTA is still active, while the target is unreachable,
    or when exact source identity/candidate availability cannot be proven.
    """
    deadline = time.monotonic() + max(0, wait_seconds)
    attempts = 0
    source_rejoin_attempted = False
    while True:
        lock = read_json(work / "ACTIVE_LOCK.json")
        phase = lock.get("phase") if lock else None
        if phase in (None, READY_PHASE):
            return phase
        if phase not in ("ota_running", *RECONCILABLE_PHASES):
            raise RuntimeError(f"Campaign phase {phase!r} is not reconcilable")
        attempts += 1
        rc = run_logged(
            campaign_cmd(profile_path, "reconcile-source", confirm_ieee=confirm_ieee),
            orchestration_log,
        )
        lock = read_json(work / "ACTIVE_LOCK.json")
        phase = lock.get("phase") if lock else None
        if rc == 0 and phase == READY_PHASE:
            return phase
        if (rc != 0 and join_strategy != "none" and not source_rejoin_attempted and
                "Fresh target GET response missing" in _last_logged_run(orchestration_log)):
            source_rejoin_attempted = True
            stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S%f")
            evidence = work / f"source_rejoin_{stamp}.json"
            rejoin_rc = run_logged(
                source_rejoin_cmd(
                    profile_path,
                    confirm_ieee,
                    evidence,
                    join_strategy=join_strategy,
                    allow_join_all_fallback=allow_join_all_fallback,
                ),
                orchestration_log,
            )
            if rejoin_rc != 0:
                raise RuntimeError(
                    f"Source rejoin recovery failed with exit {rejoin_rc}; "
                    f"permit-join closure is unconfirmed until evidence in {evidence} "
                    "and live bridge state are checked; OTA resume remains blocked"
                )
            continue
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Source reconciliation did not become safe after {attempts} attempt(s); "
                f"last phase={phase!r}, exit={rc}"
            )
        time.sleep(max(1, retry_seconds))


def resume_transition(profile_path: Path, confirm_ieee: str, *,
                      confirm_unloaded: bool, accept_risk: bool,
                      reconcile_wait_seconds: int = 600,
                      reconcile_retry_seconds: int = 20,
                      join_strategy: str | None = None,
                      allow_join_all_fallback: bool | None = None) -> dict[str, Any]:
    profile = campaign.load_profile(profile_path)
    resolved_join_strategy = join_strategy or profile.get("source_rejoin_strategy", "auto")
    if resolved_join_strategy not in JOIN_STRATEGIES:
        raise ValueError("join strategy must be one of none/scoped/coordinator/all/auto")
    resolved_global_fallback = (
        bool(profile.get("allow_join_all_fallback", False))
        if allow_join_all_fallback is None else bool(allow_join_all_fallback)
    )
    if confirm_ieee != profile["ieee"]:
        raise ValueError("Exact IEEE confirmation mismatch")
    if profile["preflash_role"] == profile["postflash_role"]:
        raise ValueError("Resume supervisor is for cross-role transition campaigns")
    if profile.get("non_pm") is True and not (confirm_unloaded and accept_risk):
        raise ValueError("Non-PM transition requires load-unplugged and nonrecoverable-risk confirmations")

    work = Path(profile["workdir"])
    work.mkdir(parents=True, exist_ok=True)
    supervisor_path = work / SUPERVISOR_FILE
    previous_supervisor = read_json(supervisor_path)
    if previous_supervisor and process_alive(previous_supervisor.get("pid")):
        raise RuntimeError(
            f"Existing supervised process PID {previous_supervisor.get('pid')} is still alive; "
            "refusing duplicate OTA launch"
        )

    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    orchestration_log = work / f"resume_supervisor_{stamp}.log"
    lock = read_json(work / "ACTIVE_LOCK.json")
    phase = lock.get("phase") if lock else None
    if phase not in (None, READY_PHASE):
        phase = reconcile_until_ready(
            profile_path, confirm_ieee, work, orchestration_log,
            wait_seconds=reconcile_wait_seconds,
            retry_seconds=reconcile_retry_seconds,
            join_strategy=resolved_join_strategy,
            allow_join_all_fallback=resolved_global_fallback,
        )
    if phase not in (None, READY_PHASE):
        raise RuntimeError(f"Campaign phase {phase!r} is not safe for an automated resume")

    rc = run_logged(campaign_cmd(profile_path, "qualify"), orchestration_log)
    if rc:
        raise RuntimeError(f"Fresh qualification failed with exit {rc}")

    transition_cmd = campaign_cmd(
        profile_path,
        "transition",
        confirm_ieee=confirm_ieee,
        confirm_unloaded=confirm_unloaded,
        accept_risk=accept_risk,
    )
    transition_log = work / f"transition_{stamp}.log"
    record = {
        "schema": 1,
        "state": "launching",
        "created_at": now(),
        "device": profile["device"],
        "ieee": profile["ieee"],
        "profile": str(profile_path),
        "profile_sha256": hashlib.sha256(profile_path.read_bytes()).hexdigest(),
        "image_sha256": profile["sha256"],
        "recovery_policy": {
            "join_strategy": resolved_join_strategy,
            "allow_join_all_fallback": resolved_global_fallback,
            "join_via": profile.get("join_via"),
            "join_seconds": profile.get("join_seconds", 120),
        },
        "orchestration_log": str(orchestration_log),
        "transition_log": str(transition_log),
        "launch_contract": {
            "stdout": "durable file",
            "stderr": "redirected to stdout file",
            "stdin": "DEVNULL",
            "stdout_pipe": False,
            "qualification_immediately_before_launch": True,
        },
    }
    write_json_atomic(supervisor_path, record)
    previous_token = lock.get("token") if lock else None
    pid = launch_logged(transition_cmd, transition_log)
    record.update(state="starting", launched_at=now(), pid=pid)
    write_json_atomic(supervisor_path, record)
    try:
        fresh_lock = confirm_transition_started(work, pid, previous_token)
    except Exception as error:
        record.update(state="launch_failed", launch_failed_at=now(), launch_error=repr(error))
        write_json_atomic(supervisor_path, record)
        raise
    record.update(
        state="running",
        confirmed_at=now(),
        campaign_token=fresh_lock.get("token"),
        campaign_started=fresh_lock.get("started"),
    )
    write_json_atomic(supervisor_path, record)
    return record


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    resume = sub.add_parser("resume-transition")
    resume.add_argument("--profile", required=True)
    resume.add_argument("--confirm-ieee", required=True)
    resume.add_argument("--confirm-load-unplugged", action="store_true")
    resume.add_argument("--accept-nonrecoverable-ota-risk", action="store_true")
    resume.add_argument("--reconcile-wait-seconds", type=int, default=600)
    resume.add_argument("--reconcile-retry-seconds", type=int, default=20)
    resume.add_argument("--join-strategy", choices=JOIN_STRATEGIES)
    resume.add_argument("--allow-join-all-fallback", action="store_true", default=None)

    show = sub.add_parser("status")
    show.add_argument("--profile", required=True)

    args = parser.parse_args(argv)
    profile_path = Path(args.profile).expanduser().resolve()
    if args.command == "status":
        print(json.dumps(status(profile_path), indent=2, default=str))
        return
    result = resume_transition(
        profile_path,
        args.confirm_ieee,
        confirm_unloaded=args.confirm_load_unplugged,
        accept_risk=args.accept_nonrecoverable_ota_risk,
        reconcile_wait_seconds=args.reconcile_wait_seconds,
        reconcile_retry_seconds=args.reconcile_retry_seconds,
        join_strategy=args.join_strategy,
        allow_join_all_fallback=args.allow_join_all_fallback,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
