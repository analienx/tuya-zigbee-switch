"""Consolidated non-PM builds must not regenerate historical CLI4/CLI5 identities."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = (ROOT / 'make_scripts/build_bseed_mains_client.sh').read_text()
NET = (ROOT / 'src/telink/hal/zigbee_network.c').read_text()
CLIENT = (ROOT / 'src/telink/client.mk').read_text()


def test_historical_cli5_build_is_refused_and_current_candidate_uses_allocator_definition():
    assert 'nonpm-keepalive)' in BUILD
    candidate = BUILD.split('nonpm-keepalive)\n', 1)[1].split('\nnonpm)\n', 1)[0]
    assert 'exit 2' in candidate
    assert 'original source/artifact' in candidate
    assert 'bseed_nonpm_release.py vars --role client' in BUILD
    assert "SW_BUILD='1.1.2-bseedcli4'" not in BUILD

def test_client_uses_poll_keepalive_on_initial_join_and_rejoin():
    assert '-DEND_DEVICE=1' in CLIENT
    assert '-DZB_MAC_RX_ON_WHEN_IDLE=1' in CLIENT
    assert '-DBSEED_MAINS_CLIENT=1' in CLIENT
    assert re.search(r'^#define\s+MAINS_CLIENT_KEEPALIVE_POLL_MS\s+60000u\b', NET, re.MULTILINE)
    assert NET.count('configure_mains_client_keepalive();') == 3
    assert 'case BDB_COMMISSION_STA_PARENT_LOST:' in NET
    assert 'zb_rejoinReqWithBackOff(' in NET
    assert 'if (network_recovery_state != TELINK_NETWORK_RECOVERY_REJOIN)' in NET


def test_candidate_keeps_router_and_pm_images_separate():
    candidate = BUILD.split('\nnonpm)\n', 1)[1].split(';;', 1)[0]
    assert 'ROUTER_IMAGE_TYPE=43555' in candidate
    assert "DEFAULT_OUT='build/bseed-ts011f-nonpm-client'" in candidate
    assert 'DEVICE_CONFIG_GUARD=BSEED_TS011F_NONPM' in candidate
    assert 'BSEED_PM_B28WRPVX=1' not in candidate
    assert '"normalOtaIndex": False' in BUILD
