"""Unit tests for the migration-safe image-type collision policy."""

from helper_scripts.check_image_types import (
    all_collisions,
    changed_image_type_boards,
    collisions_for_changed_boards,
    image_type_owners,
)


def _db(**entries):
    return entries


def test_global_collision_report_names_all_owners() -> None:
    db = _db(
        BOARD_A={"firmware_image_type": 100},
        BOARD_B={"firmware_image_type": 100},
        BOARD_C={"firmware_image_type": 101},
    )
    assert image_type_owners(db)[100] == ["BOARD_A", "BOARD_B"]
    assert all_collisions(db) == {100: ["BOARD_A", "BOARD_B"]}


def test_untouched_historical_collision_does_not_block_changed_mode() -> None:
    base = _db(
        LEGACY_A={"firmware_image_type": 45577, "config_str": "old-a"},
        LEGACY_B={"firmware_image_type": 45577, "config_str": "old-b"},
        SAFE={"firmware_image_type": 50000},
    )
    current = _db(
        LEGACY_A={"firmware_image_type": 45577, "config_str": "new-a"},
        LEGACY_B={"firmware_image_type": 45577, "config_str": "old-b"},
        SAFE={"firmware_image_type": 50000},
    )
    # Non-image fields may change on a deployed legacy board without forcing
    # an OTA identity migration merely to satisfy CI.
    assert changed_image_type_boards(base, current) == set()
    assert collisions_for_changed_boards(base, current) == {}


def test_new_board_cannot_reuse_existing_image_type() -> None:
    base = _db(BOARD_A={"firmware_image_type": 100})
    current = _db(
        BOARD_A={"firmware_image_type": 100},
        BOARD_B={"firmware_image_type": 100},
    )
    assert changed_image_type_boards(base, current) == {"BOARD_B"}
    assert collisions_for_changed_boards(base, current) == {
        100: ["BOARD_A", "BOARD_B"]
    }


def test_changed_board_cannot_move_onto_another_boards_id() -> None:
    base = _db(
        BOARD_A={"firmware_image_type": 100},
        BOARD_B={"firmware_image_type": 101},
    )
    current = _db(
        BOARD_A={"firmware_image_type": 101},
        BOARD_B={"firmware_image_type": 101},
    )
    assert changed_image_type_boards(base, current) == {"BOARD_A"}
    assert collisions_for_changed_boards(base, current) == {
        101: ["BOARD_A", "BOARD_B"]
    }


def test_unique_new_image_type_passes_changed_mode() -> None:
    base = _db(BOARD_A={"firmware_image_type": 100})
    current = _db(
        BOARD_A={"firmware_image_type": 100},
        BOARD_B={"firmware_image_type": 102},
    )
    assert collisions_for_changed_boards(base, current) == {}


def test_bseed_deployed_image_type_is_not_renumbered_by_this_policy() -> None:
    # The live TS0726 V7 OTA identity is 45577. This test makes the preservation
    # rule explicit: an unchanged deployed ID is not classified as a change.
    base = _db(SWITCH_BSEED_TS0726_3GANG={"firmware_image_type": 45577})
    current = _db(SWITCH_BSEED_TS0726_3GANG={"firmware_image_type": 45577})
    assert changed_image_type_boards(base, current) == set()
