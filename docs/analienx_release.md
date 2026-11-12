# Analienx release for AVATTO and BSEED TS0726 boards

Analienx firmware (based on Romasku) for the AVATTO switch modules and the
BSEED TS0726 Echo Click / Scale switches. It carries this repository's shared
Telink Router hardening, which these boards do not get from the upstream 1.1.2
builds they run today:

- Router recovery and bounded rejoin-stall handling
  ([router reliability](router_reliability.md));
- the parent/route link policy ([link policy](bseed_link_policy.md));
- the relay `relay_physical_mode` and relay-endpoint `currentLevel` attributes
  that the deployed converter already reads. Upstream 1.1.2 answers those with
  `UNSUPPORTED_ATTRIBUTE`.

## Identity

| | |
| --- | --- |
| Basic build ID | `1.1.2-anx1` |
| OTA file version | `0x11023020` |
| Build date | `20261112` |
| Role | Router |
| Manufacturer code | 4417 |

Every board keeps its own `firmware_image_type` from `device_db.yaml`. The
version is above upstream `0x11023000`, so Zigbee2MQTT offers it to a board
on upstream 1.1.2. [`helper_scripts/analienx_release.py`](../helper_scripts/analienx_release.py)
holds the identity and the board set and refuses a board whose image type is
shared with another board, or whose version is not above every released one.

One upstream collision is recorded instead of refused: the ZWSM16-1
(`MODULE_AVATTO_A_TS0001`, in use) and the dry-contact module share image
type 43527. A device reports its installed type in every OTA query, so the
ZWSM16-1 cannot move to a new type without an image-type migration. The
dry-contact module is never built by this release, and an OTA index entry for
the ZWSM16-1 must filter by its manufacturer name (`_TZ3000_4rbqgcuv`,
`4rbqgcuv`) so that no dry-contact module is offered it.

This replaces the never-released `1.1.2-avlp1` identity of PR #99.

### Why Router only

Upstream builds a board's Router and End Device roles under the same image
type and version, so one OTA identity cannot tell them apart. A Router image
offered to a board running upstream End Device firmware would silently change
its role, which a no-neutral module cannot carry. The release therefore builds
only boards whose `device_db.yaml` role is `router`. The one exception is the
TS0726 1-gang: its role there is `end_device`, but the BedroomDimmer runs the
upstream Router build (Zigbee2MQTT database and state, 2026-11-12), so the
release keeps its installed role. Publish its image only to this site's own
index, never to a shared one.

The no-neutral LZWSM16-1/-2/-3 modules, the TS0726 2-gang and the TS0726
L 1-gang are End Device boards and need an End Device line with its own image
types first, as the BSEED Client lines have.

### Sealing

[`zigbee2mqtt/ota/analienx_identity.json`](../zigbee2mqtt/ota/analienx_identity.json)
records the SHA-512 of each board's `forward.ota` once its bytes are chosen.
After that, CI refuses different bytes under the same build ID: changed
firmware needs a new build ID and file version in `RELEASE`. Every release run
uploads `SEAL_PROPOSAL.json` with the exact hashes of that run; sealing is a
reviewed commit that copies them, never an automatic step. Nothing is sealed
yet.

## Boards

Boards in use in Home Assistant on 2026-11-12, all Routers on upstream 1.1.2:

| Device | Board | Image type |
| --- | --- | --- |
| HallMainSwitchAvatto, WorkRoomSwitchLedMain | `MODULE_AVATTO_A_TS0001` (ZWSM16-1) | 43527 |
| WorkRoomMainSwitchDimmer | `MODULE_AVATTO_A_TS0002` (ZWSM16-2) | 43528 |
| BedroomDimmer | `SWITCH_BSEED_TS0726_1GANG` | 45580 |

The release also builds every other buildable AVATTO and BSEED TS0726 Router
board in `device_db.yaml`: ZWSM16-3/-4 (A and B), the four AVATTO touch
switches and the TS0726 4-gang, 12 boards in all.
`python helper_scripts/analienx_release.py list` prints the current set.

Not in this release:

| Board | Reason |
| --- | --- |
| `SWITCH_BSEED_TS0726_3GANG` | has its own BSEED line ([TS0726 build](bseed_unified_v8.md)) |
| `MODULE_AVATTO_DRY_TS0001` | shares image type 43527 with the ZWSM16-1 |
| `SWITCH_BSEED_TS0726_L_2GANG` | shares image type 43633 with `MODULE_NOUS_B1Z_TS0001` |
| `SWITCH_BSEED_TS0726_L_3GANG_2` | shares image type 47007 with `MODULE_TUYA_COMMON_TS0001_2` |
| `SWITCH_BSEED_TS0726_L_3GANG`, `_L_4GANG` | `build: no` in `device_db.yaml` |
| `MODULE_AVATTO_TS0011`, `_TS0012`, `_TS0012_1`, `_TS0013`, `SWITCH_BSEED_TS0726_2GANG`, `_L_1GANG` | End Device boards; see "Why Router only" |

Each shared image type needs a new, unique type before that board can join.

## Build and evidence

[`.github/workflows/analienx-release.yml`](../.github/workflows/analienx-release.yml)
runs on GitHub-hosted Actions from the exact candidate head, one job per board:

1. builds with [`make_scripts/build_analienx_board.sh`](../make_scripts/build_analienx_board.sh)
   (`-O2`, link policy on, `DEVICE_TYPE=router`);
2. checks the produced bytes: OTA header and identity, whole container within
   the SDK's 0x34000-byte limit, startup marker, embedded version, length
   field, CRC, the length-prefixed Basic build ID, no other length-prefixed
   release build ID, no second start marker, and the sealed SHA-512 once
   sealed;
3. starts the OTA payload in the pinned TC32 model in both slots and restarts
   it on the flash its first start left behind;
4. runs the image's own OTA activation on a staged copy of itself, complete,
   partial and CRC-corrupt, in both slot directions.

Each board uploads `analienx-<board>-<sha>` with `forward.ota`, `forward.bin`,
`manifest.json`, `BOOT_REPORT.json` and the linker symbols. A final job
requires a report for every board and uploads `RELEASE_SET.json` and
`SEAL_PROPOSAL.json`.

No custom-to-custom OTA of these boards and no `-O2` Router of this tree is
recorded as having started on these exact boards. The TC32 model is not
hardware.

## Before any flash

- This release does not publish anything to the OTA index and does not
  authorize a flash. Each device needs the owner's decision.
- Check network-wide OTA activity and open campaign locks first; a new
  campaign cannot bypass an unverified one.
- One device first: an AVATTO ZWSM16-1, then the ZWSM16-2, then the TS0726
  1-gang. Accept only on fresh evidence: the new `swBuildId` from a Basic read,
  Router role, relay control from Zigbee and the button. Zigbee2MQTT OTA
  `status: ok` or 100% is not a confirmed start.
- Settings use NVM schema `NVM_MIGRATIONS_VERSION`; check that relay modes,
  switch modes, bindings and `power_on_behavior` survive the update.
- The BSEED canary tooling has no AVATTO or TS0726 1-gang profile yet. Add one
  before using `bseed_canary.py`; never use ad hoc flash scripts.
