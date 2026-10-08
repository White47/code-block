"""Abrupt exits, finally continuations and source-backed match cases."""

import unittest
from textwrap import dedent
from unittest.mock import patch

from tests.test_flow import analyze_files

from codeviz.core import EdgeType, NodeKind, stable_node_id
from codeviz.semantic.training import TrainingRole

_CONTROL = {EdgeType.CONTROL_FLOW, EdgeType.BRANCH_TRUE,
            EdgeType.BRANCH_FALSE, EdgeType.LOOP_BACK}


class ControlExitSemanticsTests(unittest.TestCase):
    def test_finally_clones_keep_canonical_ml_loop_and_callpoint_provenance(self):
        source = ('import torch\ndef train(flag):\n'
                  '    optimizer = torch.optim.SGD([])\n'
                  '    try:\n        if flag: return None\n'
                  '    finally:\n        for epoch in []:\n            optimizer.step()\n')
        result = analyze_files({"app.py": source})
        graph = result.flow.graph
        clones = [node for node in graph.nodes.values() if node.metadata.get("canonical_step_id")]
        self.assertTrue(clones)
        loop = next(event for event in result.training.events if event.role == TrainingRole.EPOCH_LOOP)
        self.assertFalse(graph.nodes[loop.source_node_id].metadata.get("canonical_step_id"))
        update = next(event for event in result.training.events if event.role == TrainingRole.PARAMETER_UPDATE)
        parents = [graph.nodes[edge.source] for edge in graph.edges
                   if edge.type == EdgeType.CONTAINS and edge.target == update.source_node_id]
        self.assertEqual(len(parents), 1)
        self.assertFalse(parents[0].metadata.get("canonical_step_id"))
        self.assertEqual(parents[0].source_anchors[0].start_line, 8)
        shifted = analyze_files({"app.py": "\n" + source}).flow.graph
        self.assertEqual({node.id for node in clones}, {node.id for node in shifted.nodes.values()
                                                       if node.metadata.get("canonical_step_id")})
        self.assertTrue(all(edge.source_anchor for edge in graph.edges if edge.type in _CONTROL))

    def test_original_basic_if_loop_step_ids_are_preserved(self):
        graph = self.graph('''
            def run(items):
                value = 0
                for item in items:
                    if item:
                        value += item
                    else:
                        continue
                return value
        ''')
        owner = next(node for node in graph.nodes.values() if node.kind == NodeKind.FUNCTION)
        specs = [("Assign", 2, NodeKind.FLOW_STEP), ("For", 3, NodeKind.CONTROL_BLOCK),
                 ("If", 4, NodeKind.CONTROL_BLOCK), ("AugAssign", 5, NodeKind.FLOW_STEP),
                 ("Continue", 7, NodeKind.FLOW_STEP), ("Return", 8, NodeKind.FLOW_STEP)]
        for ordinal, (name, line, kind) in enumerate(specs):
            self.assertEqual(self.one(graph, name, line).id, stable_node_id(
                "app.py", f"{owner.qualified_name}.{name}", kind, owner.qualified_name,
                occurrence_key=f"scope:{owner.id}:step:{ordinal}",
            ))

    def test_continuation_budget_reports_partial_instead_of_merging_exit_classes(self):
        source = ('def run(flag):\n    try:\n        if flag: return 1\n'
                  '        work()\n    finally:\n        cleanup()\n    onward()\n')
        with patch("codeviz.analysis.control_flow._MAX_CONTINUATION_NODES", 0):
            result = analyze_files({"app.py": source})
        self.assertFalse(any(issue.code.endswith("_ERROR") for issue in result.issues))
        self.assertTrue(any(issue.code == "CFG_PARTIAL" and "budget" in issue.message
                            and issue.source_anchor for issue in result.issues))
        returned = self.one(result.flow.graph, "Return")
        self.assertFalse(any(node.name == "Expr" and node.source_anchors[0].start_line == 7
                             and node.id in self.reachable(result.flow.graph, returned.id)
                             for node in result.flow.graph.nodes.values()))

    def graph(self, source):
        result = analyze_files({"app.py": dedent(source).lstrip("\n")})
        self.assertFalse(any(issue.code.endswith("_ERROR") for issue in result.issues))
        return result.flow.graph

    def nodes(self, graph, name, line=None):
        return [node for node in graph.nodes.values()
                if node.kind in (NodeKind.FLOW_STEP, NodeKind.CONTROL_BLOCK)
                and node.name == name and (line is None or node.source_anchors[0].start_line == line)]

    def one(self, graph, name, line=None):
        nodes = self.nodes(graph, name, line)
        self.assertEqual(len(nodes), 1, (name, line))
        return nodes[0]

    def reachable(self, graph, source):
        found = {source}
        todo = [source]
        while todo:
            current = todo.pop()
            for edge in graph.edges:
                if edge.source == current and edge.type in _CONTROL and edge.target not in found:
                    found.add(edge.target)
                    todo.append(edge.target)
        return found

    def test_mixed_return_and_normal_paths_do_not_share_a_false_continuation(self):
        graph = self.graph('''
            def run(flag):
                try:
                    if flag:
                        return 1
                    work()
                finally:
                    cleanup()
                onward()
        ''')
        returned = self.one(graph, "Return")
        work = self.one(graph, "Expr", 5)
        onward = self.one(graph, "Expr", 8)
        cleanups = self.nodes(graph, "Expr", 7)
        self.assertTrue(any(node.id in self.reachable(graph, returned.id) for node in cleanups),
                        "return must first pass through its cleanup continuation")
        self.assertNotIn(onward.id, self.reachable(graph, returned.id))
        self.assertIn(onward.id, self.reachable(graph, work.id))
        self.assertEqual(len(cleanups), 2, "normal and return cleanup paths need separate contexts")

    def test_finally_return_overrides_break_and_bypasses_loop_else_and_onward(self):
        graph = self.graph('''
            def run(items):
                for item in items:
                    try:
                        break
                    finally:
                        return 1
                else:
                    exhausted()
                onward()
        ''')
        broken = self.one(graph, "Break")
        returned = self.one(graph, "Return")
        reachable = self.reachable(graph, broken.id)
        self.assertIn(returned.id, reachable)
        self.assertNotIn(self.one(graph, "Expr", 8).id, reachable)
        self.assertNotIn(self.one(graph, "Expr", 9).id, reachable)

    def test_finally_break_overrides_return_without_loop_else(self):
        graph = self.graph('''
            def run(items):
                for item in items:
                    try:
                        return item
                    finally:
                        break
                else:
                    exhausted()
                onward()
        ''')
        returned = self.one(graph, "Return")
        reachable = self.reachable(graph, returned.id)
        self.assertIn(self.one(graph, "Break").id, reachable)
        self.assertNotIn(self.one(graph, "Expr", 8).id, reachable)
        self.assertIn(self.one(graph, "Expr", 9).id, reachable)

    def test_finally_continue_overrides_return_and_loops_after_cleanup(self):
        graph = self.graph('''
            def run(items):
                for item in items:
                    try:
                        return item
                    finally:
                        cleanup()
                        continue
                    unreachable()
        ''')
        returned = self.one(graph, "Return")
        reachable = self.reachable(graph, returned.id)
        self.assertIn(self.one(graph, "Expr", 6).id, reachable)
        self.assertIn(self.one(graph, "Continue").id, reachable)
        self.assertIn(self.one(graph, "For").id, reachable)
        self.assertNotIn(self.one(graph, "Expr", 8).id, reachable)

    def test_nested_finally_preserves_raise_and_runs_both_cleanups_in_order(self):
        graph = self.graph('''
            def run(error):
                try:
                    try:
                        raise error
                    finally:
                        inner_cleanup()
                finally:
                    outer_cleanup()
                onward()
        ''')
        raised = self.one(graph, "Raise")
        inner = self.one(graph, "Expr", 6)
        outer = self.one(graph, "Expr", 8)
        self.assertIn(inner.id, self.reachable(graph, raised.id))
        self.assertIn(outer.id, self.reachable(graph, inner.id))
        self.assertNotIn(self.one(graph, "Expr", 9).id, self.reachable(graph, raised.id))

    def test_finally_raise_overrides_normal_path_without_ordinary_fallthrough(self):
        graph = self.graph('''
            def run(error):
                try:
                    work()
                finally:
                    raise error
                onward()
        ''')
        work = self.one(graph, "Expr", 3)
        raised = self.one(graph, "Raise")
        self.assertIn(raised.id, self.reachable(graph, work.id))
        self.assertNotIn(self.one(graph, "Expr", 6).id, self.reachable(graph, work.id))

    def test_loop_else_is_reached_by_exhaustion_but_not_break(self):
        graph = self.graph('''
            def run(items):
                for item in items:
                    if item:
                        break
                else:
                    exhausted()
                onward()
        ''')
        broken = self.one(graph, "Break")
        exhausted = self.one(graph, "Expr", 6)
        onward = self.one(graph, "Expr", 7)
        self.assertNotIn(exhausted.id, self.reachable(graph, broken.id))
        self.assertIn(onward.id, self.reachable(graph, broken.id))
        self.assertIn(exhausted.id, self.reachable(graph, self.one(graph, "For").id))

    def test_match_return_has_no_case_fallthrough_and_guard_has_false_path(self):
        graph = self.graph('''
            def choose(value):
                match value:
                    case 1 if enabled(value):
                        return 1
                    case _:
                        fallback()
                onward()
        ''')
        returned = self.one(graph, "Return")
        fallback = self.one(graph, "Expr", 6)
        onward = self.one(graph, "Expr", 7)
        conditions = [node for node in graph.nodes.values()
                      if node.kind == NodeKind.CONTROL_BLOCK and
                      node.source_anchors[0].start_line == 3]
        self.assertTrue(conditions)
        self.assertTrue(any("enabled" in str(node.metadata.get("condition")) for node in conditions))
        self.assertNotIn(fallback.id, self.reachable(graph, returned.id))
        self.assertNotIn(onward.id, self.reachable(graph, returned.id))
        self.assertIn(onward.id, self.reachable(graph, fallback.id))
        self.assertTrue(any(edge.source in {node.id for node in conditions}
                            and edge.type == EdgeType.BRANCH_FALSE
                            and fallback.id in self.reachable(graph, edge.target)
                            for edge in graph.edges))

    def test_case_capture_without_guard_is_irrefutable_not_a_false_branch(self):
        graph = self.graph('''
            def choose(value):
                match value:
                    case captured:
                        return captured
                unreachable()
        ''')
        match = self.one(graph, "Match")
        onward = self.one(graph, "Expr", 5)
        self.assertNotIn(onward.id, self.reachable(graph, match.id))
