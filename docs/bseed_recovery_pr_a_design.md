# BSEED recovery PR-A implementation notes

Status: **draft, not wired into firmware and not suitable for deployment**.
The planned release covers the PM, non-PM and TS0726 Router/Client pairs. The
shared retry core currently owns one claimed operation, alternates the secure
current-channel and all-channel rungs, and exposes the pending start time.

## Ownership and SDK callbacks

The pinned SDK's `zdo_start_device_confirm_t` contains status, channel, PAN ID
and short address, but no operation token. A software generation guards only
callbacks genuinely associated with the generation that created them; giving
the current generation to an uncorrelated callback cannot protect against a
delayed duplicate. A fixed delay is not proof that callbacks have drained.
Until a documented SDK terminal/quiescence guarantee or equally safe mechanism
is established, do not connect this core to production retries or claim that
the adapter handles duplicate completions. The shared core now rejects a
mismatched internal generation token; the SDK adapter remains outstanding.

The pinned Telink SDK provides a one-shot `zb_rejoinReq()` and a separate
SDK-owned `zb_rejoinReqWithBackOff()`; both must not run at once. The new
driver must claim the shared core before calling the one-shot, including when
the SDK completes synchronously. A timeout marks the operation wedged but
does not clear the claim. Rejected requests and real terminal results release
the claim once; neither a parent MAC ACK nor an unrelated coordinator probe
proves that an in-flight SDK operation has finished.

The pinned `bdb_zdoStartDevCnf()` maps a failed start confirmation with status
`ZDO_NETWORK_LOST` to `BDB_COMMISSION_STA_PARENT_LOST`, whereas other rejoin
failures map to `BDB_COMMISSION_STA_REJOIN_FAILURE`. `PARENT_LOST` can also
arrive as a nonterminal indication during a recovery. Therefore the adapter
must distinguish actual one-shot completion from an unrelated parent-loss
indication rather than treating every `PARENT_LOST` as completion or ignoring
all of them. Exercise synchronous completion, rejections, delayed or missing
callbacks and this mapping against the pinned SDK contract.

The existing Router not-joined recovery tick itself does not retry, but the
application can start SDK backoff for an ordinary unjoined, non-factory-new
device. The true gap includes an orphaned one-shot that keeps the application
in JOINING. The replacement must remove competing recovery ownership from
both paths without disrupting factory-new commissioning, active OTA or local
relay and protection behavior.

## Persistent budget compatibility

Existing NVM item 70 stores a four-byte Router rescue record. Any expanded
record must preserve valid existing consumption when upgrading, handle the
HAL's fixed-size read checks, fail closed on unreadable or corrupt data, and
verify persisted writes. Connected-credit refill and a wedged-device slow
lane need distinct accounting: a disconnected wedge cannot earn CONNECTED
credit. With no persistent real-time clock, outages and lost uncheckpointed
uptime can delay a slow-lane reboot beyond 24 hours; do not claim a hard
wall-clock guarantee. Never erase Zigbee keys or application settings.

## Reporting and identity

The pinned SDK's reporting code sends via APS bindings. Holding the whole
reporting handler would also suppress non-coordinator recipients. Implement
destination-specific hold and latest-value flush only when it can preserve
the other binding, local-control and protection paths. Do not claim this
behavior until exercised against the actual reporting path.

`zigbee-herdsman` 10.9.1 updates cached Basic fields when it receives a
`genBasic` report or read response. A report still requires a working path,
does not prove the cached Zigbee role, and is not a substitute for the
runner's correlated identity and role verification.

## Scope and candidate identities

The GitHub-hosted allocator run `41993716294` selected PM `r20`/`cli23`
at `0x12053022`, non-PM `r19`/`c17` at `0x1102301e`, and TS0726
`rv3`/`cli5` at `0x11023012`. These are allocation results only, not sealed
images. Recheck the registry at release time. The old TS0726 `rv2`/`cli4`
tuple remains distinct and must be marked retired rather than reused. Run
exact-head GitHub CI, the PM and non-PM matrices, and Router and Client TS0726
builds before proposing artifacts. Hardware canaries remain staged, with the
living-room TS0726 flash scheduled with the user.
