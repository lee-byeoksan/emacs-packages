#!/usr/bin/env python3
"""Check real macOS assertions and cleanup; never starts an AI CLI."""
import json
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
EMACS = '/Applications/Emacs.app/Contents/MacOS/Emacs'

def assertion(pid):
    result = subprocess.run(['/usr/bin/pmset', '-g', 'assertions'], capture_output=True, text=True, check=True)
    return [line.strip() for line in result.stdout.splitlines() if f'pid {pid}(' in line]

def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.1)
    raise AssertionError('timed out waiting for expected process/assertion state')

results = []
for phase in ('disable', 'normal-exit', 'kill'):
    with tempfile.TemporaryDirectory(prefix='eam-caffeine-') as directory:
        ready = Path(directory) / 'ready'
        finish = Path(directory) / 'finish'
        form = f'''(progn
          (eam-caffeine-mode 1)
          (let ((first eam-caffeine--process))
            (eam-caffeine-mode 1)
            (unless (eq first eam-caffeine--process) (error "Duplicate caffeinate")))
          (with-temp-file {json.dumps(str(ready))}
            (insert (number-to-string (process-id eam-caffeine--process))))
          (while (not (file-exists-p {json.dumps(str(finish))})) (accept-process-output nil .1))
          {'(eam-caffeine-mode -1)' if phase == 'disable' else ''}
          {'(while t (accept-process-output nil .1))' if phase == 'disable' else '(kill-emacs)'})'''
        emacs = subprocess.Popen([EMACS, '--batch', '-Q', '-L', str(ROOT / 'lisp'),
                                  '-l', 'eam-caffeine', '--eval', form],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        caffeine_pid = None
        try:
            wait_for(lambda: ready.exists() and ready.stat().st_size)
            caffeine_pid = int(ready.read_text())
            lines = wait_for(lambda: assertion(caffeine_pid))
            assert any('PreventUserIdleSystemSleep' in line for line in lines), lines
            assert not any('PreventUserIdleDisplaySleep' in line for line in lines), lines
            if phase == 'kill':
                emacs.kill()
            else:
                finish.touch()
            if phase != 'disable':
                emacs.wait(timeout=8)
            wait_for(lambda: not assertion(caffeine_pid))
            if phase == 'disable':
                assert emacs.poll() is None, 'Disabling caffeine killed Emacs'
            results.append({'phase': phase, 'assertion_type': 'PreventUserIdleSystemSleep',
                            'released': True, 'emacs_alive_after_disable': phase == 'disable'})
        finally:
            if emacs.poll() is None:
                emacs.terminate()
            emacs.wait(timeout=8)
            if caffeine_pid is not None:
                wait_for(lambda: not assertion(caffeine_pid))
print(json.dumps(results, indent=2))
