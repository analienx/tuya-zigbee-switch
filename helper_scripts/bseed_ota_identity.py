"""BSEED OTA identity gate: versions move forward exactly once, bytes match claims.

One (image_type, file_version) maps to exactly one byte-identity, except
stock from_tuya max-version wrappers flagged shared_identity. Every build
intended for OTA serving must pass `gate` before publishing; new releases
take their FILE_VERSION from `suggest-next`/`emit-make-vars`, never by hand.

Registry: zigbee2mqtt/ota/bseed_identity.json (checked in, reviewed).
Offline only; never touches live devices.
"""
import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "zigbee2mqtt" / "ota" / "bseed_identity.json"

OTA_MAGIC = 0x0BEEF11E
VERSION_STR_RE = re.compile(rb"1\.\d+\.\d+-bseed[a-z0-9_.-]{0,24}")


class IdentityError(ValueError):
    pass


def parse_ota_header(blob):
    if len(blob) < 56:
        raise IdentityError("Truncated OTA file (%d bytes)" % len(blob))
    magic, _hdr_ver, hdr_len, _fc = struct.unpack_from("<IHHH", blob, 0)
    if magic != OTA_MAGIC:
        raise IdentityError("Bad OTA magic %#x" % magic)
    manufacturer, image_type, file_version = struct.unpack_from("<HHI", blob, 10)
    (total_size,) = struct.unpack_from("<I", blob, 52)
    if total_size != len(blob):
        raise IdentityError("OTA size field %d != file size %d"
                            % (total_size, len(blob)))
    return {"manufacturer_code": manufacturer, "image_type": image_type,
            "file_version": file_version, "header_length": hdr_len,
            "file_size": len(blob)}


def load_registry(path):
    data = json.loads(Path(path).read_text(encoding="utf8"))
    lines = {}
    for line in data.get("lines", []):
        lines[int(line["image_type"])] = line
    return lines


def embedded_version_strings(blob):
    return sorted(set(m.decode() for m in VERSION_STR_RE.findall(blob)))


def require_embedded_string(blob, claimed):
    found = embedded_version_strings(blob)
    if claimed not in found:
        raise IdentityError("claimed version_str %r not embedded in "
                            "image (found %s)" % (claimed, found))
    strays = [s for s in found if s != claimed]
    if strays:
        raise IdentityError("stray version strings in image: %s "
                            "(claimed %r)" % (strays, claimed))


def gate_image(blob, registry, expect_version_str=None, expect_image_type=None,
               expect_file_version=None, allow_downgrade=False):
    header = parse_ota_header(blob)
    image_type, file_version = header["image_type"], header["file_version"]
    if expect_image_type is not None and image_type != expect_image_type:
        raise IdentityError("image_type %d != expected %d"
                            % (image_type, expect_image_type))
    if expect_file_version is not None and file_version != expect_file_version:
        raise IdentityError("file_version %#x != expected %#x"
                            % (file_version, expect_file_version))
    if image_type not in registry:
        raise IdentityError("image_type %d has no registry line; register the "
                            "board/role line before building" % image_type)
    line = registry[image_type]
    digest = hashlib.sha512(blob).hexdigest()
    report = dict(header)
    report["sha512"] = digest
    entries = [e for e in line["versions"]
               if int(e["file_version"]) == file_version]
    if line.get("shared_identity"):
        known = [str(e.get("sha512")) for e in entries if e.get("sha512")]
        if digest not in known:
            raise IdentityError(
                "shared identity (type %d ver %#x) with unlisted bytes; "
                "register the wrapper hash first" % (image_type, file_version))
        report["verdict"] = "ok-shared-wrapper"
        return report
    for entry in entries:
        known = entry.get("sha512")
        if known and known != digest:
            raise IdentityError(
                "RELABEL REFUSED: (type %d ver %#x) already maps to other "
                "bytes; bump the version, never rebuild under it"
                % (image_type, file_version))
    if entries and all(e.get("sha512") for e in entries):
        claimed_here = expect_version_str or entries[0].get("version_str")
        if claimed_here:
            require_embedded_string(blob, claimed_here)
        report["verdict"] = "ok-identical-rebuild"
        return report
    known_max = max(int(e["file_version"]) for e in line["versions"])
    board_max = board_maximum(registry, line.get("board_key"))
    if file_version < known_max and not allow_downgrade:
        raise IdentityError("file_version %#x below line maximum %#x; "
                            "refusing downgrade bytes" % (file_version,
                                                          known_max))
    if file_version < board_max and not allow_downgrade:
        raise IdentityError("file_version %#x below board maximum %#x; "
                            "versions rise across both roles of a board"
                            % (file_version, board_max))
    if entries and not entries[0].get("sha512"):
        report["action_required"] = ("first bytes for reserved identity; "
                                     "record sha512 %s" % digest)
    claimed = expect_version_str
    if claimed is None and entries and entries[0].get("version_str"):
        claimed = entries[0]["version_str"]
    if claimed:
        require_embedded_string(blob, claimed)
        for entry in line["versions"]:
            if (entry.get("version_str") == claimed
                    and int(entry["file_version"]) != file_version):
                raise IdentityError("version_str %r already used by version "
                                    "%#x in this line" % (claimed, int(
                                        entry["file_version"])))
    report["verdict"] = "ok-new-version" if file_version >= known_max else \
        "ok-downgrade-allowed"
    return report


