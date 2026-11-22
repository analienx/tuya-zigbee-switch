# Balcony non-PM Client c17 → c19: exact software successor

This source change permits only the explicitly approved non-PM
`BedroomSocketBalcony` Client-to-Client OTA from `1.1.3-bseedc17` to
`1.1.3-bseedc19`, using the immutable c19 candidate SHA256.
It is **not** a generalized no-readback recovery policy.

## Historical lock proof

The earlier November 15 OTA left a local failure record after network
ownership was isolated and released. A later November 16 OTA independently
installed Client c17, completed a clean postflash check and released its
network lease with software-only verification. The original unsuccessful
work lock remains untouched and negative evidence is not reclassified.

The private target configuration must name four original immutable sources:
the old work lock, successor work lock, successor software-release evidence
and successful postflash evidence. Every identity, digest, role, transaction
and chronology is revalidated against fresh Zigbee2MQTT identity. A stale
or mismatching source refuses the new canary.

## Operational gates that remain mandatory

- Exact IEEE, board, role, source build and SHA256-correct sealed c19 image.
- Owner's standing irreversible-brick risk grant is recorded, **not**
  represented as tested SWire readback or guaranteed recovery.
- Same-role only, 48-byte OTA blocks paced at least 1800 ms, max three
  coordinator slots and no automatic global rejoin.
- Keep the relay's actual OFF state; `auto_relay_off=false` prohibits a
  relay command solely for OTA.
- **Fresh human confirmation of physical load disconnection** is still
  required before any OTA; relay OFF or a prior-day statement is not proof.
- Existing active OTA leases, historical source mismatch, unsafe topology
  and failed preflight remain hard stops.
- A successful OTA transaction is not hardware acceptance. Check installed
  build, EndDevice role, relay retention, routes, binding and stability.

Run repository verification only on GitHub-hosted CI for the exact SHA.
No new hardware write is authorized by a documentation or CI-only result.
