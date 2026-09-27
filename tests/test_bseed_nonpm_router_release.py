from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "make_scripts" / "build_bseed_ts011f_nonpm_router.sh").read_text()


def test_nonpm_router_release_identity_is_separate_from_pm():
    assert "BOARD='OUTLET_BSEED_TS011F'" in SCRIPT
    assert "CANONICAL='o1jzcxou;TS011F-BS;LC2;SB4u;RC3;ID2;M;'" in SCRIPT
    assert "IMAGE_TYPE=43555" in SCRIPT
    assert "STOCK_MANUFACTURER_NAME='_TZ3000_o1jzcxou'" in SCRIPT
    assert "STOCK_IMAGE_TYPE=54179" in SCRIPT
    assert 'bseed_nonpm_release.py vars --role router' in SCRIPT
    assert 'DEVICE_CONFIG_GUARD=BSEED_TS011F_NONPM' in SCRIPT
    assert 'BSEED_BUILD_DATE="$RELEASE_DATE_OVERRIDE"' in SCRIPT


def test_nonpm_router_release_does_not_enable_pm_backend():
    assert "BSEED_PM_B28WRPVX=1" not in SCRIPT
    assert "BSEED_PM_B28WRPVX_PROTECTION=1" not in SCRIPT
    assert "HLW8012_VOLTAGE_MULTIPLIER" not in SCRIPT
    assert "HLW8012_CURRENT_MULTIPLIER" not in SCRIPT
    assert "HLW8012_POWER_MULTIPLIER" not in SCRIPT


def test_nonpm_router_release_keeps_router_and_payload_identity_guards():
    assert "DEVICE_TYPE=router" in SCRIPT
    assert "payloadFromByte56Identical" in SCRIPT
    assert "sourceDirty" in SCRIPT
    assert "stock['fileVersion'] == 4294967295" in SCRIPT
