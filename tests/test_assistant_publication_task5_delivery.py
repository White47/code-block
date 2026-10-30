"""C3 Task 5: only delivered desktop views acquire bounded source-backed context."""

import json
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType

from codeviz.assistant.tool_contracts import EntityRef
from codeviz.core import ViewKind
from codeviz.persistence.store import CacheStore
from codeviz.ui.assistant_context import NodeContext, ViewNodeKey
from codeviz.ui.controller import AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.web_bridge import DesktopBridge


class DeliveredViewPublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="codeviz-task5-delivery-")
        self.addCleanup(temporary.cleanup)
        scope = Path(temporary.name)
        self.root = scope / "project"
        self.root.mkdir()
        (self.root / "app.py").write_text(
            "class Engine:\n"
            "    def run(self, value):\n"
            "        return value + 1\n"
            "\n"
            "def main():\n"
            "    return Engine().run(2)\n",
            encoding="utf-8",
        )
        store = CacheStore(scope / "cache")
        self.controller = AppController(store)
        self.bridge = DesktopBridge(
            self.controller, store, PreferencesStore(scope / "preferences"))
        self.success(self.bridge.open_project(str(self.root)))
        self.success(self.bridge.analyze())
        self.assertTrue(self.context().eligible)

    def success(self, response):
        self.assertTrue(response["ok"], response)
        json.dumps(response)
        return response["data"]

    def context(self):
        return self.controller.capture_assistant_context()

    def project_view(self):
        return self.success(self.bridge.get_view("ProjectStructure"))

    def assert_snapshot_backed(self, context):
        self.assertIsInstance(context.context_map, MappingProxyType)
        self.assertGreater(len(context.context_map), 0)
        self.assertLessEqual(len(context.context_map), 100)
        snapshot = context.snapshot
        known_anchors = {
            *(record.source_anchor for record in snapshot.symbol_inventory),
            *(anchor for record in snapshot.code_node_inventory
              for anchor in record.source_anchors),
            *(anchor for record in snapshot.component_inventory
              for anchor in record.source_anchors),
        }
        for key, node in context.context_map.items():
            self.assertIsInstance(key, ViewNodeKey)
            self.assertIsInstance(node, NodeContext)
            self.assertLessEqual(len(node.targets), 20)
            self.assertLessEqual(len(node.source_anchors), 20)
            self.assertTrue(set(node.source_anchors) <= known_anchors)
            for target in node.targets:
                inventory = {
                    "symbol": snapshot.get_symbol,
                    "code": snapshot.get_code_node,
                    "semantic": snapshot.get_component,
                }
                self.assertIsNotNone(inventory[target.kind](target.id), target)
        for target, fact in context.navigation_facts.items():
            for key in fact.visible_locations:
                self.assertIn(key, context.context_map)
                self.assertIn(target, context.context_map[key].targets)

    def test_open_analyze_capture_and_internal_view_probes_leave_map_empty(self):
        accepted = self.context()
        self.assertEqual(dict(accepted.context_map), {})
        self.controller.view(ViewKind.PROJECT_STRUCTURE)
        self.success(self.bridge.get_view_state())
        self.assertIs(self.context(), accepted)
        self.assertEqual(dict(self.context().context_map), {})

    def test_delivered_root_view_publishes_typed_snapshot_backed_context(self):
        before = self.context()
        delivered = self.project_view()
        published = self.context()
        self.assertEqual(published.publication_revision, before.publication_revision + 1)
        self.assertIs(published.snapshot, before.snapshot)
        self.assertEqual(dict(before.context_map), {})
        self.assert_snapshot_backed(published)
        keys = set(published.context_map)
        self.assertEqual({key.view_kind for key in keys}, {ViewKind.PROJECT_STRUCTURE})
        self.assertEqual({key.root_id for key in keys}, {None})
        self.assertEqual({key.detail_source_id for key in keys}, {None})
        self.assertTrue({key.visible_node_id for key in keys} <=
                        {node["id"] for node in delivered["nodes"]})
        represented_symbols = [
            key for key in keys if published.snapshot.get_symbol(key.visible_node_id)
        ]
        self.assertTrue(represented_symbols, "fixture must expose a known symbol")
        self.assertTrue(any(
            EntityRef("symbol", key.visible_node_id) in published.context_map[key].targets
            for key in represented_symbols))
        for key in represented_symbols:
            self.assertNotIn(EntityRef("code", key.visible_node_id),
                             published.context_map[key].targets)
        with self.assertRaises(TypeError):
            published.context_map[next(iter(keys))] = published.context_map[next(iter(keys))]
        with self.assertRaises(FrozenInstanceError):
            published.context_map[next(iter(keys))].source_only = False

    def test_delivered_detail_uses_explicit_source_bucket(self):
        root = self.project_view()
        source_id = root["nodes"][0]["id"]
        before = self.context()
        detail = self.success(self.bridge.get_detail_view(
            "ProjectStructure", None, source_id))
        published = self.context()
        detail_keys = {
            key for key in published.context_map if key.detail_source_id == source_id
        }
        self.assertTrue(detail_keys)
        self.assertEqual({key.root_id for key in detail_keys}, {None})
        self.assertEqual({key.view_kind for key in detail_keys},
                         {ViewKind.PROJECT_STRUCTURE})
        self.assertTrue({key.visible_node_id for key in detail_keys} <=
                        {node["id"] for node in detail["nodes"]})
        self.assertEqual(published.publication_revision, before.publication_revision + 1)
        self.assert_snapshot_backed(published)

    def test_rejected_request_and_repeat_delivery_do_not_replace_context(self):
        self.project_view()
        delivered = self.context()
        response = self.bridge.get_view("ProjectStructure", root_id="missing-root")
        self.assertFalse(response["ok"], response)
        self.assertIs(self.context(), delivered)
        self.project_view()
        self.assertIs(self.context(), delivered)

    def test_analysis_invalidation_clears_delivered_map(self):
        self.project_view()
        delivered = self.context()
        self.assertTrue(delivered.context_map)
        self.success(self.bridge.analyze())
        after = self.context()
        self.assertGreater(after.analysis_generation, delivered.analysis_generation)
        self.assertEqual(dict(after.context_map), {})
        self.assertTrue(delivered.context_map)


if __name__ == "__main__":
    unittest.main()
