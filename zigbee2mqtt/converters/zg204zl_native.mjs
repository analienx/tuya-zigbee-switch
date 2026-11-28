import * as m from 'zigbee-herdsman-converters/lib/modernExtend';
import * as exposes from 'zigbee-herdsman-converters/lib/exposes';
import {Zcl} from 'zigbee-herdsman';

const e = exposes.presets;
const ea = exposes.access;
const manufacturerCode = 0x1141;
const cluster = {
    name: 'nativeControl', ID: 0xfc70, manufacturerCode,
    attributes: {
        configuration: {name: 'configuration', ID: 0, type: 0x41},
        diagnostics: {name: 'diagnostics', ID: 1, type: 0x41},
    },
    commands: {
        setConfig: {name: 'setConfig', ID: 0, parameters: [
            {name: 'version', type: 0x20}, {name: 'flags', type: 0x18},
            {name: 'level', type: 0x20}, {name: 'reserved', type: 0x20},
            {name: 'start', type: 0x21}, {name: 'end', type: 0x21},
            {name: 'fadeIn', type: 0x21}, {name: 'fadeOut', type: 0x21},
        ]},
    },
    commandsResponse: {},
};
const options = {manufacturerCode, disableDefaultResponse: false};
const keys = ['time_enabled', 'lux_enabled', 'start', 'end', 'brightness_percent', 'fade_in', 'fade_out'];

