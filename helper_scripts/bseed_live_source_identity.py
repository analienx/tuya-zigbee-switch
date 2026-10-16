"""Fail-closed live source identity checks for BSEED OTA campaigns.

Normal custom firmware is identified by its Basic softwareBuildID. A narrowly
scoped legacy PM Router fallback exists for old b28wrpvx Router builds that do
not expose softwareBuildID: those campaigns must pin preflash_file_version in
the sealed identity policy, and a fresh target-originated OTA state must report
that exact installed version. A present-but-wrong Basic build never falls back.
"""

from bseed_socket_version_policy import number, split_manufacturer


def legacy_pm_router_file_version(profile):
    """Return the pinned legacy PM Router source version, or None.

    The version-policy gate separately proves that this numeric version maps to
    profile['preflash_build'] in the sealed registry. Here we additionally
    constrain the runtime fallback to exactly the custom PM Router board/role.
    """
    raw = profile.get("preflash_file_version")
    if raw is None:
        return None
    board, stock_prefix = split_manufacturer(profile.get("manufacturer", ""))
    if not (
        board == "b28wrpvx"
        and stock_prefix is None
        and profile.get("model") == "TS011F-BS-PM"
        and profile.get("preflash_role") == "Router"
        and profile.get("require_pm") is True
        and profile.get("non_pm") is not True
    ):
        raise ValueError(
            "preflash_file_version fallback is limited to exact custom PM Router"
        )
    return number(raw)


def require_inventory_source(profile, device):
    """Validate inventory identity without treating missing legacy Basic as proof."""
    expected = profile.get("preflash_build")
    build = device.get("software_build_id")
    if build is not None:
        if expected and build != expected:
            raise ValueError("Live source build differs from version-policy baseline")
        return {"method": "basic_build", "build": build, "file_version": None}
    legacy = legacy_pm_router_file_version(profile)
    if expected and legacy is None:
        raise ValueError("Live source Basic build is missing")
    return {
        "method": "legacy_pm_router_pending_fresh_file_version",
        "build": expected,
        "file_version": None,
    }


def require_fresh_source(profile, target):
    """Require exact source identity from a fresh, non-retained target message."""
    target = target or {}
    device = target.get("device") or {}
    expected = (
        profile.get("ieee"),
        profile.get("preflash_role"),
    )
    found = (device.get("ieeeAddr"), device.get("type"))
    if found != expected:
        raise ValueError(
            "Fresh source identity role/IEEE absent or mismatched: " + repr(found)
        )

    expected_build = profile.get("preflash_build")
    build = device.get("softwareBuildID")
    if build is not None:
        if expected_build and build != expected_build:
            raise ValueError(
                "Fresh source identity Basic build mismatched: " + repr(build)
            )
        return {"method": "basic_build", "build": build, "file_version": None}

    expected_version = legacy_pm_router_file_version(profile)
    installed = (target.get("update") or {}).get("installed_version")
    if expected_version is None or installed != expected_version:
        raise ValueError(
            "Fresh source identity legacy OTA fileVersion absent or mismatched: "
            + repr(installed)
        )
    return {
        "method": "ota_file_version",
        "build": expected_build,
        "file_version": installed,
    }


def require_check_record_source(profile, record):
    """Bind a flash to the immediately preceding canonical source proof."""
    expected_version = legacy_pm_router_file_version(profile)
    if expected_version is None:
        return True
    proof = record.get("source_identity") or {}
    if (
        proof.get("method") != "ota_file_version"
        or proof.get("file_version") != expected_version
        or proof.get("build") != profile.get("preflash_build")
    ):
        raise AssertionError(
            "Legacy PM Router flash requires fresh exact source fileVersion in LAST_CHECK"
        )
    return True
