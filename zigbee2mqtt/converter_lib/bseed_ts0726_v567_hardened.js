'use strict';

/**
 * V7 compatibility overlay for the frozen, proven V5/V6 hardened BSEED base.
 *
 * Keep bseed_ts0726_v56_hardened.js byte-stable. This overlay changes only
 * firmware identity/transport routing required by the V7 canary:
 *   - add the exact 1.1.7-bseedv7 fingerprint;
 *   - use the same custom 0xff06 direct-binding-command transport as V6.
 *
 * No bindings, groups, reporting, relay state or device configuration are
 * created or mutated by this overlay.
 */

const definitions = require('./bseed_ts0726_v56_hardened.js');
const exposes = require('zigbee-herdsman-converters/lib/exposes');
const e = exposes.presets;
const ea = exposes.access;

const V5_SW_BUILD = '1.1.5-bseedv5';
const V6_SW_BUILD = '1.1.6-bseedv6';
const V7_SW_BUILD = '1.1.7-bseedv7';

const CHANNELS = [
    {name: 'switch_left_action_mode', endpointName: 'switch_left', endpointId: 1, label: 'Left'},
    {name: 'switch_middle_action_mode', endpointName: 'switch_middle', endpointId: 2, label: 'Middle'},
    {name: 'switch_right_action_mode', endpointName: 'switch_right', endpointId: 3, label: 'Right'},
];

const LOOKUP = {
    'On then off': 0,
    'Off then on': 1,
    'Toggle': 2,
    'Match local state': 3,
    'Opposite local state': 4,
};
const REVERSE = new Map(Object.entries(LOOKUP).map(([key, value]) => [String(value), key]));

if (!Array.isArray(definitions) || definitions.length !== 1) {
    throw new Error('BSEED V7 overlay expected exactly one hardened base definition');
}

const definition = definitions[0];
if (!Array.isArray(definition.fingerprint)) {
    throw new Error('BSEED V7 overlay expected a fingerprint array');
}

if (!definition.fingerprint.some((fp) => fp.softwareBuildID === V7_SW_BUILD)) {
    definition.fingerprint.push({
        manufacturerName: 'iedhxgyi',
        modelID: 'TS0726-3-BS',
        softwareBuildID: V7_SW_BUILD,
        priority: 100,
    });
}

const rawAttributeValue = (msg) =>
    msg.data?.[0xff06] ?? msg.data?.['65286'] ?? msg.data?.switchActions ?? msg.data?.[0x0010] ?? msg.data?.['16'];

const directBindingTransport = async (meta) => {
    const endpoint = meta?.device?.getEndpoint?.(1);
    if (!endpoint) throw new Error('Direct-binding command cannot read firmware identity: EP1 unavailable');

    let result;
    try {
        result = await endpoint.read('genBasic', ['swBuildId'], {timeout: 30_000});
    } catch (error) {
        throw new Error(
            'Direct-binding command cannot verify firmware identity; retry after the device responds to Basic/swBuildId',
        );
    }

    const swBuild = result?.swBuildId;
    if (swBuild === V5_SW_BUILD) {
        return {attribute: 'switchActions', max: 2};
    }
    if (swBuild === V6_SW_BUILD || swBuild === V7_SW_BUILD) {
        return {attribute: {ID: 0xff06, type: 0x30}, max: 4};
    }
    throw new Error(`Direct-binding command is not enabled for firmware ${JSON.stringify(swBuild)}`);
};

const v567ButtonCommandBehavior = ({name, endpointName, endpointId, label}) => ({
    isModernExtend: true,
    exposes: [
        e.enum(name, ea.ALL, Object.keys(LOOKUP))
            .withEndpoint(endpointName)
            .withProperty(name)
            .withLabel(`${label} — Direct-binding command`)
            .withDescription(
                'Chooses the On/Off command sent directly to bound lights. Toggle is the simplest choice and does not depend on local state. ' +
                'Match local state sends explicit On/Off to match this channel; Opposite local state sends the inverse. ' +
                'On then off and Off then on are mainly useful with maintained rocker inputs.'
            )
            .withCategory('config'),
    ],
    fromZigbee: [{
        cluster: 'genOnOffSwitchCfg',
        type: ['attributeReport', 'readResponse'],
        convert: (model, msg) => {
            if (msg.endpoint.ID !== endpointId) return;
            const value = REVERSE.get(String(rawAttributeValue(msg)));
            if (value !== undefined) return {[name]: value};
        },
    }],
    toZigbee: [{
        key: [name],
        convertSet: async (entity, key, value, meta) => {
            if (!Object.prototype.hasOwnProperty.call(LOOKUP, value)) {
                throw new Error(`${name}: unsupported value ${JSON.stringify(value)}`);
            }
            const raw = LOOKUP[value];
            const transport = await directBindingTransport(meta);
            if (raw > transport.max) {
                throw new Error(`${name}: ${value} requires BSEED V6 or V7 firmware`);
            }
            const endpoint = meta?.device?.getEndpoint?.(endpointId);
            if (!endpoint) throw new Error(`${name}: EP${endpointId} is unavailable`);
            const payload = typeof transport.attribute === 'string'
                ? {[transport.attribute]: raw}
                : {[transport.attribute.ID]: {value: raw, type: transport.attribute.type}};
            await endpoint.write('genOnOffSwitchCfg', payload);
            return {state: {[key]: value}};
        },
        convertGet: async (entity, key, meta) => {
            const transport = await directBindingTransport(meta);
            const endpoint = meta?.device?.getEndpoint?.(endpointId);
            if (!endpoint) throw new Error(`${name}: EP${endpointId} is unavailable`);
            const attributeKey = typeof transport.attribute === 'string'
                ? transport.attribute
                : transport.attribute.ID;
            await endpoint.read('genOnOffSwitchCfg', [attributeKey]);
        },
    }],
    configure: [],
});

for (const channel of CHANNELS) {
    const index = definition.extend.findIndex((extend) =>
        (extend.exposes || []).some((expose) =>
            expose?.property === channel.name || expose?.name === channel.name
        )
    );
    if (index < 0) {
        throw new Error(`BSEED V7 overlay cannot locate ${channel.name}`);
    }
    definition.extend[index] = v567ButtonCommandBehavior(channel);
}

module.exports = definitions;
