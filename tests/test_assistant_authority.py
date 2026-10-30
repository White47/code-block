"""Focused C4 trusted authority and replay guards."""

import asyncio
import hashlib
import os
import time
import unittest
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from assistant_service_fixtures import final_answer_event

from codeviz.analysis.project import ProjectAnalysis
from codeviz.analysis.python import ParsedFile
from codeviz.analysis.symbols import SymbolIndex
from codeviz.assistant.authority import AuthorityState
from codeviz.assistant.config import ProviderConfig
from codeviz.assistant.consent import ConsentGrant
from codeviz.assistant.contracts import JobStatus, RequestScope
from codeviz.assistant.events import EventRing
from codeviz.assistant.jobs import JobRequest, _Job
from codeviz.assistant.provider import PreparedRequest, ProviderDraft, ProviderEvent
from codeviz.assistant.service import AssistantService, ServiceError
from codeviz.assistant.snapshot import SnapshotBinding, build_snapshot
from codeviz.assistant.source_policy import SourceConsent
from codeviz.ui.assistant_context import PublicationIdentity
from codeviz.ui.controller import AnalysisStatus


class FakeProvider:
    def __init__(self):
        self.sends = 0
        self.closed = False

    async def prepare(self, draft):
        return PreparedRequest("prepared", "POST", "https://provider.invalid:443/chat/completions",
                               draft.body, draft.max_output_tokens, draft.deadline,
                               len(draft.body) + 64, (), (), True)

    async def dispatch(self, prepared):
        self.sends += 1
        yield final_answer_event()
        yield ProviderEvent("usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')

    async def cancel(self):
        pass

    async def aclose(self):
        self.closed = True


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        source = root / "app.py"
        source.write_bytes(b"value = 1\n")
        self.identity = PublicationIdentity(
            1, "epoch", 1, os.path.normcase(os.path.normpath(str(root))),
            root, (), AnalysisStatus.READY, True)
        parsed = ParsedFile("app.py", "app", None, 1, parsed_success=True,
                            content_hash=hashlib.sha256(source.read_bytes()).hexdigest())
        self.snapshot = build_snapshot(
            ProjectAnalysis((parsed,), SymbolIndex(), ()),
            binding=SnapshotBinding(str(root), (), "epoch", 1))
        self.config = ProviderConfig.from_base_url(
            "https://provider.invalid", "fake", 1)
        self.grant = ConsentGrant(self.identity.project_key, self.config.origin,
                                  1, (), 1, True)
        self.authority = AuthorityState.from_trusted(
            self.config, self.grant, (("conversation", 0),))
        self.provider = FakeProvider()
        self.service = AssistantService(lambda: self.provider)
        self.service._replace_publication(self.identity, self.snapshot)
        self.service._replace_source_consent(SourceConsent(
            self.snapshot.binding, "consent-authority", self.config.origin, ()))

    def tearDown(self):
        self.service.shutdown()

    def request(self, request_id="request"):
        return JobRequest(
            RequestScope("epoch", 1, request_id), "conversation", "question",
            "context", "fake", 1, 1, self.authority.scope_fingerprint,
            ProviderDraft(b'{"model":"fake","messages":[{"role":"user","content":"question"}]}',
                          4, time.monotonic() + 60),
            self.identity)

    def wait_for(self, job_id, status):
        deadline = time.monotonic() + 2
        while self.service.get(job_id).status is not status and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.service.get(job_id).status, status)

    def test_default_authority_fails_closed(self):
        with self.assertRaises(ServiceError) as error:
            self.service.start(self.request())
        self.assertEqual(error.exception.code, "AI_CONSENT_REQUIRED")
        self.assertEqual(self.provider.sends, 0)

    def test_trusted_grant_identity_and_scope_are_required(self):
        self.service._replace_authority(self.authority)
        with self.assertRaises(ServiceError) as error:
            self.service.start(replace(self.request(), normalized_scope="other"))
        self.assertEqual(error.exception.code, "AI_CONSENT_REQUIRED")
        started = self.service.start(self.request())
        self.wait_for(started.job_id, JobStatus.COMPLETED)

    def test_request_model_must_match_current_config(self):
        self.service._replace_authority(self.authority)
        with self.assertRaises(ServiceError) as error:
            self.service.start(replace(self.request(), model="different"))
        self.assertEqual(error.exception.code, "AI_CONSENT_REQUIRED")

    def test_prepared_origin_must_match_current_grant(self):
        class WrongTarget(FakeProvider):
            async def prepare(self, draft):
                prepared = await super().prepare(draft)
                return replace(prepared, endpoint="https://other.invalid/v1/chat")

        self.service.shutdown()
        self.provider = WrongTarget()
        self.service = AssistantService(lambda: self.provider)
        self.service._replace_publication(self.identity, self.snapshot)
        self.service._replace_source_consent(SourceConsent(
            self.snapshot.binding, "consent-authority", self.config.origin, ()))
        self.service._replace_authority(self.authority)
        started = self.service.start(self.request())
        self.wait_for(started.job_id, JobStatus.FAILED)
        self.assertEqual(self.provider.sends, 0)

    def test_same_origin_wrong_endpoint_cannot_dispatch(self):
        class WrongPath(FakeProvider):
            async def prepare(self, draft):
                return replace(await super().prepare(draft),
                               endpoint="https://provider.invalid/other")

        self.service.shutdown()
        self.provider = WrongPath()
        self.service = AssistantService(lambda: self.provider)
        self.service._replace_publication(self.identity, self.snapshot)
        self.service._replace_source_consent(SourceConsent(
            self.snapshot.binding, "consent-authority", self.config.origin, ()))
        self.service._replace_authority(self.authority)
        started = self.service.start(self.request())
        self.wait_for(started.job_id, JobStatus.FAILED)
        self.assertEqual(self.provider.sends, 0)

    def test_connection_slot_shares_global_admission_cap(self):
        self.service._replace_authority(self.authority)
        first = self.service._reserve_connection_slot()
        second = self.service._reserve_connection_slot()
        try:
            with self.assertRaises(ServiceError) as error:
                self.service.start(self.request())
            self.assertEqual(error.exception.code, "AI_BUSY")
            with self.assertRaises(ServiceError) as error:
                self.service._reserve_connection_slot()
            self.assertEqual(error.exception.code, "AI_BUSY")
        finally:
            self.service._release_connection_slot(first)
            self.service._release_connection_slot(second)
        started = self.service.start(self.request())
        self.wait_for(started.job_id, JobStatus.COMPLETED)

    def test_prepared_fields_changed_at_barrier_cannot_dispatch(self):
        entered = Event()
        release = Future()

        async def barrier(prepared):
            entered.set()
            await asyncio.wrap_future(release)
            object.__setattr__(prepared, "body", b'{"changed":true}')

        self.service.shutdown()
        self.service = AssistantService(lambda: self.provider, before_dispatch=barrier)
        self.service._replace_publication(self.identity, self.snapshot)
        self.service._replace_source_consent(SourceConsent(
            self.snapshot.binding, "consent-authority", self.config.origin, ()))
        self.service._replace_authority(self.authority)
        started = self.service.start(self.request())
        try:
            self.assertTrue(entered.wait(2))
            release.set_result(None)
            self.wait_for(started.job_id, JobStatus.FAILED)
            self.assertEqual(self.provider.sends, 0)
        finally:
            if not release.done():
                release.set_result(None)

    def test_replay_precedes_revoked_authority_and_ignores_wire_and_publication_revision(self):
        self.service._replace_authority(self.authority)
        request = self.request()
        started = self.service.start(request)
        self.wait_for(started.job_id, JobStatus.COMPLETED)
        self.service._replace_authority(None)
        replay = replace(request, draft=ProviderDraft(
            b'{"different":"wire"}', 8, time.monotonic() + 60),
            expected_identity=replace(self.identity, publication_revision=2))
        self.assertEqual(self.service.start(replay).job_id, started.job_id)
        with self.assertRaises(ServiceError) as error:
            self.service.start(replace(replay, question="changed"))
        self.assertEqual(error.exception.code, "AI_REQUEST_CONFLICT")
        with self.assertRaises(ServiceError) as error:
            self.service.start(replace(replay, scope=RequestScope("epoch", 1, "new"),
                                       expected_identity=self.identity))
        self.assertEqual(error.exception.code, "AI_CONSENT_REQUIRED")

    def test_conversation_revision_change_closes_prepared_send(self):
        entered = Event()
        release = Future()

        async def barrier(_prepared):
            entered.set()
            await asyncio.shield(asyncio.wrap_future(release))

        self.service.shutdown()
        self.service = AssistantService(lambda: self.provider, before_dispatch=barrier)
        self.service._replace_publication(self.identity, self.snapshot)
        self.service._replace_source_consent(SourceConsent(
            self.snapshot.binding, "consent-authority", self.config.origin, ()))
        self.service._replace_authority(self.authority)
        started = self.service.start(self.request())
        try:
            self.assertTrue(entered.wait(2))
            self.service._set_conversation_revision("conversation", 1)
            self.assertTrue(self.service.get(started.job_id).cancel_requested)
            self.assertEqual(self.service.start(self.request()).job_id, started.job_id)
            with self.assertRaises(ServiceError) as conflict:
                self.service.start(replace(self.request(), question="changed"))
            self.assertEqual(conflict.exception.code, "AI_REQUEST_CONFLICT")
            self.service._drain_pending_closures()
            self.wait_for(started.job_id, JobStatus.CANCELLED)
            self.assertEqual(self.provider.sends, 0)
        finally:
            if not release.done():
                release.set_result(None)

    def test_config_and_consent_revision_changes_close_prepared_send(self):
        for revision_field in ("config_revision", "grant_revision"):
            with self.subTest(revision_field=revision_field):
                entered = Event()
                release = Future()
                provider = FakeProvider()

                async def barrier(_prepared, flag=entered, gate=release):
                    flag.set()
                    await asyncio.shield(asyncio.wrap_future(gate))

                service = AssistantService(lambda current=provider: current,
                                           before_dispatch=barrier)
                try:
                    service._replace_publication(self.identity, self.snapshot)
                    service._replace_source_consent(SourceConsent(
                        self.snapshot.binding, "consent-authority", self.config.origin, ()))
                    service._replace_authority(self.authority)
                    started = service.start(self.request(revision_field))
                    self.assertTrue(entered.wait(2))
                    service._replace_authority(replace(self.authority, **{revision_field: 2}))
                    self.assertTrue(service.get(started.job_id).cancel_requested)
                    service._drain_pending_closures()
                    deadline = time.monotonic() + 2
                    while (service.get(started.job_id).status is not JobStatus.CANCELLED
                           and time.monotonic() < deadline):
                        time.sleep(0.01)
                    self.assertEqual(service.get(started.job_id).status, JobStatus.CANCELLED)
                    if not release.done():
                        release.set_result(None)
                    self.assertEqual(provider.sends, 0)
                finally:
                    if not release.done():
                        release.set_result(None)
                    service.shutdown()

    def test_completion_rechecks_current_authority(self):
        entered = Event()
        release = Future()

        class ClosingProvider(FakeProvider):
            async def aclose(self):
                entered.set()
                await asyncio.wrap_future(release)
                await super().aclose()

        self.service.shutdown()
        self.provider = ClosingProvider()
        self.service = AssistantService(lambda: self.provider)
        self.service._replace_publication(self.identity, self.snapshot)
        self.service._replace_source_consent(SourceConsent(
            self.snapshot.binding, "consent-authority", self.config.origin, ()))
        self.service._replace_authority(self.authority)
        started = self.service.start(self.request())
        try:
            self.assertTrue(entered.wait(2))
            # Simulate a stale current slot before a trusted notification can mark cancellation.
            with self.service._lock:
                self.service._authority = replace(self.authority, grant_revision=2)
            release.set_result(None)
            deadline = time.monotonic() + 2
            while (not self.service.get(started.job_id).resources_released
                   and time.monotonic() < deadline):
                time.sleep(0.01)
            self.assertTrue(self.service.get(started.job_id).resources_released)
            self.assertEqual(self.service.get(started.job_id).status,
                             JobStatus.STALE)
        finally:
            if not release.done():
                release.set_result(None)
            self.service._replace_authority(None)
            self.service._drain_pending_closures()

    def test_authority_replacement_closes_b_operation_before_entry(self):
        self.service._replace_authority(self.authority)
        request = self.request()
        job = _Job("manual", request, request.digest(), EventRing("manual", kind="explanation"))
        job.conversation_revision = 0
        job.authority_origin = self.authority.origin
        job.authority_endpoint = self.authority.endpoint
        job.admission_pending = False
        with self.service._lock:
            self.service._jobs[job.job_id] = job
            self.service._active_job_ids.add(job.job_id)
            self.service._requests[("epoch", "request")] = (job.digest, job.job_id)
        raw = Future()
        entered = Event()
        with patch.object(self.service._b_executor, "submit", return_value=raw):
            future = asyncio.run_coroutine_threadsafe(
                self.service._run_b(job, entered.set), self.service._loop)
            deadline = time.monotonic() + 2
            while job.b_future is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIs(job.b_future, raw)
            self.service._replace_authority(None)
            self.assertTrue(job.cancel_requested)
            self.assertTrue(raw.cancelled() or not entered.is_set())
            self.service._drain_pending_closures()
            try:
                future.result(2)
            except Exception:
                pass
        self.assertFalse(entered.is_set())


if __name__ == "__main__":
    unittest.main()
