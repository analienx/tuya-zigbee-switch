"""Shared post-abort OTA query recovery, without changing SDK timeout policy."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OTA=ROOT/'src/telink/hal/zigbee_ota.c'


def test_client_and_pm_router_share_deferred_abort_requery():
    code=OTA.read_text(encoding='utf8')
    assert '#if defined(BSEED_MAINS_CLIENT) || defined(BSEED_PM_B28WRPVX)' in code
    assert '#define BSEED_OTA_DEFERRED_REQUERY' in code
    assert code.count('#ifdef BSEED_OTA_DEFERRED_REQUERY') >= 4
    assert 'hal_tasks_init(&ota_abort_query_retry_task)' in code
    assert 'hal_tasks_unschedule(&ota_abort_query_retry_task)' in code
    assert 'hal_tasks_schedule(&ota_abort_query_retry_task,' in code
    assert 'ota_queryStart(OTA_PERIODIC_QUERY_INTERVAL)' in code


def test_role_builds_enable_only_intended_recovery_variants():
    router=(ROOT/'make_scripts/build_bseed_ts011f_pm_v8.sh').read_text(encoding='utf8')
    client=(ROOT/'src/telink/client.mk').read_text(encoding='utf8')
    assert 'BSEED_PM_B28WRPVX=1' in router
    assert '-DBSEED_MAINS_CLIENT=1' in client
