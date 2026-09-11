"""Offline checks for the command hook's side-effect-free output protocol."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'bridge/notify_claude.py'
spec = importlib.util.spec_from_file_location('notify_claude', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HookTests(unittest.TestCase):
    def test_only_explicit_events_and_terminal_sequence(self):
        for event in ('Stop', 'Notification', 'PermissionRequest'):
            result = subprocess.run([sys.executable, str(SCRIPT)],
                                    input=json.dumps({'hook_event_name': event,
                                                      'message': 'private text',
                                                      'tool_input': {'command': 'never execute'}}).encode(),
                                    capture_output=True, check=True)
            payload = json.loads(result.stdout)
            self.assertEqual(set(payload), {'terminalSequence'})
            self.assertEqual(payload['terminalSequence'],
                             f'\x1b]777;notify;Claude Code;{event}\x07')
            self.assertNotIn('private text', result.stdout.decode())

    def test_invalid_and_large_input_ignored(self):
        for raw in (b'', b'not json', b'[]', b'{"hook_event_name":"PreToolUse"}',
                    b'x' * 1048577):
            self.assertIsNone(module.notification(raw))


if __name__ == '__main__':
    unittest.main()
