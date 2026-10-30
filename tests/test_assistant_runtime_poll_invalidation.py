"""Invalidated retired results must advance their authoritative poll cursor."""

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import test_assistant_jobs as fixture

from codeviz.assistant.contracts import JobStatus
from codeviz.assistant.service import AssistantService


class RuntimePollInvalidationTests(unittest.TestCase):
    def test_retired_result_revocation_advances_cursor_and_returns_stale_status(self):
        with TemporaryDirectory() as directory:
            harness = SimpleNamespace(root=Path(directory).resolve())
            identity, request = fixture.JobsFirstSliceTests._request(harness, "retired-clear")
            service = AssistantService(fixture.FakeProvider)
            try:
                service.replace_current(identity)
                fixture._install_authority(service, identity)
                started = service.start(request)
                deadline = time.monotonic() + 2
                while not service.get(started.job_id).resources_released and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(service.get(started.job_id).resources_released)
                self.assertEqual(service.get(started.job_id).status, JobStatus.COMPLETED)
                before = service.poll(started.job_id, 0)
                service._replace_runtime_authority(None, None)
                after = service.poll(started.job_id, before["seq"])
                self.assertGreater(after["seq"], before["seq"])
                self.assertEqual(after["status"], "Stale")
                self.assertEqual(after["events"][-1]["status"], "Stale")
            finally:
                service.shutdown()
