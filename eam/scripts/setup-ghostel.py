#!/usr/bin/env python3
"""Install a pinned, unmodified Ghostel dependency only under this project."""
from pathlib import Path
import hashlib
import platform
import subprocess
import tarfile
root = Path(__file__).resolve().parent.parent
base = root / 'var/deps'
dest = base / 'ghostel'
tag = 'v0.53.0'
if platform.system() != 'Darwin' or platform.machine() != 'arm64':
    raise SystemExit('This prototype installer currently targets Apple Silicon macOS.')
base.mkdir(parents=True, exist_ok=True)
archive = base / ('ghostel-' + tag + '.tar.gz')
if not (dest / 'lisp/ghostel.el').exists():
    subprocess.run(['curl', '-fL', '--retry', '2', 'https://api.github.com/repos/dakra/ghostel/tarball/' + tag, '-o', str(archive)], check=True)
    dest.mkdir(exist_ok=True)
    with tarfile.open(archive) as tar:
        for entry in tar.getmembers():
            parts = Path(entry.name).parts[1:]
            if not parts: continue
            entry.name = str(Path(*parts))
            tar.extract(entry, dest, filter='data')
module = dest / 'ghostel-module.dylib'
expected = '6e4a509c23fe6c39e90610dd17cb2543622436898f86daf80f47cde523240b9d'
if not module.exists():
    subprocess.run(['curl', '-fL', '--retry', '2', 'https://github.com/dakra/ghostel/releases/download/' + tag + '/ghostel-module-aarch64-macos.dylib', '-o', str(module)], check=True)
if hashlib.sha256(module.read_bytes()).hexdigest() != expected:
    raise SystemExit('Ghostel module SHA256 mismatch')
print('Ghostel ' + tag + ' ready: ' + str(dest))
