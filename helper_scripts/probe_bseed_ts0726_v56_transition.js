#!/usr/bin/env node
'use strict';

const Module = require('module');
const path = require('path');

const originalLoad = Module._load;
const customClusters = [];

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
            deviceAddCustomCluster: (name, definition) => {
                customClusters.push({name, definition});
                return emptyExtend();
            },
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
        return {Zcl: {BuffaloZclDataType: {LIST_UINT8: 0x1001}, DataType: {LONG_CHAR_STR: 0x44}}};
    }
    return originalLoad.call(this, request, parent, isMain);
};

function die(message) {
    process.stderr.write(`FAIL: ${message}\n`);
    process.exit(2);
}

function actionConverters(definition) {
    const all = definition.extend.flatMap((ext) => ext.toZigbee || []);
    const keys = ['switch_left_action_mode', 'switch_middle_action_mode', 'switch_right_action_mode'];
    return Object.fromEntries(keys.map((key) => {
        const matches = all.filter((converter) => (converter.key || []).includes(key));
        if (matches.length !== 1) die(`${key}: expected one converter, got ${matches.length}`);
        return [key, matches[0]];
    }));
}

function endpoints(events, getActiveBuild) {
    const result = new Map();
    for (let id = 1; id <= 6; id++) {
        result.set(id, {
            ID: id,
            async write(cluster, payload) { events.push({op: 'write', endpoint: id, cluster, payload}); return {}; },
            async read(cluster, attributes) {
                events.push({op: 'read', endpoint: id, cluster, attributes});
                if (id === 1 && cluster === 'genBasic' && attributes?.[0] === 'swBuildId') {
                    const build = getActiveBuild();
                    if (build === '__NO_RESPONSE__') throw new Error('simulated Basic read failure');
                    return {swBuildId: build};
                }
                return {};
            },
            async command(cluster, command, payload) { events.push({op: 'command', endpoint: id, cluster, command, payload}); return {}; },
        });
    }
    return result;
}

