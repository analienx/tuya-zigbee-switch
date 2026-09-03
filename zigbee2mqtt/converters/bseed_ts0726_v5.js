'use strict';

/**
 * BSEED TS0726-3-BS v5 canary overlay.
 *
 * Deployment:
 *   - keep the exact historical fleet converter (ef79...) unchanged;
 *   - load this file beside it;
 *   - this definition wins only for forward firmware 1.1.5-bseedv5.
 *
 * No configure callback below creates bindings or reporting.
 */

const {
    binary,
    deviceAddCustomCluster,
    deviceEndpoints,
    enumLookup,
    numeric,
    onOff,
} = require("zigbee-herdsman-converters/lib/modernExtend");
const exposes = require("zigbee-herdsman-converters/lib/exposes");
const e = exposes.presets;
const ea = exposes.access;
const {Zcl} = require("zigbee-herdsman");

const withExposeLabel = (extend, label) => {
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

const ENDPOINT_IDS = {
    switch_left: 1,
    switch_middle: 2,
    switch_right: 3,
    relay_left: 4,
    relay_middle: 5,
    relay_right: 6,
    advanced: 1,
};

const pinnedEndpoint = (meta, endpointName) => {
    const id = ENDPOINT_IDS[endpointName];
    if (!id) throw new Error(`Unknown endpoint ${endpointName}`);
    const endpoint = meta?.device?.getEndpoint?.(id);
    if (!endpoint) throw new Error(`Endpoint ${endpointName} (EP${id}) is unavailable`);
    return endpoint;
};

const rawAttributeValue = (msg, attribute) => {
    if (typeof attribute === "string") return msg.data?.[attribute];
    return msg.data?.[attribute.ID] ?? msg.data?.[String(attribute.ID)] ?? (attribute.name ? msg.data?.[attribute.name] : undefined);
};

const decorateExpose = (expose, endpointName, property, label, description, category) => {
    expose.withEndpoint?.(endpointName);
    expose.withProperty?.(property);
    if (label) expose.withLabel?.(label);
    if (description) expose.withDescription?.(description);
    if (category) expose.withCategory?.(category);
    return expose;
};

const pinnedEnum = ({
    name,
    endpointName,
    lookup,
    cluster,
    attribute,
    label,
    description,
    access = "ALL",
    entityCategory,
}) => {
    const endpointId = ENDPOINT_IDS[endpointName];
    const expose = decorateExpose(
        e.enum(name, ea[access], Object.keys(lookup)),
        endpointName,
        name,
        label,
        description,
        entityCategory,
    );
    const reverse = new Map(Object.entries(lookup).map(([key, value]) => [String(value), key]));
    const attributeKey = typeof attribute === "string" ? attribute : attribute.ID;

    return {
        isModernExtend: true,
        exposes: [expose],
        fromZigbee: [{
            cluster,
            type: ["attributeReport", "readResponse"],
            convert: (model, msg) => {
                if (msg.endpoint.ID !== endpointId) return;
                const raw = rawAttributeValue(msg, attribute);
                const value = reverse.get(String(raw));
                if (value !== undefined) return {[name]: value};
            },
        }],
        toZigbee: [{
            key: [name],
            convertSet: ea[access] & ea.SET ? async (entity, key, value, meta) => {
                if (!Object.prototype.hasOwnProperty.call(lookup, value)) {
                    throw new Error(`${name}: unsupported value ${JSON.stringify(value)}`);
                }
                const raw = lookup[value];
                const payload = typeof attribute === "string"
                    ? {[attribute]: raw}
                    : {[attribute.ID]: {value: raw, type: attribute.type}};
                await pinnedEndpoint(meta, endpointName).write(cluster, payload);
                return {state: {[key]: value}};
            } : undefined,
            convertGet: ea[access] & ea.GET ? async (entity, key, meta) => {
                await pinnedEndpoint(meta, endpointName).read(cluster, [attributeKey]);
            } : undefined,
        }],
        configure: [],
    };
};

const pinnedBinary = ({
    name,
    endpointName,
    valueOn,
    valueOff,
    cluster,
    attribute,
    label,
    description,
    access = "ALL",
    entityCategory,
}) => {
    const endpointId = ENDPOINT_IDS[endpointName];
    const expose = decorateExpose(
        e.binary(name, ea[access], valueOn[0], valueOff[0]),
        endpointName,
        name,
        label,
        description,
        entityCategory,
    );
    const attributeKey = typeof attribute === "string" ? attribute : attribute.ID;

    return {
        isModernExtend: true,
        exposes: [expose],
        fromZigbee: [{
            cluster,
            type: ["attributeReport", "readResponse"],
            convert: (model, msg) => {
                if (msg.endpoint.ID !== endpointId) return;
                const raw = rawAttributeValue(msg, attribute);
                if (raw === valueOn[1]) return {[name]: valueOn[0]};
                if (raw === valueOff[1]) return {[name]: valueOff[0]};
            },
        }],
        toZigbee: [{
            key: [name],
            convertSet: ea[access] & ea.SET ? async (entity, key, value, meta) => {
                let raw;
                if (value === valueOn[0] || value === valueOn[1]) raw = valueOn[1];
                else if (value === valueOff[0] || value === valueOff[1]) raw = valueOff[1];
                else throw new Error(`${name}: expected ${valueOn[0]} or ${valueOff[0]}`);
                const payload = typeof attribute === "string"
                    ? {[attribute]: raw}
                    : {[attribute.ID]: {value: raw, type: attribute.type}};
                await pinnedEndpoint(meta, endpointName).write(cluster, payload);
                return {state: {[key]: raw === valueOn[1] ? valueOn[0] : valueOff[0]}};
            } : undefined,
            convertGet: ea[access] & ea.GET ? async (entity, key, meta) => {
                await pinnedEndpoint(meta, endpointName).read(cluster, [attributeKey]);
            } : undefined,
        }],
        configure: [],
    };
};

const pinnedNumeric = ({
    name,
    endpointName,
    cluster,
    attribute,
    label,
    description,
    unit,
    valueMin,
    valueMax,
    access = "ALL",
    entityCategory,
}) => {
    const endpointId = ENDPOINT_IDS[endpointName];
    let expose = e.numeric(name, ea[access]);
    expose = decorateExpose(expose, endpointName, name, label, description, entityCategory);
    if (unit) expose.withUnit?.(unit);
    if (valueMin !== undefined) expose.withValueMin?.(valueMin);
    if (valueMax !== undefined) expose.withValueMax?.(valueMax);
    const attributeKey = typeof attribute === "string" ? attribute : attribute.ID;

    return {
        isModernExtend: true,
        exposes: [expose],
        fromZigbee: [{
            cluster,
            type: ["attributeReport", "readResponse"],
            convert: (model, msg) => {
                if (msg.endpoint.ID !== endpointId) return;
                const raw = rawAttributeValue(msg, attribute);
                if (typeof raw === "number") return {[name]: raw};
            },
        }],
        toZigbee: [{
            key: [name],
            convertSet: ea[access] & ea.SET ? async (entity, key, value, meta) => {
                if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`${name}: expected a number`);
                if (valueMin !== undefined && value < valueMin) throw new Error(`${name}: minimum is ${valueMin}`);
                if (valueMax !== undefined && value > valueMax) throw new Error(`${name}: maximum is ${valueMax}`);
                const payload = typeof attribute === "string"
                    ? {[attribute]: value}
                    : {[attribute.ID]: {value, type: attribute.type}};
                await pinnedEndpoint(meta, endpointName).write(cluster, payload);
                return {state: {[key]: value}};
            } : undefined,
            convertGet: ea[access] & ea.GET ? async (entity, key, meta) => {
                await pinnedEndpoint(meta, endpointName).read(cluster, [attributeKey]);
            } : undefined,
        }],
        configure: [],
    };
};

