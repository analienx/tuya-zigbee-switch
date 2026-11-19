"""Exercise the Basic 0xFF13 parent / next-hop diagnostics through the real pinned ZHC on CI."""
import json
import os
import subprocess

from test_bseed_socket_converter_exposes import ROOT, _render

SCRIPT = r'''
const assert = require('node:assert/strict');
const {prepareDefinition} = require('zigbee-herdsman-converters');
const definitions = require(process.argv[1]);
const expectNames = process.argv[2] === 'names';
const NAMES = ['zigbee_uplink', 'zigbee_uplink_lqi', 'zigbee_uplink_cost', 'zigbee_link_failures_1h'];
const PARENT = '0xa4c13837b0bc0001';
class FakeDevice {
    static byNetworkAddress(nwk) { return nwk === 0x161c ? {ieeeAddr: PARENT} : undefined; }
    constructor(build) {
        this.softwareBuildID = build; this.ieeeAddr = '0xa4c13837b0bc5952'; this.reads = []; this.endpoints = [];
        this.endpoint = {ID: 1, read: async (cluster, attrs) => { this.reads.push([cluster, attrs]); return {}; }};
    }
    getEndpoint(id) { return id === 1 ? this.endpoint : undefined; }
}
const snapshot = (flags, nwk, lqi, cost, failures, schema = 1) =>
    Buffer.from([schema, flags, nwk & 0xff, nwk >> 8, lqi, cost, failures, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
(async () => {
    for (const [model, good, bad] of [
        ['TS011F-BS-PM', ['1.2.5-bseedr19', '1.2.5-bseedr22', '1.2.5-bseedr24', '1.2.5-bseedcli22', '1.2.5-bseedcli26', '1.2.5-bseedcli27'],
         ['1.2.5-bseedr18', '1.2.5-bseedcli21', '1.2.5-8b8cc492', '1.2.5-bseedv8u4', undefined]],
        ['TS011F-BS', ['1.1.3-bseedr18', '1.1.3-bseedr19', '1.1.3-bseedc16', '1.1.3-bseedc17', '1.1.3-bseedc19'],
         ['1.1.3-bseedr17', '1.1.3-bseedc15', '1.1.3-bseedv8']],
    ]) {
        const def = prepareDefinition(definitions.find(d => d.zigbeeModel?.includes(model)));
        const exposed = build => def.exposes(new FakeDevice(build), {}).map(e => e.name);
        for (const build of good) for (const name of NAMES) assert.ok(exposed(build).includes(name), `${model} ${build} ${name}`);
        for (const build of bad) for (const name of NAMES) assert.ok(!exposed(build).includes(name), `${model} ${build} ${name}`);
        const fz = def.fromZigbee.find(c => {
            if (c.cluster !== 'genBasic') return false;
            try {
                return c.convert(def, {type: 'readResponse', data: {[0xff13]: snapshot(4, 0x161c, 1, 1, 0)},
                                       device: new FakeDevice(good[0])}, () => {}, {}, {})?.zigbee_uplink !== undefined;
            } catch (error) {
                return false;  // another Basic converter that needs a full message
            }
        });
        assert.ok(fz, model);
        const decode = (value, build = good[0], type = 'readResponse') =>
            fz.convert(def, {type, data: {[0xff13]: value}, device: new FakeDevice(build)}, () => {}, {}, {});
        // Client parent with a known neighbour entry, resolved to its name.
        assert.deepEqual(decode(snapshot(0x04, 0x161c, 167, 1, 2)), {
            zigbee_uplink: expectNames ? 'LivingRoomSocketWifiLeft' : PARENT,
            zigbee_uplink_lqi: 167, zigbee_uplink_cost: 1, zigbee_link_failures_1h: 2});
        // Router whose next hop is the coordinator, from an attribute report.
        assert.equal(decode(snapshot(0x05, 0x0000, 200, 1, 0), good[0], 'attributeReport').zigbee_uplink, 'Coordinator');
        // An address the coordinator does not know stays a short address.
        assert.equal(decode(snapshot(0x04, 0x2b3c, 90, 3, 0)).zigbee_uplink, '0x2b3c');
        // Link not known (no neighbour entry): no LQI or cost is invented.
        assert.deepEqual(decode(snapshot(0x00, 0x161c, 0, 0, 5)), {
            zigbee_uplink: expectNames ? 'LivingRoomSocketWifiLeft' : PARENT,
            zigbee_uplink_lqi: null, zigbee_uplink_cost: null, zigbee_link_failures_1h: 5});
        // Not joined.
        assert.equal(decode(snapshot(0x00, 0xffff, 0, 0, 0)).zigbee_uplink, 'none');
        // Unknown schema, short payloads and unsupported builds publish nothing.
        assert.deepEqual(decode(snapshot(0x04, 0x161c, 167, 1, 0, 2)), {});
        assert.deepEqual(decode(Buffer.from([1, 4, 0x1c])), {});
        assert.deepEqual(decode('not a buffer'), {});
        assert.equal(decode(snapshot(0x04, 0x161c, 167, 1, 0), bad[0]), undefined);
        // Other Basic attributes are left to their own converters.
        assert.equal(fz.convert(def, {type: 'readResponse', data: {[0xff03]: 50}, device: new FakeDevice(good[0])},
                                () => {}, {}, {}), undefined);

        // Polling: one Basic 0xFF13 read every 300 s, only for supported builds.
        assert.equal(typeof def.onEvent, 'function', model);
        for (const [build, reads] of [[good[0], 1], [bad[0], 0]]) {
            const timers = [];
            const realSetTimeout = global.setTimeout;
            global.setTimeout = (fn, ms) => { const t = {fn, ms, unref() { return t; }}; timers.push(t); return t; };
            const device = new FakeDevice(build);
            try {
                await def.onEvent({type: 'start', data: {device, options: {}, state: {}, deviceExposesChanged: () => {}}});
            } finally {
                global.setTimeout = realSetTimeout;
            }
            const timer = timers.find(t => t.ms === 300_000);
            assert.ok(timer, `${model} ${build} poll timer`);
            global.setTimeout = (fn, ms) => ({unref() { return this; }});
            try { await timer.fn(); } finally { global.setTimeout = realSetTimeout; }
            assert.deepEqual(device.reads, Array(reads).fill(['genBasic', [0xff13]]), `${model} ${build}`);
            await def.onEvent({type: 'stop', data: {ieeeAddr: device.ieeeAddr}});
        }
    }
})().catch(e => {console.error(e); process.exit(1);});
'''


