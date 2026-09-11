#!/usr/bin/env python3
"""Read-only local readiness report. Never logs in or sends a model request."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
MODULE_HASH = '6e4a509c23fe6c39e90610dd17cb2543622436898f86daf80f47cde523240b9d'

def executable(name):
    for directory in (Path.home() / '.local/bin', Path('/opt/homebrew/bin'), Path('/usr/local/bin')):
        path = directory / name
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return shutil.which(name)

def version(path):
    if not path:
        return {'ready': False, 'error': 'executable not found'}
    try:
        result = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=10)
        return {'path': path, 'ready': result.returncode == 0,
                'version': result.stdout.splitlines()[0] if result.stdout else '',
                'exit_code': result.returncode}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'path': path, 'ready': False, 'error': type(error).__name__}

def report():
    module = ROOT / 'var/deps/ghostel/ghostel-module.dylib'
    digest = None
    if module.is_file():
        with module.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'platform': platform.platform(),
            'supported_installer': platform.system() == 'Darwin' and platform.machine() == 'arm64',
            'emacs': version('/Applications/Emacs.app/Contents/MacOS/Emacs'),
            'claude': version(executable('claude')), 'codex': version(executable('codex')),
            'ghostel': {'expected_version': 'v0.53.0', 'module_sha256': digest,
                        'ready': digest == MODULE_HASH and (module.parent / 'lisp/ghostel.el').is_file()},
            'authentication': 'not checked; use native CLI login',
            'gui': 'not launched', 'model_requests': 0}

if __name__ == '__main__':
    result = report()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['supported_installer'] and
                     all(result[k]['ready'] for k in ('emacs', 'claude', 'codex', 'ghostel')) else 1)
