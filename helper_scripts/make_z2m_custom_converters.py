import argparse
import html
import re
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

env = Environment(
    loader=FileSystemLoader("helper_scripts/templates"),
    autoescape=select_autoescape(),
    trim_blocks=True,
    lstrip_blocks=True,
)

def _plain_text(value):
    """Normalize human_name for a one-line JS device description."""
    if value is None:
        return ""
    value = html.unescape(str(value))
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(value.split())


def collect_devices(db):
    devices = []

    for db_key, device in db.items():
        # Skip if build == no. Defaults to yes
        if not device.get("build", True):
            continue

        config = device["config_str"]
        zb_manufacturer, zb_model, *peripherals = config.rstrip(";").split(";")

        relay_cnt = 0
        switch_cnt = 0
        cover_switch_cnt = 0
        cover_cnt = 0
        indicators_cnt = 0
        has_dedicated_net_led = False
        has_battery_cluster = False
        for peripheral in peripherals:
            if peripheral == "SLP" or peripheral == "M":
                continue
            if peripheral[0] == "R":
                relay_cnt += 1
            if peripheral[0] == "S":
                switch_cnt += 1
            if peripheral[0] == "X":
                cover_switch_cnt += 1
            if peripheral[0] == "C":
                cover_cnt += 1
            if peripheral[0] == "I":
                indicators_cnt += 1
            if peripheral[0] == "L":
                has_dedicated_net_led = True
            if peripheral[:2] == "BT":
                has_battery_cluster = True

        if switch_cnt == 1:
            switch_names = ["switch"]
        elif switch_cnt == 2:
            switch_names = ["switch_left", "switch_right"]
        elif switch_cnt == 3:
            switch_names = ["switch_left", "switch_middle", "switch_right"]
        else:
            switch_names = [f"switch_{index}" for index in range(switch_cnt)]

        if relay_cnt == 1:
            relay_names = ["relay"]
        elif relay_cnt == 2:
            relay_names = ["relay_left", "relay_right"]
        elif relay_cnt == 3:
            relay_names = ["relay_left", "relay_middle", "relay_right"]
        else:
            relay_names = [f"relay_{index}" for index in range(relay_cnt)]

        if cover_switch_cnt == 1:
            cover_switch_names = ["cover_switch"]
        elif cover_switch_cnt == 2:
            cover_switch_names = ["cover_switch_left", "cover_switch_right"]
        elif cover_switch_cnt == 3:
            cover_switch_names = [
                "cover_switch_left",
                "cover_switch_middle",
                "cover_switch_right",
            ]
        else:
            cover_switch_names = [
                f"cover_switch_{i + 1}" for i in range(cover_switch_cnt)
            ]

        if cover_cnt == 1:
            cover_names = ["cover"]
        elif cover_cnt == 2:
            cover_names = ["cover_left", "cover_right"]
        elif cover_cnt == 3:
            cover_names = ["cover_left", "cover_middle", "cover_right"]
        else:
            cover_names = [f"cover_{index}" for index in range(cover_cnt)]

        devices.append(
            {
                "db_key": db_key,
                "human_name": _plain_text(device.get("human_name") or db_key),
                "zb_manufacturer": zb_manufacturer,
                "zb_models": [zb_model] + (device.get("old_zb_models") or []),
                # Deployment-only safety overlay. This branch is used for the
                # BSEED canary; upstream/generic converter branches must not
                # carry this special case.
                "bseed_canary_no_configure": db_key == "SWITCH_BSEED_TS0726_3GANG",
                # Firmware capability is explicit. Do not advertise physical
                # relay policy to old firmware merely because a board has relays.
                "has_physical_relay_policy": bool(
                    device.get("physical_relay_policy", False)
                ),
                # Compatibility overlay only: retain the historical Z2M action
                # event stream without re-creating configure-time bindings.
                "preserve_legacy_action": bool(
                    device.get("preserve_legacy_action", False)
                ),
                "model": device.get("override_z2m_device")
                or device["stock_converter_model"],
                "switchNames": switch_names,
                "relayNames": relay_names,
                "relayIndicatorNames": relay_names[:indicators_cnt],
                "coverSwitchNames": cover_switch_names,
                "coverNames": cover_names,
                "has_dedicated_net_led": has_dedicated_net_led,
                "has_battery_cluster": has_battery_cluster,
            }
        )

    return devices


def _contract_signature(device):
    """Functional generated contract, excluding presentation-only metadata."""
    return repr(
        sorted(
            (k, v)
            for k, v in device.items()
            if k not in {"db_key", "human_name"}
        )
    )


