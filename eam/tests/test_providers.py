import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'bridge'))
from providers import subscription_env

class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.env = os.environ.copy()
        self.env['EMACS_AI_FIXTURE_REQUESTS'] = str(self.path / 'requests')
        self.env['EMACS_AI_FIXTURE_CHILD'] = str(self.path / 'child')
        self.env['CODEX_HOME'] = str(self.path)
        (ROOT / 'tests/fake-provider.py').chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def command(self, provider):
        return [sys.executable, str(ROOT / 'bridge/providers.py'), '--provider', provider,
                '--executable', str(ROOT / 'tests/fake-provider.py'), '--cwd', str(self.path),
                '--state', str(self.path / (provider + '.state')), '--prefix', str(self.path / provider)]

    def run_turn(self, provider, prompt='입력 그대로\n$(echo unsafe) `literal`'):
        return subprocess.run(self.command(provider), input=prompt, text=True,
                              capture_output=True, env=self.env, timeout=10)

    def test_both_protocols_utf8_dedup_prompt_and_resume(self):
        for provider in ('claude', 'codex'):
            with self.subTest(provider=provider):
                first = self.run_turn(provider)
                self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
                self.assertEqual(first.stdout, '한글🙂\n')
                second = self.run_turn(provider, '두 번째')
                self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
                state = json.loads((self.path / (provider + '.state')).read_text())
                self.assertEqual(state['status'], 'completed')
                self.assertEqual(state['backend_id'], 'fixture-' + provider)
        requests = [json.loads(x) for x in (self.path / 'requests').read_text().splitlines()]
        claude = [r for r in requests if 'prompt' in r]
        self.assertEqual(claude[0]['prompt'], '입력 그대로\n$(echo unsafe) `literal`')
        self.assertIn('--resume', claude[1]['args'])
        codex = [r for r in requests if r.get('method') == 'turn/start']
        self.assertEqual(codex[0]['params']['input'], [{'type': 'text', 'text': claude[0]['prompt']}])
        resume = next(r for r in requests if r.get('method') == 'thread/resume')
        self.assertTrue(resume['params']['excludeTurns'])
        self.assertNotIn('baseInstructions', resume['params'])
        self.assertNotIn('developerInstructions', resume['params'])

    def test_eof_and_oversize_fail_closed(self):
        for mode in ('eof', 'oversize'):
            self.env['EMACS_AI_FIXTURE_MODE'] = mode
            result = self.run_turn('claude')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('[connection stopped]', result.stdout)
            self.assertIsNone(json.loads((self.path / 'claude.state').read_text())['backend_id'])
        self.assertGreater((self.path / 'claude.protocol.jsonl').stat().st_size, 4 * 1024 * 1024)

    def test_cancellation_stops_descendants_and_clears_resume(self):
        self.env['EMACS_AI_FIXTURE_MODE'] = 'wait'
        p = subprocess.Popen(self.command('claude'), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=self.env)
        try:
            p.stdin.write(b'test'); p.stdin.close(); p.stdin = None
            deadline = time.monotonic() + 5
            while not (self.path / 'child').exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue((self.path / 'child').exists())
            p.send_signal(signal.SIGTERM)
            out, _ = p.communicate(timeout=5)
            self.assertIn(b'Cancelled', out)
            state = json.loads((self.path / 'claude.state').read_text())
            self.assertEqual(state['status'], 'cancelled')
            self.assertIsNone(state['backend_id'])
            child = (self.path / 'child').read_text()
            result = subprocess.run(['ps', '-p', child, '-o', 'stat='], capture_output=True, text=True)
            self.assertTrue(not result.stdout.strip() or result.stdout.strip().startswith('Z'))
        finally:
            if p.poll() is None: p.kill(); p.wait()

    def test_api_account_rejected_before_model_request(self):
        self.env['EMACS_AI_FIXTURE_AUTH'] = 'apiKey'
        result = self.run_turn('codex')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('turn/start', (self.path / 'requests').read_text())

    def test_child_environment_does_not_mutate_parent(self):
        old = os.environ.get('ANTHROPIC_API_KEY')
        try:
            os.environ['ANTHROPIC_API_KEY'] = 'fixture-only'
            self.assertNotIn('ANTHROPIC_API_KEY', subscription_env('claude'))
            self.assertEqual(os.environ['ANTHROPIC_API_KEY'], 'fixture-only')
        finally:
            if old is None: os.environ.pop('ANTHROPIC_API_KEY', None)
            else: os.environ['ANTHROPIC_API_KEY'] = old

if __name__ == '__main__':
    unittest.main()