const logicalOnOff = (endpointNames) => {
    const result = onOff({endpointNames, configureReporting: false});
    for (const expose of result.exposes || []) {
        if (expose.type === "switch") {
            for (const feature of expose.features || []) {
                if (feature.name === "state") {
                    feature.withLabel?.(channelLabel(feature.endpoint || expose.endpoint) + " — Logical state");
                    feature.withDescription?.(
                        "The Zigbee state for this channel. Use it for automations and state tracking. " +
                        "It is not the same as mains power: with Mains power behavior set to Always on, " +
                        "this may be OFF while the smart load remains continuously powered.",
                    );
                }
            }
        } else if (expose.name === "power_on_behavior") {
            expose.withLabel?.(channelLabel(expose.endpoint) + " — State after power-up");
            expose.withDescription?.(
                "Chooses the Zigbee state restored after restart. This affects the logical channel only. " +
                "It never overrides Mains power behavior, so Always on remains electrically powered.",
            );
        }
    }
    return result;
};

const physicalRelayMode = (name, endpointName) =>
    pinnedEnum({
        name,
        endpointName,
        lookup: {"Follow logical state": 0, "Always on": 1, "Always off": 2},
        cluster: "genOnOff",
        attribute: {ID: 0xff03, type: 0x30},
        label: channelLabel(endpointName) + " — Mains power",
        description:
            "Controls the actual electrical output for this channel. For smart bulbs and smart dimmers choose Always on " +
            "so they stay powered while logical state changes. Follow logical state is for a load this device should physically switch. " +
            "Always off keeps the output de-energized. Changing this can affect power immediately, so verify the connected load first. " +
            "The choice survives restart.",
        entityCategory: "config",
    });

