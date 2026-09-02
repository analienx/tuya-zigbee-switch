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

    const expectedProperties = [
        'relay_left_physical_mode',
        'relay_middle_physical_mode',
        'relay_right_physical_mode',
    ];
    for (const property of expectedProperties) {
        const expose = exposes.find((item) => item.property === property);
        if (!expose) die(`missing processed expose ${property}`);
        if (expose.label !== 'Physical relay behavior') {
            die(`${property}: expected label 'Physical relay behavior', got ${JSON.stringify(expose.label)}`);
        }
        if (expose.category !== 'config') die(`${property}: expected category=config, got ${JSON.stringify(expose.category)}`);
        if (JSON.stringify(expose.values) !== JSON.stringify(EXPECTED_PHYSICAL_VALUES)) {
            die(`${property}: unexpected enum values ${JSON.stringify(expose.values)}`);
        }
        const description = expose.description || '';
        for (const marker of ['smart bulbs', 'Always on', 'immediately']) {
            if (!description.includes(marker)) die(`${property}: description missing ${JSON.stringify(marker)}`);
        }
    }

    const deviceConfig = exposes.find((item) => item.name === 'device_config');
    if (!deviceConfig) die('missing processed device_config expose');
    if (deviceConfig.label !== 'Advanced hardware configuration') {
        die(`device_config label mismatch: ${JSON.stringify(deviceConfig.label)}`);
    }
    if (deviceConfig.category !== 'diagnostic') {
        die(`device_config must remain advanced/diagnostic, got category=${JSON.stringify(deviceConfig.category)}`);
    }
    if ((deviceConfig.access & 2) === 0) {
        die(`device_config must be editable in v5, got access=${JSON.stringify(deviceConfig.access)}`);
    }
    const configDescription = deviceConfig.description || '';
    for (const marker of ['transactional chunk', 'recovery firmware']) {
        if (!configDescription.includes(marker)) die(`device_config description missing ${JSON.stringify(marker)}`);
    }

    const expectedIndicatorValues = [
        'logical_state',
        'inverse_logical_state',
        'manual',
        'physical_output',
        'binding_status',
    ];
    for (const endpoint of ['relay_left', 'relay_middle', 'relay_right']) {
        const indicator = exposes.find(
            (item) => item.endpoint === endpoint && item.label === 'Indicator LED source',
        );
        if (!indicator) die(`missing Indicator LED source expose for ${endpoint}`);
        if (JSON.stringify(indicator.values) !== JSON.stringify(expectedIndicatorValues)) {
            die(`${endpoint}: unexpected indicator values ${JSON.stringify(indicator.values)}`);
        }
        const intent = exposes.find(
            (item) => item.endpoint === endpoint && item.label === 'Bound-light intent state',
        );
        if (!intent) die(`missing bound-light intent expose for ${endpoint}`);
        if (!(intent.description || '').includes('not proof')) {
            die(`${endpoint}: binding intent warning missing`);
        }
    }

    for (const endpoint of ['relay_left', 'relay_middle', 'relay_right']) {
        const logical = exposes.find(
            (item) => item.endpoint === endpoint && item.label === 'Logical relay state',
        );
        if (!logical) die(`missing Logical relay state expose for ${endpoint}`);
        if (!(logical.description || '').includes('does not necessarily switch mains power')) {
            die(`${endpoint}: logical-state description does not distinguish mains`);
        }
    }

    // deviceAddCustomCluster is local decoder metadata registration, not a
    // Zigbee mutation. It is expected exactly once for the v5 chunk protocol.
    const clusterRegistrations = execution.events.filter((event) => event.op === 'addCustomCluster');
    if (clusterRegistrations.length !== 1) {
        die(`expected one local custom-cluster registration, got ${clusterRegistrations.length}`);
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
        item.label === 'Logical relay state' ||
        item.label === 'Logical state after power-up' ||
        item.property?.includes('physical_mode') ||
        item.label === 'Local relay trigger' ||
        item.label === 'Bound-device trigger' ||
        item.label === 'Button type' ||
        item.label === 'Button command behavior' ||
        item.label === 'Indicator LED source' ||
        item.label === 'Bound-light intent state',
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
