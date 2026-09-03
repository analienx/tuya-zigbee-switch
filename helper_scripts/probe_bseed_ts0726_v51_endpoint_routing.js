#!/usr/bin/env node
'use strict';

const Module = require('module');
const path = require('path');

const originalLoad = Module._load;

const makeExpose = (name, access = 7) => ({
    name,
    property: name,
    access,
    withEndpoint(endpoint) { this.endpoint = endpoint; this.property = `${name}_${endpoint}`; return this; },
    withProperty(property) { this.property = property; return this; },
    withCategory(category) { this.category = category; return this; },
    withDescription(description) { this.description = description; return this; },
    withLabel(label) { this.label = label; return this; },
    withUnit(unit) { this.unit = unit; return this; },
    withValueMin(value) { this.value_min = value; return this; },
    withValueMax(value) { this.value_max = value; return this; },
});
const emptyExtend = () => ({isModernExtend: true, exposes: [], fromZigbee: [], toZigbee: [], configure: []});

Module._load = function(request, parent, isMain) {
    if (request === 'zigbee-herdsman-converters/lib/modernExtend') {
        return {
            binary: emptyExtend,
            deviceAddCustomCluster: emptyExtend,
            deviceEndpoints: (args) => ({...emptyExtend(), endpoint: () => args.endpoints}),
            enumLookup: emptyExtend,
            numeric: emptyExtend,
            onOff: emptyExtend,
        };
    }
    if (request === 'zigbee-herdsman-converters/lib/exposes') {
        return {
            presets: {
                action: (values) => ({...makeExpose('action', 1), values}),
                enum: (name, access, values) => ({...makeExpose(name, access), values}),
                binary: (name, access, valueOn, valueOff) => ({...makeExpose(name, access), value_on: valueOn, value_off: valueOff}),
                numeric: (name, access) => makeExpose(name, access),
            },
            access: {STATE: 1, SET: 2, GET: 4, STATE_GET: 5, STATE_SET: 3, ALL: 7},
            enum: (name, access, values) => ({...makeExpose(name, access), values}),
            text: (name, access) => makeExpose(name, access),
        };
    }
    if (request === 'zigbee-herdsman') {
        return {
            Zcl: {
                BuffaloZclDataType: {LIST_UINT8: 0x1001},
                DataType: {LONG_CHAR_STR: 0x44, ENUM8: 0x30},
            },
        };
    }
    return originalLoad.call(this, request, parent, isMain);
};

function die(message) {
    process.stderr.write(`FAIL: ${message}\n`);
    process.exit(2);
}

function findConverter(definition, key) {
    const matches = definition.extend
        .flatMap((ext) => ext.toZigbee || [])
        .filter((converter) => (converter.key || []).includes(key));
    if (matches.length !== 1) die(`${key}: expected exactly one toZigbee converter, got ${matches.length}`);
    return matches[0];
}

