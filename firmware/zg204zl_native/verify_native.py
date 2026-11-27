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
    args = p.parse_args()
    baseline.VARIANTS['v0149'] = baseline.Variant(0x01493001, 'RF_POWER_INDEX_P2p61dBm', 2.61)
    result = verify_image.verify(args.directory / 'ZG204ZL_v0149.bin',
                                 args.directory / '1141-0232-01493001-ZG204ZL.zigbee', 'v0149')
    if result['native']['bytes'] > 0x20000 - 256:
        raise SystemExit('Native feature does not fit with the required 256-byte reserve')
    result.update(sourceCommit=args.source_commit, upstreamCommit=baseline.UPSTREAM_COMMIT,
                  feature='native conditional binding', deployment='HARDWARE_UNQUALIFIED')
    (args.directory / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
