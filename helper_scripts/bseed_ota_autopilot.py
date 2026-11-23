#!/usr/bin/env python3
"""Unattended same-role OTA autopilot: resume interrupted transfers until installed.

One command per socket takes a planned same-role campaign from wherever it is
to `postflash_candidate`, and records why every attempt ended:

    python helper_scripts/bseed_ota_autopilot.py run --profile P --confirm-ieee 0x...
    python helper_scripts/bseed_ota_autopilot.py status --profile P

Each loop:
- an interrupted transfer (update_error / timeout) is reconciled. It uses the
  fast source check, with no rejoin or permit-join in two-/three-slot mode;
- preflight and check are retried briefly, because a weak route can drop one
  read-only GET;
- the campaign's own `flash` runs, and the attempt's progress, duration and
  Zigbee2MQTT error are recorded in OTA_AUTOPILOT.json;
- a finished transfer whose post-flash check was unconfirmed (e.g. an idle
  meter) gets a bounded number of `postflash` re-runs.

The autopilot stops at the first of:
- `postflash_candidate`;
- a non-recoverable phase;
- more than `--max-stagnant` consecutive attempts that do not beat the best
  progress so far;
- the attempt budget.

It never releases a lease (that needs the owner's words), never changes roles,
never edits the profile, and refuses to touch a transfer another process is
running (`ota_running`).

Why it exists (2026-11-21): KitchenSocketLeft and HifiLeft needed 11 and 7
attempts. Each attempt moved data for 2-12 min. The Telink client then aborted
after ~50 s of lost block responses (5 s x 10 retries), Zigbee2MQTT waited out
`request_timeout_ms` (900 s), and each next attempt was rebuilt by hand
40-60 min later. Keep `request_timeout_ms` at the 300000 canary default so an
abort is detected in 5 min (docs/bseed_ota_timeout_evidence_policy_20261010.md).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
HELPERS = ROOT / "helper_scripts"
if str(HELPERS) not in sys.path:
    sys.path.insert(0, str(HELPERS))

import bseed_ota_resume_supervisor as sup  # noqa: E402

STATE_FILE = "OTA_AUTOPILOT.json"
STOP_FILE = "AUTOPILOT_STOP"
INSTALLED = ("postflash_candidate", "postflash_accepted")
POSTFLASH_PENDING = "ota_transfer_ok_postflash_unverified"
OWNER_STALL_TIMEOUT_MS = 300000
FORCE_MIN_TIMEOUT_MS = 1800000


def multi_slot(profile: dict[str, Any]) -> bool:
    return int(profile.get("parallel_ota_max", 1) or 1) > 1


def validate_policy(profile: dict[str, Any]) -> list[str]:
    """Refuse unsafe profiles; return advisories for slow-but-legal ones."""
    if profile["preflash_role"] != profile["postflash_role"]:
        raise ValueError("Autopilot is same-role only; role changes use resume-transition")
    if multi_slot(profile):
        if profile.get("source_rejoin_strategy") != "none":
            raise ValueError("Multi-slot OTA requires source_rejoin_strategy=none (no live rejoin)")
        if profile.get("allow_join_all_fallback", False) is not False:
            raise ValueError("Multi-slot OTA cannot open permit-join-all")
    advisories = []
    timeout = int(profile.get("request_timeout_ms") or 0)
    if timeout > OWNER_STALL_TIMEOUT_MS and timeout < FORCE_MIN_TIMEOUT_MS:
        advisories.append(
            "request_timeout_ms=%d: every client abort idles %d min before it is detected; "
            "the owner's canary default is %d" % (timeout, timeout // 60000, OWNER_STALL_TIMEOUT_MS))
    return advisories


def join_strategy(profile: dict[str, Any]) -> str:
    """No live rejoin while a peer OTA shares the coordinator."""
    return "none" if multi_slot(profile) else profile.get("source_rejoin_strategy", "auto")


def progress_gate(history: list[dict[str, Any]], max_stagnant: int,
                  min_gain: float = 0.0) -> dict[str, Any]:
    """Allow a retry while the best progress keeps rising, tolerating a few duds.

    An attempt that sends nothing (relay GET lost, peer check) or stalls before
    its previous offset is cheap with fast stall detection, so up to
    `max_stagnant` consecutive non-improving attempts are allowed. An attempt
    improves only when it beats the best by more than `min_gain` points, so a
    crawl of a fraction of a percent per attempt cannot keep it going forever.
    """
    best, stagnant = 0.0, 0
    for attempt in history:
        progress = float(attempt.get("max_progress") or 0.0)
        if progress > best + min_gain or (progress > best and best == 0.0):
            best, stagnant = progress, 0
        else:
            stagnant += 1
    return {"best_progress": best, "stagnant_attempts": stagnant,
            "allow_retry": stagnant <= max_stagnant}


Z2M_PROGRESS = re.compile(r"OTA update of '(0x[0-9a-f]{16})' at ([0-9.]+)%")


def log_progress(path: Path, ieee: str) -> float:
    """Highest percent Zigbee2MQTT logged for this socket (also covers attempts
    whose device_state never carried an update block)."""
    best = 0.0
    try:
        text = path.read_text(encoding="utf8", errors="replace")
    except OSError:
        return best
    for match in Z2M_PROGRESS.finditer(text):
        if match.group(1) == ieee.lower():
            best = max(best, float(match.group(2)))
    return best


def progress_history(work: Path, profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Per real attempt (OTA request sent): max of device_state and log progress."""
    history = sup.ota_progress_history(work, profile)
    for attempt in history:
        if attempt.get("log"):
            attempt["max_progress"] = max(float(attempt["max_progress"] or 0.0),
                                          log_progress(Path(attempt["log"]), profile["ieee"]))
    return history