def mark_ambiguous_models(devices):
    """Classify model collisions per re-review 5492467354 (gate E).

    - RESOLVABLE: every claimant of a model has a distinct manufacturer, so
      each definition is pinned with a `fingerprint` on
      (manufacturerName, modelID). Matching becomes order-independent.
    - Deterministic merge: definitions with an IDENTICAL
      (manufacturer, models, contract) collapse into one — they were
      byte-identical in the output anyway.
    - UNRESOLVED LEGACY: the same (manufacturer, model) tuple is claimed by
      definitions with different contracts. Fingerprints cannot separate
      them; the legacy bare `zigbeeModel` matcher is preserved for the whole
      model group and a deterministic warning lists all DB keys. We do NOT
      pretend the group became deterministic and we never silently merge
      different contracts.
    """
    # 1. Deterministic merge of byte-identical definitions.
    seen = {}
    deduped = []
    merged = []
    for device in devices:
        key = (
            device["zb_manufacturer"],
            tuple(device["zb_models"]),
            _contract_signature(device),
        )
        if key in seen:
            # Presentation metadata must not prevent deterministic merging of
            # byte/semantic-identical functional contracts. Pick a stable
            # description independent of DB traversal order.
            kept = seen[key]
            kept["human_name"] = min(kept["human_name"], device["human_name"])
            merged.append((device["db_key"], kept["db_key"]))
            continue
        seen[key] = device
        deduped.append(device)

    # 2. Group claims per model string.
    claims = {}
    for device in deduped:
        for model in device["zb_models"]:
            claims.setdefault(model, []).append(device)

    # 3. Classify each MODEL independently. A single definition may need
    # both match surfaces: exact fingerprints for resolvable collisions and
    # zigbeeModel fallback entries for unique / unresolved legacy aliases.
    for device in deduped:
        device["unique_models"] = []
        device["ambiguous_models"] = []  # RESOLVABLE -> fingerprint
        device["unresolved_models"] = []  # legacy model-only fallback
        for model in device["zb_models"]:
            group = claims[model]
            if len(group) == 1:
                device["unique_models"].append(model)
                continue
            manufacturers = {x["zb_manufacturer"] for x in group}
            if len(manufacturers) == len(group):
                device["ambiguous_models"].append(model)
            else:
                device["unresolved_models"].append(model)

        device["fingerprints"] = [
            {
                "manufacturerName": device["zb_manufacturer"],
                "modelID": model,
            }
            for model in device["ambiguous_models"]
        ]
        # ZHC supports fingerprint + zigbeeModel on the same definition.
        # Preserve unique aliases and unresolved legacy collisions here so a
        # collision in one old alias never drops the definition's current
        # unique model (e.g. TS0002-GIR + old TS0002-custom).
        device["legacy_models"] = (
            device["unique_models"] + device["unresolved_models"]
        )
        device["has_collision"] = bool(device["fingerprints"])
        device["has_unresolved"] = bool(device["unresolved_models"])

    return deduped, merged


def generate(db, z2m_v1=False, only_db_keys=None):
    # Collision classification MUST run against the complete DB. Filtering
    # first would make a target-only overlay lose fingerprints for models that
    # collide elsewhere in the DB (e.g. TS0726-3-BS).
    devices, merged = mark_ambiguous_models(collect_devices(db))

    if only_db_keys:
        wanted = set(only_db_keys)
        found = {device["db_key"] for device in devices if device["db_key"] in wanted}
        missing = wanted - found
        if missing:
            raise ValueError(
                "Requested DB key(s) not generated: %s" % ", ".join(sorted(missing))
            )
        devices = [device for device in devices if device["db_key"] in wanted]

    template = env.get_template("switch_custom.js.jinja")
    rendered = template.render(devices=devices, z2m_v1=z2m_v1)

    for device in devices:
        if device["has_collision"]:
            print(
                "Ambiguous model(s) %s disambiguated via fingerprint for %s"
                % (
                    ",".join(device["ambiguous_models"]),
                    device["zb_manufacturer"],
                ),
                file=sys.stderr,
            )
        if device["has_unresolved"]:
            print(
                "UNRESOLVED legacy model collision for %s: model(s) %s "
                "claimed by DB keys with different contracts: %s"
                % (
                    device["zb_manufacturer"],
                    ",".join(device["unresolved_models"]),
                    ",".join(
                        d["db_key"]
                        for d in devices
                        if set(d["unresolved_models"])
                        & set(device["unresolved_models"])
                    ),
                ),
                file=sys.stderr,
            )
    for dup, kept in merged:
        print(
            "Merged byte-identical definition: %s merged into %s"
            % (dup, kept),
            file=sys.stderr,
        )

    return rendered


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create Zigbee2mqtt converter for custom devices",
        epilog="Generates a js file that adds support of re-flashed devices to z2m",
    )
    parser.add_argument(
        "db_file", metavar="INPUT", type=str, help="File with device db"
    )
    parser.add_argument(
        "--z2m-v1", action=argparse.BooleanOptionalAction, help="Use old z2m"
    )
    parser.add_argument(
        "--only-db-key",
        action="append",
        dest="only_db_keys",
        help=(
            "Generate only the selected DB key. May be repeated. Collision "
            "classification still uses the full DB so fingerprints remain correct."
        ),
    )

    args = parser.parse_args()

    db_str = Path(args.db_file).read_text()
    db = yaml.safe_load(db_str)

    print(
        generate(
            db,
            z2m_v1=args.z2m_v1,
            only_db_keys=args.only_db_keys,
        )
    )

    exit(0)
