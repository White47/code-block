"""Public-bridge security acceptance with inert providers and saved fixtures only."""

import asyncio
import json
import unittest
from dataclasses import replace
from threading import Event
from unittest.mock import patch

import test_assistant_integration as flow
from test_assistant_runtime_explanation import OfflineExplanationProvider

from codeviz.analysis.project import analyze_project
from codeviz.assistant.budget import EXPLANATION_BUDGET
from codeviz.core import Confidence, EdgeType


class AdversarialProvider(OfflineExplanationProvider):
    def __init__(self, scenario):
        super().__init__()
        self.scenario = scenario
        self.closed = Event()

    async def aclose(self):
        self.closed.set()

    async def prepare(self, draft):
        prepared = await super().prepare(draft)
        if self.scenario == "budget":
            # A deliberately excessive, still conservative reservation, not a raised cap.
            prepared = replace(
                prepared, input_token_upper_bound=EXPLANATION_BUDGET.max_input_tokens + 1)
            self.prepared[-1] = (prepared, self.prepared[-1][1])
        return prepared

    async def dispatch(self, prepared):
        self.sends += 1
        logical = next(value for handle, value in self.prepared if handle is prepared)
        if self.scenario == "network":
            raise TimeoutError("synthetic-private-network-detail")
        if logical["mode"] == "tools":
            if self.scenario == "tool":
                yield flow.event("tool", {"id": "attack", "name": "delete_file", "arguments": {}})
            elif self.scenario == "protected":
                yield flow.event("tool", {"id": "attack", "name": "read_source_excerpt", "arguments": {
                    "file_path": ".env", "start_line": 1, "end_line": 1}})
        elif logical["mode"] == "final":
            sections = [{"kind": kind, "claims": []} for kind in flow.KINDS]
            sections[0]["claims"] = [{"text": '<img src="https://invalid.test/track">',
                "basis": "static", "evidenceIds": ["FORGED-EVIDENCE"], "relationEvidenceId": None}]
            yield flow.event("answer", {"index": 0, "last": True, "content": json.dumps({"sections": sections})})
        yield flow.event("usage", {"inputTokens": 1, "outputTokens": 1, "basis": "actual"})


