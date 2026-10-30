"""Local conversation operations require neither analysis nor an AI worker."""

import asyncio
import json
import os
import time
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

from test_assistant_connection_runner import FakeProvider, _valid_events

from codeviz.assistant.answer_public import answer_to_public
from codeviz.assistant.bridge_requests import (
    ClearRequest,
    ConnectionRequest,
    ProjectRequest,
    parse_configure,
)
from codeviz.assistant.contracts import JobStatus, RequestScope
from codeviz.assistant.events import EventRing
from codeviz.assistant.jobs import JobRequest, _Job
from codeviz.assistant.provider import ProviderDraft
from codeviz.assistant.response import Answer, ModelClaim, Section
from codeviz.assistant.service import ServiceError, _AssistantRuntime
from codeviz.ui.assistant_context import AssistantContext, PublicationIdentity
from codeviz.ui.controller import AnalysisStatus


def context(*, snapshot=None, epoch="local-epoch", revision=1, root=None):
    root = root or Path(os.path.abspath("runtime-fixture"))
    return AssistantContext(revision, epoch, 1, os.path.normcase(os.path.normpath(str(root))),
                            root, () if snapshot is None else snapshot.binding.ignore_paths,
                            AnalysisStatus.NOT_ANALYZED if snapshot is None else AnalysisStatus.READY,
                            snapshot is not None, snapshot, {}, {})


def publish(runtime, ctx):
    identity = PublicationIdentity(ctx.publication_revision, ctx.project_epoch,
        ctx.analysis_generation, ctx.project_key, ctx.root, ctx.ignore_paths, ctx.status, ctx.eligible)
    runtime._replace_publication(identity, ctx.snapshot)


