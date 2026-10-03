"""Artifact redirects retain signed URL auth without forwarding GitHub tokens."""
import io
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helper_scripts'))
from bseed_ci_finalize import api_request


def test_artifact_redirect_does_not_forward_api_authorization(monkeypatch):
    def open_request(request, *, timeout):
        assert request.get_header('Authorization') == 'Bearer synthetic-test-token'
        assert timeout == 60
        redirected = urllib.request.HTTPRedirectHandler().redirect_request(
            request, None, 302, 'Found', {},
            'https://artifact-storage.example.invalid/signed-artifact.zip')
        assert redirected.get_header('Authorization') is None
        return io.BytesIO(b'archive-fixture')

    monkeypatch.setattr(urllib.request, 'urlopen', open_request)
    assert api_request('https://api.github.com/repos/example/repo/actions/artifacts/1/zip',
                       'synthetic-test-token', binary=True) == b'archive-fixture'


def test_candidate_package_preserves_verified_native_payloads(tmp_path, monkeypatch):
    import hashlib
    import json
    import struct
    from bseed_ci_finalize import package_candidates
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setenv('RUNNER_ENVIRONMENT', 'github-hosted')
    rows = []
    for image_type in (43556, 65024, 43555, 65026):
        folder = tmp_path / str(image_type)
        folder.mkdir()
        raw = b'native-payload-' + str(image_type).encode()
        ota = bytearray(62)
        struct.pack_into('<I', ota, 14, 0x12053019)
        ota = bytes(ota) + raw
        source = folder / 'forward.ota'
        source.write_bytes(ota)
        source.with_suffix('.bin').write_bytes(raw)
        (folder / 'manifest.json').write_text('{}')
        rows.append({'role': 'Router' if image_type < 65000 else 'EndDevice',
                     'imageType': image_type, 'build': 'test-fixture',
                     'nativeIntegrityVerified': True, 'basicBuildIdVerified': True,
                     'hardwareAcceptance': False, 'artifact': str(source),
                     'sha256': hashlib.sha256(ota).hexdigest()})
    report = {'sourceCommit': 'a' * 40, 'artifacts': rows[:2],
              'nonpmArtifacts': rows[2:], 'publicActions': {}}
    metadata = package_candidates(report, tmp_path)
    assert len(metadata['entries']) == 4
    for entry, row in zip(metadata['entries'], rows):
        folder = tmp_path / 'flash-candidates' / entry['variant']
        ota = (folder / 'firmware.ota').read_bytes()
        raw = (folder / 'firmware.bin').read_bytes()
        assert ota == Path(row['artifact']).read_bytes()
        assert raw == ota[62:]
        assert entry['nativeSha256'] == hashlib.sha256(raw).hexdigest()
        assert entry['otaSha256'] == row['sha256']
        assert entry['fileVersion'] == 0x12053019
    assert json.loads((tmp_path/'flash-candidates/CANDIDATE_PACKAGE.json').read_text()) == metadata
