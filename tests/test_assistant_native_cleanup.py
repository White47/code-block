"""Failed native runs clean the verified host, not only the venv launcher."""

import ctypes
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import verify_assistant_native as verifier


class NativeCleanupTests(unittest.TestCase):
    def test_terminate_error_still_attempts_bounded_kill(self):
        process = SimpleNamespace(poll=lambda: None,
            terminate=Mock(side_effect=OSError("synthetic terminate failure")),
            kill=Mock(), wait=Mock(return_value=0))
        report = {}
        verifier._cleanup_process(process, report, kill_timeout=.5)
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=.5)
        self.assertTrue(report["cleanupEscalated"])
        self.assertEqual(report["cleanupErrorType"], "OSError")

    def test_cleanup_closes_verified_host_before_launcher_and_releases_handle(self):
        cleanup = getattr(verifier, "_cleanup_owned_processes", None)
        self.assertTrue(callable(cleanup), "Keep both verified native host and launcher for cleanup")
        calls = []

        def process(name):
            return SimpleNamespace(poll=lambda: None,
                terminate=lambda: calls.append((name, "terminate")),
                wait=lambda timeout: calls.append((name, "wait", timeout)),
                close=lambda: calls.append((name, "close")))

        cleanup(process("launcher"), process("native"), {})
        self.assertEqual(calls, [("native", "terminate"), ("native", "wait", 15),
                                 ("native", "close"), ("launcher", "terminate"),
                                 ("launcher", "wait", 15)])

    def test_native_handle_claim_rejects_wrong_parent_without_termination(self):
        claim = getattr(verifier, "_claim_native_process", None)
        self.assertTrue(callable(claim), "Pin a handle only to the verified launcher's child")
        kernel = SimpleNamespace(OpenProcess=Mock(return_value=88), CloseHandle=Mock(),
            WaitForSingleObject=Mock(return_value=0), TerminateProcess=Mock())

        def query(_handle, _kind, info, _size, _returned):
            info._obj.parent_pid = 66  # A different launcher: never terminate it.
            return 0

        native = SimpleNamespace(NtQueryInformationProcess=Mock(side_effect=query))
        with patch.object(ctypes, "WinDLL", side_effect=[kernel, native], create=True):
            with self.assertRaisesRegex(ValueError, "parent"):
                claim(99, SimpleNamespace(pid=77))
        kernel.CloseHandle.assert_called_once_with(88)
        kernel.TerminateProcess.assert_not_called()


if __name__ == "__main__":
    unittest.main()
