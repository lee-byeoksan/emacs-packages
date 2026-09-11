#!/usr/bin/env python3
"""Legacy developer entry point. Prefer build-package.sh; shipped code is native."""
import argparse
from pathlib import Path
import subprocess
ROOT = Path(__file__).resolve().parents[1]
def build(destination):
    result = subprocess.run(['bash', str(ROOT / 'scripts/build-package.sh'), str(destination)],
                            check=True, capture_output=True, text=True)
    print(result.stdout, end='')
    return Path(result.stdout.strip())
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'var/packages')
    build(parser.parse_args().output_dir)
