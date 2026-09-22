"""Graph drawing instructions preserve view semantics without opening Tk."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from codeviz.core import (
    Confidence,
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
from codeviz.views import DetailIslandState


class _FakeWidget:
    def __init__(self, *_args, **_kwargs):
        pass

    def grid(self, **_kwargs):
        pass

    def rowconfigure(self, *_args, **_kwargs):
        pass

    def columnconfigure(self, *_args, **_kwargs):
        pass


class _FakeScrollbar(_FakeWidget):
    def set(self, *_args):
        pass


class _FakeCanvas(_FakeWidget):
    def __init__(self, *_args, **_kwargs):
        self.items: dict[int, dict[str, object]] = {}
        self.bindings: dict[str, object] = {}
        self.tag_bindings: dict[tuple[str, str], object] = {}
        self.scroll_calls: list[tuple[int, str]] = []
        self.current: tuple[int, ...] = ()
        self.width = 800
        self.height = 600
        self._next = 1

    def configure(self, **_kwargs):
        pass

    def bind(self, event, callback):
        self.bindings[event] = callback

    def tag_bind(self, tag, event, callback):
        self.tag_bindings[(tag, event)] = callback

    def delete(self, _tag):
        self.items.clear()

    def _create(self, kind, coords, options):
        item = self._next
        self._next += 1
        tags = options.pop("tags", ())
        if isinstance(tags, str):
            tags = (tags,)
        self.items[item] = {"kind": kind, "coords": tuple(coords),
                            "tags": tuple(tags), **options}
        return item

    def create_rectangle(self, *coords, **options):
        return self._create("rectangle", coords, options)

    def create_text(self, *coords, **options):
        return self._create("text", coords, options)

    def create_line(self, *coords, **options):
        if len(coords) == 1 and isinstance(coords[0], list):
            coords = tuple(coords[0])
        return self._create("line", coords, options)

    def find_all(self):
        return tuple(self.items)

    def find_withtag(self, tag):
        if tag == "current":
            return self.current
        return tuple(item for item, data in self.items.items()
                     if tag in data["tags"])

    def gettags(self, item):
        return self.items[item]["tags"]

    def type(self, item):
        return self.items[item]["kind"]

    def itemcget(self, item, option):
        return str(self.items[item].get(option, ""))

    def move(self, target, dx, dy):
        items = (target,) if isinstance(target, int) else self.find_withtag(target)
        for item in items:
            coords = list(self.items[item]["coords"])
            for index in range(0, len(coords), 2):
                coords[index] = float(coords[index]) + dx
                coords[index + 1] = float(coords[index + 1]) + dy
            self.items[item]["coords"] = tuple(coords)

    def focus_set(self):
        pass

    def winfo_width(self):
        return self.width

    def winfo_height(self):
        return self.height

    def canvasx(self, value):
        return float(value)

    def canvasy(self, value):
        return float(value)

    def yview_scroll(self, amount, units):
        self.scroll_calls.append((amount, units))

    def xview(self, *_args):
        pass

    def yview(self, *_args):
        return (0.0, 1.0)

    def yview_moveto(self, _fraction):
        pass


def _graph_canvas(**callbacks):
    canvas = _FakeCanvas()
    with (patch("tkinter.Frame", _FakeWidget),
          patch("tkinter.Canvas", return_value=canvas),
          patch("tkinter.Scrollbar", _FakeScrollbar)):
        graph = GraphCanvas(object(), on_select=callbacks.get("on_select", lambda _id: None),
                            on_drill_down=callbacks.get("on_drill_down", lambda _id: None),
                            on_move=callbacks.get("on_move", lambda _id, _point: None),
                            on_open_detail=callbacks.get("on_open_detail"),
                            on_move_island=callbacks.get("on_move_island"),
                            on_close_island=callbacks.get("on_close_island"),
                            on_open_source=callbacks.get("on_open_source"),
                            on_viewport=callbacks.get("on_viewport"))
    return graph, canvas


def _interactive_views():
    root = VisibleNode("root", "Root", code_node_ids=("root",),
                       metadata={"has_children": True})
    leaf = VisibleNode("leaf", "Leaf", code_node_ids=("leaf",))
    edge = VisibleEdge("root", "leaf", EdgeType.CONTROL_FLOW,
                       code_edge_ids=("root-leaf",))
    main = VisibleGraph(ViewKind.ARCHITECTURE, (root, leaf), ("root",), (edge,),
                        layout_hints={"root": (0.0, 0.0), "leaf": (0.0, 130.0)},
                        legend=(VisibleLegendItem(EdgeType.CONTROL_FLOW, "Control Flow",
                                                  "control-solid"),))
    inner = VisibleNode("inner", "Inner", code_node_ids=("inner",))
    detail = VisibleGraph(ViewKind.ARCHITECTURE, (inner,), ("inner",),
                          layout_hints={"inner": (0.0, 0.0)})
    state = DetailIslandState("Architecture:root", ViewKind.ARCHITECTURE,
                              "root", (320.0, 80.0))
    return main, state, detail


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
        self.assertNotEqual(edge_paint("gradient-reverse"), edge_paint("control-solid"))

    def test_scene_routes_flow_around_intermediate_node(self):
        nodes = tuple(VisibleNode(node_id, node_id.title(), code_node_ids=(node_id,))
                      for node_id in ("source", "blocker", "target"))
        edge = VisibleEdge("source", "target", EdgeType.CONTROL_FLOW,
                           code_edge_ids=("source-target",))
        view = VisibleGraph(ViewKind.EXECUTION, nodes, edges=(edge,),
                            layout_hints={"source": (0.0, 0.0),
                                          "blocker": (0.0, 110.0),
                                          "target": (0.0, 230.0)})

        scene = graph_scene(view)
        blocker = next(node for node in scene.nodes if node.id == "blocker")

        self.assertGreater(max(scene.edges[0].points[::2]), blocker.x + blocker.width)

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

    def test_render_retains_viewport_scenes_details_and_context_tags(self):
        graph, canvas = _graph_canvas()
        main, state, detail = _interactive_views()
        viewport = Viewport(1.25, Point(18.0, -7.0))

        graph.render(main, viewport=viewport, details=((state, detail),))

        self.assertEqual(graph.viewport, viewport)
        self.assertIs(graph.main_view, main)
        self.assertIs(graph.detail_view(state.id), detail)
        self.assertEqual(graph.selected_context, None)
        all_tags = {tag for item in canvas.items.values() for tag in item["tags"]}
        self.assertIn("context:main", all_tags)
        self.assertIn(f"context:{state.id}", all_tags)
        self.assertIn(f"island:{state.id}", all_tags)
        self.assertIn("expand:root", all_tags)
        self.assertIn(f"close:{state.id}", all_tags)
        self.assertIn("source:root", all_tags)
        self.assertNotIn("expand:inner", all_tags)

    def test_render_transforms_detail_nodes_and_draws_dashed_connector(self):
        graph, canvas = _graph_canvas()
        main, state, detail = _interactive_views()
        graph.render(main, viewport=Viewport(2.0, Point(10.0, 20.0)),
                     details=((state, detail),))

        inner_items = [item for item in canvas.items.values()
                       if "node:inner" in item["tags"] and item["kind"] == "rectangle"]
        self.assertEqual(len(inner_items), 1)
        self.assertGreater(inner_items[0]["coords"][0], state.position[0] * 2.0)
        connectors = [item for item in canvas.items.values()
                      if "detail-connector" in item["tags"]]
        self.assertEqual(len(connectors), 1)
        self.assertTrue(connectors[0]["dash"])

    def test_single_click_selects_context_and_double_click_or_plus_opens_detail(self):
        selections: list[tuple[str, str | None]] = []
        opened: list[str] = []
        graph, canvas = _graph_canvas(
            on_select=lambda node_id, context: selections.append((node_id, context)),
            on_open_detail=opened.append,
        )
        main, state, detail = _interactive_views()
        graph.render(main, details=((state, detail),))

        graph._press(SimpleNamespace(x=100, y=100), node_id="root", context_id=None)
        self.assertEqual(selections, [("root", None)])
        self.assertEqual(opened, [])
        graph._double_click(SimpleNamespace(), node_id="root")
        graph._open_detail(SimpleNamespace(), node_id="root")
        self.assertEqual(opened, ["root", "root"])

        graph._press(SimpleNamespace(x=400, y=180), node_id="inner",
                     context_id=state.id)
        self.assertEqual(selections[-1], ("inner", state.id))
        self.assertEqual(graph.selected_context, state.id)
        self.assertFalse(any("expand:inner" in item["tags"]
                             for item in canvas.items.values()))

    def test_click_release_refreshes_selection_without_opening_detail(self):
        opened: list[str] = []
        graph, _canvas = _graph_canvas(on_open_detail=opened.append)
        main, _state, _detail = _interactive_views()
        graph.render(main)

        graph._press(SimpleNamespace(x=100, y=100), node_id="root", context_id=None)
        graph._release(SimpleNamespace())

        selected = next(node for node in graph.main_scene.nodes if node.id == "root")
        self.assertTrue(selected.selected)
        self.assertEqual(opened, [])

    def test_probable_node_has_non_color_dash_cue(self):
        node = VisibleNode("probable", "Probable", code_node_ids=("probable",),
                           confidence=Confidence.PROBABLE)
        view = VisibleGraph(ViewKind.ARCHITECTURE, (node,),
                            layout_hints={"probable": (0.0, 0.0)})
        graph, canvas = _graph_canvas()

        graph.render(view)

        rectangle = next(item for item in canvas.items.values()
                         if item["kind"] == "rectangle" and
                         "node:probable" in item["tags"])
        self.assertTrue(rectangle["dash"])

    def test_legacy_one_argument_selection_and_drill_down_stay_compatible(self):
        selections: list[str] = []
        opened: list[str] = []
        graph, _canvas = _graph_canvas(on_select=selections.append,
                                       on_drill_down=opened.append)
        main, _state, _detail = _interactive_views()
        graph.render(main)

        graph._press(SimpleNamespace(x=100, y=100), node_id="root", context_id=None)
        graph._double_click(SimpleNamespace(), node_id="root")

        self.assertEqual(selections, ["root"])
        self.assertEqual(opened, ["root"])

    def test_close_and_source_controls_only_invoke_their_callbacks(self):
        closed: list[str] = []
        sources: list[tuple[str, str | None]] = []
        opened: list[str] = []
        graph, _canvas = _graph_canvas(on_close_island=closed.append,
                                       on_open_source=lambda node_id, context:
                                       sources.append((node_id, context)),
                                       on_open_detail=opened.append)
        main, state, detail = _interactive_views()
        graph.render(main, details=((state, detail),))

        graph._close_detail(SimpleNamespace(), island_id=state.id)
        graph._source_detail(SimpleNamespace(), island_id=state.id,
                             source_node_id="root")

        self.assertEqual(closed, [state.id])
        self.assertEqual(sources, [("root", state.id)])
        self.assertEqual(opened, [])

    def test_suggested_island_position_avoids_main_and_existing_islands(self):
        graph, _canvas = _graph_canvas()
        main, state, detail = _interactive_views()
        graph.render(main, details=((state, detail),))

        suggested = graph.suggest_island_position("root")
        candidate = Rect(*suggested, 440.0, 300.0)
        occupied = tuple(Rect(node.x, node.y, node.width, node.height)
                         for node in graph.main_scene.nodes)
        occupied += tuple(island.bounds for island in graph.detail_scenes)
        self.assertFalse(any(candidate.intersects(bounds, margin=24.0)
                             for bounds in occupied))
        with self.assertRaisesRegex(ValueError,
                                    "^node is not visible on the canvas$"):
            graph.suggest_island_position("missing")

    def test_ctrl_wheel_zooms_about_pointer_and_normal_wheel_scrolls(self):
        viewports: list[tuple[float, tuple[float, float]]] = []
        graph, canvas = _graph_canvas(on_viewport=lambda zoom, pan:
                                      viewports.append((zoom, pan)))
        main, _state, _detail = _interactive_views()
        graph.render(main, viewport=Viewport(1.0, Point(10.0, 20.0)))
        anchor = Point(200.0, 150.0)
        before = screen_to_scene(anchor, graph.viewport)

        graph._on_zoom(SimpleNamespace(x=anchor.x, y=anchor.y, delta=120, num=0))

        self.assertGreater(graph.viewport.zoom, 1.0)
        self.assertEqual(screen_to_scene(anchor, graph.viewport), before)
        self.assertEqual(viewports[-1], (graph.viewport.zoom,
                                         (graph.viewport.pan.x, graph.viewport.pan.y)))
        graph._on_mousewheel(SimpleNamespace(x=0, y=0, delta=-120, num=0, state=0))
        self.assertEqual(canvas.scroll_calls[-1], (1, "units"))

    def test_blank_drag_pans_and_reports_viewport(self):
        viewports: list[tuple[float, tuple[float, float]]] = []
        graph, canvas = _graph_canvas(on_viewport=lambda zoom, pan:
                                      viewports.append((zoom, pan)))
        main, _state, _detail = _interactive_views()
        graph.render(main)
        canvas.current = ()

        graph._blank_pan_start(SimpleNamespace(x=100, y=90))
        graph._pan_move(SimpleNamespace(x=140, y=115))
        graph._release(SimpleNamespace())

        self.assertEqual(graph.viewport.pan, Point(40.0, 25.0))
        self.assertEqual(viewports, [(1.0, (40.0, 25.0))])

    def test_node_drag_moves_only_node_and_reports_scene_delta(self):
        moved: list[tuple[str, tuple[float, float]]] = []
        graph, _canvas = _graph_canvas(on_move=lambda node_id, point:
                                       moved.append((node_id, point)))
        main, _state, _detail = _interactive_views()
        graph.render(main, viewport=Viewport(2.0, Point(0.0, 0.0)))

        graph._press(SimpleNamespace(x=100, y=100), node_id="root", context_id=None)
        graph._drag(SimpleNamespace(x=140, y=120))
        self.assertEqual(graph.viewport.pan, Point(0.0, 0.0))
        graph._release(SimpleNamespace())

        self.assertEqual(moved, [("root", (20.0, 10.0))])

    def test_island_drag_reports_new_scene_position(self):
        moved: list[tuple[str, tuple[float, float]]] = []
        graph, _canvas = _graph_canvas(on_move_island=lambda island_id, point:
                                       moved.append((island_id, point)))
        main, state, detail = _interactive_views()
        graph.render(main, viewport=Viewport(2.0, Point(0.0, 0.0)),
                     details=((state, detail),))

        graph._island_press(SimpleNamespace(x=650, y=170), island_id=state.id)
        graph._drag(SimpleNamespace(x=690, y=190))
        graph._release(SimpleNamespace())

        self.assertEqual(moved, [(state.id, (340.0, 90.0))])

    def test_escape_closes_only_selected_island(self):
        closed: list[str] = []
        graph, _canvas = _graph_canvas(on_close_island=closed.append)
        main, state, detail = _interactive_views()
        graph.render(main, details=((state, detail),))

        graph._escape(SimpleNamespace())
        self.assertEqual(closed, [])
        graph._press(SimpleNamespace(x=400, y=180), node_id="inner",
                     context_id=state.id)
        graph._escape(SimpleNamespace())
        self.assertEqual(closed, [state.id])

    def test_fit_and_reset_are_clamped_and_keep_scene_center(self):
        graph, canvas = _graph_canvas()
        main, state, detail = _interactive_views()
        graph.render(main, viewport=Viewport(2.0, Point(30.0, -20.0)),
                     details=((state, detail),))
        graph.fit_to_window()
        self.assertGreaterEqual(graph.viewport.zoom, 0.25)
        self.assertLessEqual(graph.viewport.zoom, 2.5)
        center = Point(canvas.width / 2, canvas.height / 2)
        scene_center = screen_to_scene(center, graph.viewport)

        graph.reset_zoom()

        self.assertEqual(graph.viewport.zoom, 1.0)
        self.assertEqual(screen_to_scene(center, graph.viewport), scene_center)
        before_resize = graph.viewport
        graph._on_resize(SimpleNamespace(width=900, height=700))
        self.assertEqual(graph.viewport, before_resize)


if __name__ == "__main__":
    unittest.main()
