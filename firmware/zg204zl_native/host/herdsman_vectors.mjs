// Run on hosted Actions with the exact deployed herdsman version.
import fs from 'node:fs';
import {createRequire} from 'node:module';
import {Zcl} from 'zigbee-herdsman';
const require = createRequire(import.meta.url);
const service = require('zigbee-herdsman/dist/utils/timeService');
const RealDate = globalThis.Date;
let timestamp = 0;
process.env.TZ = 'Europe/Prague';
globalThis.Date = class extends RealDate {
    constructor(...args) { super(...(args.length ? args : [timestamp])); }
    static now() { return timestamp; }
};
const names = ['time', 'timeStatus', 'timeZone', 'dstStart', 'dstEnd', 'dstShift', 'validUntilTime'];
const cluster = Zcl.Utils.getCluster('genTime', undefined, {});
const result = [];
function add(iso, clear = true) {
    timestamp = RealDate.parse(iso);
    if (clear) service.clearCachedTimeData();
    const values = service.getTimeClusterAttributes();
    const payload = names.map(name => ({attrId: cluster.attributes[name].ID,
        status: 0, dataType: cluster.attributes[name].type, attrData: values[name]}));
    const wire = Zcl.Frame.create(0, 1, true, undefined, 0, 'readRsp', cluster, payload, {}).toBuffer();
    const parts = new Intl.DateTimeFormat('en-GB', {timeZone: 'Europe/Prague', hourCycle: 'h23',
        hour: '2-digit', minute: '2-digit'}).formatToParts(new RealDate(timestamp));
    const hour = Number(parts.find(p => p.type === 'hour').value);
    const minute = Number(parts.find(p => p.type === 'minute').value);
    result.push({iso, values, hex: wire.toString('hex'), localMinute: hour * 60 + minute});
    return values;
}
try {
    for (const day of ['2027-03-27T12:34:00Z','2027-03-28T00:59:59Z','2027-03-28T01:00:00Z',
        '2027-03-29T12:34:00Z','2027-10-30T12:34:00Z','2027-10-31T00:59:59Z',
        '2027-10-31T01:00:00Z','2027-11-01T12:34:00Z','2027-06-15T12:34:00Z']) add(day);
    const initial = add('2027-06-15T12:34:00Z');
    add(new RealDate((initial.validUntilTime - 1) * 1000 + RealDate.UTC(2000, 0, 1)).toISOString(), false);
} finally { globalThis.Date = RealDate; }
fs.writeFileSync(process.argv[2], JSON.stringify(result, null, 2) + '\n');
console.log(`Wrote ${result.length} real herdsman Time frames`);
