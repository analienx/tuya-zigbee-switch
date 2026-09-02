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
    withCategory(category) { this.category = category; return this; },
    withDescription(description) { this.description = description; return this; },
    withLabel(label) { this.label = label; return this; },
    withProperty(property) { this.property = property; return this; },
});
const emptyExtend = () => ({isModernExtend: true, exposes: [], fromZigbee: [], toZigbee: [], configure: []});
const textExtend = (args) => ({
    isModernExtend: true,
    exposes: [makeExpose(args.name).withEndpoint(args.endpointName).withDescription(args.description)],
    fromZigbee: [],
    toZigbee: [{key: [args.name]}],
    configure: [],
});

Module._load = function(request, parent, isMain) {
    if (request === 'zigbee-herdsman-converters/lib/modernExtend') {
        return {
            binary: emptyExtend,
            deviceAddCustomCluster: emptyExtend,
            deviceEndpoints: (args) => ({...emptyExtend(), endpoint: (device) => args.endpoints}),
            enumLookup: emptyExtend,
            numeric: emptyExtend,
            onOff: emptyExtend,
            text: textExtend,
        };
    }
    if (request === 'zigbee-herdsman-converters/lib/exposes') {
        return {
            presets: {action: (values) => ({...makeExpose('action'), values})},
            access: {STATE: 1, SET: 2, GET: 4, ALL: 7},
            enum: (name, access, values) => ({...makeExpose(name, access), values}),
            text: (name, access) => makeExpose(name, access),
        };
    }
    if (request === 'zigbee-herdsman') {
        return {Zcl: {BuffaloZclDataType: {LIST_UINT8: 0x1001}}};
    }
    return originalLoad.call(this, request, parent, isMain);
};

function die(message) {
    process.stderr.write(`FAIL: ${message}\n`);
    process.exit(2);
}

function crc16(bytes) {
    let crc = 0xffff;
    for (const byte of bytes) {
        crc ^= byte << 8;
        for (let bit = 0; bit < 8; bit++) {
            crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xffff : (crc << 1) & 0xffff;
        }
    }
    return crc;
}

