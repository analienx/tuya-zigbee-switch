"""The experimental Client CI must not silently accept changed Router firmware."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pm_and_ts0726_router_binary_gate_is_pinned() -> None:
    workflow = (ROOT / ".github/workflows/bseed-mains-client-experimental.yml").read_text()
    assert 'cmp -s "$BASE_DIR/build/baseline-pm/forward.bin" build/client-control-pm/forward.bin' in workflow
    assert 'cmp -s "$BASE_DIR/build/baseline-ts0726/forward.bin" build/client-control-ts0726/forward.bin' in workflow
    assert 'cmp "$BASE_DIR/build/baseline-pm/forward.ota" build/client-control-pm/forward.ota' in workflow
    assert 'cmp "$BASE_DIR/build/baseline-ts0726/forward.ota" build/client-control-ts0726/forward.ota' in workflow
    assert "e4c6fa0bed8d3397478d8b03ed49c5d9efb11679848105af800f67285b21f281" in workflow
    assert "d0bf912352bf0bfc8c984ab4438ca5d1fd3fc665b1dc3dd1b3e254a5d8e155ae" in workflow
    assert "sha256sum -c -" in workflow
