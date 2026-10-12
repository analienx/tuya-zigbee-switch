"""Guarantee Silicon Labs Router/Client manifests link the real shared sources.

These are genuine firmware implementation files, not placeholders or stubs.
The GitHub-hosted SLT/SLC SDK build separately proves compiler/linker success.
"""
from pathlib import Path

REQUIRED = (
    "base_components/overload_protection.c",
    "base_components/energy_measurement/hlw8012.c",
    "device_config/device_migration.c",
    "device_config/pm_legacy_migration.c",
    "zigbee/electrical_measurement_cluster.c",
    "zigbee/metering_cluster.c",
    "zigbee/zcl_write_string_gate.c",
    "zigbee/zcl_write_dispatch.c",
)


def test_silabs_role_manifests_include_real_shared_sources():
    for role in ("zigbee", "zigbee_end_device"):
        manifest = Path(f"src/silabs/slcp_files/{role}.slcp").read_text(
            encoding="utf-8"
        )
        for source in REQUIRED:
            assert Path("src", source).is_file(), source
            assert manifest.count(f"- {{path: ../../{source}}}") == 1, (
                role, source
            )
