"""A converter release must be immutable and advertise required PM features."""
import pytest
from helper_scripts.bseed_converter_release import build_manifest, REQUIRED_MARKERS, REQUIRED_PM_BUILDS


def candidate():
    return ("const bseedPmElectricityMeter = 1;\n" +
            "const knownBuilds = new Set([" +
            ",".join('"' + b + '"' for b in REQUIRED_PM_BUILDS) + "]);\n" +
            "\n".join(REQUIRED_MARKERS) +
            "\nmodule.exports = [];\n").encode("utf-8")


def test_release_identity_and_capability_set():
    sha = "a" * 40
    m = build_manifest(candidate(), sha)
    assert m["source_commit"] == sha
    assert m["size_bytes"] == len(candidate())
    assert m["schema"] == 1
    assert set(REQUIRED_PM_BUILDS).issubset(m["bseed_known_pm_builds"])
    assert set(REQUIRED_MARKERS) == set(m["capabilities"])


@pytest.mark.parametrize("marker", REQUIRED_MARKERS)
def test_missing_stamp_or_quarantine_refuses_release(marker):
    bad = candidate().replace(marker.encode(), b"MISSING", 1)
    with pytest.raises(ValueError, match="missing BSEED capability"):
        build_manifest(bad, "a" * 40)


@pytest.mark.parametrize("build", REQUIRED_PM_BUILDS)
def test_missing_supported_new_pm_build_fails(build):
    bad = candidate().replace(('"' + build + '"').encode(), b'"removed"', 1)
    with pytest.raises(ValueError, match="unhandled PM release"):
        build_manifest(bad, "a" * 40)


@pytest.mark.parametrize("sha", ["", "refs/heads/main", "x"*40, "a"*39])
def test_unpinned_build_ref_rejected(sha):
    with pytest.raises(ValueError, match="exact 40-char"):
        build_manifest(candidate(), sha)