const buttonType = (name, endpointName) =>
    pinnedEnum({
        name,
        endpointName,
        lookup: {"Rocker / toggle": 0, "Push button": 1, "Push button (normally closed)": 2},
        cluster: "genOnOffSwitchCfg",
        attribute: {ID: 0xff00, type: 0x30},
        label: channelLabel(endpointName) + " — Button type",
        description:
            "Match this to the wall mechanism. Rocker / toggle is a maintained switch. Push button is a normal momentary input. " +
            "Use Push button (normally closed) only for an NC contact. A wrong choice makes presses appear inverted or unreliable.",
        entityCategory: "config",
    });

const buttonCommandBehavior = (name, endpointName) =>
    pinnedEnum({
        name,
        endpointName,
        lookup: {
            "On then off": 0,
            "Off then on": 1,
            "Toggle": 2,
            "Match local state": 3,
            "Opposite local state": 4,
        },
        cluster: "genOnOffSwitchCfg",
        attribute: {ID: 0x0010, type: 0x30},
        label: channelLabel(endpointName) + " — Direct-binding command",
        description:
            "Chooses the On/Off command sent directly to bound lights. Toggle is the simplest choice and does not depend on local state. " +
            "Match local state sends explicit On/Off to match this channel; Opposite local state sends the inverse. " +
            "On then off and Off then on are mainly useful with maintained rocker inputs.",
        entityCategory: "config",
    });

const localRelayTrigger = (name, endpointName) =>
    pinnedEnum({
        name,
        endpointName,
        lookup: {"Never (detached)": 0, "On press": 1, "Short press": 3, "Long press": 2},
        cluster: "genOnOffSwitchCfg",
        attribute: {ID: 0xff01, type: 0x30},
        label: channelLabel(endpointName) + " — Update local state",
        description:
            "Chooses when this physical button updates the assigned Zigbee state. Never (detached) leaves local state untouched. " +
            "On press updates immediately; Short press waits for a completed click; Long press waits for the hold threshold. " +
            "This does not override Mains power behavior.",
        entityCategory: "config",
    });

