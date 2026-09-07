## Summary

<!-- What problem does this solve? Keep the scope focused. -->

## Affected targets

- [ ] Shared core
- [ ] BSEED TS011F PM (`OUTLET_BSEED_PM_TS011F`)
- [ ] BSEED TS0726 (`SWITCH_BSEED_TS0726_3GANG`)
- [ ] Other / upstream-compatible target

Exact manufacturer/model identity, if hardware-specific:

## Runtime and compatibility impact

<!-- Describe behavior changes, NVM/config implications, OTA identity changes, or state migrations. -->

## Validation

- [ ] Relevant host tests added/updated
- [ ] Existing CI expectations preserved or deliberately strengthened
- [ ] Firmware image-type ownership checked
- [ ] Documentation updated for user-visible behavior
- [ ] No locally-built deployable artifact is being treated as authoritative

For BSEED release-affecting changes:

- [ ] Real pinned-Telink build required
- [ ] PM + TS0726 same-source regression considered
- [ ] Reproducibility considered
- [ ] Normal/from-Tuya payload identity considered if OTA conversion packaging changed

## Hardware safety

- [ ] No stock device mutation required
- [ ] Hardware testing is limited to explicitly identified targets
- [ ] Relay/power end state is defined where relevant
- [ ] Recovery path is documented before any risky hardware mutation

## Evidence

<!-- Link CI runs, logs, measurements, screenshots, or hardware evidence as applicable. -->
