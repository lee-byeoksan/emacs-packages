"""Start via eam-new, exit Emacs, attach from a second Emacs, then stop.
Uses fake executables for both provider choices; never calls an AI service.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

profile = Path(sys.argv[1]).resolve()
package, = (profile / 'elpa').glob('eam-*')
manager = package / 'bridge/persistence/manager.py'
emacs = '/Applications/Emacs.app/Contents/MacOS/Emacs-arm64-11'

def manage(action, session):
    return json.loads(subprocess.check_output(
        [sys.executable, str(manager), action],
        input=json.dumps({'session': str(session)}), text=True))

with tempfile.TemporaryDirectory(prefix='eam-default-persist-') as directory:
    root = Path(directory)
    fake = root / 'fake-provider'
    fake.write_text(f'#!{sys.executable}\nimport time\nprint("DEFAULT PERSIST 한글", flush=True)\ntime.sleep(120)\n')
    fake.chmod(0o700)
    setup = f'''
(require 'eam-app)
(setq eam-directory {json.dumps(str(root / 'records') + '/')}
      eam-cli-notifications nil)
'''
    stage1 = root / 'start.el'
    stage1.write_text(setup + f'''
(cl-letf (((symbol-function 'eam--executable) (lambda (_) {json.dumps(str(fake))})))
  (dolist (provider '("Claude" "Codex"))
    (let ((session (let ((eam-record-terminal t)) (eam-new provider {json.dumps(str(root))}))))
      (with-current-buffer (eam-terminal-output session)
        (unless eam-terminal-persistent-directory (error "Default start was not persistent"))))))
''')
    stage2 = root / 'attach.el'
    stage2.write_text(setup + '''
(dolist (path (directory-files (expand-file-name "persistent" eam-directory) t directory-files-no-dot-files-regexp))
  (let ((session (eam-attach path)))
    (with-current-buffer (eam-terminal-output session)
      (unless (equal eam-terminal-persistent-directory path) (error "Wrong session attached"))
      (eam-detach))))
''')
    sessions = []
    metadata = []
    try:
        subprocess.run([emacs, '--batch', '-Q', '-l', str(profile / 'bootstrap.el'),
                        '-l', str(stage1)], check=True, timeout=30)
        sessions = sorted((root / 'records/persistent').iterdir())
        assert len(sessions) == 2
        for _ in range(100):
            metadata = [manage('inspect', p) for p in sessions]
            if all(x['status']['attached_clients'] == 0 for x in metadata):
                break
            time.sleep(.05)
        assert all(x['status']['state'] == 'running' and x['status']['attached_clients'] == 0 for x in metadata)
        live = json.loads(subprocess.check_output(
            [sys.executable, str(manager), 'list-live'],
            input=json.dumps({'root': str(root / 'records/persistent')}), text=True))
        assert len(live['sessions']) == 2
        assert all(x['attached_clients'] == 0 for x in live['sessions'])
        before = [x['status']['recorder_pid'] for x in metadata]
        subprocess.run([emacs, '--batch', '-Q', '-l', str(profile / 'bootstrap.el'),
                        '-l', str(stage2)], check=True, timeout=30)
        after = [manage('inspect', p) for p in sessions]
        assert before == [x['status']['recorder_pid'] for x in after]
        assert all(x['status']['state'] == 'running' for x in after)
        for p, info in zip(sessions, after):
            assert manage('stop', p)['state'] == 'stopped'
            assert b'DEFAULT PERSIST' in Path(info['metadata']['archive']).read_bytes()
        print('PASS: both providers via eam-new survive Emacs exit, attach in second Emacs, close only detaches, explicit stop retains archives')
    finally:
        if (root / 'records/persistent').exists():
            for p in (root / 'records/persistent').iterdir():
                info = manage('inspect', p)
                manage('stop', p)
                shutil.rmtree(info['metadata']['runtime'], ignore_errors=True)
