"""C5 application retention and clear invariants."""

import asyncio
import json
import os
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

from assistant_service_fixtures import EMPTY_SCOPE_FINGERPRINT, final_answer_text
from tests.test_assistant_jobs import FakeProvider, _install_authority

from codeviz.assistant.contracts import JobStatus, RequestScope
from codeviz.assistant.jobs import JobRequest
from codeviz.assistant.provider import ProviderDraft
from codeviz.assistant.service import AssistantService, ConversationStore, ServiceError
from codeviz.ui.assistant_context import PublicationIdentity
from codeviz.ui.controller import AnalysisStatus


def size(value):
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8"))


class ConversationStoreTests(unittest.TestCase):
    def test_canonical_bytes_include_every_retained_occurrence(self):
        store = ConversationStore()
        store.register("project", "chat", 0)
        store.reserve_append("project", "chat", 0,
                             messages=({"role": "user", "text": "汉字", "anchor": None},),
                             evidence={"e1": {"text": "汉字", "anchor": None}},
                             results={"r1": {"text": "汉字"}},
                             bindings={"b1": {"evidenceId": "e1", "target": None}})
        self.assertEqual(store.logical_bytes, size(store.logical_state()))
        self.assertEqual(store.evidence_count, 1)
        self.assertEqual(store.logical_state()["conversations"][0]["messages"][0]["text"], "汉字")

    def test_atomic_limits_do_not_partially_append_or_evict(self):
        store = ConversationStore()
        store.register("p", "first", 0)
        for index in range(40):
            store.reserve_append("p", "first", 0, messages=({"index": index},))
        before = store.logical_state()
        with self.assertRaises(ServiceError) as caught:
            store.reserve_append("p", "first", 0, messages=({"index": 40},))
        self.assertEqual(caught.exception.code, "AI_CONVERSATION_CAPACITY")
        self.assertEqual(store.logical_state(), before)
        for index in range(1, 10):
            store.register("p", f"chat-{index}", 0)
        store.set_draft("p", "first", True)
        store.register("p", "eleventh", 0)
        self.assertIn("first", store.conversation_ids)
        self.assertNotIn("chat-1", store.conversation_ids)

    def test_evicts_complete_oldest_with_all_registries(self):
        store = ConversationStore()
        store.register("p", "old", 0)
        store.reserve_append("p", "old", 0, evidence={"e": {"text": "secret"}},
                             results={"r": {"text": "secret"}},
                             bindings={"b": {"evidenceId": "e"}})
        store.note_request("p", "old", ("epoch", "request"), "digest")
        for index in range(1, 10):
            store.register("p", f"chat-{index}", 0)
        store.set_active("p", "chat-1", "running")
        store.set_draft("p", "chat-2", True)
        store.register("p", "new", 0)
        self.assertEqual(store.evicted_ids, ("old",))
        self.assertEqual(store.evidence_count, 0)
        self.assertEqual(store.tombstone(("epoch", "request")), "digest")
        self.assertNotIn("secret", json.dumps(store.logical_state()))

    def test_full_store_rejects_when_every_conversation_is_active_or_draft(self):
        store = ConversationStore()
        for index in range(10):
            name = f"chat-{index}"
            store.register("p", name, 0)
            if index % 2:
                store.set_draft("p", name, True)
            else:
                store.set_active("p", name, name)
        before = store.logical_state()
        with self.assertRaises(ServiceError) as caught:
            store.reserve_append("p", "new", 0, messages=({"text": "hello"},),
                                 create=True)
        self.assertEqual(caught.exception.code, "AI_CONVERSATION_CAPACITY")
        self.assertEqual(store.logical_state(), before)

    def test_byte_and_global_evidence_caps(self):
        store = ConversationStore()
        store.register("p", "chat", 0)
        store.set_active("p", "chat", "running")
        store.reserve_append("p", "chat", 0,
                             evidence={f"e{i}": None for i in range(1000)})
        before = store.logical_state()
        with self.assertRaises(ServiceError):
            store.reserve_append("p", "chat", 0, evidence={"extra": None})
        self.assertEqual(store.logical_state(), before)
        store.clear("p", "chat", 1)
        store.set_active("p", "chat", "running")
        with self.assertRaises(ServiceError):
            store.reserve_append("p", "chat", 1,
                                 messages=({"text": "x" * (2 * 1024 * 1024)},))
        self.assertEqual(store.logical_state()["conversations"][0]["messages"], [])

    def test_clear_revision_blocks_late_append_and_bounds_tombstones(self):
        store = ConversationStore()
        store.register("p", "chat", 0)
        store.reserve_append("p", "chat", 0, messages=({"text": "old"},),
                             active_job_id="job")
        store.note_request("p", "chat", ("epoch", "request"), "digest")
        self.assertEqual(store.clear("p", "chat", 1), ("job",))
        self.assertEqual(store.logical_state()["conversations"][0]["messages"], [])
        self.assertEqual(store.tombstone(("epoch", "request")), "digest")
        with self.assertRaises(ServiceError):
            store.reserve_append("p", "chat", 0, messages=({"text": "late"},))
        for index in range(1100):
            store.remember_tombstone(("epoch", str(index)), "digest")
        self.assertLessEqual(store.tombstone_count, 1024)


class ServiceConversationTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        self.identity = PublicationIdentity(
            1, "epoch", 1, os.path.normcase(os.path.normpath(str(root))), root,
            (), AnalysisStatus.READY, True)

    def request(self, request_id="request"):
        return JobRequest(RequestScope("epoch", 1, request_id), "conversation",
                          "question", "context-digest", "fake", 1, 1,
                          EMPTY_SCOPE_FINGERPRINT, ProviderDraft(
                              b'{"model":"fake"}', 4, time.monotonic() + 60),
                          self.identity)

    def test_eleventh_conversation_replaces_oldest_inactive_authority_slot(self):
        service = AssistantService(FakeProvider)
        try:
            service.replace_current(self.identity)
            _install_authority(service, self.identity)
            project = self.identity.project_key
            service._conversations.register(project, "conversation", 0)
            for index in range(1, 10):
                name = f"chat-{index}"
                service._set_conversation_revision(name, 0)
                service._conversations.register(project, name, 0)
            service._set_conversation_revision("chat-10", 0)
            self.assertEqual(service._authority.conversation_revision("conversation"), None)
            self.assertEqual(service._authority.conversation_revision("chat-10"), 0)
            self.assertNotIn("conversation", service._conversations.conversation_ids)
            self.assertEqual(len(service._authority.conversation_revisions), 10)
        finally:
            service.shutdown()

    def test_completion_retains_one_turn_and_clear_removes_it(self):
        service = AssistantService(FakeProvider)
        try:
            service.replace_current(self.identity)
            _install_authority(service, self.identity)
            receipt = service.start(self.request())
            deadline = time.monotonic() + 2
            while (service.get(receipt.job_id).status is not JobStatus.COMPLETED
                   and time.monotonic() < deadline):
                time.sleep(0.01)
            self.assertEqual(service.get(receipt.job_id).status, JobStatus.COMPLETED)
            record = service._conversations.logical_state()["conversations"][0]
            self.assertEqual(len(record["messages"]), 2)
            answer = json.loads(record["results"][receipt.job_id]["answer"])
            self.assertEqual(answer["sections"], json.loads(final_answer_text())["sections"])
            self.assertEqual(answer["evidence"], [])
            self.assertEqual(answer["relationFacts"], [])
            self.assertTrue(answer["checkedAt"])
            service._set_conversation_revision("conversation", 1)
            record = service._conversations.logical_state()["conversations"][0]
            self.assertEqual(record["messages"], [])
            self.assertEqual(record["results"], {})
            replay = service.start(self.request())
            self.assertEqual(replay.job_id, receipt.job_id)
            self.assertEqual(service._conversations.logical_state()["conversations"][0]["messages"], [])
        finally:
            service.shutdown()

    def test_history_selects_recent_completed_source_free_turns_in_scope(self):
        service = AssistantService(FakeProvider)
        try:
            service.replace_current(self.identity)
            _install_authority(service, self.identity)
            for index in range(7):
                receipt = service.start(self.request(f"request-{index}"))
                deadline = time.monotonic() + 2
                while (service.get(receipt.job_id).status is not JobStatus.COMPLETED
                       and time.monotonic() < deadline):
                    time.sleep(0.01)
                self.assertEqual(service.get(receipt.job_id).status, JobStatus.COMPLETED)
            history = service._conversations._select_eligible_history(
                self.identity.project_key, "conversation", 0,
                project_epoch="epoch", origin="https://provider.invalid:443", generation=1,
                consent_revision=1, config_revision=1,
                normalized_scope=EMPTY_SCOPE_FINGERPRINT)
            self.assertEqual([message["role"] for message in history],
                             ["user", "assistant"] * 3)
            self.assertEqual([message["content"] for message in history if
                              message["role"] == "user"], ["question"] * 3)
            for changed in ({"origin": "https://other.invalid:443"},
                            {"project_epoch": "other-epoch"}, {"generation": 2},
                            {"consent_revision": 2},
                            {"config_revision": 2},
                            {"normalized_scope": "other"}):
                scope = {"project_epoch": "epoch",
                         "origin": "https://provider.invalid:443", "generation": 1,
                         "consent_revision": 1, "config_revision": 1,
                         "normalized_scope": EMPTY_SCOPE_FINGERPRINT}
                scope.update(changed)
                self.assertEqual(service._conversations._select_eligible_history(
                    self.identity.project_key, "conversation", 0, **scope), ())
            self.assertEqual(service._conversations._select_eligible_history(
                "another-project", "conversation", 0,
                project_epoch="epoch", origin="https://provider.invalid:443", generation=1,
                consent_revision=1, config_revision=1,
                normalized_scope="normalized-scope"), ())
        finally:
            service.shutdown()

    def test_history_excludes_failed_and_unverifiable_source_turns(self):
        store = ConversationStore()
        store.register("project", "chat", 0)
        scope = {"projectEpoch": "epoch", "origin": "https://provider.invalid:443",
                 "generation": 1,
                 "consentRevision": 1, "configRevision": 1,
                 "normalizedScope": "scope"}
        store.reserve_append("project", "chat", 0, messages=(
            {"role": "user", "content": "failed", "jobId": "failed", "scope": scope},
            {"role": "user", "content": "source", "jobId": "source", "scope": scope},
            {"role": "assistant", "content": "quoted source", "jobId": "source",
             "scope": scope, "sourceTags": [{"file": "a.py"}], "sourceFree": False},
            {"role": "user", "content": "safe", "jobId": "safe", "scope": scope},
            {"role": "assistant", "content": "answer", "jobId": "safe",
             "scope": scope, "sourceTags": [], "sourceFree": True}),
            results={"source": {"answer": "quoted source"},
                     "safe": {"answer": "answer"}})
        self.assertEqual(store._select_eligible_history(
            "project", "chat", 0, project_epoch="epoch",
            origin="https://provider.invalid:443",
            generation=1, consent_revision=1, config_revision=1,
            normalized_scope="scope"), (
                {"role": "user", "content": "safe"},
                {"role": "assistant", "content": "answer"}))

    def test_clear_cancels_prepared_job_without_late_result(self):
        entered = Event()
        release = Future()
        provider = FakeProvider()

        async def hold(_prepared):
            entered.set()
            await asyncio.shield(asyncio.wrap_future(release))

        service = AssistantService(lambda: provider, before_dispatch=hold)
        try:
            service.replace_current(self.identity)
            _install_authority(service, self.identity)
            service.start(self.request())
            self.assertTrue(entered.wait(2))
            service._set_conversation_revision("conversation", 1)
            release.set_result(None)
            deadline = time.monotonic() + 2
            while service._active_job_ids and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(service._active_job_ids)
            self.assertEqual(provider.sends, 0)
            record = service._conversations.logical_state()["conversations"][0]
            self.assertEqual(record["messages"], [])
            self.assertEqual(record["results"], {})
        finally:
            if not release.done():
                release.set_result(None)
            service.shutdown()

    def test_evicted_request_cannot_be_admitted_after_revision_increases(self):
        provider = FakeProvider()
        service = AssistantService(lambda: provider)
        try:
            service.replace_current(self.identity)
            _install_authority(service, self.identity)
            first = service.start(self.request())
            deadline = time.monotonic() + 2
            while (service.get(first.job_id).status is not JobStatus.COMPLETED
                   and time.monotonic() < deadline):
                time.sleep(0.01)
            self.assertEqual(service.get(first.job_id).status, JobStatus.COMPLETED)
            for index in range(9):
                service._conversations.register(self.identity.project_key,
                                                f"other-{index}", 0)
            service._conversations.register(self.identity.project_key, "new", 0)
            with service._lock:
                service._drop_evicted_requests_locked()
            self.assertEqual(service._conversations.evicted_ids, ("conversation",))
            self.assertEqual(len(service._requests), 1)
            self.assertEqual(service._conversations.tombstone(("epoch", "request")),
                             self.request().digest())
            service._set_conversation_revision("conversation", 1)
            with self.assertRaises(ServiceError) as caught:
                service.start(self.request())
            self.assertEqual(caught.exception.code, "AI_CONVERSATION_CLEARED")
            for index in range(1023):
                service._requests[("epoch", f"other-request-{index}")] = (
                    "digest", "retired-job")
            with self.assertRaises(ServiceError) as limit:
                service.start(self.request("new-request"))
            self.assertEqual(limit.exception.code, "AI_REQUEST_LIMIT")
            self.assertEqual(provider.sends, 1)
        finally:
            service.shutdown()


if __name__ == "__main__":
    unittest.main()
