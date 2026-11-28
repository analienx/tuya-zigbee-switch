# Native binding agent instructions

Read [README.md](README.md), repository AGENTS.md and
`skills/bseed-zigbee-ota/SKILL.md` before changing this feature or preparing a canary.

- Work under issue #164. v0149 is a hardware-unqualified development candidate.
  Do not silently replace the established wired-flash preset with this image.
- Build/test/verify only on GitHub-hosted Actions at the exact candidate SHA via
  `.github/workflows/zg204zl-native.yml`. No local Python/C/Node verification,
  compiler, WSL build, self-hosted runner, or personal Executor workaround.
- `apply_native.py` requires pristine pinned upstream. `specialize_ota.py` runs
  after SDK installation and pins both changed SDK files. Keep edits anchored
  and fail closed before writing when a pin or anchor differs.
- Keep `USE_NV_APP`, app footprint, OTA identity, stock conversion and rollback
  boundaries explicit. Never enlarge the application erase/write range to make
  a candidate pass the size gate. Do not remove client OTA support to save space.
- Test actual native C plus the converter and real herdsman wire frames. Extend
  the independent model and failure cases for behavior changes. Keep the final
  256-byte flash reserve and 512-byte new-state RAM gate.
- Preserve serialization until actual stack completion. A deadline does not
  cancel an old radio attempt. Do not replace quarantine with a blind retry.
- Groups are best-effort, deduplicated by group ID, with Level preference only
  after configuration. Re-OFF recovery must not target groups that had any
  successful first hop. Membership overlap cannot be inferred from bindings.
- Retain readback and awake-window setup. A failed NV readback can leave persisted
  changes even though RAM was not committed. Do not report that as no change.
- No live flashing, converter deployment, MQTT writes, re-interview, bind/unbind,
  coordinator joining or service restart without the user's separate live scope.
  Follow existing profile-driven hardware tooling for an authorized canary.
- Keep firmware binaries, household configuration, identities, network secrets
  and runtime traces outside this public repository. Record public hosted SHA,
  run URL, sizes and hashes; leave physical acceptance pending until measured.
