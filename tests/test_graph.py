import json
import unittest

from codeviz.core import (
    CodeEdge,
    CodeGraph,
    CodeNode,
    Confidence,
    DataNode,
    DataRole,
    EdgeType,
    EntryPoint,
    EntryPointKind,
    NodeKind,
    NodeOrigin,
    SemanticComponent,
    SourceAnchor,
    ViewKind,
    VisibleEdge,
    VisibleGraph,
    VisibleNode,
)


def source_node(node_id: str, name: str) -> CodeNode:
    return CodeNode(
        id=node_id,
        kind=NodeKind.FUNCTION,
        name=name,
        qualified_name=name,
        scope="module",
        language="python",
        source_anchors=(SourceAnchor("train.py", 3),),
    )


class CodeGraphTests(unittest.TestCase):
    def test_source_node_requires_anchor_but_synthetic_project_does_not(self):
        with self.assertRaises(ValueError):
            CodeNode("f", NodeKind.FUNCTION, "f", "f", "module", "python")

        project = CodeNode(
            "project", NodeKind.PROJECT, "Demo", "Demo", "", "python",
            origin=NodeOrigin.SYNTHETIC,
        )
        self.assertEqual(project.source_anchors, ())

    def test_graph_rejects_dangling_edges_and_duplicate_nodes(self):
        graph = CodeGraph()
        graph.add_node(source_node("caller", "train"))

        with self.assertRaises(ValueError):
            graph.add_node(source_node("caller", "train"))
        with self.assertRaises(ValueError):
            graph.add_edge(CodeEdge("caller", "missing", EdgeType.CALLS))

    def test_possible_call_targets_remain_uncertain(self):
        graph = CodeGraph()
        for node_id in ("caller", "unknown", "candidate"):
            graph.add_node(source_node(node_id, node_id))
        edge = CodeEdge(
            "caller", "unknown", EdgeType.CALLS,
            confidence=Confidence.UNRESOLVED,
            source_anchor=SourceAnchor("train.py", 9),
            possible_targets=("candidate",),
        )
        graph.add_edge(edge)

        self.assertEqual(graph.edges[0].possible_targets, ("candidate",))
        with self.assertRaises(ValueError):
            CodeEdge("caller", "candidate", EdgeType.CALLS, possible_targets=("candidate",))

    def test_graph_survives_json_round_trip(self):
        graph = CodeGraph()
        graph.add_node(source_node("caller", "train"))
        graph.add_node(source_node("callee", "forward"))
        graph.add_edge(CodeEdge("caller", "callee", EdgeType.CALLS, source_anchor=SourceAnchor("train.py", 9)))

        restored = CodeGraph.from_dict(json.loads(json.dumps(graph.to_dict())))

        self.assertEqual(restored.nodes["caller"], graph.nodes["caller"])
        self.assertEqual(restored.edges, graph.edges)


class SemanticModelTests(unittest.TestCase):
    def test_semantic_and_visible_nodes_require_provenance(self):
        with self.assertRaises(ValueError):
            SemanticComponent("attention", "Attention")
        component = SemanticComponent("attention", "Attention", code_symbol_ids=("forward",))
        visible = VisibleNode("attention", "Attention", semantic_component_ids=(component.id,))

        self.assertEqual(visible.semantic_component_ids, ("attention",))
        with self.assertRaises(ValueError):
            VisibleNode("orphan", "Orphan")

    def test_data_entrypoint_and_view_keep_source_relationships(self):
        anchor = SourceAnchor("train.py", 12)
        loss = DataNode("loss", "loss", DataRole.LOSS, anchor, shape=(), dtype="float32")
        entry = EntryPoint("train", "Train", EntryPointKind.TRAIN, anchor, model_ids=("model",))
        node = VisibleNode("loss", "Loss", source_anchors=(anchor,))
        view = VisibleGraph(ViewKind.EXECUTION, (node,), root_ids=("loss",))

        self.assertEqual(loss.source_anchor, entry.source_anchor)
        self.assertEqual(view.nodes[0].id, "loss")
        with self.assertRaises(ValueError):
            VisibleGraph(ViewKind.EXECUTION, (node,), root_ids=("missing",))

    def test_visible_edge_keeps_its_own_provenance(self):
        left = VisibleNode("model", "Model", code_node_ids=("model-definition",))
        right = VisibleNode("block", "Block", code_node_ids=("block-definition",))
        edge = VisibleEdge("model", "block", EdgeType.CONTAINS, code_edge_ids=("ir-edge-1",))
        view = VisibleGraph(ViewKind.ARCHITECTURE, (left, right), edges=(edge,))

        self.assertEqual(view.edges[0].code_edge_ids, ("ir-edge-1",))
        with self.assertRaises(ValueError):
            VisibleEdge("model", "block", EdgeType.CONTAINS)


if __name__ == "__main__":
    unittest.main()
