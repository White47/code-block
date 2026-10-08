"""Presentation-only vertical layout contracts for relation-specific views."""

import unittest
from copy import deepcopy
from itertools import permutations
from math import isfinite

from codeviz.core import EdgeType, ViewKind, VisibleEdge, VisibleNode
from codeviz.views import ViewState
from codeviz.views.layout import layout_positions


def nodes(*names: str) -> tuple[VisibleNode, ...]:
    return tuple(VisibleNode(name, name, code_node_ids=(name,),
                             metadata={"source_fact": name}) for name in names)


def edge(source: str, target: str, kind: EdgeType) -> VisibleEdge:
    return VisibleEdge(source, target, kind,
                       code_edge_ids=(f"{source}:{kind.value}:{target}",))


def positions(view: ViewKind, visible_nodes: tuple[VisibleNode, ...],
              edges: tuple[VisibleEdge, ...], roots: tuple[str, ...] = (),
              state: ViewState | None = None) -> dict[str, tuple[float, float]]:
    return layout_positions(visible_nodes, edges, roots, "directed",
                            state if state is not None else ViewState(), view_kind=view)


class VerticalLayoutRecoveryTests(unittest.TestCase):
    def assert_downward(self, points: dict[str, tuple[float, float]],
                        *path: str) -> None:
        for source, target in zip(path, path[1:], strict=False):
            self.assertLess(points[source][1], points[target][1],
                            f"{source} must be above {target}")

    def test_symbol_callers_are_above_callees_with_parallel_peers(self):
        visible = nodes("caller", "left", "right", "leaf")
        relations = (edge("caller", "left", EdgeType.CALLS),
                     edge("caller", "right", EdgeType.CALLS),
                     edge("left", "leaf", EdgeType.CALLS),
                     edge("right", "leaf", EdgeType.CALLS))
        points = positions(ViewKind.SYMBOL, visible, relations, ("caller",))
        self.assert_downward(points, "caller", "left", "leaf")
        self.assert_downward(points, "caller", "right", "leaf")
        self.assertEqual(points["left"][1], points["right"][1])
        self.assertNotEqual(points["left"][0], points["right"][0])

    def test_data_flow_uses_projected_write_and_read_direction(self):
        # READS is already binding -> use in VisibleEdge, unlike the code fact.
        visible = nodes("producer", "binding", "left_use", "right_use", "result")
        relations = (edge("producer", "binding", EdgeType.WRITES),
                     edge("binding", "left_use", EdgeType.READS),
                     edge("binding", "right_use", EdgeType.READS),
                     edge("left_use", "result", EdgeType.DATA_FLOW),
                     edge("right_use", "result", EdgeType.DATA_FLOW))
        points = positions(ViewKind.DATA_FLOW, visible, relations, ("producer",))
        self.assert_downward(points, "producer", "binding", "left_use", "result")
        self.assert_downward(points, "producer", "binding", "right_use", "result")
        self.assertEqual(points["left_use"][1], points["right_use"][1])
        self.assertNotEqual(points["left_use"][0], points["right_use"][0])

    def test_data_flow_selected_binding_does_not_flatten_its_upstream(self):
        visible = nodes("producer", "binding", "use")
        relations = (edge("producer", "binding", EdgeType.WRITES),
                     edge("binding", "use", EdgeType.READS))
        points = positions(ViewKind.DATA_FLOW, visible, relations, ("binding",))
        self.assert_downward(points, "producer", "binding", "use")

    def test_symbol_selected_callee_does_not_flatten_its_caller(self):
        visible = nodes("caller", "focus", "callee")
        relations = (edge("caller", "focus", EdgeType.CALLS),
                     edge("focus", "callee", EdgeType.CALLS))
        points = positions(ViewKind.SYMBOL, visible, relations, ("focus",))
        self.assert_downward(points, "caller", "focus", "callee")

    def test_symbol_display_relations_follow_their_visible_direction(self):
        visible = nodes("source", "middle", "target")
        for relation in (EdgeType.CONTAINS, EdgeType.IMPORTS, EdgeType.EXPORTS,
                         EdgeType.CALLS, EdgeType.INHERITS, EdgeType.IMPLEMENTS):
            with self.subTest(relation=relation):
                relations = (edge("source", "middle", relation),
                             edge("middle", "target", relation))
                points = positions(ViewKind.SYMBOL, visible, relations, ("middle",))
                self.assert_downward(points, "source", "middle", "target")

    def test_data_flow_parameter_and_return_edges_read_downward(self):
        visible = nodes("argument", "parameter", "return", "consumer")
        relations = (edge("argument", "parameter", EdgeType.PASSES_TO),
                     edge("parameter", "return", EdgeType.DATA_FLOW),
                     edge("return", "consumer", EdgeType.RETURNS_TO))
        points = positions(ViewKind.DATA_FLOW, visible, relations, ("argument",))
        self.assert_downward(points, "argument", "parameter", "return", "consumer")

    def test_architecture_uses_containment_not_control_or_calls(self):
        visible = nodes("module", "left", "right", "method")
        containment = (edge("module", "left", EdgeType.CONTAINS),
                       edge("module", "right", EdgeType.CONTAINS),
                       edge("left", "method", EdgeType.CONTAINS))
        expected = positions(ViewKind.ARCHITECTURE, visible, containment, ("module",))
        extra = (edge("method", "right", EdgeType.CONTROL_FLOW),
                 edge("right", "left", EdgeType.CALLS))
        actual = positions(ViewKind.ARCHITECTURE, visible, containment + extra, ("module",))
        self.assertEqual(actual, expected)
        self.assertEqual(actual["left"][1], actual["right"][1])
        self.assert_downward(actual, "module", "left", "method")

    def test_execution_and_function_flow_keep_parallel_branch_and_join(self):
        visible = nodes("condition", "left", "right", "join")
        forward = (edge("condition", "left", EdgeType.BRANCH_TRUE),
                   edge("condition", "right", EdgeType.BRANCH_FALSE),
                   edge("left", "join", EdgeType.CONTROL_FLOW),
                   edge("right", "join", EdgeType.CONTROL_FLOW))
        for view in (ViewKind.EXECUTION, ViewKind.FUNCTION_FLOW):
            with self.subTest(view=view):
                expected = positions(view, visible, forward, ("condition",))
                actual = positions(view, visible,
                                   forward + (edge("join", "left", EdgeType.LOOP_BACK),),
                                   ("condition",))
                self.assertEqual(actual, expected)
                self.assert_downward(actual, "condition", "left", "join")
                self.assert_downward(actual, "condition", "right", "join")
                self.assertEqual(actual["left"][1], actual["right"][1])
                self.assertNotEqual(actual["left"][0], actual["right"][0])

    def test_execution_and_function_flow_ignore_non_control_relations(self):
        visible = nodes("entry", "middle", "exit")
        forward = (edge("entry", "middle", EdgeType.CONTROL_FLOW),
                   edge("middle", "exit", EdgeType.CONTROL_FLOW))
        unrelated = (edge("exit", "entry", EdgeType.CALLS),
                     edge("exit", "middle", EdgeType.READS))
        for view in (ViewKind.EXECUTION, ViewKind.FUNCTION_FLOW):
            with self.subTest(view=view):
                self.assertEqual(positions(view, visible, forward, ("entry",)),
                                 positions(view, visible, forward + unrelated, ("entry",)))

    def test_execution_contains_and_root_pinning_remain_compatible(self):
        visible = nodes("root", "body", "tail")
        relations = (edge("root", "body", EdgeType.CONTROL_FLOW),
                     edge("body", "tail", EdgeType.CONTAINS),
                     edge("tail", "root", EdgeType.CONTROL_FLOW))
        for view in (ViewKind.EXECUTION, ViewKind.FUNCTION_FLOW):
            with self.subTest(view=view):
                points = positions(view, visible, relations, ("root",))
                self.assertEqual(points["root"][1], 0.0)
                self.assert_downward(points, "root", "body", "tail")

    def test_symbol_calls_are_not_ranked_by_unrelated_control_edges(self):
        visible = nodes("caller", "callee", "leaf")
        calls = (edge("caller", "callee", EdgeType.CALLS),
                 edge("callee", "leaf", EdgeType.CALLS))
        expected = positions(ViewKind.SYMBOL, visible, calls, ("caller",))
        actual = positions(ViewKind.SYMBOL, visible,
                           calls + (edge("leaf", "callee", EdgeType.CONTROL_FLOW),),
                           ("caller",))
        self.assertEqual(actual, expected)

    def test_cycles_are_bounded_stable_and_do_not_flatten_downstream(self):
        visible = nodes("a", "b", "tail")
        for view, relation in ((ViewKind.SYMBOL, EdgeType.CALLS),
                               (ViewKind.DATA_FLOW, EdgeType.DATA_FLOW),
                               (ViewKind.FUNCTION_FLOW, EdgeType.CONTROL_FLOW)):
            with self.subTest(view=view):
                relations = (edge("a", "b", relation), edge("b", "a", relation),
                             edge("b", "tail", relation))
                expected = positions(view, visible, relations)
                for node_order in permutations(visible):
                    for edge_order in permutations(relations):
                        self.assertEqual(positions(view, node_order, edge_order), expected)
                self.assertTrue(all(isfinite(value) for point in expected.values()
                                    for value in point))
                self.assertEqual(len(set(expected.values())), len(visible))
                self.assertGreater(expected["tail"][1],
                                   max(expected["a"][1], expected["b"][1]))

    def test_saved_manual_positions_override_all_view_policies_after_restore(self):
        visible = nodes("source", "target")
        relations = (edge("source", "target", EdgeType.CALLS),
                     edge("source", "target", EdgeType.DATA_FLOW),
                     edge("source", "target", EdgeType.CONTAINS),
                     edge("source", "target", EdgeType.CONTROL_FLOW))
        for view in (ViewKind.ARCHITECTURE, ViewKind.EXECUTION, ViewKind.FUNCTION_FLOW,
                     ViewKind.SYMBOL, ViewKind.DATA_FLOW):
            with self.subTest(view=view):
                manual = {"source": (812.0, 999.0), "target": (-51.0, -23.0)}
                state = ViewState(positions={"source": (1.0, 2.0)},
                                  positions_by_view={view.value: manual})
                restored = ViewState.from_dict(state.to_dict())
                self.assertEqual(positions(view, visible, relations, ("source",), restored),
                                 manual)

    def test_layout_does_not_change_facts_provenance_or_other_view_state(self):
        visible = nodes("source", "target")
        relations = (edge("source", "target", EdgeType.READS),)
        state = ViewState(positions_by_view={ViewKind.SYMBOL.value: {
            "source": (13.0, 21.0)}}, selected_id="source", expanded_ids=("target",))
        before = deepcopy((visible, relations, state))
        positions(ViewKind.DATA_FLOW, visible, relations, ("source",), state)
        self.assertEqual((visible, relations, state), before)
        self.assertEqual(state.positions_for(ViewKind.SYMBOL), {"source": (13.0, 21.0)})


if __name__ == "__main__":
    unittest.main()
