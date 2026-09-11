#!/usr/bin/env python3
"""Prepare a distinct local app ID so Computer Use can target the test Emacs.

Reuses the installed Emacs binary and resources; does not download software.
Run with python3, then open the printed app path using Computer Use.
"""
from pathlib import Path
import platform
import plistlib
import shlex
import shutil
import filecmp
import argparse
import re
parser = argparse.ArgumentParser()
mode = parser.add_mutually_exclusive_group()
mode.add_argument("--terminal", action="store_true")
mode.add_argument("--validation", action="store_true")
mode.add_argument("--app", action="store_true")
parser.add_argument("--instance", help="Distinct daily-app test instance name (requires --app)")
options = parser.parse_args()
if options.instance and (not options.app or not re.fullmatch(r'[a-z0-9-]{1,32}', options.instance)):
    parser.error('--instance requires --app and 1–32 lowercase letters, digits or hyphens')
app_name = 'EAM' + (' ' + options.instance if options.instance else '')
app_id = 'local.eam.app' + ('.' + options.instance if options.instance else '')

root = Path(__file__).resolve().parent.parent
source = Path('/Applications/Emacs.app/Contents')
target = root / ('var/EAM.app/Contents' if options.app else 'var/EAM Validation.app/Contents' if options.validation else ('var/EAM Terminal Test.app/Contents' if options.terminal else 'var/EAM Test.app/Contents'))
if options.app:
    target = root / 'var' / (app_name + '.app') / 'Contents'
architecture = 'arm64' if platform.machine() == 'arm64' else 'x86_64'
binaries = sorted(p for p in (source / 'MacOS').glob(f'Emacs-{architecture}-*')
                  if p.is_file() and not p.suffix == '.pdmp')
if not binaries:
    raise SystemExit('No matching installed Emacs for Mac OS X binary')
binary = binaries[-1]
(target / 'MacOS').mkdir(parents=True, exist_ok=True)
for path in (source / 'MacOS').iterdir():
    dest = target / 'MacOS' / path.name
    if path == binary:
        if dest.is_symlink() or not dest.exists() or not filecmp.cmp(path, dest, shallow=False):
            staging = dest.with_name(dest.name + '.new')
            shutil.copyfile(path, staging)
            staging.chmod(0o755)
            staging.replace(dest)
    elif not dest.exists() and not dest.is_symlink():
        dest.symlink_to(path, target_is_directory=path.is_dir())
resources = target / 'Resources'
if not resources.exists():
    resources.symlink_to(source / 'Resources', target_is_directory=True)
info = plistlib.loads((source / 'Info.plist').read_bytes())
info.update(CFBundleIdentifier=(app_id if options.app else 'local.eam.validation' if options.validation else ('local.eam.terminal-test' if options.terminal else 'local.eam.gui-test')),
            CFBundleName=(app_name if options.app else 'EAM Test'),
            CFBundleDisplayName=(app_name if options.app else 'EAM Test'),
            CFBundleExecutable='eam-launch')
info.pop('CFBundleDocumentTypes', None)
info.pop('CFBundleURLTypes', None)
(target / 'Info.plist').write_bytes(plistlib.dumps(info))
args = [str(target / 'MacOS' / binary.name), '-Q', '--title',
        (app_name if options.app else 'eam GUI verification'), '-L', str(root / 'lisp'),
        '-l', 'eam-standalone', '-l', str(root / ('lisp/eam-app.el' if options.app else 'tests/cli-validation-gui.el' if options.validation else ('tests/terminal-gui.el' if options.terminal else 'tests/gui-driver.el'))),
        '-f', ('eam-keys-mode' if options.app else 'eam-terminal-gui-fixture' if (options.terminal or options.validation) else 'eam-gui-ready')]
launcher = target / 'MacOS/eam-launch'
launcher.write_text('#!/bin/bash\nexec ' + shlex.join(args) + '\n')
launcher.chmod(0o755)
print(target.parent)