function bytes(value) {
    const b = Buffer.from(value?.data ?? value);
    if (b.length !== 12 || b[0] !== 1) throw new Error('Unsupported native-control record');
    return b;
}
function minute(value) {
    if (typeof value !== 'string' || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value))
        throw new Error('Time must use HH:MM, 00:00 through 23:59');
    return Number(value.slice(0, 2)) * 60 + Number(value.slice(3));
}
function hhmm(value) { return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`; }
function decode(raw) {
    const b = bytes(raw);
    return {
        time_enabled: !!(b[1] & 1), lux_enabled: !!(b[1] & 2),
        start: hhmm(b.readUInt16LE(4)), end: hhmm(b.readUInt16LE(6)),
        brightness_percent: Math.round(b[2] * 1000 / 254) / 10,
        fade_in: b.readUInt16LE(8) / 10, fade_out: b.readUInt16LE(10) / 10,
    };
}
function encode(value) {
    const number = (key, min, max) => {
        const v = value[key];
        if (typeof v !== 'number' || !Number.isFinite(v) || v < min || v > max)
            throw new Error(`${key} must be a number from ${min} to ${max}`);
        return v;
    };
    for (const key of ['time_enabled', 'lux_enabled'])
        if (typeof value[key] !== 'boolean') throw new Error(`${key} must be true or false`);
    const start = minute(value.start), end = minute(value.end);
    if (value.time_enabled && start === end) throw new Error('Enabled time window must have different start/end');
    const fadeIn = number('fade_in', 0, 120), fadeOut = number('fade_out', 0, 120);
    if (Math.abs(fadeIn * 10 - Math.round(fadeIn * 10)) > 1e-8 ||
        Math.abs(fadeOut * 10 - Math.round(fadeOut * 10)) > 1e-8)
        throw new Error('Fade durations must use tenths of a second');
    return {version: 1, flags: Number(value.time_enabled) | Number(value.lux_enabled) << 1,
        level: Math.round(number('brightness_percent', 0.4, 100) * 254 / 100), reserved: 0,
        start, end, fadeIn: Math.round(fadeIn * 10), fadeOut: Math.round(fadeOut * 10)};
}
function wire(value) {
    const b = Buffer.alloc(12);
    b[0] = value.version; b[1] = value.flags; b[2] = value.level;
    b.writeUInt16LE(value.start, 4); b.writeUInt16LE(value.end, 6);
    b.writeUInt16LE(value.fadeIn, 8); b.writeUInt16LE(value.fadeOut, 10);
    return b;
}
function setup(meta, entity) {
    if (!meta.device || entity.ID !== 1) throw new Error('Use the sensor endpoint 1, not a Zigbee group');
    meta.device.addCustomCluster('nativeControl', cluster);
}
function diagnostics(raw) {
    const b = bytes(raw);
    const reasons = ['admitted', 'master_disabled', 'clock_unknown', 'outside_time_window',
        'lux_threshold_missing', 'too_bright_or_lux_invalid', 'resumed_fade', 'no_binding'];
    return {version: b[0], phase: ['idle', 'active', 'releasing'][b[1] & 3],
        reason: reasons[b[1] >> 2] ?? 'unknown', clock_valid: !!b[2],
        clock_age_minutes: b.readUInt16LE(3), last_aps_status: b[5], last_zcl_status: b[6],
        configured: !!(b[7] & 1), off_unconfirmed: !!(b[7] & 2), stale_on_dropped: !!(b[7] & 4),
        transport_blocked: !!(b[7] & 8),
        onoff_destinations: b[8], level_destinations: b[9], group_destinations: b[10],
        last_zcl_cluster: b[11] & 0x80 ? 8 : 6, last_zcl_command: b[11] & 0x7f};
}

const native = {
    key: ['native_lighting', 'native_diagnostics'],
    convertSet: async (entity, key, value, meta) => {
        if (key !== 'native_lighting' || !value || typeof value !== 'object' || Array.isArray(value))
            throw new Error('native_lighting must be an object; diagnostics are read-only');
        for (const k of Object.keys(value)) if (!keys.includes(k)) throw new Error(`Unknown setting ${k}`);
        setup(meta, entity);
        const old = await entity.read('nativeControl', ['configuration'], options);
        const candidate = encode({...decode(old.configuration), ...value});
        if (candidate.flags & 2) {
            const threshold = await entity.read(0x0401, [0x0010]);
            const target = Object.values(threshold)[0];
            if (typeof target !== 'number' || target === 0xffff)
                throw new Error('Set/read illuminance_threshold before enabling lux filtering');
        }
        await entity.command('nativeControl', 'setConfig', candidate, options);
        const result = await entity.read('nativeControl', ['configuration'], options);
        if (!bytes(result.configuration).equals(wire(candidate)))
            throw new Error('Configuration readback differs; inspect before binding or retrying');
        return {state: {native_lighting: decode(result.configuration)}};
    },
    convertGet: async (entity, key, meta) => {
        setup(meta, entity);
        await entity.read('nativeControl', [key === 'native_lighting' ? 'configuration' : 'diagnostics'], options);
    },
};
const remote = {
    key: ['remote_on_off'],
    convertSet: async (entity, key, value) => {
        if (value !== 'ON' && value !== 'OFF') throw new Error('remote_on_off must be ON or OFF');
        await entity.command('genOnOff', value.toLowerCase(), {}, {disableDefaultResponse: false});
        const result = await entity.read('genOnOff', ['onOff']);
        if (!!result.onOff !== (value === 'ON')) throw new Error('Remote-control enable readback differs');
        return {state: {remote_on_off: value}};
    },
    convertGet: async (entity) => { await entity.read('genOnOff', ['onOff']); },
};

export default {
    // Scope the external converter to this qualified protocol/firmware identity.
    // Older sensors continue matching the built-in definition unchanged.
    fingerprint: [{modelID: 'ZG-204ZL-z', softwareBuildID: '3001-0149'}],
    model: 'ZG-204ZL-z', vendor: 'Custom devices (DiY)',
    description: 'Luminance motion sensor with native conditional binding and fades',
    ota: true,
    extend: [
        m.deviceAddCustomCluster('nativeControl', cluster), m.battery(), m.occupancy(), m.illuminance(),
        m.enumLookup({name: 'power_on_behavior', cluster: 'genOnOff', attribute: 'startUpOnOff',
            lookup: {off: 0, on: 1, toggle: 2, previous: 255}, description: 'Power-on behavior'}),
        m.numeric({name: 'pir_timeout', cluster: 'msOccupancySensing', attribute: 'pirOToUDelay',
            unit: 's', valueMin: 0, valueMax: 65535, access: 'ALL', description: 'PIR timeout in seconds'}),
        m.numeric({name: 'illuminance_threshold', cluster: 0x0401, attribute: {ID: 0x5003, type: Zcl.DataType.UINT24},
            unit: 'lx', valueMin: 0, valueMax: 1000000, access: 'ALL',
            description: 'Native illuminance threshold; lux condition can be disabled independently'}),
    ],
    exposes: [
        e.binary('remote_on_off', ea.ALL, 'ON', 'OFF').withDescription('Enable the sensor\'s bound light control'),
        e.composite('native_lighting', 'native_lighting', ea.ALL)
            .withFeature(e.binary('time_enabled', ea.SET, true, false))
            .withFeature(e.binary('lux_enabled', ea.SET, true, false))
            .withFeature(e.text('start', ea.SET).withDescription('Inclusive local start, HH:MM'))
            .withFeature(e.text('end', ea.SET).withDescription('Exclusive local end, HH:MM; may cross midnight'))
            .withFeature(e.numeric('brightness_percent', ea.SET).withUnit('%').withValueMin(0.4).withValueMax(100).withValueStep(0.1))
            .withFeature(e.numeric('fade_in', ea.SET).withUnit('s').withValueMin(0).withValueMax(120).withValueStep(0.1))
            .withFeature(e.numeric('fade_out', ea.SET).withUnit('s').withValueMin(0).withValueMax(120).withValueStep(0.1)),
        e.text('native_diagnostics', ea.STATE_GET).withCategory('diagnostic')
            .withDescription('Native decision/clock/transport record; APS success does not prove lamp state'),
    ],
    fromZigbee: [
        {cluster: 'nativeControl', type: ['readResponse', 'attributeReport'], convert: (model, msg) => {
            const result = {};
            if (msg.data.configuration !== undefined) result.native_lighting = decode(msg.data.configuration);
            if (msg.data.diagnostics !== undefined) result.native_diagnostics = JSON.stringify(diagnostics(msg.data.diagnostics));
            return result;
        }},
        {cluster: 'genOnOff', type: ['readResponse', 'attributeReport'], convert: (model, msg) =>
            msg.data.onOff === undefined ? {} : {remote_on_off: msg.data.onOff ? 'ON' : 'OFF'}},
    ],
    toZigbee: [native, remote],
};
