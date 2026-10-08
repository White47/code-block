"""Source-anchored structural CFG paths, without runtime exception claims."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent

from codeviz.analysis.project import analyze_project
from codeviz.core import EdgeType, NodeKind, SourceAnchor

_CONTROL_EDGES = frozenset({EdgeType.CONTROL_FLOW, EdgeType.BRANCH_TRUE,
                            EdgeType.BRANCH_FALSE, EdgeType.LOOP_BACK})


class GeneralPythonControlTests(unittest.TestCase):
    def analyze(self, source):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "app.py").write_text(dedent(source).lstrip("\n"), encoding="utf-8")
            result = analyze_project(root)
        self.assertTrue(all(file.was_parsed for file in result.files))
        self.assertIsNotNone(getattr(result, "flow", None))
        self.assertFalse(any(issue.code.endswith("_ERROR") for issue in result.issues))
        return result.flow.graph

    def nodes_at(self, graph, line, name=None):
        return [node for node in graph.nodes.values()
                if node.kind in (NodeKind.FLOW_STEP, NodeKind.CONTROL_BLOCK)
                and (name is None or node.name == name)
                and any(anchor.file_path == "app.py" and anchor.start_line == line
                        for anchor in node.source_anchors)]

    def node_at(self, graph, line, name):
        nodes = self.nodes_at(graph, line, name)
        self.assertEqual(len(nodes), 1, f"expected CFG {name} anchored at line {line}")
        return nodes[0]

    def control_edges(self, graph):
        edges = [edge for edge in graph.edges if edge.type in _CONTROL_EDGES]
        self.assertTrue(all(isinstance(edge.source_anchor, SourceAnchor) for edge in edges))
        return edges

    def reachable(self, graph, source, blocked=()):
        blocked = set(blocked)
        seen = set() if source in blocked else {source}
        frontier = list(seen)
        edges = self.control_edges(graph)
        while frontier:
            current = frontier.pop()
            for edge in edges:
                if edge.source == current and edge.target not in seen and edge.target not in blocked:
                    seen.add(edge.target)
                    frontier.append(edge.target)
        return seen

    def assert_cleanup_before_exit(self, graph, abrupt, cleanup):
        self.assertIn(cleanup.id, self.reachable(graph, abrupt.id),
                      "abrupt exit must first enter the finally cleanup")
        before_cleanup = self.reachable(graph, abrupt.id, blocked=(cleanup.id,))
        edges = self.control_edges(graph)
        premature_exits = [node_id for node_id in before_cleanup
                           if not any(edge.source == node_id for edge in edges)]
        self.assertEqual(premature_exits, [], "exit must not bypass finally cleanup")

    def assert_unreachable_line(self, graph, source, line):
        reachable = self.reachable(graph, source.id)
        targets = self.nodes_at(graph, line)
        # An implementation may omit unreachable statements altogether.
        self.assertTrue(all(node.id not in reachable for node in targets),
                        f"no control path may continue to unreachable line {line}")

    def test_match_case_conditions_connect_source_backed_case_bodies(self):
        graph = self.analyze('''
            def choose(subject):
                match subject:
                    case 1 if subject:
                        chosen = "one"
                    case _:
                        chosen = "other"
                return chosen
        ''')
        first_body = self.node_at(graph, 4, "Assign")
        fallback_body = self.node_at(graph, 6, "Assign")
        returned = self.node_at(graph, 7, "Return")
        conditions = [node for node in self.nodes_at(graph, 3)
                      if node.kind == NodeKind.CONTROL_BLOCK]
        self.assertTrue(conditions, "case pattern/guard needs a source-backed condition node")
        edges = self.control_edges(graph)
        condition_ids = {node.id for node in conditions}
        self.assertTrue(any(edge.source in condition_ids and edge.type == EdgeType.BRANCH_TRUE
                            and first_body.id in self.reachable(graph, edge.target)
                            for edge in edges), "successful case condition must enter its body")
        self.assertTrue(any(edge.source in condition_ids and edge.type == EdgeType.BRANCH_FALSE
                            and fallback_body.id in self.reachable(graph, edge.target)
                            for edge in edges), "failed case condition must reach fallback case")
        self.assertIn(returned.id, self.reachable(graph, first_body.id))
        self.assertIn(returned.id, self.reachable(graph, fallback_body.id))
        self.assertNotIn(fallback_body.id, self.reachable(graph, first_body.id),
                         "successful case must not fall through into the next body")

    def test_return_enters_finally_cleanup_and_does_not_resume_unreachable_code(self):
        graph = self.analyze('''
            def run(value):
                try:
                    return value
                finally:
                    cleanup()
                unreachable()
        ''')
        returned = self.node_at(graph, 3, "Return")
        cleanup = self.node_at(graph, 5, "Expr")
        self.assert_cleanup_before_exit(graph, returned, cleanup)
        self.assert_unreachable_line(graph, returned, 6)
        self.assert_unreachable_line(graph, cleanup, 6)

    def test_explicit_raise_enters_finally_cleanup_without_normal_fallthrough(self):
        graph = self.analyze('''
            def run(error):
                try:
                    raise error
                finally:
                    cleanup()
                unreachable()
        ''')
        raised = self.node_at(graph, 3, "Raise")
        cleanup = self.node_at(graph, 5, "Expr")
        self.assert_cleanup_before_exit(graph, raised, cleanup)
        self.assert_unreachable_line(graph, raised, 6)
        self.assert_unreachable_line(graph, cleanup, 6)

    def test_normal_try_runs_finally_before_the_following_statement(self):
        graph = self.analyze('''
            def run(value):
                try:
                    copied = value
                finally:
                    cleanup()
                onward()
        ''')
        body = self.node_at(graph, 3, "Assign")
        cleanup = self.node_at(graph, 5, "Expr")
        onward = self.node_at(graph, 6, "Expr")
        self.assertIn(cleanup.id, self.reachable(graph, body.id))
        self.assertIn(onward.id, self.reachable(graph, cleanup.id))
        self.assertNotIn(onward.id, self.reachable(graph, body.id, blocked=(cleanup.id,)),
                         "normal try must not skip finally on its way onward")

    def test_break_runs_finally_before_loop_exit(self):
        graph = self.analyze('''
            def run(items):
                for item in items:
                    try:
                        break
                    finally:
                        cleanup()
                    unreachable()
                onward()
        ''')
        broken = self.node_at(graph, 4, "Break")
        cleanup = self.node_at(graph, 6, "Expr")
        onward = self.node_at(graph, 8, "Expr")
        self.assert_cleanup_before_exit(graph, broken, cleanup)
        self.assertIn(onward.id, self.reachable(graph, cleanup.id))
        self.assertNotIn(onward.id, self.reachable(graph, broken.id, blocked=(cleanup.id,)),
                         "break must not bypass finally to reach the loop exit")
        self.assert_unreachable_line(graph, broken, 7)
        self.assert_unreachable_line(graph, cleanup, 7)

    def test_continue_runs_finally_before_loop_back(self):
        graph = self.analyze('''
            def run(items):
                for item in items:
                    try:
                        continue
                    finally:
                        cleanup()
                    unreachable()
                onward()
        ''')
        continued = self.node_at(graph, 4, "Continue")
        cleanup = self.node_at(graph, 6, "Expr")
        loop = self.node_at(graph, 2, "For")
        self.assertIn(cleanup.id, self.reachable(graph, continued.id),
                      "continue must execute finally before the next iteration")
        self.assertIn(loop.id, self.reachable(graph, cleanup.id))
        self.assertNotIn(loop.id, self.reachable(graph, continued.id, blocked=(cleanup.id,)),
                         "continue must not take a loop-back edge that bypasses cleanup")
        self.assertTrue(any(edge.type == EdgeType.LOOP_BACK and edge.target == loop.id
                            and edge.source in self.reachable(graph, cleanup.id)
                            for edge in self.control_edges(graph)))
        self.assert_unreachable_line(graph, continued, 7)
        self.assert_unreachable_line(graph, cleanup, 7)


if __name__ == "__main__":
    unittest.main()
