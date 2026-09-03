'use strict';

/**
 * Production definition for the BSEED TS0726-3-BS controller.
 *
 * The proven hardened V5/V6 definition is kept as a library outside the
 * Zigbee2MQTT external_converters auto-load directory. This wrapper exports
 * exactly one definition and changes only the per-button "Control bound light"
 * surface so direct-binding transmission can be explicitly disabled with
 * raw value 0. The option is useful for a pure local-relay channel and does
 * not remove or rewrite any existing Zigbee binding/group topology.
 */

const definitions = require('../converter_lib/bseed_ts0726_v56_hardened.js');
const exposes = require('zigbee-herdsman-converters/lib/exposes');
const e = exposes.presets;
const ea = exposes.access;

const CHANNELS = [
    {name: 'switch_left_binded_mode', endpointName: 'switch_left', endpointId: 1, label: 'Left'},
    {name: 'switch_middle_binded_mode', endpointName: 'switch_middle', endpointId: 2, label: 'Middle'},
    {name: 'switch_right_binded_mode', endpointName: 'switch_right', endpointId: 3, label: 'Right'},
];

const LOOKUP = {
    'Never (disabled)': 0,
    'On press': 1,
    'Long press': 2,
    'Short press': 3,
};
const REVERSE = new Map(Object.entries(LOOKUP).map(([key, value]) => [String(value), key]));

const rawValue = (msg) =>
    msg.data?.[0xff05] ?? msg.data?.['65285'];

const productionBoundDeviceTrigger = ({name, endpointName, endpointId, label}) => ({
    isModernExtend: true,
    exposes: [
        e.enum(name, ea.ALL, Object.keys(LOOKUP))
            .withEndpoint(endpointName)
            .withProperty(name)
            .withLabel(`${label} — Control bound light`)
            .withDescription(
                'Chooses when this button sends a direct On/Off command to existing bindings or groups. ' +
                'Never (disabled) sends no direct-binding command and is the correct choice for a pure local-relay channel. ' +
                'On press is fastest; Short press waits for a completed click; Long press sends only after a hold. ' +
                'This setting does not create, remove or rewrite bindings.'
            )
            .withCategory('config'),
    ],
    fromZigbee: [{
        cluster: 'genOnOffSwitchCfg',
        type: ['attributeReport', 'readResponse'],
        convert: (model, msg) => {
            if (msg.endpoint.ID !== endpointId) return;
            const value = REVERSE.get(String(rawValue(msg)));
            if (value !== undefined) return {[name]: value};
        },
    }],
    toZigbee: [{
        key: [name],
        convertSet: async (entity, key, value, meta) => {
            if (!Object.prototype.hasOwnProperty.call(LOOKUP, value)) {
                throw new Error(`${name}: unsupported value ${JSON.stringify(value)}`);
            }
            const endpoint = meta?.device?.getEndpoint?.(endpointId);
            if (!endpoint) throw new Error(`${name}: EP${endpointId} is unavailable`);
            await endpoint.write('genOnOffSwitchCfg', {
                [0xff05]: {value: LOOKUP[value], type: 0x30},
            });
            return {state: {[key]: value}};
        },
        convertGet: async (entity, key, meta) => {
            const endpoint = meta?.device?.getEndpoint?.(endpointId);
            if (!endpoint) throw new Error(`${name}: EP${endpointId} is unavailable`);
            await endpoint.read('genOnOffSwitchCfg', [0xff05]);
        },
    }],
    configure: [],
});

if (!Array.isArray(definitions) || definitions.length !== 1) {
    throw new Error('BSEED production wrapper expected exactly one hardened base definition');
}

const definition = definitions[0];
for (const channel of CHANNELS) {
    const index = definition.extend.findIndex((extend) =>
        (extend.exposes || []).some((expose) =>
            expose?.property === channel.name || expose?.name === channel.name
        )
    );
    if (index < 0) {
        throw new Error(`BSEED production wrapper cannot locate ${channel.name}`);
    }
    definition.extend[index] = productionBoundDeviceTrigger(channel);
}

module.exports = definitions;
