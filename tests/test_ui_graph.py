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
from codeviz.ui.canvas_scene import (
    DetailIslandScene,
    GraphScene,
    Point,
    Rect,
    Viewport,
    place_detail_island,
    route_edge,
    scene_to_screen,
    screen_to_scene,
    zoom_about,
)
from codeviz.ui.graph_canvas import GraphCanvas, edge_paint, graph_scene


class GraphSceneTests(unittest.TestCase):
    def test_viewport_transform_round_trips_and_zoom_keeps_pointer_anchor(self):
        viewport = Viewport(zoom=1.5, pan=Point(20.0, -10.0))
        scene_point = Point(80.0, 50.0)
        screen_point = scene_to_screen(scene_point, viewport)
        self.assertEqual(screen_to_scene(screen_point, viewport), scene_point)
        changed = zoom_about(viewport, screen_point, 2.0)
        self.assertEqual(scene_to_screen(scene_point, changed), screen_point)
        self.assertEqual(changed.zoom, 2.0)

    def test_zoom_is_clamped_to_supported_range(self):
        viewport = Viewport()
        self.assertEqual(zoom_about(viewport, Point(0.0, 0.0), 0.01).zoom, 0.25)
        self.assertEqual(zoom_about(viewport, Point(0.0, 0.0), 9.0).zoom, 2.5)

    def test_rect_intersection_can_include_a_margin(self):
        left = Rect(0.0, 0.0, 20.0, 20.0)
        right = Rect(25.0, 0.0, 20.0, 20.0)
        self.assertFalse(left.intersects(right))
        self.assertTrue(left.intersects(right, margin=5.0))

    def test_detail_island_scene_keeps_source_and_nested_graph(self):
        graph = GraphScene((), (), 340.0, 300.0)
        island = DetailIslandScene("detail:model", "model", "Model",
                                   Rect(10.0, 20.0, 340.0, 300.0), graph)
        self.assertEqual(island.source_node_id, "model")
        self.assertIs(island.graph, graph)

    def test_detail_island_prefers_right_then_avoids_occupied_rectangles(self):
        source = Rect(100.0, 100.0, 170.0, 66.0)
        occupied = (source, Rect(310.0, 70.0, 340.0, 300.0))
        placed = place_detail_island(source, (340.0, 300.0), occupied)
        self.assertFalse(any(placed.intersects(item, margin=24.0) for item in occupied))
        self.assertLess(placed.x, source.x)

    def test_direct_route_ignores_source_and_target_as_obstacles(self):
        source = Rect(20.0, 20.0, 100.0, 60.0)
        target = Rect(20.0, 240.0, 100.0, 60.0)
        self.assertEqual(route_edge(source, target, (source, target)),
                         (Point(70.0, 80.0), Point(70.0, 240.0)))

    def test_routed_edge_does_not_cross_blocking_node(self):
        source = Rect(20.0, 20.0, 100.0, 60.0)
        target = Rect(20.0, 240.0, 100.0, 60.0)
        blocker = Rect(0.0, 130.0, 140.0, 50.0)
        points = route_edge(source, target, (blocker,))
        self.assertGreater(max(point.x for point in points), blocker.x + blocker.width)

    def test_routed_edge_uses_left_dogleg_when_right_route_is_blocked(self):
        source = Rect(20.0, 20.0, 100.0, 60.0)
        target = Rect(20.0, 240.0, 100.0, 60.0)
        middle = Rect(0.0, 130.0, 140.0, 50.0)
        right_barrier = Rect(95.0, 85.0, 80.0, 20.0)
        points = route_edge(source, target, (middle, right_barrier))
        self.assertLess(min(point.x for point in points), middle.x)

    def test_routed_edge_falls_back_to_direct_when_all_candidates_are_blocked(self):
        source = Rect(20.0, 20.0, 100.0, 60.0)
        target = Rect(20.0, 240.0, 100.0, 60.0)
        obstacles = (
            Rect(0.0, 130.0, 140.0, 50.0),
            Rect(95.0, 85.0, 80.0, 20.0),
            Rect(-60.0, 85.0, 60.0, 20.0),
        )
        self.assertEqual(route_edge(source, target, obstacles),
                         (Point(70.0, 80.0), Point(70.0, 240.0)))

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

    def test_canvas_translates_legend_and_empty_state_only_for_display(self):
        import tkinter as tk

        from codeviz.ui.i18n import translate

        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("Tk display unavailable")
        self.addCleanup(root.destroy)
        graph = GraphCanvas(root, on_select=lambda _id: None,
                            on_drill_down=lambda _id: None,
                            on_move=lambda _id, _point: None,
                            translate=lambda phrase: translate("zh-CN", phrase))
        view = VisibleGraph(ViewKind.EXECUTION, (), legend=(
            VisibleLegendItem(EdgeType.CONTROL_FLOW, "Control Flow", "control-solid"),))
        graph.render(view)
        labels = [graph.canvas.itemcget(item, "text") for item in graph.canvas.find_all()
                  if graph.canvas.type(item) == "text"]
        self.assertTrue(any("控制流" in label for label in labels))
        self.assertTrue(any("未发现执行入口" in label for label in labels))
        self.assertEqual(view.legend[0].label, "Control Flow")

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
