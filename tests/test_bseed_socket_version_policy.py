"""Release ledger: BSEED socket image versions always rise, across roles."""
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'helper_scripts'))
from bseed_socket_version_policy import BOARDS, CANDIDATES, require_increasing


def profile(board, old, new, **changes):
    model, builds = BOARDS[board]
    data = dict(manufacturer=board, model=model, preflash_build=old,
                postflash_build=new, file_version=builds[new],
                postflash_role='EndDevice' if 'cli' in new else 'Router')
    data.update(changes)
    return data


def test_release_ledger_is_strictly_increasing_and_unique_within_each_board():
    for board, (model, builds) in BOARDS.items():
        versions = list(builds.values())
        assert versions == sorted(set(versions)), board
        assert all(0 < value < 0xFFFFFFFF for value in versions)
        assert model.startswith('TS011F')


def test_all_new_candidate_versions_exceed_previous_role_on_same_board():
    for board, (_, builds) in BOARDS.items():
        names = list(builds)
        for prior, successor in zip(names, names[1:]):
            assert require_increasing(profile(board, prior, successor)) is True
            with pytest.raises(ValueError, match='must increase'):
                require_increasing(profile(board, successor, prior))


def test_duplicate_wrong_numeric_identity_missing_baseline_and_wrong_role_fail():
    good = profile('o1jzcxou', '1.1.2-bseedcli6', '1.1.3-bseedv10')
    assert require_increasing(good)
    for update, fragment in (
        ({'file_version': 0x11023013}, 'registered firmware build'),
        ({'file_version': 0x11023010}, 'registered firmware build'),
        ({'preflash_build': 'unknown'}, 'Missing or unknown preflash'),
        ({'postflash_build': '1.1.2-bseedcli6'}, 'registered firmware build'),
        ({'postflash_role': 'EndDevice'}, 'intended Zigbee role'),
        ({'model': 'TS011F-BS-PM'}, 'board/model'),
    ):
        with pytest.raises(ValueError, match=fragment):
            require_increasing({**good, **update})
    with pytest.raises(ValueError, match='Unregistered'):
        require_increasing({**good, 'postflash_build': '1.1.3-bseedv11',
                            'file_version': 0x11023015})
    with pytest.raises(ValueError, match='reused'):
        require_increasing({**good, 'postflash_build': 'not-registered'})


def test_new_candidate_numbers_exceed_previous_both_role_family_releases():
    assert CANDIDATES >= {'1.2.5-bseedv8u5-rc3', '1.2.5-bseedcli8',
                          '1.1.2-bseedcli7', '1.1.3-bseedv10'}
    for board, predecessor, new in (
        ('b28wrpvx', '1.2.5-bseedcli7', '1.2.5-bseedv8u5-rc3'),
        ('b28wrpvx', '1.2.5-bseedv8u5-rc3', '1.2.5-bseedcli8'),
        ('o1jzcxou', '1.1.3-bseedv9', '1.1.2-bseedcli7'),
        ('o1jzcxou', '1.1.2-bseedcli7', '1.1.3-bseedv10'),
    ):
        assert require_increasing(profile(board, predecessor, new))
        with pytest.raises(ValueError, match='must increase'):
            require_increasing(profile(board, new, predecessor))
