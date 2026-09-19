import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "helper_scripts/make_z2m_custom_converters.py"


def generated(*args):
    return subprocess.check_output([sys.executable, str(GEN), *args, "device_db.yaml"], cwd=ROOT, text=True)


@pytest.mark.parametrize("args", [(), ("--z2m-v1",)])
def test_socket_only_reconnect_endpoint_selection_is_generated(args):
    result = generated(*args)
    assert result.count("bseedSocketRelayOnOff(),") == 2
    assert result.count("// BSEED_SOCKET_RECONNECT_READ_START") == 1
    for model in ('"TS011F-BS-PM"', '"TS011F-BS"'):
        start = result.index(model)
        end = result.find("\n    {\n", start + len(model))
        definition = result[start:end] if end >= 0 else result[start:]
        assert "bseedSocketRelayOnOff()," in definition
    dimmer = result[result.index('"TS0726-3-BS"'):]
    assert "bseedSocketRelayOnOff()," not in dimmer.split("\n    {\n", 1)[0]



@pytest.mark.parametrize("args", [(), ("--z2m-v1",)])
def test_reconnect_get_uses_relay_endpoint_without_changing_set(args):
    generated_js = generated(*args)
    helper = generated_js.split("// BSEED_SOCKET_RECONNECT_READ_START", 1)[1].split("// BSEED_SOCKET_RECONNECT_READ_END", 1)[0]
    fixture = r"""
const assert = require('node:assert/strict');
const events = [];
const onOff = (options) => ({
    exposes: ['unchanged'],
    toZigbee: [{
        key: ['state'],
        convertSet: () => 'unchanged-set',
        convertGet: async (endpoint, key) => {
            await endpoint.read('genOnOff', ['onOff']);
            return key;
        },
    }],
});
"""
    validation = r"""
(async () => {
    const endpoint1 = {ID: 1, read: () => {throw Error('wrong endpoint 1');}};
    const endpoint2 = {ID: 2, read: async (cluster, attrs) => events.push([cluster, attrs])};
    const converted = bseedSocketRelayOnOff();
    const state = converted.toZigbee[0];
    assert.deepEqual(converted.exposes, ['unchanged']);
    assert.equal(state.convertSet(), 'unchanged-set');
    assert.equal(await state.convertGet(endpoint1, 'state', {device: {getEndpoint: (id) => id === 2 ? endpoint2 : null}}), 'state');
    assert.deepEqual(events, [['genOnOff', ['onOff']]]);
    await assert.rejects(() => state.convertGet(endpoint1, 'state', {device: {getEndpoint: () => null}}), /endpoint 2 unavailable/);
    assert.deepEqual(events, [['genOnOff', ['onOff']]]);
    console.log('PASS: endpoint 2 selected, writes unchanged, missing relay fails closed');
})().catch(e => {console.error(e); process.exitCode = 1;});
"""
    result = subprocess.run(['node', '-e', fixture + helper + validation], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert 'PASS:' in result.stdout
