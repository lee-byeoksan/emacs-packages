#!/usr/bin/env python3
"""Identity-selection tests with no real process signaling."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("watchdog", Path(__file__).resolve().parents[1] /
                                             "experiments/lifecycle/watchdog.py")
w = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = w
spec.loader.exec_module(w)


class IdentityTests(unittest.TestCase):
    def test_reparented_owned_and_unrelated(self):
        root = w.Process(10, 1, 501, 1, 0)
        child = w.Process(11, 10, 501, 2, 0)
        shared = w.Process(12, 1, 501, 3, 0)
        owned = w.discover({10: root}, {p.pid: p for p in (root, child, shared)})
        self.assertEqual(set(owned), {10, 11})
        orphan = w.Process(11, 1, 501, 2, 0)
        self.assertEqual(set(w.discover(owned, {11: orphan, 12: shared})), {11})

    def test_reused_pid_does_not_own_new_children(self):
        old = w.Process(10, 1, 501, 1, 0)
        reused = w.Process(10, 1, 501, 4, 0)
        child = w.Process(11, 10, 501, 5, 0)
        self.assertEqual(w.discover({10: old}, {10: reused, 11: child}), {})

    def test_recheck_refuses_reused_pid(self):
        old = w.Process(10, 1, 501, 1, 0)
        class Source:
            def get(self, pid):
                return w.Process(pid, 1, 501, 4, 0)
        with patch.object(w.os, "kill") as kill:
            self.assertFalse(w.signal_verified(Source(), old, 15))
            kill.assert_not_called()

    def test_permission_error_is_not_exit(self):
        class Source:
            def get(self, pid):
                raise PermissionError("denied")
        with patch.object(w.os, "kill") as kill:
            with self.assertRaises(PermissionError):
                w.signal_verified(Source(), w.Process(10, 1, 501, 1, 0), 15)
            kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
