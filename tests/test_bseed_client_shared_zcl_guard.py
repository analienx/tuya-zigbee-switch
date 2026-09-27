"""The experimental Client CI must verify history without rebuilding it from current source."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/bseed-mains-client-experimental.yml"


def test_historical_routers_are_verified_not_rebuilt_from_current_source() -> None:
    workflow = (ROOT / WORKFLOW).read_text()
    assert "make bseed/identity-gate" in workflow
    assert "git worktree add" not in workflow
    assert "merge-base" not in workflow
    assert "baseline-pm" not in workflow
    assert "baseline-ts0726" not in workflow
    assert "client-control-pm" not in workflow
    assert "client-control-ts0726" not in workflow
    assert "build_bseed_ts011f_pm_v8.sh" not in workflow
    assert "build_bseed_ts0726_v8.sh" not in workflow
    assert "e4c6fa0bed8d3397478d8b03ed49c5d9efb11679848105af800f67285b21f281" not in workflow
    assert "d0bf912352bf0bfc8c984ab4438ca5d1fd3fc665b1dc3dd1b3e254a5d8e155ae" not in workflow


def test_rollback_wraps_sealed_history_with_max_version() -> None:
    workflow = (ROOT / WORKFLOW).read_text()
    assert "reseal-ota" in workflow
    assert "zigbee2mqtt/ota/bseed/ts011f-pm-v12053007.ota" in workflow
    assert "--source-image-type 43556 --source-file-version 0x12053007" in workflow
    assert "zigbee2mqtt/ota/bseed/ts0726-v1102300a.ota" in workflow
    assert "--source-image-type 45577 --source-file-version 0x1102300A" in workflow
    assert "--image-type 65024 --file-version 0xFFFFFFFF" in workflow
    assert "--image-type 65025 --file-version 0xFFFFFFFF" in workflow
    assert "rollback[56:] == source[56:]" in workflow
    assert "rollback-to-router.ota" in workflow


def test_candidate_clients_keep_identity_reproducibility_and_role_gates() -> None:
    workflow = (ROOT / WORKFLOW).read_text()
    assert "build_bseed_mains_client.sh pm" in workflow
    assert "build_bseed_mains_client.sh ts0726" in workflow
    assert "1.2.5-bseedcli11" in workflow
    assert "1.1.8-bseedcli2" in workflow
    assert "Rebuild both clients and require byte-identical output" in workflow
    assert "tests/test_bseed_mains_client.py" in workflow
    assert "ref: ${{ github.event.pull_request.head.sha || github.sha }}" in workflow
    assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_HEAD"' in workflow
