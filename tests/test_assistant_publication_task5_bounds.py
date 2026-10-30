"""Task 5 delivery replacement, aggregate limits, and stale-view publication."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.assistant.tool_contracts import EntityRef
from codeviz.core import (
    SourceAnchor,
    ViewKind,
    VisibleAggregation,
    VisibleGraph,
    VisibleNode,
)
from codeviz.persistence.store import CacheStore
from codeviz.ui.assistant_context import ViewNodeKey
from codeviz.ui.controller import AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.web_bridge import DesktopBridge


class DeliveredViewBoundsTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="codeviz-task5-bounds-")
        self.addCleanup(temporary.cleanup)
        scope = Path(temporary.name)
        root = scope / "project"
        root.mkdir()
        (root / "app.py").write_text(
            "".join(f"def function_{index}():\n    return {index}\n"
                    for index in range(25)),
            encoding="utf-8",
        )
        store = CacheStore(scope / "cache")
        self.controller = AppController(store)
        self.bridge = DesktopBridge(
            self.controller, store, PreferencesStore(scope / "preferences"))
        self.success(self.bridge.open_project(str(root)))
        self.success(self.bridge.analyze())
        self.assertTrue(self.context().eligible)
        self.code_id = self.context().snapshot.code_node_inventory[0].id

    def success(self, response):
        self.assertTrue(response["ok"], response)
        json.dumps(response)
        return response["data"]

    def context(self):
        return self.controller.capture_assistant_context()

    def graph(self, kind, ids, *, code_ids=None, anchors=()):
        targets = (self.code_id,) if code_ids is None else code_ids
        return VisibleGraph(
            view_kind=kind,
            nodes=tuple(VisibleNode(
                id=node_id, display_name=node_id,
                code_node_ids=targets, source_anchors=anchors,
            ) for node_id in ids),
        )

    def assert_locations_match_map(self, context):
        for target, fact in context.navigation_facts.items():
            expected = {
                key for key, node in context.context_map.items()
                if target in node.targets
            }
            self.assertEqual(set(fact.visible_locations), expected, target)
        for key, node in context.context_map.items():
            for target in node.targets:
                self.assertIn(target, context.navigation_facts)
                self.assertIn(key, context.navigation_facts[target].visible_locations)

    def test_aggregate_cap_and_latest_root_bucket_per_kind(self):
        first = self.graph(ViewKind.CALLS, tuple(f"first-{i}" for i in range(80)))
        second = self.graph(ViewKind.DATA_FLOW, tuple(f"second-{i}" for i in range(80)))
        replacement = self.graph(ViewKind.CALLS, ("replacement-0", "replacement-1"))
        with (patch.object(self.controller, "view", side_effect=(first, replacement)),
              patch.object(self.controller, "detail_view", return_value=second)):
            self.success(self.bridge.get_view("Calls", root_id="root-a"))
            self.success(self.bridge.get_detail_view("DataFlow", "root-b", "detail-b"))
            bounded = self.context()
            self.assertEqual(len(bounded.context_map), 100)
            self.assertEqual(sum(key.detail_source_id == "detail-b"
                                 for key in bounded.context_map), 80)
            self.assertEqual(sum(key.detail_source_id is None
                                 for key in bounded.context_map), 20)
            self.assert_locations_match_map(bounded)

            self.success(self.bridge.get_view("Calls", root_id="root-c"))
        current = self.context()
        self.assertLessEqual(len(current.context_map), 100)
        self.assertEqual({key.visible_node_id for key in current.context_map
                          if key.view_kind is ViewKind.CALLS},
                         {"replacement-0", "replacement-1"})
        self.assertEqual({key.root_id for key in current.context_map},
                         {"root-b", "root-c"})
        self.assertEqual(sum(key.detail_source_id == "detail-b"
                             for key in current.context_map), 80)
        self.assert_locations_match_map(current)

    def test_latest_orphan_detail_bucket_per_view_kind(self):
        graphs = (
            self.graph(ViewKind.CALLS, ("detail-a",)),
            self.graph(ViewKind.DATA_FLOW, ("detail-b",)),
            self.graph(ViewKind.CALLS, ("detail-c",)),
        )
        with patch.object(self.controller, "detail_view", side_effect=graphs):
            self.success(self.bridge.get_detail_view("Calls", None, "orphan-a"))
            self.success(self.bridge.get_detail_view("DataFlow", None, "orphan-b"))
            self.success(self.bridge.get_detail_view("Calls", None, "orphan-c"))
        current = self.context()
        self.assertEqual(set(current.context_map), {
            ViewNodeKey(ViewKind.DATA_FLOW, None, "orphan-b", "detail-b"),
            ViewNodeKey(ViewKind.CALLS, None, "orphan-c", "detail-c"),
        })
        self.assert_locations_match_map(current)

    def test_identical_delivery_is_noop_changed_map_replaces_facts_and_revision(self):
        members = (self.code_id,)
        first = VisibleGraph(
            view_kind=ViewKind.CALLS,
            nodes=(VisibleNode("visible-a", "visible-a", member_ids=members),),
            aggregations=(VisibleAggregation("visible-a", members),),
        )
        changed = VisibleGraph(
            view_kind=ViewKind.CALLS,
            nodes=(VisibleNode("visible-b", "visible-b", member_ids=members),),
            aggregations=(VisibleAggregation("visible-b", members),),
        )
        with patch.object(self.controller, "view", side_effect=(first, first, changed)):
            self.success(self.bridge.get_view("Calls", root_id="focus"))
            published = self.context()
            old_key = ViewNodeKey(ViewKind.CALLS, "focus", None, "visible-a")
            self.assertEqual(set(published.context_map), {old_key})
            self.success(self.bridge.get_view("Calls", root_id="focus"))
            self.assertIs(self.context(), published)
            self.success(self.bridge.get_view("Calls", root_id="focus"))
        current = self.context()
        key = ViewNodeKey(ViewKind.CALLS, "focus", None, "visible-b")
        self.assertEqual(set(current.context_map), {key})
        self.assertEqual(current.publication_revision,
                         published.publication_revision + 1)
        self.assertIs(current.snapshot, published.snapshot)
        self.assertEqual(current.navigation_facts[EntityRef("code", self.code_id)].visible_locations,
                         (key,))
        self.assertEqual(set(published.context_map), {old_key})
        self.assert_locations_match_map(current)

    def test_overlong_visible_id_and_more_than_twenty_provenance_items(self):
        records = []
        seen_anchors = set()
        for record in self.context().snapshot.code_node_inventory:
            if record.source_anchors and record.source_anchors[0] not in seen_anchors:
                records.append(record)
                seen_anchors.add(record.source_anchors[0])
            if len(records) == 21:
                break
        self.assertEqual(len(records), 21, "fixture needs 21 distinct code anchors")
        anchors = tuple(SourceAnchor(
            item.source_anchors[0].file_path,
            item.source_anchors[0].start_line,
            item.source_anchors[0].start_column,
            item.source_anchors[0].end_line,
            item.source_anchors[0].end_column,
        ) for item in records)
        members = tuple(item.id for item in records)
        graph = VisibleGraph(
            view_kind=ViewKind.CALLS,
            nodes=(
                VisibleNode("dense", "dense", member_ids=members, source_anchors=anchors),
                VisibleNode("x" * 513, "long", code_node_ids=(self.code_id,)),
            ),
            aggregations=(VisibleAggregation("dense", members),),
        )
        with patch.object(self.controller, "view", return_value=graph):
            self.success(self.bridge.get_view("Calls"))
        current = self.context()
        self.assertEqual({key.visible_node_id for key in current.context_map}, {"dense"})
        node = current.context_map[ViewNodeKey(ViewKind.CALLS, None, None, "dense")]
        self.assertEqual(len(node.targets), 20)
        self.assertEqual(len(node.source_anchors), 20)
        self.assertTrue(node.truncated)
        self.assert_locations_match_map(current)

    def test_reanalysis_during_view_build_discards_stale_delivery(self):
        old_graph = self.graph(ViewKind.CALLS, ("old-view",))
        before = self.context()
        completed = []

        def supersede(*args, **kwargs):
            self.controller.analyze()
            completed.append(self.context())
            return old_graph

        with patch.object(self.controller, "view", side_effect=supersede):
            self.success(self.bridge.get_view("Calls"))
        self.assertEqual(len(completed), 1)
        current = self.context()
        self.assertEqual(current.publication_revision,
                         completed[0].publication_revision,
                         "a graph built before reanalysis must not publish afterward")
        self.assertGreater(current.analysis_generation, before.analysis_generation)
        self.assertEqual(dict(current.context_map), {})
        self.assertTrue(current.eligible)


if __name__ == "__main__":
    unittest.main()
