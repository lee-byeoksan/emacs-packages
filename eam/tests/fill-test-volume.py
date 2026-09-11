#!/usr/bin/env python3
"""Fill only an explicitly mounted, small test volume, never a host directory."""
import errno
import os
from pathlib import Path
import shutil
import sys

volume = Path(sys.argv[1]).resolve()
assert os.path.ismount(volume), 'Must be a mounted test volume'
assert volume.name == 'volume' and volume.parent.name.startswith('ai-enospc-')
assert shutil.disk_usage(volume).total <= 40 * 1024 * 1024
count = 0
try:
    with (volume / 'filler').open('xb', buffering=0) as output:
        while count <= 40 * 1024 * 1024:
            count += output.write(b'x' * 65536)
        raise AssertionError('Test volume did not fill within safety bound')
except OSError as error:
    if error.errno != errno.ENOSPC:
        raise
    print(f'ENOSPC after {count} bytes; free={shutil.disk_usage(volume).free}')
