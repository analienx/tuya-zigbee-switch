"""Version automation: releases take FILEVER from emit-make-vars, never by hand."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "helper_scripts" / "bseed_ota_identity.py"
IMAGE_TYPES = (43556, 65024, 43555, 65026)


def run(*args):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT,
                          text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, check=True)
    return proc.stdout


def test_makefile_exposes_suggest_next_for_every_release_line():
    makefile = (ROOT / "Makefile").read_text()
    assert "bseed/suggest-next:" in makefile
    for image_type in IMAGE_TYPES:
        assert f"suggest-next --image-type {image_type}" in makefile


def test_emit_make_vars_matches_suggest_next():
    for image_type in IMAGE_TYPES:
        nxt = json.loads(run("suggest-next", "--image-type", str(image_type)))
        out = run("emit-make-vars", "--image-type", str(image_type),
                  "--version-str", "9.9.9-bseedautomationprobe")
        assert f"FILE_VERSION={nxt['next_file_version_hex']}" in out
        assert "VERSION_STR=9.9.9-bseedautomationprobe" in out
