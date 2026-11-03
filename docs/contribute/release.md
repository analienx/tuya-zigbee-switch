# BSEED release process

**One exact source → verified native images → sealed candidate → device canary → reviewed public publication.**

[Current firmware status](../bseed_socket_release_status.md) · [Release notes](../releases/README.md) · [BSEED OTA skill](../../skills/bseed-zigbee-ota/SKILL.md) · [Contributing](../../CONTRIBUTING.md)

> [!CAUTION]
> **Do not publish or flash from an ordinary developer build.** The exact hardware identity, mains load safety, immutable OTA version, network-wide campaign exclusion and physical rollback options remain required. A passing OTA transfer is neither a confirmed boot nor hardware acceptance.

## The release states

| Stage | Required proof | Who/what advances it |
| --- | --- | --- |
| 1. Source proposed | Board/role scope, design review, safety delta, new version allocation | Pull request |
| 2. CI verified | GitHub-hosted exact-head host tests, PM/non-PM role matrices, paired Router TC32 where affected | GitHub Actions only |
| 3. Candidate sealed | Native OTA headers/CRC/build identity, role-specific image type, immutable hash and provenance in the identity registry | Sealing/finalization workflow |
| 4. Single-device canary | Exact target, fresh identity and physical safety, exclusive network lock, bounded OTA, postflash reinterview | Authorized hardware operator |
| 5. Hardware accepted | Actual installed build, functioning relay and metering, persistence, route/parent health and soak | Reviewed device-specific evidence |
| 6. Published | Reviewed index entry plus release notes and user-facing compatibility restrictions | Explicit release change |

## 1 · Define the candidate

- Identify the **exact Zigbee manufacturer/model, board, MCU, Router or mains-Client role**. Generic Tuya `TS011F` product names are not enough.
- Update the relevant version definition in `helper_scripts/bseed_pm_release.py` or `helper_scripts/bseed_nonpm_release.py`, using the [immutable identity and allocator policy](../bseed_pm_variant_matrix.md). Each board family advances its own version counter; Router and Client have separate image types.
- Do not rebuild changed bytes under any previously reserved or sealed firmware version. Keep older goldens and stock-conversion wrappers untouched.
- Explain firmware runtime changes, compatibility, NVM/calibration/relay persistence, parent-routing behavior, and recovery risks in the PR.
- Create a dated note from [the release-note template](../releases/TEMPLATE.md). Mark it **candidate**, not released.

## 2 · Verify and seal at an exact commit

Use **public GitHub-hosted runners only**, on a clean checkout of the exact source SHA. Run the workflows appropriate to changed code, including:

- [host tests](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/test.yml);
- [PM role reproducibility](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/pm-reproducibility.yml) and the [PM matrix contract](../bseed_pm_variant_matrix.md);
- non-PM role/OTA matrix where the non-PM board is affected;
- [paired Telink Router TC32](https://github.com/analienx/tuya-zigbee-switch/actions/workflows/telink-router-ci.yml) for Router lifecycle or shared SDK changes;
- candidate finalization and identity/OTA packaging gates.

Keep the evidence links, exact SHA, image type, native FILEVER, build ID and SHA-256 in the release note. A green run does not imply the firmware has been installed on hardware. See [build and seal reference](../../skills/bseed-zigbee-ota/references/build-and-verify.md).

## 3 · Qualify one physical device

1. Read [targeted OTA guidance](../bseed_targeted_ota_runner.md) and the current campaign's earlier locks, recovery facts and attached-load policy.
2. Ensure **no coordinator upgrade, Zigbee2MQTT restart, open permit-join or other firmware campaign** overlaps the operation. Preserve a verified backup/recovery path.
3. Use the private one-target canary profile and independently sealed image. Run the non-flash qualification first.
4. Only after actual operator authorization for the exact IEEE, perform the bounded same-role OTA; do not weaken integrity, identity or exclusion checks.
5. On transport completion, reinterview the **same** device. Verify the fresh Basic build, role, relay state and policy, metering/energy on PM boards, bindings and parent/child topology.
6. Keep the result **unverified** until a physical load test and sustained stability/soak gate pass. Do not automatically retry failed transfers or mark the whole fleet accepted.

Cross-role Router↔Client transitions, stock conversion, TS0726 dimmer experiments and recovery after an interrupted OTA have their **own** authorization and hardware gates. They are not implicit parts of a same-role socket release.

## 4 · Promote and publish separately

- Review the canary record and outstanding safety findings for **each affected board and role**. A good PM Client run never grants a non-PM Router release.
- Review any Zigbee2MQTT converter changes and compatibility requirements before publication.
- Only now update `zigbee2mqtt/ota/index_bseed.json` and its corresponding immutable artifacts, with the exact identities and digests; maintain stock conversion wrappers only when separately verified for their target.
- Update [firmware availability](../bseed_socket_release_status.md), [the changelog](../changelog_fw.md) and the user-facing [README](../../README.md). Change a candidate's status to *published* only when the index actually contains it.
- Get GitHub-hosted CI green on the **final publication PR's** exact SHA. Retain release decisions and evidence with the PR/issue.

**No-op release:** A documentation update, repository CI change or private canary does not change published firmware unless the OTA index and its binaries were actually updated.

## Special case · generic upstream releases

The upstream Romasku project may also maintain a general `VERSION` and firmware changelog. Keep that generic release process separate from BSEED's board-specific OTA identity registry and hardware promotion gates. Do not use a generic "push main and build" instruction as authorization to distribute BSEED firmware.

## Documentation maintenance

For docs-only changes, use [the documentation hub](../README.md) and [release-note template](../releases/TEMPLATE.md). Update candidate rows when the release definitions advance, preserve dated historical notes, and run the GitHub-hosted documentation integrity check. The check verifies internal paths and metadata alignment but never performs a hardware action.
