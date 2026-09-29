"""The same release built on different days must not change its sealed bytes."""
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
from bseed_pm_release import RELEASE_DATE
from bseed_nonpm_release import RELEASE_DATE as NONPM_RELEASE_DATE


@pytest.mark.parametrize('release_date', [RELEASE_DATE, NONPM_RELEASE_DATE], ids=['pm', 'nonpm'])
def test_pinned_date_ignores_compiler_wall_clock_and_is_correct_zcl_string(tmp_path, release_date):
    code = '#include <string.h>\n#include "zigbee/build_date.h"\n' + r'''
int main(void) {
    const char expected[] = {8, EXPECTED_DATE_BYTES};
    zb_build_date_init(ZB_BUILD_DATE_YYYYMMDD);
    return memcmp(expected, ZB_BUILD_DATE_YYYYMMDD, sizeof(expected));
}
'''
    code = code.replace('EXPECTED_DATE_BYTES', ','.join(str(ord(c)) for c in release_date))
    outputs = []
    for i, day in enumerate(('Jan  1 2000', 'Dec 31 2040')):
        binary = tmp_path / f'date-{i}'
        subprocess.run(['cc', '-O2', '-std=c99', '-I', str(ROOT / 'src'),
                        '-DBSEED_BUILD_DATE=' + release_date, '-D__DATE__="' + day + '"',
                        '-x', 'c', '-', '-o', str(binary)], input=code, text=True,
                       capture_output=True, check=True)
        subprocess.run([str(binary)], check=True, timeout=5)
        outputs.append(binary.read_bytes())
    assert outputs[0] == outputs[1]


def test_pm_builds_pass_the_pinned_date_without_affecting_other_board_defaults():
    for name in ('build_bseed_mains_client.sh', 'build_bseed_ts011f_pm_v8.sh'):
        source = (ROOT / 'make_scripts' / name).read_text()
        assert "RELEASE_DATE_OVERRIDE=''" in source
        assert 'RELEASE_DATE_OVERRIDE="${release_vars[3]}"' in source
        assert 'BSEED_BUILD_DATE="$RELEASE_DATE_OVERRIDE"' in source
        assert '"buildDate": os.environ[' in source
