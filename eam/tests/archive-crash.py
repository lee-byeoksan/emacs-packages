#!/usr/bin/env python3
"""Kill only our batch writer; verify acknowledged bytes and bounded reopening."""
import json
from pathlib import Path
import subprocess
import tempfile
import time

root = Path(__file__).resolve().parent.parent
emacs = '/Applications/Emacs.app/Contents/MacOS/Emacs'
with tempfile.TemporaryDirectory(prefix='ai-archive-crash-') as directory:
    base = Path(directory)
    archive, ack = base / 'record.txt', base / 'ack'
    payload = '한글🙂 기록\n' * 1000
    # Signal readiness only after the first append has returned successfully.
    form = f'''(let* ((eam-directory {json.dumps(directory)})
                     (s (eam--create)) (text {json.dumps(payload, ensure_ascii=False)}))
                (setf (eam-session-file s) {json.dumps(str(archive))})
                (dotimes (i 500)
                  (eam--append s text)
                  (when (= i 0) (with-temp-file {json.dumps(str(ack))} (insert "ready")))
                  (accept-process-output nil .01)))'''
    process = subprocess.Popen([emacs, '--batch', '-Q', '-L', str(root / 'lisp'),
                                '-l', 'eam', '--eval', form],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 8
        while not ack.exists():
            assert process.poll() is None, process.stderr.read().decode()
            assert time.monotonic() < deadline
            time.sleep(.01)
        process.kill()
        assert process.wait(timeout=5) == -9
        saved = archive.read_bytes()
        assert saved.startswith(payload.encode()), 'Acknowledged append missing'
        reopen = f'''(let ((eam-buffer-limit 128))
          (eam-history-open {json.dumps(str(archive))})
          (unless (and (<= (buffer-size) 128) (eq buffer-undo-list t)) (error "Unbounded reopen"))
          (princ (buffer-size)))'''
        result = subprocess.run([emacs, '--batch', '-Q', '-L', str(root / 'lisp'), '-l', 'eam',
                                 '--eval', reopen], capture_output=True, text=True, check=True, timeout=8)
        print(json.dumps({'signal': 'SIGKILL', 'acknowledged_bytes': len(payload.encode()),
                          'retained_bytes': len(saved), 'reopened_chars': int(result.stdout),
                          'power_loss_test': False}, indent=2))
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
