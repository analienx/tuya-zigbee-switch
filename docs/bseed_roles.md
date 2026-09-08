# BSEED firmware roles: Router vs Mains Client

BSEED mains-powered targets can use two different Zigbee roles built from the same firmware source tree:

| Role | Zigbee topology | Radio while idle | Routes other devices | Intended use |
|---|---|---|---|---|
| **Router** | Router | On | **Yes** | Default choice when the device should strengthen the Zigbee mesh. |
| **Mains Client** | End-device topology | **Always on** | **No** | Mains-powered leaf device that remains immediately reachable but never becomes a routing dependency. |

The Mains Client is **not** the normal sleepy/battery EndDevice build. It links the Telink end-device stack, keeps `RxOnWhenIdle` enabled, advertises mains power, does not enable power-management sleep, and does not run Poll Control.

## Why changing role requires another firmware install

Router vs Mains Client is a build-time Zigbee stack choice:

- Router links `libzb_router`;
- Mains Client links `libzb_ed` with the always-awake mains-client contract.

It therefore cannot safely be exposed as an ordinary runtime attribute or device-configuration toggle. Switching roles means installing the image for the desired role.

This is deliberate: the Zigbee stack, node descriptor and routing behavior all agree on one role from boot.

## Recommended user experience: role channels

Do **not** put Router and Mains Client candidates into one mixed OTA index. A mixed index can create competing candidates for the same currently installed image identity and makes the selected role ambiguous.

The intended BSEED distribution model is instead:

- `index_bseed.json` — stable/default BSEED Router channel and stock conversion path;
- `index_bseed_client.json` — choose Mains Client; contains Router→Client transition images plus normal Client→Client updates;
- `index_bseed_router.json` — choose Router; contains Client→Router transition images plus normal Router→Router updates.

The two role-selection indexes are published only after the Mains Client hardware canary is accepted. Until then, `index_bseed.json` remains the only production BSEED index.

In other words, the user's role selector is the **OTA channel URL**. No force-flash guessing and no manual selection between two entries with the same current identity is required.

## Switching roles in Zigbee2MQTT

Once the role channels are promoted:

1. Choose the target role by setting the corresponding BSEED OTA index URL.
2. Restart Zigbee2MQTT so the new index is loaded.
3. **Enable permit-join before starting a cross-role OTA.**
4. Check for OTA updates and install the offered transition image.
5. The firmware records the new device type and factory-resets the Zigbee stack/network state for the role transition.
6. Application NVM is not deliberately cleared by that role-change path, so device configuration/calibration is preserved where its own schema permits it.
7. Allow the device to rejoin, then interview and reconfigure it in Zigbee2MQTT.

Normal updates inside the same role do not need the role-transition wrapper.

## OTA identities

The roles intentionally use separate custom image types.

| Target | Router image type | Mains Client image type |
|---|---:|---:|
| BSEED TS011F-PM | `43556` | `65024` (`0xFE00`) |
| BSEED TS0726 | `45577` | `65025` (`0xFE01`) |

Cross-role wrappers use the **currently installed role's image type in the outer OTA header** while carrying the exact compiled payload of the destination role. This lets the currently installed firmware accept the transition without pretending both roles are the same OTA identity.

## Mains Client correctness contract

The client build is specifically hardened against assumptions inherited from ordinary sleepy EndDevice firmware:

- receiver stays on while idle;
- mains power is advertised in the Zigbee power descriptor;
- `PM_ENABLE` is not enabled;
- Poll Control is compiled to no-op behavior for `BSEED_MAINS_CLIENT` and is not added as an endpoint cluster;
- direct binding uses the same shared `switch_cluster.c` implementation as Router firmware rather than a client-only binding fork;
- fresh Mains Client switch configs default to **RISE / press-start** binding so very fast presses do not depend on release/SHORT detection;
- Mains Client debounce defaults to **20 ms** instead of the generic 50 ms;
- persisted user switch/binding settings remain authoritative;
- output On/Off and Level Control clusters required for direct binding remain present;
- production Router binaries are rebuilt and required to remain byte-identical when client-only code changes;
- both client targets are rebuilt twice and required to be byte-identical in GitHub Actions.

These checks address the known client-specific failure modes we have identified. They do not justify claiming that any firmware is literally bug-free.

## Validation status

Current Mains Client candidate source:

`68c90171a24f6d24dfbfb4e3979fa0acbfe3ab6b`

Software status on that exact SHA:

- normal tests: pass;
- lint: pass;
- image-type collision guard: pass;
- real pinned TC32 PM + TS0726 client builds: pass;
- Router byte-regression gate: pass;
- second-build reproducibility: pass;
- Router→Client wrappers: pass;
- Client→Router rollback wrappers: pass;
- role and binding contract tests: pass.

The Mains Client remains **hardware-canary pending**. The public production landing page should describe it as a validated candidate until the first real-device Router→Client→Router campaign is accepted. After that canary, the two role-selection indexes can be published and the status can be promoted to supported.
