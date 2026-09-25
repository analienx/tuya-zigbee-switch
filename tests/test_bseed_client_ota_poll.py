"""Regression for the client OTA/keepalive poll findings (Phase 1 F2/F3).

The reference Telink app fast-polls for the whole download and the mains
client must re-verify its 60 s keepalive: a lost poll rate is never recovered
otherwise, and the parent ages the child while uplink keeps working.
"""
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src/telink/hal"
OTA = (SRC / "zigbee_ota.c").read_text(encoding="utf-8")
NET = (SRC / "zigbee_network.c").read_text(encoding="utf-8")
HAL_H = (SRC / "telink_zigbee_hal.h").read_text(encoding="utf-8")


def test_ota_callback_switches_poll_rate_around_download() -> None:
    assert "if (status == ZCL_STA_SUCCESS) {\n            hal_zigbee_set_ota_poll_active(true);" in OTA
    assert "if (evt == OTA_EVT_IMAGE_DONE || evt == OTA_EVT_COMPLETE) {\n        hal_zigbee_set_ota_poll_active(false);" in OTA


def test_keepalive_tick_reverifies_and_retries() -> None:
    assert "hal_zigbee_get_poll_rate_ms() != MAINS_CLIENT_KEEPALIVE_POLL_MS" in NET
    assert "if (!ota_fast_poll_active &&" in NET
    assert "hal_tasks_schedule(&keepalive_verify_task, next_ms);" in NET
    assert "next_ms = KEEPALIVE_RETRY_MS;" in NET
    assert "hal_tasks_schedule(&keepalive_verify_task, KEEPALIVE_VERIFY_TICK_MS);" in NET


def test_ota_poll_mode_is_role_aware() -> None:
    assert "void hal_zigbee_set_ota_poll_active(bool fast);" in HAL_H
    assert "hal_zigbee_set_poll_rate_ms(RESPONSE_POLL_RATE);" in NET
    assert "hal_zigbee_set_poll_rate_ms(fast ? RESPONSE_POLL_RATE : POLL_RATE);" in NET
