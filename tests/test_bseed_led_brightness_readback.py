"""Exercise brightness writes through the real pinned ZHC converter on CI."""
import subprocess

from test_bseed_socket_converter_exposes import ROOT, _render


def test_brightness_publishes_committed_readback_and_propagates_failures(tmp_path):
    converter = tmp_path / 'switch_custom.js'
    converter.write_text(_render())
    script = r'''
const assert = require('node:assert/strict');
const {prepareDefinition} = require('zigbee-herdsman-converters');
const definitions = require(process.argv[1]);
class Endpoint {
    constructor() { this.ID = 1; this.value = 30; this.calls = []; }
    async write(cluster, attributes) {
        this.calls.push(['write', cluster, attributes]);
        if (this.writeError) throw Error('write failed');
        if (!this.failNV) this.value = Math.min(100, Math.max(1, attributes[0xff03].value));
    }
    async read(cluster, attributes) {
        this.calls.push(['read', cluster, attributes]);
        if (this.readError) throw Error('readback failed');
        return this.response === undefined ? {[0xff03]: this.value} : this.response;
    }
}
(async () => {
    for (const model of ['TS011F-BS-PM', 'TS011F-BS']) {
        const definition = prepareDefinition(definitions.find(d => d.zigbeeModel?.includes(model)));
        const converter = definition.toZigbee.find(c => c.key?.includes('led_brightness'));
        assert.ok(converter);
        const endpoint = new Endpoint();
        const meta = {mapped: definition, device: {getEndpoint: id => id === 1 ? endpoint : null},
                      endpoint_name: 'switch', options: {}};
        for (const [requested, actual, failNV] of [[80, 30, true], [40, 40, false], [250, 100, false]]) {
            endpoint.failNV = failNV;
            endpoint.calls = [];
            assert.deepEqual(await converter.convertSet(endpoint, 'led_brightness', requested, meta),
                             {state: {led_brightness: actual}});
            assert.deepEqual(endpoint.calls.map(c => c[0]), ['write', 'read']);
            assert.deepEqual(endpoint.calls[1], ['read', 'genBasic', [0xff03]]);
        }
        endpoint.readError = true;
        await assert.rejects(converter.convertSet(endpoint, 'led_brightness', 50, meta), /readback failed/);
        endpoint.readError = false;
        for (const response of [{}, {[0xff03]: 0}, {[0xff03]: 101}, {[0xff03]: '50'}]) {
            endpoint.response = response;
            await assert.rejects(converter.convertSet(endpoint, 'led_brightness', 50, meta), /missing or invalid/);
        }
        endpoint.writeError = true;
        endpoint.calls = [];
        await assert.rejects(converter.convertSet(endpoint, 'led_brightness', 50, meta), /write failed/);
        assert.deepEqual(endpoint.calls.map(c => c[0]), ['write']);
    }
})().catch(e => {console.error(e); process.exit(1);});
'''
    subprocess.run(['node', '-e', script, str(converter)], cwd=ROOT,
                   check=True, capture_output=True, text=True)
