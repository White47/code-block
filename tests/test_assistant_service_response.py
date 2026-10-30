"""Service completion must pass the response gate, even with a clean transport."""

import asyncio
import hashlib
import json
import os
import time
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, current_thread
from unittest.mock import patch

from assistant_service_fixtures import EMPTY_SCOPE_FINGERPRINT
from assistant_tool_fixtures import make_source_case
from tests.test_assistant_jobs import FakeProvider, _install_authority

from codeviz.assistant.authority import AuthorityState
from codeviz.assistant.contracts import AgentError, JobStatus, RequestScope
from codeviz.assistant.jobs import JobRequest, RootRetrieval, _Job
from codeviz.assistant.provider import (
    PreparedRequest,
    ProviderDraft,
    ProviderEvent,
    WireOccurrence,
)
from codeviz.assistant.response_session import RootSourceFragment, inject_root
from codeviz.assistant.retained_evidence import RetainedEvidenceIdentity
from codeviz.assistant.sdk_provider import _draft, _schema
from codeviz.assistant.service import AssistantService
from codeviz.assistant.snapshot import AnchorRecord
from codeviz.assistant.tool_contracts import EntityRef
from codeviz.ui.assistant_context import PublicationIdentity
from codeviz.ui.controller import AnalysisStatus


class RawAnswerProvider(FakeProvider):
    async def dispatch(self, prepared):
        self.sends += 1
        yield ProviderEvent("answer", b'{"index":0,"last":true,"content":"ok"}')
        yield ProviderEvent("usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')


def _answer(evidence_id: str | None = None) -> str:
    sections = [{"kind": kind, "claims": []} for kind in (
        "summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")]
    if evidence_id is None:
        sections[0]["claims"] = [{
            "text": "Unable to confirm the function without evidence.",
            "basis": "unknown", "evidenceIds": [], "relationEvidenceId": None,
        }]
    else:
        sections[0]["claims"] = [{
            "text": "The saved source defines a value.",
            "basis": "static", "evidenceIds": [evidence_id], "relationEvidenceId": None,
        }]
    return json.dumps({"sections": sections}, separators=(",", ":"))


class RootWireProvider(FakeProvider):
    def __init__(self, *, forged: bool = False):
        super().__init__()
        self.forged = forged
        self.logical = None
        self.prepared = None

    async def prepare(self, draft):
        logical = json.loads(draft.body)
        self.logical = logical
        self.assert_no_provided_ids(logical)
        wire = json.dumps({"messages": logical["messages"]}, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        source = tuple(WireOccurrence(tag["pointer"], tag["start"], tag["end"], tag["file"])
                       for tag in logical["sourceTags"])
        metadata = tuple(WireOccurrence(tag["pointer"], tag["start"], tag["end"], tag["file"])
                         for tag in logical.get("metadataTags", []))
        prepared = PreparedRequest("root-prepared", "POST",
                                   "https://provider.invalid:443/v1/chat", wire,
                                   draft.max_output_tokens, draft.deadline,
                                   len(wire), source, metadata, True)
        self.prepared = prepared
        return prepared

    @staticmethod
    def assert_no_provided_ids(value):
        if isinstance(value, dict):
            if "providedIds" in value:
                raise AssertionError("providedIds leaked into the provider draft")
            for child in value.values():
                RootWireProvider.assert_no_provided_ids(child)
        elif isinstance(value, list):
            for child in value:
                RootWireProvider.assert_no_provided_ids(child)

    def evidence_id(self):
        assert self.logical is not None
        tags = self.logical["sourceTags"]
        assert len(tags) == 1, "The fixture must disclose exactly one root header"
        tag = tags[0]
        pointer = tag["pointer"].split("/")
        assert len(pointer) == 4 and pointer[1] == "messages" and pointer[3] == "content"
        root = self.logical["messages"][int(pointer[2])]["content"]
        header = json.loads(root[:tag["start"]])
        evidence_id = header["evidenceId"]
        assert isinstance(evidence_id, str) and evidence_id
        catalogs = []
        for message in self.logical["messages"]:
            content = message.get("content")
            if not isinstance(content, str):
                continue
            _prefix, separator, suffix = content.partition("\n")
            if not separator:
                continue
            try:
                catalog, _end = json.JSONDecoder().raw_decode(suffix)
            except ValueError:
                continue
            if isinstance(catalog, dict) and "receiptEvidenceIds" in catalog:
                catalogs.append(catalog)
        assert catalogs, "Final guidance must carry its service-owned receipt catalog"
        assert all(catalog["receiptEvidenceIds"] == [evidence_id] for catalog in catalogs)
        assert all(catalog["relationEvidenceIds"] == [] for catalog in catalogs)
        return evidence_id

    async def dispatch(self, prepared):
        self.sends += 1
        evidence_id = "forged-request-id" if self.forged else self.evidence_id()
        payload = json.dumps({"index": 0, "last": True,
                              "content": _answer(evidence_id)}, separators=(",", ":"))
        yield ProviderEvent("answer", payload.encode("utf-8"))
        yield ProviderEvent("usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')


class AssistantServiceResponseTests(unittest.TestCase):
    def test_b_completion_during_failure_transition_cannot_skip_session_close(self):
        with TemporaryDirectory() as directory:
            provider = RootWireProvider()
            _case, request, service = self._root_case(Path(directory), provider)
            entered, release, closed = Event(), Event(), Event()
            module = __import__("codeviz.assistant.service", fromlist=["create_tool_session"])
            original_create = module.create_tool_session
            original_fresh = module.verify_snapshot_freshness

            def create(*args, **kwargs):
                session = original_create(*args, **kwargs)
                close = session.close

                def tracked_close():
                    close()
                    closed.set()

                session.close = tracked_close
                return session

            def blocked_fresh(*args, **kwargs):
                entered.set()
                release.wait(3)
                return original_fresh(*args, **kwargs)

            def interleaved_read(job, name):
                value = object.__getattribute__(job, name)
                # Force B to finish after the old cleanup branch observes a
                # pending future, but before it publishes its terminal status.
                if (name == "b_future" and value is not None and provider.closed
                        and current_thread() is service._thread
                        and object.__getattribute__(job, "dispatch_closed")
                        and object.__getattribute__(job, "status") is JobStatus.RUNNING):
                    finished = Event()
                    value.add_done_callback(lambda _: finished.set())
                    release.set()
                    if not finished.wait(2):
                        raise AssertionError("B did not finish at the forced transition")
                return value

            try:
                with patch.object(module, "create_tool_session", side_effect=create), \
                        patch.object(module, "verify_snapshot_freshness", side_effect=blocked_fresh), \
                        patch.object(module.asyncio, "timeout_at", side_effect=lambda _: asyncio.timeout(0.2)), \
                        patch.object(_Job, "__getattribute__", interleaved_read):
                    started = service.start(request)
                    self.assertTrue(entered.wait(2))
                    deadline = time.monotonic() + 2
                    while service.get(started.job_id).status is not JobStatus.FAILED and time.monotonic() < deadline:
                        time.sleep(0.005)
                    self.assertEqual(service.get(started.job_id).status, JobStatus.FAILED)
                    release.set()
                    self._wait_released(service, started.job_id)
                    self.assertTrue(closed.is_set())
            finally:
                release.set()
                service.shutdown()

    def test_revocation_blocks_completion_even_without_answer_citations(self):
        class UncitedProvider(RootWireProvider):
            async def dispatch(self, prepared):
                self.sends += 1
                yield ProviderEvent("answer", json.dumps({
                    "index": 0, "last": True, "content": _answer(),
                }).encode())
                yield ProviderEvent("usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')

        with TemporaryDirectory() as directory:
            provider = UncitedProvider()
            _case, request, service = self._root_case(Path(directory), provider)

            async def revoke_on_close():
                provider.closed = True
                service._replace_source_consent(None)

            provider.aclose = revoke_on_close
            try:
                started = service.start(request)
                snapshot = self._wait_released(service, started.job_id)
                self.assertEqual(snapshot.status, JobStatus.FAILED)
                self.assertEqual(snapshot.issue.code, "AI_CONSENT_REQUIRED")
                record = service._conversations.logical_state()["conversations"][0]
                self.assertEqual(record["results"], {})
            finally:
                service.shutdown()

    def test_timed_out_b_worker_closes_session_before_confirming_release(self):
        with TemporaryDirectory() as directory:
            provider = RootWireProvider()
            _case, request, service = self._root_case(Path(directory), provider)
            entered, release, closed = Event(), Event(), Event()
            module = __import__("codeviz.assistant.service", fromlist=["create_tool_session"])
            original_create = module.create_tool_session
            original_fresh = module.verify_snapshot_freshness

            def create(*args, **kwargs):
                session = original_create(*args, **kwargs)
                close = session.close

                def tracked_close():
                    close()
                    closed.set()

                session.close = tracked_close
                return session

            def blocked_fresh(*args, **kwargs):
                entered.set()
                release.wait(3)
                return original_fresh(*args, **kwargs)

            try:
                with patch.object(module, "create_tool_session", side_effect=create), \
                        patch.object(module, "verify_snapshot_freshness", side_effect=blocked_fresh), \
                        patch.object(module.asyncio, "timeout_at", side_effect=lambda _: asyncio.timeout(0.2)):
                    started = service.start(request)
                    self.assertTrue(entered.wait(2))
                    deadline = time.monotonic() + 2
                    while service.get(started.job_id).status is not JobStatus.FAILED and time.monotonic() < deadline:
                        time.sleep(0.005)
                    self.assertEqual(service.get(started.job_id).status, JobStatus.FAILED)
                    self.assertFalse(service.get(started.job_id).resources_released)
                    release.set()
                    self._wait_released(service, started.job_id)
                    self.assertTrue(closed.is_set())
            finally:
                release.set()
                service.shutdown()

    def test_appended_root_attribution_obeys_real_adapter_pointer_order(self):
        logical = {
            "version": 1, "mode": "final", "tools": [], "answerSchema": _schema(),
            "messages": [{"role": "user", "content": "prior"} for _ in range(10)],
            "sourceTags": [{"pointer": "/messages/9/content", "start": 0,
                            "end": 5, "file": "app.py"}], "metadataTags": [],
        }
        draft = ProviderDraft(json.dumps(logical).encode(), 512, time.monotonic() + 120)
        fragment = RootSourceFragment("id", "app.py", "value = 1",
                                      AnchorRecord("app.py", 1, 0, 1, 9), False)
        injected, _, _, _ = inject_root(draft, fragment)
        _draft(injected, time.monotonic())

    def _wait_released(self, service, job_id):
        deadline = time.monotonic() + 3
        snapshot = service.get(job_id)
        while not snapshot.resources_released and time.monotonic() < deadline:
            time.sleep(0.005)
            snapshot = service.get(job_id)
        self.assertTrue(snapshot.resources_released)
        return snapshot

    def _root_case(self, root, provider):
        case = make_source_case(root, b"value = 1\n")
        case.consent = replace(case.consent, provider_origin="https://provider.invalid:443")
        binding = case.snapshot.binding
        identity = PublicationIdentity(
            1, binding.project_epoch, binding.analysis_generation,
            os.path.normcase(os.path.normpath(str(root.resolve()))), root.resolve(), (),
            AnalysisStatus.READY, True)
        fingerprint = hashlib.sha256(json.dumps(
            case.consent.allowed_files, ensure_ascii=True,
            separators=(",", ":")).encode("ascii")).hexdigest()
        authority = AuthorityState(
            True, 1, 1, identity.project_key, "https://provider.invalid:443",
            fingerprint, (("conversation", 0),), "fake",
            "https://provider.invalid:443/v1/chat")
        logical = {
            "version": 1, "mode": "final",
            "messages": [{"role": "user", "content": "question"}],
            "tools": [], "answerSchema": {}, "sourceTags": [], "metadataTags": [],
        }
        request = JobRequest(
            RequestScope(binding.project_epoch, binding.analysis_generation, "root-answer"),
            "conversation", "question", "context-digest", "fake", 1, 1, fingerprint,
            ProviderDraft(json.dumps(logical, separators=(",", ":")).encode("utf-8"),
                          512, time.monotonic() + 120),
            identity, RootRetrieval(case.anchor, EntityRef("code", "seed")))
        service = AssistantService(lambda: provider)
        service._replace_publication(identity, case.snapshot)
        service._replace_source_consent(case.consent)
        service._replace_authority(authority)
        return case, request, service

    def test_unvalidated_raw_answer_never_becomes_a_completed_result(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            identity = PublicationIdentity(
                1, "epoch", 1, os.path.normcase(os.path.normpath(str(root))),
                root, (), AnalysisStatus.READY, True)
            request = JobRequest(
                RequestScope("epoch", 1, "raw-answer"), "conversation", "question",
                "context-digest", "fake", 1, 1, EMPTY_SCOPE_FINGERPRINT,
                ProviderDraft(b'{"model":"fake"}', 4, time.monotonic() + 120), identity)
            provider = RawAnswerProvider()
            service = AssistantService(lambda: provider)
            try:
                service.replace_current(identity)
                _install_authority(service, identity)
                receipt = service.start(request)
                deadline = time.monotonic() + 2
                snapshot = service.get(receipt.job_id)
                while not snapshot.resources_released and time.monotonic() < deadline:
                    time.sleep(0.005)
                    snapshot = service.get(receipt.job_id)
                self.assertEqual(snapshot.status, JobStatus.FAILED)
                self.assertEqual(snapshot.issue.code, "AI_EVIDENCE_INVALID")
                self.assertTrue(snapshot.resources_released)
                self.assertTrue(provider.closed)
                conversations = service._conversations.logical_state()["conversations"]
                self.assertTrue(all(not item["results"] for item in conversations))
            finally:
                service.shutdown()

    def test_actual_root_receipt_can_complete_only_as_validated_public_answer(self):
        with TemporaryDirectory() as directory:
            provider = RootWireProvider()
            case, request, service = self._root_case(Path(directory), provider)
            observed = []

            async def before_dispatch(prepared):
                with service._lock:
                    ledger = next(iter(service._jobs.values())).ledger
                    observed.append(ledger.usage)

            service._before_dispatch = before_dispatch
            try:
                started = service.start(request)
                snapshot = self._wait_released(service, started.job_id)
                self.assertEqual(snapshot.status, JobStatus.COMPLETED)
                self.assertIsNone(snapshot.issue)
                self.assertEqual(provider.sends, 1)
                self.assertEqual(len(observed), 1)
                self.assertEqual(observed[0].tool_calls, 1)
                self.assertEqual(observed[0].model_requests, 1)
                self.assertGreaterEqual(len(provider.prepared.source_occurrences), 1)
                self.assertEqual({item.file for item in provider.prepared.source_occurrences},
                                 {"app.py"})
                record = service._conversations.logical_state()["conversations"][0]
                public = json.loads(record["results"][started.job_id]["answer"])
                self.assertEqual(public["sections"][0]["claims"][0]["basis"], "inference")
                self.assertEqual(public["evidence"][0]["evidenceId"], provider.evidence_id())
                assistant = record["messages"][-1]
                self.assertFalse(assistant["sourceFree"])
                self.assertEqual(assistant["sourceTags"], [])
                self.assertEqual(service._conversations._select_eligible_history(
                    identity.project_key if (identity := request.expected_identity) else "",
                    "conversation", 0, project_epoch=request.scope.project_epoch,
                    origin="https://provider.invalid:443",
                    generation=request.scope.analysis_generation,
                    consent_revision=1, config_revision=1,
                    normalized_scope=request.normalized_scope), ())
                self.assertEqual(case.ledger.usage.tool_calls, 0)
            finally:
                service.shutdown()

    def test_completed_root_transfers_retained_owner_until_authority_changes(self):
        with TemporaryDirectory() as directory:
            provider = RootWireProvider()
            case, request, service = self._root_case(Path(directory), provider)
            sessions = []
            closes = []
            original_create = __import__(
                "codeviz.assistant.service", fromlist=["create_tool_session"]
            ).create_tool_session

            def create(*args, **kwargs):
                session = original_create(*args, **kwargs)
                original_close = session.close

                def close():
                    self.assertFalse(service._lock.locked())
                    self.assertFalse(service._conversations._lock.locked())
                    closes.append(True)
                    original_close()

                session.close = close
                sessions.append(session)
                return session

            identity = RetainedEvidenceIdentity(
                request.scope.project_epoch, request.scope.analysis_generation,
                request.config_revision, request.consent_revision, 0,
                case.consent.provider_origin, request.normalized_scope, case.consent,
            )
            try:
                with patch("codeviz.assistant.service.create_tool_session", side_effect=create):
                    started = service.start(request)
                    snapshot = self._wait_released(service, started.job_id)
                self.assertEqual(snapshot.status, JobStatus.COMPLETED)
                self.assertEqual(closes, [])
                self.assertTrue(sessions[0]._retention_manifest)
                lease = service._conversations._borrow_retained(
                    request.expected_identity.project_key, request.conversation_id,
                    started.job_id, identity,
                )
                self.assertEqual(
                    lease.resolve_evidence(provider.evidence_id()).proof.evidence_id,
                    provider.evidence_id(),
                )
                lease.release()

                service.replace_current(replace(
                    request.expected_identity,
                    publication_revision=request.expected_identity.publication_revision + 1,
                ))
                service._drain_pending_closures()
                service._conversations._borrow_retained(
                    request.expected_identity.project_key, request.conversation_id,
                    started.job_id, identity,
                ).release()

                service._replace_authority(replace(service._authority, config_revision=2))
                service._drain_pending_closures()
                self.assertEqual(closes, [True])
                with self.assertRaises(ValueError):
                    service._conversations._borrow_retained(
                        request.expected_identity.project_key, request.conversation_id,
                        started.job_id, identity,
                    )
            finally:
                service.shutdown()

    def test_unprovided_id_from_root_wire_fails_closed_without_result(self):
        with TemporaryDirectory() as directory:
            provider = RootWireProvider(forged=True)
            _case, request, service = self._root_case(Path(directory), provider)
            try:
                started = service.start(request)
                snapshot = self._wait_released(service, started.job_id)
                self.assertEqual(snapshot.status, JobStatus.FAILED)
                self.assertEqual(snapshot.issue.code, "AI_EVIDENCE_INVALID")
                record = service._conversations.logical_state()["conversations"][0]
                self.assertEqual(record["results"], {})
            finally:
                service.shutdown()

    def test_failed_root_evidence_is_inert_and_removed_on_scope_revoke(self):
        with TemporaryDirectory() as directory:
            provider = RootWireProvider(forged=True)
            _case, request, service = self._root_case(Path(directory), provider)
            try:
                started = service.start(request)
                snapshot = self._wait_released(service, started.job_id)
                self.assertEqual(snapshot.status, JobStatus.FAILED)
                record = service._conversations.logical_state()["conversations"][0]
                self.assertEqual(len(record["evidence"]), 1)
                evidence_id = next(iter(record["evidence"]))
                self.assertEqual(set(record["bindings"]), {evidence_id})
                self.assertEqual(record["retained"], {})
                with self.assertRaises(AgentError):
                    service._borrow_evidence("conversation", evidence_id)

                service._replace_runtime_authority(None, None)
                service._drain_pending_closures()
                self.assertEqual(service._conversations.evidence_count, 0)
            finally:
                service.shutdown()

    def test_failed_evidence_capacity_decline_preserves_original_failure(self):
        with TemporaryDirectory() as directory:
            provider = RootWireProvider(forged=True)
            _case, request, service = self._root_case(Path(directory), provider)
            service._conversations.MAX_EVIDENCE = 0
            try:
                started = service.start(request)
                snapshot = self._wait_released(service, started.job_id)
                self.assertEqual(snapshot.status, JobStatus.FAILED)
                self.assertEqual(snapshot.issue.code, "AI_EVIDENCE_INVALID")
                record = service._conversations.logical_state()["conversations"][0]
                self.assertEqual(record["evidence"], {})
                self.assertEqual(record["bindings"], {})
                self.assertEqual(record["results"], {})
            finally:
                service.shutdown()

    def test_cancel_after_root_retrieval_closes_request_session(self):
        with TemporaryDirectory() as directory:
            provider = RootWireProvider()
            _case, request, service = self._root_case(Path(directory), provider)
            entered = Event()
            release = Event()
            closed = Event()
            original_create = __import__(
                "codeviz.assistant.service", fromlist=["create_tool_session"]).create_tool_session

            def create(*args, **kwargs):
                session = original_create(*args, **kwargs)
                original_close = session.close

                def close():
                    original_close()
                    closed.set()

                session.close = close
                return session

            async def hold(_prepared):
                entered.set()
                while not release.is_set():
                    await __import__("asyncio").sleep(0.005)

            service._before_dispatch = hold
            try:
                with patch("codeviz.assistant.service.create_tool_session", side_effect=create):
                    started = service.start(request)
                    self.assertTrue(entered.wait(3))
                    service.cancel(started.job_id)
                    snapshot = self._wait_released(service, started.job_id)
                    self.assertEqual(snapshot.status, JobStatus.CANCELLED)
                    self.assertTrue(closed.wait(1))
                    self.assertEqual(provider.sends, 0)
            finally:
                release.set()
                service.shutdown()


if __name__ == "__main__":
    unittest.main()
