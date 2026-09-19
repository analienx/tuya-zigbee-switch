import fs from "node:fs";

const PATCH = Symbol.for("ts0505b.ota.acceptanceProbe");
const STATUS_TOPIC = "bridge/ts0505b_ota_probe";
const DEFAULT_CONFIG = "/config/zigbee2mqtt/ts0505b_ota_probe.json";
const OTA_ABORT = 0x95;
const NO_IMAGE_AVAILABLE = 0x98;

function parseNumber(value, name) {
    const parsed = typeof value === "string" ? Number.parseInt(value, 0) : value;
    if (!Number.isInteger(parsed) || parsed < 0) throw new Error(`invalid ${name}`);
    return parsed;
}

function normalizeCase(input, defaults) {
    return {
        name: String(input.name ?? "unnamed"),
        manufacturerCode: parseNumber(input.manufacturerCode ?? defaults.manufacturerCode, "manufacturerCode"),
        imageType: parseNumber(input.imageType ?? defaults.imageType, "imageType"),
        fileVersion: parseNumber(input.fileVersion, "fileVersion"),
        imageSize: parseNumber(input.imageSize, "imageSize"),
        waitMs: parseNumber(input.waitMs ?? defaults.waitMs ?? 20000, "waitMs"),
    };
}

function normalizeConfig(raw) {
    if (!raw || typeof raw !== "object") throw new Error("config must be an object");
    if (!raw.target_ieee || !/^0x[0-9a-f]{16}$/i.test(raw.target_ieee)) throw new Error("target_ieee is required");
    if (!raw.run_id || !/^[A-Za-z0-9._-]{1,64}$/.test(raw.run_id)) throw new Error("run_id is required");
    if (raw.armed !== true) throw new Error("probe must be explicitly armed in local runtime sidecar");
    const defaults = {
        manufacturerCode: parseNumber(raw.manufacturerCode ?? 0x100b, "manufacturerCode"),
        imageType: parseNumber(raw.imageType ?? 0x020c, "imageType"),
        waitMs: parseNumber(raw.waitMs ?? 20000, "waitMs"),
    };
    if (!Array.isArray(raw.cases) || raw.cases.length !== 1) throw new Error("one case per run_id is required");
    return {
        targetIeee: raw.target_ieee.toLowerCase(),
        stockFileVersion: parseNumber(raw.stock_file_version ?? 0x10003607, "stock_file_version"),
        runId: raw.run_id,
        cooldownMs: parseNumber(raw.cooldownMs ?? 1500, "cooldownMs"),
        cases: raw.cases.map((item) => normalizeCase(item, defaults)),
    };
}

function isDataRequest(type) {
    return type === "commandImageBlockRequest" || type === "commandImagePageRequest";
}

export default class Ts0505bOtaAcceptanceProbe {
    constructor(zigbee, mqtt, _state, _publishEntityState, eventBus, _enableDisableExtension, _restartCallback, _addExtension, _settings, logger) {
        this.zigbee = zigbee;
        this.mqtt = mqtt;
        this.eventBus = eventBus;
        this.logger = logger;
        this.settings = _settings;
        this.configPath = process.env.Z2M_TS0505B_OTA_PROBE_CONFIG || DEFAULT_CONFIG;
        this.config = undefined;
        this.sentinelPath = undefined;
        this.device = undefined;
        this.endpoint = undefined;
        this.prototype = undefined;
        this.wrapper = undefined;
        this.caseIndex = -1;
        this.current = undefined;
        this.timer = undefined;
        this.results = [];
    }

    async start() {
        this.config = normalizeConfig(JSON.parse(fs.readFileSync(this.configPath, "utf8").replace(/^\uFEFF/, "")));
        this.sentinelPath = this.configPath + "." + this.config.runId + ".sentinel.json";
        if (fs.existsSync(this.sentinelPath)) {
            await this.publishStatus({state: "blocked-replay", run_id: this.config.runId});
            return;
        }
        fs.writeFileSync(this.sentinelPath, JSON.stringify({state: "reserved", run_id: this.config.runId}) + "\n", {encoding: "utf8", flag: "wx"});
        this.device = [...this.zigbee.zhController.getDevicesIterator()]
            .find((item) => item.ieeeAddr?.toLowerCase() === this.config.targetIeee);
        if (!this.device) throw new Error("configured TS0505B OTA probe target not found");
        if (this.device.modelID !== "TS0505B" || this.device.manufacturerName !== "_TZ3210_mja6r5ix") {
            throw new Error("stock TS0505B target identity mismatch");
        }
        if (this.device.scheduledOta || this.settings?.get?.()?.ota?.disable_automatic_update_check !== true) {
            throw new Error("scheduled or automatic OTA activity must be disabled before probe");
        }

        this.endpoint = this.device.endpoints.find((item) => item.ID === 1);
        if (!this.endpoint) throw new Error("configured TS0505B OTA probe endpoint 1 not found");

        this.installNoImageSuppressor();
        this.eventBus.onDeviceMessage(this, this.onDeviceMessage.bind(this));
        await this.publishStatus({state: "armed", cases: this.config.cases.length});
        await this.nextCase();
    }

