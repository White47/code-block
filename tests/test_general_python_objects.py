"""Bounded object/module facts from source; no fixture module is executed."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent

from codeviz.analysis.project import analyze_project
from codeviz.core import Confidence, EdgeType, NodeKind, SourceAnchor


class GeneralPythonObjectTests(unittest.TestCase):
    def analyze(self, files):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative, source in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(dedent(source).lstrip("\n"), encoding="utf-8")
            result = analyze_project(root)
        self.assertTrue(all(file.was_parsed for file in result.files))
        self.assertIsNotNone(getattr(result, "flow", None), "analysis must retain Code Graph")
        self.assertFalse(any(issue.code.endswith("_ERROR") for issue in result.issues))
        return result

    def symbol(self, result, qualified_name):
        matches = [entry for entry in result.symbol_index.entries.values()
                   if entry.qualified_name == qualified_name and entry.definition]
        self.assertEqual(len(matches), 1, f"expected definition {qualified_name}")
        return matches[0]

    def call(self, result, name, line, file_path="app.py"):
        graph = result.flow.graph
        sites = [node for node in graph.nodes.values()
                 if node.kind == NodeKind.CALL_SITE and node.name == name
                 and any(anchor.file_path == file_path and anchor.start_line == line
                         for anchor in node.source_anchors)]
        self.assertEqual(len(sites), 1, f"expected call {name} at {file_path}:{line}")
        edges = [edge for edge in graph.edges
                 if edge.type == EdgeType.CALLS and edge.source == sites[0].id]
        self.assertEqual(len(edges), 1, "call must retain a single bounded candidate relation")
        self.assertIsInstance(edges[0].source_anchor, SourceAnchor)
        self.assertEqual(edges[0].source_anchor.file_path, file_path)
        self.assertEqual(edges[0].source_anchor.start_line, line)
        return edges[0]

    def assert_candidates(self, result, edge, qualified_names):
        expected = {self.symbol(result, name).id for name in qualified_names}
        graph = result.flow.graph
        candidates = set(getattr(edge, "possible_targets", ()))
        if graph.nodes[edge.target].kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS):
            candidates.add(edge.target)
        self.assertEqual(candidates, expected, "candidates require receiver/assignment evidence")
        self.assertEqual(edge.confidence, Confidence.PROBABLE,
                         "candidate propagation must not claim runtime dispatch certainty")
        self.assertTrue(all(graph.nodes[item].source_anchors for item in candidates))

    def test_plain_local_module_import_has_an_imports_graph_edge(self):
        result = self.analyze({
            "helper.py": "def calculate(): return 1\n",
            "app.py": "import helper\nvalue = helper.calculate()\n",
        })
        consumer = self.symbol(result, "app")
        imported = self.symbol(result, "helper")
        alias = self.symbol(result, "app.helper")
        edges = [edge for edge in result.flow.graph.edges
                 if edge.type == EdgeType.IMPORTS and edge.target == imported.id
                 and edge.source in (consumer.id, alias.id)]
        self.assertTrue(edges, "local imports must materialize in the Code Graph")
        for edge in edges:
            self.assertIsInstance(edge.source_anchor, SourceAnchor)
            self.assertEqual((edge.source_anchor.file_path, edge.source_anchor.start_line),
                             ("app.py", 1))

    def test_relative_import_alias_edges_point_to_the_actual_local_definition(self):
        result = self.analyze({
            "pkg/__init__.py": "",
            "pkg/helper.py": "def calculate(): return 1\n",
            "pkg/app.py": "from .helper import calculate as run\nvalue = run()\n",
        })
        consumer = self.symbol(result, "pkg.app")
        alias = self.symbol(result, "pkg.app.run")
        target = self.symbol(result, "pkg.helper.calculate")
        edges = [edge for edge in result.flow.graph.edges
                 if edge.type == EdgeType.IMPORTS and edge.target == target.id
                 and edge.source in (consumer.id, alias.id)]
        self.assertTrue(edges, "aliased relative import must point at its local definition")
        self.assertTrue(all(isinstance(edge.source_anchor, SourceAnchor) for edge in edges))
        self.assertTrue(all(edge.source_anchor.file_path == "pkg/app.py"
                            and edge.source_anchor.start_line == 1 for edge in edges))

    def test_local_inheritance_has_a_source_backed_class_to_base_edge(self):
        result = self.analyze({
            "base.py": "class Base: pass\n",
            "app.py": "from base import Base as Parent\nclass Child(Parent): pass\n",
        })
        child = self.symbol(result, "app.Child")
        base = self.symbol(result, "base.Base")
        edges = [edge for edge in result.flow.graph.edges
                 if edge.type == EdgeType.INHERITS
                 and edge.source == child.id and edge.target == base.id]
        self.assertTrue(edges, "local base-class syntax must produce INHERITS")
        self.assertTrue(all(isinstance(edge.source_anchor, SourceAnchor) for edge in edges))
        self.assertTrue(all(edge.source_anchor.file_path == "app.py"
                            and edge.source_anchor.start_line == 2 for edge in edges))

    def test_constructed_receiver_disambiguates_duplicate_method_names_conservatively(self):
        result = self.analyze({"app.py": '''
            class Job:
                def run(self): return 1
            class Other:
                def run(self): return 2
            def start():
                job = Job()
                return job.run()
        '''})
        self.assert_candidates(result, self.call(result, "run", 7), {"app.Job.run"})

    def test_super_call_keeps_inherited_method_as_a_candidate(self):
        result = self.analyze({"app.py": '''
            class Base:
                def run(self): return 1
            class Other:
                def run(self): return 2
            class Child(Base):
                def run(self):
                    return super().run()
        '''})
        self.assert_candidates(result, self.call(result, "run", 7), {"app.Base.run"})

    def test_function_assignment_propagates_a_callable_candidate(self):
        result = self.analyze({"app.py": '''
            def helper(): return 1
            def unrelated(): return 2
            def start():
                fn = helper
                return fn()
        '''})
        self.assert_candidates(result, self.call(result, "fn", 5), {"app.helper"})

    def test_conditional_function_rebinding_preserves_both_callable_candidates(self):
        result = self.analyze({"app.py": '''
            def first(): return 1
            def second(): return 2
            def unrelated(): return 3
            def start(flag):
                fn = first
                if flag:
                    fn = second
                return fn()
        '''})
        self.assert_candidates(result, self.call(result, "fn", 8), {"app.first", "app.second"})

    def test_branch_constructed_receivers_preserve_both_method_candidates(self):
        result = self.analyze({"app.py": '''
            class First:
                def run(self): return 1
            class Second:
                def run(self): return 2
            class Unrelated:
                def run(self): return 3
            def start(flag):
                if flag:
                    job = First()
                else:
                    job = Second()
                return job.run()
        '''})
        self.assert_candidates(result, self.call(result, "run", 12),
                               {"app.First.run", "app.Second.run"})


if __name__ == "__main__":
    unittest.main()
