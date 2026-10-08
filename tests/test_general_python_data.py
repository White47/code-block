"""Ordinary source-backed def-use facts and bounded DataFlow selection."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent

from codeviz.analysis.project import analyze_project
from codeviz.core import Confidence, EdgeType, NodeKind, SourceAnchor, ViewKind
from codeviz.views import ViewRequest, build_visible_graph


class GeneralPythonDataTests(unittest.TestCase):
    def analyze(self, source):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "app.py").write_text(dedent(source).lstrip("\n"), encoding="utf-8")
            result = analyze_project(root)
        self.assertTrue(all(file.was_parsed for file in result.files))
        self.assertIsNotNone(getattr(result, "flow", None))
        self.assertFalse(any(issue.code.endswith("_ERROR") for issue in result.issues))
        return result

    def symbol(self, result, qualified_name):
        matches = [entry for entry in result.symbol_index.entries.values()
                   if entry.definition and entry.qualified_name == qualified_name]
        self.assertEqual(len(matches), 1, f"expected definition {qualified_name}")
        return matches[0]

    def data_id(self, result, symbol):
        # Existing graph permits a Data node linked by symbol_id. A general
        # def-use graph may instead connect the indexed variable directly.
        proxies = [node.id for node in result.flow.graph.nodes.values()
                   if node.kind == NodeKind.DATA and node.metadata.get("symbol_id") == symbol.id]
        self.assertLessEqual(len(proxies), 1, "data proxy must disambiguate its lexical symbol")
        return proxies[0] if proxies else symbol.id

    def step(self, result, line, name):
        matches = [node for node in result.flow.graph.nodes.values()
                   if node.kind == NodeKind.FLOW_STEP and node.name == name
                   and node.source_anchors[0].start_line == line]
        self.assertEqual(len(matches), 1, f"expected {name} at line {line}")
        return matches[0]

    def assert_edge(self, result, source, target, kind, line):
        edges = [edge for edge in result.flow.graph.edges
                 if edge.source == source and edge.target == target and edge.type == kind]
        self.assertTrue(edges, f"ordinary def-use must retain {kind.value} at line {line}")
        self.assertTrue(any(isinstance(edge.source_anchor, SourceAnchor)
                            and edge.source_anchor.file_path == "app.py"
                            and edge.source_anchor.start_line == line for edge in edges))
        return edges

    def data_view(self, result, symbol):
        try:
            view = build_visible_graph(result, ViewRequest(
                ViewKind.DATA_FLOW, selected_id=symbol.id, hops=3,
                max_nodes=30, max_edges=60))
        except ValueError as error:
            self.fail(f"DataFlow must accept an ordinary variable/parameter symbol ID: {error}")
        represented = {item for node in view.nodes for item in node.code_node_ids}
        self.assertTrue({symbol.id, self.data_id(result, symbol)} & represented)
        self.assertTrue(view.edges, "ordinary selection must expose def-use relationships")
        self.assertLessEqual(len(view.nodes), 30)
        self.assertLessEqual(len(view.edges), 60)
        self.assertTrue(all(node.source_anchors or node.member_ids for node in view.nodes))
        self.assertTrue(all(edge.source_anchors for edge in view.edges))
        return view

    def test_plain_parameter_to_local_assignment_has_reads_writes_and_data_flow(self):
        result = self.analyze('''
            def normalize(raw):
                cleaned = raw
                return cleaned
        ''')
        raw = self.data_id(result, self.symbol(result, "app.normalize.raw"))
        cleaned = self.data_id(result, self.symbol(result, "app.normalize.cleaned"))
        assignment = self.step(result, 2, "Assign")
        self.assert_edge(result, assignment.id, raw, EdgeType.READS, 2)
        self.assert_edge(result, assignment.id, cleaned, EdgeType.WRITES, 2)
        self.assert_edge(result, raw, cleaned, EdgeType.DATA_FLOW, 2)

    def test_return_reads_plain_local_with_return_source_anchor(self):
        result = self.analyze('''
            def normalize(raw):
                cleaned = raw
                return cleaned
        ''')
        returned = self.step(result, 3, "Return")
        cleaned = self.data_id(result, self.symbol(result, "app.normalize.cleaned"))
        self.assert_edge(result, returned.id, cleaned, EdgeType.READS, 3)

    def test_data_flow_can_focus_plain_local_symbol_without_ml_role_names(self):
        result = self.analyze('''
            def normalize(raw):
                cleaned = raw
                return cleaned
        ''')
        cleaned = self.symbol(result, "app.normalize.cleaned")
        view = self.data_view(result, cleaned)
        lines = {anchor.start_line for node in view.nodes for anchor in node.source_anchors}
        self.assertTrue({1, 2, 3} <= lines, "context must connect parameter, write and return")

    def test_data_flow_can_focus_plain_parameter_symbol(self):
        result = self.analyze('''
            def normalize(raw):
                cleaned = raw
                return cleaned
        ''')
        self.data_view(result, self.symbol(result, "app.normalize.raw"))

    def test_branch_assignments_preserve_both_may_sources_at_the_merge(self):
        result = self.analyze('''
            def choose(flag, left, right):
                if flag:
                    chosen = left
                else:
                    chosen = right
                final = chosen
                return final
        ''')
        chosen = self.data_id(result, self.symbol(result, "app.choose.chosen"))
        left = self.data_id(result, self.symbol(result, "app.choose.left"))
        right = self.data_id(result, self.symbol(result, "app.choose.right"))
        for origin, line in ((left, 3), (right, 5)):
            with self.subTest(line=line):
                edges = self.assert_edge(result, origin, chosen, EdgeType.DATA_FLOW, line)
                self.assertTrue(all(edge.confidence == Confidence.PROBABLE for edge in edges),
                                "conditional origins describe possible flow, not runtime values")
        view = self.data_view(result, self.symbol(result, "app.choose.final"))
        represented = {item for node in view.nodes for item in node.code_node_ids}
        self.assertTrue({left, right} <= represented,
                        "downstream context must preserve both branch origins")

    def test_branch_origin_bridge_does_not_intersect_away_distinct_possible_sources(self):
        # Existing critical-data APIs already expose these endpoints; the plain
        # bridge makes loss of branch origins observable before general data lands.
        result = self.analyze('''
            def choose(flag, input, target):
                if flag:
                    bridge = input
                else:
                    bridge = target
                prediction = bridge
                return prediction
        ''')
        graph = result.flow.graph
        first = self.data_id(result, self.symbol(result, "app.choose.input"))
        second = self.data_id(result, self.symbol(result, "app.choose.target"))
        destination = self.data_id(result, self.symbol(result, "app.choose.prediction"))
        # Permit either direct origin propagation or an explicit bridge node.
        def reaches(source):
            seen = {source}
            frontier = [source]
            while frontier:
                current = frontier.pop()
                for edge in graph.edges:
                    if edge.type == EdgeType.DATA_FLOW and edge.source == current:
                        self.assertIsInstance(edge.source_anchor, SourceAnchor)
                        self.assertNotEqual(edge.confidence, Confidence.EXACT)
                        if edge.target not in seen:
                            seen.add(edge.target)
                            frontier.append(edge.target)
            return destination in seen

        for origin in (first, second):
            with self.subTest(origin=origin):
                self.assertTrue(reaches(origin), "both alternative origins must survive the merge")

    def test_same_named_locals_in_other_functions_do_not_leak_into_data_context(self):
        result = self.analyze('''
            def first(raw):
                cleaned = raw
                return cleaned
            def second(raw):
                cleaned = raw
                return cleaned
        ''')
        selected = self.symbol(result, "app.first.cleaned")
        other_symbols = [self.symbol(result, name)
                         for name in ("app.second.raw", "app.second.cleaned")]
        excluded = {item.id for item in other_symbols}
        excluded.update(self.data_id(result, item) for item in other_symbols)
        view = self.data_view(result, selected)
        represented = {item for node in view.nodes for item in node.code_node_ids}
        self.assertTrue(excluded.isdisjoint(represented), "def-use must respect lexical ownership")


if __name__ == "__main__":
    unittest.main()
