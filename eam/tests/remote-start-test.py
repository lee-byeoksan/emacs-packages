"""Verify Emacs discovery/start/reuse across real Emacs process exits."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
EMACS = os.environ.get('EMACS', '/Applications/Emacs.app/Contents/MacOS/Emacs')
BINARY = Path(os.environ.get('EAM_WEB_BINARY', str(ROOT / 'native/target/debug/eam-runtime')))

AUTH = os.environ.get('EAM_WEB_AUTH', 'token')

class RemoteStartTest(unittest.TestCase):
    def test_start_reuse_and_exit(self):
        with tempfile.TemporaryDirectory(prefix='eam remote start ') as temp:
            root = Path(temp)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            origin = 'https://eam.example.test'
            cli = root / 'tailscale'
            connected = "#!/bin/sh\nprintf '%s\\n' " + "'" + json.dumps({
                'BackendState':'Running','Self':{'DNSName':'eam.example.test.','UserID':123},
                'User':{'999':{'LoginName':'other@example.test'},'123':{'LoginName':'owner@example.test'}}}) + "'\n"
            cli.write_text(connected)
            cli.chmod(0o700)
            pid = None
            env = dict(os.environ)
            env.pop('EAM_WEB_TOKEN', None)
            def emacs(extra='', restart=False):
                # A separate Emacs must not own or terminate the remote server.
                expr = f'''(progn
                  (setq eam-remote-auth '{AUTH}
                        eam-directory {json.dumps(str(root / 'data'))}
                        eam-native-executable {json.dumps(str(BINARY))}
                        eam-remote-port {port}
                        eam-remote-browse-root {json.dumps(str(root))}
                        eam-remote-tailscale-executable {json.dumps(str(cli))})
                  {extra}
                  {'(eam-remote-restart)' if restart else '(let ((noninteractive nil)) (eam-remote-mode 1))'}
                  (let ((deadline (+ (float-time) 12)))
                    (while (and (< (float-time) deadline)
                                (or (process-live-p eam-remote--job) (timerp eam-remote--timer)))
                      (accept-process-output nil .05)))
                  (princ eam-remote--status))'''
                return subprocess.run([EMACS, '--batch', '-Q', '-L', str(ROOT / 'lisp'),
                                       '-l', 'eam-remote', '--eval', expr],
                                      env=env, text=True, capture_output=True, timeout=20)
            def config(token):
                req = urllib.request.Request(f'http://127.0.0.1:{port}/api/config',
                    headers={'Host': 'eam.example.test', 'Origin': origin, **({'Tailscale-User-Login':'owner@example.test'} if AUTH == 'tailscale' else {'Cookie': 'eam_token=' + token})})
                with urllib.request.urlopen(req, timeout=2) as response:
                    return json.load(response)
            try:
                first = emacs()
                token_path = root / 'data/remote/token'
                if token_path.exists() or AUTH == 'tailscale':
                    try:
                        pid = config('' if AUTH == 'tailscale' else token_path.read_text().strip())['pid']
                    except OSError:
                        pass
                self.assertEqual(first.returncode, 0, first.stderr)
                self.assertIn('Ready: ' + origin, first.stdout, first.stderr)
                token_file = root / 'data/remote/token'
                token = '' if AUTH == 'tailscale' else token_file.read_text().strip()
                if AUTH == 'token': self.assertEqual(token_file.stat().st_mode & 0o777, 0o600)
                else: self.assertFalse(token_file.exists())
                pid = config(token)['pid']
                self.assertEqual(config(token)['sessions'], str((root/'data/persistent').resolve()))
                second = emacs()
                self.assertIn('Ready: ' + origin, second.stdout, second.stderr)
                self.assertEqual(config(token)['pid'], pid)
                self.assertEqual('' if AUTH == 'tailscale' else token_file.read_text().strip(), token)
                restarted_live = emacs(restart=True)
                self.assertIn('Ready: ' + origin, restarted_live.stdout, restarted_live.stderr)
                next_pid = config(token)['pid']
                self.assertNotEqual(next_pid, pid)
                pid = next_pid
                mismatch = emacs('(setq eam-remote-origin "https://other.example.test")')
                self.assertIn('does not match', mismatch.stdout)
                self.assertEqual(config(token)['pid'], pid)
                cli.write_text("#!/bin/sh\nprintf '%s\\n' '{\"BackendState\":\"Stopped\"}'\n")
                offline = emacs()
                self.assertIn('Connect Tailscale', offline.stdout)
                self.assertEqual(config(token)['pid'], pid)
                os.kill(pid, signal.SIGTERM)
                for _ in range(100):
                    try:
                        config(token)
                    except OSError:
                        break
                    time.sleep(.05)
                else:
                    self.fail('Server did not stop')
                pid = None
                cli.write_text(connected)
                restarted = emacs(f'(setq eam-remote-origin "{origin}")')
                self.assertIn('Ready: ' + origin, restarted.stdout, restarted.stderr)
                pid = config(token)['pid']
                self.assertEqual('' if AUTH == 'tailscale' else token_file.read_text().strip(), token)
                print(AUTH, 'PASS discovery, detached start, Emacs exit, reuse, mismatch/offline, restart token')
            finally:
                if pid is not None:
                    os.kill(pid, signal.SIGTERM)

if __name__ == '__main__':
    unittest.main()
