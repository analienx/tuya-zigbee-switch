# BSEED four-variant OTA timing and firmware finalization — 2026-10-10

**Scope:** PM and non-PM TS011F-BS sockets, both Router and always-awake mains Client (EndDevice) roles. This is OTA orchestration and release-documentation hardening, not a new firmware source release. Firmware payload bytes and identities are unchanged.

## Why three minutes is not a universal timeout

Zigbee2MQTT officially defaults to 150,000 ms per-block REQUEST-INACTIVITY timeout, 250 ms block-response pacing, and 50-byte maximum block size. These are not complete-transfer timeouts. Increasing a server-side wait may help unusually slow devices, but does NOT repair a client-side ABORT, an RF downlink failure, a coordinator transmit failure or a bad firmware image.

| Device/history | Direct evidence | Conclusion |
|---|---|---|
| Bedroom non-PM Client, 2026-09-24 | Paced 48-byte, 1,200 ms, 300,000 ms block wait reached about 90.4% then stalled; the device remained alive and queried again. | Five minutes did not carry the transfer to completion. It is **not proven** that the configured timeout caused the underlying radio/app stall. |
| Bedroom non-PM Client, next attempt | Same pacing and 1,800,000 ms request wait retained the partial offset and reached 100% / upgrade-end. | Long wait is compatible with successful recovery, not conclusive proof of the preceding timeout cause. |
| KitchenLeft PM Client, 2026-09-20 | Nine requests repeated file offset 41150 over ~18 seconds, then device ABORT despite 600,000 ms server timeout. | Server timeout was not the mechanism of the client ABORT; investigate RF delivery, application, write/flash and power/parent. |
| HifiLeft PM Client, 2026-09-19 | Earlier custom Client transfers failed at different points with 150 and 600 s waits. | Multiple timeout choices failed; simply changing it is not a fix. |
| HifiLeft PM Client, 2026-10-04 | 32-byte, 1,200 ms pacing reached reported 0.59% at 21:20:25, returned update_error at 21:50:51 with 1,800,000 ms configured. | About 30 minutes without a **reported percentage change**. No actual imageBlockRequest gap has been measured, so three-minute safety cannot be concluded. |
| KitchenLeft PM Client cli12 acceptance | Reviewed 180,000 ms as a **private device-specific** campaign setting. | Not a PM-fleet default or a non-PM recommendation. |

Historical non-PM traces show MAC_BAD_STATE coordinator transmit failures and client downlink imageBlockResponse loss even as uplink imageBlockRequests arrived. Retained offsets and retries can make progress without solving the underlying RF/firmware cause.

### Preflash power-monitoring opt-out (2026-10-10)

Both PM Router and PM Client may defer fresh activePower measurement until
postflash if they are **physically unloaded**. Use the explicit private profile
`pm_preflash_load_proof=physically_unloaded` and operator
`--confirm-load-unplugged` during flash. Otherwise, the meter route
still requires a new raw ZCL power report; cached zero remains unsafe evidence.
PM firmware/role, converter quarantine, exact-image and network-OTA safeguards
are not bypassed. Physical disconnection is independent of the relay state.

### Timing decision policy

1. Do NOT globally set 180,000 ms for PM or non-PM. Keep each sealed campaign's reviewed profile value unless independently measured block-request intervals justify a different value. Bedroom FORCE's explicit 1,800,000 ms minimum stays.
2. Hifi's unproven experimental change from 1,800,000 to 180,000 ms has been **reverted to 1,800,000 ms** in its private profile. No Hifi flash was launched.
3. Keep the last viable block limit and response pacing during recovery; Hifi remains 32 bytes / 1,200 ms. Never accelerate both variables in the same attempt.
4. Gather transaction-matched private Zigbee2MQTT OTA debug: imageBlockRequest arrivals and offsets, imageBlockResponse send failures, repeated offsets, UpgradeEnd status, MAC_BAD_STATE and coordinator health. Separate **actual block silence** from a gap between percentage updates.
5. Use the read-only helper bseed_ota_activity_report.py or supervisor status. It labels timeout_sufficiency as undetermined until real block-level evidence is independently reviewed. Neither authorizes a new OTA.
6. Preserve generous distinct total transfer monitor time; do not confuse with per-block inactivity. Strictly increasing progress gates retries, and transport success with incomplete postflash must NEVER reflash automatically.

## Immutable four-variant release matrix

| Board | Role | Sealed candidate | OTA native type | Version |
|---|---|---|---:|---|
| PM b28wrpvx / TS011F-BS-PM | Router | 1.2.5-bseedr12 | 43556 | 0x12053019 |
| PM b28wrpvx / TS011F-BS-PM | Client | 1.2.5-bseedcli14 | 65024 | 0x12053019 |
| non-PM o1jzcxou / TS011F-BS | Router | 1.1.3-bseedr12 | 43555 | 0x11023016 |
| non-PM o1jzcxou / TS011F-BS | Client | 1.1.3-bseedc9 | 65026 | 0x11023016 |

Images retain existing sealed hashes, role types, OTA identities and provenance. Do not relabel or rebuild sealed files under the same version. If genuine firmware source repair becomes necessary, allocate new board-wide monotonically increasing versions, use GitHub-hosted CI to build/verify/seal both roles on each affected board, and require new canary acceptance.

**Offline sealed and GitHub CI validated does not mean hardware/fleet accepted.** The PM Client needs device-originated meter values and controlled zero/load tests, PM Router needs child/routing and meter retention, and non-PM sockets need relay physical-mode retention, stable Rx-on/rejoin and role checks. Every selected device needs exact image/hash, live installed build/role, fresh link, no conflicting OTA, serial campaign lock, postflash and soak evidence before proceeding to the next socket.

## Repository implementation

- Read-only per-transaction helper helper_scripts/bseed_ota_activity_report.py reports progress and terminal timings, without mistaking percent changes for real block requests.
- Supervisor status attaches exact-device/image activity diagnostics; progress history excludes other devices and candidates.
- Automated CI fixtures cover all four role/board combinations, target mismatch, stale or missing transaction, incomplete log, anomalous progress, hardware-acceptance separation, and valid two-line foreign JSONL regression.
- Documentation and skills point to the same operational policy. No household private MQTT files, OTA binaries or coordinator backups belong in public git.
- No device OTA, reset, join, coordinator update or firmware binary change was performed in this pass.

## Sources

- docs/bseed_nonpm_rc2_canary_20260925.md
- docs/bseed_kitchenleft_ota_abort_triage_20260920.md
- skills/bseed-zigbee-ota/SKILL.md (2026-09-24 paced-server incident)
- docs/bseed_socket_release_status.md and docs/bseed_pm_variant_matrix.md
- https://www.zigbee2mqtt.io/guide/usage/ota_updates.html
