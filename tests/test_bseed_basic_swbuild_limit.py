"""Shared Zigbee Basic swBuildId contract: all firmware variants use this source."""
from pathlib import Path
import re
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src/zigbee/basic_cluster.c'


def _guard():
    source = SOURCE.read_text(encoding='utf8')
    assert source.count('DEF_STR(STRINGIFY_VALUE(VERSION_STR), swBuildId);') == 1
    return re.search(r'typedef char sw_build_id_must_fit_zcl_basic_16_bytes\[\s*.*?\];', source, re.S).group(0)


def test_build_guard_is_shared_across_roles_and_uses_same_version_string():
    guard = _guard()
    assert 'sizeof(STRINGIFY_VALUE(VERSION_STR)) <= 17' in guard
    assert 'sw_build_id_must_fit_zcl_basic_16_bytes' in guard
    assert len('1.2.5-bseedv8u4'.encode('ascii')) == 15
    assert len('1.2.5-bseedv8u5-rc1'.encode('ascii')) == 19


@pytest.mark.parametrize(('letters', 'should_compile'), [('a' * 15, True), ('b' * 16, True), ('c' * 17, False)])
def test_real_c_guard_compiles_only_zcl_compatible_strings(letters, should_compile):
    cc = shutil.which('cc') or shutil.which('gcc')
    if cc is None:
        pytest.skip('C compiler unavailable on this host; Linux toolchain must run this test')
    code = '#define STRINGIFY(x) #x\n#define STRINGIFY_VALUE(x) STRINGIFY(x)\n#define VERSION_STR ' + letters + '\n' + _guard() + '\nint main(void) { return 0; }\n'
    result = subprocess.run([cc, '-std=c99', '-x', 'c', '-fsyntax-only', '-'], input=code, capture_output=True, text=True, timeout=12)
    assert (result.returncode == 0) == should_compile, result.stderr
