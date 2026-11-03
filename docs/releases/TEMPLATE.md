# Firmware release note template

> Copy to a new, dated file. **Do not** edit older release notes to make historical candidates look current; update [firmware availability](../bseed_socket_release_status.md) instead.

# Candidate or published release name · YYYY-MM-DD

**Channel:** Proposed / CI-passed / sealed candidate / device canary / hardware accepted / published OTA

> [!CAUTION]
> Publication and deployment scope, plus any irreversible recovery risk.

## At a glance

| Board / role | Build | Native FILEVER | Image type | Status |
| --- | --- | --- | --- | --- |
| Exact manufacturer/model | — | — | — | — |

## What changed

### User-facing behavior
Explain the observable improvement and what does **not** change.

### Stability and safety
Explain NVM, relay, metering, parent/route, OTA and transition consequences.

## Evidence

- Exact immutable source commit:
- GitHub-hosted CI runs and paired role matrices:
- Sealed artifact name, SHA-256 and registry record:
- Device-specific hardware evidence (private identifiers omitted):
- Existing rollback/recovery eligibility:

## Compatibility and migration

Exact boards, OTA image types, version progression, converter dependencies, stock conversion restrictions and any incompatible state.

## Open gates and limitations

Which tests are *not* complete? What blocks wider release?

## Publication outcome

What changed in the public OTA index, if anything? If nothing, state **not published** prominently.
