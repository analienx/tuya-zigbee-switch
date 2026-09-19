"""Regression for the Telink PM UNSUPPORTED_ATTRIBUTE root cause.

Advertising a PM cluster in a simple descriptor without a cluster register
handler does not expose the attributes to the Telink ZCL read dispatcher.
"""

from pathlib import Path


ZCL = (Path(__file__).resolve().parents[1] / "src/telink/hal/zigbee_zcl.c").read_text(
    encoding="utf-8"
)


def test_pm_clusters_have_real_attribute_registration_callbacks() -> None:
    for cluster, callback in (
        ("ZCL_CLUSTER_MS_ELECTRICAL_MEASUREMENT", "register_pm_electrical_attrs"),
        ("ZCL_CLUSTER_SE_METERING", "register_pm_metering_attrs"),
    ):
        assert f"if (cluster_id == {cluster}) {{\n        return {callback};" in ZCL
        assert f"return zcl_registerCluster(ep, {cluster}," in ZCL
    assert "mfr, n, attrs, NULL, cb);" in ZCL
