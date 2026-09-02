#!/usr/bin/env node
'use strict';

/**
 * Probe a generated Romasku/BSEED external converter against the ACTUAL
 * zigbee-herdsman-converters package installed on the machine running this
 * script. No Zigbee device is contacted: all device/endpoints are spies.
 *
 * Purpose:
 *   - prove the BSEED TS0726 canary definition cannot bind or configure
 *     reporting during automatic configure;
 *   - enumerate harmless attribute reads that the installed ZHC would issue;
 *   - dump the processed expose metadata users will see (labels, descriptions,
 *     endpoint, category and enum values);
 *   - record the exact installed ZHC version and module path.
 *
 * Example inside the Zigbee2MQTT container/app directory:
 *   node probe_bseed_ts0726_installed_zhc.js \
 *     /config/zigbee2mqtt/external_converters/switch_custom.js
 *
 * Exit status is non-zero if any bind/configureReporting/write mutation is
 * observed or if the expected UX/matcher contract is missing.
 */

const fs = require('fs');
const path = require('path');
const Module = require('module');

const TARGET_MODEL = 'EC-GL86ZPCS31';
const TARGET_MANUFACTURER = 'iedhxgyi';
const TARGET_ZB_MODEL = 'TS0726-3-BS';
const EXPECTED_PHYSICAL_VALUES = ['follow_state', 'always_on', 'always_off'];
const TARGET_SW_BUILD = '1.1.5-bseedv5';

function die(message) {
    process.stderr.write(`FAIL: ${message}\n`);
    process.exit(2);
}

function locateInstalledZhc() {
    const candidates = [
        process.cwd(),
        '/app',
        '/opt/zigbee2mqtt',
        '/usr/src/app',
        '/app/node_modules',
    ];

    let modernExtend;
    for (const candidate of candidates) {
        try {
            modernExtend = require.resolve('zigbee-herdsman-converters/lib/modernExtend', {paths: [candidate]});
            break;
        } catch (_) {
            // Try next candidate.
        }
    }
    if (!modernExtend) {
        die(`cannot resolve installed zigbee-herdsman-converters from: ${candidates.join(', ')}`);
    }

    let cursor = path.dirname(modernExtend);
    let packageRoot;
    while (cursor !== path.dirname(cursor)) {
        if (path.basename(cursor) === 'zigbee-herdsman-converters') {
            packageRoot = cursor;
            break;
        }
        cursor = path.dirname(cursor);
    }
    if (!packageRoot) die(`cannot derive zigbee-herdsman-converters package root from ${modernExtend}`);

    const packageJsonPath = path.join(packageRoot, 'package.json');
    const pkg = JSON.parse(fs.readFileSync(packageJsonPath, 'utf8'));
    const nodeModulesRoot = path.dirname(packageRoot);

    // The generated converter uses ordinary require('zigbee-herdsman-*'). If
    // it lives under /config, Node would not normally search /app/node_modules.
    // Add the discovered real node_modules root to global resolution before
    // requiring the converter. This changes only this probe process.
    process.env.NODE_PATH = [nodeModulesRoot, process.env.NODE_PATH].filter(Boolean).join(path.delimiter);
    Module._initPaths();

    return {
        version: pkg.version,
        packageRoot,
        packageJsonPath,
        modernExtend,
        nodeModulesRoot,
    };
}

function exposeSummary(definition) {
    const exposes = [];
    const add = (expose) => {
        exposes.push({
            name: expose.name ?? null,
            property: expose.property ?? null,
            label: expose.label ?? null,
            endpoint: expose.endpoint ?? null,
            category: expose.category ?? null,
            access: expose.access ?? null,
            values: Array.isArray(expose.values) ? [...expose.values] : null,
            description: expose.description ?? null,
        });
        for (const feature of expose.features || []) add(feature);
    };
    for (const extend of definition.extend || []) {
        for (const expose of extend.exposes || []) add(expose);
    }
    return exposes;
}

