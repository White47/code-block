"""Native close cleanup waits for editor approval and never grants an early permit."""

import inspect
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import test_assistant_jobs as jobs_fixture

from codeviz.assistant.service import AssistantService
from codeviz.ui.desktop import _NativeCloseGuard


class AssistantCloseBoundaryTests(unittest.TestCase):
    def test_preparing_native_close_does_not_destroy_reusable_worker(self):
        self.assertTrue(callable(getattr(AssistantService, "_prepare_close", None)),
                        "Missing two-phase assistant close cleanup")
        with TemporaryDirectory() as directory:
            harness = SimpleNamespace(root=Path(directory).resolve())
            identity, request = jobs_fixture.JobsFirstSliceTests._request(harness, "before-close")
            service = AssistantService(jobs_fixture.FakeProvider)
            try:
                service.replace_current(identity)
                jobs_fixture._install_authority(service, identity)
                service._prepare_close()
                self.assertTrue(service._loop.is_running())
                self.assertTrue(service._thread.is_alive())
                started = service.start(request)
                jobs_fixture.JobsFirstSliceTests._wait_status(self, service, started.job_id,
                                                            jobs_fixture.JobStatus.COMPLETED)
            finally:
                service.shutdown()

    def test_veto_blocked_cleanup_failure_and_retry_preserve_native_close_guard(self):
        self.assertIn("before_destroy", inspect.signature(_NativeCloseGuard).parameters,
                      "Missing assistant cleanup seam")
        entered, release, failed, destroyed = Event(), Event(), Event(), Event()
        state = {"fail": True}

        def run_js(script):
            if "codeviz:close-failed" in script:
                failed.set()

        window = SimpleNamespace(run_js=Mock(side_effect=run_js), destroy=Mock())

        def cleanup():
            self.assertFalse(guard._lock._is_owned())
            entered.set()
            if not release.wait(3):
                raise RuntimeError("cleanup test did not release")
            if state["fail"]:
                raise RuntimeError("private cleanup diagnostic must not escape")

        guard = _NativeCloseGuard(window, before_destroy=cleanup)
        guard.loaded()
        self.assertFalse(guard.closing())
        guard.respond(guard._request, False)
        self.assertFalse(entered.is_set())
        self.assertFalse(guard.closing())
        guard.respond(guard._request, True)
        try:
            self.assertTrue(entered.wait(2))
            self.assertFalse(guard.closing(), "Blocked cleanup must not grant the native closing permit")
            window.destroy.assert_not_called()
        finally:
            release.set()
        self.assertTrue(failed.wait(2))
        window.destroy.assert_not_called()
        self.assertNotIn("private cleanup diagnostic", " ".join(call.args[0] for call in window.run_js.call_args_list))
        state["fail"] = False
        window.destroy.side_effect = destroyed.set
        self.assertFalse(guard.closing())
        guard.respond(guard._request, True)
        self.assertTrue(destroyed.wait(2))
        window.destroy.assert_called_once()
