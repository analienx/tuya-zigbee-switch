# BSEED — fast fleet LED brightness

Change **only the indicator LEDs' brightness** on Zigbee2MQTT sockets with
recent, compatible BSEED custom firmware. This is a settings change, **not OTA**.
The relay/physical output and the existing appliance state are never written.

## Quick operator command (ZephyrusG16)

A private launcher with the MQTT and network-lock paths is installed at:

`C:\Workspace\scratch\bseed-latest-20261120\led_brightness.py`

From PowerShell or Command Prompt:

```text
python C:\Workspace\scratch\bseed-latest-20261120\led_brightness.py --percent 30
```

Other brightness:

```text
python C:\Workspace\scratch\bseed-latest-20261120\led_brightness.py --percent 40 --jobs 3
```

Single-device test or read-only preview:

```text
python C:\Workspace\scratch\bseed-latest-20261120\led_brightness.py --percent 30 --only BedroomSocketCabinetL
python C:\Workspace\scratch\bseed-latest-20261120\led_brightness.py --percent 30 --dry-run
```

Default 2 concurrent workers; set `--jobs 3` when coordinator utilization
allows it. No software install or configuration changes are needed on the
machine beyond the existing Python/paho-mqtt/PyYAML environment.

## Direct portable command

```text
python helper_scripts/bseed_led_brightness.py --percent 30 --mqtt-config PRIVATE_Z2M_CONFIGURATION.yaml --broker PRIVATE_MQTT_HOST --network-lock-dir PRIVATE_BSEED_NETWORK_LOCK_DIRECTORY --evidence-dir PRIVATE_EVIDENCE_DIRECTORY --jobs 2
```

The paths/credentials stay in the private launcher and are never committed.
The tool discovers fresh `bridge/devices`, uses the exact BSEED PM/non-PM
board manufacturers, checks current firmware versions and the converter's
endpoint-qualified `led_brightness_switch` property. It selects the newest
**installed** compatible build per PM/non-PM Router/EndDevice family. Supports
future custom version increments automatically once the converter advertises
this property. Use `--include-compatible` to include older versions *only*
when they actually implement the brightness attribute.

## Guarantees and limitations

- The only allowed MQTT operation is `<friendly_name>/get` or `/set` with
  JSON key `led_brightness_switch`. Never write relay or indicator on/off
  state, OTA, `state`, `state_relay`, or `relay_physical_mode`.
- Refuses stock firmware, incompatible/revision-too-old custom firmware,
  unrecognized models/manufacturers, non-interviewed devices, duplicate IEEE,
  unavailable Zigbee2MQTT bridge, and any socket holding an OTA network lease.
- Requires a fresh GET before changing an outlet. Successful MQTT SET means
  only broker admission; **two separately requested fresh GET readings** of
  the requested value establish verified completion.
- Stale/optimistic immediate reports may show the preceding value; only fresh
  post-write GET results count. An offline/nonresponsive socket is marked
  skipped, not successful. A second targeted attempt is safe.
- Bounded concurrency `1..3` and no automatic write retries. Another OTA
  can continue, but prefer `--jobs 1` if coordinator health is strained.
- Each run writes a timestamped JSON status/evidence file under the private
  `--evidence-dir`; failures are visible and reported as nonzero exit code.

## First verified application — 22 November 2026

16 eligible sockets were identified on PM `r24/cli27` and non-PM `r21/c19`.
The initial 30% batch confirmed 15 and skipped `BedroomSocketCabinetL`
because its GET timed out. A subsequent exact-target retry using this faster
tool returned two independent readings of **30**, completing **16/16**.
Excluded: manufacturer firmware sockets, legacy non-PM `c17`, and any
still-active stock-to-custom OTA (including HallMain during the initial run).
