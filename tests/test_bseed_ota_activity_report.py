"""Four-variant OTA activity diagnostics; CI-only regression suite."""
import datetime as dt
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper_scripts"))
from bseed_ota_activity_report import inspect_jsonl


@pytest.fixture(params=[
    ("PM Router", "0x0000000000000001", "a" * 64),
    ("PM Client", "0x0000000000000002", "b" * 64),
    ("non-PM Router", "0x0000000000000003", "c" * 64),
    ("non-PM Client", "0x0000000000000004", "d" * 64),
])
def target(request):
    return request.param


def _write_log(path, ieee, sha, timeout=300000, *,
               progresses=((4, 0.0), (42, 0.32), (80, 0.59)),
               terminal_at=1900, phase="update_error"):
    t = dt.datetime(2026, 10, 4, tzinfo=dt.timezone.utc)
    rows = [{
        "event": "ota_request_sent",
        "when": t.isoformat(),
        "value": {
            "ieee": ieee, "image_sha256": sha,
            "transaction": "bseed-ota-synthetic",
            "image_block_request_timeout": timeout,
            "default_maximum_data_size": 32,
            "image_block_response_delay": 1200,
        },
    }]
    for seconds, percentage in progresses:
        rows.append({
            "when": (t + dt.timedelta(seconds=seconds)).isoformat(),
            "event": "device_state",
            "value": {"update": {"state": "updating", "progress": percentage}},
        })
    if terminal_at is not None:
        rows.append({
            "when": (t + dt.timedelta(seconds=terminal_at)).isoformat(),
            "event": "ota_final",
            "value": {"phase": phase, "response": {"status": "error"}},
        })
    path.write_text("\n".join(map(json.dumps, rows)) + "\n", encoding="utf8")
    return path


def test_identical_protection_for_all_four_board_roles(tmp_path, target):
    _, ieee, sha = target
    path = _write_log(tmp_path / "ota.jsonl", ieee, sha, timeout=180000)
    r = inspect_jsonl(path, ieee=ieee, sha256=sha)
    assert r["phase"] == "failed_after_partial_progress"
    assert r["maximum_reported_percent"] == 0.59
    assert r["last_reported_percent"] == 0.59
    assert r["configured_block_request_timeout_ms"] == 180000
    assert r["seconds_last_progress_change_to_terminal"] == 1820
    assert r["confirmed_device_block_request_interval_ms"] is None
    assert r["timeout_sufficiency"] == "undetermined"
    assert not r["automatic_retry_authorized"]
    assert not r["hardware_accepted"]


def test_timeout_does_not_claim_root_cause_or_safe_180_seconds(tmp_path):
    log = _write_log(tmp_path / "timeout.jsonl", "0x0000000000000001", "f" * 64,
                     timeout=1800000, progresses=((2, 0), (80, 0.59)))
    r = inspect_jsonl(log, ieee="0x0000000000000001", sha256="f" * 64)
    assert r["seconds_last_progress_change_to_terminal"] > 180
    assert r["timeout_sufficiency"] == "undetermined"
    assert "requires raw device-scoped" in r["block_timing_evidence"]


def test_success_is_not_hardware_acceptance(tmp_path):
    log = _write_log(tmp_path / "done.jsonl", "0x0000000000000001", "e" * 64,
                     terminal_at=160, phase="ota_transfer_ok_postflash_unverified")
    r = inspect_jsonl(log, ieee="0x0000000000000001", sha256="e" * 64)
    assert r["phase"] == "transport_succeeded_postflash_unverified"
    assert not r["hardware_accepted"]


def test_no_terminal_does_not_mean_stopped(tmp_path):
    log = _write_log(tmp_path / "active.jsonl", "0x0000000000000001", "a" * 64,
                     terminal_at=None)
    r = inspect_jsonl(log, ieee="0x0000000000000001", sha256="a" * 64)
    assert r["phase"] == "transport_open_or_observer_incomplete"
    assert r["terminal_at"] is None
    assert not r["automatic_retry_authorized"]


def test_foreign_transaction_cannot_poison_policy(tmp_path):
    log = _write_log(tmp_path / "foreign.jsonl", "0x0000000000000001", "a" * 64)
    with pytest.raises(ValueError, match="requested IEEE/image"):
        inspect_jsonl(log, ieee="0x0000000000000002", sha256="a" * 64)
    with pytest.raises(ValueError, match="requested IEEE/image"):
        inspect_jsonl(log, ieee="0x0000000000000001", sha256="b" * 64)


def test_only_readonly_check_not_counted_as_real_attempt(tmp_path):
    p = tmp_path / "check.jsonl"
    p.write_text(json.dumps({
        "event": "check_passed_no_flash", "when": "2026-10-10T18:00:00+00:00",
        "value": {"update_available": True}}) + "\n")
    with pytest.raises(ValueError, match="Exactly one real OTA attempt"):
        inspect_jsonl(p, ieee="0x0000000000000001", sha256="a" * 64)


def test_two_real_attempts_in_same_file_refused(tmp_path):
    p = _write_log(tmp_path / "duplicated.jsonl", "0x0000000000000001", "a" * 64)
    lines = p.read_text().splitlines()
    p.write_text("\n".join(lines + [lines[0]]) + "\n")
    with pytest.raises(ValueError, match="Exactly one real OTA attempt"):
        inspect_jsonl(p, ieee="0x0000000000000001", sha256="a" * 64)


def test_boolean_nan_and_infinite_progress_not_accepted(tmp_path):
    p = _write_log(tmp_path / "bad.jsonl", "0x0000000000000001", "a" * 64,
                   progresses=((3, True), (4, float("nan")), (5, float("inf"))))
    result = inspect_jsonl(p, ieee="0x0000000000000001", sha256="a" * 64)
    assert result["maximum_reported_percent"] is None
    assert result["reported_progress_samples"] == 0
    assert result["phase"] == "failed_before_observed_progress"
