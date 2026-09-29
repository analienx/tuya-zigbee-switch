"""Inspect real release-script make arguments without invoking the native compiler."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
from bseed_pm_release import CLIENT, ROUTER, RELEASE_DATE
from bseed_nonpm_release import CLIENT as NONPM_CLIENT, ROUTER as NONPM_ROUTER


@pytest.mark.parametrize('script,mode,candidate,pm', [
    ('build_bseed_mains_client.sh', 'pm', CLIENT, True),
    ('build_bseed_ts011f_pm_v8.sh', None, ROUTER, True),
    ('build_bseed_mains_client.sh', 'nonpm', NONPM_CLIENT, False),
    ('build_bseed_ts011f_nonpm_router.sh', None, NONPM_ROUTER, False),
])
def test_actual_release_make_inputs(tmp_path, script, mode, candidate, pm):
    fake = tmp_path / 'make'
    capture = tmp_path / 'arguments.json'
    fake.write_text('#!/usr/bin/env python3\nimport json, os, sys\n'
                    'if "build" in sys.argv:\n'
                    '    open(os.environ["BSEED_CAPTURE"], "w").write(json.dumps(sys.argv[1:]))\n'
                    '    sys.exit(83)\n')
    fake.chmod(0o755)
    output = tmp_path / 'artifact'
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'],
               BSEED_CAPTURE=str(capture), BSEED_PM_CONSOLIDATED='1',
               BSEED_PM_CONSOLIDATED_OUTPUT=str(output))
    result = subprocess.run(['bash', str(ROOT / 'make_scripts' / script),
                             *([mode] if mode else []), str(output)], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 83, result.stdout + result.stderr
    args = json.loads(capture.read_text())
    assert 'VERSION_STR=' + candidate['build'] in args
    assert 'FILE_VERSION=' + hex(candidate['version']) in args
    assert 'BSEED_BUILD_DATE=' + candidate.get('date', RELEASE_DATE) in args
    assert 'IMAGE_TYPE=' + str(candidate['type']) in args
    assert ('BSEED_PM_B28WRPVX=1' in args) == pm
    if not pm:
        assert 'DEVICE_CONFIG_GUARD=BSEED_TS011F_NONPM' in args
    if candidate['role'] == 'EndDevice':
        assert 'client.mk' in args
    else:
        assert 'DEVICE_TYPE=router' in args
