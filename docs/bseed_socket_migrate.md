# One-command BSEED socket migration

`helper_scripts/bseed_socket_migrate.py` turns a socket migration into four
commands. It writes nothing into the repository: profiles, images, logs and
locks live under the private `site.json` paths.

## Site configuration (private)

`BSEED_SITE` (or `--site`) points to the private `site.json` the canary
tooling already uses. The migration flow additionally reads:

| key | meaning |
|---|---|
| `candidate_package` | directory holding a sealed `CANDIDATE_PACKAGE.json` and `<variant>/firmware.ota` (pm-client, pm-router, nonpm-router, nonpm-client) |
| `campaign_root`, `serve_root` | private workdirs and served images |
| `publish` | `ssh_alias`, `remote_dir`, `url_base` of the HA `www` folder |
| `mutation_broker` | `script` and `policy` used by `release` |
| `devices.<name>.pm_settings_baseline`, `pm_router_matrix_evidence` | only for same-role PM Router flashes |

## Commands

1. `plan --device NAME --to client|router|same` reads the Zigbee2MQTT
   inventory and, for a role change, the target's Mgmt_Lqi neighbour table
   (one read-only ZDO request). Role changes rejoin network-wide
   (`join_mode`), never through one chosen parent Router. It chooses the route and candidate, refuses
   unsupported cases with a reason, writes `profile.json`, the image (native,
   or a header-only wrapper whose only changed bytes are 12–17), the template
   index and `MIGRATION.json`, and runs the campaign's own `load_profile`,
   version policy, automatic relay OFF validation and, for non-PM sockets,
   the recovery gate. `CHILDREN_WILL_REPARENT` names end devices that use the
   socket as their parent when it becomes a Client.
2. `publish --device NAME` runs `prepare`, copies image and index to HA and
   checks the served image hash.
3. `run --device NAME` waits for the shared network lease (one OTA at a
   time), then runs `transition` for a role change or `flash` otherwise,
   with a durable log. Automatic relay OFF switches the relay OFF before the
   flash and back ON only after `postflash_candidate`. When the transfer stops
   (`update_error`, timeout) it runs `reconcile-source` and retries only while
   the attempt's maximum OTA progress strictly increases, at most
   `--max-resumes` (default 3). Any other stop prints the phase and the log.
4. `release --device NAME --authority "<owner's words>"` retires the
   `postflash_candidate` lease through the mutation broker once the relay has
   been restored. `status --device NAME` shows plan, lock, relay record and
   leases.

`queue --device A --device B --to client` chains plan, publish and run per
device. Without `--release-authority` it stops after the first candidate,
because the next OTA cannot start while that lease is held.

## Routes

| source | `--to` | route |
|---|---|---|
| stock `_TZ3000_b28wrpvx` / `TS011F` Router | `client` | stock wrapper 54179 / 0xFFFFFFFF → `cli` Client, `physically_unloaded` proof by verified relay OFF |
| custom `b28wrpvx` / `TS011F-BS-PM` | `same` | native image, meter proof |
| custom PM Client ↔ Router | `router` / `client` | FORCE wrapper on the source query type, network-wide rejoin |
| custom `o1jzcxou` / `TS011F-BS` | `same` | native image; runs only where the non-PM recovery gate admits it |

Refused: stock PM to Router except the exact WindowRightL authorized canary, non-PM role changes, other
boards. All transfers use 48-byte blocks with 1200 ms response pacing.

## Exact stock PM Router canary (2026-11-22)

The Router-to-Router stock path is narrowly authorized for WRSocketWindowRightL
(IEEE 0xa4c138793505c5cc), not a general fleet converter. Its required
stock-facing OTA header is image type 54179, version 0xffffffff; only six
header bytes differ from the sha-verified, sealed native BSEED r24 Router
image. The production version policy checks the native board/role/version.

The device must freshly answer a read-only relay GET with exact stock
manufacturer, IEEE and Router role, relay OFF, and reported power/current both
exactly zero. Stock composite power values may be cached: this is an
explicit relay-OFF plus stock-reported-zero proof, NOT a fresh ZCL meter
sample. No relay SET commands or auto-OFF controls are allowed. A profile
for any other IEEE, build, role, relay mode or missing native image is refused.

After installation the relay property changes from stock state to custom
state_relay. The post-flash verifier uses the latter, requires the correct
r24 Router Basic build/interview and fresh PM evidence, and retains the
coordinator lease until postflash_candidate is achieved. OTA transport
success alone is never physical acceptance. The first canary intentionally
does not auto-restore source groups/reporting, to avoid unverified relay
writes; separately audit children, routing, metering and group parity.

The private site must contain devices.WRSocketWindowRightL with the exact
IEEE, expect_relay OFF and stock_router_canary_authority equal to
WRSocketWindowRightL:0xa4c138793505c5cc:Router:relay-OFF.
The process is plan, publish, run, then release only after owner's
post-flash acceptance. Do not bypass active network leases or run this
canary in parallel with a Router's children.

## When the socket does not rejoin

`run` ends with a network-wide rejoin window bounded by one deadline (at most
35 minutes; role changes only). If the
target still never announces (both LEDs flashing together), follow "A
converted socket that never comes back" in the skill: owner network reset (10
quick presses), then one more `bseed_z2m_rejoin_window.py --network-wide`
window, then `metadata`, `restore-state`, `provision-pm` and `postflash`.