class AssistantSecurityAcceptanceTests(unittest.TestCase):
    def ready(self, provider=None, before_dispatch=None):
        return flow.AssistantIntegrationTests.ready(self, provider=provider, before_dispatch=before_dispatch)

    def released(self, bridge, epoch, job):
        return flow.AssistantIntegrationTests.released(self, bridge, epoch, job)

    def test_static_fixtures_keep_unknown_calls_and_training_stages_separate(self):
        analysis = analyze_project(flow.PROJECT)
        self.assertIn("DYNAMIC_CALL_UNRESOLVED", {issue.code for issue in analysis.issues})
        self.assertTrue(any(edge.type == EdgeType.CALLS and edge.confidence == Confidence.UNRESOLVED
                            for edge in analysis.flow.graph.edges))
        roles = {component.role for component in analysis.semantic.graph.components}
        self.assertTrue({"ModelDefinition", "ForwardPass", "LossCalculation", "BackwardPass",
                         "ClearGradients", "ParameterUpdate", "EpochLoop", "BatchLoop"} <= roles)

    def test_hostile_source_cannot_grant_tools_files_or_trusted_evidence(self):
        for scenario, code in (("tool", "AI_PROTOCOL_INCOMPATIBLE"),
                               ("protected", "AI_PROTOCOL_INCOMPATIBLE"),
                               ("evidence", "AI_EVIDENCE_INVALID")):
            with self.subTest(scenario=scenario):
                root, bridge, _controller, ctx, request, provider = self.ready(AdversarialProvider(scenario))
                (root / ".env").write_text("DENIED-SOURCE-SENTINEL", encoding="utf-8")
                selected = {**request, "context": {"kind": "source", "filePath": "dynamic.py", "anchor": None, "mode": "file"}}
                with patch("socket.create_connection", side_effect=AssertionError("No network")):
                    started = bridge.assistant_start(selected)
                    self.assertTrue(started["ok"], started)
                    result = self.released(bridge, ctx.project_epoch, started["data"]["jobId"])
                self.assertEqual(result["status"], "Failed", result)
                self.assertEqual(result["issue"]["code"], code, result)
                self.assertIsNone(result["answer"])
                wires = b"\n".join(item.body for item, _logical in provider.prepared)
                self.assertIn(b"UNTRUSTED COMMENT", wires)
                self.assertNotIn(b"DENIED-SOURCE-SENTINEL", wires)
                self.assertFalse((root / "OUTPUT_MUST_NOT_EXIST").exists())

    def test_budget_and_provider_failure_never_publish_answer_or_private_errors(self):
        for scenario in ("budget", "network"):
            with self.subTest(scenario=scenario):
                _root, bridge, _controller, ctx, request, provider = self.ready(AdversarialProvider(scenario))
                started = bridge.assistant_start(request)
                self.assertTrue(started["ok"], started)
                result = self.released(bridge, ctx.project_epoch, started["data"]["jobId"])
                self.assertEqual(result["status"], "Failed", result)
                self.assertIsNone(result["answer"])
                self.assertNotIn("synthetic-private-network-detail", json.dumps(result))
                if scenario == "budget":
                    self.assertEqual(result["issue"]["code"], "AI_BUDGET_EXCEEDED")
                    self.assertEqual(provider.sends, 0)
                else:
                    self.assertEqual(provider.sends, 1)

    def test_changed_added_and_deleted_inventory_fail_before_provider_dispatch(self):
        for mutation in ("changed", "added", "deleted"):
            with self.subTest(mutation=mutation):
                root, bridge, _controller, ctx, request, provider = self.ready()
                if mutation == "changed":
                    (root / "helpers.py").write_text("changed = True\n", encoding="utf-8")
                elif mutation == "added":
                    (root / "added.py").write_text("added = True\n", encoding="utf-8")
                else:
                    (root / "helpers.py").unlink()
                started = bridge.assistant_start(request)
                self.assertTrue(started["ok"], started)
                result = self.released(bridge, ctx.project_epoch, started["data"]["jobId"])
                self.assertIn(result["status"], ("Failed", "Stale"))
                self.assertIsNone(result["answer"])
                self.assertEqual(provider.sends, 0)

    def test_cancel_before_send_and_clear_do_not_recreate_history(self):
        entered = Event()

        async def hold(_prepared):
            entered.set()
            await asyncio.Event().wait()

        _root, bridge, _controller, ctx, request, provider = self.ready(before_dispatch=hold)
        started = bridge.assistant_start(request)
        self.assertTrue(started["ok"], started)
        self.assertTrue(entered.wait(2))
        cancelled = bridge.assistant_cancel({"projectEpoch": ctx.project_epoch, "jobId": started["data"]["jobId"]})
        self.assertTrue(cancelled["ok"], cancelled)
        self.assertTrue(cancelled["data"]["cancelRequested"])
        cleared = bridge.assistant_clear_conversation({"projectEpoch": ctx.project_epoch, "conversationId": request["conversationId"]})
        self.assertTrue(cleared["ok"], cleared)
        result = self.released(bridge, ctx.project_epoch, started["data"]["jobId"])
        self.assertTrue(result["cancelAccepted"])
        restored = bridge.assistant_get_state({"projectEpoch": ctx.project_epoch})
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(restored["data"]["conversations"][0]["messages"], [])
        self.assertEqual(provider.sends, 0)

    def test_project_switch_prevents_late_response_from_becoming_current_history(self):
        entered = Event()

        async def hold(_prepared):
            entered.set()
            await asyncio.Event().wait()

        root, bridge, controller, ctx, request, provider = self.ready(AdversarialProvider("idle"), before_dispatch=hold)
        started = bridge.assistant_start(request)
        self.assertTrue(started["ok"], started)
        self.assertTrue(entered.wait(2))
        other = root.parent / "other"
        other.mkdir()
        controller.open_project(other)
        controller.analyze()
        new = controller.capture_assistant_context()
        self.assertNotEqual(new.project_epoch, ctx.project_epoch)
        state = bridge.assistant_get_state({"projectEpoch": new.project_epoch})
        self.assertTrue(state["ok"], state)
        self.assertEqual(state["data"]["conversations"], [])
        # Old project polling is deliberately inaccessible, not a current result.
        result = bridge.assistant_poll({"projectEpoch": ctx.project_epoch,
            "jobId": started["data"]["jobId"], "afterSeq": 0})
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"]["code"], "AI_RESULT_UNAVAILABLE")
        self.assertTrue(provider.closed.wait(2))
        self.assertEqual(provider.sends, 0)

    def test_origin_switch_revokes_old_grant_without_sending(self):
        _root, bridge, _controller, ctx, request, provider = self.ready()
        configured = bridge.assistant_configure({"baseUrl": "https://other.invalid/v1", "model": "probe-model",
            "key": "offline-test-only", "enabled": True})
        self.assertTrue(configured["ok"], configured)
        rejected = bridge.assistant_start(request)
        self.assertFalse(rejected["ok"])
        state = bridge.assistant_get_state({"projectEpoch": ctx.project_epoch})
        self.assertTrue(state["ok"], state)
        self.assertFalse(state["data"]["grant"] and state["data"]["grant"]["enabled"])
        self.assertEqual(provider.sends, 0)