def board_maximum(registry, board_key):
    versions = [int(e["file_version"]) for line in registry.values()
                if line.get("board_key") == board_key
                and not line.get("shared_identity")
                for e in line["versions"]]
    if not versions:
        raise IdentityError("board %r has no versioned lines" % board_key)
    return max(versions)


def suggest_next(registry, image_type):
    if image_type not in registry:
        raise IdentityError("image_type %d has no registry line" % image_type)
    line = registry[image_type]
    if line.get("shared_identity"):
        raise IdentityError("shared-identity line takes no versions")
    nxt = board_maximum(registry, line.get("board_key")) + 1
    return {"image_type": image_type, "next_file_version": nxt,
            "next_file_version_hex": hex(nxt)}


def emit_make_vars(registry, image_type, version_str):
    nxt = suggest_next(registry, image_type)
    for entry in registry[image_type]["versions"]:
        if entry.get("version_str") == version_str:
            raise IdentityError("version_str %r already used in line %d"
                                % (version_str, image_type))
    return {"FILE_VERSION": nxt["next_file_version_hex"],
            "VERSION_STR": version_str}


def check_index(registry, ota_dir):
    failures = []
    checked = 0
    for path in sorted(Path(ota_dir).glob("*.ota")):
        checked += 1
        try:
            report = gate_image(path.read_bytes(), registry)
            print("%s: %s" % (path.name, report["verdict"]))
        except IdentityError as exc:
            failures.append(path.name)
            print("%s: REFUSED %s" % (path.name, exc))
    if not checked:
        raise IdentityError("no .ota files in %s" % ota_dir)
    if failures:
        raise IdentityError("%d file(s) refused: %s"
                            % (len(failures), ", ".join(failures)))
    return checked


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    sub = parser.add_subparsers(dest="mode", required=True)
    index = sub.add_parser("check-index", help="gate every checked-in image")
    index.add_argument("--ota-dir",
                       default=str(ROOT / "zigbee2mqtt" / "ota" / "bseed"))
    gate = sub.add_parser("gate", help="vet a candidate image")
    gate.add_argument("--image", required=True)
    gate.add_argument("--expect-version-str")
    gate.add_argument("--expect-image-type", type=lambda x: int(x, 0))
    gate.add_argument("--expect-file-version", type=lambda x: int(x, 0))
    gate.add_argument("--allow-downgrade", action="store_true")
    nxt = sub.add_parser("suggest-next", help="next monotonic FILEVER")
    nxt.add_argument("--image-type", type=lambda x: int(x, 0), required=True)
    emit = sub.add_parser("emit-make-vars", help="version vars for the build")
    emit.add_argument("--image-type", type=lambda x: int(x, 0), required=True)
    emit.add_argument("--version-str", required=True)
    args = parser.parse_args(argv)
    registry = load_registry(args.registry)
    try:
        if args.mode == "check-index":
            print("checked %d" % check_index(registry, args.ota_dir))
        elif args.mode == "gate":
            blob = Path(args.image).read_bytes()
            report = gate_image(blob, registry, args.expect_version_str,
                                args.expect_image_type, args.expect_file_version,
                                args.allow_downgrade)
            print(json.dumps(report, indent=2))
        elif args.mode == "suggest-next":
            print(json.dumps(suggest_next(registry, args.image_type), indent=2))
        else:
            for key, value in emit_make_vars(registry, args.image_type,
                                             args.version_str).items():
                print("%s=%s" % (key, value))
    except IdentityError as exc:
        print("OTA-IDENTITY-REFUSED: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
