# Agent instructions: wired Telink recovery

This is the entry point for agents using `telink_swire_recovery.py`. It also ships
as `AGENTS.md` at the root of each downloadable wired recovery kit.

## Read first and establish scope

1. Follow the repository [agent rules](../../../AGENTS.md), the
   [BSEED maintenance skill](../../../skills/bseed-zigbee-ota/SKILL.md) where
   applicable, and the latest device-specific incident handover.
2. Read the [operator and protocol guide](../swire_recovery.md) and
   [logic review](../swire_recovery_review.md). In a downloaded kit these are
   `README.md` and `swire_recovery_review.md` beside this file.
3. Hardware work needs authorization for the actual target and current physical
   setup. Reuse authorization and physical facts already established in the task;
   do not repeat the operator's standing device-loss acknowledgement. Never infer
   isolation, voltage, secure contacts or an unloaded socket from a device name.
4. Before live HA/Zigbee2MQTT access, load the canonical
   [Home Assistant read-only skill](https://github.com/analienx/config/blob/main/skills/home-assistant-readonly/SKILL.md).
   Do not copy credentials or helpers into this repository.

Repository builds, tests, lint, offline dry-run verification, packaging and source
acceptance must run on GitHub-hosted Actions from the exact candidate SHA. Do not
run them on the workstation or WSL. The commands below describe the separately
authorized hardware procedure; they are not permission to run local repository
verification or to flash a device merely because CI passed.

## Locate the tool and discover its contract

In a kit, work from its root and use `python telink_swire_recovery.py`. In a
checkout, the script is `helper_scripts/telink_swire_recovery.py`. Port access
needs Python 3.8+ and pyserial in the approved runtime environment. Firmware
images and bundles come from the chosen exact-SHA hosted artifacts.

Use `capabilities --json` to discover commands and exit codes. Use `ports --json`
to identify the actual adapter, and `wiring --device PROFILE --json` for notes.
For the adapter-side TX-resistor/RX junction with one SWS hook, select
`wiring --connection cp2102-single-sws --device PROFILE --json`.
For bounded repeated target discovery use `watch-link --device PROFILE --port PORT
--seconds 180 --out NEW_PRIVATE_DIRECTORY --json`. The operator starts this command;
do not start hardware access merely because a launcher was prepared. The watcher
waits for the bound USB adapter, tracks its available identity through COM-number
changes, and backs off serial open/I/O errors. Preserve its summary and each probe
receipt. It never unlocks a port, resets/halts a target, sends adapter loop patterns
to a target or advances a recovery session. Ctrl+C prevents further probes.
`SERIAL_OPEN_FAILED`/`SERIAL_IO_FAILED` are USB/driver-stage failures, not evidence
of an incorrect SWire divider. An echoed read clock alone is not a target reply.
Run `test-link --phase adapter` only with the target disconnected; after physical
qualification, `test-link --phase target --device PROFILE` requires real target
replies. Echo alone is never proof of SWire readback. The diagnostic neither
resets nor halts the CPU and probes flash only when its control register reports
exactly `0x05`. Its small reads do not replace a full backup or advance a session.
Adapter tests run for five seconds by default; `--duration 10` selects a longer
stress test and `--debug` shows each chunk or target divider attempt. Interpret
`statistics` as measured verified echo throughput and chunk turnaround, separate
from configured baud and firmware speed. Preserve partial metrics and the first
byte-error details on failure. Live progress goes to stderr in JSON mode, while
the private receipt retains a bounded structured event history.

`read` automatically downloads and hash-verifies the pinned pvvx reader on first
use and then uses its shared offline cache. `fetch-reader --json` prepares or
verifies that cache without opening hardware. An explicit `--reader PATH` must
match the same hash. A corrupt source or failed download stops before serial
execution. Loopback/target diagnostics use the built-in limited protocol and do
not require a pvvx download; no manual pvvx checkout is necessary.
`--json` never prompts; stdout contains one JSON response and progress goes to
stderr. Argument failures also return JSON. Human `--help` is ordinary text.

| Profile | Allowed input |
| --- | --- |
| `bseed-ts011f` | PM/non-PM Router/Client finalized OTA with matching manifest and role-matrix evidence |
| `gledopto-gl-sd-301p` | Exact pinned vendor stock or GLSD-ED-008 candidate bytes; artifact publication and format admission do not authorize a live flash or lift incident holds |
| `tuya-zigbee-switch` | `--board NAME --role router\|end_device`: the one released OTA for that board in `zigbee2mqtt/ota/index_<role>.json`, checked against its SHA-512 (Avatto, MHCOZY, Moes, BSEED switches, ...); BSEED sockets and the TS0726 3-gang are refused here |
| `bseed-ts0726` | This fork's released TS0726 3-gang (`iedhxgyi`) Router image from `index_bseed.json`, checked against its SHA-512 |
| `bseed-ts011f-pm-r19-recovery` | Exact pinned historical PM Router r19 incident fallback only |
| `generic-tlsr8258` | Other explicitly qualified TLSR8258 512 KiB layouts with an input SHA pin; cannot bypass the profiles above |

`boards --search TEXT` finds a board name from part of its Zigbee manufacturer
name, as Zigbee2MQTT shows it (for example `4rbqgcuv`). Indexed profiles download
the index from `main` unless `--index FILE` is given; the image itself is
always checked against the SHA-512 and size the index lists.
An offline index is trusted operator-supplied provenance, not a signed catalogue.
Reopening a bundle rechecks its board/role/source URL and native payload hash.
The TS0726 profile rejects an explicit board other than the 3-gang instead of
silently substituting it. Reset/pairing is a separately authorized device action.

Never flash `forward.bin`, an OTA container directly, a full-chip dump, a FORCE
wrapper or a guessed board/role. Use the finalized image admitted by `prepare`
or the prebuilt kit's validated bundle.

## Choose the recovery route

- **Verified in-place restore:** use a qualified bidirectional circuit to obtain
  matching full double reads. `inspect` compares both slots with known original
  firmware references. Use its `replaceSlot` only after the expected incident
  pattern passes. Supplying a baseline requires the selected unique enabled slot,
  `--full-slot`, staged verification, held activation and final verification.
- **TX-only restore:** reading is optional only where the device handover permits
  that route. Select it explicitly without a baseline. The default target is A;
  B requires a matching baseline proving A is disabled. Transmission remains
  unverified. A UART loopback does not establish bidirectional SWire capability.

Do not guess Table Left's slot from the OTA history. Its handover requires the
exact r21-enabled/r19-disabled readback pattern before replacing r21 in place.
Unexpected markers, mismatched references, corruption or ambiguous slots mean stop.

## Agent execution sequence

1. Run `plan --bundle BUNDLE --slot SLOT --full-slot --json` for the chosen area.
   Read numeric write/preserved ranges and `confirmationTokens` from that result.
   Do not infer addresses or tokens from a filename or firmware version.
2. Use a private `--session PATH`, a stable `--operation-id`, and the independently
   confirmed `--target-id` for each new JSON write session. Keep the same physical
   board attached. TX-only cannot electronically identify it.
3. For verified recovery: `stage` with the original `--baseline`, then a new
   no-reset full `read`, then `verify --session PATH --dump FILE --staged --json`.
4. Only after phase `VERIFIED`, use `activate --session PATH --operation-id ID
   --hold --confirm TOKEN --json`. Activation reasserts HALT without requesting a
   software reset. The verified route requires `--hold`.
5. Take a new no-reset full read and run `verify --session PATH --dump FILE
   --json`. `ACTIVATED_VERIFIED` proves bytes, not boot. Follow the incident's
   controlled low-voltage bench start, reassembly and independent Zigbee checks.
6. For an expressly selected TX-only route, `flash` may combine stage/activation
   without a baseline. Its result is `SENT_UNVERIFIED`, never hardware acceptance.

Use the exact command examples in the operator guide. Placeholder targets, ports,
paths and tokens must be replaced with established task facts. Keep dumps,
sessions, logs and profiles private and outside Git. The default state directory
is `~/.telink-swire`; no background service is required.

## Resume safely

- Poll `status --session PATH --json` for phase, progress and `nextActions`.
  `status --port PORT --json` shows shared ownership and lock state.
- After a lost response, repeat the **same session, operation ID and arguments**.
  A completed operation returns `replayed: true` without opening hardware.
  Do not invent a new operation ID to force the same write through.
- An interrupted write or failed verification blocks activation. Take a fresh
  no-reset full double read, inspect its actual marker, and use `reconcile` to
  compare the complete staged or activated image and preserved regions. It does
  not write. An old successful read cannot override a newer failed verification.
- `read --halt`, adapter loopback selftests and power-cycle activation are not
  part of a held recovery. Do not reset or reconnect the board between staged
  verification and activation. Stop on lost contact or uncertain power state.
- Never delete state files or change workdirs/session paths to bypass a stop.
  `close` requires an assessed reason and the token returned by status. It records
  a stop, changes no hardware and does not prove recovery. A fallback uses a new
  session after that decision and any incident-required fresh backup.
- A stale process lock requires explicit `unlock` with its nonce and a dead owner.
  Unlock preserves evidence and does not reconcile hardware. Never kill an
  unrelated process or remove a live owner's lock to continue.

## Interpret results and finish

Inspect `status`, `errorCode`, `details`, `retrySafe` and `sideEffectsPossible`.
Exit 0 means completion at the stated proof level. Exit 2 is an argument/read/
precondition failure; exit 3 means uncertain write; exit 130 means interruption.
Preserve referenced private diagnostic files. Do not automatically repeat a write
because an error is labelled retryable; completed-write replay is safe only with
the exact same operation ID and request.

Report the tool/image source SHA, exact target/profile/slot, private evidence
locations and what was actually observed. Distinguish `SENT_UNVERIFIED`, matching
flash bytes, and actual boot/relay/metering/network acceptance. Never infer boot
from OTA 100%, an online flag, a marker or a passing model. Never erase all flash,
factory reset, re-pair, toggle a load, publish OTA, retire a campaign lease or lift
a firmware quarantine as an automatic consequence of this tool succeeding.
