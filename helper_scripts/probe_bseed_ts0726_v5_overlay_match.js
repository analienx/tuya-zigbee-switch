#!/usr/bin/env node
'use strict';

/** Dependency-free matcher proof for coexisting v4 + v5 target overlays. */

const fs = require('fs');

const V4 = '1.1.4-bseedv4';
const V4R = '1.1.4-bseedv4r';
const V5 = '1.1.5-bseedv5';
const V5R = '1.1.5-bseedv5r';
const LEGACY = '1.1.2-8542fc05';

function die(message) {
    process.stderr.write('FAIL: ' + message + '\n');
    process.exit(2);
}

function count(text, needle) {
    return text.split(needle).length - 1;
}

function exactOverlay(path, build) {
    const text = fs.readFileSync(path, 'utf8');
    for (const marker of [
        'manufacturerName: "iedhxgyi"',
        'modelID: "TS0726-3-BS"',
        'softwareBuildID: "' + build + '"',
        'priority: 100',
        'model: "EC-GL86ZPCS31"',
    ]) {
        if (count(text, marker) !== 1) die(`${path}: marker count !=1: ${marker}`);
    }
    if (text.includes('zigbeeModel:')) die(`${path}: bare zigbeeModel fallback present`);
    return text;
}

function main() {
    const v4Path = process.argv[2];
    const v5Path = process.argv[3];
    if (!v4Path || !v5Path) {
        die('usage: probe_bseed_ts0726_v5_overlay_match.js <v4-overlay.js> <v5-overlay.js>');
    }

    const v4 = exactOverlay(v4Path, V4);
    const v5 = exactOverlay(v5Path, V5);

    for (const other of [V5, V5R, LEGACY]) {
        if (v4.includes('softwareBuildID: "' + other + '"')) {
            die(`v4 overlay unexpectedly matches ${other}`);
        }
    }
    for (const other of [V4, V4R, V5R, LEGACY]) {
        if (v5.includes('softwareBuildID: "' + other + '"')) {
            die(`v5 overlay unexpectedly matches ${other}`);
        }
    }

    process.stdout.write(JSON.stringify({
        status: 'PASS',
        coexistence: true,
        selection: {
            [LEGACY]: 'base_converter_fallback',
            [V4]: 'v4_overlay_exact',
            [V4R]: 'base_converter_fallback',
            [V5]: 'v5_overlay_exact',
            [V5R]: 'base_converter_fallback',
        },
        noBareModelFallback: true,
        runtimeMatcherProbeStillRequired: true,
    }, null, 2) + '\n');
}

main();
