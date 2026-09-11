"""End one batch Emacs process; reattach the same CLI from a second Emacs."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EMACS = '/Applications/Emacs.app/Contents/MacOS/Emacs'


def emacs(form, env):
    subprocess.run([EMACS, '--batch', '-Q', '-L', str(ROOT / 'lisp'), '-l', str(HERE / 'ghostel-test.el'),
                    '--eval', form], env=env, check=True, timeout=15)


with tempfile.TemporaryDirectory(prefix='eam-restart-', dir='/tmp') as root:
    metadata = Path(root) / 'runtime'
    env = dict(os.environ, EAM_PROBE_METADATA=str(metadata))
    emacs('''(progn
      (eam-pty-probe-new)
      (eam-pty-test-wait (lambda () (string-match-p "READY:" (buffer-string))))
      (with-temp-file (getenv "EAM_PROBE_METADATA") (insert eam-pty-probe-runtime))
      (set-process-query-on-exit-flag (get-buffer-process (current-buffer)) nil))''', env)
    runtime = Path(metadata.read_text())
    first = json.loads((runtime / 'state.json').read_text())
    os.kill(first['child_pid'], 0)
    emacs('''(progn
      (setq eam-pty-probe-runtime (with-temp-buffer
        (insert-file-contents (getenv "EAM_PROBE_METADATA")) (buffer-string)))
      (eam-pty-test-wait (lambda () (with-temp-buffer
        (insert-file-contents (expand-file-name "state.json" eam-pty-probe-runtime))
        (string-match-p "detached" (buffer-string)))))
      (eam-pty-probe-attach eam-pty-probe-runtime)
      (eam-pty-test-wait (lambda () (string-match-p "ROW 0499" (buffer-string))))
      (ghostel-send-key "q")
      (eam-pty-test-wait (lambda () (not (file-exists-p
        (expand-file-name "socket" eam-pty-probe-runtime))))))''', env)
    last = json.loads((runtime / 'state.json').read_text())
    assert first['child_pid'] == last['child_pid'] and last['exit_code'] == 0
    for path in runtime.iterdir():
        path.unlink()
    runtime.rmdir()
    print('Two separate Emacs processes: same CLI PID survived; replay and exit passed')
