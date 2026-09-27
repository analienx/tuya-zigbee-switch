"""Execute the generated helper against the deployed ZHC version, on CI."""
import subprocess
import json
import sys

from test_bseed_socket_converter_exposes import ROOT, _render
sys.path.insert(0, str(ROOT / 'helper_scripts'))
from bseed_pm_provision import REPORTS, missing_reports
from bseed_pm_release import CANDIDATES


def test_real_converter_dispatch_reads_relay_for_plain_and_scoped_state(tmp_path):
    converter = tmp_path / 'switch_custom.js'
    converter.write_text(_render())
    script = r'''
const assert = require('node:assert/strict');
const {prepareDefinition} = require('zigbee-herdsman-converters');
const {onOff} = require('zigbee-herdsman-converters/lib/modernExtend');
const originalGet = onOff().toZigbee.find(c => c.key.includes('state')).convertGet;
const definitions = require(process.argv[1]);
// Same endpoint predicate as deployed Z2M 2.14.0 publish.ts. This must be
// exercised before convertGet: a direct callback test misses selection bugs.
const select = (cs, endpointName) => cs.find(c =>
    (!c.key || c.key.includes('state')) &&
    (!c.endpoints || (endpointName && c.endpoints.includes(endpointName))));
(async () => {
    for (const model of ['TS011F-BS-PM', 'TS011F-BS']) {
        const def = prepareDefinition(definitions.find(d => d.zigbeeModel?.includes(model)));
        const calls = [];
        const ep1 = {ID: 1, read: async () => {throw Error('wrong endpoint');}};
        const ep2 = {ID: 2, read: async (cluster, attrs) => calls.push([cluster, attrs])};
        const meta = {device: {getEndpoint: id => id === 2 ? ep2 : ep1}};
        const plain = select(def.toZigbee, undefined), scoped = select(def.toZigbee, 'relay');
        assert.equal(typeof plain.convertGet, 'function');
        assert.equal(plain.convertSet, undefined);
        assert.equal(typeof scoped.convertSet, 'function');
        assert.deepEqual(scoped.endpoints, ['relay']);
        // Availability selects by key only; preserve this path too.
        const availability = def.toZigbee.find(c => c.key.includes('state'));
        for (const c of [plain, scoped, availability]) await c.convertGet(ep1, 'state', meta);
        assert.deepEqual(calls, Array(3).fill(['genOnOff', ['onOff']]));
        await assert.rejects(plain.convertGet(ep1, 'state', {device: {getEndpoint: () => null}}),
            /endpoint 2 unavailable/);
    }
    assert.equal(onOff().toZigbee.find(c => c.key.includes('state')).convertGet, originalGet);
})().catch(e => {console.error(e); process.exit(1);});
'''
    subprocess.run(['node', '-e', script, str(converter)], cwd=ROOT,
                   check=True, capture_output=True, text=True)


def test_generated_converter_loads_and_pm_nonpm_contracts_stay_separate(tmp_path):
    converter = tmp_path / 'switch_custom.js'
    rendered = _render()
    for candidate in CANDIDATES.values():
        assert '"' + candidate['build'] + '"' in rendered
    converter.write_text(rendered)
    script = r'''
const assert = require('node:assert/strict');
const {prepareDefinition} = require('zigbee-herdsman-converters');
const definitions = require(process.argv[1]);
const find = name => prepareDefinition(definitions.find(d => d.zigbeeModel?.includes(name)));
const pm = find('TS011F-BS-PM'), nonpm = find('TS011F-BS');
assert.deepEqual(pm.endpoint({}), {switch: 1, relay: 2});
for (const property of ['power', 'current', 'voltage', 'energy']) {
    assert.ok(pm.exposes.some(e => e.property === property));
    assert.ok(pm.meta.multiEndpointSkip.includes(property));
    assert.ok(!nonpm.exposes.some(e => e.property === property));
}
assert.ok(pm.fromZigbee.some(f => f.cluster === 'haElectricalMeasurement'));
assert.ok(pm.fromZigbee.some(f => f.cluster === 'seMetering'));
assert.ok(!nonpm.fromZigbee.some(f => f.cluster === 'haElectricalMeasurement' || f.cluster === 'seMetering'));
assert.equal(typeof pm.configure, 'function');
assert.ok(pm.options.some(o => o.property === 'bseed_pm_telemetry_quarantine'));
assert.ok(!nonpm.options.some(o => o.property === 'bseed_pm_telemetry_quarantine'));
'''
    subprocess.run(['node', '-e', script, str(converter)], cwd=ROOT,
                   check=True, capture_output=True, text=True)


