"""Targeted regressions for the C phase review findings; offline only."""

import asyncio
import json
import os
import time
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

from tests import test_assistant_runtime_explanation as runtime_fixtures
from tests.assistant_service_fixtures import final_answer_event, scope_fingerprint
from tests.assistant_tool_fixtures import make_index_case, source_case_with
from tests.test_assistant_service_loop import LoopProvider

from codeviz.analysis.python import parse_python_file
from codeviz.assistant.authority import AuthorityState
from codeviz.assistant.bridge_requests import (
    CancelRequest,
    PollRequest,
    ProjectRequest,
    parse_start,
)
from codeviz.assistant.contracts import JobStatus, RequestScope
from codeviz.assistant.index_tools import get_local_relations, search_symbols
from codeviz.assistant.jobs import JobRequest
from codeviz.assistant.provider import ProviderDraft, ProviderEvent
from codeviz.assistant.service import AssistantService, ConversationStore, ServiceError
from codeviz.assistant.tool_contracts import EntityRef, RelationsRequest, SearchRequest
from codeviz.assistant.tool_loop import project_tool_result
from codeviz.core import SourceAnchor
from codeviz.ui.assistant_context import PublicationIdentity
from codeviz.ui.controller import AnalysisStatus


class MetadataClosureTests(unittest.TestCase):
    def test_linked_allowed_file_secret_blocks_next_provider_send(self):
        class SemanticProvider(LoopProvider):
            async def dispatch(self, prepared):
                self.sends += 1
                if self.sends == 1:
                    yield ProviderEvent("tool", json.dumps({"id": "semantic",
                        "name": "get_local_relations", "arguments": {
                            "seed": {"kind": "semantic", "id": "component-a"}}}).encode())
                yield ProviderEvent("usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            # Deliberately synthetic screening marker, outside all returned anchors.
            (root / "layers.py").write_text("value = 1\n# password = 'synthetic-fixture-only'\n")

            def configure(analysis):
                entry = analysis.symbol_index.entries["s0000"]
                analysis.symbol_index.entries[entry.id] = replace(entry, file_path="layers.py",
                    source_anchor=SourceAnchor("layers.py", 1, 0, 1, 5))
                return replace(analysis, files=(*analysis.files, parse_python_file(root, "layers.py")))

            case = source_case_with(root, b"value = 1\n", configure=configure)
            consent = replace(case.consent, allowed_files=("app.py", "layers.py"),
                              provider_origin="https://provider.invalid:443")
            identity = PublicationIdentity(1, case.scope.project_epoch, 1,
                os.path.normcase(os.path.normpath(str(root))), root, (), AnalysisStatus.READY, True)
            fingerprint = scope_fingerprint(consent.allowed_files)
            provider = SemanticProvider()
            service = AssistantService(lambda: provider)
            self.addCleanup(service.shutdown)
            service._replace_publication(identity, case.snapshot)
            service._replace_source_consent(consent)
            service._replace_authority(AuthorityState(True, 1, 1, identity.project_key,
                consent.provider_origin, fingerprint, (("conversation", 0),), "fake",
                "https://provider.invalid:443/v1/chat"))
            logical = {"version": 1, "mode": "tools", "messages": [
                {"role": "user", "content": "Explain"}], "tools": [{"name": "get_local_relations"}],
                "answerSchema": None, "sourceTags": [], "metadataTags": []}
            request = JobRequest(RequestScope(case.scope.project_epoch, 1, "closure"),
                "conversation", "Explain", "context", "fake", 1, 1, fingerprint,
                ProviderDraft(json.dumps(logical).encode(), 512, time.monotonic() + 90), identity)
            started = service.start(request)
            deadline = time.monotonic() + 3
            while not service.get(started.job_id).resources_released and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(provider.sends, 1)
            self.assertEqual(service.get(started.job_id).status, JobStatus.FAILED)
            self.assertEqual(service.get(started.job_id).issue.code, "AI_CONSENT_REQUIRED")

    def test_semantic_metadata_includes_linked_symbol_file_not_only_display_anchor(self):
        case = make_index_case()
        # Rebuild via B3 after moving a linked definition to the second file.
        from codeviz.assistant.snapshot import build_snapshot

        entry = case.analysis.symbol_index.entries["s0000"]
        case.analysis.symbol_index.entries[entry.id] = replace(
            entry, file_path="denied.py", source_anchor=SourceAnchor("denied.py", 1, 0, 1, 5))
        snapshot = build_snapshot(case.analysis, binding=case.snapshot.binding)
        consent = replace(case.consent, allowed_files=("app.py", "denied.py"))
        result = get_local_relations(snapshot, consent,
            RelationsRequest(EntityRef("semantic", "component-a")), checkpoint=lambda: None)
        self.assertTrue(result.relations)
        projection = project_tool_result(result, snapshot=snapshot)
        self.assertEqual({path for _, _, path in projection.metadata_spans},
                         {"app.py", "denied.py"})
        search = search_symbols(snapshot, consent, SearchRequest("component-a"),
                                checkpoint=lambda: None)
        self.assertEqual({path for _, _, path in
                          project_tool_result(search, snapshot=snapshot).metadata_spans},
                         {"app.py", "denied.py"})


class ProvisionalRetentionTests(unittest.TestCase):
    ready = runtime_fixtures.RuntimeExplanationTests.ready
    wait_poll = runtime_fixtures.RuntimeExplanationTests.wait_poll
    TEXT = '<b onclick="inert()">Unvalidated quotation</b>中文'

    def streaming(self, outcome):
        runtime, ctx, payload, provider = self.ready()
        entered, release = Event(), Event()
        self.addCleanup(release.set)

        async def dispatch(prepared):
            provider.sends += 1
            logical = next(value for handle, value in provider.prepared if handle is prepared)
            if provider.sends == 1:
                yield ProviderEvent("text", self.TEXT.encode())
                entered.set()
                while not release.is_set():
                    await asyncio.sleep(0.001)
                if outcome == "failed":
                    raise RuntimeError("synthetic offline failure")
                yield ProviderEvent("text", b" second chunk")
            if logical["mode"] == "final":
                yield final_answer_event()
            yield ProviderEvent("usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')

        provider.dispatch = dispatch
        return runtime, ctx, payload, provider, entered, release

    def test_failed_and_cancelled_provisional_survive_retirement_with_original_cursor(self):
        for outcome in ("failed", "cancelled"):
            with self.subTest(outcome=outcome):
                runtime, ctx, payload, provider, entered, release = self.streaming(outcome)
                started = runtime.start(parse_start(payload), ctx)
                self.assertTrue(entered.wait(2))
                if outcome == "cancelled":
                    runtime.cancel(CancelRequest(started["jobId"], ctx.project_epoch))
                release.set()
                poll = self.wait_poll(runtime, started["jobId"], ctx.project_epoch,
                                      lambda value: value["resourcesReleased"])
                self.assertEqual(poll["status"], "Failed" if outcome == "failed" else "Cancelled")
                self.assertFalse(runtime._engine._jobs)
                messages = runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)["conversations"][0]["messages"]
                self.assertEqual([item["type"] for item in messages], ["question", "provisional"])
                self.assertEqual(messages[1]["text"], self.TEXT)
                self.assertTrue(all(item["seq"] == poll["seq"] and item["jobId"] == started["jobId"]
                                    and item["configRevision"] == 1 for item in messages))
                record = runtime._conversations.logical_state()["conversations"][0]
                self.assertEqual(record["messages"][1]["content"], self.TEXT)
                scope = record["messages"][0]["scope"]
                self.assertEqual(runtime._conversations._select_eligible_history(ctx.project_key,
                    payload["conversationId"], 0, project_epoch=ctx.project_epoch, origin=scope["origin"],
                    generation=ctx.analysis_generation, consent_revision=scope["consentRevision"],
                    config_revision=1, normalized_scope=scope["normalizedScope"]), ())

    def test_stream_and_validated_final_share_one_assistant_slot_at_message_cap(self):
        runtime, ctx, payload, provider, entered, release = self.streaming("completed")
        runtime._conversations.MAX_MESSAGES = 2
        started = runtime.start(parse_start(payload), ctx)
        self.assertTrue(entered.wait(2))
        record = runtime._conversations.logical_state()["conversations"][0]
        self.assertEqual(len(record["messages"]), 2)
        self.assertEqual(record["messages"][1]["content"], self.TEXT)
        release.set()
        poll = self.wait_poll(runtime, started["jobId"], ctx.project_epoch,
                              lambda value: value["resourcesReleased"])
        self.assertEqual(poll["status"], "Completed")
        messages = runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)["conversations"][0]["messages"]
        self.assertEqual([item["type"] for item in messages], ["question", "validated_answer"])
        record = runtime._conversations.logical_state()["conversations"][0]
        self.assertEqual(len(record["messages"]), 2)
        self.assertNotIn("provisional", record["messages"][1])

    def test_provisional_cannot_escape_the_sole_two_mib_store_cap(self):
        self.TEXT = "x" * 6000
        runtime, ctx, payload, provider, entered, release = self.streaming("completed")
        store = runtime._conversations
        store.reserve_append(ctx.project_key, payload["conversationId"], 0,
            bindings={"padding": "x" * (store.MAX_BYTES - store.logical_bytes - 4096)})
        release.set()
        started = runtime.start(parse_start(payload), ctx)
        poll = self.wait_poll(runtime, started["jobId"], ctx.project_epoch,
                              lambda value: value["resourcesReleased"])
        self.assertEqual(poll["status"], "Failed")
        self.assertEqual(poll["displayText"], "")
        self.assertLessEqual(store.logical_bytes, 2 * 1024 * 1024)
        record = store.logical_state()["conversations"][0]
        self.assertEqual([item["role"] for item in record["messages"]], ["user"])


