"""Build-only regression: a stored hardware pin map cannot rewire BSEED sockets."""
from pathlib import Path
import subprocess
import pytest

PM = "b28wrpvx;TS011F-BS-PM;LC3;SB5u;RD2;IB4;M;"
NONPM = "o1jzcxou;TS011F-BS;LC2;SB4u;RC3;ID2;M;"

@pytest.fixture(scope="module", params=[("pm", "router"), ("pm", "client"),
                                       ("nonpm", "router"), ("nonpm", "client")])
def socket_stub(request):
    board, role = request.param
    path = Path(f"build/stub/antibrick_{board}_{role}").resolve()
    args = ["make", "-s", "-C", "src/stub", "build", f"BINARY={path}"]
    args.append("BSEED_PM_B28WRPVX=1" if board == "pm" else
                "DEVICE_CONFIG_GUARD=BSEED_TS011F_NONPM")
    if role == "client":
        args.append("STUB_END_DEVICE=1")
    subprocess.run(args, check=True, capture_output=True, text=True)
    return path, PM if board == "pm" else NONPM

@pytest.mark.parametrize("mutation", [None, "relay_pin", "button_pull", "led_pin",
                                      "board_identity", "extra_gpio", "malformed"])
def test_boot_uses_only_approved_socket_pinmap(socket_stub, tmp_path, mutation):
    binary, canonical = socket_stub
    candidate = canonical
    if mutation == "relay_pin": candidate = candidate.replace("RD2;", "RB5;").replace("RC3;", "RB5;")
    if mutation == "button_pull": candidate = candidate.replace("SB5u;", "SB5d;").replace("SB4u;", "SB4d;")
    if mutation == "led_pin": candidate = candidate.replace("LC3;", "LC4;").replace("LC2;", "LC4;")
    if mutation == "board_identity": candidate = "Other;Model;" + candidate.split(";", 2)[2]
    if mutation == "extra_gpio": candidate = candidate[:-1] + ";RA0;"
    if mutation == "malformed": candidate = candidate.rstrip(";")
    proc = subprocess.run([str(binary), "--device-config", candidate, "--freeze-time"],
                          input="q\n", cwd=tmp_path, capture_output=True, text=True,
                          timeout=10, check=False)
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, output[-1500:]
    assert "Config parsed successfully" in output
    # The fallback is RAM-only: preserve the suspect NVM for recovery/forensics.
    raw = (tmp_path / "stub_nvm_data" / "item_02.bin").read_bytes()
    assert int.from_bytes(raw[:2], "little") == len(candidate)
    assert raw[2:2 + len(candidate)] == candidate.encode("ascii")
    assert ("Stored device config is unsafe" in output) == (mutation is not None)
    assert "Initializing Zigbee with 1 switches, 1 relays" in output or mutation is not None


def test_all_bseed_socket_release_builds_enable_guard():
    client = Path("make_scripts/build_bseed_mains_client.sh").read_text()
    router = Path("make_scripts/build_bseed_ts011f_nonpm_router.sh").read_text()
    pm = Path("make_scripts/build_bseed_ts011f_pm_v8.sh").read_text()
    assert "EXTRA_ARGS=(DEVICE_CONFIG_GUARD=BSEED_TS011F_NONPM)" in client
    assert "DEVICE_CONFIG_GUARD=BSEED_TS011F_NONPM" in router
    assert "BSEED_PM_B28WRPVX=1" in pm and "BSEED_PM_B28WRPVX=1" in client
    source = Path("src/device_config/config_nv.c").read_text()
    preflight = source.split("bool device_config_prepare_for_parse(void) {", 1)[1].split("#ifdef", 1)[0]
    assert preflight.count("device_config_is_valid(") == 2


def test_invalid_persisted_zcl_settings_cannot_block_socket_boot(socket_stub, tmp_path):
    """Corrupt duration/modes in both switch and relay records, then cold boot."""
    binary, canonical = socket_stub
    first = subprocess.run([str(binary), "--device-config", canonical, "--freeze-time"],
                           input="q\n", cwd=tmp_path, capture_output=True, text=True,
                           timeout=10, check=False)
    assert first.returncode == 0, first.stderr[-1200:]
    nvm = tmp_path / "stub_nvm_data"
    nvm.joinpath("item_04.bin").write_bytes(bytes([255, 2, 255, 255, 0, 0, 255, 255]))
    nvm.joinpath("item_09.bin").write_bytes(bytes([1, 127, 255, 255]))
    second = subprocess.run([str(binary), "--freeze-time"], input="q\n",
                            cwd=tmp_path, capture_output=True, text=True,
                            timeout=10, check=False)
    output = second.stdout + second.stderr
    assert second.returncode == 0, output[-1500:]
    assert "Config parsed successfully" in output
    assert "Initializing Zigbee with 1 switches, 1 relays" in output
    assert "Stored device config is unsafe" not in output
