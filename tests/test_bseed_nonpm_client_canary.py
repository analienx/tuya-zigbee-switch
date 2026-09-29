from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_nonpm_target_has_distinct_router_and_client_identities():
    script = (ROOT / "make_scripts/build_bseed_mains_client.sh").read_text()
    assert "BOARD='OUTLET_BSEED_TS011F'" in script
    assert "CANONICAL='o1jzcxou;TS011F-BS;LC2;SB4u;RC3;ID2;M;'" in script
    assert "ROUTER_IMAGE_TYPE=43555" in script
    assert "CLIENT_IMAGE_TYPE=65026" in script
    assert 'bseed_nonpm_release.py vars --role client' in script
    assert "DEFAULT_OUT='build/bseed-ts011f-nonpm-client'" in script


def test_nonpm_stock_conversion_is_staged_through_router_not_direct_to_client():
    router = (ROOT / "make_scripts/build_bseed_ts011f_nonpm_router.sh").read_text()
    client = (ROOT / "make_scripts/build_bseed_mains_client.sh").read_text()

    assert "STOCK_MANUFACTURER_NAME='_TZ3000_o1jzcxou'" in router
    assert "STOCK_IMAGE_TYPE=54179" in router
    assert "IMAGE_TYPE=43555" in router
    assert "OTA_VERSION=0xFFFFFFFF" in router
    assert 'bseed_nonpm_release.py vars --role router' in router
    assert 'BUILD ONLY: never publishes, flashes, or mutates a live device.' in router

    # The Client artifact intentionally accepts only the already-custom Router
    # identity. Stock conversion and role conversion are two observable stages.
    assert "ROUTER_IMAGE_TYPE=43555" in client
    assert "FROM_ROUTER_OTA" in client
    assert '"stockConversion": False' in client
    assert "from_tuya" not in client.lower()


def test_nonpm_canary_workflow_builds_matrix_and_verifies_history_separately():
    workflow = (ROOT / ".github/workflows/bseed-nonpm-client-canary.yml").read_text()
    # Stored historical bytes are verified directly; they are never rebuilt
    # from current shared source to compare hashes.
    assert "make bseed/identity-gate" in workflow
    assert "git worktree add" not in workflow
    assert "merge-base" not in workflow
    assert "baseline-nonpm" not in workflow
    assert "client-control-nonpm" not in workflow
    assert "9e5a22ec58513ae1cd2c8a4fe7df602d0fc413df4d36c5dbc34ee9bb3550130f" not in workflow
    # The candidate path builds both fresh roles with clean rebuilds.
    assert "helper_scripts/bseed_nonpm_variant_matrix.py" in workflow
    assert "set -o pipefail" in workflow
    assert "build/bseed-nonpm-role-matrix-*/ROLE_MATRIX.json" in workflow
    assert "build/bseed-nonpm-role-matrix-*/router/*" in workflow
    assert "build/bseed-nonpm-role-matrix-*/client/*" in workflow
    assert "bseed-nonpm-role-matrix-${{ github.event.pull_request.head.sha || github.sha }}" in workflow
    # Exact candidate head, never a synthetic merge checkout.
    assert "ref: ${{ github.event.pull_request.head.sha || github.sha }}" in workflow
    assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_HEAD"' in workflow


def test_nonpm_canary_identity_guard_is_exact():
    script = (ROOT / "make_scripts/build_bseed_ts011f_nonpm_router.sh").read_text()
    assert "BOARD='OUTLET_BSEED_TS011F'" in script
    assert "STOCK_MANUFACTURER_NAME='_TZ3000_o1jzcxou'" in script
    assert "CANONICAL='o1jzcxou;TS011F-BS;LC2;SB4u;RC3;ID2;M;'" in script
    assert '[[ "${db_values[4]}" == "$STOCK_MANUFACTURER_NAME" ]]' in script
    assert '[[ "${db_values[3]}" == "$STOCK_IMAGE_TYPE" ]]' in script
    assert "[[ \"${db_values[6]}\" == 'Telink' && \"${db_values[7]}\" == 'TLSR8258' ]]" in script