    installNoImageSuppressor() {
        const prototype = Object.getPrototypeOf(this.endpoint);
        const original = prototype?.commandResponse;
        if (!prototype || typeof original !== "function") throw new Error("endpoint commandResponse unavailable");
        if (prototype[PATCH]) throw new Error("TS0505B OTA probe hook already installed");

        const probe = this;
        const wrapper = async function (clusterKey, commandKey, payload, options, tsn) {
            const target = this.deviceIeeeAddress?.toLowerCase() === probe.config.targetIeee;
            const ota = clusterKey === "genOta" || clusterKey === 0x0019;
            const queryResponse = commandKey === "queryNextImageResponse" || commandKey === 0x02;
            const blockResponse = commandKey === "imageBlockResponse" || commandKey === 0x05;
            if (probe.current && target && ota && blockResponse && payload?.status === 0) {
                throw new Error("blocked another OTA handler from delivering firmware bytes");
            }
            if (probe.current && target && ota && queryResponse && payload?.status === NO_IMAGE_AVAILABLE) {
                probe.logger?.warning?.("TS0505B OTA probe suppressed competing NO_IMAGE_AVAILABLE response");
                return;
            }
            return await Reflect.apply(original, this, [clusterKey, commandKey, payload, options, tsn]);
        };

        prototype[PATCH] = {original, wrapper};
        prototype.commandResponse = wrapper;
        this.prototype = prototype;
        this.wrapper = wrapper;
    }

    async nextCase() {
        if (this.timer) clearTimeout(this.timer);
        this.caseIndex += 1;
        if (this.caseIndex >= this.config.cases.length) {
            this.current = undefined;
            fs.writeFileSync(this.sentinelPath, JSON.stringify({state: "complete", run_id: this.config.runId, results: this.results}, null, 2) + "\n", "utf8");
            await this.publishStatus({state: "complete", run_id: this.config.runId, results: this.results});
            return;
        }

        const offer = this.config.cases[this.caseIndex];
        this.current = {offer, querySeen: false, acceptedPrebyte: false, startedAt: Date.now()};
        await this.publishStatus({state: "notifying", case_index: this.caseIndex, offer});
        await this.endpoint.commandResponse("genOta", "imageNotify", {
            payloadType: 3,
            queryJitter: 100,
            manufacturerCode: offer.manufacturerCode,
            imageType: offer.imageType,
            fileVersion: offer.fileVersion,
        }, {sendPolicy: "immediate"});

        this.timer = setTimeout(() => this.finishCurrent("no_block_request").catch((error) => this.fail(error)), offer.waitMs);
    }

    async onDeviceMessage(data) {
        if (!this.current || data.device?.ieeeAddr?.toLowerCase() !== this.config.targetIeee) return;
        if (data.endpoint?.ID !== 1 || data.cluster !== "genOta") return;

        if (data.type === "commandQueryNextImageRequest") {
            if (data.data?.manufacturerCode !== this.current.offer.manufacturerCode ||
                data.data?.imageType !== this.current.offer.imageType ||
                data.data?.fileVersion !== this.config.stockFileVersion) {
                await this.fail(new Error("OTA Query Next Image stock identity/version mismatch"));
                return;
            }
            const offer = this.current.offer;
            this.current.querySeen = true;
            await data.endpoint.commandResponse("genOta", "queryNextImageResponse", {
                status: 0,
                manufacturerCode: offer.manufacturerCode,
                imageType: offer.imageType,
                fileVersion: offer.fileVersion,
                imageSize: offer.imageSize,
            }, undefined, data.meta.zclTransactionSequenceNumber);
            await this.publishStatus({state: "offered", case_index: this.caseIndex, offer});
            return;
        }

        if (isDataRequest(data.type)) {
            // Every data request is aborted, even if its metadata is unexpected.
            await data.endpoint.commandResponse("genOta", "imageBlockResponse", {status: OTA_ABORT},
                undefined, data.meta.zclTransactionSequenceNumber);
            if (data.data?.manufacturerCode !== this.current.offer.manufacturerCode ||
                data.data?.imageType !== this.current.offer.imageType ||
                data.data?.fileVersion !== this.current.offer.fileVersion ||
                data.data?.fileOffset !== 0) {
                await this.fail(new Error("unexpected OTA block request: aborted without data"));
                return;
            }
            this.current.acceptedPrebyte = true;
            await this.finishCurrent("block_request_aborted");
        }
    }

    async finishCurrent(reason) {
        if (!this.current) return;
        if (this.timer) clearTimeout(this.timer);
        const result = {
            case_index: this.caseIndex,
            name: this.current.offer.name,
            fileVersion: this.current.offer.fileVersion,
            imageSize: this.current.offer.imageSize,
            query_seen: this.current.querySeen,
            accepted_prebyte: this.current.acceptedPrebyte,
            reason,
        };
        this.results.push(result);
        this.current = undefined;
        await this.publishStatus({state: "case_complete", result});
        this.timer = setTimeout(() => this.nextCase().catch((error) => this.fail(error)), this.config.cooldownMs);
    }

    async fail(error) {
        this.current = undefined;
        if (this.timer) clearTimeout(this.timer);
        this.logger?.error?.(`TS0505B OTA acceptance probe failed: ${error.message}`);
        await this.publishStatus({state: "failed", error: error.message});
    }

    async publishStatus(extra) {
        await this.mqtt.publish(STATUS_TOPIC, JSON.stringify({
            mode: "metadata-only",
            payload_bytes_sent: 0,
            ...extra,
        }), {clientOptions: {retain: true}});
    }

    async stop() {
        if (this.timer) clearTimeout(this.timer);
        this.eventBus.removeListeners(this);
        const record = this.prototype?.[PATCH];
        if (this.prototype && record?.wrapper === this.wrapper && this.prototype.commandResponse === this.wrapper) {
            this.prototype.commandResponse = record.original;
            delete this.prototype[PATCH];
        }
        this.current = undefined;
    }
}

export {NO_IMAGE_AVAILABLE, OTA_ABORT, STATUS_TOPIC, isDataRequest, normalizeCase, normalizeConfig};
