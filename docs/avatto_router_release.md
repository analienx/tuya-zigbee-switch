# AVATTO ZWSM16 Router release (Analienx firmware, based on Romasku)

| Module | Board | Zigbee ID | Image type | Candidate | Version |
|---|---|---|---|---|---|
| ZWSM16-1 | `MODULE_AVATTO_A_TS0001` | `4rbqgcuv` / `TS0001-AVB` | 43527 | `1.1.2-avlp1` | `0x11023001` |
| ZWSM16-2 | `MODULE_AVATTO_A_TS0002` | `mtnpt6ws` / `TS0002-AVB` | 43528 | `1.1.2-avlp1` | `0x11023001` |

Both live modules (WorkRoomSwitchLedMain, WorkRoomMainSwitchDimmer) run upstream
Romasku `1.1.2-8542fc05`, OTA file version `0x11023000`. That commit is an
ancestor of this repository's `main`. A generic `board.mk` build of `main`
would carry the same `0x11023000` (`VERSION` is still `1.1.2`), so
Zigbee2MQTT would never offer it; these candidates therefore use explicit,
higher versions from `helper_scripts/avatto_release.py`.

The images include Router recovery and the parent/route link policy
(`docs/bseed_link_policy.md`), plus the relay `relay_physical_mode` and
relay-endpoint `currentLevel` attributes. The deployed converter already
reads those, which is why the live 1.1.2 modules log `UNSUPPORTED_ATTRIBUTE`.

## Build and verification

`.github/workflows/avatto-router-ci.yml` builds both images on GitHub-hosted
Actions from the exact head with `make_scripts/build_avatto_router.sh`. It
verifies the native image (OTA header 56 bytes, manufacturer 4417, image
type, file version, firmware subelement, startup marker, embedded version and
CRC, and the embedded Basic build ID) and uploads `forward.ota`,
`forward.bin` and `manifest.json`. No `from_tuya` wrapper is built: both
modules already run custom firmware.

## Before any flash

- Same-role Router-to-Router OTA. Check settings persistence: relay modes,
  switch modes, bindings, `power_on_behavior`, which use NVM migration
  schema `NVM_MIGRATIONS_VERSION`.
- One module first (ZWSM16-1), then the other. Confirm the installed build
  and role after re-interview; transport `status: ok` is not acceptance.
- The BSEED canary tooling does not have AVATTO board profiles yet. Add them
  before using `bseed_canary.py`; never use ad hoc one-off flash scripts.
