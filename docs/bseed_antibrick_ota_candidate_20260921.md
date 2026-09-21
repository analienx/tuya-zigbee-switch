# BSEED socket anti-brick OTA candidate — engineering preparation

**Status: offline candidates; not hardware accepted and not fleet flash authorized.** Production Router golden images and ordinary OTA index are unchanged. The failed/unverified KitchenLeft and KitchenRight histories are not resolved by compiling a replacement.

## Hardware/role matrix

| Physical board | Existing retained release | Opt-in candidate identity | Build command (Linux native Telink workspace) |
| --- | --- | --- | --- |
| PM TS011F `b28wrpvx` Router | `1.2.5-bseedv8u4` | `1.2.5-bseedv8u5-rc2`, `0x1205300E` | `BSEED_PM_ROUTER_CANDIDATE=1 bash make_scripts/build_bseed_ts011f_pm_v8.sh` |
| PM TS011F `b28wrpvx` Client | `1.2.5-bseedcli6` experimental | `1.2.5-bseedcli7`, `0x1205300F` | `BSEED_ANTIBRICK_RC=1 bash make_scripts/build_bseed_mains_client.sh pm` |
| non-PM TS011F `o1jzcxou` Router | `1.1.3-bseedv8` | `1.1.3-bseedv9`, `0x11023012` | `BSEED_ANTIBRICK_RC=1 bash make_scripts/build_bseed_ts011f_nonpm_router.sh` |
| non-PM TS011F `o1jzcxou` Client | `1.1.2-bseedcli4` experimental (`cli5-rc1` separate) | `1.1.2-bseedcli6`, `0x11023011` | `BSEED_ANTIBRICK_RC=1 bash make_scripts/build_bseed_mains_client.sh nonpm` |

A role change uses the separate *from-router* transition package and the scoped rejoin/metadata workflow; never submit it through a same-role updater. The `from_tuya` package is a stock-conversion wrapper, not a factory backup. For a same-role update use only `forward.ota` for the matching board and role.

## Mandatory preflight before each physical canary

1. Use `skills/bseed-zigbee-ota/SKILL.md` and a private target profile; read the *live* IEEE, manufacturer/model, hardware board variant, logical role, software build, target relay state and load. Reject stale role/build metadata or an unresolved OTA lock.
2. Save the complete Z2M/coordinator config and a target-specific read-only settings, binds, group, endpoint/cluster and reporting snapshot. Record physically verified relay/load behavior and a separately verified recovery image for that board. Do not substitute an RC artifact for an accepted Router rollback image.
3. Check the image SHA-256, matching OTA header manufacturer/image type, strictly greater file version, compiled board configuration and software build ID. Serve one private image/index per target. Do not change `index_bseed.json` or publish experimental Client images.
4. Before OTA: all host regressions and native Telink build/manifest/CRC checks must pass, including the PM/non-PM × Router/Client guard matrix and corrupted-NVM cold-boot simulation. Confirm no concurrent update; use the existing one-target campaign's preflight/check/lock machinery.
5. After a completed OTA: targeted interview and *fresh device-originated* build/role verification, then local button/relay response, retained settings and energy, no-load/known-load PM readings, binding/reporting integrity, parent/rejoin and 24-hour canary stability. A successful transfer without new build or restored state stays UNVERIFIED; never automatically reflash it.

## Release blockers remaining after these configuration fixes

Left's persistent loss of local response is not attributable from coordinator logs. Right's previous transfer still has unverified installed build/relay-energy retention. PM legacy-NVM migration can fail before local-control initialization; watchdog starts after `app_init()`. Client parent/rejoin fault containment and independently recoverable boot/OTA rollback remain unproven. Do not claim near-zero brick probability or fleet readiness from compiled binaries and host tests alone. Controlled one-device acceptance is the next gate.
