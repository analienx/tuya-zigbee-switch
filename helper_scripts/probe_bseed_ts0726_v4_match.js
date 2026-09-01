#!/usr/bin/env node
'use strict';

/**
 * Offline proof for the canary deployment architecture:
 *
 *   historical fleet-wide converter + target-only exact-fingerprint overlay
 *
 * No Zigbee I/O. This loads both converter modules, applies the installed
 * ZHC 26.90.0 matcher ordering (fingerprints before zigbeeModel fallback),
 * and proves the target resolves to the overlay while unrelated model IDs
 * cannot be captured by it.
 */

const fs = require('fs');
const crypto = require('crypto');

const TARGET = {
    manufacturerName: 'iedhxgyi',
    modelID: 'TS0726-3-BS',
    expectedModel: 'EC-GL86ZPCS31',
    softwareBuildID: '1.1.4-bseedv4',
};

function die(message) {
    process.stderr.write(`FAIL: ${message}\n`);
    process.exit(2);
}

function sha256(path) {
    return crypto.createHash('sha256').update(fs.readFileSync(path)).digest('hex');
}

function loadDefinitions(path) {
    delete require.cache[require.resolve(path)];
    const exported = require(path);
    const defs = Array.isArray(exported) ? exported : exported.default;
    if (!Array.isArray(defs)) die(`${path}: converter does not export an array`);
    return defs;
}

function modelCandidates(definitions, modelID) {
    return definitions.filter((d) =>
        (d.fingerprint || []).some((fp) => fp.modelID === modelID) ||
        (d.zigbeeModel || []).includes(modelID)
    );
}

function fingerprintMatches(fp, identity) {
    return (fp.manufacturerName === undefined || fp.manufacturerName === identity.manufacturerName) &&
           (fp.modelID === undefined || fp.modelID === identity.modelID) &&
           (fp.softwareBuildID === undefined || fp.softwareBuildID === identity.softwareBuildID);
}

function selectLikeZhc(definitions, identity) {
    const candidates = modelCandidates(definitions, identity.modelID);
    let best = null;
    let bestPriority = undefined;

    // ZHC 26.90.0: first search ALL candidate fingerprints. zigbeeModel
    // fallback is only considered if no fingerprint matches.
    for (const candidate of candidates) {
        for (const fp of candidate.fingerprint || []) {
            const priority = fp.priority ?? 0;
            if (fingerprintMatches(fp, identity) &&
                (bestPriority === undefined || priority > bestPriority)) {
                best = candidate;
                bestPriority = priority;
            }
        }
    }
    if (best) return {definition: best, via: 'fingerprint', candidates};

    for (const candidate of candidates) {
        if ((candidate.zigbeeModel || []).includes(identity.modelID)) {
            return {definition: candidate, via: 'zigbeeModel', candidates};
        }
    }
    return {definition: undefined, via: undefined, candidates};
}

function main() {
    const historicalPath = process.argv[2];
    const overlayPath = process.argv[3];
    if (!historicalPath || !overlayPath) {
        die('usage: probe_bseed_ts0726_v4_match.js <historical-switch_custom.js> <bseed_ts0726_v4.js>');
    }

    const historical = loadDefinitions(historicalPath);
    const overlay = loadDefinitions(overlayPath);

    if (overlay.length !== 1) die(`target overlay must export exactly one definition, got ${overlay.length}`);
    const overlayDef = overlay[0];
    if (overlayDef.model !== TARGET.expectedModel) {
        die(`overlay model is ${overlayDef.model}, expected ${TARGET.expectedModel}`);
    }
    const fps = (overlayDef.fingerprint || []).filter(
        (fp) => fp.manufacturerName === TARGET.manufacturerName && fp.modelID === TARGET.modelID,
    );
    if (fps.length !== 1) die(`target exact fingerprint count is ${fps.length}, expected 1`);
    if ((overlayDef.zigbeeModel || []).includes(TARGET.modelID)) {
        die('overlay contains ambiguous bare TS0726-3-BS zigbeeModel matcher');
    }
    if (fps[0].softwareBuildID !== TARGET.softwareBuildID || (fps[0].priority ?? 0) !== 100) {
        die(`overlay fingerprint must target ${TARGET.softwareBuildID} at priority 100`);
    }

    const combined = [...overlay, ...historical];
    const selected = selectLikeZhc(combined, TARGET);
    if (!selected.definition) die('combined matcher found no target definition');
    if (selected.definition !== overlayDef || selected.via !== 'fingerprint') {
        die(`target selected ${selected.definition.model} via ${selected.via}, not overlay fingerprint`);
    }


    // Legacy and recovery firmware must NOT be captured by the forward overlay.
    for (const softwareBuildID of ['1.1.2-8542fc05', '1.1.4-bseedv4r']) {
        const fallback = selectLikeZhc(combined, {...TARGET, softwareBuildID});
        if (!fallback.definition || fallback.definition === overlayDef || fallback.via !== 'zigbeeModel') {
            die(`softwareBuildID ${softwareBuildID} did not fall back to the historical converter`);
        }
    }

    // An overlay with only TS0726-3-BS must not create candidates for unrelated
    // live custom firmware model IDs.
    for (const unrelated of [
        'TS011F-BS-PM',
        'TS011F-BS',
        'TS0001-AVB',
        'TS0002-AVB',
        'TS0726-1-BS',
    ]) {
        if (modelCandidates(overlay, unrelated).length !== 0) {
            die(`target-only overlay unexpectedly matches unrelated modelID ${unrelated}`);
        }
    }

    const oldTargetCandidates = modelCandidates(historical, TARGET.modelID).map((d) => ({
        model: d.model,
        hasFingerprint: Boolean(d.fingerprint && d.fingerprint.length),
        hasBareModel: Boolean(d.zigbeeModel && d.zigbeeModel.includes(TARGET.modelID)),
    }));

    process.stdout.write(JSON.stringify({
        status: 'PASS',
        historical: {
            path: historicalPath,
            sha256: sha256(historicalPath),
            definitionCount: historical.length,
            targetCandidates: oldTargetCandidates,
        },
        overlay: {
            path: overlayPath,
            sha256: sha256(overlayPath),
            definitionCount: overlay.length,
            targetModel: overlayDef.model,
            fingerprint: overlayDef.fingerprint,
            zigbeeModel: overlayDef.zigbeeModel || [],
        },
        selection: {
            manufacturerName: TARGET.manufacturerName,
            modelID: TARGET.modelID,
            model: selected.definition.model,
            via: selected.via,
            candidateModels: selected.candidates.map((d) => d.model),
        },
    }, null, 2) + '\n');
}

main();