const localRelayIndex = (name, endpointName) =>
    pinnedEnum({
        name,
        endpointName,
        lookup: {Left: 1, Middle: 2, Right: 3},
        cluster: "genOnOffSwitchCfg",
        attribute: {ID: 0xff02, type: 0x20},
        label: channelLabel(endpointName) + " — Local state channel",
        description:
            "Chooses which local Zigbee channel this button updates: Left, Middle or Right. " +
            "This affects logical state only and does not remap physical GPIO pins.",
        entityCategory: "config",
    });

const boundDeviceTrigger = (name, endpointName) =>
    pinnedEnum({
        name,
        endpointName,
        lookup: {"On press": 1, "Short press": 3, "Long press": 2},
        cluster: "genOnOffSwitchCfg",
        attribute: {ID: 0xff05, type: 0x30},
        label: channelLabel(endpointName) + " — Control bound light",
        description:
            "Chooses when the button sends a direct command to existing bindings or groups. On press is fastest; " +
            "Short press waits for a completed click; Long press sends only after a hold. This does not create or remove bindings.",
        entityCategory: "config",
    });

const longPressThreshold = (name, endpointName) =>
    pinnedNumeric({
        name,
        endpointName,
        cluster: "genOnOffSwitchCfg",
        attribute: {ID: 0xff03, type: 0x21},
        label: channelLabel(endpointName) + " — Hold threshold",
        description: "How long the button must stay pressed before it becomes a long press. Increase it to reduce accidental holds; decrease it for faster hold actions.",
        unit: "ms",
        valueMin: 0,
        valueMax: 5000,
        entityCategory: "config",
    });

const holdDimmingSpeed = (name, endpointName) =>
    pinnedNumeric({
        name,
        endpointName,
        cluster: "genOnOffSwitchCfg",
        attribute: {ID: 0xff04, type: 0x20},
        label: channelLabel(endpointName) + " — Dimming speed",
        description: "Speed of direct-binding brightness changes while held. Higher values dim faster; lower values give finer control.",
        unit: "level/s",
        valueMin: 1,
        valueMax: 255,
        entityCategory: "config",
    });

const indicatorBehavior = (name, endpointName) =>
    pinnedEnum({
        name,
        endpointName,
        lookup: {
            "Logical state": 0,
            "Inverse logical state": 1,
            "Manual": 2,
            "Physical output": 3,
            "Binding status": 4,
        },
        cluster: "genOnOff",
        attribute: {ID: 0xff01, type: 0x30},
        label: channelLabel(endpointName) + " — LED shows",
        description:
            "Chooses what the small panel LED represents. Logical state follows this channel's Zigbee state. " +
            "Inverse logical state shows the opposite. Manual lets you control the LED separately. Physical output shows " +
            "the electrical-output command. Binding status is recommended when this button directly controls a smart light: " +
            "it follows locally tracked On/Off intent sent through the binding. It is intent, not confirmation of remote state. " +
            "These options control only the panel LED and never change the electrical output.",
        entityCategory: "config",
    });

const bindingIntentState = (name, endpointName) =>
    pinnedBinary({
        name,
        endpointName,
        valueOn: ["ON", 1],
        valueOff: ["OFF", 0],
        cluster: "genOnOff",
        attribute: {ID: 0xff04, type: 0x10},
        label: channelLabel(endpointName) + " — Bound light (tracked)",
        description:
            "The On/Off state this device currently believes the bound light should be in. Direct-binding commands update it locally, " +
            "and Home Assistant can correct it from the real light state. Use it with LED shows = Binding status. " +
            "It is not remote-state confirmation by itself. Changing this only corrects the local tracker: it sends no command, " +
            "changes no binding and never changes electrical power.",
        entityCategory: "config",
    });