class EvictionDeliveryTests(unittest.TestCase):
    ready = runtime_fixtures.RuntimeExplanationTests.ready
    wait_poll = runtime_fixtures.RuntimeExplanationTests.wait_poll
    streaming = ProvisionalRetentionTests.streaming
    TEXT = ProvisionalRetentionTests.TEXT

    def test_final_capacity_eviction_is_delivered_on_retired_and_subsequent_polls(self):
        runtime, ctx, payload, provider, entered, release = self.streaming("completed")
        started = runtime.start(parse_start(payload), ctx)
        self.assertTrue(entered.wait(2))
        store = runtime._conversations
        store.register(ctx.project_key, "inactive-victim", 0)
        store.reserve_append(ctx.project_key, "inactive-victim", 0,
                             bindings={"padding": "x" * 8000})
        store.MAX_BYTES = store.logical_bytes + 100
        release.set()
        poll = self.wait_poll(runtime, started["jobId"], ctx.project_epoch,
                              lambda value: value["resourcesReleased"])
        self.assertEqual(poll["status"], "Completed")
        self.assertNotIn("inactive-victim", store.conversation_ids)
        self.assertEqual(poll["evictedConversationIds"], ["inactive-victim"])
        again = runtime.poll(PollRequest(started["jobId"], ctx.project_epoch, poll["seq"]))
        self.assertEqual(again["evictedConversationIds"], ["inactive-victim"])
        self.assertEqual(len(runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)["conversations"]), 1)

    def test_sequential_mutations_keep_ten_unique_ids_and_overflow_is_atomic(self):
        store = ConversationStore()
        store.register("project", "owner", 0)
        store.reserve_append("project", "owner", 0, active_job_id="job")
        for index in range(10):
            store.MAX_CONVERSATIONS = 2
            store.register("project", f"victim-{index}", 0)
            store.MAX_CONVERSATIONS = 1
            self.assertEqual(store.reserve_append("project", "owner", 0,
                bindings={f"change-{index}": index}, _delivery_job_id="job"), (f"victim-{index}",))
        self.assertEqual(store._pending_evictions("project", "owner", 0, "job"),
                         tuple(f"victim-{index}" for index in range(10)))
        store.MAX_CONVERSATIONS = 2
        store.register("project", "eleventh-victim", 0)
        before = store.logical_state()
        store.MAX_CONVERSATIONS = 1
        with self.assertRaises(ServiceError) as caught:
            store.reserve_append("project", "owner", 0, bindings={"overflow": True},
                                 _delivery_job_id="job")
        self.assertEqual(caught.exception.code, "AI_CONVERSATION_CAPACITY")
        self.assertEqual(store.logical_state(), before)
        self.assertEqual(store.logical_bytes, len(store._encoded(before)))
