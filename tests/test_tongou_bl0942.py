import pytest

from client import StubProc
from conftest import Device

ZCL_CLUSTER_ELECTRICAL_MEASUREMENT = 0x0B04
ZCL_CLUSTER_METERING = 0x0702
ATTR_METERING_DIVISOR = 0x0302
ATTR_CURRENT_SUMMATION = 0x0000
ATTR_RMS_VOLTAGE = 0x0505
ATTR_CALIBRATION_VALUES = 0xFF20
ATTR_OVERLOAD_POWER_LIMIT = 0xFF30
ATTR_OVERLOAD_CURRENT_LIMIT = 0xFF31


def _le24(value: int) -> bytes:
    return bytes((value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF))


def _bl0942_frame(*, voltage_raw: int, current_raw: int, power_raw: int, cf_count: int) -> bytes:
    body = bytearray([0x55])
    body += _le24(current_raw)
    body += _le24(voltage_raw)
    body += _le24(current_raw)  # I_FAST_RMS is irrelevant to this driver
    body += _le24(power_raw & 0xFFFFFF)
    body += _le24(cf_count & 0xFFFFFF)
    body += bytes((0, 0))       # frequency
    body += bytes((0, 0, 0, 0))
    assert len(body) == 22
    checksum = (0x58 + sum(body)) & 0xFF
    body.append(checksum ^ 0xFF)
    return bytes(body)


def _inject_uart(proc: StubProc, payload: bytes) -> None:
    command = "uart_rx " + " ".join(f"{byte:02X}" for byte in payload)
    result = proc.exec(command)
    assert result.ok


@pytest.fixture
def device_config() -> str:
    return "StubManufacturer;StubDevice;RC2;EBB0B7;M;"


def test_eb_token_registers_metering_cluster(device: Device):
    assert device.read_zigbee_attr(
        1, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT, ATTR_RMS_VOLTAGE
    ) is not None
    assert device.read_zigbee_attr(
        1, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT, ATTR_CALIBRATION_VALUES
    ) == "V413A261W105"


def test_eb_calibration_markers_override_defaults():
    with StubProc(
        device_config="StubManufacturer;StubDevice;RC2;EBB0B7V412A256W103;M;"
    ) as proc:
        device = Device(proc)
        assert device.read_zigbee_attr(
            1, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT, ATTR_CALIBRATION_VALUES
        ) == "V412A256W103"


def test_tongou_63a_limits_do_not_overflow():
    with StubProc(
        device_config=(
            "StubManufacturer;StubDevice;RC2;EBB0B7;"
            "OLC63000P65000;M;"
        )
    ) as proc:
        device = Device(proc)
        assert device.read_zigbee_attr(
            1, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT, ATTR_OVERLOAD_CURRENT_LIMIT
        ) == "63000"
        assert device.read_zigbee_attr(
            1, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT, ATTR_OVERLOAD_POWER_LIMIT
        ) == "14490"


def test_generic_metering_keeps_one_wh_wire_resolution(device: Device):
    assert device.read_zigbee_attr(
        1, ZCL_CLUSTER_METERING, ATTR_METERING_DIVISOR
    ) == "1000"


def test_tongou_compat_uses_stock_divisor_100():
    with StubProc(
        device_config="StubManufacturer;StubDevice;TQ;RC2;EBB0B7;M;"
    ) as proc:
        device = Device(proc)
        assert device.read_zigbee_attr(
            1, ZCL_CLUSTER_METERING, ATTR_METERING_DIVISOR
        ) == "100"


def test_bl0942_cf_counter_drives_energy_not_rounded_power():
    with StubProc(device_config="StubManufacturer;StubDevice;RC2;EBB0B7;M;") as proc:
        device = Device(proc)
        frame1 = _bl0942_frame(
            voltage_raw=3650000, current_raw=251000, power_raw=143500, cf_count=1000
        )
        frame2 = _bl0942_frame(
            voltage_raw=3650000, current_raw=251000, power_raw=143500, cf_count=1054
        )
        _inject_uart(proc, frame1)
        device.step_time(1000)
        _inject_uart(proc, frame2)
        device.step_time(1000)
        device.status()  # one more app loop consumes the frame parsed by the task

        # 54 CF counts * multiplier 105 * 8 / 4500 = 10 Wh + remainder.
        assert device.read_zigbee_attr(
            1, ZCL_CLUSTER_METERING, ATTR_CURRENT_SUMMATION
        ) == "10"