def test_pm_configure_uses_real_modern_extend_and_preserves_legacy_scales():
    helper = _render().split('// BSEED_PM_METER_START')[1].split('// BSEED_PM_METER_END')[0]
    script = r'''
const assert = require('node:assert/strict');
const {electricityMeter} = require('zigbee-herdsman-converters/lib/modernExtend');
class Endpoint {
    constructor() {
        this.ID = 1; this.deviceIeeeAddress = '0x0000000000000001';
        this.cache = {}; this.binds = []; this.reports = []; this.reads = [];
    }
    getInputClusters() { return [{name: 'haElectricalMeasurement', ID: 2820}, {name: 'seMetering', ID: 1794}]; }
    saveClusterAttributeKeyValue(cluster, values) { this.cache[cluster] = {...this.cache[cluster], ...values}; }
    getClusterAttributeValue(cluster, key) { return this.cache[cluster]?.[key]; }
    save() {}
    async bind(cluster) { this.binds.push(cluster); }
    async configureReporting(cluster, items) { this.reports.push({cluster, items}); }
    async read(cluster, attributes) {
        this.reads.push({cluster, attributes});
        if (attributes.some(a => /multiplier|divisor/i.test(a))) throw Error('scale-read-failed');
        return {};
    }
}
const makeDevice = (build, manufacturer = 'b28wrpvx') => {
    const ep = new Endpoint();
    return {modelID: 'TS011F-BS-PM', manufacturerName: manufacturer, softwareBuildID: build,
        endpoints: [ep], getEndpoint: () => ep, ieeeAddr: ep.deviceIeeeAddress};
};
''' + helper + r'''
(async () => {
    let sequence = 1;
    for (const build of ['1.2.5-bseedcli6', '1.2.5-bseedcli11', '1.2.5-bseedr7', '1.2.5-bseedr8']) {
      for (const staleCache of [false, true]) {
        const device = makeDevice(build), ep = device.endpoints[0];
        if (staleCache) ep.cache = {haElectricalMeasurement: {acVoltageDivisor: 1}, seMetering: {divisor: 100}};
        const extension = bseedPmElectricityMeter();
        const earlyModel = {meta: {multiEndpoint: true, multiEndpointSkip: ['power', 'current', 'voltage', 'energy']}};
        const early = (cluster, data) => extension.fromZigbee.find(f => f.cluster === cluster).convert(earlyModel,
            {cluster, data, device, endpoint: ep, type: 'attributeReport', meta: {zclTransactionSequenceNumber: sequence++}},
            () => {}, {}, {device});
        assert.deepEqual(early('haElectricalMeasurement', {rmsVoltage: 23000, rmsCurrent: 1250, activePower: 288}),
            {voltage: 230, current: 1.25, power: 288});
        assert.deepEqual(early('seMetering', {currentSummDelivered: 12345}), {energy: 12.345});
        // Still test configure independently from the early-report repair.
        ep.cache = staleCache ? {haElectricalMeasurement: {acVoltageDivisor: 1}, seMetering: {divisor: 100}} : {};
        for (const configure of extension.configure) await configure(device, {}, {});
        assert.deepEqual(new Set(ep.binds), new Set(['haElectricalMeasurement', 'seMetering']));
        assert.deepEqual(new Set(ep.reports.flatMap(r => r.items.map(i => i.attribute))),
            new Set(['activePower', 'rmsCurrent', 'rmsVoltage', 'currentSummDelivered']));
        assert.equal(ep.cache.haElectricalMeasurement.acVoltageDivisor, 100);
        assert.equal(ep.cache.haElectricalMeasurement.acCurrentDivisor, 1000);
        assert.equal(ep.cache.seMetering.divisor, 1000);
        assert.ok(!ep.reads.some(r => r.attributes.some(a => /multiplier|divisor/i.test(a))));
        const model = {meta: {multiEndpoint: true, multiEndpointSkip: ['power', 'current', 'voltage', 'energy']},
            endpoint: () => ({switch: 1, relay: 2})};
        const decode = (cluster, data) => extension.fromZigbee.find(f => f.cluster === cluster).convert(model,
            {cluster, data, device, endpoint: ep, type: 'attributeReport', meta: {zclTransactionSequenceNumber: sequence++}},
            () => {}, {}, {device});
        assert.deepEqual(decode('haElectricalMeasurement', {rmsVoltage: 23000, rmsCurrent: 1250, activePower: 288}),
            {voltage: 230, current: 1.25, power: 288});
        assert.deepEqual(decode('seMetering', {currentSummDelivered: 12345}), {energy: 12.345});
        assert.deepEqual(decode('haElectricalMeasurement', {rmsCurrent: 0, activePower: 0}), {current: 0, power: 0});
        console.log(JSON.stringify(ep.reports));
      }
    }
    for (const build of ['1.2.5-bseedv8u4', 'unknown', undefined]) {
        const device = makeDevice(build), ep = device.endpoints[0];
        ep.cache = {haElectricalMeasurement: {acVoltageDivisor: 1}, seMetering: {divisor: 100}};
        const before = structuredClone(ep.cache);
        const extension = bseedPmElectricityMeter();
        const model = {meta: {publishDuplicateTransaction: true}};
        const msg = {cluster: 'haElectricalMeasurement', data: {rmsVoltage: 230}, device, endpoint: ep};
        assert.deepEqual(extension.fromZigbee.find(f => f.cluster === msg.cluster).convert(model, msg, () => {}, {}, {device}), {voltage: 230});
        await assert.rejects(extension.configure[0](device, {}, {}), /scale-read-failed/);
        assert.deepEqual(ep.cache, before);
    }
    const wrongBoard = makeDevice('1.2.5-bseedcli11', 'another-board');
    await assert.rejects(bseedPmElectricityMeter().configure[0](wrongBoard, {}, {}), /scale-read-failed/);
    assert.deepEqual(wrongBoard.endpoints[0].cache, {});
    // A transition can report new raw units before Basic metadata catches up.
    // The campaign sets this persistent option before OTA and leaves it set
    // after a failed interview. Do not publish guessed values in that window.
    for (const build of ['1.2.5-bseedv8u4', 'unknown', undefined, '1.2.5-bseedcli11']) {
        const device = makeDevice(build), ep = device.endpoints[0];
        const extension = bseedPmElectricityMeter();
        for (const [cluster, data] of [
            ['haElectricalMeasurement', {rmsVoltage: 23000, rmsCurrent: 1250, activePower: 288}],
            ['seMetering', {currentSummDelivered: 12345}],
        ]) {
            const converter = extension.fromZigbee.find(f => f.cluster === cluster);
            const result = converter.convert({}, {device, endpoint: ep, cluster, data},
                () => {throw Error('quarantined publication');}, {bseed_pm_telemetry_quarantine: true}, {device});
            assert.deepEqual(result, {});
        }
        assert.deepEqual(ep.cache, {});
    }
})().catch(e => {console.error(e); process.exit(1);});
'''
    result = subprocess.run(['node', '-e', script], cwd=ROOT, check=True, capture_output=True, text=True)
    batches = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('[{')]
    assert len(batches) == 8
    for batch in batches:
        rows = [{'cluster': r['cluster'], 'attribute': item['attribute'],
                 'minimum_report_interval': item['minimumReportInterval'],
                 'maximum_report_interval': item['maximumReportInterval'],
                 'reportable_change': item['reportableChange']}
                for r in batch for item in r['items']]
        assert not missing_reports({'endpoints': {'1': {'configured_reportings': rows}}})
        for cluster, attr, _, min_s, max_s, change in REPORTS:
            assert next(r for r in rows if r['cluster'] == cluster and r['attribute'] == attr) == {
                'cluster': cluster, 'attribute': attr, 'minimum_report_interval': min_s,
                'maximum_report_interval': max_s, 'reportable_change': change}
