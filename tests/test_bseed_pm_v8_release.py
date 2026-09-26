import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "make_scripts" / "build_bseed_ts011f_pm_v8.sh"
VALIDATOR = ROOT / "make_scripts" / "validate_bseed_ts011f_pm_v8.py"
BOARD = "OUTLET_BSEED_PM_TS011F"
CONFIG = "b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;"


def test_pm_device_db_identity_is_the_hardware_proven_target():
    db = yaml.safe_load((ROOT / "device_db.yaml").read_text(encoding="utf-8"))
    entry = db[BOARD]
    assert entry["config_str"] == CONFIG
    assert entry["stock_manufacturer_id"] == 4417
    assert entry["stock_image_type"] == 54179
    assert entry["firmware_image_type"] == 43556
    assert entry["device_type"] == "router"
    assert entry["mcu_family"] == "Telink"
    assert entry["mcu"] == "TLSR8258"


def test_pm_build_pins_proven_meter_and_sleepy_parent_canary_version():
    text = BUILD.read_text(encoding="utf-8")
    assert "BOARD='OUTLET_BSEED_PM_TS011F'" in text
    assert f"CANONICAL='{CONFIG}'" in text
    assert "MANUFACTURER_CODE=4417" in text
    assert "IMAGE_TYPE=43556" in text
    assert "SW_BUILD='1.2.5-bseedv8u4'" in text
    assert "FILE_VERSION_HEX='0x12053007'" in text
    assert "FILE_VERSION_DEC=302329863" in text
    assert "VOLTAGE_MULTIPLIER=161460" in text
    assert "CURRENT_MULTIPLIER=144679" in text
    assert "POWER_MULTIPLIER=16989" in text
    assert "BSEED_PM_B28WRPVX=1" in text
    assert "BSEED_PM_B28WRPVX_PROTECTION=1" in text


def test_pm_build_is_build_only_and_verifies_ota_header_and_hashes():
    text = BUILD.read_text(encoding="utf-8")
    assert "make -C src/telink build" in text
    assert "make -C src/telink ota" in text
    assert 'struct.unpack("<I5HIH32sI"' in text
    assert '"sha256"' in text
    assert '"sha512"' in text
    assert "make -C src/telink flash" not in text
    assert " tlsrpgm" not in text.lower()
    assert "publish" not in "\n".join(
        line for line in text.lower().splitlines() if not line.lstrip().startswith("#")
    )


def test_pm_validator_proves_same_sha_pm_and_ts0726_builds():
    text = VALIDATOR.read_text(encoding="utf-8")
    assert 'PM_INTEGRATION_BASE = "8ed8ddfcf5892f0b801d19df4882a145a42aa3b1"' in text
    assert '"tests/test_unified_pm_v8.py"' in text
    assert '"tests/test_pm_cluster_layout_guard.py"' in text
    assert '"tests/test_bseed_pm_v8_release.py"' in text
    assert '"tests/test_nvm_migration_version.py"' in text
    assert '"tests/test_config_resource_guard.py"' in text
    assert 'run(["bash", "make_scripts/build_bseed_ts011f_pm_v8.sh"])' in text
    assert 'run(["bash", "make_scripts/build_bseed_ts0726_v8.sh"])' in text
    assert '"board": "OUTLET_BSEED_PM_TS011F"' in text
    assert '"swBuildId": "1.2.5-bseedv8u4"' in text
    assert '"fileVersion": 302329863' in text
    assert '"imageType": 43556' in text
    assert '"samplingSemantics": "hardware-proven-8b8cc492"' in text
    assert '"fileVersion": 285356042' in text
    assert '"imageType": 45577' in text
    assert "device flash" in text


def test_pm_router_recovery_candidate_pins_next_monotonic_version():
    text = BUILD.read_text(encoding="utf-8")
    assert "BSEED_PM_ROUTER_RECOVERY" in text
    assert "SW_BUILD='1.2.5-bseedv8u5-rc4'" in text
    assert "FILE_VERSION_HEX='0x12053011'" in text
    assert "FILE_VERSION_DEC=302329873" in text
    assert "build/bseed-ts011f-pm-router-v8u5-rc4" in text
    # Default release stays pinned; recovery is opt-in only.
    assert "SW_BUILD='1.2.5-bseedv8u4'" in text
    assert "FILE_VERSION_HEX='0x12053007'" in text


def test_pm_router_read_fix_has_new_immutable_identity():
    text = BUILD.read_text(encoding="utf-8")
    assert "BSEED_PM_ROUTER_READ_FIX" in text
    assert "SW_BUILD='1.2.5-bseedv8u5-rc5'" in text
    assert "FILE_VERSION_HEX='0x12053012'" in text
    assert "FILE_VERSION_DEC=302329874" in text
    assert "build/bseed-ts011f-pm-router-v8u5-rc5" in text


def test_pm_router_builds_from_client_return_wrapper():
    text = BUILD.read_text(encoding="utf-8")
    assert "CLIENT_IMAGE_TYPE=65024" in text
    assert 'FROM_CLIENT_OTA="$OUT_DIR/from-client.ota"' in text
    assert 'OTA_IMAGE_TYPE="$CLIENT_IMAGE_TYPE"' in text
    assert '"fromClientOtaHeader"' in text
    assert '"clientReturn"' in text
    assert "[12, 13]" in text
    assert "from_tuya" in text.lower()


def test_release_handoff_scripts_parse_before_executor_use():
    subprocess.run(["bash", "-n", str(BUILD)], cwd=ROOT, check=True)
    compile(
        VALIDATOR.read_text(encoding="utf-8"),
        str(VALIDATOR),
        "exec",
    )


def test_pm_matrix_publishes_same_role_canaries_without_cross_role_recovery_job():
    workflow = (ROOT / ".github/workflows/bseed-pm-matrix.yml").read_text()
    assert "build/bseed-pm-role-matrix-*/router/forward.ota" in workflow
    assert "build/bseed-pm-role-matrix-*/client/forward.ota" in workflow
    assert "BSEED_PM_ROUTER_RECOVERY=1" not in workflow
    assert "from-client.ota" not in workflow