def test_tongou_divisor_coalesces_ten_wh_per_wire_unit():
    with StubProc(
        device_config="StubManufacturer;StubDevice;TQ;RC2;EBB0B7;M;"
    ) as proc:
        device = Device(proc)
        _inject_uart(
            proc,
            _bl0942_frame(
                voltage_raw=3650000, current_raw=251000,
                power_raw=143500, cf_count=2000
            ),
        )
        device.step_time(1000)
        _inject_uart(
            proc,
            _bl0942_frame(
                voltage_raw=3650000, current_raw=251000,
                power_raw=143500, cf_count=2054
            ),
        )
        device.step_time(1000)
        device.status()  # one more app loop consumes the frame parsed by the task

        assert device.read_zigbee_attr(
            1, ZCL_CLUSTER_METERING, ATTR_CURRENT_SUMMATION
        ) == "1"
        assert device.read_zigbee_attr(
            1, ZCL_CLUSTER_METERING, ATTR_METERING_DIVISOR
        ) == "100"


def test_bl0942_cf_counter_handles_24bit_wrap_without_energy_spike():
    with StubProc(device_config="StubManufacturer;StubDevice;RC2;EBB0B7;M;") as proc:
        device = Device(proc)
        for cf in (0xFFFFF0, 0x000020):
            _inject_uart(
                proc,
                _bl0942_frame(
                    voltage_raw=3650000, current_raw=251000,
                    power_raw=143500, cf_count=cf
                ),
            )
            device.step_time(1000)
        device.status()

        # Natural wrap delta is 48 counts => floor(48*105*8/4500) = 8 Wh.
        assert device.read_zigbee_attr(
            1, ZCL_CLUSTER_METERING, ATTR_CURRENT_SUMMATION
        ) == "8"


def test_bl0942_backward_cf_jump_rebaselines_as_chip_reset():
    with StubProc(device_config="StubManufacturer;StubDevice;RC2;EBB0B7;M;") as proc:
        device = Device(proc)
        for cf in (10000, 100):
            _inject_uart(
                proc,
                _bl0942_frame(
                    voltage_raw=3650000, current_raw=251000,
                    power_raw=143500, cf_count=cf
                ),
            )
            device.step_time(1000)
        device.status()

        # A backwards jump away from the natural wrap boundary is a BL0942
        # restart/reset, not 16 million counter increments.
        assert device.read_zigbee_attr(
            1, ZCL_CLUSTER_METERING, ATTR_CURRENT_SUMMATION
        ) == "0"


def test_bl0942_extreme_register_values_saturate_instead_of_wrapping():
    # Raw metering faults and bad calibration must not turn a very high
    # measurement into a deceptively low value through integer overflow.
    with StubProc(device_config="StubManufacturer;StubDevice;RC2;EBB0B7W1050;M;") as proc:
        device = Device(proc)
        _inject_uart(
            proc,
            _bl0942_frame(
                voltage_raw=0xFFFFFF, current_raw=0xFFFFFF,
                power_raw=0x7FFFFF, cf_count=1000,
            ),
        )
        device.step_time(1000)
        device.status()
        assert device.read_zigbee_attr(
            1, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT, ATTR_RMS_VOLTAGE
        ) == "65535"
        assert device.read_zigbee_attr(
            1, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT, 0x0508
        ) == "65535"
        assert device.read_zigbee_attr(
            1, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT, 0x050B
        ) == "32767"


def test_bl0942_calibration_rejects_missing_and_stale_meter_frames():
    with StubProc(device_config="StubManufacturer;StubDevice;RC2;EBB0B7;M;") as proc:
        device = Device(proc)
        original = device.read_zigbee_attr(
            1, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT, ATTR_CALIBRATION_VALUES
        )
        # No valid frame: calibration must not learn from zero/uninitialized data.
        device.write_zigbee_attr(1, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT, 0xFF10, 24000)
        assert device.read_zigbee_attr(
            1, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT, ATTR_CALIBRATION_VALUES
        ) == original
        _inject_uart(proc, _bl0942_frame(
            voltage_raw=3650000, current_raw=251000,
            power_raw=143500, cf_count=1000))
        device.step_time(1000)
        device.status()
        device.write_zigbee_attr(1, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT, 0xFF10, 24000)
        fresh = device.read_zigbee_attr(
            1, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT, ATTR_CALIBRATION_VALUES
        )
        assert fresh != original
        # No new UART frame for 6 s, so the previous calibration sample expires.
        device.step_time(6000)
        device.status()
        device.write_zigbee_attr(1, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT, 0xFF10, 25000)
        assert device.read_zigbee_attr(
            1, ZCL_CLUSTER_ELECTRICAL_MEASUREMENT, ATTR_CALIBRATION_VALUES
        ) == fresh
