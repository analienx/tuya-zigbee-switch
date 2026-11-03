#!/usr/bin/env python3
"""Read-only documentation integrity checks for GitHub-hosted CI.

Only the curated product documentation is checked; historical vendor/firmware
references remain available without forcing a full repository-wide migration.
No network access, firmware build, Zigbee operation or file mutation.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DOCS = (
    "README.md",
    "docs/README.md",
    "docs/bseed_socket_release_status.md",
    "docs/changelog_fw.md",
    "docs/contribute/release.md",
    "docs/releases/README.md",
    "docs/releases/2026-11-02-link-policy-candidates.md",
)
CURRENT_START = "<!-- current-candidate-matrix:start -->"
CURRENT_END = "<!-- current-candidate-matrix:end -->"
LINK = re.compile(r"!?\[[^\]\n]+\]\(([^)\s]+)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
# Intentionally precise: accepted public index has 3 Router images + 3 stock wrappers.
PUBLISHED = {
    43556: (302329863, "b28wrpvx"),
    43555: (285356033, "o1jzcxou"),
    45577: (285356042, "iedhxgyi"),
}


class DocsError(ValueError):
    pass


def source_candidate(repo: Path, file: str, role: str) -> dict:
    """Read literal source definitions without importing (or executing) firmware code."""
    tree = ast.parse((repo / file).read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id != role:
            continue
        value = node.value
        if isinstance(value, ast.Dict):
            result = ast.literal_eval(value)
        elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "dict":
            if value.args or any(x.arg is None for x in value.keywords):
                raise DocsError(f"{file}: dynamic role constructor not supported")
            result = {x.arg: ast.literal_eval(x.value) for x in value.keywords}
        else:
            raise DocsError(f"{file}: {role} must have a literal definition")
        if any(k not in result for k in ("build", "version", "type")):
            raise DocsError(f"{file}: incomplete {role} candidate definition")
        return result
    raise DocsError(f"{file}: missing {role} candidate")


def check_current_matrix(repo: Path) -> list[str]:
    errors: list[str] = []
    page = (repo / "docs/bseed_socket_release_status.md").read_text(encoding="utf-8")
    if page.count(CURRENT_START) != 1 or page.count(CURRENT_END) != 1:
        return ["release status: expected one bounded current candidate table"]
    between = page.split(CURRENT_START, 1)[1].split(CURRENT_END, 1)[0]
    rows = [line for line in between.splitlines() if line.startswith("| ") and " | " in line]
    # Drop header and delimiter, retaining exactly four role rows.
    rows = [row for row in rows if re.search(r"bseed(?:r|cli|c)\d+", row)]
    if len(rows) != 4:
        errors.append(f"current candidate table: expected 4 rows; got {len(rows)}")
    identities = (
        ("pm", "ROUTER", "PM TS011F-BS-PM", "Router", "helper_scripts/bseed_pm_release.py"),
        ("pm", "CLIENT", "PM TS011F-BS-PM", "mains Client", "helper_scripts/bseed_pm_release.py"),
        ("nonpm", "ROUTER", "non-PM TS011F-BS", "Router", "helper_scripts/bseed_nonpm_release.py"),
        ("nonpm", "CLIENT", "non-PM TS011F-BS", "mains Client", "helper_scripts/bseed_nonpm_release.py"),
    )
    overview = (repo / "README.md").read_text(encoding="utf-8")
    for board, role, label, role_name, filename in identities:
        current = source_candidate(repo, filename, role)
        matching = [row for row in rows if row.split("|")[1].strip() == label
                    and row.split("|")[2].strip() == role_name]
        if len(matching) != 1:
            errors.append(f"missing/duplicate candidate row: {board} {role}")
            continue
        row = matching[0]
        for expected in (current["build"], f"0x{current['version']:08x}", str(current["type"])):
            if expected.lower() not in row.lower():
                errors.append(f"{board} {role}: status page mismatch for {expected}")
        if "not public" not in row.lower() or "pending" not in row.lower():
            errors.append(f"{board} {role}: missing unpublished/hardware-pending label")
        if current["build"] not in overview:
            errors.append(f"{board} {role}: candidate missing from README")
    return errors


def check_public_ota_index(repo: Path) -> list[str]:
    entries = json.loads((repo / "zigbee2mqtt/ota/index_bseed.json").read_text(encoding="utf-8"))
    if not isinstance(entries, list) or len(entries) != 6:
        return ["public BSEED index: expected three accepted Router images + three stock wrappers"]
    custom = [e for e in entries if e.get("imageType") in PUBLISHED]
    wrappers = [e for e in entries if e.get("imageType") == 54179]
    errors: list[str] = []
    if len(custom) != 3 or len(wrappers) != 3:
        errors.append("public index: wrong custom Router / stock-wrapper count")
    for image_type, (version, manufacturer) in PUBLISHED.items():
        matches = [e for e in custom if e.get("imageType") == image_type]
        if len(matches) != 1:
            errors.append(f"public index: missing/duplicate Router image type {image_type}")
            continue
        e = matches[0]
        if e.get("fileVersion") != version or e.get("manufacturerName") != [manufacturer]:
            errors.append(f"public index: accepted image changed for type {image_type}; review documentation and promotion")
    if any(e.get("fileVersion") != 0xFFFFFFFF for e in wrappers):
        errors.append("public index: unexpected stock conversion version")
    if len({str(e.get("manufacturerName")) for e in wrappers}) != 3:
        errors.append("public index: duplicate stock-wrapper manufacturer")
    if any(e.get("imageType") in (65024, 65026) for e in entries):
        errors.append("public index: Client image inserted without explicit publication review")
    return errors


def github_slug(title: str) -> str:
    title = re.sub(r"<[^>]+>", "", title)
    title = re.sub(r"[`*_~]", "", title)
    title = re.sub(r"[^\w\- ]", "", title, flags=re.UNICODE)
    return re.sub(r"\s+", "-", title.strip().lower())


def anchored_headings(path: Path) -> set[str]:
    found: set[str] = set()
    counts: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = HEADING.match(line)
        if not match:
            continue
        slug = github_slug(match.group(1))
        suffix = counts.get(slug, 0)
        counts[slug] = suffix + 1
        found.add(slug if suffix == 0 else f"{slug}-{suffix}")
    return found


def check_links(repo: Path, files: tuple[str, ...] = DOCS) -> list[str]:
    errors: list[str] = []
    root = repo.resolve()
    for name in files:
        file = repo / name
        if not file.is_file():
            errors.append(f"{name}: missing Markdown source")
            continue
        in_fence = False
        for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith(("```", "~~~")):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            # Inline code is not a Markdown link.
            line = re.sub(r"`[^`]*`", "", line)
            for match in LINK.finditer(line):
                href = match.group(1).strip("<>")
                if href.startswith(("https://", "http://", "mailto:", "data:")):
                    continue
                parts = urlsplit(href)
                if parts.scheme or parts.netloc:
                    continue
                path = unquote(parts.path)
                if path:
                    target = (root / path.lstrip("/")) if path.startswith("/") else file.parent / path
                else:
                    target = file
                target = target.resolve()
                if not target.is_relative_to(root) or not target.is_file():
                    errors.append(f"{name}:{number}: broken relative link {href}")
                    continue
                if parts.fragment and target.suffix.lower() == ".md":
                    anchor = unquote(parts.fragment).lower()
                    if anchor not in anchored_headings(target):
                        errors.append(f"{name}:{number}: unresolved heading #{anchor} in {target.relative_to(root)}")
    return errors


def check(repo: Path = ROOT) -> list[str]:
    errors = check_links(repo)
    errors += check_current_matrix(repo)
    errors += check_public_ota_index(repo)
    return errors


def main() -> int:
    errors = check()
    if errors:
        print("Documentation integrity FAILED")
        for error in errors:
            print(" -", error)
        return 1
    print(f"Documentation integrity PASS: {len(DOCS)} curated pages; current 4-role candidate matrix; 6 published index entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
