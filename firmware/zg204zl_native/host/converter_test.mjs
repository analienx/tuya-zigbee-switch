import assert from 'node:assert/strict';
import fs from 'node:fs';
import {Zcl} from 'zigbee-herdsman';
import definition from '../../../zigbee2mqtt/converters/zg204zl_native.mjs';

const vectors = JSON.parse(fs.readFileSync(new URL('../vectors.json', import.meta.url)));
const setter = definition.toZigbee.find(c => c.key.includes('native_lighting'));
const base = Buffer.from(vectors.configurations[0].hex, 'hex');
let current, registered, frames, rejectReadback;
const device = {addCustomCluster: (name, cluster) => { registered = cluster; }};
const entity = {
    ID: 1,
    read: async (cluster) => {
        if (cluster === 0x0401) return {16: 13000};
        return {configuration: rejectReadback ? base : current};
    },
    command: async (name, command, payload, options) => {
        assert.equal(name, 'nativeControl'); assert.equal(command, 'setConfig');
        assert.equal(options.manufacturerCode, 0x1141); assert.equal(options.disableDefaultResponse, false);
        const frame = Zcl.Frame.create(1, 0, false, options.manufacturerCode, 0, command,
            registered, payload, {}).toBuffer();
        frames.push(frame); current = frame.subarray(5);
    },
};
for (const vector of vectors.configurations) {
    current = base; frames = []; rejectReadback = false;
    const result = await setter.convertSet(entity, 'native_lighting', vector.ui, {device});
    assert.equal(frames[0].subarray(5).toString('hex'), vector.hex);
    assert.deepEqual(result.state.native_lighting, {...vector.ui,
        brightness_percent: Math.round(Buffer.from(vector.hex, 'hex')[2] * 1000 / 254) / 10});
}
current = base; frames = []; rejectReadback = false;
await setter.convertSet(entity, 'native_lighting', {time_enabled: true}, {device});
assert.equal(frames[0].subarray(5).toString('hex'), vectors.configurations[1].hex.replace('0103', '0101'));
for (const value of [{start:'25:00'}, {brightness_percent:NaN}, {fade_in:120.1},
    {fade_out:0.05}, {time_enabled:'true'}, {unexpected:1},
    {time_enabled:true,start:'00:00',end:'00:00'}]) {
    frames = []; current = base;
    await assert.rejects(setter.convertSet(entity, 'native_lighting', value, {device}));
    assert.equal(frames.length, 0);
}
current = base; frames = []; rejectReadback = true;
await assert.rejects(setter.convertSet(entity, 'native_lighting', {fade_in:2}, {device}), /readback/);
assert.equal(frames.length, 1);
assert.equal(definition.ota, true);
assert.deepEqual(definition.fingerprint, [{modelID:'ZG-204ZL-z', softwareBuildID:'3001-0149'}]);
assert.ok(definition.extend.length >= 7);
console.log('Converter: shared wire vectors, real herdsman framing, validation and readback passed');
