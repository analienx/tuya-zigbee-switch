from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def test_ts0726_currentlevel_candidate_does_not_replace_v8_default():
    script = (ROOT / 'make_scripts/build_bseed_ts0726_v8.sh').read_text()
    wrapper = (ROOT / 'make_scripts/build_bseed_ts0726_level_canary.sh').read_text()
    assert "SW_BUILD='1.1.8-bseedv8'" in script
    assert "FILE_VERSION_HEX='0x1102300A'" in script
    assert 'BSEED_TS0726_RELEASE_CHANNEL' in script
    assert "SW_BUILD='1.1.9-bseedlevel1'" in script
    assert "FILE_VERSION_HEX='0x1102300D'" in script
    assert 'FILE_VERSION_DEC=285356045' in script
    assert "BSEED_TS0726_RELEASE_CHANNEL=currentlevel-canary" in wrapper
    assert 'build_bseed_ts0726_v8.sh' in wrapper
    assert 'exec bash' in wrapper
    assert 'publish' in wrapper and 'never' in wrapper


def test_canary_version_does_not_collide_with_published_router_index():
    index = json.loads((ROOT / 'zigbee2mqtt/ota/index_bseed.json').read_text())
    router_versions = [entry['fileVersion'] for entry in index if entry['imageType'] == 45577]
    assert router_versions and max(router_versions) < 0x1102300D
    assert not any(entry['imageType'] == 45577 and entry['fileVersion'] == 0x1102300D for entry in index)