const indicatorState = (name, endpointName) =>
    pinnedBinary({
        name,
        endpointName,
        valueOn: ["ON", 1],
        valueOff: ["OFF", 0],
        cluster: "genOnOff",
        attribute: {ID: 0xff02, type: 0x10},
        label: channelLabel(endpointName) + " — Manual LED",
        description: "Turns the panel LED on or off when LED shows is Manual. It has no effect in other LED modes and never controls power.",
        entityCategory: "config",
    });

const lastButtonAction = (name, endpointName) =>
    pinnedEnum({
        name,
        endpointName,
        access: "STATE_GET",
        lookup: {Released: 0, Press: 1, "Long press": 2, "Position on": 3, "Position off": 4},
        cluster: "genMultistateInput",
        attribute: "presentValue",
        label: channelLabel(endpointName) + " — Last button input",
        description: "Diagnostic view of the most recent physical input event seen by firmware. Useful for checking wiring and input mode.",
        entityCategory: "diagnostic",
    });

const networkIndicator = (name, endpointName) =>
    pinnedBinary({
        name,
        endpointName,
        valueOn: ["ON", 1],
        valueOff: ["OFF", 0],
        cluster: "genBasic",
        attribute: {ID: 0xff01, type: 0x10},
        label: "Network LED",
        description: "Controls the separate network-status LED. This is independent of the three channel LEDs.",
        entityCategory: "config",
    });

const multiPressResetCount = (name, endpointName) =>
    pinnedNumeric({
        name,
        endpointName,
        cluster: "genBasic",
        attribute: {ID: 0xff02, type: 0x20},
        label: "Factory-reset press count",
        description: "Consecutive presses required for factory reset. Set to 0 to disable press-based reset. Change only if you have another recovery method.",
        valueMin: 0,
        valueMax: 255,
        entityCategory: "config",
    });

const DEVICE_CONFIG_CHUNK_MAX = 24;
const DEVICE_CONFIG_UNLOCK_MS = 60_000;
let deviceConfigTransaction = 0;
const deviceConfigUnlocks = new Map();

const deviceConfigDeviceKey = (meta) => {
    const ieee = meta?.device?.ieeeAddr;
    if (!ieee) throw new Error("Cannot identify device for advanced configuration lock");
    return ieee;
};

const deviceConfigUnlock = () => ({
    isModernExtend: true,
    exposes: [
        exposes
            .enum("device_config_unlock", ea.SET, ["enable_editing"])
            .withEndpoint("advanced")
            .withProperty("device_config_unlock")
            .withLabel("Advanced — Enable editing")
            .withDescription(
                "Unlocks Hardware configuration for this device for 60 seconds. The button itself changes nothing. " +
                "A valid save consumes the unlock immediately. If transfer fails, unlock again before retrying. " +
                "Restarting Zigbee2MQTT also returns the editor to locked.",
            )
            .withCategory("config"),
    ],
    fromZigbee: [],
    toZigbee: [
        {
            key: ["device_config_unlock"],
            convertSet: async (entity, key, value, meta) => {
                if (value !== "enable_editing") throw new Error("Unsupported advanced-editing action");
                deviceConfigUnlocks.set(deviceConfigDeviceKey(meta), Date.now() + DEVICE_CONFIG_UNLOCK_MS);
                return {};
            },
        },
    ],
    configure: [],
});

const requireDeviceConfigUnlock = (meta) => {
    const key = deviceConfigDeviceKey(meta);
    const expiresAt = deviceConfigUnlocks.get(key) || 0;
    if (Date.now() >= expiresAt) {
        deviceConfigUnlocks.delete(key);
        throw new Error(
            "Hardware configuration is locked. Click 'Enable advanced editing' and save again within 60 seconds.",
        );
    }
    return key;
};

