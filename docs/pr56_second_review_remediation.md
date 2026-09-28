# PR 56 second-review remediation

The follow-up addresses three defects found after the first remediation:

- PM energy decoding omits the ZCL UINT48 invalid value instead of publishing an enormous energy total. Other attributes survive and the incoming message is not mutated. The pinned Herdsman/ZHC runtime test exercises report and read-response frames through valid, invalid and valid readings.
- Telink SDK Write and Write Undivided response-allocation failures free the parsed command and clear its application-dispatch pointer before returning. A narrow, fail-closed source transform creates a build-local SDK copy. The fault-injection test compiles the actual pinned handlers and uses the SDK's dispatch block; successful writes still dispatch when response transmission fails.
- Cross-role recovery allows stale cached build/interview metadata to reach one targeted interview after matching the target and live role. Rejoin evidence is explicitly only a `rejoin_candidate`, never verified identity. A fresh final inventory with the expected build, completed interview and live role remains mandatory before quarantine release.

Fresh firmware candidates replace the previously sealed source set:

| Target | Build ID | File version |
| --- | --- | --- |
| PM Router | 1.2.5-bseedr9 | 0x12053016 |
| PM Client | 1.2.5-bseedcli12 | 0x12053016 |
| PM return Router | 1.2.5-bseedr10 | 0x12053017 |
| Non-PM Router | 1.1.3-bseedr10 | 0x11023014 |
| Non-PM Client | 1.1.3-bseedc7 | 0x11023014 |
| TS0726 currentLevel Router | 1.1.9-bseedlv3 | 0x1102300F |
| TS0726 Client | 1.1.8-bseedcli3 | 0x1102300F |

The canonical allocator is checked on public Actions, including shared board versions. Existing sealed identities and published OTA indexes are preserved. Tests and native role matrices must pass on the exact candidate SHA; hardware acceptance remains pending. No device operations are part of this remediation.

## Exact-source CI and sealed socket artifacts

Source: `11b26c48a07acbee0ec87bf3adbe5cb284ebe140`. All 947 tests passed on [GitHub-hosted Actions](https://github.com/analienx/tuya-zigbee-switch/actions/runs/36377097782), including real converter decoding, SDK allocation fault injection and complete offline recovery-helper flows.

The [finalizer](https://github.com/analienx/tuya-zigbee-switch/actions/runs/36377097919) verified the native headers, payload integrity, build IDs, independent rebuild hashes and exact-source public workflow results. Its registry proposal is committed verbatim; all older entries are preserved. Registry proposal SHA-256: `feae2ebf7d60df1f91c23a65e81615a7a769edf47a277b2bec119a7eb182c5f7`.

| Native socket image | SHA-256 |
| --- | --- |
| 1.2.5-bseedr9 | `b371ac3cb39bb28fb416eb3617d8af5d383d533198c1c1623ab05830a054253e` |
| 1.2.5-bseedcli12 | `64b1eea15dd3f682a0aacd245c3c9802565de253dfda6c91252b428a1ad11d44` |
| 1.2.5-bseedr10 | `8bded61cd90e16b4baae5634265a5e907fa3a0f53411e1fbe3ce2c2bab47b55d` |
| 1.1.3-bseedr10 | `c2bb21dee350fd375586029eefb03f85791b0941bd386882a0b8653bb15bdb96` |
| 1.1.3-bseedc7 | `f0a499ea9e351265cb47fa26717f00246450cecaf727adad3298552bb1d8a94f` |

PM return transport wrapper SHA-256: `92e20866318c77052216f2dabc2f68ed022ed86b36ff66aa45f54996a0fb1f9b`. It remains experimental: `applyPathFix: false`, `deploymentReady: false`. The successful offline checks do not establish boot, relay operation, retention, radio behavior or soak acceptance on hardware. The registry-only follow-up must pass the same public CI against these sealed bytes before review completion.
