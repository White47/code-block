"""Public bridge regressions for bounded, source-backed structure exploration.

Fixtures are written locally and analyzed statically; target code is never imported.
This file deliberately owns its fixtures and does not depend on dirty test helpers.
"""

import ast
import inspect
import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.core import NodeKind, ViewKind
from codeviz.persistence.codec import encode_analysis
from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.web_bridge import DesktopBridge
from codeviz.views.builder import ViewRequest, build_visible_graph

MODEL_SOURCE = """import torch.nn as nn

class Block(nn.Module):
    def __init__(self):
        super().__init__()
        self.projection = nn.Linear(8, 8)
    def forward(self, x):
        return self.projection(x)

class Encoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.layers = nn.ModuleList([Block() for _ in range(2)])
    def forward(self, x):
        for layer in self.layers:
            x = layer(x)
        return x

class Classifier(nn.Module):
    def __init__(self):
        super().__init__()
        self.embedding = nn.Embedding(100, 8)
        self.encoder = Encoder()
        self.head = nn.Linear(8, 2)
    def forward(self, tokens):
        hidden = self.embedding(tokens)
        hidden = self.encoder(hidden)
        return self.head(hidden)

class Pair(nn.Module):
    def __init__(self):
        super().__init__()
        self.left = Encoder()
        self.right = Encoder()
    def forward(self, x):
        return self.left(x) + self.right(x)

class Recursive(nn.Module):
    def __init__(self):
        super().__init__()
        self.child = Recursive()
    def forward(self, x):
        return self.child(x)
"""

RUN_SOURCE = """import torch
import torch.nn as nn
from model import Classifier

def train(loader):
    model = Classifier()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.1)
    criterion = nn.CrossEntropyLoss()
    model.train()
    for batch, target in loader:
        optimizer.zero_grad()
        prediction = model(batch)
        loss = criterion(prediction, target)
        loss.backward()
        optimizer.step()
    return model
"""

PLAIN_SOURCE = """class Service:
    def process(self, value):
        return helper(value)

def helper(value):
    result = value + 1
    return result

def main(value):
    return Service().process(value)
"""

DATA_MODEL_SOURCE = """import torch.nn as nn

class Net(nn.Module):
    def forward(self, features):
        prediction = features
        scratch = features
        return prediction

class OtherNet(nn.Module):
    def forward(self, features):
        prediction = features
        return prediction
"""


class StructureExplorationTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.cache = CacheStore(self.base / "cache")
        self.controller = AppController(self.cache)
        self.bridge = DesktopBridge(
            self.controller, self.cache, PreferencesStore(self.base / "preferences"))

    def data(self, response):
        self.assertIs(response["ok"], True, response)
        self.assertEqual(response["schemaVersion"], 1)
        json.dumps(response, allow_nan=False)
        return response["data"]

    def invalid(self, response):
        self.assertIs(response["ok"], False, response)
        self.assertEqual(response["error"]["code"], "INVALID_REQUEST", response)

    def analyze(self, files=None):
        for name, source in (files or {"model.py": MODEL_SOURCE, "run.py": RUN_SOURCE}).items():
            (self.root / name).write_text(source, encoding="utf-8")
        self.data(self.bridge.open_project(str(self.root)))
        session = self.data(self.bridge.analyze())
        self.assertIn(session["status"], ("Ready", "Partial"))
        return self.controller.analysis

    def require_depth_contract(self, method="get_view"):
        parameters = inspect.signature(getattr(DesktopBridge, method)).parameters
        self.assertIn(
            "structure_depth", parameters,
            f"{method} must expose the approved trailing structure_depth argument")
        self.assertEqual(list(parameters)[-1], "structure_depth")
        self.assertEqual(parameters["structure_depth"].default, 1)

    def model_root(self, name="Classifier"):
        matches = [item.symbol_id for item in self.controller.analysis.model.definitions
                   if item.qualified_name == f"model.{name}"]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def view(self, depth, root=None, selected=None, kind="Architecture"):
        self.require_depth_contract()
        return self.data(self.bridge.get_view(kind, root, selected, 1, "both", depth))

    def names(self, graph):
        return {node["codeName"] for node in graph["nodes"]}

    def ids(self, graph):
        return {node["id"] for node in graph["nodes"]}

    def node(self, graph, name):
        matches = [node for node in graph["nodes"] if node["codeName"] == name]
        self.assertEqual(len(matches), 1, (name, graph["nodes"]))
        return matches[0]

    def save_selection(self, root, selected):
        return self.data(self.bridge.save_layout({
            "activeView": "Architecture", "selectedId": selected,
            "openTabs": ["Architecture"], "graphRoots": {"Architecture": root},
        }))

    def test_approved_bridge_signatures_have_trailing_depth_default_one(self):
        self.require_depth_contract("get_view")
        self.require_depth_contract("get_inspector")

    def test_project_structure_is_a_real_view_kind(self):
        self.assertIn("PROJECT_STRUCTURE", ViewKind.__members__)
        self.assertEqual(ViewKind.__members__["PROJECT_STRUCTURE"].value, "ProjectStructure")

    def test_default_architecture_is_root_and_exactly_three_direct_children(self):
        self.analyze()
        root = self.model_root()
        graph = self.data(self.bridge.get_view("Architecture", root))
        self.assertEqual(len(graph["nodes"]), 4)
        self.assertEqual(self.names(graph), {
            "model.Classifier", "model.Classifier.embedding",
            "model.Classifier.encoder", "model.Classifier.head",
        })
        self.assertEqual(graph["rootIds"], [root])
        context = graph["queryContext"]
        self.assertEqual(context.get("controlMode"), "structure")
        self.assertEqual(context.get("structureDepth"), 1)
        self.assertIs(context.get("canExpand"), True)
        self.assertIs(context.get("depthLimit"), False)
        self.assertIs(context.get("hasMore"), True)
        self.assertIs(context.get("budgetTruncated"), False)

    def test_depth_two_layers_three_members_four_block_projection(self):
        self.require_depth_contract()
        analysis = self.analyze()
        root = self.model_root()
        before = self.cache.analysis_path(self.root).read_bytes()
        original_model = analysis.model
        original_semantic = analysis.semantic.graph
        one, two, three, four = [self.view(depth, root) for depth in (1, 2, 3, 4)]
        self.assertEqual(len(one["nodes"]), 4)
        self.assertLess(self.ids(one), self.ids(two))
        self.assertLess(self.ids(two), self.ids(three))
        self.assertLess(self.ids(three), self.ids(four))
        self.assertEqual(self.names(two) - self.names(one), {"model.Encoder.layers"})
        self.assertNotIn("model.Block.projection", self.names(three))
        members = [node for node in three["nodes"] if node["id"] not in self.ids(two)]
        layers = self.node(two, "model.Encoder.layers")
        self.assertTrue(members)
        self.assertEqual({edge["target"] for edge in three["edges"]
                          if edge["type"] == "CONTAINS" and edge["source"] == layers["id"]},
                         {node["id"] for node in members})
        self.assertIn("model.Block.projection", self.names(four) - self.names(three))
        for depth, graph in enumerate((one, two, three, four), 1):
            self.assertEqual(graph["queryContext"]["structureDepth"], depth)
            self.assertIs(graph["queryContext"]["budgetTruncated"], False)
            self.assertTrue(all(edge["source"] in self.ids(graph)
                                and edge["target"] in self.ids(graph)
                                for edge in graph["edges"]))
        self.assertIs(analysis.model, original_model)
        self.assertEqual(analysis.semantic.graph, original_semantic)
        self.assertEqual(self.cache.analysis_path(self.root).read_bytes(), before)

    def test_shrink_two_to_one_clears_hidden_selection_without_forced_expansion(self):
        self.require_depth_contract()
        self.analyze()
        root = self.model_root()
        shallow = self.view(1, root)
        deep = self.view(2, root)
        projected = self.node(deep, "model.Encoder.layers")
        self.save_selection(root, projected["id"])
        shrunk = self.view(1, root, projected["id"])
        self.assertEqual(self.ids(shrunk), self.ids(shallow))
        self.assertNotIn(projected["id"], self.ids(shrunk))
        self.assertIsNone(shrunk["queryContext"].get("selectedId"))
        self.assertIsNone(self.data(self.bridge.get_view_state())["selectedId"])

    def test_depth_accepts_only_integer_one_through_five_on_both_bridge_methods(self):
        self.require_depth_contract()
        self.require_depth_contract("get_inspector")
        self.analyze()
        root = self.model_root()
        for depth in (1, 2, 3, 4, 5):
            with self.subTest(valid_depth=depth):
                self.view(depth, root)
                self.data(self.bridge.get_inspector("Architecture", root, root, None, depth))
        for depth in (True, False, 0, 6, -1, 1.0, "2", None, [], {}):
            with self.subTest(invalid_depth=depth):
                self.invalid(self.bridge.get_view("Architecture", root, None, 1, "both", depth))
                self.invalid(self.bridge.get_inspector("Architecture", root, root, None, depth))

    def test_plain_python_structure_contains_exact_sourced_modules_classes_functions(self):
        self.require_depth_contract()
        analysis = self.analyze({"app.py": PLAIN_SOURCE})
        self.assertEqual(analysis.model.definitions, ())
        graph = self.view(5, kind="ProjectStructure")
        expected = [entry for entry in analysis.symbol_index.entries.values()
                    if entry.kind in (NodeKind.MODULE, NodeKind.CLASS,
                                      NodeKind.FUNCTION, NodeKind.METHOD)]
        self.assertEqual({entry.qualified_name for entry in expected}, {
            "app", "app.Service", "app.Service.process", "app.helper", "app.main",
        })
        for entry in expected:
            node = self.node(graph, entry.qualified_name)
            self.assertIn(entry.id, node["codeNodeIds"])
            anchor = entry.source_anchor
            self.assertIn({
                "filePath": "app.py", "startLine": anchor.start_line,
                "startColumn": anchor.start_column, "endLine": anchor.end_line,
                "endColumn": anchor.end_column,
            }, node["sourceAnchors"])
            self.data(self.bridge.get_inspector("ProjectStructure", None, node["id"], None, 5))
            self.data(self.bridge.read_source(node["sourceAnchors"][0]))
        self.assertEqual(graph["queryContext"]["controlMode"], "structure")
        self.assertIs(graph["queryContext"]["budgetTruncated"], False)

    def test_projected_nodes_retain_real_provenance_inspector_and_exact_source_anchor(self):
        self.require_depth_contract()
        self.require_depth_contract("get_inspector")
        analysis = self.analyze()
        root = self.model_root()
        graph = self.view(4, root)
        semantic_ids = {component.id for component in analysis.semantic.graph.components}
        code_ids = set(analysis.flow.graph.nodes) | set(analysis.symbol_index.entries)
        for name in ("model.Encoder.layers", "model.Block.projection"):
            node = self.node(graph, name)
            real = next(item for item in analysis.model.instances if item.qualified_name == name)
            self.assertNotEqual(node["id"], real.id)
            self.assertIn(real.id, node["semanticComponentIds"])
            self.assertTrue(node["semanticComponentIds"])
            self.assertLessEqual(set(node["semanticComponentIds"]), semantic_ids)
            self.assertTrue(node["codeNodeIds"])
            self.assertLessEqual(set(node["codeNodeIds"]), code_ids)
            anchor = real.source_anchor
            expected = {
                "filePath": "model.py", "startLine": anchor.start_line,
                "startColumn": anchor.start_column, "endLine": anchor.end_line,
                "endColumn": anchor.end_column,
            }
            self.assertIn(expected, node["sourceAnchors"])
            # Independently check the anchor against the fixture's constructor AST.
            expression = next(item.value for item in ast.walk(ast.parse(MODEL_SOURCE))
                              if isinstance(item, ast.Assign)
                              and any(isinstance(target, ast.Attribute)
                                      and target.attr == name.rsplit(".", 1)[-1]
                                      for target in item.targets))
            self.assertEqual((anchor.start_line, anchor.end_line),
                             (expression.lineno, expression.end_lineno))
            inspected = self.data(self.bridge.get_inspector(
                "Architecture", root, node["id"], None, 4))
            self.assertEqual(inspected["id"], node["id"])
            self.assertIn(expected, inspected["sourceAnchors"])
            source = self.data(self.bridge.read_source(expected))
            self.assertEqual(source["filePath"], "model.py")
            self.assertEqual(source["startLine"], anchor.start_line)

    def test_two_instances_of_one_definition_have_distinct_stable_occurrence_ids(self):
        self.require_depth_contract()
        self.analyze()
        root = self.model_root("Pair")
        graph = self.view(2, root)
        layers = [node for node in graph["nodes"] if node["codeName"] == "model.Encoder.layers"]
        self.assertEqual(len(layers), 2)
        self.assertEqual(len({node["id"] for node in layers}), 2)
        self.assertEqual(layers[0]["semanticComponentIds"], layers[1]["semanticComponentIds"])
        self.assertEqual(layers[0]["sourceAnchors"], layers[1]["sourceAnchors"])
        parents = {edge["target"]: edge["source"] for edge in graph["edges"]
                   if edge["type"] == "CONTAINS" and edge["target"] in {n["id"] for n in layers}}
        self.assertEqual(set(parents.values()), {
            self.node(graph, "model.Pair.left")["id"], self.node(graph, "model.Pair.right")["id"],
        })
        self.view(1, root)
        self.assertEqual(graph["nodes"], self.view(2, root)["nodes"])
        self.data(self.bridge.analyze())
        self.assertEqual(graph["nodes"], self.view(2, root)["nodes"])

    def test_projected_layers_detail_returns_real_container_members_and_source(self):
        self.require_depth_contract()
        self.require_depth_contract("get_inspector")
        analysis = self.analyze()
        root = self.model_root()
        layers = self.node(self.view(2, root), "model.Encoder.layers")
        self.assertIs(layers["metadata"]["canOpenDetail"], True)
        self.assertIs(layers["metadata"]["hasChildren"], True)
        container = next(instance for instance in analysis.model.instances
                         if instance.qualified_name == "model.Encoder.layers")
        self.assertIsNone(container.target_definition_id)
        real_members = {instance.id: instance for instance in analysis.model.instances
                        if instance.id in container.member_instance_ids}
        self.assertEqual(len(real_members), 1)
        self.assertEqual({instance.qualified_name for instance in real_members.values()},
                         {"model.Encoder.layers[*]"})
        detail = self.data(self.bridge.get_detail_view("Architecture", root, layers["id"]))
        self.assertEqual(detail["viewKind"], "Architecture")
        self.assertIn(layers["id"], self.ids(detail))
        members = [node for node in detail["nodes"]
                   if set(node["semanticComponentIds"]) & set(real_members)]
        self.assertEqual(len(members), len(real_members))
        self.assertEqual({edge["target"] for edge in detail["edges"]
                          if edge["type"] == "CONTAINS" and edge["source"] == layers["id"]},
                         {node["id"] for node in members})
        for node in members:
            member = real_members[node["semanticComponentIds"][0]]
            self.assertNotEqual(node["id"], member.id)
            self.assertEqual(node["codeName"], member.qualified_name)
            anchor = member.source_anchor
            expected = {
                "filePath": "model.py", "startLine": anchor.start_line,
                "startColumn": anchor.start_column, "endLine": anchor.end_line,
                "endColumn": anchor.end_column,
            }
            self.assertIn(expected, node["sourceAnchors"])
            source = self.data(self.bridge.read_source(expected))
            self.assertEqual(source["filePath"], "model.py")
            self.assertEqual(source["startLine"], anchor.start_line)
            self.assertIn("Block()", source["text"])
            inspected = self.data(self.bridge.get_inspector(
                "Architecture", root, node["id"], layers["id"], 3))
            self.assertEqual(inspected["id"], node["id"])
            self.assertIn(expected, inspected["sourceAnchors"])

    def test_recursive_definition_is_bounded_deterministic_at_max_depth(self):
        self.require_depth_contract()
        self.analyze()
        root = self.model_root("Recursive")
        graph = self.view(5, root)
        self.assertGreaterEqual(len(graph["nodes"]), 2)
        self.assertLessEqual(len(graph["nodes"]), 6)
        self.assertEqual(len(self.ids(graph)), len(graph["nodes"]))
        self.assertEqual(graph, self.view(5, root))
        self.assertEqual(graph["queryContext"]["structureDepth"], 5)
        self.assertIs(graph["queryContext"]["depthLimit"], True)
        self.assertIs(graph["queryContext"]["budgetTruncated"], False)

    def test_forged_projected_identity_is_rejected_by_reads_and_mutations(self):
        self.require_depth_contract()
        self.require_depth_contract("get_inspector")
        self.analyze()
        root = self.model_root()
        real = self.node(self.view(2, root), "model.Encoder.layers")["id"]
        self.save_selection(root, None)
        before = self.data(self.bridge.get_view_state())
        for forged in (real + ":forged", real + "/forged", "projection:" + root + ":missing"):
            with self.subTest(forged=forged):
                self.invalid(self.bridge.get_view("Architecture", root, forged, 1, "both", 3))
                self.invalid(self.bridge.get_inspector("Architecture", root, forged, None, 3))
                self.invalid(self.bridge.move_node(forged, [1, 2], "Architecture"))
                self.invalid(self.bridge.expand(forged))
                self.invalid(self.bridge.collapse(forged))
                self.invalid(self.bridge.open_detail_island("Architecture", forged))
                self.invalid(self.bridge.save_layout({
                    "activeView": "Architecture", "selectedId": forged,
                    "openTabs": ["Architecture"], "graphRoots": {"Architecture": root},
                }))
                self.assertEqual(self.data(self.bridge.get_view_state()), before)

    def test_legitimate_projection_cannot_be_used_under_other_root_or_view(self):
        self.require_depth_contract()
        self.require_depth_contract("get_inspector")
        self.analyze()
        root = self.model_root()
        projected = self.node(self.view(2, root), "model.Encoder.layers")["id"]
        pair = self.model_root("Pair")
        self.invalid(self.bridge.get_view("Architecture", pair, projected, 1, "both", 3))
        self.invalid(self.bridge.get_inspector("Architecture", pair, projected, None, 3))
        self.invalid(self.bridge.get_view("ProjectStructure", None, projected, 1, "both", 3))
        self.invalid(self.bridge.move_node(projected, [1, 2], "ProjectStructure"))

    def test_reopen_resets_depth_retains_hidden_positions_safe_islands_and_annotations(self):
        self.require_depth_contract()
        self.analyze()
        original_status = self.data(self.bridge.get_session())["status"]
        self.assertEqual(original_status, "Partial")
        self.assertIn("GENERIC_DATA_CALL_PARTIAL",
                      {issue.code for issue in self.controller.analysis.issues})
        root = self.model_root()
        shallow = self.view(1, root)
        projected = self.node(self.view(2, root), "model.Encoder.layers")
        self.save_selection(root, projected["id"])
        self.data(self.bridge.move_node(projected["id"], [321, 654], "Architecture"))
        encoder = self.node(shallow, "model.Classifier.encoder")
        islands = self.data(self.bridge.open_detail_island("Architecture", encoder["id"], [20, 30]))
        self.assertEqual(len(islands["detailIslands"]), 1)
        self.cache.save_annotations(self.root, {projected["id"]: "keep source-backed note"})
        analysis_before = self.cache.analysis_path(self.root).read_bytes()
        restarted = DesktopBridge(cache_store=self.cache)
        # Read the real cache and forbid analysis during restoration. Partial is
        # intrinsic to this fixture's static call propagation, not a load failure.
        with patch.object(self.cache, "load_analysis", wraps=self.cache.load_analysis) as load, \
                patch("codeviz.ui.controller.analyze_project",
                      side_effect=AssertionError("reopen must not reanalyze")) as analyze:
            opened = self.data(restarted.open_project(str(self.root)))
        load.assert_called_once_with(self.root.resolve())
        analyze.assert_not_called()
        self.assertEqual(opened["status"], original_status)
        self.assertIs(opened["hasAnalysis"], True)
        self.assertEqual(opened["outdatedReasons"], [])
        self.assertIsNone(opened["cacheIssue"])
        self.assertIsNotNone(restarted._controller.analysis)
        cached = self.cache.load_analysis(self.root)
        self.assertIsNotNone(cached)
        self.assertEqual(encode_analysis(restarted._controller.analysis),
                         encode_analysis(cached.analysis))
        state = self.data(restarted.get_view_state())
        self.assertEqual(state["selectedId"], projected["id"])
        self.assertEqual(state["activeView"], "Architecture")
        self.assertEqual(state["graphRoots"]["Architecture"], root)
        island = state["detailIslands"][0]
        self.assertEqual(island["sourceNodeId"], encoder["id"])
        self.assertEqual(island["id"], f"Architecture:{encoder['id']}")
        detail = self.data(restarted.get_detail_view(
            "Architecture", root, island["sourceNodeId"]))
        self.assertEqual(detail["rootIds"], [encoder["id"]])
        restored_member = self.node(detail, "model.Encoder.layers")
        self.assertEqual(restored_member["id"], projected["id"])
        self.assertEqual(restored_member["semanticComponentIds"], projected["semanticComponentIds"])
        self.assertEqual(restored_member["sourceAnchors"], projected["sourceAnchors"])
        inspected = self.data(restarted.get_inspector(
            "Architecture", root, projected["id"], island["sourceNodeId"]))
        self.assertEqual(inspected["id"], projected["id"])
        self.assertEqual(inspected["sourceAnchors"], restored_member["sourceAnchors"])
        self.invalid(restarted.get_detail_view(
            "Architecture", self.model_root("Pair"), island["sourceNodeId"]))
        self.assertEqual(self.data(restarted.get_view_state())["selectedId"], projected["id"])
        self.assertEqual(state["positionsByView"]["Architecture"][projected["id"]], [321, 654])
        self.assertEqual(state["detailIslands"], islands["detailIslands"])
        self.assertEqual(self.ids(self.data(restarted.get_view("Architecture", root))), self.ids(shallow))
        restored = self.data(restarted.get_view("Architecture", root, None, 1, "both", 2))
        self.assertEqual(self.node(restored, "model.Encoder.layers")["id"], projected["id"])
        self.assertEqual(restored["layoutHints"][projected["id"]], [321, 654])
        self.assertEqual(self.cache.load_annotations(self.root),
                         {projected["id"]: "keep source-backed note"})
        self.assertEqual(self.cache.analysis_path(self.root).read_bytes(), analysis_before)
        persisted = self.cache.load_view_state(self.root).to_dict()
        self.assertNotIn("structure_depth", persisted)
        self.assertNotIn("structureDepth", persisted)

    def test_sanitize_internal_detail_builder_error_propagates_without_mutation(self):
        self.analyze()
        root = self.model_root()
        encoder = self.node(self.view(1, root), "model.Classifier.encoder")
        self.save_selection(root, root)
        self.data(self.bridge.open_detail_island("Architecture", encoder["id"], [20, 30]))
        before = self.controller.view_state.to_dict()
        persisted = self.cache.load_view_state(self.root).to_dict()
        analysis_bytes = self.cache.analysis_path(self.root).read_bytes()
        raised = None
        with patch("codeviz.ui.controller.build_architecture_structure",
                   side_effect=ValueError("internal invariant broken")) as builder, \
                patch.object(self.cache, "save_view_state", wraps=self.cache.save_view_state) as save:
            try:
                self.controller.sanitize_view_state()
            except ValueError as error:
                raised = error
        builder.assert_called()
        with self.subTest(contract="unexpected error propagates"):
            self.assertIsInstance(raised, ValueError)
            self.assertEqual(str(raised), "internal invariant broken")
        with self.subTest(contract="no island removal or state mutation"):
            self.assertEqual(self.controller.view_state.to_dict(), before)
        with self.subTest(contract="no persistence"):
            save.assert_not_called()
            self.assertEqual(self.cache.load_view_state(self.root).to_dict(), persisted)
            self.assertEqual(self.cache.analysis_path(self.root).read_bytes(), analysis_bytes)

    def test_open_island_projection_identity_rejects_another_root(self):
        self.analyze()
        root = self.model_root()
        encoder = self.node(self.view(1, root), "model.Classifier.encoder")
        detail = self.data(self.bridge.get_detail_view("Architecture", root, encoder["id"]))
        layers = self.node(detail, "model.Encoder.layers")
        self.assertTrue(layers["id"].startswith("projection:"))
        self.save_selection(root, root)
        self.data(self.bridge.open_detail_island("Architecture", encoder["id"], [20, 30]))
        before = self.controller.view_state.to_dict()
        persisted = self.cache.load_view_state(self.root).to_dict()
        with patch.object(self.cache, "save_view_state", wraps=self.cache.save_view_state) as save:
            self.assertTrue(self.controller.valid_identity(layers["id"], ViewKind.ARCHITECTURE, root))
            valid_elsewhere = self.controller.valid_identity(
                layers["id"], ViewKind.ARCHITECTURE, self.model_root("Pair"))
        save.assert_not_called()
        self.assertEqual(self.controller.view_state.to_dict(), before)
        self.assertEqual(self.cache.load_view_state(self.root).to_dict(), persisted)
        self.assertIs(valid_elsewhere, False, "an open island must not bypass the requested root")

    def test_move_open_island_projection_under_another_root_rejects_without_persisting(self):
        self.analyze()
        root = self.model_root()
        encoder = self.node(self.view(1, root), "model.Classifier.encoder")
        detail = self.data(self.bridge.get_detail_view("Architecture", root, encoder["id"]))
        layers = self.node(detail, "model.Encoder.layers")
        self.save_selection(root, root)
        self.data(self.bridge.open_detail_island("Architecture", encoder["id"], [20, 30]))
        self.save_selection(self.model_root("Pair"), None)
        before = self.controller.view_state.to_dict()
        persisted = self.cache.load_view_state(self.root).to_dict()
        raised = None
        with patch.object(self.cache, "save_view_state", wraps=self.cache.save_view_state) as save:
            try:
                self.controller.move_node(layers["id"], (321, 654), ViewKind.ARCHITECTURE)
            except ValueError as error:
                raised = error
        with self.subTest(contract="reject cross-root movement"):
            self.assertIsInstance(raised, ValueError)
        with self.subTest(contract="state unchanged"):
            self.assertEqual(self.controller.view_state.to_dict(), before)
        with self.subTest(contract="no persistence"):
            save.assert_not_called()
            self.assertEqual(self.cache.load_view_state(self.root).to_dict(), persisted)

    def test_same_symbols_in_different_projects_reject_each_others_projected_ids(self):
        self.require_depth_contract()
        self.require_depth_contract("get_inspector")
        self.analyze()
        root = self.model_root()
        first = self.node(self.view(2, root), "model.Encoder.layers")
        other_root = self.base / "other-project"
        other_root.mkdir()
        for name, source in {"model.py": MODEL_SOURCE, "run.py": RUN_SOURCE}.items():
            (other_root / name).write_text(source, encoding="utf-8")
        other = DesktopBridge(cache_store=self.cache)
        self.data(other.open_project(str(other_root)))
        self.data(other.analyze())
        other_model = next(definition.symbol_id for definition in other._controller.analysis.model.definitions
                           if definition.qualified_name == "model.Classifier")
        # Same filenames and symbols deliberately collide; project context must
        # distinguish only the view occurrence identity.
        self.assertEqual(root, other_model)
        second = self.node(self.data(other.get_view("Architecture", root, None, 1, "both", 2)),
                           "model.Encoder.layers")
        self.assertEqual(first["semanticComponentIds"], second["semanticComponentIds"])
        self.assertEqual(first["codeNodeIds"], second["codeNodeIds"])
        self.assertEqual(first["sourceAnchors"], second["sourceAnchors"])
        self.assertNotEqual(first["id"], second["id"])
        self.save_selection(root, None)
        self.data(other.save_layout({
            "activeView": "Architecture", "selectedId": None,
            "openTabs": ["Architecture"], "graphRoots": {"Architecture": root},
        }))
        for bridge, local, foreign in ((self.bridge, first, second), (other, second, first)):
            with self.subTest(project=str(bridge._controller.project_root)):
                self.data(bridge.get_inspector("Architecture", root, local["id"], None, 2))
                before = self.data(bridge.get_view_state())
                self.invalid(bridge.get_view("Architecture", root, foreign["id"], 1, "both", 2))
                self.invalid(bridge.get_inspector("Architecture", root, foreign["id"], None, 2))
                self.invalid(bridge.move_node(foreign["id"], [5, 6], "Architecture"))
                self.invalid(bridge.expand(foreign["id"]))
                self.invalid(bridge.collapse(foreign["id"]))
                self.invalid(bridge.open_detail_island("Architecture", foreign["id"]))
                self.invalid(bridge.save_layout({
                    "activeView": "Architecture", "selectedId": foreign["id"],
                    "openTabs": ["Architecture"], "graphRoots": {"Architecture": root},
                }))
                self.assertEqual(self.data(bridge.get_view_state()), before)

    def check_structure_budget(self, max_nodes, max_edges):
        analysis = self.analyze()
        for kind, root in ((ViewKind.ARCHITECTURE, self.model_root()),
                           (ViewKind.ARCHITECTURE, None),
                           (ViewKind.PROJECT_STRUCTURE, None)):
            with self.subTest(kind=kind.value, root=root, nodes=max_nodes, edges=max_edges):
                common = dict(root_id=root, structure_depth=5,
                              project_namespace=self.controller.structure_namespace())
                full = build_visible_graph(analysis, ViewRequest(kind, **common))
                self.assertFalse(full.query_context["budget_truncated"])
                self.assertGreater(len(full.nodes), max_nodes if max_nodes < 100 else 3)
                self.assertGreater(len(full.edges), max_edges if max_edges < 300 else 1)
                request = ViewRequest(kind, max_nodes=max_nodes, max_edges=max_edges, **common)
                bounded = build_visible_graph(analysis, request)
                self.assertLessEqual(len(bounded.nodes), max_nodes)
                self.assertLessEqual(len(bounded.edges), max_edges)
                if max_nodes < 100:
                    self.assertEqual(len(bounded.nodes), max_nodes)
                    self.assertLess(len(bounded.nodes), len(full.nodes))
                if max_edges < 300:
                    self.assertEqual(len(bounded.edges), max_edges)
                    self.assertLess(len(bounded.edges), len(full.edges))
                self.assertEqual(bounded.max_nodes, max_nodes)
                self.assertEqual(bounded.max_edges, max_edges)
                self.assertIs(bounded.query_context["budget_truncated"], True)
                self.assertIs(bounded.query_context["has_more"], True)
                visible = {node.id for node in bounded.nodes}
                self.assertLessEqual(set(bounded.root_ids), visible)
                self.assertLessEqual(set(bounded.layout_hints), visible)
                for edge in bounded.edges:
                    self.assertIn(edge.source, visible)
                    self.assertIn(edge.target, visible)
                self.assertEqual(bounded, build_visible_graph(analysis, request))

    def test_structure_node_budget_limits_actual_nodes_and_reports_truncation(self):
        self.check_structure_budget(max_nodes=3, max_edges=300)

    def test_structure_edge_budget_limits_actual_edges_and_reports_truncation(self):
        self.check_structure_budget(max_nodes=100, max_edges=1)

    def test_switch_root_then_reopen_retains_other_root_projected_position_and_island(self):
        self.analyze()
        classifier = self.model_root()
        projected = self.node(self.view(2, classifier), "model.Encoder.layers")
        self.save_selection(classifier, None)
        self.data(self.bridge.move_node(projected["id"], [246, 135], "Architecture"))
        opened = self.data(self.bridge.open_detail_island(
            "Architecture", projected["id"], [40, 60]))
        self.assertEqual(len(opened["detailIslands"]), 1)
        pair = self.model_root("Pair")
        self.save_selection(pair, pair)
        pair_graph = self.view(1, pair)
        self.assertNotIn(projected["id"], self.ids(pair_graph))
        restarted = DesktopBridge(cache_store=self.cache)
        self.data(restarted.open_project(str(self.root)))
        restored = self.data(restarted.get_view_state())
        self.assertEqual(restored["graphRoots"]["Architecture"], pair)
        self.assertEqual(restored["positionsByView"]["Architecture"].get(projected["id"]),
                         [246, 135])
        self.assertEqual(restored["detailIslands"], opened["detailIslands"])
        self.assertNotIn(projected["id"], self.ids(self.data(restarted.get_view("Architecture", pair))))
        again = self.data(restarted.get_view("Architecture", classifier, None, 1, "both", 2))
        self.assertEqual(again["layoutHints"][projected["id"]], [246, 135])
        detail = self.data(restarted.get_detail_view("Architecture", classifier, projected["id"]))
        self.assertIn("model.Encoder.layers[*]", self.names(detail))

    def test_live_deep_selection_survives_state_snapshots_then_reopen_clears_only_selection(self):
        self.require_depth_contract()
        analysis = self.analyze()
        root = self.model_root()
        shallow = self.view(1, root)
        deep = self.view(4, root)
        projection = self.node(deep, "model.Block.projection")
        self.assertNotIn(projection["id"], self.ids(shallow))
        selected = self.save_selection(root, projection["id"])
        self.assertEqual(selected["selectedId"], projection["id"])
        moved = self.data(self.bridge.move_node(projection["id"], [432, 765], "Architecture"))
        self.assertEqual(moved["selectedId"], projection["id"])
        analysis_before = self.cache.analysis_path(self.root).read_bytes()
        for read in range(3):
            with self.subTest(snapshot=read):
                state = self.data(self.bridge.get_view_state())
                self.assertEqual(state["selectedId"], projection["id"])
                self.assertEqual(state["positionsByView"]["Architecture"][projection["id"]],
                                 [432, 765])
                self.assertEqual(self.cache.load_view_state(self.root).selected_id, projection["id"])
                self.assertIs(self.controller.analysis, analysis)
        restarted = DesktopBridge(cache_store=self.cache)
        with patch("codeviz.ui.controller.analyze_project",
                   side_effect=AssertionError("reopen must use cached analysis")) as analyze:
            opened = self.data(restarted.open_project(str(self.root)))
        analyze.assert_not_called()
        self.assertIs(opened["hasAnalysis"], True)
        self.assertIsNone(opened["cacheIssue"])
        restored = self.data(restarted.get_view_state())
        self.assertIsNone(restored["selectedId"])
        self.assertEqual(restored["positionsByView"]["Architecture"][projection["id"]], [432, 765])
        default = self.data(restarted.get_view("Architecture", root))
        self.assertEqual(default["queryContext"]["structureDepth"], 1)
        self.assertEqual(self.ids(default), self.ids(shallow))
        self.assertNotIn(projection["id"], self.ids(default))
        expanded = self.data(restarted.get_view("Architecture", root, None, 1, "both", 4))
        self.assertEqual(self.node(expanded, "model.Block.projection")["id"], projection["id"])
        self.assertEqual(expanded["layoutHints"][projection["id"]], [432, 765])
        self.assertIsNone(self.data(restarted.get_view_state())["selectedId"])
        self.assertEqual(self.cache.analysis_path(self.root).read_bytes(), analysis_before)
        persisted = self.cache.load_view_state(self.root).to_dict()
        self.assertNotIn("structure_depth", persisted)
        self.assertNotIn("structureDepth", persisted)

    def test_definition_detail_selection_requires_a_valid_active_island_context(self):
        analysis = self.analyze()
        root = self.model_root()
        encoder = self.node(self.view(1, root), "model.Classifier.encoder")
        detail = self.data(self.bridge.get_detail_view("Architecture", root, encoder["id"]))
        layers = self.node(detail, "model.Encoder.layers")
        raw_layers = next(item.id for item in analysis.model.instances
                          if item.qualified_name == "model.Encoder.layers")
        self.assertNotEqual(layers["id"], raw_layers)
        self.assertEqual(layers["semanticComponentIds"], [raw_layers])
        self.assertTrue(layers["id"].startswith("projection:"))
        self.assertEqual(layers["id"], self.node(self.view(2, root), "model.Encoder.layers")["id"])
        self.assertEqual(detail["rootIds"], [encoder["id"]])
        self.assertNotIn(layers["id"], self.ids(self.view(1, root)))
        self.save_selection(root, None)
        payload = {"activeView": "Architecture", "selectedId": layers["id"],
                   "openTabs": ["Architecture"], "graphRoots": {"Architecture": root}}
        self.invalid(self.bridge.save_layout(payload))
        opened = self.data(self.bridge.open_detail_island("Architecture", encoder["id"], [10, 20]))
        island = opened["detailIslands"][0]
        inspected = self.data(self.bridge.get_inspector(
            "Architecture", root, layers["id"], encoder["id"]))
        self.assertEqual(inspected["id"], layers["id"])
        selected = self.data(self.bridge.save_layout(payload))
        self.assertEqual(selected["selectedId"], layers["id"])
        self.assertEqual(self.data(self.bridge.get_view_state())["selectedId"], layers["id"])
        self.invalid(self.bridge.save_layout({**payload, "selectedId": raw_layers}))
        # A real definition member outside the rendered island remains invalid.
        foreign = next(item.id for item in analysis.model.instances
                       if item.qualified_name == "model.Block.projection")
        self.assertNotIn(foreign, self.ids(detail))
        self.invalid(self.bridge.save_layout({**payload, "selectedId": foreign}))
        self.invalid(self.bridge.save_layout({**payload, "selectedId": layers["id"] + ":forged"}))
        self.invalid(self.bridge.save_layout({**payload, "activeView": "ProjectStructure"}))
        self.data(self.bridge.close_detail_island(island["id"]))
        self.invalid(self.bridge.save_layout(payload))

    def test_rendered_default_budget_aggregate_supports_inspector_move_and_layout_only_if_real(self):
        analysis = self.analyze()
        root = self.model_root()
        original = analysis.semantic.graph
        definition = next(component for component in original.components if component.id == root)
        layer = next(component for component in original.components
                     if component.code_name == "model.Classifier.embedding")
        # A wide static semantic fixture shares genuine definition/anchor
        # provenance and creates no runtime instances or executed target code.
        children = tuple(replace(layer, id=f"wide-child-{index}", display_name=f"layer_{index}",
                                 code_name=f"model.Classifier.layer_{index}", code_symbol_ids=(root,))
                         for index in range(120))
        containment = next(relation for relation in original.relations
                           if relation.source == root and relation.target == layer.id
                           and relation.type.value == "CONTAINS")
        wide_graph = replace(original,
                             components=(replace(definition, child_component_ids=tuple(
                                 child.id for child in children)), *children),
                             relations=tuple(replace(containment, target=child.id)
                                             for child in children), roots=(root,))
        self.controller.analysis = replace(analysis, semantic=replace(analysis.semantic, graph=wide_graph))
        self.save_selection(root, None)
        graph = self.view(1, root)
        self.assertEqual(graph["maxNodes"], 100)
        self.assertEqual(len(graph["nodes"]), 100)
        self.assertIs(graph["queryContext"]["budgetTruncated"], True)
        self.assertEqual(len(graph["aggregations"]), 1)
        aggregate = graph["aggregations"][0]
        visible = next(node for node in graph["nodes"] if node["id"] == aggregate["id"])
        self.assertEqual(visible["memberIds"], aggregate["memberIds"])
        self.assertEqual(set(aggregate["memberIds"]),
                         {child.id for child in children} - self.ids(graph))
        inspected = self.data(self.bridge.get_inspector("Architecture", root, aggregate["id"]))
        self.assertEqual(inspected["id"], aggregate["id"])
        self.assertEqual(set(inspected["memberIds"]), set(aggregate["memberIds"]))
        moved = self.data(self.bridge.move_node(aggregate["id"], [321, 456], "Architecture"))
        self.assertEqual(moved["positionsByView"]["Architecture"][aggregate["id"]], [321, 456])
        saved = self.save_selection(root, aggregate["id"])
        self.assertEqual(saved["selectedId"], aggregate["id"])
        self.assertEqual(self.data(self.bridge.get_view_state())["selectedId"], aggregate["id"])
        self.assertEqual(self.view(1, root)["layoutHints"][aggregate["id"]], [321, 456])
        before = self.data(self.bridge.get_view_state())
        for forged in (aggregate["id"] + ":forged", "aggregate:Architecture:missing"):
            with self.subTest(forged=forged):
                self.invalid(self.bridge.get_inspector("Architecture", root, forged))
                self.invalid(self.bridge.move_node(forged, [0, 0], "Architecture"))
                self.invalid(self.bridge.save_layout({
                    "activeView": "Architecture", "selectedId": forged,
                    "openTabs": ["Architecture"], "graphRoots": {"Architecture": root},
                }))
                self.assertEqual(self.data(self.bridge.get_view_state()), before)

    def test_architecture_critical_data_node_inspector_move_save_and_reopen_position(self):
        analysis = self.analyze({"model.py": DATA_MODEL_SOURCE})
        root = self.model_root("Net")
        visible = self.view(1, root)
        prediction = self.node(visible, "model.Net.forward.prediction")
        self.assertEqual(prediction["role"], "DataNode")
        self.assertIn(prediction["id"], analysis.flow.graph.nodes)
        self.assertEqual(analysis.flow.graph.nodes[prediction["id"]].kind, NodeKind.DATA)
        self.assertFalse(analysis.flow.graph.nodes[prediction["id"]].metadata.get("generic_data"))
        self.assertEqual(prediction["codeNodeIds"], [prediction["id"]])
        self.assertTrue(prediction["sourceAnchors"])
        anchor = prediction["sourceAnchors"][0]
        self.assertEqual(anchor["filePath"], "model.py")
        source = self.data(self.bridge.read_source(anchor))
        self.assertIn("prediction = features", source["text"])
        inspected = self.data(self.bridge.get_inspector("Architecture", root, prediction["id"]))
        self.assertEqual(inspected["id"], prediction["id"])
        self.assertIn(anchor, inspected["sourceAnchors"])
        self.save_selection(root, None)
        position = [321.5, -246.25]
        moved = self.data(self.bridge.move_node(prediction["id"], position, "Architecture"))
        self.assertEqual(moved["positionsByView"]["Architecture"][prediction["id"]], position)
        saved = self.save_selection(root, prediction["id"])
        self.assertEqual(saved["selectedId"], prediction["id"])
        for snapshot in range(3):
            with self.subTest(snapshot=snapshot):
                state = self.data(self.bridge.get_view_state())
                self.assertEqual(state["selectedId"], prediction["id"])
                self.assertEqual(state["positionsByView"]["Architecture"][prediction["id"]], position)
        self.assertEqual(self.view(1, root)["layoutHints"][prediction["id"]], position)
        analysis_before = self.cache.analysis_path(self.root).read_bytes()
        restarted = DesktopBridge(cache_store=self.cache)
        with patch("codeviz.ui.controller.analyze_project",
                   side_effect=AssertionError("reopen must use static cached analysis")) as analyze:
            opened = self.data(restarted.open_project(str(self.root)))
        analyze.assert_not_called()
        self.assertIs(opened["hasAnalysis"], True)
        self.assertIsNone(opened["cacheIssue"])
        restored = self.data(restarted.get_view_state())
        self.assertEqual(restored["positionsByView"]["Architecture"][prediction["id"]], position)
        reopened = self.data(restarted.get_view("Architecture", root))
        self.assertIn(prediction["id"], self.ids(reopened))
        self.assertEqual(reopened["layoutHints"][prediction["id"]], position)
        self.assertEqual(self.data(restarted.get_inspector(
            "Architecture", root, prediction["id"]))["id"], prediction["id"])
        self.assertEqual(self.cache.analysis_path(self.root).read_bytes(), analysis_before)

    def test_architecture_data_identity_requires_actual_root_critical_slice_membership(self):
        analysis = self.analyze({"model.py": DATA_MODEL_SOURCE})
        root = self.model_root("Net")
        other_root = self.model_root("OtherNet")
        prediction = self.node(self.view(1, root), "model.Net.forward.prediction")
        other_prediction = self.node(self.view(1, other_root), "model.OtherNet.forward.prediction")
        self.assertNotEqual(prediction["id"], other_prediction["id"])
        self.assertNotIn(other_prediction["id"], self.ids(self.view(1, root)))
        self.assertNotIn(prediction["id"], self.ids(self.view(1, other_root)))
        generic = next(node for node in analysis.flow.graph.nodes.values()
                       if node.kind is NodeKind.DATA
                       and node.qualified_name == "model.Net.forward.scratch")
        self.assertTrue(generic.metadata.get("generic_data"))
        self.assertNotIn(generic.id, self.ids(self.view(1, root)))
        self.save_selection(root, None)
        before = self.data(self.bridge.get_view_state())
        for invalid_id in (other_prediction["id"], generic.id, prediction["id"] + ":forged"):
            with self.subTest(invalid_id=invalid_id):
                self.invalid(self.bridge.get_view("Architecture", root, invalid_id))
                self.invalid(self.bridge.get_inspector("Architecture", root, invalid_id))
                self.invalid(self.bridge.move_node(invalid_id, [1, 2], "Architecture"))
                self.invalid(self.bridge.save_layout({
                    "activeView": "Architecture", "selectedId": invalid_id,
                    "openTabs": ["Architecture"], "graphRoots": {"Architecture": root},
                }))
                self.assertEqual(self.data(self.bridge.get_view_state()), before)
        self.invalid(self.bridge.get_inspector("Architecture", other_root, prediction["id"]))
        self.invalid(self.bridge.save_layout({
            "activeView": "Architecture", "selectedId": prediction["id"],
            "openTabs": ["Architecture"], "graphRoots": {"Architecture": other_root},
        }))
        self.assertEqual(self.data(self.bridge.get_view_state()), before)

    def test_definition_detail_data_selection_requires_an_open_source_valid_island(self):
        source = """import torch.nn as nn

class Child(nn.Module):
    def forward(self, features):
        prediction = features
        return prediction

class Parent(nn.Module):
    def __init__(self):
        super().__init__()
        self.child = Child()
    def forward(self, features):
        return self.child(features)

class Other(nn.Module):
    def forward(self, features):
        prediction = features
        return prediction
"""
        analysis = self.analyze({"model.py": source})
        root = self.model_root("Parent")
        other_root = self.model_root("Other")
        main = self.view(1, root)
        child = self.node(main, "model.Parent.child")
        detail = self.data(self.bridge.get_detail_view("Architecture", root, child["id"]))
        prediction = self.node(detail, "model.Child.forward.prediction")
        self.assertEqual(prediction["role"], "DataNode")
        self.assertNotIn(prediction["id"], self.ids(main))
        self.assertNotIn(prediction["id"], self.ids(self.view(1, other_root)))
        self.assertEqual(analysis.flow.graph.nodes[prediction["id"]].kind, NodeKind.DATA)
        self.assertFalse(analysis.flow.graph.nodes[prediction["id"]].metadata.get("generic_data"))
        self.assertLessEqual(len(detail["nodes"]), 18)
        self.assertLessEqual(len(detail["edges"]), 40)
        anchor = prediction["sourceAnchors"][0]
        self.assertEqual(anchor["filePath"], "model.py")
        self.assertIn("prediction = features", self.data(self.bridge.read_source(anchor))["text"])
        self.save_selection(root, None)
        payload = {"activeView": "Architecture", "selectedId": prediction["id"],
                   "openTabs": ["Architecture"], "graphRoots": {"Architecture": root}}
        self.invalid(self.bridge.save_layout(payload))
        opened = self.data(self.bridge.open_detail_island("Architecture", child["id"], [10, 20]))
        self.assertEqual(len(opened["detailIslands"]), 1)
        island = opened["detailIslands"][0]
        self.assertEqual(island["sourceNodeId"], child["id"])
        inspected = self.data(self.bridge.get_inspector(
            "Architecture", root, prediction["id"], child["id"]))
        self.assertEqual(inspected["id"], prediction["id"])
        self.assertIn(anchor, inspected["sourceAnchors"])
        saved = self.data(self.bridge.save_layout(payload))
        self.assertEqual(saved["selectedId"], prediction["id"])
        state = self.data(self.bridge.get_view_state())
        self.assertEqual(state["selectedId"], prediction["id"])
        self.assertEqual(state["detailIslands"], opened["detailIslands"])
        self.invalid(self.bridge.save_layout({**payload, "graphRoots": {"Architecture": other_root}}))
        self.invalid(self.bridge.get_inspector("Architecture", other_root, prediction["id"]))
        self.assertEqual(self.data(self.bridge.get_view_state()), state)
        self.data(self.bridge.close_detail_island(island["id"]))
        closed = self.data(self.bridge.get_view_state())
        self.assertEqual(closed["detailIslands"], [])
        self.invalid(self.bridge.save_layout(payload))
        self.assertEqual(self.data(self.bridge.get_view_state()), closed)

    def test_control_modes_match_supported_views_and_gradient_remains_unsupported(self):
        self.require_depth_contract()
        analysis = self.analyze()
        function = next(entry for entry in analysis.symbol_index.entries.values()
                        if entry.qualified_name == "model.Classifier.forward")
        data_node = next(node for node in analysis.flow.graph.nodes.values()
                         if node.kind is NodeKind.DATA)
        execution = self.data(self.bridge.get_view("Execution"))
        self.assertTrue(execution["nodes"])
        cases = (
            ("Architecture", self.model_root(), "structure"),
            ("ProjectStructure", None, "structure"),
            ("FunctionFlow", function.id, "none"),
            ("Execution", execution["rootIds"][0], "none"),
            ("Symbol", function.id, "relations"),
            ("DataFlow", data_node.id, "relations"),
            ("Calls", function.id, "relations"),
        )
        for kind, root, mode in cases:
            with self.subTest(kind=kind):
                graph = self.view(1, root, kind=kind)
                self.assertEqual(graph["queryContext"].get("controlMode"), mode)
                if mode == "none":
                    self.assertEqual(self.ids(graph), self.ids(self.view(5, root, kind=kind)))
                    self.assertIs(graph["queryContext"].get("canExpand"), False)
        self.invalid(self.bridge.get_view("Gradient", function.id))
