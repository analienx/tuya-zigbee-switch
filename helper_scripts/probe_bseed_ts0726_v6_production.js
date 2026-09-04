'use strict';

const assert = require('assert');
const path = require('path');

const converterPath = process.argv[2];
if (!converterPath) {
    throw new Error('usage: node probe_bseed_ts0726_v6_production.js <production-converter.js>');
}

const definitions = require(path.resolve(converterPath));
assert(Array.isArray(definitions), 'converter must export an array');
assert.strictEqual(definitions.length, 1, 'production converter must export exactly one definition');
const definition = definitions[0];
assert.strictEqual(definition.model, 'EC-GL86ZPCS31');
assert(!definition.zigbeeModel, 'bare zigbeeModel fallback is forbidden');
assert.strictEqual(definition.fingerprint.length, 2, 'expected V5 recovery + V6 production fingerprints');
assert(definition.fingerprint.some((f) => f.softwareBuildID === '1.1.5-bseedv5'));
assert(definition.fingerprint.some((f) => f.softwareBuildID === '1.1.6-bseedv6'));

const findExtend = (property) => {
    const result = definition.extend.find((extend) =>
        (extend.exposes || []).some((expose) => expose?.property === property || expose?.name === property)
    );
    assert(result, `missing production extend for ${property}`);
    return result;
};

const calls = [];
const boundModes = new Map([[1, 3], [2, 3], [3, 3]]);
const acceptBoundModeWrites = new Map([[1, true], [2, true], [3, true]]);
const endpoint = (id) => ({
    ID: id,
    read: async (cluster, attrs, options) => {
        calls.push({op: 'read', id, cluster, attrs, options});
        if (id === 1 && cluster === 'genBasic' && attrs.includes('swBuildId')) {
            return {swBuildId: '1.1.6-bseedv6'};
        }
        if (cluster === 'genOnOffSwitchCfg' && attrs.includes(0xff05)) {
            return {65285: boundModes.get(id)};
        }
        return {};
    },
    write: async (cluster, payload, options) => {
        calls.push({op: 'write', id, cluster, payload, options});
        if (cluster === 'genOnOffSwitchCfg' && Object.prototype.hasOwnProperty.call(payload, 65285)) {
            if (acceptBoundModeWrites.get(id)) {
                boundModes.set(id, payload[65285].value);
            }
        }
    },
    command: async (cluster, command, payload, options) => {
        calls.push({op: 'command', id, cluster, command, payload, options});
    },
});
const endpoints = new Map([1, 2, 3, 4, 5, 6].map((id) => [id, endpoint(id)]));
const logMessages = [];
const meta = {
    device: {ieeeAddr: '0xa4c13843a9d40f85', getEndpoint: (id) => endpoints.get(id)},
    logger: {error: (message) => logMessages.push(message)},
};

(async () => {
    for (const [property, id] of [
        ['switch_left_binded_mode', 1],
        ['switch_middle_binded_mode', 2],
        ['switch_right_binded_mode', 3],
    ]) {
        const extend = findExtend(property);
        const tz = extend.toZigbee.find((item) => item.key?.includes(property));
        assert(tz?.convertSet && tz?.convertGet, `${property} must support SET+GET`);

        calls.length = 0;
        const setResult = await tz.convertSet(null, property, 'Never (disabled)', meta);
        assert.strictEqual(calls.length, 2, `${property} disabled SET must write then read back`);
        assert.strictEqual(calls[0].op, 'write');
        assert.strictEqual(calls[0].id, id);
        assert.strictEqual(calls[0].cluster, 'genOnOffSwitchCfg');
        assert.deepStrictEqual(calls[0].payload, {65285: {value: 0, type: 0x30}});
        assert.strictEqual(calls[1].op, 'read');
        assert.strictEqual(calls[1].id, id);
        assert.strictEqual(calls[1].cluster, 'genOnOffSwitchCfg');
        assert.deepStrictEqual(calls[1].attrs, [0xff05]);
        assert.deepStrictEqual(setResult, {state: {[property]: 'Never (disabled)'}});

        calls.length = 0;
        await tz.convertGet(null, property, meta);
        assert.strictEqual(calls.length, 1);
        assert.strictEqual(calls[0].op, 'read');
        assert.strictEqual(calls[0].id, id);
        assert.strictEqual(calls[0].cluster, 'genOnOffSwitchCfg');
        assert.deepStrictEqual(calls[0].attrs, [0xff05]);

        const fz = extend.fromZigbee[0];
        assert.deepStrictEqual(
            fz.convert(null, {endpoint: {ID: id}, data: {65285: 0}}),
            {[property]: 'Never (disabled)'},
        );
    }

    // A SET that the device ACKs but does not retain must publish device truth,
    // not the requested optimistic value. This models the live V6 failure seen
    // on RIGHT where 0xff05=0 reads back as 3.
    const rightProperty = 'switch_right_binded_mode';
    const rightExtend = findExtend(rightProperty);
    const rightTz = rightExtend.toZigbee.find((item) => item.key?.includes(rightProperty));
    boundModes.set(3, 3);
    acceptBoundModeWrites.set(3, false);
    calls.length = 0;
    logMessages.length = 0;
    const mismatchResult = await rightTz.convertSet(null, rightProperty, 'Never (disabled)', meta);
    assert.deepStrictEqual(mismatchResult, {state: {[rightProperty]: 'Short press'}});
    assert.strictEqual(calls.length, 2, 'mismatch path must still be write + authoritative readback');
    assert.strictEqual(calls[0].op, 'write');
    assert.strictEqual(calls[1].op, 'read');
    assert(logMessages.some((message) => message.includes('publishing device truth')));
    acceptBoundModeWrites.set(3, true);

    // Ensure the production wrapper did not disturb the hardened V6 binding-command path.
    const action = findExtend('switch_right_action_mode');
    const actionTz = action.toZigbee.find((item) => item.key?.includes('switch_right_action_mode'));
    calls.length = 0;
    await actionTz.convertSet(null, 'switch_right_action_mode', 'Toggle', meta);
    assert(calls.length >= 2, 'binding-command SET must perform identity read then action write');
    assert.deepStrictEqual(
        {op: calls[0].op, id: calls[0].id, cluster: calls[0].cluster, attrs: calls[0].attrs},
        {op: 'read', id: 1, cluster: 'genBasic', attrs: ['swBuildId']},
    );
    const actionWrite = calls.find((call) => call.op === 'write');
    assert(actionWrite, 'missing V6 direct-binding action write');
    assert.strictEqual(actionWrite.id, 3);
    assert.strictEqual(actionWrite.cluster, 'genOnOffSwitchCfg');
    assert.deepStrictEqual(actionWrite.payload, {65286: {value: 2, type: 0x30}});

    console.log(JSON.stringify({
        status: 'PASS',
        definitionCount: definitions.length,
        v5RecoveryFingerprint: true,
        v6ProductionFingerprint: true,
        disabledBoundControl: '0xff05=0',
        endpointsPinned: [1, 2, 3],
        boundModeSetReadbackVerified: true,
        rejectedWritePublishesDeviceTruth: true,
        rightPureRelayRepresentable: true,
        directBindingIdentityReadFirst: true,
        directBindingV6Attribute: '0xff06',
    }, null, 2));
})().catch((error) => {
    console.error(error.stack || error);
    process.exit(1);
});