def served_image_ok(profile: dict[str, Any], opener=urllib.request.urlopen) -> bool:
    try:
        with opener(profile["url"], timeout=30) as reply:
            return hashlib.sha256(reply.read()).hexdigest() == profile["sha256"]
    except OSError:
        return False


def republish(profile: dict[str, Any], site: dict[str, Any],
              call: Callable[..., int] = subprocess.call) -> None:
    """Re-upload the profile's own image and index (hash-checked) when the served copy is gone."""
    image = Path(profile["image"])
    if hashlib.sha256(image.read_bytes()).hexdigest() != profile["sha256"]:
        raise RuntimeError("local image differs from the profile sha256; not republishing")
    index = Path(profile["index_output"])
    if not index.is_file():
        raise RuntimeError("private index missing: " + str(index))
    folder = profile["url"].split("?")[0].rstrip("/").split("/")[-2]
    remote = site["publish"]["remote_dir"].rstrip("/") + "/" + folder
    alias = site["publish"]["ssh_alias"]
    for cmd in (["ssh", "-o", "BatchMode=yes", alias, "mkdir -p " + remote],
                ["scp", "-q", "-o", "BatchMode=yes", str(image), alias + ":" + remote + "/firmware.ota"],
                ["scp", "-q", "-o", "BatchMode=yes", str(index), alias + ":" + remote + "/index.json"]):
        if call(cmd, stdin=subprocess.DEVNULL):
            raise RuntimeError("republish step failed: " + " ".join(cmd[:2]))


def ota_logs(work: Path) -> set[Path]:
    return set(work.glob("ota_bseed-ota-*.jsonl"))


def refusal_reason(paths: set[Path]) -> str | None:
    """The campaign_error of a flash that sent nothing, if it logged one."""
    for path in sorted(paths):
        for line in path.read_text(encoding="utf8", errors="replace").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if item.get("event") == "campaign_error":
                return str((item.get("value") or {}).get("error"))[:300]
    return None


