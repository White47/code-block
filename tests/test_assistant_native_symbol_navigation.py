"""Saved-function citations must navigate to a delivered ordinary-symbol graph."""

import time
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

from scripts.assistant_smoke_host import _create_smoke_bridge

from codeviz.assistant.service import AssistantService
from codeviz.assistant.tool_contracts import EntityRef
from codeviz.core import NodeKind, ViewKind
from codeviz.ui import controller as controller_module


class NativeSymbolNavigationTests(unittest.TestCase):
    def test_lazy_symbol_guard_requires_complete_ordinary_definition_identity(self):
        matches = AssistantService._navigation_symbol_identity
        with TemporaryDirectory() as directory:
            bridge, _provider = _create_smoke_bridge(Path(directory))
            self.addCleanup(bridge._finish_assistant_close)
            snapshot = bridge._controller.capture_assistant_context().snapshot
            symbol = next(item for item in snapshot.symbol_inventory
                          if item.qualified_name == "sample.process_record")
            code = snapshot.get_code_node(symbol.id)
            for kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS):
                self.assertTrue(matches(replace(symbol, kind=kind), replace(code, kind=kind)))
            self.assertFalse(matches(None, code))
            self.assertFalse(matches(symbol, None))
            self.assertFalse(matches(replace(symbol, definition=False), code))
            self.assertFalse(matches(replace(symbol, kind=NodeKind.VARIABLE),
                                     replace(code, kind=NodeKind.VARIABLE)))
            other_anchor = replace(symbol.source_anchor, file_path="other.py")
            for change in (
                {"id": "different-id"}, {"kind": NodeKind.CLASS}, {"name": "other"},
                {"qualified_name": "sample.other"}, {"scope": "other"},
                {"file_path": "other.py", "source_anchors": (other_anchor,)},
                {"source_anchors": code.source_anchors * 2},
                {"source_anchors": (replace(symbol.source_anchor, end_column=22),)},
            ):
                with self.subTest(fields=tuple(change)):
                    self.assertFalse(matches(symbol, replace(code, **change)))

    def test_symbol_projection_requires_full_definition_identity_not_just_id(self):
        matches = getattr(controller_module, "_same_indexed_definition", None)
        self.assertTrue(callable(matches), "Projection must prove the entire indexed definition")
        with TemporaryDirectory() as directory:
            bridge, _provider = _create_smoke_bridge(Path(directory))
            self.addCleanup(bridge._finish_assistant_close)
            snapshot = bridge._controller.capture_assistant_context().snapshot
            symbol = next(item for item in snapshot.symbol_inventory
                          if item.qualified_name == "sample.process_record")
            code = snapshot.get_code_node(symbol.id)
            self.assertTrue(matches(symbol, code))
            self.assertFalse(matches(None, code))
            self.assertFalse(matches(replace(symbol, definition=False), code))
            for change in ({"name": "other"}, {"qualified_name": "sample.other"},
                           {"scope": "other"}, {"kind": NodeKind.CLASS},
                           {"source_anchors": code.source_anchors * 2},
                           {"source_anchors": (replace(code.source_anchors[0], end_column=22),)}):
                with self.subTest(fields=tuple(change)):
                    self.assertFalse(matches(symbol, replace(code, **change)))

    def released(self, bridge, epoch, job):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = bridge.assistant_poll({"projectEpoch": epoch, "jobId": job, "afterSeq": 0})
            self.assertTrue(response["ok"], response)
            if response["data"]["resourcesReleased"]:
                return response["data"]
            Event().wait(.005)
        self.fail("Worker did not release within the test deadline")

    def test_saved_function_citation_reveals_in_its_delivered_symbol_view(self):
        with TemporaryDirectory() as directory:
            bridge, provider = _create_smoke_bridge(Path(directory))
            self.addCleanup(bridge._finish_assistant_close)
            ctx = bridge._controller.capture_assistant_context()
            symbol = next(item for item in ctx.snapshot.symbol_inventory
                          if item.qualified_name == "sample.process_record")
            delivered = bridge.get_view(ViewKind.SYMBOL.value, root_id=symbol.id)
            self.assertTrue(delivered["ok"], delivered)
            configured = bridge.assistant_configure({"baseUrl": "https://provider.invalid/v1",
                "model": "probe-model", "key": "synthetic-native-only", "enabled": True})
            self.assertTrue(configured["ok"], configured)
            chat = bridge.assistant_new_conversation({"projectEpoch": ctx.project_epoch})
            grant = bridge.assistant_grant_consent({"projectEpoch": ctx.project_epoch,
                "analysisGeneration": ctx.analysis_generation, "configRevision": 1,
                "allowedFiles": ["sample.py"], "confirmed": True})
            self.assertTrue(grant["ok"], grant)
            anchor = symbol.source_anchor
            started = bridge.assistant_start({"projectEpoch": ctx.project_epoch,
                "analysisGeneration": ctx.analysis_generation, "configRevision": 1,
                "conversationId": chat["data"]["conversationId"], "requestId": "native-source-citation",
                "consentRevision": grant["data"]["consentRevision"], "question": "Explain the saved function.",
                "context": {"kind": "source", "filePath": "sample.py", "mode": "function",
                    "anchor": {"filePath": anchor.file_path, "startLine": anchor.start_line,
                        "startColumn": anchor.start_column, "endLine": anchor.end_line,
                        "endColumn": anchor.end_column}}})
            self.assertTrue(started["ok"], started)
            answer = self.released(bridge, ctx.project_epoch, started["data"]["jobId"])
            self.assertEqual(answer["status"], "Completed", answer)
            evidence = answer["answer"]["evidence"][0]
            self.assertTrue(evidence["graphAvailable"])
            for action in ("source", "graph"):
                navigation = bridge.assistant_resolve_evidence({"projectEpoch": ctx.project_epoch,
                    "analysisGeneration": ctx.analysis_generation,
                    "conversationId": chat["data"]["conversationId"],
                    "evidenceId": evidence["evidenceId"], "action": action})
                self.assertTrue(navigation["ok"], navigation)
                checked = self.released(bridge, ctx.project_epoch, navigation["data"]["jobId"])
                current = bridge._controller.capture_assistant_context()
                fact = current.navigation_facts.get(EntityRef("symbol", symbol.id))
                self.assertEqual(checked["status"], "Completed", (action, checked, fact))
                if action == "graph":
                    self.assertEqual(checked["target"]["reveal"]["selectedId"], symbol.id)
            self.assertEqual(provider.sends, 2, "Evidence navigation must never send to the model")

    def test_saved_definition_can_plan_its_symbol_view_before_delivery(self):
        with TemporaryDirectory() as directory:
            bridge, provider = _create_smoke_bridge(Path(directory))
            self.addCleanup(bridge._finish_assistant_close)
            ctx = bridge._controller.capture_assistant_context()
            symbol = next(item for item in ctx.snapshot.symbol_inventory
                          if item.qualified_name == "sample.process_record")
            target = EntityRef("symbol", symbol.id)
            self.assertEqual(ctx.navigation_facts[target].visible_locations, ())
            self.assertEqual(dict(ctx.context_map), {})
            configured = bridge.assistant_configure({"baseUrl": "https://provider.invalid/v1",
                "model": "probe-model", "key": "synthetic-native-only", "enabled": True})
            self.assertTrue(configured["ok"], configured)
            chat = bridge.assistant_new_conversation({"projectEpoch": ctx.project_epoch})
            grant = bridge.assistant_grant_consent({"projectEpoch": ctx.project_epoch,
                "analysisGeneration": ctx.analysis_generation, "configRevision": 1,
                "allowedFiles": ["sample.py"], "confirmed": True})
            self.assertTrue(grant["ok"], grant)
            anchor = symbol.source_anchor
            started = bridge.assistant_start({"projectEpoch": ctx.project_epoch,
                "analysisGeneration": ctx.analysis_generation, "configRevision": 1,
                "conversationId": chat["data"]["conversationId"], "requestId": "lazy-symbol-citation",
                "consentRevision": grant["data"]["consentRevision"], "question": "Explain the saved function.",
                "context": {"kind": "source", "filePath": "sample.py", "mode": "function",
                    "anchor": {"filePath": anchor.file_path, "startLine": anchor.start_line,
                        "startColumn": anchor.start_column, "endLine": anchor.end_line,
                        "endColumn": anchor.end_column}}})
            self.assertTrue(started["ok"], started)
            answer = self.released(bridge, ctx.project_epoch, started["data"]["jobId"])
            self.assertEqual(answer["status"], "Completed", answer)
            evidence = answer["answer"]["evidence"][0]
            before = bridge._controller.capture_assistant_context()
            sends = provider.sends
            navigation = bridge.assistant_resolve_evidence({"projectEpoch": ctx.project_epoch,
                "analysisGeneration": ctx.analysis_generation,
                "conversationId": chat["data"]["conversationId"],
                "evidenceId": evidence["evidenceId"], "action": "graph"})
            self.assertTrue(navigation["ok"], navigation)
            checked = self.released(bridge, ctx.project_epoch, navigation["data"]["jobId"])
            after = bridge._controller.capture_assistant_context()
            self.assertEqual(after.context_map, before.context_map)
            self.assertEqual(after.navigation_facts, before.navigation_facts)
            self.assertEqual(after.publication_revision, before.publication_revision)
            self.assertEqual(checked["status"], "Completed", checked)
            reveal = checked["target"]["reveal"]
            self.assertEqual((reveal["viewKind"], reveal["rootId"], reveal["selectedId"]),
                             (ViewKind.SYMBOL.value, symbol.id, symbol.id))
            self.assertEqual(after.navigation_facts[target].visible_locations, ())
            self.assertEqual(provider.sends, sends)
            delivered = bridge.get_view(reveal["viewKind"], root_id=reveal["rootId"],
                                        selected_id=reveal["selectedId"])
            self.assertTrue(delivered["ok"], delivered)
            self.assertIn(symbol.id, delivered["data"]["rootIds"])
            self.assertIn(symbol.id, {node["id"] for node in delivered["data"]["nodes"]})
            materialized = bridge._controller.capture_assistant_context()
            self.assertTrue(any(location.root_id == symbol.id
                                and location.visible_node_id == symbol.id
                                and location.detail_source_id is None
                                for location in materialized.navigation_facts[target].visible_locations))
            self.assertEqual(provider.sends, sends)


if __name__ == "__main__":
    unittest.main()
