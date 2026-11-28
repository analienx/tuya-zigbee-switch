"""Validate hosted native-control candidates; never authorizes deployment."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'zg204zl_reliability'))
import apply as baseline
import verify_image

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--source-commit', required=True)
    p.add_argument('--variant', choices=['v0149', 'v0150', 'v0151'], default='v0149')
    args = p.parse_args()
    version = {'v0149': 0x01493001, 'v0150': 0x01503001, 'v0151': 0x01513001}[args.variant]
    baseline.VARIANTS[args.variant] = baseline.Variant(version, 'RF_POWER_INDEX_P2p61dBm', 2.61)
    result = verify_image.verify(args.directory / f'ZG204ZL_{args.variant}.bin',
                                 args.directory / f'1141-0232-{version:08X}-ZG204ZL.zigbee', args.variant)
    if result['native']['bytes'] > 0x20000 - 256:
        raise SystemExit('Native feature does not fit with the required 256-byte reserve')
    result.update(sourceCommit=args.source_commit, upstreamCommit=baseline.UPSTREAM_COMMIT,
                  feature='native conditional binding', deployment='HARDWARE_UNQUALIFIED')
    if args.variant != 'v0149':
        result['benchOnly'] = True
        result['diagnostic'] = {'v0150': 'skip_idle_sleep', 'v0151': 'timer_sleep_uses_suspend'}[args.variant]
    (args.directory / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
