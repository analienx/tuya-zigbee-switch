from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_nonpm_target_has_distinct_router_and_client_identities():
    script = (ROOT / "make_scripts/build_bseed_mains_client.sh").read_text()
    assert "BOARD='OUTLET_BSEED_TS011F'" in script
    assert "CANONICAL='o1jzcxou;TS011F-BS;LC2;SB4u;RC3;ID2;M;'" in script
    assert "ROUTER_IMAGE_TYPE=43555" in script
    assert "CLIENT_IMAGE_TYPE=65026" in script
    assert "FILE_VERSION_HEX='0x1102300D'" in script
    assert "DEFAULT_OUT='build/bseed-ts011f-nonpm-client'" in script


def test_nonpm_stock_conversion_is_staged_through_router_not_direct_to_client():
    router = (ROOT / "make_scripts/build_bseed_ts011f_nonpm_canary_router.sh").read_text()
    client = (ROOT / "make_scripts/build_bseed_mains_client.sh").read_text()

    assert "STOCK_MANUFACTURER_NAME='_TZ3000_o1jzcxou'" in router
    assert "STOCK_IMAGE_TYPE=54179" in router
    assert "IMAGE_TYPE=43555" in router
    assert "OTA_VERSION=0xFFFFFFFF" in router
    assert '"sacrificialHardwareCanaryOnly": True' in router
    assert '"normalOtaIndex": False' in router

    # The Client artifact intentionally accepts only the already-custom Router
    # identity. Stock conversion and role conversion are two observable stages.
    assert "ROUTER_IMAGE_TYPE=43555" in client
    assert "FROM_ROUTER_OTA" in client
    assert '"stockConversion": False' in client
    assert "from_tuya" not in client.lower()


def test_nonpm_canary_workflow_proves_router_regression_and_client_rollback():
    workflow = (ROOT / ".github/workflows/bseed-nonpm-client-canary.yml").read_text()
    assert "git merge-base HEAD origin/main" in workflow
    assert "SOURCE_ROOT=\"$BASE_DIR\"" in workflow
    assert "cmp \"$BASE_DIR/build/baseline-nonpm/forward.bin\" build/client-control-nonpm/forward.bin" in workflow
    assert "build_bseed_mains_client.sh nonpm" in workflow
    assert "--image-type 65026 --file-version 0xFFFFFFFF" in workflow
    assert "rollback-to-router.ota" in workflow
    assert "Rebuild Client and require byte-identical output" in workflow
    assert "normalOtaIndex" in workflow


def test_nonpm_canary_identity_guard_is_exact():
    script = (ROOT / "make_scripts/build_bseed_ts011f_nonpm_canary_router.sh").read_text()
    assert "BOARD='OUTLET_BSEED_TS011F'" in script
    assert "STOCK_MANUFACTURER_NAME='_TZ3000_o1jzcxou'" in script
    assert "CANONICAL='o1jzcxou;TS011F-BS;LC2;SB4u;RC3;ID2;M;'" in script
    assert 'entry["stock_manufacturer_name"] == stock_name' in script
    assert 'entry["stock_image_type"]' in script
    assert 'entry["mcu"] == "TLSR8258"' in script
