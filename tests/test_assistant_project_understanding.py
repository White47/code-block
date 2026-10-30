"""Project references reuse the retained-answer transaction and owner lifetime."""

import asyncio
import json
import unittest
from dataclasses import replace
from threading import Event

import test_assistant_project_retrieval as retrieval_fixtures
from test_assistant_project_retrieval import ProjectProvider
from test_assistant_runtime_conversations import publish

from codeviz.assistant.bridge_requests import (
    CancelRequest,
    ClearRequest,
    ProjectRequest,
    parse_configure,
    parse_start,
)


class ProjectUnderstandingTests(unittest.TestCase):
    ready = retrieval_fixtures.ProjectRetrievalTests.ready
    settled = retrieval_fixtures.ProjectRetrievalTests.settled

    def test_atomic_reference_replacement_capacity_and_owner_lifetime(self):
        _root, runtime, ctx, payload, provider = self.ready()
        request = ProjectRequest(ctx.project_epoch)

        def run(number, *, project=True):
            started = runtime.start(parse_start({**payload, "requestId": f"turn-{number}",
                "context": {"kind": "project" if project else "question"}}), ctx)
            return started, self.settled(runtime, ctx, started["jobId"])

        started, completed = run(1)
        self.assertEqual(completed["status"], "Completed")
        state = runtime.get_state(request, ctx)
        reference = state.get("projectUnderstanding")
        self.assertIsNotNone(reference)
        self.assertEqual(set(reference), {"schemaVersion", "artifactId", "revision", "projectEpoch",
            "analysisGeneration", "conversationId", "conversationRevision", "jobId",
            "configRevision", "consentRevision", "coverage"})
        self.assertEqual((reference["schemaVersion"], reference["revision"]), (1, 1))
        self.assertEqual(reference["jobId"], started["jobId"])
        self.assertEqual(reference["conversationId"], payload["conversationId"])
        coverage = reference["coverage"]
        self.assertEqual(set(coverage), {"indexedFileCount", "authorizedFileCount",
            "metadataFileCount", "sourceFileCount", "gapCodes"})
        self.assertEqual((coverage["indexedFileCount"], coverage["authorizedFileCount"]), (3, 3))
        actual = runtime._engine._project_coverage(started["jobId"])
        self.assertEqual(coverage["metadataFileCount"], len(actual.metadata_files))
        self.assertEqual(coverage["sourceFileCount"], len(actual.source_files))
        self.assertEqual(coverage["gapCodes"], sorted(set(actual.gap_codes)))
        owner = state["conversations"][0]
        answers = [message for message in owner["messages"]
                   if message["type"] == "validated_answer" and message["jobId"] == started["jobId"]]
        self.assertEqual(len(answers), 1)
        self.assertFalse(answers[0]["stale"])
        self.assertEqual(answers[0]["answer"], completed["answer"])

        store = runtime._conversations
        logical = store.logical_state()
        records = logical["conversations"]
        self.assertEqual(records[0]["projectUnderstanding"], reference)
        self.assertEqual(store.logical_bytes, len(store._encoded(logical)))
        without = json.loads(json.dumps(logical))
        del without["conversations"][0]["projectUnderstanding"]
        self.assertGreater(store.logical_bytes, len(store._encoded(without)))

        original_close = provider.aclose
        byte_limit = store.MAX_BYTES

        async def capacity_at_commit():
            await original_close()
            store.MAX_BYTES = store.logical_bytes

        provider.aclose = capacity_at_commit
        try:
            rejected, failed = run(2)
            self.assertEqual(failed["status"], "Failed")
            self.assertEqual(runtime.get_state(request, ctx)["projectUnderstanding"], reference)
            self.assertNotIn(rejected["jobId"], store.logical_state()["conversations"][0]["results"])
        finally:
            store.MAX_BYTES = byte_limit
            provider.aclose = original_close
        _ordinary, ordinary = run(3, project=False)
        self.assertEqual(ordinary["status"], "Completed")
        self.assertEqual(runtime.get_state(request, ctx)["projectUnderstanding"], reference)
        payload["conversationId"] = runtime.new_conversation(request, ctx)["conversationId"]
        newer, result = run(4)
        self.assertEqual(result["status"], "Completed")
        replacement = runtime.get_state(request, ctx)["projectUnderstanding"]
        self.assertEqual(replacement["revision"], 2)
        self.assertNotEqual(replacement["artifactId"], reference["artifactId"])
        self.assertEqual(replacement["jobId"], newer["jobId"])
        self.assertEqual(sum("projectUnderstanding" in record
                             for record in store.logical_state()["conversations"]), 1)

        ctx = replace(ctx, publication_revision=ctx.publication_revision + 1)
        publish(runtime, ctx)
        self.assertEqual(runtime.get_state(request, ctx)["projectUnderstanding"], replacement)
        runtime.clear_conversation(ClearRequest(ctx.project_epoch, payload["conversationId"]), ctx)
        self.assertIsNone(runtime.get_state(request, ctx).get("projectUnderstanding"))

    def test_project_active_purpose_is_state_only_and_cancelled_late_answer_cannot_publish(self):
        entered, release = Event(), Event()

        class GatedProjectProvider(ProjectProvider):
            async def dispatch(self, prepared):
                logical = next(logical for handle, logical in self.prepared if handle is prepared)
                if logical["mode"] == "final":
                    entered.set()
                    while not release.is_set():
                        await asyncio.sleep(.005)
                async for event in super().dispatch(prepared):
                    yield event

        provider = GatedProjectProvider()
        self.addCleanup(release.set)
        _root, runtime, ctx, payload, _provider = self.ready(provider=provider)
        started = runtime.start(parse_start(payload), ctx)
        self.assertTrue(entered.wait(5))
        active = runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)["activeJobs"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["purpose"], "project_understanding")
        self.assertNotIn("purpose", started)
        cancelled = runtime.cancel(CancelRequest(started["jobId"], ctx.project_epoch))
        self.assertNotIn("purpose", cancelled)
        release.set()
        state = self.settled(runtime, ctx, started["jobId"])
        self.assertNotEqual(state["status"], "Completed")
        self.assertIsNone(runtime.get_state(ProjectRequest(ctx.project_epoch), ctx).get("projectUnderstanding"))

    def test_dependency_invalidation_omits_reference_and_eviction_removes_owner(self):
        for action in ("config", "revoke", "analysis", "evict"):
            with self.subTest(action=action):
                _root, runtime, ctx, payload, _provider = self.ready()
                started = runtime.start(parse_start(payload), ctx)
                self.assertEqual(self.settled(runtime, ctx, started["jobId"])["status"], "Completed")
                request = ProjectRequest(ctx.project_epoch)
                self.assertIsNotNone(runtime.get_state(request, ctx).get("projectUnderstanding"))
                if action == "config":
                    runtime.configure(parse_configure({"baseUrl": "https://provider.invalid/v1",
                        "model": "other-model", "key": None, "enabled": True}))
                elif action == "revoke":
                    runtime.revoke_consent(ctx.project_epoch)
                elif action == "analysis":
                    ctx = replace(ctx, publication_revision=ctx.publication_revision + 1,
                                  analysis_generation=ctx.analysis_generation + 1)
                    publish(runtime, ctx)
                else:
                    self.assertTrue(runtime._conversations.evict_inactive(ctx.project_key,
                                                                          payload["conversationId"]))
                self.assertIsNone(runtime.get_state(request, ctx).get("projectUnderstanding"))


if __name__ == "__main__":
    unittest.main()
