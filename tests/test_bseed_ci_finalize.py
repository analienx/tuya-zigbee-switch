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
