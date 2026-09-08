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

## Recommended user experience: one normal index + temporary transition indexes

The OTA override is global to Zigbee2MQTT, so a permanent "Client channel" or "Router channel" is not ideal for homes that intentionally mix both roles. It would keep offering role changes to other matching devices.

The intended BSEED distribution model is therefore:

- `index_bseed.json` — the normal/default BSEED index. After Client promotion it contains normal **Router→Router** and **Client→Client** updates, plus the supported stock→custom conversion path. It contains **no cross-role transitions**.
- `index_bseed_to_client.json` — temporary role-change index containing **Router→Client** transition images.
- `index_bseed_to_router.json` — temporary role-change index containing **Client→Router** transition images.

The two transition indexes are published only after the Mains Client hardware canary is accepted. Until then, `index_bseed.json` remains the only production BSEED index and contains Router firmware only.

This lets different BSEED devices in the same Zigbee network intentionally use different roles without permanent "wrong role available" notifications. The user's role selector is a temporary OTA-index change, not a runtime firmware attribute.

## Switching roles in Zigbee2MQTT

Once the transition indexes are promoted:

1. Keep `index_bseed.json` for ordinary updates.
2. When changing one device's role, temporarily set the OTA override to `index_bseed_to_client.json` or `index_bseed_to_router.json`.
3. Restart Zigbee2MQTT so the temporary index is loaded.
4. **Enable permit-join before starting a cross-role OTA.**
5. Check for OTA updates on the device you intend to change and install the offered transition image.
6. The firmware records the new device type and factory-resets the Zigbee stack/network state for the role transition.
7. Application NVM is not deliberately cleared by that role-change path, so device configuration/calibration is preserved where its own schema permits it.
8. Zigbee stack state is reset, including network membership and the binding table. Allow the device to rejoin, then interview and reconfigure it.
9. **Recreate the device's direct Zigbee bindings after the role change.** Stored application policy may survive, but coordinator/target binding relationships belong to Zigbee stack state and should be treated as lost across the transition.
10. Restore the normal `index_bseed.json` OTA URL and restart Zigbee2MQTT again.

Normal updates inside the same role use `index_bseed.json` and do not need the role-transition wrapper, rejoin, or binding recreation.

## OTA identities

The roles intentionally use separate custom image types.

| Target | Router image type | Mains Client image type |
|---|---:|---:|
| BSEED TS011F-PM | `43556` | `65024` (`0xFE00`) |
| BSEED TS0726 | `45577` | `65025` (`0xFE01`) |

Cross-role wrappers use the **currently installed role's image type in the outer OTA header** while carrying the exact compiled payload of the destination role. This lets the currently installed firmware accept the transition without pretending both roles are the same OTA identity.

Because the normal index can contain both native Router and native Client entries without image-type collision, mixed-role deployments continue receiving ordinary updates from the same `index_bseed.json`.

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

Current Mains Client implementation candidate:

`68c90171a24f6d24dfbfb4e3979fa0acbfe3ab6b`

Software status on that implementation SHA:

- normal tests: pass;
- lint: pass;
- image-type collision guard: pass;
- real pinned TC32 PM + TS0726 client builds: pass;
- Router byte-regression gate: pass;
- second-build reproducibility: pass;
- Router→Client wrappers: pass;
- Client→Router rollback wrappers: pass;
- role and binding contract tests: pass.

Later documentation-only commits may move the branch head without changing those firmware bytes; CI still rebuilds the exact branch head and must retain the same router/client contracts.

The Mains Client remains **hardware-canary pending**. The public production landing page should describe it as a validated candidate until the first real-device Router→Client→Router campaign is accepted. After that canary, native Client images can join the normal BSEED index, the two temporary transition indexes can be published, and the status can be promoted to supported.