class RuntimeConversationTests(unittest.TestCase):
    def test_unconfigured_local_new_clear_and_exact_empty_state_are_memory_only(self):
        runtime = _AssistantRuntime()
        self.addCleanup(runtime.shutdown)
        ctx = context()
        publish(runtime, ctx)
        request = ProjectRequest(ctx.project_epoch)
        with patch("codeviz.assistant.service.AssistantService", side_effect=AssertionError("worker")), \
                patch("codeviz.assistant.provider.create_provider", side_effect=AssertionError("SDK")), \
                patch.object(Path, "open", side_effect=AssertionError("disk")):
            self.assertEqual(runtime.get_state(request, ctx), {
                "projectEpoch": ctx.project_epoch, "analysisGeneration": 1, "configRevision": 0,
                "conversations": [], "grant": None, "activeJobs": []})
            result = runtime.new_conversation(request, ctx)
            self.assertEqual(set(result), {"conversationId", "evictedConversationIds"})
            self.assertEqual(result["evictedConversationIds"], [])
            name = result["conversationId"]
            self.assertEqual(runtime.clear_conversation(ClearRequest(ctx.project_epoch, name), ctx),
                             {"cleared": True, "conversationRevision": 1})
            self.assertEqual(runtime.get_state(request, ctx)["conversations"], [
                {"conversationId": name, "conversationRevision": 1, "messages": []}])
            self.assertIsNone(runtime._engine)

    def test_superseded_capture_and_foreign_epoch_cannot_mutate_current_store(self):
        runtime = _AssistantRuntime()
        self.addCleanup(runtime.shutdown)
        old = context()
        publish(runtime, old)
        current = replace(old, publication_revision=2)
        publish(runtime, current)
        for request, ctx in ((ProjectRequest(old.project_epoch), old),
                             (ProjectRequest("foreign"), current)):
            with self.assertRaises(ServiceError) as caught:
                runtime.new_conversation(request, ctx)
            self.assertEqual(caught.exception.code, "AI_RESULT_UNAVAILABLE")
        self.assertEqual(runtime.get_state(ProjectRequest(current.project_epoch), current)["conversations"], [])

    def test_eleventh_local_conversation_evicts_inactive_store_entry(self):
        runtime = _AssistantRuntime()
        self.addCleanup(runtime.shutdown)
        ctx = context()
        publish(runtime, ctx)
        request = ProjectRequest(ctx.project_epoch)
        first = runtime.new_conversation(request, ctx)["conversationId"]
        for _ in range(9):
            runtime.new_conversation(request, ctx)
        result = runtime.new_conversation(request, ctx)
        self.assertEqual(result["evictedConversationIds"], [first])
        self.assertEqual(len(runtime.get_state(request, ctx)["conversations"]), 10)
        with self.assertRaises(ServiceError) as caught:
            runtime.clear_conversation(ClearRequest(ctx.project_epoch, first), ctx)
        self.assertEqual(caught.exception.code, "AI_RESULT_UNAVAILABLE")

    def test_lazy_engine_construction_is_outside_locks_and_shares_existing_store(self):
        from codeviz.assistant import service

        runtime = _AssistantRuntime(provider_factory=lambda config, key: FakeProvider(_valid_events(), []))
        self.addCleanup(runtime.shutdown)
        ctx = context()
        publish(runtime, ctx)
        name = runtime.new_conversation(ProjectRequest(ctx.project_epoch), ctx)["conversationId"]
        runtime.configure(parse_configure({"baseUrl": "https://provider.invalid/v1", "model": "probe-model",
            "key": "offline-test-only", "enabled": True}))
        actual = service.AssistantService

        def construct(*args, **kwargs):
            self.assertFalse(runtime._lock.locked())
            return actual(*args, **kwargs)

        with patch.object(service, "AssistantService", side_effect=construct):
            runtime.test_connection(ConnectionRequest(1, "share-store"))
        self.assertIs(runtime._engine._conversations, runtime._conversations)
        self.assertEqual(runtime._conversations.revision_of(ctx.project_key, name), 0)

    def test_state_recovers_bound_question_and_rejects_unbound_internal_messages(self):
        runtime = _AssistantRuntime()
        self.addCleanup(runtime.shutdown)
        ctx = context()
        publish(runtime, ctx)
        name = runtime.new_conversation(ProjectRequest(ctx.project_epoch), ctx)["conversationId"]
        runtime._conversations.reserve_append(ctx.project_key, name, 0, messages=(
            {"role": "tool", "content": "private tool source"},))
        with self.assertRaises(ServiceError) as caught:
            runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)
        self.assertEqual(caught.exception.code, "AI_CAPACITY")

    def test_state_restores_original_binding_cursor_and_validated_answer(self):
        runtime = _AssistantRuntime()
        self.addCleanup(runtime.shutdown)
        ctx = context()
        publish(runtime, ctx)
        name = runtime.new_conversation(ProjectRequest(ctx.project_epoch), ctx)["conversationId"]
        scope = {"projectEpoch": "historical-epoch", "generation": 3, "configRevision": 2,
                 "consentRevision": 4, "origin": "https://provider.invalid:443", "normalizedScope": "old"}
        claim = ModelClaim("Unable to confirm.", "unknown", (), None)
        answer = answer_to_public(Answer(tuple(Section(kind, (claim,) if kind == "summary" else ())
            for kind in ("summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")),
            (), (), False, (), "2026-10-19T00:00:00Z"))
        encoded = json.dumps(answer, separators=(",", ":"))
        runtime._conversations.reserve_append(ctx.project_key, name, 0, messages=(
            {"role": "user", "content": "Question", "jobId": "old-job", "scope": scope,
             "sourceTags": [{"private": "must not appear"}]},
            {"role": "assistant", "content": encoded, "jobId": "old-job", "scope": scope}),
            results={"old-job": {"answer": encoded}})
        runtime._conversations._remember_recovery(ctx.project_key, name, 0, "old-job", {
            "projectEpoch": "historical-epoch", "analysisGeneration": 3, "configRevision": 2,
            "consentRevision": 4, "conversationRevision": 0, "seq": 7, "status": "Completed"})
        state = runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)
        messages = state["conversations"][0]["messages"]
        self.assertEqual([message["type"] for message in messages], ["question", "validated_answer"])
        self.assertEqual([message["seq"] for message in messages], [7, 7])
        self.assertTrue(all(message["stale"] for message in messages))
        self.assertEqual(messages[1]["answer"], answer)
        self.assertNotIn("must not appear", json.dumps(state))
        runtime.clear_conversation(ClearRequest(ctx.project_epoch, name), ctx)
        self.assertEqual(runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)["conversations"][0]["messages"], [])

    def test_clear_cancels_ungranted_work_and_preserves_only_old_revision_cleanup(self):
        runtime = _AssistantRuntime()
        self.addCleanup(runtime.shutdown)
        ctx = context()
        publish(runtime, ctx)
        name = runtime.new_conversation(ProjectRequest(ctx.project_epoch), ctx)["conversationId"]
        runtime.configure(parse_configure({"baseUrl": "https://provider.invalid/v1", "model": "probe-model",
            "key": "offline-test-only", "enabled": True}))
        engine = runtime._ensure_engine(runtime._config, runtime._key)
        identity = runtime._context_identity(ctx)
        request = JobRequest(RequestScope(ctx.project_epoch, 1, "queued"), name, "Question", "ctx", "probe-model",
            1, 0, "scope", ProviderDraft(b"{}", 32, time.monotonic() + 90), identity)
        job = _Job("queued-job", request, request.digest(), EventRing("queued-job", kind="explanation"),
                   conversation_revision=0, status=JobStatus.RUNNING)
        close_entered, close_release = Event(), Event()
        self.addCleanup(close_release.set)

        class ClosingProvider(FakeProvider):
            async def cancel(self):
                close_entered.set()
                while not close_release.is_set():
                    await asyncio.sleep(0.001)

        job.provider = ClosingProvider(_valid_events(), [])
        with engine._lock:
            engine._jobs[job.job_id] = job
            engine._active_job_ids.add(job.job_id)
            engine._requests[(ctx.project_epoch, "queued")] = (job.digest, job.job_id)
            runtime._conversations.reserve_append(ctx.project_key, name, 0,
                messages=({"role": "user", "content": "Question", "jobId": job.job_id,
                    "scope": {"projectEpoch": ctx.project_epoch, "generation": 1,
                              "configRevision": 1, "consentRevision": 0}},),
                active_job_id=job.job_id, request_key=(ctx.project_epoch, "queued"), digest=job.digest)
            engine._sync_ring_locked(job)
        before = runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)
        self.assertEqual(before["conversations"][0]["messages"][0]["seq"], before["activeJobs"][0]["seq"])
        with patch.object(engine, "_set_conversation_revision", side_effect=AssertionError("requires grant")):
            runtime.clear_conversation(ClearRequest(ctx.project_epoch, name), ctx)
        self.assertTrue(close_entered.wait(1))
        state = runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)
        self.assertEqual(state["conversations"][0]["messages"], [])
        self.assertEqual(state["conversations"][0]["conversationRevision"], 1)
        self.assertEqual(state["activeJobs"][0]["conversationRevision"], 0)
        self.assertFalse(state["activeJobs"][0]["cancelAccepted"])
        self.assertTrue(state["activeJobs"][0]["cancelRequested"])
        self.assertEqual(runtime._conversations.tombstone((ctx.project_epoch, "queued")), job.digest)
        with self.assertRaises(ServiceError):
            runtime._conversations.reserve_append(ctx.project_key, name, 0, messages=({"content": "late"},))
        close_release.set()

    def test_state_complete_envelope_limit_rejects_oversized_grant_without_truncation(self):
        runtime = _AssistantRuntime()
        self.addCleanup(runtime.shutdown)
        ctx = context()
        publish(runtime, ctx)
        runtime.configure(parse_configure({"baseUrl": "https://provider.invalid/v1", "model": "probe-model",
            "key": "offline-test-only", "enabled": True}))
        files = tuple(f"{index:05d}_{'x' * 270}.py" for index in range(8000))
        runtime._consents.grant(project_key=ctx.project_key, config=runtime._config,
                               allowed_files=files, confirmed=True)
        with self.assertRaises(ServiceError) as caught:
            runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)
        self.assertEqual(caught.exception.code, "AI_CAPACITY")
        self.assertIsNone(runtime._engine)
