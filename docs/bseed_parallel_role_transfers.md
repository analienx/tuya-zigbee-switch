# Mixed-role parallel OTA

The shared per-IEEE transfer pool accepts any combination of the **already-supported** firmware routes: Router → Router, Client → Client, Router → Client and Client → Router. Existing firmware/board, image hash, exact target, relay, metering and version-policy gates remain in effect. This is not authorization for unsupported hardware or firmware images.

## Planning and execution

Opt a device into the 3-slot pool in the private site configuration, for example:

```json
{
  "devices": {
    "SocketA": {
      "parallel_ota_max": 3,
      "concurrent_ota_ieees": ["0xa4c1380000000002"],
      "response_delay_ms": 1800
    },
    "SocketB": {
      "parallel_ota_max": 3,
      "concurrent_ota_ieees": ["0xa4c1380000000001"],
      "response_delay_ms": 1800
    }
  }
}
```

Use the actual exact IEEE addresses and reciprocal peer allowlists. The planner sets `parallel_role_transition=true` for an eligible role-changing profile, disables automatic source rejoin during parallel transfer, and retains the planned post-transfer rejoin mode. Two devices can use the three-slot pool without occupying the third slot. The previous two-slot protocol remains unchanged.

Run a mixed queue with explicit target roles, or run-many with the prepared profiles and exact IEEE confirmations. The mixed queue checks live topology before grouping transfers. A Router that is a parent of another selected device cannot safely reboot alongside that device; the queue falls back to serial operation for such a dependency. Direct split role-transfer mode checks the target and potential peers again before sending the image.

## Separate transfer and recovery

- `transition-transfer` performs the existing checked, preserve-relay OTA transfer and records the original transaction; it does not open a join window.
- After the worker group stops, run-many invokes `transition-finalize` for role-changing members, one at a time, establishing Routers before converting Routers to Clients where possible.
- A separate coordinator recovery marker, guarded by the same admission mutex, prevents overlapping rejoin windows or new transfer admission while recovery is in progress. The exact target campaign and network lease must match a confirmed successful transfer.
- Each per-device lease and original journal are retained. No fabricated transfer success, foreign lock retirement or relay-OFF command is introduced.
- The combined single-device transition remains available for serial fallback. A split role-transfer worker's `transfer_complete` result means that recovery/acceptance is still outstanding, not that the new role is accepted.

An unresolved legacy `ota_running` lease, even if a UI showed 100%, is **not** silently converted to success or ignored. Reconcile the old transaction using the existing supported operator workflow. All hosts participating in mixed-role campaigns must run the new coordinator admission and recovery code; do not mix old and new runner binaries during recovery. Physical role changes and unexpected reboot interruptions cannot be guaranteed by offline tests.

## Verification

Run `python -m pytest -q tests/test_bseed_parallel_role_transfers.py tests/test_bseed_dual_ota_lease.py tests/test_bseed_ota_autopilot.py tests/test_bseed_ota_campaign.py tests/test_bseed_socket_migrate.py tests/test_bseed_concurrent_ota_gate.py`. New tests cover the four role combinations, simultaneous three-member ownership, serialized recovery, recovery-vs-admission exclusion, exact token validation, unresolved transfer refusal, and group recovery order.
