"""Exercise the optional read-only Z2M backend on GitHub-hosted CI."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_backend_requires_awaited_read_and_never_uses_cached_state():
    script = r'''
const assert = require('node:assert/strict');
const Probe = require('./zigbee2mqtt/extensions/bseed_link_probe');
let calls = [], outputs = [], failure = false;
const endpoint = {read: async (cluster, attributes, options) => {
    calls.push({cluster, attributes, options});
    if (failure) throw new Error('read timeout');
    return {onOff: 0};
}};
const entity = {name: 'Fixture', zh: {ieeeAddr: '0x0011223344556677',
    modelID: 'TS011F-BS', manufacturerName: 'o1jzcxou', getEndpoint: id => id === 2 ? endpoint : null}};
const bus = {onMQTTMessage: (owner, cb) => {}, removeListeners: owner => {}};
const probe = new Probe({resolveEntity: () => entity},
    {publish: async (topic, body, opts) => outputs.push({topic, body: JSON.parse(body), opts})},
    {state_relay: 'OFF'}, null, bus, null, null, null, {get: () => ({mqtt: {base_topic: 'z2m'}})});
const request = n => ({request_id: 'bseed-link-'+n.toString(16).padStart(32, '0'),
    device: 'Fixture', ieee: entity.zh.ieeeAddr, endpoint: 2, cluster: 'genOnOff',
    attribute: 'onOff', timeout_seconds: 12, issued_at: Date.now()/1000});
const send = r => probe.onMessage({topic: 'z2m/bridge/request/bseed/link_probe', message: JSON.stringify(r)});
(async () => {
    await probe.start();
    await send(request(1));
    assert.equal(outputs.at(-1).body.passed, true);
    assert.equal(outputs.at(-1).body.response_type, 'readResponse');
    assert.equal(outputs.at(-1).opts.retain, false);
    assert.deepEqual(calls[0].attributes, ['onOff']);
    assert.equal(calls[0].options.disableResponse, false);
    assert.equal(calls[0].options.disableRecovery, true);
    assert.equal(calls[0].options.sendPolicy, 'immediate');
    assert.equal(calls[0].options.transactionSequenceNumber, outputs.at(-1).body.transaction);
    failure = true;
    await send(request(2));
    assert.equal(outputs.at(-1).body.passed, false); // cached OFF cannot rescue timeout
    assert.match(outputs.at(-1).body.errors[0], /timeout/);
    failure = false;
    for (const r of [request(1), {...request(3), endpoint: 1},
        {...request(4), attribute: 'toggle'}, {...request(5), device: 'Neighbor'},
        {...request(6), issued_at: 0}]) {
        const before = calls.length;
        await send(r);
        assert.equal(outputs.at(-1).body.passed, false);
        assert.equal(calls.length, before);
    }
    assert.equal(probe.busy, false);
    await probe.stop();
})().catch(error => {console.error(error); process.exit(1);});
'''
    subprocess.run(['node', '-e', script], cwd=ROOT, check=True, capture_output=True, text=True)