async function main() {
    const input = process.argv[2];
    if (!input) die('usage: probe_bseed_ts0726_v51_endpoint_routing.js <overlay.js>');

    const target = path.resolve(input);
    const exported = require(target);
    const definitions = Array.isArray(exported) ? exported : exported.default;
    if (!Array.isArray(definitions) || definitions.length !== 1) die('expected one definition');
    const definition = definitions[0];

    const events = [];
    const endpoints = new Map();
    for (let id = 1; id <= 6; id += 1) {
        endpoints.set(id, {
            ID: id,
            async write(cluster, payload, options) {
                events.push({op: 'write', endpoint: id, cluster, payload, options});
                return {};
            },
            async read(cluster, attributes, options) {
                events.push({op: 'read', endpoint: id, cluster, attributes, options});
                return {};
            },
            async command(cluster, command, payload, options) {
                events.push({op: 'command', endpoint: id, cluster, command, payload, options});
                return {};
            },
        });
    }

    // Deliberately NO endpoint_name. This matches ordinary WindFront/MQTT SETs
    // which caused the live v5 canary to fall back to the first cluster endpoint.
    const meta = {
        device: {
            ieeeAddr: '0xa4c13843a9d40f85',
            getEndpoint(id) { return endpoints.get(id); },
        },
    };

    const cases = [
        ['relay_left_physical_mode', 'Always on', 4, 'genOnOff', 0xff03, 1],
        ['relay_middle_physical_mode', 'Always on', 5, 'genOnOff', 0xff03, 1],
        ['relay_right_physical_mode', 'Follow logical state', 6, 'genOnOff', 0xff03, 0],

        ['switch_left_mode', 'Push button', 1, 'genOnOffSwitchCfg', 0xff00, 1],
        ['switch_middle_mode', 'Push button', 2, 'genOnOffSwitchCfg', 0xff00, 1],
        ['switch_right_mode', 'Push button', 3, 'genOnOffSwitchCfg', 0xff00, 1],

        ['switch_left_action_mode', 'Match local state', 1, 'genOnOffSwitchCfg', 'switchActions', 3],
        ['switch_middle_action_mode', 'Match local state', 2, 'genOnOffSwitchCfg', 'switchActions', 3],
        ['switch_right_action_mode', 'Toggle', 3, 'genOnOffSwitchCfg', 'switchActions', 2],

        ['switch_left_relay_mode', 'Short press', 1, 'genOnOffSwitchCfg', 0xff01, 3],
        ['switch_middle_relay_mode', 'Short press', 2, 'genOnOffSwitchCfg', 0xff01, 3],
        ['switch_right_relay_mode', 'Short press', 3, 'genOnOffSwitchCfg', 0xff01, 3],

        ['switch_left_relay_index', 'Left', 1, 'genOnOffSwitchCfg', 0xff02, 1],
        ['switch_middle_relay_index', 'Middle', 2, 'genOnOffSwitchCfg', 0xff02, 2],
        ['switch_right_relay_index', 'Right', 3, 'genOnOffSwitchCfg', 0xff02, 3],

        ['switch_left_binded_mode', 'Short press', 1, 'genOnOffSwitchCfg', 0xff05, 3],
        ['switch_middle_binded_mode', 'Short press', 2, 'genOnOffSwitchCfg', 0xff05, 3],
        ['switch_right_binded_mode', 'Short press', 3, 'genOnOffSwitchCfg', 0xff05, 3],

        ['switch_left_long_press_duration', 800, 1, 'genOnOffSwitchCfg', 0xff03, 800],
        ['switch_middle_long_press_duration', 900, 2, 'genOnOffSwitchCfg', 0xff03, 900],
        ['switch_right_long_press_duration', 1000, 3, 'genOnOffSwitchCfg', 0xff03, 1000],

        ['switch_left_level_move_rate', 42, 1, 'genOnOffSwitchCfg', 0xff04, 42],
        ['switch_middle_level_move_rate', 44, 2, 'genOnOffSwitchCfg', 0xff04, 44],
        ['switch_right_level_move_rate', 50, 3, 'genOnOffSwitchCfg', 0xff04, 50],

        ['relay_left_indicator_mode', 'Binding status', 4, 'genOnOff', 0xff01, 4],
        ['relay_middle_indicator_mode', 'Binding status', 5, 'genOnOff', 0xff01, 4],
        ['relay_right_indicator_mode', 'Physical output', 6, 'genOnOff', 0xff01, 3],

        ['relay_left_binding_intent', 'ON', 4, 'genOnOff', 0xff04, 1],
        ['relay_middle_binding_intent', 'OFF', 5, 'genOnOff', 0xff04, 0],
        ['relay_right_binding_intent', 'ON', 6, 'genOnOff', 0xff04, 1],

        ['relay_left_indicator', 'ON', 4, 'genOnOff', 0xff02, 1],
        ['relay_middle_indicator', 'OFF', 5, 'genOnOff', 0xff02, 0],
        ['relay_right_indicator', 'ON', 6, 'genOnOff', 0xff02, 1],
    ];

    for (const [key, value, endpoint, cluster, attr, raw] of cases) {
        const converter = findConverter(definition, key);
        if (typeof converter.convertSet !== 'function' || typeof converter.convertGet !== 'function') {
            die(`${key}: SET/GET converter missing`);
        }

        const before = events.length;
        await converter.convertSet(endpoints.get(1), key, value, meta);
        const write = events[before];
        if (!write || write.op !== 'write') die(`${key}: SET did not emit one write`);
        if (write.endpoint !== endpoint) die(`${key}: SET hit EP${write.endpoint}, expected EP${endpoint}`);
        if (write.cluster !== cluster) die(`${key}: SET cluster ${write.cluster}, expected ${cluster}`);
        const encoded = write.payload[attr]?.value ?? write.payload[attr];
        if (encoded !== raw) die(`${key}: SET raw ${encoded}, expected ${raw}`);
        if (key.endsWith('_action_mode') && write.payload.switchActions !== raw) {
            die(`${key}: SET must use primitive named switchActions payload: ${JSON.stringify(write.payload)}`);
        }

        const beforeGet = events.length;
        await converter.convertGet(endpoints.get(1), key, meta);
        const read = events[beforeGet];
        if (!read || read.op !== 'read') die(`${key}: GET did not emit one read`);
        if (read.endpoint !== endpoint) die(`${key}: GET hit EP${read.endpoint}, expected EP${endpoint}`);
        if (read.cluster !== cluster || read.attributes[0] !== attr) {
            die(`${key}: GET route mismatch ${JSON.stringify(read)}`);
        }
    }

    const config = findConverter(definition, 'device_config');
    const beforeConfigGet = events.length;
    await config.convertGet(endpoints.get(1), 'device_config', meta);
    const configRead = events[beforeConfigGet];
    if (!configRead || configRead.op !== 'read' || configRead.endpoint !== 1 ||
        configRead.cluster !== 'genBasic' || configRead.attributes[0] !== 'deviceConfig') {
        die(`device_config GET is not pinned to EP1 named deviceConfig: ${JSON.stringify(configRead)}`);
    }

    const fz = definition.extend.flatMap((ext) => ext.fromZigbee || []);

    const runFz = (cluster, endpointId, data) => {
        const results = [];
        for (const converter of fz.filter((item) => item.cluster === cluster)) {
            const result = converter.convert(
                definition,
                {endpoint: {ID: endpointId}, data, type: 'readResponse'},
                () => {},
                {},
                {device: meta.device},
            );
            if (result) results.push(result);
        }
        return Object.assign({}, ...results);
    };

    const leftPhysical = runFz('genOnOff', 4, {65283: 1});
    if (leftPhysical.relay_left_physical_mode !== 'Always on') {
        die(`LEFT physical readback did not map correctly: ${JSON.stringify(leftPhysical)}`);
    }
    if ('relay_middle_physical_mode' in leftPhysical || 'relay_right_physical_mode' in leftPhysical) {
        die(`LEFT raw report leaked into another channel: ${JSON.stringify(leftPhysical)}`);
    }

    const middleAction = runFz('genOnOffSwitchCfg', 2, {switchActions: 3});
    if (middleAction.switch_middle_action_mode !== 'Match local state') {
        die(`MIDDLE action readback did not map correctly: ${JSON.stringify(middleAction)}`);
    }
    if ('switch_left_action_mode' in middleAction || 'switch_right_action_mode' in middleAction) {
        die(`MIDDLE action readback leaked into another channel: ${JSON.stringify(middleAction)}`);
    }

    const canonical = 'iedhxgyi;TS0726-3-BS;LC4;SB1u;RC2;IC0;SB7u;RC3;ID7;SB4u;RD2;IB5;M;';
    const configResult = runFz('genBasic', 1, {deviceConfig: canonical});
    if (configResult.device_config !== canonical) {
        die(`device_config readback failed: ${JSON.stringify(configResult)}`);
    }

    process.stdout.write(JSON.stringify({
        status: 'PASS',
        metaEndpointNamePresent: false,
        setGetCases: cases.length,
        allSetEndpointsPinned: true,
        allGetEndpointsPinned: true,
        leftPhysicalReadbackIsolated: true,
        middleActionReadbackIsolated: true,
        deviceConfigGetEndpoint: 1,
        deviceConfigNamedAttribute: 'deviceConfig',
        deviceConfigReadback: true,
        acceptedProfile: {
            left: {mains: 'Always on', led: 'Binding status', action: 'Match local state'},
            middle: {mains: 'Always on', led: 'Binding status', action: 'Match local state'},
            right: {mains: 'Follow logical state', led: 'Physical output', action: 'Toggle'},
        },
    }, null, 2) + '\n');
}

main().catch((error) => die(error && error.stack ? error.stack : String(error)));