const configTransportCluster = () =>
    deviceAddCustomCluster("genBasic", {
        name: "genBasic",
        ID: 0x0000,
        attributes: {
            deviceConfig: {name: "deviceConfig", ID: 0xff00, type: Zcl.DataType.LONG_CHAR_STR, write: true},
        },
        commands: {
            deviceConfigStage: {
                name: "deviceConfigStage",
                ID: 0xf0,
                parameters: [{name: "data", type: Zcl.BuffaloZclDataType.LIST_UINT8}],
            },
            deviceConfigCommit: {
                name: "deviceConfigCommit",
                ID: 0xf1,
                parameters: [{name: "data", type: Zcl.BuffaloZclDataType.LIST_UINT8}],
            },
        },
        commandsResponse: {},
    });

const validateDeviceConfig = (value) => {
    if (typeof value !== "string") throw new Error("Hardware configuration must be text");
    if (value.length < 4 || value.length >= 128) {
        throw new Error("Hardware configuration must contain 4..127 printable ASCII characters");
    }
    for (const ch of value) {
        const code = ch.charCodeAt(0);
        if (code < 0x20 || code > 0x7e) {
            throw new Error("Hardware configuration accepts printable ASCII only");
        }
    }
    if (!value.endsWith(";")) throw new Error("Hardware configuration must end with ';'");

    const tokens = value.slice(0, -1).split(";");
    if (tokens.some((token) => token.length === 0)) {
        throw new Error("Hardware configuration contains an empty token");
    }
    if (tokens[0] !== "iedhxgyi" || tokens[1] !== "TS0726-3-BS") {
        throw new Error("Manufacturer and model must remain iedhxgyi / TS0726-3-BS");
    }

    const entries = tokens.slice(2);
    const network = entries.filter((token) => /^L[A-E][0-7]i?$/.test(token));
    const switches = entries.filter((token) => /^S[A-E][0-7][uUdDfFnN]$/.test(token));
    const relays = entries.filter((token) => /^R[A-E][0-7]([A-E][0-7])?$/.test(token));
    const indicators = entries.filter((token) => /^I[A-E][0-7]i?$/.test(token));
    const momentary = entries.filter((token) => token === "M");
    const advancedOptions = entries.filter((token) => token === "SLP" || /^D[0-9]+$/.test(token));
    const recognized = new Set([...network, ...switches, ...relays, ...indicators, ...momentary, ...advancedOptions]);
    const unknown = entries.filter((token) => !recognized.has(token));
    if (unknown.length) {
        throw new Error("Unsupported token(s) for this BSEED 3-gang board: " + unknown.join(", "));
    }
    if (network.length !== 1 || switches.length !== 3 || relays.length !== 3 || indicators.length !== 3 || momentary.length !== 1) {
        throw new Error("This board must keep exactly 1 network LED, 3 switch inputs, 3 relay outputs, 3 channel LEDs and the M marker");
    }

    const pins = [];
    for (const token of network) pins.push(token.slice(1, 3));
    for (const token of switches) pins.push(token.slice(1, 3));
    for (const token of indicators) pins.push(token.slice(1, 3));
    for (const token of relays) {
        pins.push(token.slice(1, 3));
        if (token.length === 5) pins.push(token.slice(3, 5));
    }
    const duplicates = pins.filter((pin, index) => pins.indexOf(pin) !== index);
    if (duplicates.length) {
        throw new Error("GPIO pin(s) assigned more than once: " + [...new Set(duplicates)].join(", "));
    }

    return Buffer.from(value, "ascii");
};

const crc16CcittFalse = (bytes) => {
    let crc = 0xffff;
    for (const byte of bytes) {
        crc ^= byte << 8;
        for (let bit = 0; bit < 8; bit++) {
            crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xffff : (crc << 1) & 0xffff;
        }
    }
    return crc;
};

