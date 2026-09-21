# BSEED socket anti-brick OTA candidate — engineering preparation

**Status: offline candidates; not hardware accepted and not fleet flash authorized.** Production Router golden images and ordinary OTA index are unchanged. The failed/unverified KitchenLeft and KitchenRight histories are not resolved by compiling a replacement.

## Hardware/role matrix

| Physical board | Existing retained release | Opt-in candidate identity | Build command (Linux native Telink workspace) |
| --- | --- | --- | --- |
| PM TS011F `b28wrpvx` Router | `1.2.5-bseedv8u4` | `1.2.5-bseedv8u5-rc3`, `0x12053010` | `BSEED_PM_ROUTER_CANDIDATE=2 bash make_scripts/build_bseed_ts011f_pm_v8.sh` |
| PM TS011F `b28wrpvx` Client | `1.2.5-bseedcli6` experimental | `1.2.5-bseedcli8`, `0x12053011` | `BSEED_ANTIBRICK_RC=2 bash make_scripts/build_bseed_mains_client.sh pm` |
| non-PM TS011F `o1jzcxou` Router | `1.1.3-bseedv8` | `1.1.3-bseedv10`, `0x11023014` | `BSEED_ANTIBRICK_RC=2 bash make_scripts/build_bseed_ts011f_nonpm_router.sh` |
| non-PM TS011F `o1jzcxou` Client | `1.1.2-bseedcli4` experimental (`cli5-rc1` separate) | `1.1.2-bseedcli7`, `0x11023013` | `BSEED_ANTIBRICK_RC=2 bash make_scripts/build_bseed_mains_client.sh nonpm` |

A role change uses the separate *from-router* transition package and the scoped rejoin/metadata workflow; never submit it through a same-role updater. The `from_tuya` package is a stock-conversion wrapper, not a factory backup. For a same-role update use only `forward.ota` for the matching board and role.

## Mandatory preflight before each physical canary

1. Use `skills/bseed-zigbee-ota/SKILL.md` and a private target profile; read the *live* IEEE, manufacturer/model, hardware board variant, logical role, software build, target relay state and load. Reject stale role/build metadata or an unresolved OTA lock.
2. Save the complete Z2M/coordinator config and a target-specific read-only settings, binds, group, endpoint/cluster and reporting snapshot. Record physically verified relay/load behavior and a separately verified recovery image for that board. Do not substitute an RC artifact for an accepted Router rollback image.
3. Check the image SHA-256, matching OTA header manufacturer/image type, strictly greater file version, compiled board configuration and software build ID. Serve one private image/index per target. Do not change `index_bseed.json` or publish experimental Client images.
4. Before OTA: all host regressions and native Telink build/manifest/CRC checks must pass, including the PM/non-PM × Router/Client guard matrix and corrupted-NVM cold-boot simulation. Confirm no concurrent update; use the existing one-target campaign's preflight/check/lock machinery.
5. After a completed OTA: targeted interview and *fresh device-originated* build/role verification, then local button/relay response, retained settings and energy, no-load/known-load PM readings, binding/reporting integrity, parent/rejoin and 24-hour canary stability. A successful transfer without new build or restored state stays UNVERIFIED; never automatically reflash it.

## Release blockers remaining after these configuration fixes

Left's persistent loss of local response is not attributable from coordinator logs. Right's previous transfer still has unverified installed build/relay-energy retention. PM legacy-NVM migration can fail before local-control initialization; watchdog starts after `app_init()`. Client parent/rejoin fault containment and independently recoverable boot/OTA rollback remain unproven. Do not claim near-zero brick probability or fleet readiness from compiled binaries and host tests alone. Controlled one-device acceptance is the next gate.

## Reproducible four-image offline gate

Run the four opt-in build commands above in one clean native Telink workspace at the same Git commit, then run `python3 helper_scripts/bseed_antibrick_rc_gate.py`. The tool checks the exact expected board/role/build/version for every candidate, matching Git provenance, all available SHA-256/SHA-512 hashes, OTA header tuples, wrapper payload identity, and the blocked normal-index Client flag. It performs no device/network actions. Do not use a package assembled from mixed commits or prior build directories. A pass allows *only* consideration of a designated single-device canary after the hardware prerequisites above, not publication or a fleet update.

## 2026-09-21 integration with non-PM CLI5 canary and monotonic version policy

The complete `fix/bseed-nonpm-client-keepalive-cli5` history through `0fb79459` is integrated with the anti-brick branch; `cli5-rc1` remains a separately signed-off *older* candidate, not the latest anti-brick image. Its non-invasive, explicitly nonrecoverable OTA exception is restricted to the original BedroomSocketCabinetRight IEEE, image SHA-256 and `cli4` to `cli5-rc1` same-role transition; it NEVER applies to `cli7`, other sockets, PM or Router images. Build-option `1` preserves the earlier candidate identity; **build-option `2` is the new merged release candidate**. Do not rebuild historical `cli5-rc1` under its signed-off hash from this newer source.

`helper_scripts/bseed_socket_version_policy.py` is the board-scoped custom firmware release ledger. The preflash build must be identified, registered and actually present in current Zigbee2MQTT inventory, and the new custom image's numeric `fileVersion` must be strictly greater than that build's registered number across both Router and Client. Both the profile-driven and direct OTA runners reject unregistered/mismatched BSEED custom firmware versions. Do not reuse an OTA fileVersion with altered firmware bytes; reserve a NEW number for each new release image. A Telink/Tuya stock-facing conversion wrapper's `0xFFFFFFFF` outer OTA version is a special transport identity, not a custom firmware release number. Independently verify its embedded firmware version and use the dedicated conversion procedure.

A new candidate image is still hardware-unaccepted; native compilation, unit tests and increasing OTA version do not grant authorization to flash production. Never repurpose the exact `cli5-rc1` opt-in waiver for an anti-brick candidate.
