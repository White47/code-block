"""Task 5: delivered group members and anchors need local provenance."""

import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.assistant.tool_contracts import EntityRef
from codeviz.core import (
    NodeKind,
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
from codeviz.views.structure_identity import StructureResolver


class DeliveredGroupProvenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="codeviz-task5-provenance-")
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
        snapshot = self.controller.capture_assistant_context().snapshot
        self.assertIsNotNone(snapshot)
        self.functions = tuple(sorted(
            (record for record in snapshot.code_node_inventory
             if record.kind is NodeKind.FUNCTION and record.name.startswith("function_")),
            key=lambda record: int(record.name.removeprefix("function_")),
        ))
        self.assertEqual(len(self.functions), 25)

    def success(self, response):
        self.assertTrue(response["ok"], response)
        json.dumps(response)
        return response["data"]

    def deliver(self, node):
        aggregations = ((VisibleAggregation(node.id, node.member_ids),)
                        if node.id.startswith("aggregate:") and node.member_ids else ())
        graph = VisibleGraph(view_kind=ViewKind.FUNCTION_FLOW, nodes=(node,),
                             aggregations=aggregations)
        with patch.object(self.controller, "view", return_value=graph):
            delivered = self.success(self.bridge.get_view("FunctionFlow"))
        self.assertEqual([item["id"] for item in delivered["nodes"]], [node.id])
        context = self.controller.capture_assistant_context()
        self.assertTrue(context.eligible)
        return context.context_map[ViewNodeKey(ViewKind.FUNCTION_FLOW, None, None, node.id)]

    def test_valid_aggregate_with_unknown_member_resolves_only_known_records(self):
        members = (self.functions[0].id, "unknown-member", self.functions[1].id)
        aggregate = VisibleNode(
            "aggregate:FunctionFlow:fixture", "3 more", member_ids=members)
        node = self.deliver(aggregate)
        self.assertEqual(node.targets, (
            EntityRef("code", members[0]), EntityRef("code", members[2])))
        self.assertFalse(node.source_only)
        self.assertTrue(node.truncated)
        self.assertNotIn(EntityRef("symbol", aggregate.id), node.targets)
        self.assertFalse(any(target.id == "unknown-member" for target in node.targets))

    def test_mismatched_aggregate_record_cannot_authorize_known_member(self):
        represented, other = self.functions[:2]
        aggregate = VisibleNode(
            "aggregate:FunctionFlow:mismatched", "1 more",
            member_ids=(represented.id,))
        graph = VisibleGraph(
            view_kind=ViewKind.FUNCTION_FLOW,
            nodes=(aggregate,),
            aggregations=(VisibleAggregation(aggregate.id, (other.id,)),),
        )
        with patch.object(self.controller, "view", return_value=graph):
            delivered = self.success(self.bridge.get_view("FunctionFlow"))
        self.assertEqual([item["id"] for item in delivered["nodes"]], [aggregate.id])
        context = self.controller.capture_assistant_context()
        node = context.context_map[
            ViewNodeKey(ViewKind.FUNCTION_FLOW, None, None, aggregate.id)]
        self.assertEqual(node.targets, ())
        self.assertTrue(node.source_only)
        self.assertTrue(node.truncated)

    def test_fully_verified_group_members_are_not_truncated(self):
        members = tuple(record.id for record in self.functions[:2])
        aggregate = VisibleNode(
            "aggregate:FunctionFlow:complete", "2 more", member_ids=members)
        node = self.deliver(aggregate)
        self.assertEqual(node.targets,
                         tuple(EntityRef("code", member) for member in members))
        self.assertFalse(node.truncated)

    def test_real_project_root_with_one_mapped_module_is_not_truncated(self):
        graph = self.controller.view(ViewKind.PROJECT_STRUCTURE)
        project = next(node for node in graph.nodes if node.role == "Project")
        self.assertEqual(len(project.member_ids), 1)
        self.assertEqual(project.code_node_ids, project.member_ids)
        snapshot = self.controller.capture_assistant_context().snapshot
        self.assertIsNotNone(snapshot)
        module_id, = project.member_ids
        self.assertIsNotNone(snapshot.get_symbol(module_id))

        delivered = self.success(self.bridge.get_view("ProjectStructure"))
        self.assertIn(project.id, {node["id"] for node in delivered["nodes"]})
        context = self.controller.capture_assistant_context()
        root = context.context_map[
            ViewNodeKey(ViewKind.PROJECT_STRUCTURE, None, None, project.id)]
        self.assertEqual(root.targets, (EntityRef("symbol", module_id),))
        self.assertFalse(root.source_only)
        self.assertFalse(root.truncated)

    def test_invalid_direct_function_flow_id_cannot_claim_known_member(self):
        graph = self.controller.view(ViewKind.FUNCTION_FLOW)
        direct = next(node for node in graph.nodes if node.code_node_ids)
        self.assertEqual(direct.code_node_ids, (direct.id,))
        member_id = next(record.id for record in self.functions
                         if record.id != direct.id)
        invalid_id = "missing-direct-code-node"
        snapshot = self.controller.capture_assistant_context().snapshot
        self.assertIsNotNone(snapshot)
        self.assertIsNone(snapshot.get_code_node(invalid_id))
        self.assertIsNotNone(snapshot.get_code_node(member_id))
        forged = replace(direct, code_node_ids=(invalid_id,), member_ids=(member_id,))
        graph = replace(graph, nodes=tuple(
            forged if node.id == direct.id else node for node in graph.nodes))

        with patch.object(self.controller, "view", return_value=graph):
            delivered = self.success(self.bridge.get_view("FunctionFlow"))
        self.assertIn(forged.id, {node["id"] for node in delivered["nodes"]})
        context = self.controller.capture_assistant_context()
        node = context.context_map[
            ViewNodeKey(ViewKind.FUNCTION_FLOW, None, None, forged.id)]
        self.assertEqual(node.targets, ())
        self.assertTrue(node.source_only)

    def test_direct_function_flow_node_cannot_claim_foreign_code_through_matching_aggregation(self):
        graph = self.controller.view(ViewKind.FUNCTION_FLOW)
        direct_nodes = tuple(node for node in graph.nodes
                             if node.code_node_ids == (node.id,))
        self.assertGreaterEqual(len(direct_nodes), 2)
        visible, foreign = direct_nodes[:2]
        self.assertNotEqual(visible.id, foreign.id)
        snapshot = self.controller.capture_assistant_context().snapshot
        self.assertIsNotNone(snapshot)
        foreign_record = snapshot.get_code_node(foreign.id)
        self.assertIsNotNone(foreign_record)
        anchor, = foreign_record.source_anchors
        forged = replace(
            visible, code_node_ids=(foreign.id,), member_ids=(foreign.id,),
            source_anchors=(SourceAnchor(
                anchor.file_path, anchor.start_line, anchor.start_column,
                anchor.end_line, anchor.end_column),),
        )
        graph = replace(
            graph,
            nodes=tuple(forged if node.id == visible.id else node
                        for node in graph.nodes),
            aggregations=(*graph.aggregations,
                          VisibleAggregation(forged.id, (foreign.id,))),
        )

        with patch.object(self.controller, "view", return_value=graph):
            delivered = self.success(self.bridge.get_view("FunctionFlow"))
        self.assertIn(forged.id, {item["id"] for item in delivered["nodes"]})
        context = self.controller.capture_assistant_context()
        self.assertTrue(context.eligible)
        forged_key = ViewNodeKey(ViewKind.FUNCTION_FLOW, None, None, forged.id)
        foreign_key = ViewNodeKey(ViewKind.FUNCTION_FLOW, None, None, foreign.id)
        target = EntityRef("code", foreign.id)
        self.assertIn(target, context.context_map[foreign_key].targets)
        self.assertIn(foreign_key, context.navigation_facts[target].visible_locations)
        invalid = context.context_map[forged_key]
        with self.subTest("foreign target"):
            self.assertNotIn(target, invalid.targets)
        with self.subTest("foreign source anchor"):
            self.assertNotIn(anchor, invalid.source_anchors)
        with self.subTest("foreign navigation location"):
            self.assertNotIn(forged_key,
                             context.navigation_facts[target].visible_locations)

    def assert_forged_direct_code_node_has_no_victim_provenance(self, kind, victim_id):
        graph = self.controller.view(kind, victim_id)
        real = next(node for node in graph.nodes if node.id == victim_id)
        self.assertEqual(real.code_node_ids, (victim_id,))
        snapshot = self.controller.capture_assistant_context().snapshot
        self.assertIsNotNone(snapshot)
        victim = snapshot.get_code_node(victim_id)
        self.assertIsNotNone(victim)
        anchor, = victim.source_anchors
        forged = VisibleNode(
            f"forged:{kind.value}:direct", "forged direct node",
            code_node_ids=(victim_id,),
            source_anchors=(SourceAnchor(
                anchor.file_path, anchor.start_line, anchor.start_column,
                anchor.end_line, anchor.end_column),),
        )
        self.assertNotEqual(forged.id, victim_id)
        graph = replace(graph, nodes=(*graph.nodes, forged))

        with patch.object(self.controller, "view", return_value=graph):
            delivered = self.success(self.bridge.get_view(kind.value, root_id=victim_id))
        self.assertIn(forged.id, {item["id"] for item in delivered["nodes"]})
        context = self.controller.capture_assistant_context()
        self.assertTrue(context.eligible)
        real_key = ViewNodeKey(kind, victim_id, None, victim_id)
        forged_key = ViewNodeKey(kind, victim_id, None, forged.id)
        target = EntityRef("symbol" if kind is ViewKind.SYMBOL else "code", victim_id)
        self.assertIn(target, context.context_map[real_key].targets)
        self.assertIn(real_key, context.navigation_facts[target].visible_locations)
        invalid = context.context_map[forged_key]
        with self.subTest("victim code target"):
            self.assertNotIn(EntityRef("code", victim_id), invalid.targets)
            self.assertNotIn(target, invalid.targets)
        with self.subTest("victim source anchor"):
            self.assertNotIn(anchor, invalid.source_anchors)
        with self.subTest("victim navigation location"):
            self.assertNotIn(forged_key,
                             context.navigation_facts[EntityRef("code", victim_id)].visible_locations)
            self.assertNotIn(forged_key, context.navigation_facts[target].visible_locations)
        with self.subTest("unmapped identity"):
            self.assertTrue(invalid.source_only)

    def test_forged_symbol_direct_node_cannot_claim_known_code_node(self):
        self.assert_forged_direct_code_node_has_no_victim_provenance(
            ViewKind.SYMBOL, self.functions[0].id)

    def test_forged_data_flow_direct_node_cannot_claim_known_code_node(self):
        (self.controller.project_root / "data.py").write_text(
            "def normalize(raw):\n"
            "    cleaned = raw\n"
            "    return cleaned\n",
            encoding="utf-8",
        )
        self.success(self.bridge.analyze())
        snapshot = self.controller.capture_assistant_context().snapshot
        self.assertIsNotNone(snapshot)
        victim = next(record for record in snapshot.code_node_inventory
                      if record.kind is NodeKind.DATA and record.file_path == "data.py"
                      and record.source_anchors)
        self.assert_forged_direct_code_node_has_no_victim_provenance(
            ViewKind.DATA_FLOW, victim.id)

    def test_forged_execution_node_cannot_claim_unrelated_code_and_semantic_ids(self):
        (self.controller.project_root / "run.py").write_text(
            "import torch\n"
            "def train(optimizer):\n"
            "    criterion = torch.nn.MSELoss()\n"
            "    optimizer.step()\n",
            encoding="utf-8",
        )
        self.success(self.bridge.analyze())
        snapshot = self.controller.capture_assistant_context().snapshot
        self.assertIsNotNone(snapshot)
        graph = self.controller.view(ViewKind.EXECUTION)
        real = next(node for node in graph.nodes
                    if node.semantic_component_ids and node.code_node_ids
                    and snapshot.get_component(node.semantic_component_ids[0]) is not None
                    and snapshot.get_code_node(node.code_node_ids[0]) is not None)
        semantic_id = real.semantic_component_ids[0]
        code_id = real.code_node_ids[0]
        component = snapshot.get_component(semantic_id)
        code = snapshot.get_code_node(code_id)
        self.assertIsNotNone(component)
        self.assertIsNotNone(code)
        anchors = tuple(dict.fromkeys((*component.source_anchors,
                                      *code.source_anchors)))
        self.assertTrue(anchors)
        forged = VisibleNode(
            "forged:Execution:unrelated", "forged execution node",
            semantic_component_ids=(semantic_id,), code_node_ids=(code_id,),
            source_anchors=tuple(SourceAnchor(
                anchor.file_path, anchor.start_line, anchor.start_column,
                anchor.end_line, anchor.end_column) for anchor in anchors),
        )
        self.assertNotEqual(forged.id, real.id)
        graph = replace(graph, nodes=(*graph.nodes, forged))

        with patch.object(self.controller, "view", return_value=graph):
            delivered = self.success(self.bridge.get_view("Execution"))
        self.assertIn(forged.id, {item["id"] for item in delivered["nodes"]})
        context = self.controller.capture_assistant_context()
        self.assertTrue(context.eligible)
        real_key = ViewNodeKey(ViewKind.EXECUTION, None, None, real.id)
        forged_key = ViewNodeKey(ViewKind.EXECUTION, None, None, forged.id)
        invalid = context.context_map[forged_key]
        for target in (EntityRef("code", code_id), EntityRef("semantic", semantic_id)):
            self.assertIn(target, context.context_map[real_key].targets)
            self.assertIn(real_key,
                          context.navigation_facts[target].visible_locations)
            with self.subTest("foreign target", target=target):
                self.assertNotIn(target, invalid.targets)
            with self.subTest("foreign navigation location", target=target):
                self.assertNotIn(forged_key,
                                 context.navigation_facts[target].visible_locations)
        for anchor in anchors:
            with self.subTest(anchor=anchor):
                self.assertNotIn(anchor, invalid.source_anchors)

    def test_project_root_requested_under_module_cannot_claim_sibling_module(self):
        (self.controller.project_root / "other.py").write_text(
            "def other():\n    return 1\n", encoding="utf-8")
        self.success(self.bridge.analyze())
        snapshot = self.controller.capture_assistant_context().snapshot
        self.assertIsNotNone(snapshot)
        modules = {record.file_path: record for record in snapshot.symbol_inventory
                   if record.kind is NodeKind.MODULE and record.definition}
        module_a = modules["app.py"]
        module_b = modules["other.py"]
        resolver = StructureResolver(self.controller.analysis,
                                     self.controller.structure_namespace())
        self.assertFalse(resolver.project_valid(module_b.id, module_a.id))
        graph = self.controller.view(ViewKind.PROJECT_STRUCTURE, module_a.id)
        real = next(node for node in graph.nodes if node.id == module_a.id)
        self.assertEqual(real.code_node_ids, (module_a.id,))
        forged = VisibleNode(
            resolver.project_id, "Project", code_node_ids=(module_b.id,),
            member_ids=(module_b.id,), role="Project",
            source_anchors=(SourceAnchor(
                module_b.source_anchor.file_path,
                module_b.source_anchor.start_line,
                module_b.source_anchor.start_column,
                module_b.source_anchor.end_line,
                module_b.source_anchor.end_column),),
        )
        graph = replace(graph, nodes=(forged, *graph.nodes),
                        root_ids=(forged.id,))

        with patch.object(self.controller, "view", return_value=graph):
            delivered = self.success(self.bridge.get_view(
                "ProjectStructure", root_id=module_a.id))
        self.assertIn(forged.id, {item["id"] for item in delivered["nodes"]})
        context = self.controller.capture_assistant_context()
        self.assertTrue(context.eligible)
        real_key = ViewNodeKey(ViewKind.PROJECT_STRUCTURE, module_a.id, None,
                               module_a.id)
        forged_key = ViewNodeKey(ViewKind.PROJECT_STRUCTURE, module_a.id, None,
                                 forged.id)
        self.assertIn(EntityRef("symbol", module_a.id),
                      context.context_map[real_key].targets)
        invalid = context.context_map[forged_key]
        target = EntityRef("symbol", module_b.id)
        with self.subTest("sibling module target"):
            self.assertNotIn(target, invalid.targets)
        with self.subTest("sibling module source anchor"):
            self.assertNotIn(module_b.source_anchor, invalid.source_anchors)
        with self.subTest("sibling module navigation location"):
            self.assertNotIn(forged_key, context.navigation_facts[target].visible_locations)
        with self.subTest("unmapped identity"):
            self.assertTrue(invalid.source_only)

    def test_project_structure_ordinary_sibling_node_cannot_claim_module_under_other_root(self):
        (self.controller.project_root / "other.py").write_text(
            "def other():\n    return 1\n", encoding="utf-8")
        self.success(self.bridge.analyze())
        snapshot = self.controller.capture_assistant_context().snapshot
        self.assertIsNotNone(snapshot)
        modules = {record.file_path: record for record in snapshot.symbol_inventory
                   if record.kind is NodeKind.MODULE and record.definition}
        module_a = modules["app.py"]
        module_b = modules["other.py"]
        resolver = StructureResolver(self.controller.analysis,
                                     self.controller.structure_namespace())
        self.assertFalse(resolver.project_valid(module_b.id, module_a.id))
        graph = self.controller.view(ViewKind.PROJECT_STRUCTURE, module_a.id)
        sibling_graph = self.controller.view(ViewKind.PROJECT_STRUCTURE, module_b.id)
        real = next(node for node in graph.nodes if node.id == module_a.id)
        sibling = next(node for node in sibling_graph.nodes if node.id == module_b.id)
        self.assertEqual(real.code_node_ids, (module_a.id,))
        self.assertEqual(sibling.code_node_ids, (module_b.id,))
        self.assertEqual(sibling.member_ids, ())
        graph = replace(graph, nodes=(*graph.nodes, sibling))

        with patch.object(self.controller, "view", return_value=graph):
            delivered = self.success(self.bridge.get_view(
                "ProjectStructure", root_id=module_a.id))
        self.assertIn(sibling.id, {item["id"] for item in delivered["nodes"]})
        context = self.controller.capture_assistant_context()
        self.assertTrue(context.eligible)
        real_key = ViewNodeKey(ViewKind.PROJECT_STRUCTURE, module_a.id, None,
                               module_a.id)
        sibling_key = ViewNodeKey(ViewKind.PROJECT_STRUCTURE, module_a.id, None,
                                  module_b.id)
        real_target = EntityRef("symbol", module_a.id)
        self.assertIn(real_target, context.context_map[real_key].targets)
        self.assertIn(real_key,
                      context.navigation_facts[real_target].visible_locations)
        target = EntityRef("symbol", module_b.id)
        invalid = context.context_map[sibling_key]
        with self.subTest("sibling target"):
            self.assertNotIn(target, invalid.targets)
        with self.subTest("sibling source anchor"):
            self.assertNotIn(module_b.source_anchor, invalid.source_anchors)
        with self.subTest("sibling navigation location"):
            self.assertNotIn(sibling_key,
                             context.navigation_facts[target].visible_locations)

    def test_more_than_twenty_known_members_are_bounded_and_marked_truncated(self):
        members = tuple(record.id for record in self.functions[:21])
        aggregate = VisibleNode(
            "aggregate:FunctionFlow:dense", "21 more", member_ids=members)
        node = self.deliver(aggregate)
        self.assertEqual(node.targets,
                         tuple(EntityRef("code", member) for member in members[:20]))
        self.assertEqual(len(node.targets), 20)
        self.assertTrue(node.truncated)
        self.assertFalse(node.source_only)

    def test_unrelated_inventory_anchor_does_not_authorize_group_source(self):
        represented, unrelated = self.functions[:2]
        self.assertNotEqual(represented.source_anchors[0], unrelated.source_anchors[0])
        foreign_anchor = unrelated.source_anchors[0]
        aggregate = VisibleNode(
            "aggregate:FunctionFlow:foreign-anchor", "1 more",
            member_ids=(represented.id,),
            source_anchors=(SourceAnchor(
                foreign_anchor.file_path, foreign_anchor.start_line,
                foreign_anchor.start_column, foreign_anchor.end_line,
                foreign_anchor.end_column),),
        )
        node = self.deliver(aggregate)
        self.assertNotIn(foreign_anchor, node.source_anchors)

    def test_anchor_only_function_flow_node_cannot_borrow_unrelated_function_anchor(self):
        foreign_anchor = self.functions[0].source_anchors[0]
        snapshot = self.controller.capture_assistant_context().snapshot
        self.assertIsNotNone(snapshot)
        self.assertIn(foreign_anchor, snapshot.get_code_node(self.functions[0].id).source_anchors)
        visible = VisibleNode(
            "anchor-only:unrelated-function", "unmapped source",
            source_anchors=(SourceAnchor(
                foreign_anchor.file_path, foreign_anchor.start_line,
                foreign_anchor.start_column, foreign_anchor.end_line,
                foreign_anchor.end_column),),
        )
        self.assertEqual(visible.code_node_ids, ())
        self.assertEqual(visible.semantic_component_ids, ())
        self.assertEqual(visible.member_ids, ())

        node = self.deliver(visible)
        self.assertEqual(node.targets, ())
        self.assertTrue(node.source_only)
        self.assertNotIn(foreign_anchor, node.source_anchors)

    def test_id_free_function_flow_node_retains_its_own_source_anchor(self):
        record = self.functions[0]
        anchor, = record.source_anchors
        visible = VisibleNode(
            record.id, record.name,
            source_anchors=(SourceAnchor(
                anchor.file_path, anchor.start_line, anchor.start_column,
                anchor.end_line, anchor.end_column),),
        )
        self.assertEqual(visible.code_node_ids, ())

        node = self.deliver(visible)
        self.assertEqual(node.targets, ())
        self.assertTrue(node.source_only)
        self.assertEqual(node.source_anchors, (anchor,))

    def test_valid_architecture_occurrence_cannot_borrow_unrelated_code_provenance(self):
        (self.controller.project_root / "model.py").write_text(
            "import torch.nn as nn\n\n"
            "class Net(nn.Module):\n"
            "    def forward(self, features):\n"
            "        return features\n",
            encoding="utf-8",
        )
        self.success(self.bridge.analyze())
        snapshot = self.controller.capture_assistant_context().snapshot
        self.assertIsNotNone(snapshot)
        graph = self.controller.view(ViewKind.ARCHITECTURE)
        occurrence = next(node for node in graph.nodes
                          if node.role == "ModelDefinition" and node.code_node_ids
                          and node.source_anchors)
        resolver = StructureResolver(self.controller.analysis,
                                     self.controller.structure_namespace())
        identity = resolver.resolve(occurrence.id)
        semantic_id, = occurrence.semantic_component_ids
        self.assertEqual(identity.component.id, semantic_id)
        self.assertIsNotNone(snapshot.get_component(semantic_id))
        self.assertIsNotNone(snapshot.get_code_node(occurrence.code_node_ids[0]))
        key = ViewNodeKey(ViewKind.ARCHITECTURE, None, None, occurrence.id)
        self.success(self.bridge.get_view("Architecture"))
        real = self.controller.capture_assistant_context().context_map[key]
        self.assertIn(EntityRef("semantic", semantic_id), real.targets)
        self.assertIn(EntityRef("code", occurrence.code_node_ids[0]), real.targets)
        self.assertEqual(real.source_anchors,
                         snapshot.get_component(semantic_id).source_anchors)

        foreign_codes = (
            snapshot.get_code_node(self.functions[0].id),
            next(record for record in snapshot.code_node_inventory
                 if record.file_path == "model.py" and record.kind is NodeKind.METHOD
                 and record.name == "forward"),
        )
        for foreign in foreign_codes:
            self.assertIsNotNone(foreign)
            self.assertNotIn(foreign.id, occurrence.code_node_ids)
            foreign_anchor = foreign.source_anchors[0]
            self.assertNotIn(foreign_anchor, occurrence.source_anchors)
            forged = replace(
                occurrence, code_node_ids=(foreign.id,),
                source_anchors=(SourceAnchor(
                    foreign_anchor.file_path, foreign_anchor.start_line,
                    foreign_anchor.start_column, foreign_anchor.end_line,
                    foreign_anchor.end_column),),
            )
            swapped = replace(graph, nodes=tuple(
                forged if node.id == occurrence.id else node for node in graph.nodes))
            with patch.object(self.controller, "view", return_value=swapped):
                delivered = self.success(self.bridge.get_view("Architecture"))
            self.assertIn(forged.id, {node["id"] for node in delivered["nodes"]})
            context = self.controller.capture_assistant_context()
            self.assertTrue(context.eligible)
            node = context.context_map[key]
            self.assertIn(EntityRef("semantic", semantic_id), node.targets)
            with self.subTest(f"{foreign.file_path} foreign code target"):
                self.assertNotIn(EntityRef("code", foreign.id), node.targets)
            with self.subTest(f"{foreign.file_path} foreign source anchor"):
                self.assertNotIn(foreign_anchor, node.source_anchors)

    def test_invalid_architecture_projection_publishes_no_targets_or_anchors(self):
        (self.controller.project_root / "app.py").write_text(
            "import torch.nn as nn\n\n"
            "class Net(nn.Module):\n"
            "    def forward(self, features):\n"
            "        prediction = features\n"
            "        return prediction\n",
            encoding="utf-8",
        )
        self.success(self.bridge.analyze())
        snapshot = self.controller.capture_assistant_context().snapshot
        self.assertIsNotNone(snapshot)
        real_graph = self.controller.view(ViewKind.ARCHITECTURE)
        structural = next(node for node in real_graph.nodes
                          if node.semantic_component_ids and node.code_node_ids)
        data = next(node for node in real_graph.nodes if node.role == "DataNode")
        self.assertEqual(data.code_node_ids, (data.id,))
        self.assertIsNotNone(snapshot.get_component(structural.semantic_component_ids[0]))
        self.assertIsNotNone(snapshot.get_code_node(structural.code_node_ids[0]))
        self.assertIsNotNone(snapshot.get_code_node(data.id))
        forged = VisibleNode(
            f"projection:{self.controller.structure_namespace()}:"
            f"{structural.semantic_component_ids[0]}/missing-child",
            "forged occurrence",
            semantic_component_ids=(structural.semantic_component_ids[0],),
            code_node_ids=(structural.code_node_ids[0],),
            source_anchors=structural.source_anchors,
        )
        self.assertTrue(forged.source_anchors)
        with self.assertRaisesRegex(ValueError, "invalid structural occurrence transition"):
            StructureResolver(self.controller.analysis,
                              self.controller.structure_namespace()).resolve(forged.id)
        graph = VisibleGraph(ViewKind.ARCHITECTURE, nodes=(forged, data))
        with patch.object(self.controller, "view", return_value=graph):
            delivered = self.success(self.bridge.get_view("Architecture"))
        self.assertEqual([item["id"] for item in delivered["nodes"]],
                         [forged.id, data.id])
        context = self.controller.capture_assistant_context()
        self.assertTrue(context.eligible)
        invalid = context.context_map[ViewNodeKey(ViewKind.ARCHITECTURE, None, None,
                                                   forged.id)]
        self.assertEqual(invalid.targets, ())
        self.assertEqual(invalid.source_anchors, ())
        self.assertTrue(invalid.source_only)
        valid = context.context_map[ViewNodeKey(ViewKind.ARCHITECTURE, None, None, data.id)]
        self.assertEqual(valid.targets, (EntityRef("code", data.id),))
        self.assertEqual(valid.source_anchors,
                         snapshot.get_code_node(data.id).source_anchors)

    def test_real_architecture_detail_publishes_occurrence_and_definition(self):
        (self.controller.project_root / "block.py").write_text(
            "import torch.nn as nn\n\n"
            "class Block(nn.Module):\n"
            "    def __init__(self):\n"
            "        super().__init__()\n"
            "        self.projection = nn.Linear(8, 8)\n"
            "    def forward(self, value):\n"
            "        return self.projection(value)\n",
            encoding="utf-8",
        )
        (self.controller.project_root / "model.py").write_text(
            "import torch.nn as nn\n"
            "from block import Block\n\n"
            "class Net(nn.Module):\n"
            "    def __init__(self):\n"
            "        super().__init__()\n"
            "        self.block = Block()\n"
            "    def forward(self, value):\n"
            "        return self.block(value)\n",
            encoding="utf-8",
        )
        self.success(self.bridge.analyze())
        analysis = self.controller.analysis
        snapshot = self.controller.capture_assistant_context().snapshot
        self.assertIsNotNone(snapshot)
        root = next(definition.symbol_id for definition in analysis.model.definitions
                    if definition.qualified_name == "model.Net")
        overview = self.success(self.bridge.get_view("Architecture", root))
        source = next(node for node in overview["nodes"]
                      if node["codeName"] == "model.Net.block")
        current_id, = source["semanticComponentIds"]
        definition_id = next(definition.symbol_id for definition in analysis.model.definitions
                             if definition.qualified_name == "block.Block")
        self.assertNotEqual(current_id, definition_id)
        instance = next(item for item in analysis.model.instances if item.id == current_id)
        self.assertEqual(instance.owner_definition_id, root)
        self.assertEqual(instance.target_definition_id, definition_id)
        self.assertEqual(instance.source_anchor.file_path, "model.py")
        self.assertEqual(next(item for item in analysis.model.definitions
                              if item.symbol_id == definition_id).source_anchor.file_path,
                         "block.py")
        self.assertEqual(source["codeNodeIds"], [definition_id])

        # A root-view occurrence cannot claim the definition merely by listing it.
        graph = self.controller.view(ViewKind.ARCHITECTURE, root)
        source_node = next(node for node in graph.nodes if node.id == source["id"])
        self.assertEqual(source_node.code_node_ids, (definition_id,))
        root_key = ViewNodeKey(ViewKind.ARCHITECTURE, root, None, source["id"])
        original = self.controller.capture_assistant_context().context_map[root_key]
        self.assertIn(EntityRef("semantic", current_id), original.targets)
        self.assertIn(EntityRef("code", definition_id), original.targets)
        forged = replace(next(node for node in graph.nodes if node.id == source["id"]),
                         semantic_component_ids=(current_id, definition_id))
        with patch.object(self.controller, "view", return_value=replace(
                graph, nodes=tuple(forged if node.id == forged.id else node
                                   for node in graph.nodes))):
            self.success(self.bridge.get_view("Architecture", root))
        root_node = self.controller.capture_assistant_context().context_map[root_key]
        self.assertIn(EntityRef("semantic", current_id), root_node.targets)
        self.assertNotIn(EntityRef("semantic", definition_id), root_node.targets)

        wrong_primary = replace(source_node, semantic_component_ids=(definition_id,))
        with patch.object(self.controller, "view", return_value=replace(
                graph, nodes=tuple(wrong_primary if node.id == source["id"] else node
                                   for node in graph.nodes))):
            self.success(self.bridge.get_view("Architecture", root))
        mismatched = self.controller.capture_assistant_context().context_map[root_key]
        self.assertNotIn(EntityRef("semantic", definition_id), mismatched.targets)
        with self.subTest("mismatched primary semantic ID"):
            self.assertEqual(mismatched.targets, ())

        direct_detail = self.controller.detail_view(
            ViewKind.ARCHITECTURE, root, source["id"])
        self.assertIn(source["id"], {node.id for node in direct_detail.nodes})
        projection = next(item for item in analysis.model.instances
                          if item.owner_definition_id == definition_id
                          and item.name == "projection")
        fallback = next(node for node in direct_detail.nodes
                        if node.semantic_component_ids == (projection.id,))
        self.assertTrue(fallback.id.startswith("projection:"))
        self.assertEqual(snapshot.get_component(projection.id).code_symbol_ids, ())
        self.assertEqual(fallback.code_node_ids, (definition_id,))
        self.assertEqual(projection.source_anchor.file_path, "block.py")
        detail = self.success(self.bridge.get_detail_view(
            "Architecture", root, source["id"]))
        context = self.controller.capture_assistant_context()
        fallback_key = ViewNodeKey(ViewKind.ARCHITECTURE, root, source["id"], fallback.id)
        fallback_context = context.context_map[fallback_key]
        self.assertIn(EntityRef("semantic", projection.id), fallback_context.targets)
        self.assertIn(EntityRef("code", definition_id), fallback_context.targets)
        self.assertEqual(fallback_context.source_anchors,
                         context.snapshot.get_component(projection.id).source_anchors)
        delivered = next(node for node in detail["nodes"] if node["id"] == source["id"])
        self.assertEqual(delivered["semanticComponentIds"], [current_id, definition_id])
        self.assertTrue(context.eligible)
        key = ViewNodeKey(ViewKind.ARCHITECTURE, root, source["id"], source["id"])
        node = context.context_map[key]
        current = snapshot.get_component(current_id)
        definition = snapshot.get_component(definition_id)
        self.assertIsNotNone(current)
        self.assertIsNotNone(definition)
        self.assertTrue(current.source_anchors)
        self.assertTrue(definition.source_anchors)
        self.assertNotEqual(current.source_anchors, definition.source_anchors)
        self.assertEqual({target for target in node.targets if target.kind == "semantic"},
                         {EntityRef("semantic", current_id),
                          EntityRef("semantic", definition_id)})
        self.assertEqual(set(node.source_anchors),
                         set(current.source_anchors) | set(definition.source_anchors))
        for component_id, component in ((current_id, current),
                                        (definition_id, definition)):
            fact = context.navigation_facts[EntityRef("semantic", component_id)]
            self.assertEqual(fact.source_anchors, component.source_anchors)
            self.assertIn(key, fact.visible_locations)

    def test_newer_delivered_map_supersedes_older_prepared_view(self):
        old = VisibleGraph(view_kind=ViewKind.FUNCTION_FLOW, nodes=(
            VisibleNode("old", "old", code_node_ids=(self.functions[0].id,)),))
        new = VisibleGraph(view_kind=ViewKind.FUNCTION_FLOW, nodes=(
            VisibleNode("new", "new", code_node_ids=(self.functions[1].id,)),))
        before = self.controller.capture_assistant_context()
        newer = []

        def nested_delivery(*args, **kwargs):
            if not newer:
                newer.append(None)
                self.success(self.bridge.get_view("FunctionFlow"))
                newer[0] = self.controller.capture_assistant_context()
                return old
            return new

        with patch.object(self.controller, "view", side_effect=nested_delivery):
            outer = self.success(self.bridge.get_view("FunctionFlow"))
        self.assertEqual([node["id"] for node in outer["nodes"]], ["old"])
        current = self.controller.capture_assistant_context()
        self.assertIs(current, newer[0])
        self.assertEqual(current.publication_revision, before.publication_revision + 1)
        self.assertEqual(set(current.context_map), {
            ViewNodeKey(ViewKind.FUNCTION_FLOW, None, None, "new")})


if __name__ == "__main__":
    unittest.main()
