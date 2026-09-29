// Optional read-only backend for bseed_nonpm_link_gate.py (Z2M 2.14.0).
// Installing this extension is a separate live-service action.
const {randomInt} = require('node:crypto');

class BseedLinkProbe {
    constructor(zigbee, mqtt, state, publishEntityState, eventBus,
        enableDisableExtension, restartCallback, addExtension, settings) {
        this.zigbee = zigbee;
        this.mqtt = mqtt;
        this.eventBus = eventBus;
        this.base = settings.get().mqtt.base_topic;
        this.busy = false;
        this.seen = new Set();
    }

    async start() {
        this.eventBus.onMQTTMessage(this, data => this.onMessage(data));
    }

    async stop() {
        this.eventBus.removeListeners(this);
    }

    async onMessage(data) {
        if (data.topic !== `${this.base}/bridge/request/bseed/link_probe`) return;
        let request;
        try { request = JSON.parse(data.message); } catch { return; }
        if (!request || typeof request !== 'object') return;
        const result = {schema: 1, passed: false, request_id: request.request_id,
            device: request.device, ieee: request.ieee, endpoint: request.endpoint,
            cluster: request.cluster, attribute: request.attribute, errors: []};
        let owned = false;
        try {
            if (!/^bseed-link-[a-f0-9]{32}$/.test(request.request_id) ||
                !/^0x[a-f0-9]{16}$/.test(request.ieee) ||
                request.endpoint !== 2 || request.cluster !== 'genOnOff' ||
                request.attribute !== 'onOff' || request.timeout_seconds !== 12) {
                throw new Error('Only bounded endpoint-2 relay attribute reads are allowed');
            }
            if (!Number.isFinite(request.issued_at) ||
                Math.abs(Date.now() / 1000 - request.issued_at) > 5 ||
                this.seen.has(request.request_id)) throw new Error('Expired or reused probe request');
            if (this.busy) throw new Error('Another link probe is in progress');
            const entity = this.zigbee.resolveEntity(request.ieee);
            if (!entity || entity.name !== request.device ||
                entity.zh.ieeeAddr !== request.ieee || entity.zh.modelID !== 'TS011F-BS' ||
                entity.zh.manufacturerName !== 'o1jzcxou') {
                throw new Error('Target identity mismatch');
            }
            const endpoint = entity.zh.getEndpoint(2);
            if (!endpoint) throw new Error('Relay endpoint is absent');
            this.busy = true;
            owned = true;
            this.seen.add(request.request_id);
            if (this.seen.size > 256) this.seen.delete(this.seen.values().next().value);
            result.transaction = randomInt(256);
            result.requested_at = Date.now() / 1000;
            // Herdsman's awaited read resolves only for the matching ZCL
            // transaction. No cached MQTT publication can satisfy this promise.
            const response = await endpoint.read('genOnOff', ['onOff'], {
                transactionSequenceNumber: result.transaction,
                timeout: 12000, disableResponse: false, disableRecovery: true,
                sendPolicy: 'immediate',
            });
            result.response_at = Date.now() / 1000;
            if (response.onOff !== 0 && response.onOff !== 1) throw new Error('Missing relay read value');
            if (result.response_at - result.requested_at > 12) throw new Error('Read deadline exceeded');
            result.value = response.onOff;
            result.response_type = 'readResponse';
            result.passed = true;
        } catch (error) {
            result.errors.push(String(error.message || error).slice(0,240));
        } finally {
            if (owned) this.busy = false;
        }
        await this.mqtt.publish('bridge/response/bseed/link_probe', JSON.stringify(result), {retain: false});
    }
}

module.exports = BseedLinkProbe;
