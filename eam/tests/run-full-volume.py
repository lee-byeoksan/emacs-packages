#!/usr/bin/env python3
"""Create a bounded disposable HFS+ volume and test real ENOSPC in Emacs."""
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory(prefix='ai-enospc-', dir='/private/tmp') as folder:
    base = Path(folder)
    image, volume = base / 'test.dmg', base / 'volume'
    volume.mkdir()
    subprocess.run(['/usr/bin/hdiutil', 'create', '-size', '32m', '-fs', 'HFS+',
                    '-volname', 'EmacsAITest', str(image)], check=True, timeout=60)
    mounted = False
    try:
        subprocess.run(['/usr/bin/hdiutil', 'attach', '-nobrowse', '-mountpoint', str(volume), str(image)],
                       check=True, timeout=60)
        mounted = True
        subprocess.run(['/Applications/Emacs.app/Contents/MacOS/Emacs', '--batch', '-Q', '-L', 'lisp',
                        '-l', 'tests/full-volume.el', '--eval',
                        '(ert-run-tests-batch-and-exit "eam-real-full-volume")'],
                       cwd=root, env=dict(os.environ, EMACS_AI_FULL_VOLUME=str(volume)),
                       check=True, timeout=60)
    finally:
        if mounted:
            subprocess.run(['/usr/bin/hdiutil', 'detach', str(volume)], check=True, timeout=60)
