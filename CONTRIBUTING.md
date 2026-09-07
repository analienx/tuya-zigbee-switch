# Contributing

Thanks for improving this firmware project. Changes are welcome when they preserve the shared architecture, keep hardware-specific behavior explicit, and do not weaken the release-safety boundary.

## Before you start

- Read [README.md](README.md), [docs/bseed_unified_v8.md](docs/bseed_unified_v8.md), and the relevant device entry in [device_db.yaml](device_db.yaml).
- For new hardware, start with the [porting guide](docs/contribute/porting.md).
- Do not assume two visually identical Tuya/BSEED products share the same MCU, GPIO mapping, metering IC, or OTA identity.

## Development principles

1. **One common core, explicit target guards.** Prefer reusable behavior in the shared firmware and keep device-specific assumptions behind a board/identity guard.
2. **Preserve backwards compatibility deliberately.** NVM/config migrations must be bounded, idempotent, and safe for existing installations.
3. **Do not weaken safety checks to make CI green.** Fix the underlying mismatch or update a stale test to assert the stronger current invariant.
4. **Keep deployable artifacts authoritative.** Local builds are diagnostic only. Public/deployable BSEED firmware must be produced by GitHub Actions.
5. **Do not mutate stock hardware casually.** Stock→custom testing requires exact identity matching and an explicit hardware-test plan. A conversion path is not assumed reversible.

## Testing

Before opening a pull request, run the relevant host-side tests when possible. The repository CI additionally validates formatting, image-type ownership, firmware contracts, and the BSEED release path.

BSEED release-affecting changes must preserve:

- normal CI;
- pinned real-Telink TC32 builds;
- same-source PM and TS0726 builds;
- OTA header/manifest validation;
- PM reproducibility;
- stock-wrapper payload identity when conversion packaging is affected.

## Pull requests

Keep pull requests focused. Describe:

- the problem being solved;
- exact hardware/board identities affected;
- runtime behavior changed;
- migration or compatibility impact;
- tests added/updated;
- whether any hardware mutation is required.

Do not attach or commit locally-built firmware as an authoritative release candidate. If a change needs a deployable artifact, extend or use the appropriate GitHub Actions workflow.

## Device database changes

When adding or changing a device entry, verify at minimum:

- manufacturer/model identity;
- MCU family and MCU;
- router/end-device role;
- custom firmware image type;
- stock manufacturer/image type when conversion is supported;
- exact configuration string and GPIO assumptions.

Image-type collisions are treated as release-blocking defects.

## Documentation

User-visible behavior changes should update the relevant documentation in the same pull request. If a change affects installation, conversion, target identity, or safety, update the README or BSEED V8 guide as appropriate.