class Endpoint {
    constructor(id, events) {
        this.ID = id;
        this.deviceIeeeAddress = '0xa4c13843a9d40f85';
        this.ieeeAddr = '0xa4c13843a9d40f85';
        this.supportsInputCluster = () => true;
        this.supportsOutputCluster = () => true;
        this.getInputClusters = () => [];
        this.getOutputClusters = () => [];
        this.bind = async (...args) => events.push({endpoint: id, op: 'bind', args});
        this.unbind = async (...args) => events.push({endpoint: id, op: 'unbind', args});
        this.configureReporting = async (...args) =>
            events.push({endpoint: id, op: 'configureReporting', args});
        this.read = async (...args) => {
            events.push({endpoint: id, op: 'read', args});
            return {};
        };
        this.write = async (...args) => events.push({endpoint: id, op: 'write', args});
        this.command = async (...args) => events.push({endpoint: id, op: 'command', args});
    }
}

function makeEndpoint(id, events) {
    return new Endpoint(id, events);
}

async function executeConfigureSurface(definition) {
    const events = [];
    const endpoints = Array.from({length: 6}, (_, index) => makeEndpoint(index + 1, events));
    const device = {
        ieeeAddr: '0xa4c13843a9d40f85',
        endpoints,
        powerSource: 'Mains (single phase)',
        getEndpoint: (id) => endpoints.find((endpoint) => endpoint.ID === id),
        addCustomCluster: (...args) => events.push({endpoint: null, op: 'addCustomCluster', args}),
        save: () => events.push({endpoint: null, op: 'deviceSave', args: []}),
    };
    const coordinator = makeEndpoint(1, events);
    coordinator.deviceIeeeAddress = '0x00124b002d12b1fd';

    const endpointExtend = (definition.extend || []).find((extend) => typeof extend.endpoint === 'function');
    if (!endpointExtend) die('target definition has no deviceEndpoints endpoint mapper');

    const processedDefinition = {
        ...definition,
        endpoint: endpointExtend.endpoint,
    };

    const callbacks = [];
    if (typeof definition.configure === 'function') {
        callbacks.push({source: 'definition.configure', fn: definition.configure});
    }
    for (let extendIndex = 0; extendIndex < (definition.extend || []).length; extendIndex += 1) {
        const extend = definition.extend[extendIndex];
        for (let configureIndex = 0; configureIndex < (extend.configure || []).length; configureIndex += 1) {
            const fn = extend.configure[configureIndex];
            if (typeof fn === 'function') {
                callbacks.push({source: `extend[${extendIndex}].configure[${configureIndex}]`, fn});
            }
        }
    }

    for (const callback of callbacks) {
        const before = events.length;
        try {
            await callback.fn(device, coordinator, processedDefinition);
        } catch (error) {
            die(`${callback.source} threw under spies: ${error && error.stack ? error.stack : error}`);
        }
        for (let i = before; i < events.length; i += 1) events[i].source = callback.source;
    }

    return {callbacks: callbacks.map((item) => item.source), events};
}

