"""Read-only, target-pinned OTA activity diagnosis for PM and non-PM sockets.

A JSONL progress event is not a Zigbee OTA block request. This helper NEVER
infers that a low server timeout is safe from the gap between progress
percentages, which can have different reporting cadence. Never authorizes
flashing, lock release, source reconciliation, or a firmware version change.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path
from typing import Any


def _timestamp(raw: Any) -> dt.datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        value = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return value.astimezone(dt.timezone.utc) if value.tzinfo else None
    except ValueError:
        return None


def inspect_jsonl(path: str | Path, *, ieee: str, sha256: str) -> dict[str, Any]:
    """Analyze exactly one attempted OTA; strict transaction/image correlation."""
    if not ieee or not sha256 or len(sha256) != 64:
        raise ValueError("Exact device IEEE and 64-character SHA256 are required")
    attempts: list[dict[str, Any]] = []
    latest_progress = None
    max_progress = None
    latest_progress_change_at = None
    latest_report_at = None
    request = None
    request_at = None
    terminal = None
    terminal_at = None
    observed_progress_count = 0
    with Path(path).open("r", encoding="utf8", errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            name = event.get("event")
            value = event.get("value")
            value = value if isinstance(value, dict) else {}
            when = _timestamp(event.get("when"))
            if name == "ota_request_sent":
                attempts.append(value)
                request = value
                request_at = when
            elif name == "device_state" and request is not None and terminal is None:
                update = value.get("update")
                update = update if isinstance(update, dict) else {}
                progress = update.get("progress")
                if (update.get("state") != "updating" or type(progress) not in (int, float)
                        or not math.isfinite(progress) or not 0 <= progress <= 100):
                    continue
                observed_progress_count += 1
                latest_report_at = when
                if latest_progress is None or progress != latest_progress:
                    latest_progress_change_at = when
                    latest_progress = progress
                max_progress = progress if max_progress is None else max(max_progress, progress)
            elif name == "ota_final" and request is not None:
                terminal = value
                terminal_at = when
    if len(attempts) != 1 or request is None:
        raise ValueError("Exactly one real OTA attempt per JSONL is required")
    if request.get("ieee") != ieee or request.get("image_sha256") != sha256:
        raise ValueError("Transaction does not belong to the requested IEEE/image")
    tx = request.get("transaction")
    if not isinstance(tx, str) or not tx.startswith("bseed-ota-"):
        raise ValueError("Actual OTA transaction identifier missing")
    timeout = request.get("image_block_request_timeout")
    if type(timeout) is not int or not 60000 <= timeout <= 3600000:
        timeout = None

    idle_gap = None
    if terminal_at and latest_progress_change_at:
        idle_gap = max(0.0, round((terminal_at - latest_progress_change_at).total_seconds(), 3))
    if terminal is None:
        stage = "transport_open_or_observer_incomplete"
    elif terminal.get("phase") == "ota_transfer_ok_postflash_unverified":
        stage = "transport_succeeded_postflash_unverified"
    elif max_progress is None or max_progress == 0:
        stage = "failed_before_observed_progress"
    else:
        stage = "failed_after_partial_progress"

    return {
        "device_ieee": ieee, "candidate_sha256": sha256, "transaction": tx,
        "terminal_phase": (terminal or {}).get("phase"),
        "phase": stage,
        "reported_progress_samples": observed_progress_count,
        "maximum_reported_percent": max_progress,
        "last_reported_percent": latest_progress,
        "request_at": request_at.isoformat() if request_at else None,
        "last_progress_change_at": latest_progress_change_at.isoformat() if latest_progress_change_at else None,
        "last_progress_report_at": latest_report_at.isoformat() if latest_report_at else None,
        "terminal_at": terminal_at.isoformat() if terminal_at else None,
        "seconds_last_progress_change_to_terminal": idle_gap,
        "requested_block_size_bytes": request.get("default_maximum_data_size"),
        "requested_response_delay_ms": request.get("image_block_response_delay"),
        "configured_block_request_timeout_ms": timeout,
        "confirmed_device_block_request_interval_ms": None,
        "block_timing_evidence": "not_in_campaign_jsonl; requires raw device-scoped zhc:ota trace",
        "timeout_sufficiency": "undetermined",
        "root_cause": "unproven",
        "automatic_retry_authorized": False,
        "hardware_accepted": False,
        "warning": (
            "Progress reporting and real Zigbee imageBlockRequest traffic are not equivalent. "
            "No timeout change is validated by percent gaps alone. OTA failure may be "
            "RF/downlink, device abort, rejoin, query-stage failure, or image rejection. "
            "Never infer a stopped Zigbee2MQTT transfer from stale observer logs."
        ),
    }


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--log", required=True, help="Existing PRIVATE ota_bseed-ota-*.jsonl")
    p.add_argument("--ieee", required=True)
    p.add_argument("--image-sha256", required=True)
    args = p.parse_args(argv)
    print(json.dumps(inspect_jsonl(args.log, ieee=args.ieee, sha256=args.image_sha256), indent=2))


if __name__ == "__main__":
    main()
