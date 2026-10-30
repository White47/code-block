"""Offline explanation admission and C7 projection over the real worker."""

import asyncio
import json
import time
import unittest
from dataclasses import replace
from math import ulp
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from assistant_service_fixtures import final_answer_event

from codeviz.assistant.bridge_requests import (
    CancelRequest,
    ClearRequest,
    PollRequest,
    ProjectRequest,
    parse_configure,
    parse_consent,
    parse_start,
)
from codeviz.assistant.provider import PreparedRequest, ProviderEvent, WireOccurrence
from codeviz.assistant.service import ServiceError, _AssistantRuntime
from codeviz.assistant.tool_contracts import EntityRef
from codeviz.core import ViewKind
from codeviz.ui.assistant_context import NodeContext, ViewNodeKey
from codeviz.ui.controller import AppController


class OfflineExplanationProvider:
    """Reuse with runtime(provider_factory=lambda config, key: this_fake)."""

    def __init__(self, endpoint="https://provider.invalid:443/v1/chat/completions"):
        self.endpoint = endpoint
        self.sends = 0
        self.prepared = []

    async def prepare(self, draft):
        logical = json.loads(draft.body)
        body = json.dumps({"model": "probe-model", "messages": logical["messages"]},
                          ensure_ascii=False, separators=(",", ":")).encode()
        def tags(name):
            return tuple(WireOccurrence(item["pointer"], item["start"], item["end"], item["file"])
                         for item in logical.get(name, []))
        prepared = PreparedRequest(f"fake-{len(self.prepared)}", "POST", self.endpoint,
            body, draft.max_output_tokens, draft.deadline, len(body) + 1024,
            tags("sourceTags"), tags("metadataTags"), True)
        self.prepared.append((prepared, logical))
        return prepared

    async def dispatch(self, prepared):
        self.sends += 1
        logical = next(logical for handle, logical in self.prepared if handle is prepared)
        if logical["mode"] == "final":
            yield final_answer_event()
        yield ProviderEvent("usage", b'{"inputTokens":1,"outputTokens":1,"basis":"actual"}')

    async def cancel(self):
        pass

    async def aclose(self):
        pass


