"""Regression for the Telink PM UNSUPPORTED_ATTRIBUTE root cause."""

from pathlib import Path


ZCL = (Path(__file__).resolve().parents[1] / "src/telink/hal/zigbee_zcl.c").read_text(
    encoding="utf-8"
)


APP_CFG = (Path(__file__).resolve().parents[1] / "src/telink/configs/app_cfg.h").read_text(
    encoding="utf-8"
)


def test_bseed_pm_uses_telink_standard_cluster_registration_callbacks() -> None:
    assert "#ifdef BSEED_PM_B28WRPVX" in APP_CFG
    assert "#define ZCL_ELECTRICAL_MEASUREMENT_SUPPORT 1" in APP_CFG
    assert "#define ZCL_METERING_SUPPORT" in APP_CFG
    for cluster, callback in (
        ("ZCL_CLUSTER_MS_ELECTRICAL_MEASUREMENT", "zcl_electricalMeasure_register"),
        ("ZCL_CLUSTER_SE_METERING", "zcl_metering_register"),
    ):
        block = ZCL.split(f"if (cluster_id == {cluster}) {{", 1)[1].split("}", 1)[0]
        assert "#ifdef BSEED_PM_B28WRPVX" in block
        assert f"return {callback};" in block


def test_non_bseed_builds_keep_lightweight_pm_registration() -> None:
    assert "#ifndef BSEED_PM_B28WRPVX" in ZCL
    assert "return zcl_registerCluster(ep, ZCL_CLUSTER_MS_ELECTRICAL_MEASUREMENT," in ZCL
    assert "return zcl_registerCluster(ep, ZCL_CLUSTER_SE_METERING," in ZCL
    assert "mfr, n, attrs, NULL, cb);" in ZCL