async function main() {
    let fakeNow = 1_000_000;
    Date.now = () => fakeNow;

    const input = process.argv[2];
    if (!input) die('usage: probe_bseed_ts0726_v5_config_transport.js <overlay-v5.js>');

    const target = path.resolve(input);
    const exported = require(target);
    const defs = Array.isArray(exported) ? exported : exported.default;
    if (!Array.isArray(defs) || defs.length !== 1) die('expected one definition');

    const allConverters = defs[0].extend.flatMap((ext) => ext.toZigbee || []);
    const converters = allConverters.filter((converter) => (converter.key || []).includes('device_config'));
    const unlockers = allConverters.filter((converter) => (converter.key || []).includes('device_config_unlock'));
    if (converters.length !== 1) die(`expected one device_config converter, got ${converters.length}`);
    if (unlockers.length !== 1) die(`expected one device_config unlock converter, got ${unlockers.length}`);
    const converter = converters[0];
    const unlocker = unlockers[0];

    const events = [];
    const endpoint = {
        async command(...args) { events.push({op: 'command', args}); return {}; },
        async read(...args) { events.push({op: 'read', args}); return {}; },
        async write(...args) { events.push({op: 'write', args}); return {}; },
    };
    const meta = {
        device: {
            ieeeAddr: '0xa4c13843a9d40f85',
            getEndpoint(id) { if (id !== 1) die(`unexpected endpoint ${id}`); return endpoint; },
        },
    };

    const config = 'iedhxgyi;TS0726-3-BS;LC4;SB1u;RC2;IC0;SB7u;RC3;ID7;SB4u;RD2;IB5;D50;SLP;M;';
    const source = Buffer.from(config, 'ascii');
    if (source.length <= 64) die('fixture must exercise formerly oversized config');

    let lockedRejected = false;
    try {
        await converter.convertSet(endpoint, 'device_config', config, meta);
    } catch (error) {
        lockedRejected = String(error).includes('locked');
    }
    if (!lockedRejected) die('valid config was not rejected while editor was locked');
    if (events.length !== 0) die('locked SET emitted Zigbee traffic');

    await unlocker.convertSet(endpoint, 'device_config_unlock', 'enable_editing', meta);
    if (events.length !== 0) die('unlock button emitted Zigbee traffic');

    const result = await converter.convertSet(endpoint, 'device_config', config, meta);
    if (result?.state?.device_config !== config) die('SET did not return exact public property value');

    const afterFirstSave = events.length;
    let consumedRejected = false;
    try {
        await converter.convertSet(endpoint, 'device_config', config, meta);
    } catch (error) {
        consumedRejected = String(error).includes('locked');
    }
    if (!consumedRejected) die('one-shot unlock was not consumed by the valid save');
    if (events.length !== afterFirstSave) die('second locked SET emitted Zigbee traffic');

    await unlocker.convertSet(endpoint, 'device_config_unlock', 'enable_editing', meta);
    const beforeExpiry = events.length;
    fakeNow += 60_001;
    let expiryRejected = false;
    try {
        await converter.convertSet(endpoint, 'device_config', config, meta);
    } catch (error) {
        expiryRejected = String(error).includes('locked');
    }
    if (!expiryRejected) die('60-second unlock expiry was not enforced');
    if (events.length !== beforeExpiry) die('expired unlock emitted Zigbee traffic');

    const writes = events.filter((event) => event.op === 'write');
    if (writes.length !== 0) die(`direct attribute write attempted: ${JSON.stringify(writes)}`);

    const commands = events.filter((event) => event.op === 'command');
    const stages = commands.filter((event) => event.args[1] === 'deviceConfigStage');
    const commits = commands.filter((event) => event.args[1] === 'deviceConfigCommit');
    if (commits.length !== 1) die(`expected one commit, got ${commits.length}`);
    if (stages.length !== Math.ceil(source.length / 24)) {
        die(`wrong stage count ${stages.length}`);
    }

    let transaction = null;
    const reconstructed = Buffer.alloc(source.length);
    const coverage = new Array(source.length).fill(false);
    for (const event of stages) {
        const [cluster, command, payload] = event.args;
        if (cluster !== 'genBasic' || command !== 'deviceConfigStage') {
            die(`unexpected stage route: ${JSON.stringify(event.args)}`);
        }
        const data = payload.data;
        const [tx, offset, length, ...chunk] = data;
        if (length !== chunk.length || length < 1 || length > 24) die('invalid stage chunk length');
        if (data.length > 27) die('stage frame exceeded bounded application payload');
        if (transaction === null) transaction = tx;
        if (transaction !== tx) die('transaction changed between chunks');
        Buffer.from(chunk).copy(reconstructed, offset);
        for (let i = offset; i < offset + length; i++) coverage[i] = true;
    }
    if (coverage.some((value) => !value)) die('chunk coverage incomplete');
    if (!reconstructed.equals(source)) die('reconstructed config differs from source');

    const [commitCluster, commitCommand, commitPayload] = commits[0].args;
    if (commitCluster !== 'genBasic' || commitCommand !== 'deviceConfigCommit') die('wrong commit route');
    const [commitTx, total, crcLo, crcHi] = commitPayload.data;
    if (commitTx !== transaction || total !== source.length) die('commit metadata mismatch');
    const actualCrc = crcLo | (crcHi << 8);
    const expectedCrc = crc16(source);
    if (actualCrc !== expectedCrc) die(`CRC mismatch actual=${actualCrc} expected=${expectedCrc}`);

    await unlocker.convertSet(endpoint, 'device_config_unlock', 'enable_editing', meta);
    const beforeInvalid = events.length;
    for (const invalid of [
        config.slice(0, -1),
        'other;TS0726-3-BS;LC4;SB1u;RC2;IC0;SB7u;RC3;ID7;SB4u;RD2;IB5;M;',
        'iedhxgyi;TS0726-3-BS;LC4;SB1u;RC2;IC0;SB7u;RC3;ID7;SB4u;RD2;M;',
        'iedhxgyi;TS0726-3-BS;LC4;SB1u;RC2;IC0;SB7u;RC3;ID7;SB4u;RD2;IC0;M;',
        'iedhxgyi;TS0726-3-BS;LC4;SB1u;RC2;IC0;SB7u;RC3;ID7;SB4u;RD2;IB5;XA0B0u;M;',
        'iedhxgyi;TS0726-3-BS;RC2;\u0000BAD;',
        123,
    ]) {
        let rejected = false;
        try {
            await converter.convertSet(endpoint, 'device_config', invalid, meta);
        } catch (_) {
            rejected = true;
        }
        if (!rejected) die(`invalid value was accepted: ${JSON.stringify(invalid)}`);
    }
    if (events.length !== beforeInvalid) die('invalid input emitted transport traffic');

    await converter.convertGet(endpoint, 'device_config', meta);
    const reads = events.filter((event) => event.op === 'read');
    if (reads.length !== 1) die(`expected one GET read, got ${reads.length}`);
    if (reads[0].args[0] !== 'genBasic' || reads[0].args[1][0] !== 0xff00) die('GET did not read Basic 0xff00');

    process.stdout.write(JSON.stringify({
        status: 'PASS',
        sourceBytes: source.length,
        stageCount: stages.length,
        maxChunkBytes: Math.max(...stages.map((event) => event.args[2].data[2])),
        directWriteCount: writes.length,
        commit: {transaction, total, crc16: actualCrc},
        exactRoundTrip: true,
        lockedSetRejectedWithoutTraffic: true,
        unlockButtonEmitsNoZigbeeTraffic: true,
        unlockConsumedAfterOneValidSave: true,
        unlockExpiresAfter60SecondsWithoutTraffic: true,
        safeAdvancedOptionsRoundTrip: config.includes('D50;SLP;'),
        invalidBoardLayoutsRejectedWithoutTraffic: true,
        getReadsBasicDeviceConfig: true,
    }, null, 2) + '\n');
}

main().catch((error) => die(error && error.stack ? error.stack : String(error)));
