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
