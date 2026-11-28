"""Validate hosted native-control candidates; never authorizes deployment."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'zg204zl_reliability'))
import apply as baseline
import verify_image
from startup_diagnostic import PROFILES

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--source-commit', required=True)
    diagnostics = {tag: (version, mode) for tag, version, mode in PROFILES.values()}
    p.add_argument('--variant', choices=['v0149', *diagnostics], default='v0149')
    args = p.parse_args()
    version, mode = diagnostics.get(args.variant, (0x01493001, None))
    baseline.VARIANTS[args.variant] = baseline.Variant(version, 'RF_POWER_INDEX_P2p61dBm', 2.61)
    result = verify_image.verify(args.directory / f'ZG204ZL_{args.variant}.bin',
                                 args.directory / f'1141-0232-{version:08X}-ZG204ZL.zigbee', args.variant)
    if result['native']['bytes'] > 0x20000 - 256:
        raise SystemExit('Native feature does not fit with the required 256-byte reserve')
    result.update(sourceCommit=args.source_commit, upstreamCommit=baseline.UPSTREAM_COMMIT,
                  feature='native conditional binding', deployment='HARDWARE_UNQUALIFIED')
    if mode:
        result['benchOnly'] = True
        result['diagnostic'] = mode
    (args.directory / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