const deviceConfigEditable = (name) => {
    const description =
        "Current low-level hardware map for this exact BSEED 3-gang device. Editing is locked by default: first click " +
        "Advanced — Enable editing, then save within 60 seconds. Before Zigbee traffic is sent, the converter checks identity, " +
        "required 3-gang structure, token syntax and duplicate GPIO assignments. Safe Romasku options D… (debounce) and SLP are allowed. " +
        "Firmware repeats the board-topology checks, then verifies all chunks and CRC before " +
        "replacing NVM and rebooting. A valid save consumes the unlock. Wrong pin assignments can still require recovery firmware.";

    const expose = exposes
        .text(name, ea.ALL)
        .withEndpoint("advanced")
        .withProperty(name)
        .withLabel("Advanced — Hardware configuration")
        .withDescription(description)
        .withCategory("config");

    return {
        isModernExtend: true,
        exposes: [expose],
        fromZigbee: [
            {
                cluster: "genBasic",
                type: ["attributeReport", "readResponse"],
                convert: (model, msg) => {
                    if (msg.endpoint.ID !== 1) return;
                    const value = msg.data.deviceConfig ?? msg.data[0xff00] ?? msg.data["65280"];
                    if (value !== undefined) return {[name]: value};
                },
            },
        ],
        toZigbee: [
            {
                key: [name],
                convertGet: async (entity, key, meta) => {
                    await meta.device.getEndpoint(1).read("genBasic", ["deviceConfig"], {timeout: 30_000});
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
                            "genBasic",
                            "deviceConfigStage",
                            {data: [tx, offset, chunk.length, ...chunk]},
                            options,
                        );
                    }

                    const crc = crc16CcittFalse(bytes);
                    await endpoint.command(
                        "genBasic",
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
};

const legacyActionEvent = () => {
    const switches = [
        {endpoint: 1, prefix: "switch_0", name: "switch_left"},
        {endpoint: 2, prefix: "switch_1", name: "switch_middle"},
        {endpoint: 3, prefix: "switch_2", name: "switch_right"},
    ];
    const states = {
        0: "release",
        1: "press",
        2: "long_press",
        3: "position_on",
        4: "position_off",
    };
    const onOffCommands = {commandOn: "on", commandOff: "off", commandToggle: "toggle"};
    const levelSuffixes = ["brightness_move_up", "brightness_move_down", "brightness_stop"];
    const prefixes = Object.fromEntries(switches.map((item) => [item.endpoint, item.prefix]));
    const names = Object.fromEntries(switches.map((item) => [item.endpoint, item.name]));

    const perButtonValues = [
        ...Object.values(states),
        ...Object.values(onOffCommands),
        ...levelSuffixes,
    ];
    const aggregateValues = [];
    for (const item of switches) {
        for (const suffix of perButtonValues) {
            aggregateValues.push(item.prefix + "_" + suffix);
        }
    }

    const result = (msg, suffix) => {
        const prefix = prefixes[msg.endpoint.ID];
        const name = names[msg.endpoint.ID];
        if (prefix === undefined || name === undefined || suffix === undefined) return;
        return {
            action: prefix + "_" + suffix,
            ["action_" + name]: suffix,
        };
    };

    return {
        isModernExtend: true,
        exposes: [
            e.action(aggregateValues),
            ...switches.map((item) => e.action(perButtonValues).withEndpoint(item.name)),
        ],
        fromZigbee: [
            {
                cluster: "genMultistateInput",
                type: ["attributeReport", "readResponse"],
                convert: (model, msg) => result(msg, states[msg.data.presentValue]),
            },
            {
                cluster: "genOnOff",
                type: Object.keys(onOffCommands),
                convert: (model, msg) => result(msg, onOffCommands[msg.type]),
            },
            {
                cluster: "genLevelCtrl",
                type: ["commandMove", "commandMoveWithOnOff", "commandStop", "commandStopWithOnOff"],
                convert: (model, msg) => {
                    const stop = msg.type === "commandStop" || msg.type === "commandStopWithOnOff";
                    const suffix = stop
                        ? "brightness_stop"
                        : "brightness_move_" + (msg.data.movemode === 0 ? "up" : "down");
                    return result(msg, suffix);
                },
            },
        ],
    };
};

module.exports = [
    {
        fingerprint: [
            {
                manufacturerName: "iedhxgyi",
                modelID: "TS0726-3-BS",
                softwareBuildID: "1.1.5-bseedv5",
                priority: 100,
            },
        ],
        model: "EC-GL86ZPCS31",
        vendor: "Tuya-custom",
        description: "BSEED 3-gang smart-light controller — protected mains control, direct binding and advanced configuration",
        extend: [
            configTransportCluster(),
            deviceEndpoints({
                endpoints: {
                    switch_left: 1,
                    switch_middle: 2,
                    switch_right: 3,
                    relay_left: 4,
                    relay_middle: 5,
                    relay_right: 6,
                    advanced: 1,
                },
            }),

            logicalOnOff(["relay_left", "relay_middle", "relay_right"]),

            physicalRelayMode("relay_left_physical_mode", "relay_left"),
            physicalRelayMode("relay_middle_physical_mode", "relay_middle"),
            physicalRelayMode("relay_right_physical_mode", "relay_right"),

            buttonType("switch_left_mode", "switch_left"),
            buttonCommandBehavior("switch_left_action_mode", "switch_left"),
            localRelayTrigger("switch_left_relay_mode", "switch_left"),
            localRelayIndex("switch_left_relay_index", "switch_left"),
            boundDeviceTrigger("switch_left_binded_mode", "switch_left"),
            longPressThreshold("switch_left_long_press_duration", "switch_left"),
            holdDimmingSpeed("switch_left_level_move_rate", "switch_left"),

            buttonType("switch_middle_mode", "switch_middle"),
            buttonCommandBehavior("switch_middle_action_mode", "switch_middle"),
            localRelayTrigger("switch_middle_relay_mode", "switch_middle"),
            localRelayIndex("switch_middle_relay_index", "switch_middle"),
            boundDeviceTrigger("switch_middle_binded_mode", "switch_middle"),
            longPressThreshold("switch_middle_long_press_duration", "switch_middle"),
            holdDimmingSpeed("switch_middle_level_move_rate", "switch_middle"),

            buttonType("switch_right_mode", "switch_right"),
            buttonCommandBehavior("switch_right_action_mode", "switch_right"),
            localRelayTrigger("switch_right_relay_mode", "switch_right"),
            localRelayIndex("switch_right_relay_index", "switch_right"),
            boundDeviceTrigger("switch_right_binded_mode", "switch_right"),
            longPressThreshold("switch_right_long_press_duration", "switch_right"),
            holdDimmingSpeed("switch_right_level_move_rate", "switch_right"),

            indicatorBehavior("relay_left_indicator_mode", "relay_left"),
            bindingIntentState("relay_left_binding_intent", "relay_left"),
            indicatorState("relay_left_indicator", "relay_left"),
            indicatorBehavior("relay_middle_indicator_mode", "relay_middle"),
            bindingIntentState("relay_middle_binding_intent", "relay_middle"),
            indicatorState("relay_middle_indicator", "relay_middle"),
            indicatorBehavior("relay_right_indicator_mode", "relay_right"),
            bindingIntentState("relay_right_binding_intent", "relay_right"),
            indicatorState("relay_right_indicator", "relay_right"),

            lastButtonAction("switch_left_press_action", "switch_left"),
            lastButtonAction("switch_middle_press_action", "switch_middle"),
            lastButtonAction("switch_right_press_action", "switch_right"),
            legacyActionEvent(),

            networkIndicator("network_led", "switch_left"),
            multiPressResetCount("multi_press_reset_count", "switch_left"),

            // Advanced section: deliberately last and locked by default.
            deviceConfigUnlock(),
            deviceConfigEditable("device_config"),
        ],
        meta: {multiEndpoint: true},
        configure: async () => {},
        ota: true,
    },
];