def attempt_summary(path: Path | None) -> dict[str, Any]:
    """First/last progress, duration and the Zigbee2MQTT error of one attempt log."""
    out: dict[str, Any] = {"log": str(path) if path else None}
    if not path or not path.exists():
        return out
    first = last = None
    start = end = None
    for line in path.read_text(encoding="utf8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        when = item.get("when")
        start = start or when
        end = when or end
        value = item.get("value") if isinstance(item.get("value"), dict) else {}
        update = value.get("update") if isinstance(value.get("update"), dict) else {}
        progress = update.get("progress")
        if isinstance(progress, (int, float)):
            first = progress if first is None else first
            last = progress
        if item.get("event") == "ota_final":
            response = value.get("response") or {}
            out["phase"] = value.get("phase")
            if response.get("error"):
                out["error"] = str(response["error"])[:300]
    out.update(started=start, ended=end, first_progress=first, last_progress=last)
    return out


def retry_step(step: Callable[[], int], retries: int, delay: float,
               sleep: Callable[[float], None] = time.sleep) -> tuple[int, int]:
    """Run a read-only step up to `retries` extra times; return (exit, tries)."""
    tries = 0
    while True:
        tries += 1
        rc = step()
        if rc == 0 or tries > retries:
            return rc, tries
        sleep(delay)


class Autopilot:
    def __init__(self, profile_path: Path, confirm_ieee: str, *, confirm_unloaded: bool = False,
                 max_attempts: int = 12, max_stagnant: int = 2, step_retries: int = 2,
                 step_retry_seconds: float = 30, postflash_retries: int = 2,
                 reconcile_wait_seconds: int = 900, reconcile_retry_seconds: int = 20,
                 site: dict[str, Any] | None = None, min_gain: float = 0.5,
                 peer_wait_seconds: float = 60, peer_wait_budget_seconds: float = 1800):
        self.profile_path = profile_path
        self.site = site
        self.min_gain = min_gain
        self.peer_wait_seconds, self.peer_wait_budget = peer_wait_seconds, peer_wait_budget_seconds
        self.waited = 0.0
        self.refusals: list[dict[str, Any]] = []
        self.confirm_ieee = confirm_ieee
        self.confirm_unloaded = confirm_unloaded
        self.max_attempts, self.max_stagnant = max_attempts, max_stagnant
        self.step_retries, self.step_retry_seconds = step_retries, step_retry_seconds
        self.postflash_retries = postflash_retries
        self.reconcile_wait_seconds = reconcile_wait_seconds
        self.reconcile_retry_seconds = reconcile_retry_seconds
        self.profile = sup.campaign.load_profile(profile_path)
        if confirm_ieee != self.profile["ieee"]:
            raise ValueError("Exact IEEE confirmation mismatch")
        from bseed_operator_load_policy import require_preserve_state_for_flash
        require_preserve_state_for_flash(self.profile)
        # No wattage/unplugged attestation is required for preserve-state OTA.
        self.advisories = validate_policy(self.profile)
        self.work = Path(self.profile["workdir"])
        stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        self.log = self.work / f"autopilot_{stamp}.log"
        self.attempts: list[dict[str, Any]] = []

    def phase(self) -> str | None:
        return (sup.read_json(self.work / "ACTIVE_LOCK.json") or {}).get("phase")

    def persist(self, state: str, **extra: Any) -> dict[str, Any]:
        history = progress_history(self.work, self.profile)
        record = {"schema": 1, "state": state, "updated_at": sup.now(),
                  "device": self.profile["device"], "ieee": self.profile["ieee"],
                  "profile": str(self.profile_path), "image_sha256": self.profile["sha256"],
                  "phase": self.phase(), "advisories": self.advisories,
                  "join_strategy": join_strategy(self.profile),
                  "progress_gate": progress_gate(history, self.max_stagnant, self.min_gain),
                  "attempts": self.attempts, "refusals": self.refusals,
                  "peer_wait_seconds_used": self.waited, "log": str(self.log), **extra}
        sup.write_json_atomic(self.work / STATE_FILE, record)
        print("AUTOPILOT", state, json.dumps({k: record[k] for k in ("phase", "progress_gate")}), flush=True)
        return record

    def run_campaign(self, mode: str, *, confirm: bool = False) -> int:
        return sup.run_logged(sup.campaign_cmd(
            self.profile_path, mode, confirm_ieee=self.confirm_ieee if confirm else None,
            confirm_unloaded=self.confirm_unloaded if confirm else False), self.log)

    def finish_postflash(self) -> dict[str, Any]:
        for _ in range(self.postflash_retries):
            self.run_campaign("postflash", confirm=True)
            if self.phase() in INSTALLED:
                return self.persist("installed")
        return self.persist("postflash_unconfirmed",
                            reason="transfer complete; post-flash checks still unconfirmed")

    def run(self) -> dict[str, Any]:
        self.persist("starting")
        while True:
            if (self.work / STOP_FILE).exists():
                return self.persist("stopped_by_operator", reason="stop file found; no attempt running")
            phase = self.phase()
            if phase in INSTALLED:
                return self.persist("installed")
            if phase == POSTFLASH_PENDING:
                return self.finish_postflash()
            if phase == "ota_running":
                return self.persist("blocked_active_transport",
                                    reason="another process owns a running transfer; prove it stopped first")
            if phase in sup.RECONCILABLE_PHASES:
                gate = progress_gate(progress_history(self.work, self.profile), self.max_stagnant,
                                     self.min_gain)
                if not gate["allow_retry"]:
                    return self.persist("progress_stalled", reason="no new best progress in %d attempts"
                                        % gate["stagnant_attempts"])
                self.persist("reconciling")
                try:
                    phase = sup.reconcile_until_ready(
                        self.profile_path, self.confirm_ieee, self.work, self.log,
                        wait_seconds=self.reconcile_wait_seconds, retry_seconds=self.reconcile_retry_seconds,
                        join_strategy=join_strategy(self.profile), allow_join_all_fallback=False, fast=True)
                except RuntimeError as error:
                    return self.persist("reconcile_failed", reason=str(error))
            elif phase not in (None, sup.READY_PHASE, sup.PENDING_PHASE, "preflight_abort"):
                # preflight_abort: the flash's own checks stopped before any
                # OTA was sent (e.g. a lost relay GET); safe to start again.
                return self.persist("stopped_nonrecoverable")
            if len(self.attempts) >= self.max_attempts:
                return self.persist("attempt_budget_exhausted")

            if not served_image_ok(self.profile):
                # Every preflight would refuse a 404 or a changed image.
                if not self.site:
                    return self.persist("image_not_served", url=self.profile["url"],
                                        reason="re-publish it, or pass --site to let the autopilot do it")
                try:
                    republish(self.profile, self.site)
                except (RuntimeError, OSError) as error:
                    return self.persist("republish_failed", reason=str(error))
                if not served_image_ok(self.profile):
                    return self.persist("image_not_served", url=self.profile["url"])
                self.persist("republished", url=self.profile["url"])
            try:
                sup.restore_pm_source_telemetry(self.profile, self.work)
            except (RuntimeError, ValueError, OSError) as error:
                return self.persist("pm_source_restore_failed", reason=str(error))
            for mode in ("preflight", "check"):
                rc, tries = retry_step(lambda m=mode: self.run_campaign(m), self.step_retries,
                                       self.step_retry_seconds)
                if rc:
                    return self.persist(mode + "_failed", tries=tries)
            try:
                sup.release_pm_after_deferred_reconcile(self.profile, self.work)
            except (RuntimeError, ValueError) as error:
                return self.persist("deferred_release_failed", reason=str(error))

            if (self.work / STOP_FILE).exists():
                return self.persist("stopped_by_operator", reason="stop file found before flashing")
            attempt = {"number": len(self.attempts) + 1, "launched_at": sup.now()}
            self.attempts.append(attempt)
            self.persist("flashing", attempt=attempt["number"])
            before = ota_logs(self.work)
            attempt["flash_exit"] = self.run_campaign("flash", confirm=True)
            new_logs = ota_logs(self.work) - before
            if not any(sup.ota_attempt_progress(path, self.profile) is not None for path in new_logs):
                # Nothing was sent (refused at admission, e.g. a peer between
                # attempts, or stopped by the flash's own checks): not an
                # attempt. Wait for the peer instead of spinning.
                self.attempts.pop()
                self.refusals.append({"at": sup.now(), "phase": self.phase(),
                                      "reason": refusal_reason(new_logs)})
                if self.waited >= self.peer_wait_budget:
                    return self.persist("peer_wait_timeout",
                                        reason="refused for %d s without sending an update" % self.waited)
                self.persist("waiting_for_peer", reason=self.refusals[-1]["reason"])
                time.sleep(self.peer_wait_seconds)
                self.waited += self.peer_wait_seconds
                continue
            attempt.update(attempt_summary(max(new_logs, key=lambda path: path.stat().st_mtime)))
            attempt["ended_phase"] = self.phase()
            self.persist("attempt_finished", attempt=attempt["number"])


MAX_PARALLEL = 5


def validate_group(profiles: list[dict[str, Any]]) -> None:
    """Every member of a parallel group must allow every other member."""
    if not 1 <= len(profiles) <= MAX_PARALLEL:
        raise ValueError("run-many takes 1..%d profiles" % MAX_PARALLEL)
    ieees = [p["ieee"].lower() for p in profiles]
    if len(set(ieees)) != len(ieees):
        raise ValueError("run-many needs distinct sockets")
    if len(profiles) == 1:
        return
    for p in profiles:
        slots = int(p.get("parallel_ota_max", 1) or 1)
        if slots < len(profiles):
            raise ValueError("%s: parallel_ota_max=%d is below the group size %d"
                             % (p["device"], slots, len(profiles)))
        allowed = {str(i).lower() for i in p.get("concurrent_ota_ieees") or []}
        missing = set(ieees) - {p["ieee"].lower()} - allowed
        if missing:
            raise ValueError("%s: concurrent_ota_ieees lacks %s" % (p["device"], ", ".join(sorted(missing))))


def run_many(profile_paths: list[Path], confirm_ieees: list[str], *, extra: list[str],
             stagger_seconds: float = 20, poll_seconds: float = 30,
             launch: Callable[[list[str], Path], int] = sup.launch_logged,
             alive: Callable[[int], bool] = sup.process_alive,
             sleep: Callable[[float], None] = time.sleep) -> list[dict[str, Any]]:
    """One autopilot process per socket (each takes its own slot), then wait for all."""
    profiles = [sup.campaign.load_profile(p) for p in profile_paths]
    if [p["ieee"] for p in profiles] != list(confirm_ieees):
        raise ValueError("--confirm-ieee must list each profile's exact IEEE, in the same order")
    validate_group(profiles)
    pids = []
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    for index, (path, profile) in enumerate(zip(profile_paths, profiles)):
        if index:
            sleep(stagger_seconds)  # one admission at a time on the coordinator
        cmd = [sys.executable, "-u", str(Path(__file__).resolve()), "run", "--profile", str(path),
               "--confirm-ieee", profile["ieee"], *extra]
        log = Path(profile["workdir"]) / f"autopilot_run_many_{stamp}.log"
        pids.append(launch(cmd, log))
        print("LAUNCHED", profile["device"], "pid", pids[-1], "log", log, flush=True)
    while any(alive(pid) for pid in pids):
        sleep(poll_seconds)
    results = []
    for profile in profiles:
        record = sup.read_json(Path(profile["workdir"]) / STATE_FILE) or {}
        results.append({"device": profile["device"], "state": record.get("state"),
                        "phase": record.get("phase"),
                        "attempts": len(record.get("attempts") or []),
                        "best_progress": (record.get("progress_gate") or {}).get("best_progress")})
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--profile", required=True)
    run.add_argument("--confirm-ieee", required=True)
    run.add_argument("--confirm-load-unplugged", action="store_true")
    run.add_argument("--max-attempts", type=int, default=12)
    run.add_argument("--max-stagnant", type=int, default=2)
    run.add_argument("--step-retries", type=int, default=2)
    run.add_argument("--step-retry-seconds", type=float, default=30)
    run.add_argument("--postflash-retries", type=int, default=2)
    run.add_argument("--site", help="private site.json: lets the autopilot re-publish a missing image")
    run.add_argument("--min-gain", type=float, default=0.5)
    run.add_argument("--peer-wait-seconds", type=float, default=60)
    run.add_argument("--peer-wait-budget-seconds", type=float, default=1800)
    many = sub.add_parser("run-many", help="up to %d sockets in parallel, one autopilot each" % MAX_PARALLEL)
    many.add_argument("--profile", action="append", required=True)
    many.add_argument("--confirm-ieee", action="append", required=True)
    many.add_argument("--stagger-seconds", type=float, default=20)
    many.add_argument("--max-attempts", type=int, default=12)
    many.add_argument("--max-stagnant", type=int, default=2)
    many.add_argument("--site", help="private site.json, passed to every autopilot")
    show = sub.add_parser("status")
    show.add_argument("--profile", required=True)
    stop = sub.add_parser("stop", help="stop at the next safe point (never mid-transfer)")
    stop.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    if args.command == "stop":
        work = Path(sup.campaign.load_profile(Path(args.profile).expanduser().resolve())["workdir"])
        (work / STOP_FILE).write_text(sup.now() + "\n", encoding="utf8")
        print("STOP_REQUESTED", work / STOP_FILE)
        return 0
    if args.command == "run-many":
        paths = [Path(p).expanduser().resolve() for p in args.profile]
        results = run_many(paths, args.confirm_ieee, stagger_seconds=args.stagger_seconds,
                           extra=["--max-attempts", str(args.max_attempts),
                                  "--max-stagnant", str(args.max_stagnant)] +
                                 (["--site", args.site] if args.site else []))
        print(json.dumps(results, indent=2))
        return 0 if all(r["state"] == "installed" for r in results) else 2
    profile_path = Path(args.profile).expanduser().resolve()
    if args.command == "status":
        work = Path(sup.campaign.load_profile(profile_path)["workdir"])
        print(json.dumps(sup.read_json(work / STATE_FILE), indent=2))
        return 0
    if not 1 <= args.max_attempts <= 30 or not 0 <= args.max_stagnant <= 5:
        raise SystemExit("max-attempts must be 1..30 and max-stagnant 0..5")
    pilot = Autopilot(profile_path, args.confirm_ieee, confirm_unloaded=args.confirm_load_unplugged,
                      max_attempts=args.max_attempts, max_stagnant=args.max_stagnant,
                      step_retries=args.step_retries, step_retry_seconds=args.step_retry_seconds,
                      postflash_retries=args.postflash_retries, min_gain=args.min_gain,
                      peer_wait_seconds=args.peer_wait_seconds,
                      peer_wait_budget_seconds=args.peer_wait_budget_seconds,
                      site=json.loads(Path(args.site).read_text(encoding="utf8")) if args.site else None)
    # Same per-workdir ownership as the resume supervisor: one owner at a time.
    if (pilot.work / STOP_FILE).exists():
        raise SystemExit("A stop file is present (%s); remove it to start again" % (pilot.work / STOP_FILE))
    result = sup.exclusive_supervisor(lambda _path: pilot.run())(profile_path)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["state"] == "installed" else 2


if __name__ == "__main__":
    sys.exit(main())