async function main() {
    const input = process.argv[2];
    if (!input) die('usage: probe_bseed_ts0726_v56_transition.js <overlay.js>');
    const target = path.resolve(input);
    const exported = require(target);
    const definitions = Array.isArray(exported) ? exported : exported.default;
    if (!Array.isArray(definitions) || definitions.length !== 1) die('expected one definition');
    const definition = definitions[0];

    if (!Array.isArray(definition.fingerprint) || definition.fingerprint.length !== 2) {
        die(`expected two firmware fingerprints, got ${definition.fingerprint?.length}`);
    }
    const builds = definition.fingerprint.map((fp) => fp.softwareBuildID).sort();
    if (JSON.stringify(builds) !== JSON.stringify(['1.1.5-bseedv5', '1.1.6-bseedv6'])) {
        die(`unexpected fingerprints: ${JSON.stringify(builds)}`);
    }

    if (customClusters.length !== 1 || customClusters[0].name !== 'genBasic' || customClusters[0].definition?.ID !== 0x0000) {
        die(`transition overlay must instantiate exactly one genBasic custom extension: ${JSON.stringify(customClusters)}`);
    }

    const converters = actionConverters(definition);
    const events = [];
    let activeBuild = '1.1.5-bseedv5';
    const eps = endpoints(events, () => activeBuild);
    const meta = (cachedSw) => ({
        device: {
            ieeeAddr: '0xa4c13843a9d40f85',
            softwareBuildID: cachedSw,
            getEndpoint(id) { return eps.get(id); },
        },
    });

    // V5: standard named attribute only. Extended values fail closed before traffic.
    activeBuild = '1.1.5-bseedv5';
    const v5 = meta('stale-cache-value');
    await converters.switch_left_action_mode.convertSet(eps.get(1), 'switch_left_action_mode', 'Toggle', v5);
    let event = events.at(-1);
    const v5IdentityRead = events.at(-2);
    if (v5IdentityRead?.op !== 'read' || v5IdentityRead.endpoint !== 1 ||
        v5IdentityRead.cluster !== 'genBasic' || v5IdentityRead.attributes?.[0] !== 'swBuildId') {
        die(`V5 did not fresh-read Basic/swBuildId before transport choice: ${JSON.stringify(v5IdentityRead)}`);
    }
    if (event.endpoint !== 1 || event.cluster !== 'genOnOffSwitchCfg' || event.payload.switchActions !== 2) {
        die(`V5 action route mismatch: ${JSON.stringify(event)}`);
    }
    const beforeReject = events.length;
    let rejected = false;
    try {
        await converters.switch_left_action_mode.convertSet(eps.get(1), 'switch_left_action_mode', 'Match local state', v5);
    } catch (error) {
        rejected = /requires firmware 1\.1\.6-bseedv6/.test(String(error));
    }
    if (!rejected || events.length !== beforeReject) die('V5 extended mode must fail closed without Zigbee traffic');
    await converters.switch_middle_action_mode.convertGet(eps.get(1), 'switch_middle_action_mode', v5);
    event = events.at(-1);
    if (event.endpoint !== 2 || event.attributes?.[0] !== 'switchActions') {
        die(`V5 GET route mismatch: ${JSON.stringify(event)}`);
    }

    // V6: raw custom 0xff06, allowing Match/Opposite.
    activeBuild = '1.1.6-bseedv6';
    const v6 = meta('1.1.5-bseedv5');
    await converters.switch_left_action_mode.convertSet(eps.get(1), 'switch_left_action_mode', 'Match local state', v6);
    event = events.at(-1);
    const encoded = event.payload?.[0xff06]?.value;
    if (event.endpoint !== 1 || event.cluster !== 'genOnOffSwitchCfg' || encoded !== 3) {
        die(`V6 action route mismatch: ${JSON.stringify(event)}`);
    }
    await converters.switch_middle_action_mode.convertGet(eps.get(1), 'switch_middle_action_mode', v6);
    event = events.at(-1);
    if (event.endpoint !== 2 || event.attributes?.[0] !== 0xff06) {
        die(`V6 GET route mismatch: ${JSON.stringify(event)}`);
    }

    // Unknown firmware must never guess a transport.
    activeBuild = 'future-build';
    const unknown = meta('1.1.6-bseedv6');
    const beforeUnknown = events.length;
    rejected = false;
    try {
        await converters.switch_right_action_mode.convertGet(eps.get(1), 'switch_right_action_mode', unknown);
    } catch (error) {
        rejected = /not enabled for firmware/.test(String(error));
    }
    if (!rejected || events.length !== beforeUnknown + 1) die('unknown firmware did not fail closed after exactly one identity read');

    activeBuild = '__NO_RESPONSE__';
    const beforeNoResponse = events.length;
    rejected = false;
    try {
        await converters.switch_right_action_mode.convertGet(eps.get(1), 'switch_right_action_mode', meta('1.1.6-bseedv6'));
    } catch (error) {
        rejected = /cannot verify firmware identity/.test(String(error));
    }
    if (!rejected || events.length !== beforeNoResponse + 1) {
        die('firmware identity read failure must fail closed after one Basic read and no action-cluster traffic');
    }

    // Decode both V5 named readback and V6 raw readback.
    const fz = definition.extend.flatMap((ext) => ext.fromZigbee || []).filter((item) => item.cluster === 'genOnOffSwitchCfg');
    const run = (endpoint, data) => Object.assign({}, ...fz.map((converter) =>
        converter.convert(definition, {endpoint: {ID: endpoint}, data, type: 'readResponse'}, () => {}, {}, {}) || {}
    ));
    const v5Read = run(1, {switchActions: 2});
    if (v5Read.switch_left_action_mode !== 'Toggle') die(`V5 readback decode failed: ${JSON.stringify(v5Read)}`);
    const v6Read = run(2, {65286: 3});
    if (v6Read.switch_middle_action_mode !== 'Match local state') die(`V6 readback decode failed: ${JSON.stringify(v6Read)}`);

    process.stdout.write(JSON.stringify({
        status: 'PASS',
        fingerprints: builds,
        customClusterExtensions: customClusters.map((item) => ({name: item.name, ID: item.definition.ID})),
        v5: {standardAttribute: 'switchActions', extendedFailsClosed: true},
        v6: {customAttribute: '0xff06', matchLocalState: 3},
        unknownFirmwareFailsClosed: true,
        freshFirmwareIdentityRead: true,
        staleCacheIgnored: true,
        identityReadFailureFailsClosed: true,
        readback: {v5Named: true, v6Raw: true},
    }, null, 2) + '\n');
}

main().catch((error) => die(error?.stack || String(error)));
