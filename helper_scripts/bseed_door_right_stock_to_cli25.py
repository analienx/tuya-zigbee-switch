"""Strict read-only validator for EXACT BedroomSocketDoorR stock->PM Client cli25.

This validates the stock OTA transport header against sealed PM Client bytes.
It never proves the Tuya bootloader will accept or execute that Client payload.
No live OTA admission is implied by generating a wrapper or index.
"""
from pathlib import Path
import hashlib
import struct

DOOR = "BedroomSocketDoorR"
IEEE = "0xa4c1386e4892f726"
NATIVE_SHA256 = "4246f097a8927fcfa9a02d16d7ee966cc96ea14487681b2cd9f34b50b41dc914"
WRAPPER_SHA256 = "e5c32d4476a2ca70a3c4f440545a5ca8c10d44222a5c70d311bb6629757698e8"
FILE_VERSION=0x12053024

PINNED = {
    "direct_stock_client_one_device": True,
    "device": DOOR, "ieee": IEEE,
    "manufacturer": "_TZ3000_b28wrpvx", "model": "TS011F",
    "preflash_role": "Router", "postflash_role": "EndDevice",
    "postflash_manufacturer": "b28wrpvx",
    "postflash_model": "TS011F-BS-PM",
    "postflash_build": "1.2.5-bseedcli25",
    "image_type": 54179, "manufacturer_code": 4417,
    "file_version": "0xffffffff",
    "sha256": WRAPPER_SHA256, "native_sha256": NATIVE_SHA256,
    "force_test_transition": False,
    "non_pm": False, "require_pm": True,
    "relay_get_key": "state", "expect_relay": "OFF",
    "source_rejoin_strategy": "scoped",
    "allow_join_all_fallback": False,
    "join_via": "BedroomSocketCabinetL", "join_seconds": 120,
    "network_id": "0x00124b002d12b1fd",
    "network_lock_shared": True, "parallel_ota_max": 2,
    "concurrent_ota_ieees": ["0xa4c138ac993cc245"],
    "parallel_role_change_pair": "bedroom-balcony-doorR-crossrole-20261115",
    "block_bytes": 32, "response_delay_ms": 1800,
    "request_timeout_ms": 600000,
    "pm_preflash_load_proof": "physically_unloaded",
}
def verify_waiver(profile):
    import json
    authorization = profile.get('stock_waiver_authorization')
    if not authorization or not Path(authorization).is_file():
        raise ValueError('DoorR requires exact explicit stock PM risk authorization')
    proof = json.loads(Path(authorization).read_text(encoding='utf8'))
    if not (
        proof.get('device') == DOOR and proof.get('ieee') == IEEE
        and proof.get('wrapper_sha256') == WRAPPER_SHA256
        and proof.get('target_build') == '1.2.5-bseedcli25'
        and proof.get('user_authorized_direct_client') is True
        and proof.get('user_authorized_parallel_ota') is True
        and proof.get('physical_unplugged_proven') is False
        and proof.get('stock_pm_stamp_supported') is False
        and proof.get('stock_double_get_consistency_required') is True
        and proof.get('hardware_acceptance') is False
    ):
        raise ValueError('DoorR stock PM waiver does not match exact authorized risk')
    return proof


def verify_exact(profile):
    for k, expected in PINNED.items():
        if profile.get(k) != expected:
            raise ValueError("DoorR exact direct stock->Client profile differs: " + k)
    if profile.get("preflash_build") not in (None,""):
        raise ValueError("Pristine stock firmware cannot be assigned a guessed custom software build")
    if not Path(profile["image"]).is_absolute() or not Path(profile["native_image"]).is_absolute():
        raise ValueError("Private native and wrapper paths must be absolute")
    outer=Path(profile["image"]).read_bytes()
    native=Path(profile["native_image"]).read_bytes()
    if (hashlib.sha256(outer).hexdigest()!=WRAPPER_SHA256 or
        hashlib.sha256(native).hexdigest()!=NATIVE_SHA256):
        raise ValueError("Native PM Client or stock wrapper image digest differs")
    if len(native)!=len(outer) or len(native)!=177666 or native[56:]!=outer[56:]:
        raise ValueError("Stock transport must preserve the entire sealed Client payload")
    if struct.unpack_from("<HHI",native,10)!=(4417,65024,FILE_VERSION):
        raise ValueError("Native PM Client image header differs")
    if struct.unpack_from("<HHI",outer,10)!=(4417,54179,0xffffffff):
        raise ValueError("Stock Tuya transport header differs")
    check=bytearray(native)
    struct.pack_into("<H",check,12,54179)
    struct.pack_into("<I",check,14,0xffffffff)
    if outer!=bytes(check):
        raise ValueError("Wrapper may only modify image type and OTA file version")
    return {
        "exact_target":IEEE,"source":"stock Tuya Router",
        "target":"1.2.5-bseedcli25 EndDevice",
        "native_version":FILE_VERSION,"native_role":"EndDevice",
        "stock_wrapper_sha256":WRAPPER_SHA256,
        "recovery_tested":False,
        "hardware_acceptance":False,
        "stock_client_boot_acceptance_proven":False,
        "ota_command_authorized":False,
    }