function verifyContract(definition, exposes, execution) {
    if (!Array.isArray(definition.fingerprint)) die('target definition has no fingerprint array');
    const fpMatches = definition.fingerprint.filter(
        (fp) => fp.manufacturerName === TARGET_MANUFACTURER && fp.modelID === TARGET_ZB_MODEL,
    );
    if (fpMatches.length !== 1) die(`expected exactly one target fingerprint, got ${fpMatches.length}`);
    if (fpMatches[0].softwareBuildID !== TARGET_SW_BUILD || (fpMatches[0].priority ?? 0) !== 100) {
        die(`target fingerprint must require ${TARGET_SW_BUILD} at priority 100`);
    }

    if ((definition.zigbeeModel || []).includes(TARGET_ZB_MODEL)) {
        die('ambiguous bare TS0726-3-BS zigbeeModel matcher remains on target');
    }

    const expectedPhysical = [
        {property: 'relay_left_physical_mode', label: 'Left — Mains power'},
        {property: 'relay_middle_physical_mode', label: 'Middle — Mains power'},
        {property: 'relay_right_physical_mode', label: 'Right — Mains power'},
    ];
    for (const expected of expectedPhysical) {
        const expose = exposes.find((item) => item.property === expected.property);
        if (!expose) die(`missing processed expose ${expected.property}`);
        if (expose.label !== expected.label) {
            die(`${expected.property}: expected label ${JSON.stringify(expected.label)}, got ${JSON.stringify(expose.label)}`);
        }
        if (expose.category !== 'config') {
            die(`${expected.property}: expected category=config, got ${JSON.stringify(expose.category)}`);
        }
        if (JSON.stringify(expose.values) !== JSON.stringify(EXPECTED_PHYSICAL_VALUES)) {
            die(`${expected.property}: unexpected enum values ${JSON.stringify(expose.values)}`);
        }
        const description = expose.description || '';
        for (const marker of ['smart bulbs', 'Always on', 'affect power immediately']) {
            if (!description.includes(marker)) die(`${expected.property}: description missing ${JSON.stringify(marker)}`);
        }
    }

    const deviceConfig = exposes.find((item) => item.name === 'device_config');
    if (!deviceConfig) die('missing processed device_config expose');
    if (deviceConfig.label !== 'Advanced — Hardware configuration') {
        die(`device_config label mismatch: ${JSON.stringify(deviceConfig.label)}`);
    }
    if (deviceConfig.endpoint !== 'advanced' || deviceConfig.property !== 'device_config') {
        die(`device_config must use dedicated advanced endpoint while preserving property: ${JSON.stringify(deviceConfig)}`);
    }
    if (deviceConfig.category !== 'diagnostic') {
        die(`device_config must remain advanced/diagnostic, got category=${JSON.stringify(deviceConfig.category)}`);
    }
    if ((deviceConfig.access & 2) === 0) {
        die(`device_config must be editable in v5, got access=${JSON.stringify(deviceConfig.access)}`);
    }
    const configDescription = deviceConfig.description || '';
    for (const marker of ['locked by default', 'Advanced — Enable editing', 'checks identity', 'all chunks and CRC', 'recovery firmware']) {
        if (!configDescription.includes(marker)) die(`device_config description missing ${JSON.stringify(marker)}`);
    }

    const unlock = exposes.find((item) => item.name === 'device_config_unlock');
    if (!unlock) die('missing advanced editor unlock button expose');
    if (unlock.label !== 'Advanced — Enable editing' || unlock.endpoint !== 'advanced') {
        die(`unlock button placement/label mismatch: ${JSON.stringify(unlock)}`);
    }
    if (unlock.access !== 2 || JSON.stringify(unlock.values) !== JSON.stringify(['enable_editing'])) {
        die(`unlock must be a SET-only one-value enum button: ${JSON.stringify(unlock)}`);
    }
    for (const marker of ['60 seconds', 'changes nothing', 'consumes the unlock']) {
        if (!(unlock.description || '').includes(marker)) {
            die(`unlock description missing ${JSON.stringify(marker)}`);
        }
    }

    const expectedIndicatorValues = [
        'logical_state',
        'inverse_logical_state',
        'manual',
        'physical_output',
        'binding_status',
    ];
    const channelNames = {
        relay_left: 'Left',
        relay_middle: 'Middle',
        relay_right: 'Right',
    };
    for (const [endpoint, channel] of Object.entries(channelNames)) {
        const indicator = exposes.find(
            (item) => item.endpoint === endpoint && item.label === `${channel} — LED shows`,
        );
        if (!indicator) die(`missing channel-scoped LED source expose for ${endpoint}`);
        if (JSON.stringify(indicator.values) !== JSON.stringify(expectedIndicatorValues)) {
            die(`${endpoint}: unexpected indicator values ${JSON.stringify(indicator.values)}`);
        }
        const intent = exposes.find(
            (item) => item.endpoint === endpoint && item.label === `${channel} — Bound light (tracked)`,
        );
        if (!intent) die(`missing channel-scoped binding intent expose for ${endpoint}`);
        if (!(intent.description || '').includes('not remote-state confirmation')) {
            die(`${endpoint}: binding intent warning missing`);
        }

        const logical = exposes.find(
            (item) => item.endpoint === endpoint && item.label === `${channel} — Logical state`,
        );
        if (!logical) die(`missing channel-scoped logical state expose for ${endpoint}`);
        if (!(logical.description || '').includes('not the same as mains power')) {
            die(`${endpoint}: logical-state description does not distinguish mains`);
        }
    }

    // deviceAddCustomCluster is local decoder metadata registration, not a
    // Zigbee mutation. It is expected exactly once for the v5 chunk protocol.
    const clusterRegistrations = execution.events.filter((event) => event.op === 'addCustomCluster');
    if (clusterRegistrations.length !== 1) {
        die(`expected one local custom-cluster registration, got ${clusterRegistrations.length}`);
    }
    if (clusterRegistrations[0].args?.[0] !== 'genBasic' ||
        clusterRegistrations[0].args?.[1]?.ID !== 0x0000) {
        die(`v5 transport must extend built-in genBasic instead of shadowing ID 0: ${JSON.stringify(clusterRegistrations[0])}`);
    }
    const addedCommands = clusterRegistrations[0].args?.[1]?.commands || {};
    if (addedCommands.deviceConfigStage?.ID !== 0xf0 || addedCommands.deviceConfigCommit?.ID !== 0xf1) {
        die(`genBasic extension is missing v5 transport commands: ${JSON.stringify(addedCommands)}`);
    }
    const mutations = execution.events.filter((event) =>
        ['bind', 'unbind', 'configureReporting', 'write', 'command'].includes(event.op),
    );
    if (mutations.length) {
        die(`configure surface attempted Zigbee mutation(s): ${JSON.stringify(mutations, null, 2)}`);
    }

    return {
        reads: execution.events.filter((event) => event.op === 'read'),
        deviceSaves: execution.events.filter((event) => event.op === 'deviceSave'),
        clusterRegistrations,
    };
}

