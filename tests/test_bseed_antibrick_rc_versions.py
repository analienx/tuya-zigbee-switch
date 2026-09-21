"""Keep new OTA candidate IDs distinct without mutating retained goldens."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_clients_have_explicit_opt_in_and_unique_increasing_versions():
    s = (ROOT / 'make_scripts/build_bseed_mains_client.sh').read_text()
    assert 'BSEED_ANTIBRICK_RC:-0' in s
    for current, candidate, current_hex, candidate_hex in (
        ('1.2.5-bseedcli6', '1.2.5-bseedcli7', '0x1205300C', '0x1205300F'),
        ('1.1.2-bseedcli4', '1.1.2-bseedcli6', '0x1102300F', '0x11023011'),
    ):
        assert s.count("SW_BUILD='" + current + "'") == 1
        assert s.count("SW_BUILD='" + candidate + "'") == 1
        assert int(candidate_hex, 16) > int(current_hex, 16)
    assert 'ERROR: anti-brick RC limited to verified BSEED sockets' in s
    assert 'DEVICE_CONFIG_GUARD=BSEED_TS011F_NONPM' in s
    assert 'BSEED_PM_B28WRPVX=1' in s
    assert 'never publishes, flashes' in s


def test_routers_keep_accepted_image_and_opt_in_candidate():
    pm = (ROOT / 'make_scripts/build_bseed_ts011f_pm_v8.sh').read_text()
    nonpm = (ROOT / 'make_scripts/build_bseed_ts011f_nonpm_router.sh').read_text()
    assert "SW_BUILD='1.2.5-bseedv8u4'" in pm
    assert "SW_BUILD='1.2.5-bseedv8u5-rc2'" in pm
    assert 'BSEED_PM_ROUTER_CANDIDATE:-0' in pm
    assert "SW_BUILD='1.1.3-bseedv8'" in nonpm
    assert "SW_BUILD='1.1.3-bseedv9'" in nonpm
    assert 'BSEED_ANTIBRICK_RC:-0' in nonpm
    assert 'DEVICE_CONFIG_GUARD=BSEED_TS011F_NONPM' in nonpm
