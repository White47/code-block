"""nanoGPTB detail contract; owned source is analyzed, never imported/executed."""

import ast
import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from codeviz.core import (
    CodeEdge,
    CodeGraph,
    CodeNode,
    Confidence,
    DataNode,
    DataRole,
    EdgeType,
    NodeKind,
    ViewKind,
)
from codeviz.persistence.codec import encode_analysis
from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.web_bridge import DesktopBridge
from codeviz.views.data import critical_data_slice

MODEL_SOURCE = """import torch.nn as nn

class Block(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.proj = nn.Linear(config.n_embd, config.n_embd)
        self.norm = nn.LayerNorm(config.n_embd)
    def forward(self, hidden):
        return self.proj(self.norm(hidden))

class GPT(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.transformer = nn.ModuleDict(dict(
            wte=nn.Embedding(config.vocab_size, config.n_embd),
            h=nn.ModuleList([Block(config) for _ in range(config.n_layer)]),
            ln_f=nn.LayerNorm(config.n_embd),
        ))
        self.lm_head = nn.Linear(config.n_embd, config.vocab_size)
    def forward(self, tokens):
        hidden = self.transformer.wte(tokens)
        for block in self.transformer.h:
            hidden = block(hidden)
        hidden = self.transformer.ln_f(hidden)
        logits = self.lm_head(hidden)
        return logits

class Pair(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.left = GPT(config)
        self.right = GPT(config)
    def forward(self, tokens):
        return self.left(tokens) + self.right(tokens)

class Recursive(nn.Module):
    def __init__(self):
        super().__init__()
        self.child = Recursive()
        self.proj = nn.Linear(8, 8)
    def forward(self, hidden):
        return self.child(self.proj(hidden))

class DirectList(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.layers = nn.ModuleList([Block(config)])
    def forward(self, hidden):
        return self.layers[0](hidden)

class EmptyContainers(nn.Module):
    def __init__(self):
        super().__init__()
        self.layers = nn.ModuleList([])
        self.parts = nn.ModuleDict(dict())
    def forward(self, hidden):
        return hidden

class DynamicContainers(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.layers = nn.ModuleList(config.layers)
        self.parts = nn.ModuleDict(config.parts)
    def forward(self, hidden):
        return hidden

class SequentialOnly(nn.Module):
    def __init__(self):
        super().__init__()
        self.layers = nn.Sequential(nn.Linear(8, 8), nn.LayerNorm(8))
    def forward(self, hidden):
        return self.layers(hidden)

# An execution trap: static analysis must not run target top-level code.
raise RuntimeError("synthetic architecture fixture must never execute")
"""


class ArchitectureDetailTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "project"
        self.root.mkdir()
        (self.root / "model.py").write_text(MODEL_SOURCE, encoding="utf-8")
        self.cache = CacheStore(self.base / "cache")
        self.controller = AppController(self.cache)
        self.bridge = DesktopBridge(
            self.controller, self.cache, PreferencesStore(self.base / "preferences"))
        self.data(self.bridge.open_project(str(self.root)))
        session = self.data(self.bridge.analyze())
        self.assertIn(session["status"], ("Ready", "Partial"))
        self.analysis = self.controller.analysis
        self.gpt = self.model_root("GPT")

    def data(self, response):
        self.assertIs(response["ok"], True, response)
        json.dumps(response, allow_nan=False)
        return response["data"]

    def invalid(self, response):
        self.assertIs(response["ok"], False, response)
        self.assertEqual(response["error"]["code"], "INVALID_REQUEST", response)

    def model_root(self, name):
        matches = [definition.symbol_id for definition in self.analysis.model.definitions
                   if definition.qualified_name == f"model.{name}"]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def main(self, depth=1, root=None):
        return self.data(self.bridge.get_view(
            "Architecture", root or self.gpt, None, 1, "both", depth))

    def detail(self, source, root=None):
        return self.data(self.bridge.get_detail_view(
            "Architecture", root or self.gpt, source))

    def node(self, graph, name):
        matches = [node for node in graph["nodes"] if node["codeName"] == name]
        self.assertEqual(len(matches), 1, (name, graph["nodes"]))
        return matches[0]

    def ids(self, graph):
        return {node["id"] for node in graph["nodes"]}

    def names(self, graph):
        return {node["codeName"] for node in graph["nodes"]}

    def save_selection(self, selected, root=None):
        self.data(self.bridge.save_layout({
            "activeView": "Architecture", "selectedId": selected,
            "openTabs": ["Architecture"],
            "graphRoots": {"Architecture": root or self.gpt},
        }))

    def assert_closed_graph(self, graph):
        ids = self.ids(graph)
        self.assertEqual(len(ids), len(graph["nodes"]))
        self.assertLessEqual(len(graph["nodes"]), 18)
        self.assertLessEqual(len(graph["edges"]), 40)
        self.assertTrue(set(graph["rootIds"]) <= ids)
        self.assertTrue(all(edge["source"] in ids and edge["target"] in ids
                            for edge in graph["edges"]))
        components = {item.id: item for item in self.analysis.semantic.graph.components}
        known_code = set(self.analysis.symbol_index.entries) | set(self.analysis.flow.graph.nodes)
        for node in graph["nodes"]:
            if node["id"].startswith("projection:"):
                self.assertLessEqual(len(node["id"].split(":", 2)[2].split("/")), 6)
            if node["memberIds"] or node["role"] == "DataNode":
                continue
            self.assertTrue(node["semanticComponentIds"], node)
            self.assertTrue(set(node["semanticComponentIds"]) <= set(components), node)
            self.assertTrue(node["codeNodeIds"], node)
            self.assertTrue(set(node["codeNodeIds"]) <= known_code, node)
            self.assertTrue(node["sourceAnchors"], node)
            real_anchors = {anchor for semantic_id in node["semanticComponentIds"]
                            for anchor in components[semantic_id].source_anchors}
            for code_id in node["codeNodeIds"]:
                symbol = self.analysis.symbol_index.entries.get(code_id)
                if symbol is not None:
                    real_anchors.add(symbol.source_anchor)
                flow_node = self.analysis.flow.graph.nodes.get(code_id)
                if flow_node is not None:
                    real_anchors.update(flow_node.source_anchors)
            for anchor in node["sourceAnchors"]:
                self.assertTrue(any(
                    anchor == {"filePath": real.file_path, "startLine": real.start_line,
                               "startColumn": real.start_column, "endLine": real.end_line,
                               "endColumn": real.end_column}
                    for real in real_anchors), node)
        distances = {root: 0 for root in graph["rootIds"]}
        for _ in range(3):
            for edge in graph["edges"]:
                if edge["type"] == "CONTAINS" and edge["source"] in distances:
                    distance = distances[edge["source"]] + 1
                    if distance <= 3:
                        distances.setdefault(edge["target"], distance)
        for node in graph["nodes"]:
            if node["role"] != "DataNode" and not node["memberIds"]:
                self.assertIn(node["id"], distances, "structure must be within relative depth three")

    def test_root_gpt_detail_adds_real_template_beyond_initial_depth_two(self):
        main = self.main(2)
        detail = self.detail(self.gpt)
        self.assertEqual(detail["rootIds"], [self.gpt])
        template = self.node(detail, "model.GPT.transformer[h][*]")
        self.assertNotIn(template["id"], self.ids(main),
                         "root detail must add a structural occurrence beyond initial depth two")
        self.assertEqual({node["codeName"] for node in detail["nodes"]
                          if node["role"] != "DataNode"}, {
            "model.GPT", "model.GPT.transformer", "model.GPT.lm_head",
            "model.GPT.transformer[wte]", "model.GPT.transformer[h]",
            "model.GPT.transformer[ln_f]", "model.GPT.transformer[h][*]",
        })
        self.assert_closed_graph(detail)

    def test_transformer_detail_contains_only_selected_subtree_without_root_data(self):
        source = self.node(self.main(), "model.GPT.transformer")
        detail = self.detail(source["id"])
        self.assertEqual(detail["rootIds"], [source["id"]])
        self.assertEqual(self.names(detail), {
            "model.GPT.transformer", "model.GPT.transformer[wte]",
            "model.GPT.transformer[h]", "model.GPT.transformer[ln_f]",
            "model.GPT.transformer[h][*]", "model.Block.proj", "model.Block.norm",
        }, "relative three must include Block internals without outer branches/data")
        self.assertFalse(any(node["role"] == "DataNode" for node in detail["nodes"]))
        self.assert_closed_graph(detail)

    def test_modulelist_detail_contains_real_template_and_no_sibling_or_root_data(self):
        layers = self.node(self.main(2), "model.GPT.transformer[h]")
        detail = self.detail(layers["id"])
        self.assertEqual(detail["rootIds"], [layers["id"]])
        self.assertEqual(self.names(detail), {
            "model.GPT.transformer[h]", "model.GPT.transformer[h][*]",
            "model.Block.proj", "model.Block.norm",
        }, "ModuleList detail must exclude siblings and root data")
        member = self.node(detail, "model.GPT.transformer[h][*]")
        self.assertIn((layers["id"], member["id"]), {
            (edge["source"], edge["target"]) for edge in detail["edges"]
            if edge["type"] == "CONTAINS"})
        self.assert_closed_graph(detail)

    def test_overview_models_hint_uses_only_direct_locally_known_list_dict_children(self):
        overview = self.data(self.bridge.get_overview())
        choices = {choice["name"]: choice for choice in overview["models"]}
        expected = {"GPT": 2, "DirectList": 2, "Block": 1, "Pair": 1,
                    "Recursive": 1, "EmptyContainers": 1,
                    "DynamicContainers": 1, "SequentialOnly": 1}
        self.assertEqual(set(choices), {f"model.{name}" for name in expected})
        for name, depth in expected.items():
            with self.subTest(model=name):
                choice = choices[f"model.{name}"]
                self.assertIn("initialStructureDepth", choice,
                              "new Overview models must emit the approved initial-depth hint")
                self.assertIs(type(choice["initialStructureDepth"]), int)
                self.assertIn(choice["initialStructureDepth"], (1, 2))
                self.assertEqual(choice["initialStructureDepth"], depth)
        # The hint is advisory: an explicit shrink remains authoritative.
        one = self.main(1)
        self.bridge.get_overview()
        self.assertEqual(self.ids(self.main(1)), self.ids(one))
        self.assertEqual(self.controller.current_structure_depth(ViewKind.ARCHITECTURE, self.gpt), 1)

    def test_block_occurrence_detail_keeps_source_id_global_path_and_children(self):
        main = self.main(4)
        block = self.node(main, "model.GPT.transformer[h][*]")
        detail = self.detail(block["id"])
        self.assertEqual(detail["rootIds"], [block["id"]])
        self.assertEqual(self.names(detail), {
            "model.GPT.transformer[h][*]", "model.Block.proj", "model.Block.norm",
        })
        for name in ("model.Block.proj", "model.Block.norm"):
            expected = self.node(main, name)
            actual = self.node(detail, name)
            self.assertEqual(actual["id"], expected["id"])
            self.assertEqual(actual["metadata"]["instanceContext"],
                             expected["metadata"]["instanceContext"])
            self.assertEqual(actual["sourceAnchors"], expected["sourceAnchors"])
        self.assert_closed_graph(detail)

    def test_detail_real_nodes_retain_semantic_code_ids_and_exact_ast_anchors(self):
        source = self.node(self.main(), "model.GPT.transformer")
        detail = self.detail(source["id"])
        components = {item.id: item for item in self.analysis.semantic.graph.components}
        known_code = set(self.analysis.symbol_index.entries) | set(self.analysis.flow.graph.nodes)
        expressions = {keyword.arg: keyword.value for item in ast.walk(ast.parse(MODEL_SOURCE))
                       if isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
                       and item.func.id == "dict" for keyword in item.keywords}
        for key in ("wte", "h", "ln_f"):
            with self.subTest(key=key):
                node = self.node(detail, f"model.GPT.transformer[{key}]")
                self.assertTrue(node["semanticComponentIds"])
                self.assertTrue(set(node["semanticComponentIds"]) <= set(components))
                self.assertTrue(node["codeNodeIds"])
                self.assertTrue(set(node["codeNodeIds"]) <= known_code)
                expression = expressions[key]
                anchor = {"filePath": "model.py", "startLine": expression.lineno,
                          "startColumn": expression.col_offset,
                          "endLine": expression.end_lineno,
                          "endColumn": expression.end_col_offset}
                self.assertIn(anchor, node["sourceAnchors"])
                inspected = self.data(self.bridge.get_inspector(
                    "Architecture", self.gpt, node["id"], source["id"]))
                self.assertEqual(inspected["id"], node["id"])
                self.assertIn(anchor, inspected["sourceAnchors"])
                document = self.data(self.bridge.read_source(anchor))
                self.assertEqual(document["startLine"], expression.lineno)
                self.assertIn(ast.unparse(expression.func), document["text"])

    def test_local_inspector_selection_does_not_replace_main_selection_or_depth(self):
        main = self.main()
        source = self.node(main, "model.GPT.transformer")
        child = self.node(self.main(2), "model.GPT.transformer[h]")
        self.main()
        self.save_selection(self.gpt)
        before = self.controller.view_state.to_dict()
        selected = self.data(self.bridge.get_inspector(
            "Architecture", self.gpt, child["id"], source["id"]))
        self.assertEqual(selected["id"], child["id"])
        self.assertEqual(self.controller.view_state.to_dict(), before)
        self.assertEqual(self.controller.current_structure_depth(ViewKind.ARCHITECTURE, self.gpt), 1)
        self.assertEqual(self.ids(self.main()), self.ids(main))

    def test_local_inspector_rejects_lm_head_outside_transformer_subtree(self):
        main = self.main()
        source = self.node(main, "model.GPT.transformer")
        head = self.node(main, "model.GPT.lm_head")
        self.invalid(self.bridge.get_inspector(
            "Architecture", self.gpt, head["id"], source["id"]))

    def test_local_inspector_rejects_root_data_outside_transformer_subtree(self):
        main = self.main()
        source = self.node(main, "model.GPT.transformer")
        logits = self.node(main, "model.GPT.forward.logits")
        self.assertEqual(logits["role"], "DataNode")
        self.invalid(self.bridge.get_inspector(
            "Architecture", self.gpt, logits["id"], source["id"]))

    def test_leaf_detail_remains_unavailable(self):
        leaf = self.node(self.main(2), "model.GPT.transformer[wte]")
        self.assertIs(leaf["metadata"]["canOpenDetail"], False)
        with self.assertRaisesRegex(ValueError, "node has no local detail graph"):
            self.controller.detail_view(ViewKind.ARCHITECTURE, self.gpt, leaf["id"])
        self.invalid(self.bridge.get_detail_view("Architecture", self.gpt, leaf["id"]))

    def test_forged_and_cross_root_detail_ids_are_rejected_without_state_changes(self):
        pair = self.model_root("Pair")
        # Pair has two legitimate occurrences of one definition; do not pick by name.
        candidates = [node for node in self.main(2, pair)["nodes"]
                      if node["codeName"] == "model.GPT.transformer"]
        self.assertEqual(len(candidates), 2)
        occurrence = candidates[0]["id"]
        self.assertTrue(occurrence.startswith("projection:"))
        before = self.controller.view_state.to_dict()
        for invalid_id in ("missing-detail-id", occurrence + "/forged",
                           occurrence.replace(self.controller.structure_namespace(), "foreign", 1)):
            with self.subTest(invalid_id=invalid_id):
                self.invalid(self.bridge.get_detail_view("Architecture", pair, invalid_id))
        self.invalid(self.bridge.get_detail_view("Architecture", self.gpt, occurrence))
        self.assertEqual(self.controller.view_state.to_dict(), before)

    def test_same_definition_occurrences_keep_distinct_global_detail_paths(self):
        pair = self.model_root("Pair")
        graph = self.main(3, pair)
        sources = [node for node in graph["nodes"]
                   if node["codeName"] == "model.GPT.transformer"]
        self.assertEqual(len(sources), 2)
        details = [self.detail(source["id"], pair) for source in sources]
        children = [self.node(detail, "model.GPT.transformer[h]") for detail in details]
        self.assertNotEqual(children[0]["id"], children[1]["id"])
        self.assertEqual(children[0]["sourceAnchors"], children[1]["sourceAnchors"])
        self.assertEqual(children[0]["semanticComponentIds"], children[1]["semanticComponentIds"])
        for source, detail, child in zip(sources, details, children, strict=True):
            self.assertEqual(detail["rootIds"], [source["id"]])
            self.assertIn(child["id"], self.ids(graph))
            self.assertNotIn("model.GPT.lm_head", self.names(detail))
            self.assertFalse(any(node["role"] == "DataNode" for node in detail["nodes"]))

    def test_unknown_config_n_layer_does_not_invent_twelve_blocks(self):
        layers = next(item for item in self.analysis.model.instances
                      if item.qualified_name == "model.GPT.transformer[h]")
        self.assertIsNone(layers.repeat_count)
        detail = self.detail(layers.id)
        templates = [node for node in detail["nodes"]
                     if node["codeName"] == "model.GPT.transformer[h][*]"]
        self.assertEqual(len(templates), 1)
        container = self.node(detail, layers.qualified_name)
        self.assertIsNone(container["metadata"].get("repeatCount"))
        self.assertFalse(any(node["metadata"].get("repeatCount") == 12
                             or "×12" in node["displayName"] for node in detail["nodes"]))

    def test_recursive_detail_is_bounded_deterministic_and_preserves_source_occurrence(self):
        root = self.model_root("Recursive")
        graph = self.main(5, root)
        sources = [node for node in graph["nodes"]
                   if node["codeName"] == "model.Recursive.child"]
        self.assertEqual({source["metadata"]["structureLevel"] for source in sources},
                         {1, 2, 3, 4, 5})
        for source in sources:
            with self.subTest(source=source["id"]):
                level = source["metadata"]["structureLevel"]
                if source["id"].startswith("projection:"):
                    self.assertEqual(len(source["id"].split(":", 2)[2].split("/")), level + 1)
                if level == 5:
                    self.assertIs(source["metadata"]["canOpenDetail"], False)
                    before = self.controller.view_state.to_dict()
                    with self.assertRaisesRegex(ValueError, r"^node has no local detail graph$"):
                        self.controller.detail_view(ViewKind.ARCHITECTURE, root, source["id"])
                    self.invalid(self.bridge.get_detail_view("Architecture", root, source["id"]))
                    self.assertEqual(self.controller.view_state.to_dict(), before)
                    continue
                self.assertIs(source["metadata"]["canOpenDetail"], True)
                first = self.detail(source["id"], root)
                self.assertEqual(first["rootIds"], [source["id"]])
                self.assertEqual(first, self.detail(source["id"], root))
                self.assert_closed_graph(first)
                for node in first["nodes"]:
                    if node["id"].startswith("projection:"):
                        self.assertLessEqual(len(node["id"].split(":", 2)[2].split("/")), 6)

    def test_recursive_detail_at_level_four_reports_global_depth_limit_not_expandable(self):
        root = self.model_root("Recursive")
        source = next(node for node in self.main(5, root)["nodes"]
                      if node["codeName"] == "model.Recursive.child"
                      and node["metadata"]["structureLevel"] == 4)
        detail = self.detail(source["id"], root)
        self.assertEqual(detail["rootIds"], [source["id"]])
        self.assertTrue(any(node["metadata"]["structureLevel"] == 5
                            for node in detail["nodes"]))
        self.assertIs(detail["queryContext"]["canExpand"], False)
        self.assertIs(detail["queryContext"]["depthLimit"], True)
        self.assert_closed_graph(detail)

    def test_root_relative_depth_three_remains_expandable_below_global_limit(self):
        detail = self.detail(self.gpt)
        template = self.node(detail, "model.GPT.transformer[h][*]")
        self.assertEqual(template["metadata"]["structureLevel"], 3)
        self.assertIs(template["metadata"]["hasChildren"], True)
        self.assertNotIn("model.Block.proj", self.names(detail))
        self.assertIs(detail["queryContext"]["canExpand"], True)
        self.assertIs(detail["queryContext"]["depthLimit"], False)
        self.assert_closed_graph(detail)

    def test_unique_data_edges_report_budget_truncation_without_mutating_analysis(self):
        probe_source = """import torch.nn as nn
class DataProbe(nn.Module):
    def forward(self, value):
        a0 = value
        a1 = value
        a2 = value
        a3 = value
        a4 = value
        a5 = value
        a6 = value
        a7 = value
        return a7
raise RuntimeError("data budget fixture must never execute")
"""
        (self.root / "probe.py").write_text(probe_source, encoding="utf-8")
        original = self.controller.analyze()
        original_facts = encode_analysis(original)
        cache_bytes = self.cache.analysis_path(self.root).read_bytes()
        state = self.controller.view_state.to_dict()
        definition = next(item for item in original.model.definitions
                          if item.qualified_name == "probe.DataProbe")
        semantic_root = next(item for item in original.semantic.graph.components
                             if item.id == definition.symbol_id)
        self.assertIn(definition.symbol_id, semantic_root.code_symbol_ids)
        entries = [next(entry for entry in original.symbol_index.entries.values()
                        if entry.qualified_name == f"probe.DataProbe.forward.a{index}")
                   for index in range(8)]
        graph = CodeGraph()
        for node in original.flow.graph.nodes.values():
            # Original generic/critical facts stay on the original analysis. The
            # owned in-memory projection isolates exactly eight eligible nodes.
            graph.add_node(replace(node, metadata={**node.metadata, "generic_data": True})
                           if node.kind is NodeKind.DATA else node)
        for edge in original.flow.graph.edges:
            graph.add_edge(edge)
        data_nodes = []
        for entry in entries:
            self.assertEqual(entry.parent_symbol, definition.forward_symbol_id)
            node = CodeNode(
                f"probe-data:{entry.id}", NodeKind.DATA, entry.name, entry.qualified_name,
                "probe.DataProbe.forward", "Python", source_anchors=(entry.source_anchor,),
                confidence=Confidence.PROBABLE,
                metadata={"symbol_id": entry.id, "data_role": "PREDICTION",
                          "owned_budget_probe": True},
            )
            graph.add_node(node)
            data_nodes.append(DataNode(node.id, node.name, DataRole.PREDICTION,
                                       entry.source_anchor, confidence=Confidence.PROBABLE))
        for source in data_nodes:
            for target in data_nodes:
                if source.id != target.id:
                    graph.add_edge(CodeEdge(
                        source.id, target.id, EdgeType.DATA_FLOW, Confidence.PROBABLE,
                        source_anchor=source.source_anchor,
                        metadata={"owned_budget_probe": True},
                    ))
        injected = replace(original, flow=replace(
            original.flow, graph=graph, data_nodes=(*original.flow.data_nodes, *data_nodes)))
        eligible = critical_data_slice(injected, (definition.symbol_id,), ViewKind.ARCHITECTURE)
        self.assertEqual({node.id for node in eligible.nodes}, {node.id for node in data_nodes})
        self.assertEqual(len(eligible.edges), 56)
        pairs = {(edge.source, edge.target, edge.type) for edge in eligible.edges}
        self.assertEqual(len(pairs), 56, "budget pressure must not come from duplicate edges")
        self.assertEqual(set(eligible.root_by_node.values()), {definition.symbol_id})
        self.assertTrue(all(edge.source != edge.target for edge in eligible.edges))
        self.assertTrue(all(edge.source_anchor in graph.nodes[edge.source].source_anchors
                            for edge in eligible.edges))
        self.assertEqual(CodeGraph.from_dict(graph.to_dict()).to_dict(), graph.to_dict())
        injected_facts = encode_analysis(injected)
        previous_analysis = self.analysis
        self.analysis = injected
        self.controller.analysis = injected
        try:
            detail = self.detail(definition.symbol_id, definition.symbol_id)
            self.assertEqual(detail["rootIds"], [definition.symbol_id])
            self.assert_closed_graph(detail)
            self.assertEqual(self.ids(detail), {definition.symbol_id, *(node.id for node in data_nodes)})
            self.assertEqual(len(detail["edges"]), 40)
            shown_pairs = {(edge["source"], edge["target"], EdgeType(edge["type"]))
                           for edge in detail["edges"]}
            self.assertEqual(len(shown_pairs), 40)
            self.assertTrue(shown_pairs <= pairs)
            for node in detail["nodes"]:
                if node["role"] == "DataNode":
                    anchor = graph.nodes[node["id"]].source_anchors[0]
                    self.assertEqual(node["sourceAnchors"], [{
                        "filePath": anchor.file_path, "startLine": anchor.start_line,
                        "startColumn": anchor.start_column, "endLine": anchor.end_line,
                        "endColumn": anchor.end_column,
                    }])
            self.assertGreater(detail["hiddenCounts"]["edges"], 0)
            self.assertIs(detail["queryContext"]["budgetTruncated"], True)
            self.assertIs(detail["queryContext"]["hasMore"], True)
        finally:
            self.analysis = previous_analysis
            self.controller.analysis = original
            self.assertEqual(encode_analysis(original), original_facts)
            self.assertEqual(encode_analysis(injected), injected_facts)
            self.assertEqual(self.controller.view_state.to_dict(), state)
            self.assertEqual(self.cache.analysis_path(self.root).read_bytes(), cache_bytes)

    def test_detail_cache_reopen_preserves_analysis_main_positions_and_island_position(self):
        source = self.node(self.main(), "model.GPT.transformer")
        hidden = self.node(self.main(4), "model.Block.proj")
        self.data(self.bridge.move_node(hidden["id"], [111, 222], "Architecture"))
        main = self.main()
        self.assertNotIn(hidden["id"], self.ids(main))
        self.save_selection(self.gpt)
        self.data(self.bridge.move_node(self.gpt, [321, 654], "Architecture"))
        self.data(self.bridge.open_detail_island("Architecture", source["id"], [20, 30]))
        before = self.cache.analysis_path(self.root).read_bytes()
        facts = encode_analysis(self.analysis)
        state = self.controller.view_state.to_dict()
        detail = self.detail(source["id"])
        self.assertEqual(self.cache.analysis_path(self.root).read_bytes(), before)
        self.assertEqual(encode_analysis(self.analysis), facts)
        self.assertEqual(self.controller.view_state.to_dict(), state)
        reopened = AppController(self.cache)
        reopened.open_project(self.root)
        self.assertIsNotNone(reopened.analysis)
        self.assertEqual(encode_analysis(reopened.analysis), facts)
        self.assertEqual(reopened.view_state.positions_for(ViewKind.ARCHITECTURE)[self.gpt], (321, 654))
        self.assertEqual(reopened.view_state.positions_for(ViewKind.ARCHITECTURE)[hidden["id"]],
                         (111, 222))
        islands = reopened.detail_islands(ViewKind.ARCHITECTURE)
        self.assertEqual(len(islands), 1)
        self.assertEqual(islands[0].source_node_id, source["id"])
        self.assertEqual(islands[0].position, (20, 30))
        rebuilt = reopened.detail_view(ViewKind.ARCHITECTURE, self.gpt, source["id"])
        self.assertEqual({node.id for node in rebuilt.nodes}, self.ids(detail))
        self.assertEqual(self.cache.analysis_path(self.root).read_bytes(), before)
