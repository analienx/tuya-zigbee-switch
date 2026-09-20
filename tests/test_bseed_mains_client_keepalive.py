"""Guard parent keepalives for mains-powered, non-routing Zigbee clients."""
from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[1]


def test_mains_client_refreshes_parent_on_initial_join_and_rejoin():
    source=(ROOT/'src/telink/hal/zigbee_network.c').read_text()
    assert '#define MAINS_CLIENT_KEEPALIVE_POLL_MS 60000u' in source
    assert 'zb_setPollRate(MAINS_CLIENT_KEEPALIVE_POLL_MS)' in source
    assert source.count('configure_mains_client_keepalive();')==2
    pat=r'#ifdef BSEED_MAINS_CLIENT\s+configure_mains_client_keepalive\(\);\s+#endif\s+#if defined\(ZB_ED_ROLE\) && !defined\(BSEED_MAINS_CLIENT\)'
    assert len(re.findall(pat,source))==2
    assert 'if (status != RET_OK)' in source


def test_keepalive_does_not_change_radio_role_or_sleepy_devices():
    client=(ROOT/'src/telink/client.mk').read_text()
    source=(ROOT/'src/telink/hal/zigbee_network.c').read_text()
    assert '-DEND_DEVICE=1' in client
    assert '-DZB_MAC_RX_ON_WHEN_IDLE=1' in client
    assert 'PM_ENABLE' not in client
    assert 'zb_setPollRate(POLL_RATE)' in source
    assert '#if defined(ZB_ED_ROLE) && !defined(BSEED_MAINS_CLIENT)' in source
