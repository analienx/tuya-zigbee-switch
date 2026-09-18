import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "zigbee2mqtt" / "extensions" / "ts0505b_ota_acceptance_probe.mjs"


def test_probe_never_sends_firmware_payload(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required")

    config = tmp_path / "probe.json"
    config.write_text(json.dumps({
        "target_ieee": "0x00124b0000000001",
        "manufacturerCode": "0x100B",
        "imageType": "0x020C",
        "cooldownMs": 5000,
        "cases": [{"name": "one", "fileVersion": "0x10003608", "imageSize": 65536, "waitMs": 5000}],
    }), encoding="utf-8")

    script = tmp_path / "probe-test.mjs"
    script.write_text(f"""
import assert from "node:assert/strict";
process.env.Z2M_TS0505B_OTA_PROBE_CONFIG = {str(config)!r};
const mod = await import({EXT.resolve().as_uri()!r} + "?test");
const calls = [];
const published = [];
class Endpoint {{
  constructor() {{ this.ID = 1; this.deviceIeeeAddress = "0x00124b0000000001"; }}
  async commandResponse(...args) {{ calls.push(args); return "ok"; }}
}}
const endpoint = new Endpoint();
const device = {{ieeeAddr: "0x00124b0000000001", endpoints: [endpoint]}};
const eventBus = {{
  handler: null,
  onDeviceMessage(_owner, handler) {{ this.handler = handler; }},
  removeListeners() {{ this.handler = null; }},
}};
const zigbee = {{zhController: {{*getDevicesIterator() {{ yield device; }}}}}};
const mqtt = {{publish: async (...args) => published.push(args)}};
const logger = {{warning: () => {{}}, error: (msg) => {{ throw new Error(msg); }}}};

const probe = new mod.default(zigbee, mqtt, null, null, eventBus, null, null, null, null, logger);
await probe.start();
assert.equal(calls[0][1], "imageNotify");

await eventBus.handler({{
  device, endpoint, cluster: "genOta", type: "commandQueryNextImageRequest",
  data: {{fieldControl: 0, manufacturerCode: 0x100b, imageType: 0x020c, fileVersion: 0x10003607}},
  meta: {{zclTransactionSequenceNumber: 7}},
}});
const query = calls.find((x) => x[1] === "queryNextImageResponse");
assert.ok(query);
assert.equal(query[2].status, 0);
assert.equal(query[2].imageSize, 65536);

const beforeSuppressed = calls.length;
await endpoint.commandResponse("genOta", "queryNextImageResponse", {{status: mod.NO_IMAGE_AVAILABLE}}, undefined, 7);
assert.equal(calls.length, beforeSuppressed);

await eventBus.handler({{
  device, endpoint, cluster: "genOta", type: "commandImageBlockRequest",
  data: {{fileOffset: 0, maximumDataSize: 50}},
  meta: {{zclTransactionSequenceNumber: 8}},
}});
const block = calls.find((x) => x[1] === "imageBlockResponse");
assert.ok(block);
assert.deepEqual(block[2], {{status: mod.OTA_ABORT}});
assert.equal(Object.hasOwn(block[2], "data"), false);

const statuses = published
  .filter((x) => x[0] === mod.STATUS_TOPIC)
  .map((x) => JSON.parse(x[1]));
assert.equal(statuses.every((x) => x.payload_bytes_sent === 0), true);
assert.equal(statuses.some((x) => x.result?.accepted_prebyte === true), true);
await probe.stop();
""", encoding="utf-8")

    completed = subprocess.run(
        [node, str(script)], cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert completed.returncode == 0, (
        f"probe harness failed\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
    )


def test_ts0505b_target_contains_no_household_identifiers():
    paths = [
        ROOT / "devices" / "ts0505b-mja6r5ix",
        ROOT / "zigbee2mqtt" / "extensions" / "ts0505b_ota_acceptance_probe.mjs",
        ROOT / "zigbee2mqtt" / "extensions" / "ts0505b_ota_probe.example.json",
    ]
    forbidden = (
        "hall" + "bulb",
        "target-" + "a",
        "target-" + "b",
        "target-" + "c",
        "zeph" + "yrus",
        "c:" + "\\" + "workspace",
    )
    ieee_prefix = "0x" + "a4" + "c138"
    for path in paths:
        files = path.rglob("*") if path.is_dir() else (path,)
        for file in files:
            if not file.is_file():
                continue
            text = file.read_text(encoding="utf-8").lower()
            for token in forbidden:
                assert token not in text, f"{token!r} leaked into {file}"
            assert ieee_prefix not in text, f"household IEEE prefix leaked into {file}"
