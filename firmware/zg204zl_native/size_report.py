"""Hosted-only linker-map accounting for issue #164's flash budget."""
import argparse
import json
import re
from pathlib import Path


def report(image, map_path):
    totals = {}
    # GNU ld prints a section/address/size/object line, sometimes wrapped.
    pending = ""
    text = map_path.read_text(errors="replace")
    # Telink's GNU ld fork inserts this banner between a section and its
    # object filename. Exclude discarded sections before collecting bytes.
    _, marker, text = text.partition("Linker script and memory map")
    if not marker:
        raise ValueError("Missing linker-map start marker")
    text = text.replace(" ok now generated...\n", " ")
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^\.(?:text|rodata|data|ram_code|vectors)(?:\.|\s|$)", stripped):
            pending = stripped
        elif pending and stripped.startswith("0x"):
            pending += " " + stripped
        else:
            pending = ""
        match = re.search(r"0x[0-9a-fA-F]+\s+0x([0-9a-fA-F]+)\s+(.+?\.o(?:\))?)$", pending)
        if match:
            obj = match.group(2)
            totals[obj] = totals.get(obj, 0) + int(match.group(1), 16)
            pending = ""
    length = image.stat().st_size
    return {"native_bytes": length, "free_bytes": 0x20000 - length,
            "objects": dict(sorted(totals.items(), key=lambda item: -item[1]))}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--image", type=Path, required=True)
    p.add_argument("--map", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    result = report(a.image, a.map)
    if not result["objects"]:
        raise SystemExit("No loaded input sections found in linker map")
    a.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "objects"}))
