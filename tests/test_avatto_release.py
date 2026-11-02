"""AVATTO Router release identities stay consistent with device_db and above upstream."""
from pathlib import Path
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
from avatto_release import (CANDIDATES, INSTALLED_UPSTREAM_VERSION,  # noqa: E402
                            MANUFACTURER_CODE, main, validate_candidates)
from bseed_ota_identity import IdentityError  # noqa: E402


def test_candidates_match_device_db_and_exceed_installed_upstream():
    validate_candidates()
    db = yaml.safe_load((ROOT / 'device_db.yaml').read_text(encoding='utf-8'))
    for key, c in CANDIDATES.items():
        entry = db[c['board']]
        assert entry['firmware_image_type'] == c['type'], key
        assert entry['stock_manufacturer_id'] == MANUFACTURER_CODE, key
        assert entry['device_type'] == 'router', key
        assert entry['config_str'].startswith('%s;%s;' % (c['manufacturer'], c['model'])), key
        assert c['version'] > INSTALLED_UPSTREAM_VERSION, key


def test_upstream_version_or_lower_is_refused(monkeypatch):
    monkeypatch.setitem(CANDIDATES['ts0001'], 'version', INSTALLED_UPSTREAM_VERSION)
    with pytest.raises(IdentityError, match='exceed the installed upstream'):
        validate_candidates()


def test_overlong_basic_build_id_is_refused(monkeypatch):
    monkeypatch.setitem(CANDIDATES['ts0002'], 'build', '1.1.2-avatto-linkpolicy')
    with pytest.raises(IdentityError, match='16 ASCII'):
        validate_candidates()


def test_vars_output_drives_the_build_script(capsys):
    assert main(['vars', '--variant', 'ts0002']) == 0
    out = capsys.readouterr().out
    assert "BOARD='MODULE_AVATTO_A_TS0002'" in out
    assert 'IMAGE_TYPE=43528' in out
    assert "FILE_VERSION_HEX='0x11023001'" in out
    script = (ROOT / 'make_scripts/build_avatto_router.sh').read_text(encoding='utf-8')
    assert 'avatto_release.py vars --variant' in script
    assert 'avatto_release.py verify --variant' in script
    assert 'BSEED_LINK_POLICY=1' in script
    assert 'from_tuya' not in script  # both modules already run custom firmware
