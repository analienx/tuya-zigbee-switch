#!/usr/bin/env node
'use strict';

/** Dependency-free deployment-contract proof for historical fleet + v4 overlay. */

const fs = require('fs');
const crypto = require('crypto');

const EXPECTED_HISTORICAL_SHA = 'ef79acfd2141837b539189bfadda07799b53267bd746e1209335d38b91c66bfe';
const FORWARD_BUILD = '1.1.4-bseedv4';
const RECOVERY_BUILD = '1.1.4-bseedv4r';
const LEGACY_BUILD = '1.1.2-8542fc05';

function die(message) {
    process.stderr.write('FAIL: ' + message + '\n');
    process.exit(2);
}

function sha256Buffer(buf) {
    return crypto.createHash('sha256').update(buf).digest('hex');
}

function canonicalHistorical(path) {
    const raw = fs.readFileSync(path);
    const rawHash = sha256Buffer(raw);
    if (rawHash === EXPECTED_HISTORICAL_SHA) return {bytes: raw, rawHash, normalization: 'none'};
    const normalized = Buffer.from(raw.toString('utf8').replace(/\r\n/g, '\n'), 'utf8');
    const normalizedHash = sha256Buffer(normalized);
    if (normalizedHash !== EXPECTED_HISTORICAL_SHA) {
        die('historical baseline hash mismatch: raw=' + rawHash + ' normalized=' + normalizedHash + ' expected=' + EXPECTED_HISTORICAL_SHA);
    }
    return {bytes: normalized, rawHash, normalization: 'crlf_to_lf'};
}

function count(text, needle) { return text.split(needle).length - 1; }

function main() {
    const historicalPath = process.argv[2];
    const overlayPath = process.argv[3];
    if (!historicalPath || !overlayPath) die('usage: probe_bseed_ts0726_overlay_match.js <historical.js> <overlay.js>');

    const historical = canonicalHistorical(historicalPath);
    const historicalText = historical.bytes.toString('utf8');
    const overlayBytes = fs.readFileSync(overlayPath);
    const overlayText = overlayBytes.toString('utf8');

    const markers = [
        'manufacturerName: "iedhxgyi"',
        'modelID: "TS0726-3-BS"',
        'softwareBuildID: "' + FORWARD_BUILD + '"',
        'priority: 100',
        'model: "EC-GL86ZPCS31"',
    ];
    for (const marker of markers) {
        if (count(overlayText, marker) !== 1) die('overlay marker must occur exactly once: ' + marker);
    }
    if (overlayText.includes('zigbeeModel:')) die('v4 overlay must not contain a bare zigbeeModel fallback');
    if (overlayText.includes(LEGACY_BUILD) || overlayText.includes(RECOVERY_BUILD)) die('overlay must not match legacy/recovery build IDs');
    if (!historicalText.includes('TS0726-3-BS')) die('historical fleet converter lacks TS0726-3-BS');
    if (historicalText.includes(FORWARD_BUILD)) die('historical fleet converter unexpectedly contains v4 forward identity');

    process.stdout.write(JSON.stringify({
        status: 'PASS',
        historical: {
            path: historicalPath,
            rawSha256: historical.rawHash,
            canonicalSha256: sha256Buffer(historical.bytes),
            bytes: historical.bytes.length,
            normalization: historical.normalization,
        },
        overlay: {
            path: overlayPath,
            sha256: sha256Buffer(overlayBytes),
            forwardSoftwareBuildID: FORWARD_BUILD,
            exactFingerprint: true,
            bareZigbeeModel: false,
        },
        selection: {
            [LEGACY_BUILD]: 'historical_fallback',
            [FORWARD_BUILD]: 'v4_overlay_exact_fingerprint',
            [RECOVERY_BUILD]: 'historical_fallback',
        },
        runtimeMatcherProbeStillRequired: true,
    }, null, 2) + '\n');
}

main();
