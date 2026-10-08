"""Visible views are bounded, source-backed queries over analysis facts."""

import json
import unittest
from unittest.mock import patch

from tests.test_training import analyze_files

from codeviz.core import (
    EdgeType,
    ViewIssue,
    ViewKind,
    VisibleAggregation,
    VisibleEdge,
    VisibleGraph,
    VisibleNode,
)
from codeviz.views import ViewRequest, ViewState, build_visible_graph
from codeviz.views.layout import layout_positions


class VisibleContractTests(unittest.TestCase):
    def test_layered_layout_is_stable_and_centers_a_merge_under_its_inputs(self):
        nodes = tuple(VisibleNode(name, name, code_node_ids=(name,))
                      for name in ("root", "left", "right", "merge"))
        edges = (
            VisibleEdge("root", "left", EdgeType.CONTAINS, code_edge_ids=("a",)),
            VisibleEdge("root", "right", EdgeType.CONTAINS, code_edge_ids=("b",)),
            VisibleEdge("left", "merge", EdgeType.CONTROL_FLOW, code_edge_ids=("c",)),
            VisibleEdge("right", "merge", EdgeType.CONTROL_FLOW, code_edge_ids=("d",)),
        )

        first = layout_positions(nodes, edges, ("root",), "hierarchical", ViewState())
        reversed_edges = layout_positions(nodes, tuple(reversed(edges)), ("root",),
                                          "hierarchical", ViewState())

        self.assertEqual(first, reversed_edges)
        self.assertEqual(first["root"][1], 0.0)
        self.assertEqual(first["left"][1], first["right"][1])
        self.assertNotEqual(first["left"][0], first["right"][0])
        self.assertGreater(first["merge"][1], first["left"][1])
        self.assertAlmostEqual(first["merge"][0],
                               (first["left"][0] + first["right"][0]) / 2)

    def test_default_architecture_and_execution_layouts_read_downward(self):
        nodes = tuple(VisibleNode(name, name, code_node_ids=(name,))
                      for name in ("root", "middle", "leaf"))
        architecture_edges = (
            VisibleEdge("root", "middle", EdgeType.CONTAINS, code_edge_ids=("a",)),
            VisibleEdge("root", "leaf", EdgeType.CONTAINS, code_edge_ids=("b",)),
        )
        execution_edges = (
            VisibleEdge("root", "middle", EdgeType.CONTROL_FLOW, code_edge_ids=("c",)),
            VisibleEdge("middle", "leaf", EdgeType.CONTROL_FLOW, code_edge_ids=("d",)),
        )
        for kind, edges in (("hierarchical", architecture_edges),
                            ("flowchart", execution_edges)):
            with self.subTest(kind=kind):
                positions = layout_positions(nodes, edges, ("root",), kind, ViewState())
                self.assertLess(positions["root"][1], positions["middle"][1])
                if kind == "hierarchical":
                    self.assertEqual(positions["middle"][1], positions["leaf"][1])
                    self.assertNotEqual(positions["middle"][0], positions["leaf"][0])
                else:
                    self.assertLess(positions["middle"][1], positions["leaf"][1])

    def test_layered_layout_pins_root_when_allowed_edges_form_a_cycle(self):
        nodes = tuple(VisibleNode(name, name, code_node_ids=(name,))
                      for name in ("root", "body", "tail"))
        edges = (
            VisibleEdge("root", "body", EdgeType.CONTROL_FLOW, code_edge_ids=("a",)),
            VisibleEdge("body", "tail", EdgeType.CONTAINS, code_edge_ids=("b",)),
            VisibleEdge("tail", "root", EdgeType.CONTROL_FLOW, code_edge_ids=("c",)),
        )

        positions = layout_positions(nodes, edges, ("root",), "flowchart", ViewState())

        self.assertEqual(set(positions), {node.id for node in nodes})
        self.assertEqual(len(positions), len(nodes))
        self.assertEqual(positions["root"][1], 0.0)
        self.assertGreater(positions["tail"][1], positions["body"][1])

    def test_layout_state_position_overrides_automatic_placement(self):
        nodes = tuple(VisibleNode(name, name, code_node_ids=(name,))
                      for name in ("root", "child"))
        edges = (VisibleEdge("root", "child", EdgeType.CONTAINS, code_edge_ids=("a",)),)
        state = ViewState(positions={"child": (123.0, 456.0)})

        positions = layout_positions(nodes, edges, ("root",), "hierarchical", state)

        self.assertEqual(positions["child"], (123.0, 456.0))

    def test_layout_uses_only_positions_for_requested_view(self):
        node = VisibleNode("shared", "Shared", code_node_ids=("shared",))
        state = ViewState(positions_by_view={
            ViewKind.SYMBOL.value: {"shared": (123.0, 456.0)},
            ViewKind.DATA_FLOW.value: {"shared": (789.0, 321.0)},
        })

        symbol = layout_positions((node,), (), ("shared",), "directed", state,
                                  view_kind=ViewKind.SYMBOL)
        data_flow = layout_positions((node,), (), ("shared",), "directed", state,
                                     view_kind=ViewKind.DATA_FLOW)

        self.assertEqual(symbol["shared"], (123.0, 456.0))
        self.assertEqual(data_flow["shared"], (789.0, 321.0))

    def test_layout_failure_falls_back_to_deterministic_vertical_positions(self):
        nodes = tuple(VisibleNode(name, name, code_node_ids=(name,))
                      for name in ("zeta", "alpha", "middle"))
        issues: list[ViewIssue] = []

        with patch("codeviz.views.layout._ranks", side_effect=RuntimeError("boom")):
            first = layout_positions(
                nodes, (), (), "directed", ViewState(),
                view_kind=ViewKind.SYMBOL, issues=issues)
            second = layout_positions(
                tuple(reversed(nodes)), (), (), "directed", ViewState(),
                view_kind=ViewKind.SYMBOL, issues=[])

        self.assertEqual(first, second)
        self.assertEqual(tuple(first), ("alpha", "middle", "zeta"))
        self.assertEqual([first[node_id][0] for node_id in first], [0.0, 0.0, 0.0])
        self.assertEqual(len({point[1] for point in first.values()}), len(nodes))
        graph = VisibleGraph(ViewKind.SYMBOL, nodes, layout_hints=first)
        self.assertTrue(all(node.code_node_ids for node in graph.nodes))
        self.assertEqual([issue.code for issue in issues], ["LAYOUT_FALLBACK"])
        self.assertEqual(issues[0].view_kind, ViewKind.SYMBOL)

    def test_normal_layout_emits_no_issue(self):
        node = VisibleNode("root", "Root", code_node_ids=("root",))
        issues: list[ViewIssue] = []

        positions = layout_positions(
            (node,), (), ("root",), "hierarchical", ViewState(),
            view_kind=ViewKind.ARCHITECTURE, issues=issues)

        self.assertEqual(positions, {"root": (0.0, 0.0)})
        self.assertEqual(issues, [])

    def test_graph_enforces_budget_and_aggregation_provenance(self):
        nodes = (VisibleNode("a", "A", code_node_ids=("a",)),
                 VisibleNode("b", "B", code_node_ids=("b",)))
        with self.assertRaises(ValueError):
            VisibleGraph(ViewKind.ARCHITECTURE, nodes, max_nodes=1)
        with self.assertRaises(ValueError):
            VisibleAggregation("more", ())
        with self.assertRaises(ValueError):
            VisibleGraph(ViewKind.ARCHITECTURE, nodes,
                         aggregations=(VisibleAggregation("missing", ("hidden",)),))

    def test_view_state_json_data_round_trip_and_rejects_invalid_positions(self):
        state = ViewState(expanded_ids=("model", "block"),
                          positions={"block": (12.5, 24.0)})
        restored = ViewState.from_dict(json.loads(json.dumps(state.to_dict())))
        self.assertEqual(restored, state)
        with self.assertRaises(ValueError):
            ViewState(positions={"x": (float("nan"), 0.0)})


