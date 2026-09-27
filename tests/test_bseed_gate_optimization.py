"""Safety validation remains active under Python optimization, including imports."""
import ast
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_no_bseed_gate_or_inline_build_validator_uses_assert():
    sources = [(str(p), p.read_text()) for p in (ROOT / 'helper_scripts').glob('bseed*.py')]
    for path in (ROOT / 'make_scripts').glob('build_bseed*.sh'):
        sources.extend((str(path), block) for block in re.findall(r"<<'PY'\n(.*?)\nPY\b", path.read_text(), re.S))
    for name, source in sources:
        assert not any(isinstance(node, ast.Assert) for node in ast.walk(ast.parse(source))), name


def test_optimized_link_gate_rejects_forged_evidence():
    code = '''
from bseed_nonpm_link_gate import verify_record
profile = dict(device='test', ieee='0x0000000000000001', sha256='a'*64, preflash_build='test')
bad = dict(schema=999, passed=False, samples=[])
try:
    verify_record(bad, profile)
except AssertionError as error:
    print(error)
else:
    raise RuntimeError('optimized gate accepted forged evidence')
'''
    result = subprocess.run([sys.executable, '-O', '-c', code],
                            cwd=ROOT / 'helper_scripts', text=True, capture_output=True, check=True)
    assert 'Link gate not passed' in result.stdout