async function main() {
    const converterPath = path.resolve(process.argv[2] || 'zigbee2mqtt/converters/switch_custom.js');
    if (!fs.existsSync(converterPath)) die(`converter file does not exist: ${converterPath}`);

    const zhc = locateInstalledZhc();
    delete require.cache[require.resolve(converterPath)];
    const exported = require(converterPath);
    const definitions = Array.isArray(exported) ? exported : exported.default;
    if (!Array.isArray(definitions)) die('converter did not export a definition array');

    const candidates = definitions.filter((definition) => definition.model === TARGET_MODEL);
    if (candidates.length !== 1) die(`expected exactly one ${TARGET_MODEL} definition, got ${candidates.length}`);
    const definition = candidates[0];

    const exposes = exposeSummary(definition);
    const execution = await executeConfigureSurface(definition);
    const verified = verifyContract(definition, exposes, execution);

    const interestingExposes = exposes.filter((item) =>
        item.name === 'device_config' ||
        item.name === 'device_config_unlock' ||
        item.label?.includes('Logical state') ||
        item.label?.includes('State after power-up') ||
        item.property?.includes('physical_mode') ||
        item.label?.includes('Update local state') ||
        item.label?.includes('Control bound light') ||
        item.label?.includes('Button type') ||
        item.label?.includes('Direct-binding command') ||
        item.label?.includes('LED shows') ||
        item.label?.includes('Bound light (tracked)'),
    );

    const output = {
        status: 'PASS',
        converterPath,
        converterBytes: fs.statSync(converterPath).size,
        installedZhc: zhc,
        target: {
            model: definition.model,
            vendor: definition.vendor,
            description: definition.description,
            fingerprint: definition.fingerprint,
            zigbeeModel: definition.zigbeeModel || [],
        },
        configure: {
            callbacks: execution.callbacks,
            mutationCount: 0,
            localCustomClusterRegistrationCount: verified.clusterRegistrations.length,
            bindCount: 0,
            configureReportingCount: 0,
            writeCount: 0,
            commandCount: 0,
            readCount: verified.reads.length,
            reads: verified.reads,
            deviceSaveCount: verified.deviceSaves.length,
        },
        exposes: interestingExposes,
    };

    process.stdout.write(`${JSON.stringify(output, null, 2)}\n`);
}

main().catch((error) => die(error && error.stack ? error.stack : String(error)));