class RuntimeExplanationTests(unittest.TestCase):
    def ready(self, *, before_dispatch=None, production_gate=False, source=None):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        if source is not None:
            (root / "app.py").write_text(source, encoding="utf-8")
        provider = OfflineExplanationProvider()
        kwargs = {} if production_gate else {"provider_factory": lambda config, key: provider}
        if before_dispatch is not None:
            kwargs["before_dispatch"] = before_dispatch
        runtime = _AssistantRuntime(**kwargs)
        self.addCleanup(runtime.shutdown)
        controller = AppController(assistant_publication_mirror=runtime)
        controller.open_project(root)
        controller.analyze()
        ctx = controller.capture_assistant_context()
        runtime.configure(parse_configure({"baseUrl": "https://provider.invalid/v1", "model": "probe-model",
            "key": "offline-test-only", "enabled": True}))
        name = runtime.new_conversation(ProjectRequest(ctx.project_epoch), ctx)["conversationId"]
        grant = runtime.grant_consent(parse_consent({"projectEpoch": ctx.project_epoch,
            "analysisGeneration": ctx.analysis_generation, "configRevision": 1,
            "allowedFiles": [] if source is None else ["app.py"], "confirmed": True}), ctx)
        payload = {"projectEpoch": ctx.project_epoch, "analysisGeneration": ctx.analysis_generation,
            "conversationId": name, "requestId": "explicit", "configRevision": 1,
            "consentRevision": grant["consentRevision"], "question": "What can you confirm?",
            "context": {"kind": "question"}}
        return runtime, ctx, payload, provider

    def wait_poll(self, runtime, job_id, epoch, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = runtime.poll(PollRequest(job_id, epoch, 0))
            if predicate(state):
                return state
            time.sleep(0.005)
        self.fail(f"Explanation did not settle: {state}")

    def test_question_completed_retired_poll_state_and_replay_share_validated_answer(self):
        runtime, ctx, payload, provider = self.ready()
        started = runtime.start(parse_start(payload), ctx)
        state = self.wait_poll(runtime, started["jobId"], ctx.project_epoch, lambda p: p["resourcesReleased"])
        self.assertEqual(state["status"], "Completed")
        self.assertIsNotNone(state["answer"])
        self.assertNotIn("text", state)
        self.assertEqual(state["usage"]["reserved"]["modelRequests"], 2)
        self.assertEqual(state["usage"]["reserved"]["toolCalls"], 1)
        restored = runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)["conversations"][0]["messages"]
        self.assertEqual([message["type"] for message in restored], ["question", "validated_answer"])
        self.assertTrue(all(message["seq"] == state["seq"] for message in restored))
        self.assertEqual(restored[1]["answer"], state["answer"])
        self.assertEqual(runtime.start(parse_start(payload), ctx)["jobId"], started["jobId"])
        self.assertEqual(provider.sends, 2)
        self.assertFalse(runtime._engine._jobs)
        with patch("codeviz.assistant.service.json.loads", side_effect=AssertionError("No JSON re-trust")):
            self.assertEqual(runtime.poll(PollRequest(started["jobId"], ctx.project_epoch, state["seq"]))["answer"], state["answer"])
        self.assertTrue(all(event["type"] in {"status", "display_text", "lifecycle", "result", "usage"}
                            for event in state["events"]))
        with self.assertRaises(ServiceError):
            runtime.poll(PollRequest(started["jobId"], "foreign", 0))
        with self.assertRaises(ServiceError) as conflict:
            runtime.start(parse_start({**payload, "question": "Different question"}), ctx)
        self.assertEqual(conflict.exception.code, "AI_REQUEST_CONFLICT")

    def test_gate_and_current_capture_fail_before_worker_creation(self):
        runtime, ctx, payload, provider = self.ready(production_gate=True)
        with patch("codeviz.assistant.provider.create_provider", side_effect=AssertionError("SDK")):
            with self.assertRaises(ServiceError) as caught:
                runtime.start(parse_start(payload), ctx)
            self.assertEqual(caught.exception.code, "AI_DEPENDENCY_MISSING")
        self.assertIsNone(runtime._engine)
        self.assertEqual(provider.sends, 0)
        with self.assertRaises(ServiceError):
            runtime.start(parse_start(payload), replace(ctx, publication_revision=ctx.publication_revision + 1))

    def test_cancel_and_clear_cannot_publish_late_answer_or_recreate_messages(self):
        entered = Event()

        async def before_dispatch(prepared):
            entered.set()
            await asyncio.Event().wait()

        runtime, ctx, payload, provider = self.ready(before_dispatch=before_dispatch)
        started = runtime.start(parse_start(payload), ctx)
        self.assertTrue(entered.wait(1))
        receipt = runtime.cancel(CancelRequest(started["jobId"], ctx.project_epoch))
        self.assertTrue(receipt["cancelRequested"])
        self.assertFalse(receipt["cancelAccepted"])
        runtime.clear_conversation(ClearRequest(ctx.project_epoch, payload["conversationId"]), ctx)
        state = self.wait_poll(runtime, started["jobId"], ctx.project_epoch, lambda p: p["resourcesReleased"])
        self.assertEqual(state["status"], "Cancelled")
        self.assertTrue(state["cancelAccepted"])
        self.assertEqual(provider.sends, 0)
        self.assertEqual(runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)["conversations"][0]["messages"], [])

    def test_source_and_mapped_node_admission_use_only_saved_identifiers(self):
        source = "def outer():\n    def inner():\n        return 1\n    return inner()\n"
        for kind in ("function", "file", "node"):
            with self.subTest(kind=kind):
                runtime, ctx, payload, provider = self.ready(source=source)
                symbol = next(symbol for symbol in ctx.snapshot.symbol_inventory if symbol.name == "inner")
                if kind == "node":
                    key = ViewNodeKey(ViewKind.PROJECT_STRUCTURE, None, None, "saved-visible")
                    ctx = replace(ctx, publication_revision=ctx.publication_revision + 1,
                        context_map={key: NodeContext((EntityRef("symbol", symbol.id),),
                            (symbol.source_anchor,), False, False, None)})
                    runtime._replace_publication(runtime._context_identity(ctx), ctx.snapshot)
                    selected = {"kind": "node", "viewKind": ViewKind.PROJECT_STRUCTURE.value,
                        "visibleNodeId": "saved-visible", "rootId": None, "detailSourceId": None}
                else:
                    selected = {"kind": "source", "filePath": "app.py", "mode": kind,
                        "anchor": None if kind == "file" else {"filePath": "app.py",
                            "startLine": 3, "startColumn": 8, "endLine": 3, "endColumn": 14}}
                captured = []

                def hold(engine, job, runtime=runtime, captured=captured):
                    self.assertFalse(runtime._lock.locked())
                    self.assertFalse(engine._lock.locked())
                    captured.append(job)

                with patch("codeviz.assistant.service.AssistantService._schedule_admitted", new=hold), \
                        patch("codeviz.assistant.service.monotonic", return_value=934.1), \
                        patch.object(Path, "open", side_effect=AssertionError("No start source reads")), \
                        patch("codeviz.assistant.provider.create_provider", side_effect=AssertionError("No SDK")):
                    runtime.start(parse_start({**payload, "context": selected}), ctx)
                job = captured[0]
                # Subtracting rounded timestamps can lose one floating-point unit.
                self.assertAlmostEqual(job.deadline - job.admitted_at, 180, delta=ulp(job.deadline))
                self.assertEqual(job.request.draft.deadline, job.deadline)
                self.assertEqual(job.request.draft.max_output_tokens, 2_000)
                self.assertNotIn(source.encode(), job.request.draft.body)
                if kind == "file":
                    self.assertIsNone(job.request.root_retrieval.target)
                    self.assertEqual(job.request.root_retrieval.anchor.start_line, 1)
                else:
                    self.assertEqual(job.request.root_retrieval.target, EntityRef("symbol", symbol.id))
                    self.assertEqual(job.request.root_retrieval.anchor, symbol.source_anchor)
                self.assertEqual(provider.sends, 0)

    def test_revoke_makes_retired_poll_inert_and_cannot_restore_deleted_turn(self):
        runtime, ctx, payload, provider = self.ready()
        started = runtime.start(parse_start(payload), ctx)
        before = self.wait_poll(runtime, started["jobId"], ctx.project_epoch, lambda p: p["resourcesReleased"])
        runtime.revoke_consent(ctx.project_epoch)
        after = runtime.poll(PollRequest(started["jobId"], ctx.project_epoch, before["seq"]))
        self.assertEqual(after["status"], "Stale")
        self.assertIsNone(after["answer"])
        self.assertGreater(after["seq"], before["seq"])
        self.assertEqual(runtime.get_state(ProjectRequest(ctx.project_epoch), ctx)["conversations"][0]["messages"], [])
        self.assertEqual(provider.sends, 2)
