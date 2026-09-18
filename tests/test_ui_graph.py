"""Graph drawing instructions preserve view semantics without opening Tk."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from codeviz.core import (
    EdgeType,
    SourceAnchor,
    ViewKind,
    VisibleEdge,
    VisibleGraph,
    VisibleLegendItem,
    VisibleNode,
)
from codeviz.ui.graph_canvas import GraphCanvas, edge_paint, graph_scene


class GraphSceneTests(unittest.TestCase):
    def test_single_column_is_centered_when_viewport_is_wide(self):
        node = VisibleNode("only", "Only", code_node_ids=("only",))
        view = VisibleGraph(ViewKind.ARCHITECTURE, (node,),
                            layout_hints={"only": (0.0, 0.0)})
        scene = graph_scene(view, viewport_width=600)
        self.assertEqual(scene.nodes[0].x + scene.nodes[0].width / 2, 300)

    def test_wheel_scrolls_down_and_page_keys_scroll_by_pages(self):
        import tkinter as tk

        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("Tk display unavailable")
        self.addCleanup(root.destroy)
        graph = GraphCanvas(root, on_select=lambda _id: None,
                            on_drill_down=lambda _id: None,
                            on_move=lambda _id, _point: None)
        with patch.object(graph.canvas, "yview_scroll") as scroll:
            graph._on_mousewheel(SimpleNamespace(delta=-120, num=0))
            scroll.assert_called_with(1, "units")
            graph._on_page_key(SimpleNamespace(keysym="Next"))
            scroll.assert_called_with(1, "pages")

    def test_flow_edge_connects_bottom_to_top_in_vertical_layout(self):
        nodes = (VisibleNode("first", "First", code_node_ids=("first",)),
                 VisibleNode("second", "Second", code_node_ids=("second",)))
        edge = VisibleEdge("first", "second", EdgeType.CONTROL_FLOW,
                           code_edge_ids=("first-second",))
        view = VisibleGraph(ViewKind.EXECUTION, nodes, edges=(edge,),
                            layout_kind="flowchart",
                            layout_hints={"first": (0.0, 0.0),
                                          "second": (0.0, 130.0)})
        scene = graph_scene(view)
        first, second = scene.nodes
        self.assertEqual(scene.edges[0].points,
                         (first.x + first.width / 2, first.y + first.height,
                          second.x + second.width / 2, second.y))

    def test_tk_adapter_is_lazy_and_relation_paints_are_distinct(self):
        self.assertTrue(callable(GraphCanvas))
        self.assertNotEqual(edge_paint("structural-dotted"), edge_paint("control-solid"))
        self.assertNotEqual(edge_paint("data-solid"), edge_paint("loop-curved"))

    def test_scene_uses_layout_labels_and_one_hop_selection(self):
        anchor = SourceAnchor("model.py", 2)
        model = VisibleNode("model", "Model", semantic_component_ids=("model",),
                            code_name="Net", source_anchors=(anchor,))
        layer = VisibleNode("layer", "Projection", semantic_component_ids=("layer",),
                            code_name="self.proj", source_anchors=(SourceAnchor("model.py", 4),))
        edge = VisibleEdge("model", "layer", EdgeType.CONTAINS,
                           source_anchors=(SourceAnchor("model.py", 4),))
        view = VisibleGraph(ViewKind.ARCHITECTURE, (model, layer), ("model",), (edge,),
                            layout_kind="hierarchical",
                            layout_hints={"model": (0.0, 0.0), "layer": (220.0, 140.0)},
                            legend=(VisibleLegendItem(EdgeType.CONTAINS, "Contains",
                                                      "structural-dotted"),))
        scene = graph_scene(view, selected_id="layer")
        nodes = {item.id: item for item in scene.nodes}
        self.assertEqual(nodes["model"].subtitle, "Net")
        self.assertEqual(nodes["layer"].label, "Projection")
        self.assertTrue(nodes["layer"].selected)
        self.assertTrue(nodes["model"].neighbor)
        self.assertEqual(nodes["layer"].x - nodes["model"].x, 220.0)
        self.assertEqual(scene.edges[0].style, "structural-dotted")

    def test_different_relations_and_aggregate_remain_distinguishable(self):
        anchor = SourceAnchor("run.py", 3)
        nodes = (VisibleNode("a", "A", source_anchors=(anchor,)),
                 VisibleNode("b", "B", source_anchors=(anchor,)),
                 VisibleNode("more", "12 more", member_ids=("hidden",)))
        relations = (EdgeType.CONTAINS, EdgeType.CONTROL_FLOW,
                     EdgeType.DATA_FLOW, EdgeType.LOOP_BACK)
        edges = tuple(VisibleEdge("a", "b", kind, source_anchors=(anchor,))
                      for kind in relations)
        legend = tuple(VisibleLegendItem(kind, kind.value, style)
                       for kind, style in zip(relations,
                                              ("structural-dotted", "control-solid",
                                               "data-solid", "loop-curved"), strict=True))
        view = VisibleGraph(ViewKind.EXECUTION, nodes, edges=edges,
                            layout_hints={node.id: (float(index * 220), 0.0)
                                          for index, node in enumerate(nodes)},
                            legend=legend)
        scene = graph_scene(view)
        self.assertEqual({item.style for item in scene.edges},
                         {item.style for item in legend})
        self.assertEqual(next(item for item in scene.nodes if item.id == "more").member_count, 1)
        self.assertTrue(all(item.points[0] >= 0 for item in scene.edges))


if __name__ == "__main__":
    unittest.main()
