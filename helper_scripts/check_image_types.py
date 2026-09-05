#!/usr/bin/env python3
"""Detect firmware_image_type collisions without renumbering deployed devices.

The repository contains historical image-type collisions.  CI therefore uses
``--changed-base``: only boards whose firmware_image_type is new or changed by
the candidate diff are gated against the complete current database.  Existing,
untouched collisions remain visible through ``--all`` but do not make every PR
permanently red.  This matches the migration-safe part of Romasku #474 while
leaving renumbering/OTA migration to an explicit future decision.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml


Database = dict[str, dict[str, Any]]


def load_database_text(text: str) -> Database:
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ValueError("device database must be a YAML mapping")
    return loaded


def load_database(path: Path) -> Database:
    return load_database_text(path.read_text(encoding="utf-8"))


def image_type_for(entry: dict[str, Any]) -> int | None:
    value = entry.get("firmware_image_type")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"firmware_image_type must be an integer, got {value!r}")
    return value


def image_type_owners(db: Database) -> dict[int, list[str]]:
    owners: dict[int, list[str]] = defaultdict(list)
    for board, raw_entry in db.items():
        if not isinstance(raw_entry, dict):
            continue
        image_type = image_type_for(raw_entry)
        if image_type is not None:
            owners[image_type].append(board)
    return {image_type: sorted(names) for image_type, names in owners.items()}


def all_collisions(db: Database) -> dict[int, list[str]]:
    return {
        image_type: names
        for image_type, names in image_type_owners(db).items()
        if len(names) > 1
    }


def changed_image_type_boards(base: Database, current: Database) -> set[str]:
    changed: set[str] = set()
    for board, current_entry in current.items():
        if not isinstance(current_entry, dict):
            continue
        current_type = image_type_for(current_entry)
        base_entry = base.get(board)
        base_type = (
            image_type_for(base_entry)
            if isinstance(base_entry, dict)
            else None
        )
        if current_type != base_type:
            changed.add(board)
    return changed


def collisions_for_changed_boards(
    base: Database, current: Database
) -> dict[int, list[str]]:
    owners = image_type_owners(current)
    failures: dict[int, list[str]] = {}
    for board in sorted(changed_image_type_boards(base, current)):
        entry = current.get(board)
        if not isinstance(entry, dict):
            continue
        image_type = image_type_for(entry)
        if image_type is None:
            continue
        names = owners.get(image_type, [])
        if len(names) > 1:
            failures[image_type] = names
    return failures


def database_at_git_ref(ref: str, path: Path) -> Database:
    spec = f"{ref}:{path.as_posix()}"
    try:
        text = subprocess.check_output(
            ["git", "show", spec], text=True, stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if exc.stderr else "git show failed"
        raise RuntimeError(f"cannot read {spec}: {detail}") from exc
    return load_database_text(text)


def _print_collisions(collisions: dict[int, list[str]], *, prefix: str) -> None:
    for image_type in sorted(collisions):
        boards = ", ".join(collisions[image_type])
        print(f"{prefix}: firmware_image_type {image_type}: {boards}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", default="device_db.yaml", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--all",
        action="store_true",
        help="fail on every collision in the current database (diagnostic/debt mode)",
    )
    mode.add_argument(
        "--changed-base",
        metavar="GIT_REF",
        help=(
            "fail only when a board whose firmware_image_type changed vs GIT_REF "
            "collides with any board in the current database"
        ),
    )
    args = parser.parse_args(argv)

    try:
        current = load_database(args.database)
        if args.all:
            failures = all_collisions(current)
            prefix = "collision"
        else:
            base = database_at_git_ref(args.changed_base, args.database)
            failures = collisions_for_changed_boards(base, current)
            prefix = "new/changed collision"
    except (OSError, RuntimeError, ValueError, yaml.YAMLError) as exc:
        print(f"image-type check error: {exc}", file=sys.stderr)
        return 2

    if failures:
        _print_collisions(failures, prefix=prefix)
        return 1

    if args.all:
        print("firmware_image_type values are globally unique")
    else:
        print(f"no new firmware_image_type collision vs {args.changed_base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
