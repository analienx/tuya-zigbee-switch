#!/usr/bin/env python3
from pathlib import Path

p = Path("zigbee2mqtt/converters/bseed_ts0726_v5.js")
s = p.read_text(encoding="utf-8")

def rep(old, new, label):
    global s
    if old not in s:
        raise SystemExit(f"missing marker: {label}")
    s = s.replace(old, new, 1)

rep(
'''    onOff,
    text,
} = require("zigbee-herdsman-converters/lib/modernExtend");''',
'''    onOff,
} = require("zigbee-herdsman-converters/lib/modernExtend");''',
"remove text modernExtend",
)

rep(
'''const withExposeLabel = (extend, label) => {
    for (const expose of extend.exposes || []) {
        if (typeof expose.withLabel === "function") expose.withLabel(label);
    }
    return extend;
};
''',
'''const withExposeLabel = (extend, label) => {
    for (const expose of extend.exposes || []) {
        if (typeof expose.withLabel === "function") expose.withLabel(label);
    }
    return extend;
};

const CHANNEL_LABELS = {
    switch_left: "Left",
    switch_middle: "Middle",
    switch_right: "Right",
    relay_left: "Left",
    relay_middle: "Middle",
    relay_right: "Right",
    advanced: "Advanced",
};
const channelLabel = (endpointName) => CHANNEL_LABELS[endpointName] || endpointName || "Channel";
''',
"channel labels",
)

rep(
'                    feature.withLabel?.("Logical relay state");',
'                    feature.withLabel?.(channelLabel(feature.endpoint || expose.endpoint) + " — Logical state");',
"logical channel label",
)
rep(
'            expose.withLabel?.("Logical state after power-up");',
'            expose.withLabel?.(channelLabel(expose.endpoint) + " — State after power-up");',
"startup channel label",
)

for old, new in [
    ('label: "Mains power behavior",', 'label: channelLabel(endpointName) + " — Mains power",'),
    ('label: "Button type",', 'label: channelLabel(endpointName) + " — Button type",'),
    ('label: "Direct-binding command",', 'label: channelLabel(endpointName) + " — Direct-binding command",'),
    ('label: "Update local state",', 'label: channelLabel(endpointName) + " — Update local state",'),
    ('label: "Local state channel",', 'label: channelLabel(endpointName) + " — Local state channel",'),
    ('label: "Control bound light",', 'label: channelLabel(endpointName) + " — Control bound light",'),
    ('label: "Hold threshold",', 'label: channelLabel(endpointName) + " — Hold threshold",'),
    ('label: "Dimming speed",', 'label: channelLabel(endpointName) + " — Dimming speed",'),
    ('label: "LED shows",', 'label: channelLabel(endpointName) + " — LED shows",'),
    ('label: "Bound light state (tracked)",', 'label: channelLabel(endpointName) + " — Bound light (tracked)",'),
    ('label: "Manual LED state",', 'label: channelLabel(endpointName) + " — Manual LED",'),
    ('label: "Last button input",', 'label: channelLabel(endpointName) + " — Last button input",'),
]:
    if old not in s:
        raise SystemExit(f"missing label marker: {old}")
    s = s.replace(old, new, 1)

rep(
'''        exposes
            .enum("device_config_unlock", ea.SET, ["enable_editing"])
            .withLabel("Enable advanced editing")''',
'''        exposes
            .enum("device_config_unlock", ea.SET, ["enable_editing"])
            .withEndpoint("advanced")
            .withProperty("device_config_unlock")
            .withLabel("Advanced — Enable editing")''',
"advanced unlock endpoint",
)

start = s.index("const deviceConfigEditable = (name, endpointName) => {")
end = s.index("\n\nconst legacyActionEvent", start)
new_fn = '''const deviceConfigEditable = (name) => {
    const description =
        "Current low-level hardware map for this exact BSEED 3-gang device. Editing is locked by default: first click " +
        "Advanced — Enable editing, then save within 60 seconds. Before Zigbee traffic is sent, the converter checks identity, " +
        "required 3-gang structure, token syntax and duplicate GPIO assignments. Firmware then checks all chunks and CRC before " +
        "replacing NVM and rebooting. A valid save consumes the unlock. Wrong pin assignments can still require recovery firmware.";

    const expose = exposes
        .text(name, ea.ALL)
        .withEndpoint("advanced")
        .withProperty(name)
        .withLabel("Advanced — Hardware configuration")
        .withDescription(description)
        .withCategory("diagnostic");

    return {
        isModernExtend: true,
        exposes: [expose],
        fromZigbee: [
            {
                cluster: "genBasic",
                type: ["attributeReport", "readResponse"],
                convert: (model, msg) => {
                    if (msg.endpoint.ID !== 1) return;
                    const value = msg.data[0xff00] ?? msg.data["65280"];
                    if (value !== undefined) return {[name]: value};
                },
            },
        ],
        toZigbee: [
            {
                key: [name],
                convertGet: async (entity, key, meta) => {
                    await meta.device.getEndpoint(1).read("genBasic", [0xff00], {timeout: 30_000});
                },
                convertSet: async (entity, key, value, meta) => {
                    const bytes = validateDeviceConfig(value);
                    const unlockKey = requireDeviceConfigUnlock(meta);
                    deviceConfigUnlocks.delete(unlockKey);
                    const endpoint = meta.device.getEndpoint(1);
                    const tx = (deviceConfigTransaction % 255) + 1;
                    deviceConfigTransaction = tx;
                    const options = {disableDefaultResponse: false, timeout: 30_000};

                    for (let offset = 0; offset < bytes.length; offset += DEVICE_CONFIG_CHUNK_MAX) {
                        const chunk = [...bytes.subarray(offset, offset + DEVICE_CONFIG_CHUNK_MAX)];
                        await endpoint.command(
                            "bseedBasicTransport",
                            "deviceConfigStage",
                            {data: [tx, offset, chunk.length, ...chunk]},
                            options,
                        );
                    }

                    const crc = crc16CcittFalse(bytes);
                    await endpoint.command(
                        "bseedBasicTransport",
                        "deviceConfigCommit",
                        {data: [tx, bytes.length, crc & 0xff, crc >> 8]},
                        options,
                    );
                    return {state: {[key]: value}};
                },
            },
        ],
        configure: [],
    };
};'''
s = s[:start] + new_fn + s[end:]

rep(
'''                    relay_right: 6,
                },''',
'''                    relay_right: 6,
                    advanced: 1,
                },''',
"advanced endpoint alias",
)

rep(
'            deviceConfigEditable("device_config", "switch_left"),',
'            deviceConfigEditable("device_config"),',
"advanced call",
)

p.write_text(s, encoding="utf-8")
