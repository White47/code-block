"""C4 phase-review regressions at the internal service boundary."""

import asyncio
import os
import time
import unittest
from concurrent.futures import Future
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, current_thread
from unittest.mock import patch

from assistant_service_fixtures import EMPTY_SCOPE_FINGERPRINT, final_answer_event

from codeviz.analysis.project import ProjectAnalysis
from codeviz.analysis.python import ParsedFile
from codeviz.analysis.symbols import SymbolIndex
from codeviz.assistant import service as service_module
from codeviz.assistant.authority import AuthorityState
from codeviz.assistant.contracts import JobStatus, RequestScope
from codeviz.assistant.jobs import JobRequest
from codeviz.assistant.provider import PreparedRequest, ProviderDraft, ProviderEvent
from codeviz.assistant.service import AssistantService
from codeviz.assistant.snapshot import SnapshotBinding, build_snapshot
from codeviz.assistant.source_policy import SourceConsent
from codeviz.ui.assistant_context import PublicationIdentity
from codeviz.ui.controller import AnalysisStatus


class CompletingProvider:
    def __init__(self):
        self.sends = 0

    async def prepare(self, draft):
        return PreparedRequest("prepared", "POST", "https://provider.invalid:443/v1/chat",
                               draft.body, draft.max_output_tokens, draft.deadline,
                               len(draft.body) + 64, (), (), True)

    async def dispatch(self, prepared):
        self.sends += 1
        yield final_answer_event()
        yield ProviderEvent("usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')

    async def cancel(self):
        pass

    async def aclose(self):
        pass


class C4ReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.file = self.root / "app.py"
        self.file.write_bytes(b"value = 1\n")
        self.identity = PublicationIdentity(
            1, "epoch", 1, os.path.normcase(os.path.normpath(str(self.root))),
            self.root, (), AnalysisStatus.READY, True)
        parsed = ParsedFile("app.py", "app", None, 1, parsed_success=True,
                            content_hash=sha256(self.file.read_bytes()).hexdigest())
        self.snapshot = build_snapshot(ProjectAnalysis((parsed,), SymbolIndex(), ()),
                                       binding=SnapshotBinding(str(self.root), (), "epoch", 1))
        self.request = JobRequest(
            RequestScope("epoch", 1, "review"), "conversation", "question", "digest",
            "fake", 1, 1, EMPTY_SCOPE_FINGERPRINT, ProviderDraft(
                b'{"messages":[{"role":"user","content":"question"}]}',
                4, time.monotonic() + 60),
            self.identity)

    def service(self, provider, **kwargs):
        service = AssistantService(lambda: provider, **kwargs)
        self.addCleanup(service.shutdown)
        service._replace_publication(self.identity, self.snapshot)
        service._replace_source_consent(SourceConsent(
            self.snapshot.binding, "consent-review", "https://provider.invalid:443", ()))
        service._replace_authority(AuthorityState(
            True, 1, 1, self.identity.project_key, "https://provider.invalid:443",
            EMPTY_SCOPE_FINGERPRINT, (("conversation", 0),), "fake",
            "https://provider.invalid:443/v1/chat"))
        return service

    def wait_status(self, service, job_id, status):
        deadline = time.monotonic() + 3
        while service.get(job_id).status is not status and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(service.get(job_id).status, status)

    def test_changed_source_before_dispatch_never_sends(self):
        entered = Event()
        release = Future()
        provider = CompletingProvider()

        async def barrier(_prepared):
            entered.set()
            await asyncio.wrap_future(release)

        service = self.service(provider, before_dispatch=barrier)
        started = service.start(self.request)
        self.assertTrue(entered.wait(3))
        self.file.write_bytes(b"value = 2\n")
        release.set_result(None)
        self.wait_status(service, started.job_id, JobStatus.STALE)
        self.assertEqual(provider.sends, 0)

    def test_changed_source_before_completion_stays_stale(self):
        entered = Event()
        release = Future()

        class PausedProvider(CompletingProvider):
            async def dispatch(self, prepared):
                self.sends += 1
                yield final_answer_event()
                entered.set()
                await asyncio.wrap_future(release)
                yield ProviderEvent("usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')

        service = self.service(PausedProvider())
        started = service.start(self.request)
        self.assertTrue(entered.wait(3))
        self.file.write_bytes(b"value = 2\n")
        release.set_result(None)
        self.wait_status(service, started.job_id, JobStatus.STALE)

    def test_empty_provider_stream_fails(self):
        class EmptyProvider(CompletingProvider):
            async def dispatch(self, prepared):
                self.sends += 1
                if False:
                    yield ProviderEvent("text", b"")

        provider = EmptyProvider()
        service = self.service(provider)
        started = service.start(self.request)
        self.wait_status(service, started.job_id, JobStatus.FAILED)
        self.assertEqual(provider.sends, 1)

    def test_retired_completed_becomes_stale_on_dependency_publication(self):
        service = self.service(CompletingProvider())
        started = service.start(self.request)
        self.wait_status(service, started.job_id, JobStatus.COMPLETED)
        prior = service.poll(started.job_id, 0)["seq"]
        service.replace_current(replace(self.identity, publication_revision=2,
                                        eligible=False, status=AnalysisStatus.OUTDATED))
        self.assertEqual(service.get(started.job_id).status, JobStatus.STALE)
        delta = service.poll(started.job_id, prior)
        self.assertFalse(delta["reset"])
        self.assertEqual(delta["events"][0]["status"], JobStatus.STALE.value)

    def test_view_only_publication_keeps_completed_result(self):
        service = self.service(CompletingProvider())
        started = service.start(self.request)
        self.wait_status(service, started.job_id, JobStatus.COMPLETED)
        prior = service.poll(started.job_id, 0)["seq"]
        service.replace_current(replace(self.identity, publication_revision=2))
        self.assertEqual(service.get(started.job_id).status, JobStatus.COMPLETED)
        self.assertEqual(service.poll(started.job_id, prior)["seq"], prior)

    def test_answer_without_usage_signal_fails(self):
        class MissingUsage(CompletingProvider):
            async def dispatch(self, prepared):
                self.sends += 1
                yield final_answer_event()

        service = self.service(MissingUsage())
        started = service.start(self.request)
        self.wait_status(service, started.job_id, JobStatus.FAILED)

    def test_freshness_runs_on_b_executor_without_service_lock(self):
        service = self.service(CompletingProvider())
        checked = []
        original = service_module.verify_snapshot_freshness

        def verify(*args, **kwargs):
            self.assertTrue(current_thread().name.startswith("assistant-b"))
            self.assertTrue(service._lock.acquire(blocking=False))
            service._lock.release()
            checked.append(True)
            return original(*args, **kwargs)

        with patch.object(service_module, "verify_snapshot_freshness", side_effect=verify):
            started = service.start(self.request)
            self.wait_status(service, started.job_id, JobStatus.COMPLETED)
        self.assertEqual(len(checked), 3)

    def test_source_change_while_provider_closes_prevents_completion(self):
        closing = Event()
        release = Future()

        class SlowClose(CompletingProvider):
            async def aclose(self):
                closing.set()
                await asyncio.wrap_future(release)

        service = self.service(SlowClose())
        started = service.start(self.request)
        self.assertTrue(closing.wait(3))
        self.file.write_bytes(b"value = 3\n")
        release.set_result(None)
        self.wait_status(service, started.job_id, JobStatus.STALE)

    def test_retired_poll_returns_recent_terminal_confirmation_delta(self):
        service = self.service(CompletingProvider())
        started = service.start(self.request)
        self.wait_status(service, started.job_id, JobStatus.COMPLETED)
        current = service.poll(started.job_id, 0)
        recent = service.poll(started.job_id, current["seq"] - 1)
        self.assertFalse(recent["reset"])
        self.assertEqual([event["seq"] for event in recent["events"]], [current["seq"]])
        self.assertEqual(recent["events"][0]["type"], "confirmation")
