# Repository agent instructions

For Zigbee firmware flashing, OTA, device role changes, stock-to-custom conversions and recovery, **read `skills/bseed-zigbee-ota/SKILL.md` first** and follow its safety and evidence gates. Consult `docs/bseed_targeted_ota_runner.md` and the latest device-specific incident record before using live credentials or modifying hardware. Use local, profile-driven tooling rather than inventing ad hoc one-off scripts.

Do not interpret Zigbee2MQTT OTA `status:ok` or 100% as a confirmed boot. Never commit a household MQTT configuration, private network address, SSH key, OTA firmware binary, coordinator backup, private campaign profile or runtime trace. A new OTA cannot bypass a failed or unverified prior campaign's lock merely by switching to another workdir; check network-wide OTA activity and actual postflash state first.

## Canonical Home Assistant diagnostics and Zigbee device identification

For any live Home Assistant access, Zigbee2MQTT NWK/address mapping or route-error investigation, load the **single canonical** [Home Assistant read-only skill](https://github.com/analienx/config/blob/main/skills/home-assistant-readonly/SKILL.md) from `analienx/config` (main). It provides the existing SSH alias, a host-key-verified Paramiko fallback for Windows OpenSSH exit-255 failures, and the reusable `ha_readonly.py` live inventory helper. Keep implementation and credentials in the canonical location; do not copy the helper or SSH settings here. This does not authorize Zigbee firmware flashing, HA mutations or bypass of this repository's own safety/deployment rules.

## Public-repository execution policy

This is a **public GitHub repository**. All repository execution — build, test, lint, verification, packaging, reproducibility checks, benchmarks, and acceptance evidence — must run on **GitHub-hosted Actions runners** from the exact candidate SHA. Never run those workloads in local WSL, on a personal workstation Executor, or on a self-hosted runner. There is no local/WSL “iteration aid” exception.

A local checkout may be inspected or edited to prepare changes, but local results are non-authoritative and must not be used as validation evidence. Local host access is reserved only for a separately authorized hardware/live-device action that cannot run on GitHub-hosted infrastructure; that exception never authorizes local repository verification. If local/WSL execution residue is discovered, stop the local Executor, remove only executor-owned temporary artifacts, preserve repository work, and reproduce any claimed result on GitHub-hosted CI before acceptance.

## Required BSEED shared PM firmware gate

Before releasing any BSEED TS011F-BS-PM source change, run `make bseed/pm-matrix` on a GitHub-hosted Actions runner in a clean Linux checkout with the Telink toolchain. This builds and validates the Router and mains Client from the exact same commit, not merely host tests. Read `docs/bseed_pm_variant_matrix.md` and `skills/bseed-zigbee-ota/references/build-and-verify.md`. Keep role-specific OTA identities and live hardware gates separate; a green offline matrix never authorizes flashing.
