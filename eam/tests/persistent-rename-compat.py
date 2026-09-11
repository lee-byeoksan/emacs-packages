"""Verify an existing 0.8.11 owned session survives the EAM namespace rename."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

root = Path(__file__).resolve().parents[1]
old = root / 'var/package-profile-0.8.11/elpa/emacs-ai-0.8.11/bridge/persistence/manager.py'
new = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'bridge/persistence/manager.py'

def call(manager, action, **request):
    result = subprocess.run([sys.executable, str(manager), action],
                            input=json.dumps(request), text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)

with tempfile.TemporaryDirectory(prefix='eam-compat-') as directory:
    session = str(Path(directory) / 'session')
    metadata = call(old, 'start', session=session, provider='fake', executable=sys.executable,
                    args=['-u', '-c', 'import time; print("compat 한글", flush=True); time.sleep(30)'],
                    directory=directory)
    try:
        archive = Path(metadata['archive'])
        for _ in range(100):
            if archive.exists() and b'compat' in archive.read_bytes():
                break
            time.sleep(.02)
        before = call(old, 'inspect', session=session)
        after = call(new, 'inspect', session=session)
        assert before['status']['state'] == after['status']['state'] == 'running'
        assert before['status']['recorder_pid'] == after['status']['recorder_pid']
        assert call(new, 'stop', session=session)['state'] == 'stopped'
        assert call(old, 'inspect', session=session)['status']['state'] == 'stopped'
        assert b'compat' in archive.read_bytes()
        print('0.8.11 -> EAM: same owned PID, status, explicit stop, archive preservation passed')
    finally:
        call(old, 'stop', session=session)
        shutil.rmtree(metadata['runtime'], ignore_errors=True)
