#!/usr/bin/env node
'use strict';

const Module = require('module');

// This probe must run on a clean checkout too, without installing ZHC.
// Stub only the construction-time helpers used while loading the overlay.
// The actual historical action decoder callbacks are still the real code from
// bseed_ts0726_v4.js and are invoked below with synthetic Zigbee frames.
const originalLoad = Module._load;
const emptyExtend = () => ({isModernExtend: true, exposes: [], fromZigbee: [], toZigbee: []});
const exposeAction = (values) => ({
    name: 'action',
    property: 'action',
    values,
    withEndpoint(endpoint) {
        this.endpoint = endpoint;
        this.property = `action_${endpoint}`;
        return this;
    },
});
Module._load = function(request, parent, isMain) {
    if (request === 'zigbee-herdsman-converters/lib/modernExtend') {
        return {
            binary: emptyExtend,
            deviceEndpoints: emptyExtend,
            enumLookup: emptyExtend,
            numeric: emptyExtend,
            onOff: emptyExtend,
            text: emptyExtend,
        };
    }
    if (request === 'zigbee-herdsman-converters/lib/exposes') {
        return {presets: {action: exposeAction}};
    }
    return originalLoad.call(this, request, parent, isMain);
};

/**
 * Offline behavioral proof for the preserved BSEED TS0726 action API.
 *
 * Loads a generated target-only converter and invokes ONLY the historical
 * action decoder ModernExtend callbacks against synthetic Zigbee messages.
 * No coordinator/device access.
 */

function die(message) {
    process.stderr.write(`FAIL: ${message}\n`);
    process.exit(2);
}

function loadDefinitions(path) {
    delete require.cache[require.resolve(path)];
    const exported = require(path);
    const defs = Array.isArray(exported) ? exported : exported.default;
    if (!Array.isArray(defs)) die('converter does not export an array');
    return defs;
}

function actionConverters(definition) {
    const out = [];
    for (const ext of definition.extend || []) {
        for (const converter of ext.fromZigbee || []) {
            if (['genMultistateInput', 'genOnOff', 'genLevelCtrl'].includes(converter.cluster)) {
                out.push(converter);
            }
        }
    }
    return out;
}

function invoke(converters, cluster, type, endpoint, data = {}) {
    const results = [];
    for (const converter of converters) {
        if (converter.cluster !== cluster) continue;
        if (!converter.type.includes(type)) continue;
        const result = converter.convert(
            {},
            {endpoint: {ID: endpoint}, type, data},
            () => {},
            {},
            {},
        );
        if (result && (result.action || Object.keys(result).some((k) => k.startsWith('action_')))) {
            results.push(result);
        }
    }
    if (results.length !== 1) {
        die(`${cluster}/${type}/EP${endpoint}: expected one action result, got ${JSON.stringify(results)}`);
    }
    return results[0];
}

function expect(actual, expected, label) {
    for (const [key, value] of Object.entries(expected)) {
        if (actual[key] !== value) {
            die(`${label}: expected ${key}=${value}, got ${JSON.stringify(actual)}`);
        }
    }
}

function main() {
    const path = process.argv[2];
    if (!path) die('usage: probe_bseed_ts0726_action_contract.js <target-overlay.js>');

    const defs = loadDefinitions(path);
    if (defs.length !== 1) die(`expected one target definition, got ${defs.length}`);
    const definition = defs[0];
    if (definition.model !== 'EC-GL86ZPCS31') die(`unexpected model ${definition.model}`);

    const converters = actionConverters(definition);
    const cases = [
        {
            label: 'left press report',
            actual: invoke(converters, 'genMultistateInput', 'attributeReport', 1, {presentValue: 1}),
            expected: {action: 'switch_0_press', action_switch_left: 'press'},
        },
        {
            label: 'middle long press report',
            actual: invoke(converters, 'genMultistateInput', 'attributeReport', 2, {presentValue: 2}),
            expected: {action: 'switch_1_long_press', action_switch_middle: 'long_press'},
        },
        {
            label: 'right toggle command',
            actual: invoke(converters, 'genOnOff', 'commandToggle', 3),
            expected: {action: 'switch_2_toggle', action_switch_right: 'toggle'},
        },
        {
            label: 'left dim up',
            actual: invoke(converters, 'genLevelCtrl', 'commandMoveWithOnOff', 1, {movemode: 0}),
            expected: {action: 'switch_0_brightness_move_up', action_switch_left: 'brightness_move_up'},
        },
        {
            label: 'middle dim down',
            actual: invoke(converters, 'genLevelCtrl', 'commandMove', 2, {movemode: 1}),
            expected: {action: 'switch_1_brightness_move_down', action_switch_middle: 'brightness_move_down'},
        },
        {
            label: 'right dim stop',
            actual: invoke(converters, 'genLevelCtrl', 'commandStopWithOnOff', 3, {}),
            expected: {action: 'switch_2_brightness_stop', action_switch_right: 'brightness_stop'},
        },
    ];

    for (const test of cases) expect(test.actual, test.expected, test.label);

    const genericActionExpose = (definition.extend || [])
        .flatMap((ext) => ext.exposes || [])
        .find((expose) => expose.name === 'action' && !expose.endpoint);
    if (!genericActionExpose) die('generic action expose missing');

    process.stdout.write(JSON.stringify({
        status: 'PASS',
        model: definition.model,
        cases: cases.map((test) => ({label: test.label, result: test.actual})),
        genericActionValues: genericActionExpose.values,
        configureCallbacks: (definition.extend || []).reduce(
            (count, ext) => count + (ext.configure || []).length, 0,
        ),
    }, null, 2) + '\n');
}

main();