class VisibleBuilderTests(unittest.TestCase):
    def test_symbol_context_is_bounded_and_directional(self):
        result = analyze_files({"run.py": (
            "def source(): return 1\n"
            "def middle(): return source()\n"
            "def sink(): return middle()\n")})
        middle = next(item for item in result.symbol_index.entries.values()
                      if item.name == "middle")
        upstream = build_visible_graph(result, ViewRequest(
            ViewKind.SYMBOL, root_id=middle.id, selected_id=middle.id, hops=1))
        self.assertIn(middle.id, {node.id for node in upstream.nodes})
        self.assertLessEqual(len(upstream.nodes), 100)
        self.assertTrue(all(node.source_anchors or node.member_ids
                            for node in upstream.nodes))
        callers = build_visible_graph(result, ViewRequest(
            ViewKind.SYMBOL, selected_id=middle.id, direction="upstream"))
        descendants = build_visible_graph(result, ViewRequest(
            ViewKind.SYMBOL, selected_id=middle.id, direction="downstream"))
        self.assertNotEqual({node.id for node in callers.nodes},
                            {node.id for node in descendants.nodes})
        limited = build_visible_graph(result, ViewRequest(
            ViewKind.SYMBOL, selected_id=middle.id, hops=5, max_nodes=2, max_edges=1))
        self.assertLessEqual(len(limited.nodes), 2)
        self.assertLessEqual(len(limited.edges), 1)
        self.assertIn(middle.id, {node.id for node in limited.nodes})
        self.assertGreater(limited.hidden_counts["nodes"], 0)

    def test_data_read_is_downstream_of_data_not_upstream(self):
        result = analyze_files({"run.py": "def train(x):\n    loss = x\n    return loss\n"})
        loss = next(node for node in result.flow.graph.nodes.values()
                    if node.kind.value == "Data" and node.name == "loss")
        read = next(edge for edge in result.flow.graph.edges
                    if edge.type == EdgeType.READS and edge.target == loss.id)
        upstream = build_visible_graph(result, ViewRequest(
            ViewKind.DATA_FLOW, selected_id=loss.id, direction="upstream"))
        downstream = build_visible_graph(result, ViewRequest(
            ViewKind.DATA_FLOW, selected_id=loss.id, direction="downstream"))
        self.assertNotIn(read.source, {node.id for node in upstream.nodes})
        self.assertIn(read.source, {node.id for node in downstream.nodes})
        self.assertTrue(any(edge.source == loss.id and edge.target == read.source
                            and edge.type == EdgeType.READS for edge in downstream.edges))

    def test_architecture_and_execution_choose_independent_roots(self):
        result = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def __init__(self):\n"
                "        self.layer = nn.Linear(2, 2)\n"
                "    def forward(self, x): return self.layer(x)\n"
            ),
            "run.py": (
                "import torch\n"
                "def train(optimizer, loss):\n"
                "    criterion = torch.nn.MSELoss()\n"
                "    for epoch in range(2):\n"
                "        loss.backward()\n"
                "        optimizer.step()\n"
                "def evaluate(model):\n"
                "    criterion = torch.nn.MSELoss()\n"
                "    model.eval()\n"
            ),
        })
        model_id = result.model.definitions[0].symbol_id
        train = next(entry for entry in result.training.entry_points if entry.name == "train")
        architecture = build_visible_graph(result, ViewRequest(ViewKind.ARCHITECTURE,
                                                               root_id=model_id))
        execution = build_visible_graph(result, ViewRequest(ViewKind.EXECUTION,
                                                            root_id=train.id))
        self.assertEqual(architecture.root_ids, (model_id,))
        self.assertEqual(execution.root_ids, (train.id,))
        all_executions = build_visible_graph(result, ViewRequest(ViewKind.EXECUTION))
        self.assertEqual(set(all_executions.root_ids),
                         {entry.id for entry in result.training.entry_points})
        self.assertTrue(any(node.display_name == "layer" for node in architecture.nodes))
        self.assertTrue(any(node.display_name == "EpochLoop" for node in execution.nodes))
        self.assertFalse(any(edge.type == EdgeType.GRADIENT_FLOW for edge in execution.edges))
        self.assertTrue(all(node.semantic_component_ids or node.code_node_ids or node.source_anchors
                            or node.member_ids for node in architecture.nodes + execution.nodes))
        self.assertEqual(architecture.layout_kind, "hierarchical")
        self.assertEqual(execution.layout_kind, "flowchart")

    def test_expand_loop_reveals_internal_events_without_changing_facts(self):
        result = analyze_files({"run.py": (
            "import torch\n"
            "def train(optimizer, loss):\n"
            "    criterion = torch.nn.MSELoss()\n"
            "    for epoch in range(2):\n"
            "        loss.backward()\n"
            "        optimizer.step()\n"
        )})
        loop_id = next(event.id for event in result.training.events
                       if event.role.value == "EpochLoop")
        original = result.semantic.graph
        collapsed = build_visible_graph(result, ViewRequest(ViewKind.EXECUTION))
        expanded = build_visible_graph(result, ViewRequest(ViewKind.EXECUTION),
                                       ViewState(expanded_ids=(loop_id,)))
        self.assertLess(len(collapsed.nodes), len(expanded.nodes))
        self.assertTrue(collapsed.query_context["has_more"])
        self.assertTrue(any(node.display_name == "BackwardPass" for node in expanded.nodes))
        self.assertEqual(result.semantic.graph, original)

    def test_source_anchor_and_manual_position_survive_view_query(self):
        result = analyze_files({"run.py": (
            "import torch\ndef train(optimizer):\n"
            "    criterion = torch.nn.MSELoss()\n    optimizer.step()\n")})
        entry_id = result.training.entry_points[0].id
        state = ViewState(positions={entry_id: (123.0, 456.0)})
        view = build_visible_graph(result, ViewRequest(ViewKind.EXECUTION), state)
        self.assertEqual(view.layout_hints[entry_id], (123.0, 456.0))
        self.assertTrue(any(anchor.file_path == "run.py" and anchor.start_line == 4
                            for node in view.nodes for anchor in node.source_anchors))
        self.assertEqual(view.legend[0].type, EdgeType.CONTAINS)

    def test_large_model_is_bounded_and_hidden_members_remain_addressable(self):
        assignments = "".join(f"        self.layer_{index} = nn.Linear(2, 2)\n"
                              for index in range(120))
        result = analyze_files({"model.py": (
            "import torch.nn as nn\n"
            "class Net(nn.Module):\n"
            "    def __init__(self):\n" + assignments +
            "    def forward(self, x): return x\n"
        )})
        request = ViewRequest(ViewKind.ARCHITECTURE, max_nodes=10, max_edges=5)
        first = build_visible_graph(result, request)
        second = build_visible_graph(result, request)
        self.assertEqual(first, second)
        self.assertLessEqual(len(first.nodes), 10)
        self.assertLessEqual(len(first.edges), 5)
        self.assertEqual(len(first.aggregations), 1)
        self.assertGreater(first.hidden_counts["nodes"], 100)
        self.assertGreater(first.hidden_counts["edges"], 100)
        self.assertEqual(set(first.aggregations[0].member_ids),
                         {item.id for item in result.model.instances}
                         - {node.id for node in first.nodes})

    def test_selected_hidden_component_reveals_local_context_only(self):
        result = analyze_files({"model.py": (
            "import torch.nn as nn\n"
            "class Net(nn.Module):\n"
            "    def __init__(self):\n"
            "        self.a = nn.Linear(2, 2)\n"
            "        self.b = nn.Linear(2, 2)\n"
            "        self.c = nn.Linear(2, 2)\n"
            "    def forward(self, x): return self.c(x)\n"
        )})
        selected = next(item.id for item in result.model.instances if item.name == "c")
        view = build_visible_graph(result, ViewRequest(ViewKind.ARCHITECTURE,
                                                       selected_id=selected, max_nodes=3))
        self.assertIn(selected, {node.id for node in view.nodes})
        self.assertIn(result.model.definitions[0].symbol_id, view.root_ids)
        self.assertLessEqual(len(view.nodes), 3)

    def test_tight_budget_keeps_selected_nested_component(self):
        result = analyze_files({"model.py": (
            "import torch.nn as nn\n"
            "class Net(nn.Module):\n"
            "    def __init__(self):\n"
            "        self.layers = nn.ModuleList([nn.Linear(2, 2)])\n"
            "    def forward(self, x): return x\n"
        )})
        container = next(item for item in result.model.instances if item.name == "layers")
        selected = container.member_instance_ids[0]
        self.assertIsNotNone(selected)
        # Net -> layers -> member is depth two. Selecting a hidden member
        # cannot override a shallower structural query.
        shallow = build_visible_graph(result, ViewRequest(ViewKind.ARCHITECTURE,
                                                          selected_id=selected, max_nodes=2,
                                                          structure_depth=1))
        self.assertEqual({node.id for node in shallow.nodes},
                         {result.model.definitions[0].symbol_id, container.id})
        self.assertIsNone(shallow.query_context["selected_id"])
        self.assertFalse(shallow.query_context["budget_truncated"])
        view = build_visible_graph(result, ViewRequest(ViewKind.ARCHITECTURE,
                                                       selected_id=selected, max_nodes=3,
                                                       structure_depth=2))
        root = result.model.definitions[0].symbol_id
        self.assertEqual({node.id for node in view.nodes}, {root, container.id, selected})
        self.assertEqual(view.root_ids, (result.model.definitions[0].symbol_id,))
        self.assertEqual(len(view.nodes), 3)
        self.assertEqual({(edge.source, edge.target) for edge in view.edges
                          if edge.type is EdgeType.CONTAINS},
                         {(root, container.id), (container.id, selected)})
        self.assertEqual(view.query_context["selected_id"], selected)
        self.assertFalse(view.query_context["budget_truncated"])
        limited = build_visible_graph(result, ViewRequest(ViewKind.ARCHITECTURE,
                                                          selected_id=selected, max_nodes=2,
                                                          structure_depth=2))
        self.assertEqual({node.id for node in limited.nodes}, {root, container.id})
        self.assertEqual(limited.root_ids, (root,))
        self.assertEqual(len(limited.nodes), 2)
        self.assertEqual({(edge.source, edge.target) for edge in limited.edges},
                         {(root, container.id)})
        self.assertIsNone(limited.query_context["selected_id"])
        self.assertTrue(limited.query_context["budget_truncated"])
        self.assertTrue(limited.query_context["has_more"])
        self.assertEqual(limited.hidden_counts["nodes"], 1)

    def test_one_node_budget_keeps_selected_component(self):
        result = analyze_files({"model.py": (
            "import torch.nn as nn\n"
            "class Net(nn.Module):\n"
            "    def __init__(self): self.layer = nn.Linear(2, 2)\n"
            "    def forward(self, x): return x\n"
        )})
        selected = result.model.instances[0].id
        view = build_visible_graph(result, ViewRequest(ViewKind.ARCHITECTURE,
                                                       selected_id=selected, max_nodes=1))
        self.assertEqual(tuple(node.id for node in view.nodes), (selected,))
        self.assertTrue(view.query_context["has_more"])

    def test_edge_budget_preserves_execution_main_flow(self):
        result = analyze_files({"run.py": (
            "import torch\n"
            "def train(loss, optimizer):\n"
            "    criterion = torch.nn.MSELoss()\n"
            "    loss.backward()\n"
            "    optimizer.step()\n"
        )})
        view = build_visible_graph(result, ViewRequest(ViewKind.EXECUTION, max_edges=2))
        self.assertLessEqual(len(view.edges), 2)
        self.assertTrue(any(edge.type == EdgeType.CONTROL_FLOW for edge in view.edges))

    def test_critical_code_data_flow_is_visible_without_inventing_runtime_edges(self):
        result = analyze_files({
            "model.py": (
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def forward(self, features):\n"
                "        prediction = features\n"
                "        return prediction\n"
            ),
            "run.py": (
                "from model import Net\n"
                "def train(input, target):\n"
                "    model = Net()\n"
                "    prediction = input\n"
                "    loss = prediction + target\n"
                "    return loss\n"
            ),
        })
        architecture = build_visible_graph(result, ViewRequest(ViewKind.ARCHITECTURE))
        execution = build_visible_graph(result, ViewRequest(ViewKind.EXECUTION))
        for view in (architecture, execution):
            data_edges = [edge for edge in view.edges if edge.type == EdgeType.DATA_FLOW]
            self.assertTrue(data_edges)
            self.assertTrue(all(edge.source_anchors and edge.confidence.value == "Probable"
                                for edge in data_edges))
            self.assertTrue(all(node.source_anchors and node.code_node_ids
                                for node in view.nodes if node.role == "DataNode"))
        self.assertNotEqual({node.id for node in architecture.nodes},
                            {node.id for node in execution.nodes})

    def test_selected_data_one_hop_outranks_unrelated_layers_under_budget(self):
        result = analyze_files({"model.py": (
            "import torch.nn as nn\n"
            "class Net(nn.Module):\n"
            "    def __init__(self):\n"
            "        self.first = nn.Linear(2, 2)\n"
            "        self.second = nn.Linear(2, 2)\n"
            "    def forward(self, features):\n"
            "        prediction = features\n"
            "        return prediction\n"
        )})
        data = {node.name: node.id for node in result.flow.data_nodes}
        view = build_visible_graph(result, ViewRequest(ViewKind.ARCHITECTURE,
                                                       selected_id=data["features"],
                                                       hops=1, max_nodes=4))
        visible = {node.id for node in view.nodes}
        self.assertIn(data["features"], visible)
        self.assertIn(data["prediction"], visible)
        self.assertLessEqual(len(view.nodes), 4)

    def test_selected_training_step_reveals_only_requested_hops(self):
        result = analyze_files({"run.py": (
            "import torch\n"
            "def train(optimizer, loss, scheduler):\n"
            "    criterion = torch.nn.MSELoss()\n"
            "    for epoch in range(2):\n"
            "        optimizer.zero_grad()\n"
            "        loss.backward()\n"
            "        optimizer.step()\n"
            "        scheduler.step()\n"
        )})
        roles = {event.role.value: event.id for event in result.training.events}
        request = ViewRequest(ViewKind.EXECUTION,
                              selected_id=roles["ParameterUpdate"], hops=1)
        view = build_visible_graph(result, request)
        visible_ids = {node.id for node in view.nodes}
        self.assertIn(roles["BackwardPass"], visible_ids)
        self.assertIn(roles["LRUpdate"], visible_ids)
        self.assertNotIn(roles["ClearGradients"], visible_ids)

    def test_tight_edge_budget_keeps_selected_one_hop_connection(self):
        result = analyze_files({"run.py": (
            "import torch\n"
            "def train(optimizer, loss, scheduler):\n"
            "    criterion = torch.nn.MSELoss()\n"
            "    optimizer.zero_grad()\n"
            "    loss.backward()\n"
            "    optimizer.step()\n"
            "    scheduler.step()\n"
        )})
        roles = {event.role.value: event.id for event in result.training.events}
        view = build_visible_graph(result, ViewRequest(
            ViewKind.EXECUTION, selected_id=roles["LRUpdate"],
            hops=1, max_edges=2))
        self.assertTrue(any(edge.source == roles["ParameterUpdate"]
                            and edge.target == roles["LRUpdate"]
                            and edge.type == EdgeType.CONTROL_FLOW
                            for edge in view.edges))

    def test_invalid_root_and_unsupported_view_are_explicit(self):
        result = analyze_files({"run.py": (
            "import torch\ndef train(optimizer):\n"
            "    criterion = torch.nn.MSELoss()\n    optimizer.step()\n")})
        with self.assertRaises(ValueError):
            build_visible_graph(result, ViewRequest(ViewKind.ARCHITECTURE,
                                                    root_id="not-a-model"))
        with self.assertRaises(ValueError):
            build_visible_graph(result, ViewRequest(ViewKind.CALLS))
        with self.assertRaises(ValueError):
            ViewRequest(ViewKind.EXECUTION, max_nodes=0)
        with self.assertRaises(ValueError):
            ViewRequest(ViewKind.EXECUTION, max_nodes=2.5)
        with self.assertRaises(ValueError):
            ViewRequest(ViewKind.EXECUTION, hops=True)

    def test_repetition_does_not_look_like_shared_weights(self):
        result = analyze_files({"model.py": (
            "import torch.nn as nn\n"
            "class Net(nn.Module):\n"
            "    def __init__(self):\n"
            "        self.layers = nn.ModuleList([nn.Linear(2, 2) for _ in range(3)])\n"
            "        self.left = nn.Linear(2, 2)\n"
            "        self.right = self.left\n"
            "    def forward(self, x): return x\n"
        )})
        layers = next(item for item in result.model.instances if item.name == "layers")
        view = build_visible_graph(result, ViewRequest(ViewKind.ARCHITECTURE,
                                                       expanded_ids=(layers.id,)))
        repeated = next(node for node in view.nodes if node.display_name.endswith("×3"))
        shared = next(node for node in view.nodes if node.display_name == "right")
        self.assertIsNone(repeated.metadata["shared_with_id"])
        self.assertIsNotNone(shared.metadata["shared_with_id"])

    def test_execution_layout_is_stable_and_loop_back_does_not_inflate_depth(self):
        result = analyze_files({"run.py": (
            "import torch\n"
            "def train(optimizer, loss):\n"
            "    criterion = torch.nn.MSELoss()\n"
            "    for epoch in range(2):\n"
            "        loss.backward()\n"
            "        optimizer.step()\n"
        )})
        loop_id = next(event.id for event in result.training.events
                       if event.role.value == "EpochLoop")
        state = ViewState(expanded_ids=(loop_id,))
        view = build_visible_graph(result, ViewRequest(ViewKind.EXECUTION), state)
        self.assertEqual(view, build_visible_graph(result, ViewRequest(ViewKind.EXECUTION), state))
        self.assertTrue(any(item.type == EdgeType.LOOP_BACK for item in view.edges))
        self.assertTrue(all(abs(x) < 10000 and abs(y) < 10000
                            for x, y in view.layout_hints.values()))
        self.assertGreaterEqual(len({item.style for item in view.legend}), 2)


if __name__ == "__main__":
    unittest.main()