def _run(tmp_path, names):
    converter = tmp_path / 'switch_custom.js'
    converter.write_text(_render())
    env = dict(os.environ)
    if names:
        # Zigbee2MQTT ships js-yaml; the pinned ZHC test runtime does not, so
        # stand in with a JSON loader (JSON is valid YAML).
        fake = tmp_path / 'node_modules' / 'js-yaml'
        fake.mkdir(parents=True)
        (fake / 'index.js').write_text('module.exports = {load: text => JSON.parse(text)};')
        env['NODE_PATH'] = os.pathsep.join(filter(None, [str(tmp_path / 'node_modules'), env.get('NODE_PATH')]))
        data = tmp_path / 'z2m-data'
        data.mkdir()
        (data / 'configuration.yaml').write_text(json.dumps({
            'mqtt': {'password': 'never-published'},
            'devices': {'0xA4C13837B0BC0001': {'friendly_name': 'LivingRoomSocketWifiLeft'}}}))
        env['ZIGBEE2MQTT_DATA'] = str(data)
    else:
        env['ZIGBEE2MQTT_DATA'] = str(tmp_path / 'missing')
    result = subprocess.run(['node', '-e', SCRIPT, str(converter), 'names' if names else 'ieee'],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout


def test_uplink_resolves_friendly_names_from_zigbee2mqtt_settings(tmp_path):
    _run(tmp_path, names=True)


def test_uplink_falls_back_to_ieee_without_zigbee2mqtt_settings(tmp_path):
    _run(tmp_path, names=False)


def test_link_diagnostics_only_on_bseed_sockets():
    from test_bseed_socket_converter_exposes import _definition
    text = _render()
    for model in ('TS011F-BS-PM', 'TS011F-BS'):
        assert 'romasku.linkDiagnostics()' in _definition(text, model)
    assert text.count('romasku.linkDiagnostics()') == 2
