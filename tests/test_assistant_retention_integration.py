"""One real end-to-end ownership transfer and revocation regression."""

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import test_assistant_service_response as service_fixture

from codeviz.assistant.contracts import AgentError, JobStatus
from codeviz.assistant.service import ServiceError


class RetentionIntegrationTests(unittest.TestCase):
    def test_completed_root_evidence_survives_view_change_until_actual_revocation(self):
        with TemporaryDirectory() as directory:
            provider = service_fixture.RootWireProvider()
            _case, request, service = service_fixture.AssistantServiceResponseTests._root_case(self, Path(directory), provider)
            try:
                started = service.start(request)
                snapshot = service_fixture.AssistantServiceResponseTests._wait_released(self, service, started.job_id)
                self.assertEqual(snapshot.status, JobStatus.COMPLETED)
                record = service._conversations.logical_state()["conversations"][0]
                self.assertIn(started.job_id, record["retained"])
                service.replace_current(replace(request.expected_identity, publication_revision=2))
                lease = service._borrow_evidence("conversation", provider.evidence_id())
                try:
                    self.assertEqual(lease.resolve_evidence(provider.evidence_id()).proof.evidence_id,
                                     provider.evidence_id())
                    session = lease._context._session
                    self.assertIsNone(session._reader)
                    self.assertIsNone(session._ledger)
                    self.assertIsNone(session._current_state)
                    service._replace_source_consent(None)
                    service._drain_pending_closures()
                    with self.assertRaises(ServiceError):
                        service._prepare_close()
                    self.assertFalse(session._closed)
                    self.assertEqual(service._conversations.evidence_count, 1)
                    with self.assertRaises(AgentError):
                        service._borrow_evidence("conversation", provider.evidence_id())
                    with self.assertRaises(AgentError):
                        lease.resolve_evidence(provider.evidence_id())
                finally:
                    lease.release()
                self.assertTrue(session._closed)
                self.assertEqual(service._conversations.evidence_count, 0)
            finally:
                service.shutdown()
