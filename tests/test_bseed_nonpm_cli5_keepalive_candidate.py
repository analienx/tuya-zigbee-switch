"""Keep the non-PM parent-keepalive candidate separate from released CLI4."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = (ROOT / 'make_scripts/build_bseed_mains_client.sh').read_text()
NET = (ROOT / 'src/telink/hal/zigbee_network.c').read_text()
CLIENT = (ROOT / 'src/telink/client.mk').read_text()


def test_candidate_is_opt_in_and_has_distinct_ota_version():
    assert 'nonpm-keepalive)' in BUILD
    candidate = BUILD.split('nonpm-keepalive)\n', 1)[1].split('\nnonpm)\n', 1)[0]
    assert "BOARD='OUTLET_BSEED_TS011F'" in candidate
    assert "CANONICAL='o1jzcxou;TS011F-BS;LC2;SB4u;RC3;ID2;M;'" in candidate
    assert 'CLIENT_IMAGE_TYPE=65026' in candidate
    assert "SW_BUILD='1.1.2-bseedcli5-rc2'" in candidate
    assert "FILE_VERSION_HEX='0x11023012'" in candidate
    assert 'FILE_VERSION_DEC=285356050' in candidate
    assert "SW_BUILD='1.1.2-bseedcli4'" in BUILD
    assert "FILE_VERSION_HEX='0x1102300F'" in BUILD

def test_client_uses_poll_keepalive_on_initial_join_and_rejoin():
    assert '-DEND_DEVICE=1' in CLIENT
    assert '-DZB_MAC_RX_ON_WHEN_IDLE=1' in CLIENT
    assert '-DBSEED_MAINS_CLIENT=1' in CLIENT
    assert '#define MAINS_CLIENT_KEEPALIVE_POLL_MS 60000u' in NET
    assert NET.count('configure_mains_client_keepalive();') == 3
    assert 'case BDB_COMMISSION_STA_PARENT_LOST:' in NET
    assert 'zb_rejoinReqWithBackOff(' in NET
    assert 'if (network_recovery_state != TELINK_NETWORK_RECOVERY_REJOIN)' in NET


def test_candidate_keeps_router_and_pm_images_separate():
    candidate = BUILD.split('nonpm-keepalive)\n', 1)[1].split('\nnonpm)\n', 1)[0]
    assert 'ROUTER_IMAGE_TYPE=43555' in candidate
    assert "DEFAULT_OUT='build/bseed-ts011f-nonpm-client-cli5-rc2'" in candidate
    assert 'EXTRA_ARGS=()' in candidate
    assert 'BSEED_PM_B28WRPVX=1' not in candidate
    assert '"normalOtaIndex": False' in BUILD
