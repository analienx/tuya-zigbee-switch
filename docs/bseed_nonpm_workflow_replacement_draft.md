# Proposed non-PM CI replacement — APPLIED as finding B remediation

Current source status: the draft fixes are applied in PR #56. See
[`docs/bseed_issue55_finalization.md`](https://github.com/analienx/tuya-zigbee-switch/blob/codex/issue-55-finalize-remediation/docs/bseed_issue55_finalization.md)
for the superseding implementation and GitHub-hosted-only validation path.
Pending-source/approval statements below are historical. Run no repository
build, test, lint, verification or sealing on a local machine.

Status: the replacement below was applied to
`.github/workflows/bseed-nonpm-client-canary.yml` with an exact-head guard
and pipefail-correct matrix logging. This draft is retained as the review
record; the workflow file is authoritative.

This is a reviewable draft, not an active workflow. Automatic approval review
blocked replacing `.github/workflows/bseed-nonpm-client-canary.yml` because that
removes a persistent CI control. The existing workflow is unchanged.

The old job recompiles historical Router identities and compares changed source
against an obsolete binary. The replacement keeps immutable historical-byte
verification, and builds fresh r9/c6 identities from the exact commit. It cleanly
rebuilds both roles and compares all binary/OTA files, including wrappers.
Full host tests and converter tests remain required in the separate `test` job.
Nothing here publishes to the OTA index or contacts a device.

After approval, replace the existing workflow contents with the following and
update `test_bseed_nonpm_client_canary.py` to require historical identity
verification, current matrix execution and evidence upload instead of old
baseline compilation and the max-version rollback wrapper. A native old Router
wrapped at `0xffffffff` is not a newly reviewed recovery release.

```yaml
name: BSEED non-PM role matrix

on:
  workflow_dispatch:
  push:
    paths:
      - 'src/**'
      - 'helper_scripts/**'
      - 'make_scripts/**'
      - 'tests/**'
      - 'Makefile'
      - 'board.mk'
      - 'VERSION'
      - 'NVM_MIGRATIONS_VERSION'
      - 'device_db.yaml'
      - 'zigbee2mqtt/ota/**'
      - '.github/workflows/bseed-nonpm-client-canary.yml'
  pull_request:

jobs:
  build-nonpm-canary:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout exact source
        uses: actions/checkout@v4
        with:
          fetch-depth: 0
          ref: ${{ github.event.pull_request.head.sha || github.sha }}

      - name: Install host dependencies
        run: |
          sudo apt-get update
          sudo apt-get install -y make python3 python3-yaml curl wget unzip bzip2

      - name: Cache Telink SDK and toolchain
        uses: actions/cache@v4
        with:
          path: telink_tools
          key: telink-tools-v3.7.2.0

      - name: Install pinned Telink build dependencies
        run: make -C src/telink -f tools.mk sdk toolchain

      - name: Verify stored historical artifacts against sealed identities
        run: make bseed/identity-gate

      - name: Build and reproduce current non-PM Router and Client
        shell: bash
        run: |
          set -euo pipefail
          python3 helper_scripts/bseed_nonpm_variant_matrix.py 2>&1 | tee "$RUNNER_TEMP/nonpm-matrix.log"

      - name: Upload exact-source non-PM evidence
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: bseed-nonpm-role-matrix-${{ github.event.pull_request.head.sha || github.sha }}
          path: |
            build/bseed-nonpm-role-matrix-*/ROLE_MATRIX.json
            build/bseed-nonpm-role-matrix-*/router/*
            build/bseed-nonpm-role-matrix-*/client/*
          if-no-files-found: error

      - name: Upload non-PM matrix log
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: bseed-nonpm-role-matrix-log-${{ github.event.pull_request.head.sha || github.sha }}
          path: ${{ runner.temp }}/nonpm-matrix.log
          if-no-files-found: error
```

Approval to apply this local workflow replacement does not publish source,
start CI or authorize hardware work. Exact-source CI must subsequently pass
before artifacts can be sealed; hardware acceptance remains separate.
