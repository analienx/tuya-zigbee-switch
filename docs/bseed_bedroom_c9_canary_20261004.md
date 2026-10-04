# Bedroom Client c7 to c9 readiness — 2026-10-04

The owner selected the newest sealed non-PM Client for
BedroomSocketCabinetRight, IEEE `0xa4c13824a7005afb`, confirmed the appliance
is unplugged, and explicitly accepted the exact no-disassembly continuation.
Recovery if boot fails remains unproven. Standing owner authorization covers
qualified newer sealed Client updates on this same socket; do not repeat a risk
or permission question merely because that version changes.

The exact source is `1.1.3-bseedc7`, EndDevice, `o1jzcxou / TS011F-BS`.
Destination is `1.1.3-bseedc9`, version `0x11023016`, type 65026, SHA-256
`e52701b83ea7528ed0e9b26cb9e4da0679d125c259c8141e62207c6460738fff`.
The recovery gate adds only this source/destination/hash tuple on the existing
Bedroom IEEE. Other targets, roles, boards and changed hashes still fail.
Physical appliance-unplugged confirmation and 32-byte blocks remain required.
No firmware source, binary, identity registry or fleet index changes.

The initial read-only qualification failed its cross-host wall-clock ordering.
The backend recorded a correlated endpoint-2 OnOff read response in 0.101 s;
HA's clock was approximately 0.73 s behind the Windows caller. Requiring the
backend timestamp to be no earlier than the caller's timestamp rejected that
fresh read. This is not evidence of device downlink loss.

The validator now matches the installed backend's existing five-second
issued-at age/skew bound. It still requires the unique request ID, exact IEEE,
endpoint/cluster/attribute, selected ZCL transaction, readResponse, expected
relay value, no error, ordered backend timestamps and <=12 s backend latency.
Production captures local receipt time and rejects future responses beyond
the same five-second bound. The subprocess deadline and local link-gate
latency checks remain independent. Skew outside the bound fails closed; no
host clock, backend extension or Zigbee service is changed.

Regression coverage includes positive small skew in both directions and
negative stale/future/reversed/overlong/foreign-request evidence. Recovery
coverage rejects unloaded-confirmation omission, wrong source, destination,
hash, role, IEEE and PM mode. Run all validation on public GitHub-hosted
Actions at the exact tooling SHA; no local test result is acceptance evidence.

Before transfer, recheck unchanged source, relay OFF/follow_state, closed join,
no network-wide OTA, prior reconciliation, backups and fresh qualify. Submit
one canonical same-role flash with durable output and LIVE_STATUS monitoring.
Keep that monitor and the private HTTP server alive when assistant polling
stops. Transport completion remains separate from installed-build, role,
relay/bindings and network acceptance. Other household sockets remain separate
serial candidates and cannot be launched while this campaign owns the network.
