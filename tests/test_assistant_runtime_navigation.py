"""Offline navigation checks borrow receipts from the actual explanation worker."""

import json
import time
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest.mock import patch

from test_assistant_runtime_explanation import OfflineExplanationProvider

from codeviz.assistant.bridge_requests import (
    CancelRequest,
    ClearRequest,
    ConnectionRequest,
    PollRequest,
    ProjectRequest,
    parse_configure,
    parse_consent,
    parse_resolve,
    parse_start,
)
from codeviz.assistant.navigation import NavigationError
from codeviz.assistant.provider import ProviderEvent
from codeviz.assistant.retained_evidence import (
    RetainedEvidenceContext,
    RetainedEvidenceLease,
)
from codeviz.assistant.service import AssistantService, ServiceError, _AssistantRuntime
from codeviz.assistant.tool_contracts import EntityRef
from codeviz.core import ViewKind
from codeviz.ui.assistant_context import NavigationFact, NodeContext, ViewNodeKey
from codeviz.ui.controller import AppController


class SourceEvidenceProvider(OfflineExplanationProvider):
    """Cites only an actual root header injected by the existing worker."""

    async def dispatch(self, prepared):
        self.sends += 1
        logical = next(logical for handle, logical in self.prepared if handle is prepared)
        if logical["mode"] == "final":
            header = next(json.loads(message["content"].split("\n", 1)[0])
                          for message in logical["messages"]
                          if message.get("role") == "user"
                          and message.get("content", "").startswith('{"evidenceId":'))
            sections = [{"kind": kind, "claims": []} for kind in (
                "summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")]
            sections[0]["claims"] = [{"text": "The saved source contains a definition.",
                "basis": "static", "evidenceIds": [header["evidenceId"]], "relationEvidenceId": None}]
            yield ProviderEvent("answer", json.dumps({"index": 0, "last": True,
                "content": json.dumps({"sections": sections})}).encode())
        yield ProviderEvent("usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')


class RuntimeNavigationTests(unittest.TestCase):
    def ready(self, mode="file"):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        (root / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        provider = SourceEvidenceProvider()
        runtime = _AssistantRuntime(provider_factory=lambda config, key: provider)
        self.addCleanup(runtime.shutdown)
        controller = AppController(assistant_publication_mirror=runtime)
        controller.open_project(root)
        controller.analyze()
        ctx = controller.capture_assistant_context()
        runtime.configure(parse_configure({"baseUrl": "https://provider.invalid/v1", "model": "probe-model",
            "key": "offline-test-only", "enabled": True}))
        conversation = runtime.new_conversation(ProjectRequest(ctx.project_epoch), ctx)["conversationId"]
        grant = runtime.grant_consent(parse_consent({"projectEpoch": ctx.project_epoch,
            "analysisGeneration": ctx.analysis_generation, "configRevision": 1,
            "allowedFiles": ["app.py"], "confirmed": True}), ctx)
        anchor = None if mode == "file" else {"filePath": "app.py", "startLine": 1,
            "startColumn": 0, "endLine": 2, "endColumn": 12}
        started = runtime.start(parse_start({"projectEpoch": ctx.project_epoch,
            "analysisGeneration": ctx.analysis_generation, "conversationId": conversation,
            "requestId": "evidence-owner", "configRevision": 1, "consentRevision": grant["consentRevision"],
            "question": "Explain the saved source.", "context": {"kind": "source", "filePath": "app.py",
                "mode": mode, "anchor": anchor}}), ctx)
        answer = self.wait_released(runtime, ctx, started["jobId"])
        self.assertEqual(answer["status"], "Completed", answer)
        request = parse_resolve({"projectEpoch": ctx.project_epoch,
            "analysisGeneration": ctx.analysis_generation, "conversationId": conversation,
            "evidenceId": answer["answer"]["evidence"][0]["evidenceId"], "action": "source"})
        return runtime, ctx, request, provider

    def wait_released(self, runtime, ctx, job_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = runtime.poll(PollRequest(job_id, ctx.project_epoch, 0))
            if state["resourcesReleased"]:
                return state
            time.sleep(0.005)
        self.fail(f"Navigation did not settle: {state}")

    def test_source_uses_retained_scope_without_model_or_chat_turn(self):
        runtime, ctx, request, provider = self.ready()
        before = runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)["conversations"]
        self.assertTrue(callable(getattr(runtime, "resolve_evidence", None)))
        with patch("codeviz.assistant.provider.create_provider", side_effect=AssertionError("No SDK")):
            started = runtime.resolve_evidence(request, ctx)
            state = self.wait_released(runtime, ctx, started["jobId"])
        self.assertEqual(started["kind"], "navigation")
        self.assertEqual(started["scope"]["requestId"], "evidence-owner")
        self.assertEqual(state["status"], "Completed", state)
        self.assertEqual(state["target"]["evidenceId"], request.evidence_id)
        self.assertIsNone(state["target"]["monacoRange"])
        self.assertIsNone(state["target"]["reveal"])
        self.assertNotIn("usage", state)
        self.assertNotIn("displayText", state)
        self.assertEqual(provider.sends, 2)
        self.assertEqual(runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)["conversations"], before)

    def test_active_dedup_and_cancel_hold_lease_and_global_slot_until_os_future_ends(self):
        runtime, ctx, request, provider = self.ready()
        engine = runtime._engine
        entered, unblock = Event(), Event()
        self.addCleanup(unblock.set)
        operation = engine._check_navigation
        borrow = RetainedEvidenceContext.borrow
        release = RetainedEvidenceLease.release
        releases = []

        def blocking(job):
            entered.set()
            self.assertTrue(unblock.wait(2))
            return operation(job)

        def checked_borrow(context, identity):
            self.assertFalse(runtime._lock.locked())
            self.assertFalse(engine._lock.locked())
            self.assertFalse(runtime._conversations._lock.locked())
            return borrow(context, identity)

        def checked_release(lease):
            self.assertFalse(runtime._lock.locked())
            self.assertFalse(engine._lock.locked())
            self.assertFalse(runtime._conversations._lock.locked())
            releases.append(lease)
            return release(lease)

        with patch.object(engine, "_check_navigation", side_effect=blocking), \
                patch.object(RetainedEvidenceContext, "borrow", new=checked_borrow), \
                patch.object(RetainedEvidenceLease, "release", new=checked_release):
            started = runtime.resolve_evidence(request, ctx)
            self.assertTrue(entered.wait(1))
            active_lease = engine._navigation_jobs[started["jobId"]].lease
            self.assertEqual(runtime.resolve_evidence(request, ctx)["jobId"], started["jobId"])
            with self.assertRaises(ServiceError) as busy:
                runtime.resolve_evidence(replace(request, action="graph"), ctx)
            self.assertEqual(busy.exception.code, "AI_CAPACITY")
            with patch.object(AssistantService, "_schedule_admitted"):
                runtime.start(parse_start({"projectEpoch": ctx.project_epoch,
                    "analysisGeneration": ctx.analysis_generation, "conversationId": request.conversation_id,
                    "requestId": "slot-two", "configRevision": 1, "consentRevision": 1,
                    "question": "Another question", "context": {"kind": "question"}}), ctx)
            with self.assertRaises(ServiceError) as global_busy:
                runtime.test_connection(ConnectionRequest(1, "no-third-slot"))
            self.assertEqual(global_busy.exception.code, "AI_CAPACITY")
            receipt = runtime.cancel(CancelRequest(started["jobId"], ctx.project_epoch))
            self.assertTrue(receipt["cancelRequested"])
            self.assertFalse(receipt["cancelAccepted"])
            self.assertFalse(receipt["resourcesReleased"])
            state = runtime.poll(PollRequest(started["jobId"], ctx.project_epoch, receipt["seq"]))
            self.assertFalse(state["resourcesReleased"])
            self.assertIsNone(state["target"])
            self.assertNotIn(active_lease, releases)
            summary = next(job for job in runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)["activeJobs"]
                           if job["jobId"] == started["jobId"])
            self.assertEqual(summary["seq"], state["seq"])
            self.assertNotIn("target", summary)
            unblock.set()
            final = self.wait_released(runtime, ctx, started["jobId"])
            self.assertEqual(final["status"], "Cancelled")
            self.assertTrue(final["cancelAccepted"])
            self.assertGreater(final["seq"], receipt["seq"])
            self.assertEqual(releases.count(active_lease), 1)
        self.assertEqual(provider.sends, 2)

    def test_clear_invalidates_completed_target_without_restoring_messages(self):
        runtime, ctx, request, provider = self.ready()
        started = runtime.resolve_evidence(request, ctx)
        before = self.wait_released(runtime, ctx, started["jobId"])
        runtime.clear_conversation(ClearRequest(ctx.project_epoch, request.conversation_id), ctx)
        after = runtime.poll(PollRequest(started["jobId"], ctx.project_epoch, before["seq"]))
        self.assertEqual(after["status"], "Stale")
        self.assertIsNone(after["target"])
        self.assertGreater(after["seq"], before["seq"])
        self.assertEqual(runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)["conversations"][0]["messages"], [])
        with self.assertRaises(ServiceError):
            runtime.resolve_evidence(request, ctx)
        self.assertEqual(provider.sends, 2)

    def test_evidence_cannot_be_borrowed_from_another_conversation_or_epoch(self):
        runtime, ctx, request, provider = self.ready()
        other = runtime.new_conversation(ProjectRequest(ctx.project_epoch), ctx)["conversationId"]
        with self.assertRaises(ServiceError) as foreign:
            runtime.resolve_evidence(replace(request, conversation_id=other), ctx)
        self.assertEqual(foreign.exception.code, "AI_EVIDENCE_INVALID")
        with self.assertRaises(ServiceError):
            runtime.resolve_evidence(replace(request, project_epoch="foreign-epoch"), ctx)
        self.assertFalse(runtime._engine._navigation_jobs)
        self.assertFalse(runtime._engine._navigation_completed)
        self.assertEqual(provider.sends, 2)

    def test_graph_requires_exact_local_reveal_and_refuses_group_or_detail_locations(self):
        runtime, ctx, request, provider = self.ready(mode="function")
        symbol = next(symbol for symbol in ctx.snapshot.symbol_inventory if symbol.name == "value")
        target = EntityRef("symbol", symbol.id)
        for selected_id, detail_id, expected in ((symbol.id, None, "Completed"),
                                               ("grouped", None, "Failed"),
                                               (symbol.id, "detail-only", "Failed")):
            location = ViewNodeKey(ViewKind.SYMBOL, symbol.id, detail_id, selected_id)
            ctx = replace(ctx, publication_revision=ctx.publication_revision + 1,
                navigation_facts={target: NavigationFact((symbol.source_anchor,), (location,))},
                context_map={location: NodeContext((target,), (symbol.source_anchor,), False, False, None)})
            runtime._replace_publication(runtime._context_identity(ctx), ctx.snapshot)
            started = runtime.resolve_evidence(replace(request, action="graph"), ctx)
            state = self.wait_released(runtime, ctx, started["jobId"])
            self.assertEqual(state["status"], expected, state)
            if expected == "Completed":
                self.assertEqual(state["target"]["reveal"]["selectedId"], symbol.id)
                self.assertIsNone(state["target"]["monacoRange"])
            else:
                self.assertIsNone(state["target"])
                self.assertEqual(state["issue"]["code"], "AI_EVIDENCE_INVALID")
        self.assertEqual(provider.sends, 2)

    def test_lazy_symbol_plan_requires_existing_fact_namespace_and_bound_receipt(self):
        runtime, ctx, request, provider = self.ready(mode="file")
        symbol = next(symbol for symbol in ctx.snapshot.symbol_inventory if symbol.name == "value")
        target = EntityRef("symbol", symbol.id)
        with self.assertRaises(NavigationError):
            AssistantService._navigation_reveal(replace(ctx, navigation_facts={}), target)
        for kind in ("code", "semantic"):
            other = EntityRef(kind, symbol.id)
            context = replace(ctx, navigation_facts={
                other: NavigationFact((symbol.source_anchor,), ())})
            with self.subTest(namespace=kind), self.assertRaises(NavigationError):
                AssistantService._navigation_reveal(context, other)
        started = runtime.resolve_evidence(replace(request, action="graph"), ctx)
        state = self.wait_released(runtime, ctx, started["jobId"])
        self.assertEqual(state["status"], "Failed", state)
        self.assertIsNone(state["target"])
        self.assertEqual(provider.sends, 2)

    def test_failed_lease_release_keeps_slot_and_completed_cancel_only_retries_cleanup(self):
        runtime, ctx, request, _provider = self.ready()
        attempted = Event()

        def cannot_release(_lease):
            attempted.set()
            raise RuntimeError("Offline close failure")

        with patch.object(RetainedEvidenceLease, "release", new=cannot_release):
            started = runtime.resolve_evidence(request, ctx)
            self.assertTrue(attempted.wait(1))
            state = runtime.poll(PollRequest(started["jobId"], ctx.project_epoch, 0))
            self.assertEqual(state["status"], "Completed")
            self.assertFalse(state["resourcesReleased"])
            self.assertIn(started["jobId"], runtime._engine._navigation_jobs)
        receipt = runtime.cancel(CancelRequest(started["jobId"], ctx.project_epoch))
        self.assertFalse(receipt["cancelRequested"])
        self.assertFalse(receipt["cancelAccepted"])
        final = self.wait_released(runtime, ctx, started["jobId"])
        self.assertEqual(final["status"], "Completed")
        self.assertGreater(final["seq"], state["seq"])
        self.assertNotIn(started["jobId"], runtime._engine._navigation_jobs)

    def test_prepare_close_waits_for_original_future_and_leaves_engine_reusable(self):
        runtime, ctx, request, _provider = self.ready()
        engine = runtime._engine
        entered, unblock = Event(), Event()
        self.addCleanup(unblock.set)
        operation = engine._check_navigation
        failures = []

        def blocking(job):
            entered.set()
            self.assertTrue(unblock.wait(2))
            return operation(job)

        def prepare():
            try:
                runtime.prepare_close()
            except Exception as error:
                failures.append(error)

        with patch.object(engine, "_check_navigation", side_effect=blocking):
            started = runtime.resolve_evidence(request, ctx)
            self.assertTrue(entered.wait(1))
            closer = Thread(target=prepare)
            closer.start()
            try:
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    state = runtime.poll(PollRequest(started["jobId"], ctx.project_epoch, 0))
                    if state["cancelRequested"]:
                        break
                    time.sleep(0.005)
                self.assertTrue(state["cancelRequested"])
                self.assertFalse(state["cancelAccepted"])
                self.assertFalse(state["resourcesReleased"])
                self.assertTrue(closer.is_alive())
            finally:
                unblock.set()
                closer.join(2)
            self.assertFalse(closer.is_alive())
            self.assertEqual(failures, [])
        state = self.wait_released(runtime, ctx, started["jobId"])
        self.assertEqual(state["status"], "Cancelled")
        self.assertTrue(state["cancelAccepted"])
        self.assertIsNone(state["target"])
        self.assertIs(runtime._engine, engine)
        self.assertTrue(engine._thread.is_alive())
        runtime.configure(parse_configure({"baseUrl": "https://provider.invalid/v1", "model": "probe-model",
            "key": "offline-test-only", "enabled": True}))
        self.assertTrue(runtime.new_conversation(ProjectRequest(ctx.project_epoch), ctx)["conversationId"])
