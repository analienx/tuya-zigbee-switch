# BSEED socket anti-brick gate — 2026-09-21

Scope: verified BSEED TS011F PM (`b28wrpvx`) and non-PM (`o1jzcxou`) board variants, both Client and Router. This worktree is **offline only**; no OTA, relay commands, resets or device reconfiguration were performed.

## Incident evidence (local times, Europe/Prague)

- Left ran PM Client `1.2.5-bseedcli6`. Archived Z2M logs show Left announcements after Right's Router OTA completed at 22:18 on September 20; Left announced again at 23:28 and on September 21 at 06:07:33, 06:07:49 and 06:07:59.
- Two September 21 automatic Z2M configuration attempts timed out; the third succeeded at 06:08:09. The last verified device announcement in the archived window was 06:07:59. A Basic-cluster ping at 08:54:22 timed out at 08:54:32.
- The subsequent MQTT state fields may be cached; they are **not** evidence of fresh device-originated metering. The reported dark LED may also reflect the persisted `network_led_switch=OFF`. The separately reported non-responsive physical button remains unexplained.
- These logs neither prove a device-config write near failure nor establish firmware or electrical failure as root cause. Keep the private log copies out of git: a dedicated private Zephyrus evidence directory.

## Hardening implemented on the candidate branch

- Exact board pin-map identity is validated at config write **and boot** for the two verified socket variants. An incompatible stored map is rejected in RAM and the compiled default is used without overwriting the suspect NVM. The TS0726 guard is also applied at boot; generic upstream boards retain the existing structural validation.
- Both PM Router/Client builds automatically include their dedicated PM guard; non-PM Router and Client release scripts explicitly compile the non-PM guard. PM and non-PM must not share a pin-map value.
- Stored switch settings reject zero/very short long-press durations and invalid switch/relay/binding modes; relay settings sanitize invalid startup mode, indicator mode and indicator-state values. The `PREVIOUS` startup enum is 0xFF and requires explicit allowlisting, not a `> PREVIOUS` comparison.
- The host test builds PM/non-PM × Router/Client, exercises canonical and malformed pin maps, and asserts fallback preserves the original NVM record. Normal configuration and boot-continuity suites are rerun alongside it.

## Still blocked before any firmware release
- **Do not flash these candidate changes onto production sockets.** They are not byte-matched to the image installed on Left and have not passed native Telink toolchain builds, OTA layout verification or a controlled physical canary.
- A failing PM NVM migration currently schedules a reboot before GPIO initialization; persistent flash-read/write failure could cause a boot loop. Audit migration error handling and independently verify flash layout/rollback before altering that path. A reboot loop is not fixed merely by sanitizing the pin map.
- Telink watchdog starts after `app_init()`. Review boot-time watchdog feasibility against the SDK and NVM operation durations; starting it earlier without testing could itself cause a restart loop.
- Role-transition NVM resets, parent-loss recovery, metering calibration/protection, OTA interruption/power loss and retention of device-specific data need isolated failure-injection and hardware tests. Test both Client and Router separately, plus shared boot code. No automatic retry or live device cycling is authorized by this document.
- Truly resilient OTA requires a separately validated recovery/rollback stage that survives a broken application. Application-level guards cannot make a corrupted bootloader, failed flash chip, damaged power supply or incompatible image impossible to brick.

## Reproduce host validation

In WSL with the existing BSEED pytest virtual environment, from this worktree:

```bash
make -s -C src/stub build build_end_device
/home/analienx-agent/bseed-nvm-test-venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_bseed_socket_antibrick.py tests/test_bseed_config_guard_release.py \
  tests/test_config_resource_guard.py tests/test_device_config_parser.py \
  tests/test_device_config_transport.py tests/test_boot_continuity.py
```

Observed in this worktree: **81 passed**, including corrupt saved switch/relay settings in all four builds. This is host-level validation, not physical firmware acceptance. The private incident logs and any credentials/device IDs must remain outside the public repo.
