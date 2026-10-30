"""Final worker shutdown belongs to actual native close, never close approval."""

import inspect
import unittest
from threading import Event, current_thread
from types import SimpleNamespace
from unittest.mock import Mock

from codeviz.ui.desktop import _NativeCloseGuard


class RuntimeCloseTests(unittest.TestCase):
    def test_actual_closed_schedules_final_cleanup_once_off_the_native_thread(self):
        self.assertIn("after_closed", inspect.signature(_NativeCloseGuard).parameters,
                      "Missing final native cleanup")
        entered, release, finished = Event(), Event(), Event()
        caller = current_thread()
        invocations = []

        def cleanup():
            invocations.append(current_thread())
            entered.set()
            release.wait(2)
            finished.set()

        guard = _NativeCloseGuard(SimpleNamespace(run_js=Mock(), destroy=Mock()), after_closed=cleanup)
        guard.loaded()
        self.assertFalse(entered.is_set())
        try:
            guard.closed()
            self.assertTrue(entered.wait(1))
            self.assertFalse(finished.is_set(), "Native event must not wait for cleanup")
            guard.closed()
        finally:
            release.set()
        self.assertTrue(finished.wait(1))
        self.assertEqual(len(invocations), 1)
        self.assertIsNot(invocations[0], caller)
