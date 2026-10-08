"""Source-backed import dependencies and base-class syntax."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.analysis.project import analyze_project
from codeviz.core import Confidence, EdgeType, NodeKind
from codeviz.ui.controller import AnalysisStatus, AppController


def analyze(files):
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        for relative, source in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        return analyze_project(root)


class RelationTests(unittest.TestCase):
    def test_import_work_partial_survives_relation_and_caller_with_source_trace(self):
        with patch("codeviz.analysis.resolution.MAX_IMPORT_WORK", 1):
            result = analyze({
                "root.py": "def run(): pass\nfrom tail import run\n",
                "tail.py": "def run(): pass\n",
                "app.py": "from root import run as fn\nfn()\ndef healthy(): pass\nhealthy()\n",
            })
        graph = result.flow.graph
        imports = [edge for edge in graph.edges if edge.type == EdgeType.IMPORTS
                   and edge.source_anchor.file_path == "app.py"]
        self.assertTrue(imports)
        self.assertTrue(all(edge.metadata.get("unknown") and edge.metadata.get("truncated")
                            for edge in imports))
        call = next(edge for edge in graph.edges if edge.type == EdgeType.CALLS
                    and edge.source_anchor.file_path == "app.py" and edge.source_anchor.start_line == 2)
        self.assertIn("root.run", {graph.nodes[target].qualified_name for target in call.possible_targets})
        self.assertTrue(call.metadata.get("unknown"))
        self.assertTrue(call.metadata.get("truncated"))
        self.assertEqual(AppController._result_status(result), AnalysisStatus.PARTIAL)
        self.assertTrue(any(issue.code == "IMPORT_WORK_BUDGET_PARTIAL" and issue.source_anchor
                            and issue.source_anchor.file_path == "app.py" for issue in result.issues))

    def test_import_work_partial_base_does_not_claim_candidate_count_overflow(self):
        with patch("codeviz.analysis.resolution.MAX_IMPORT_WORK", 1):
            result = analyze({
                "root.py": "class Base:\n    def run(self): pass\nfrom tail import Base\n",
                "tail.py": "class Base: pass\n",
                "app.py": "from root import Base\nclass Child(Base): pass\nChild().run()\n",
            })
        graph = result.flow.graph
        edge = next(edge for edge in graph.edges if edge.type == EdgeType.INHERITS)
        self.assertEqual(graph.nodes[edge.target].qualified_name, "root.Base")
        self.assertTrue(edge.metadata.get("truncated"))
        self.assertTrue(edge.metadata.get("unknown"))
        self.assertNotIn("RELATION_CANDIDATE_BUDGET", {issue.code for issue in result.issues})
        self.assertTrue(any(issue.code == "IMPORT_WORK_BUDGET_PARTIAL" and issue.source_anchor
                            and issue.source_anchor.file_path == "app.py"
                            and issue.source_anchor.start_line == 2 for issue in result.issues))

    def test_dotted_import_depends_on_submodule_with_alias_and_raw_path(self):
        result = analyze({"pkg/__init__.py": "", "pkg/sub.py": "",
                          "app.py": "import pkg.sub\n"})
        graph = result.flow.graph
        edge = next((edge for edge in graph.edges if edge.type == EdgeType.IMPORTS
                     and graph.nodes[edge.target].qualified_name == "pkg.sub"), None)
        self.assertIsNotNone(edge, "dependency must target imported submodule, not root binding")
        self.assertEqual(graph.nodes[edge.source].qualified_name, "app")
        self.assertEqual(edge.confidence, Confidence.EXACT)
        self.assertEqual(edge.metadata.get("raw_path"), "pkg.sub")
        self.assertEqual(edge.metadata.get("alias"), "pkg")
        self.assertEqual((edge.source_anchor.file_path, edge.source_anchor.start_line), ("app.py", 1))

    def test_function_import_owner_is_function_not_module(self):
        result = analyze({"helper.py": "", "app.py": "def start():\n    import helper as h\n"})
        graph = result.flow.graph
        edges = [edge for edge in graph.edges if edge.type == EdgeType.IMPORTS]
        self.assertEqual(len(edges), 1)
        self.assertEqual(graph.nodes[edges[0].source].qualified_name, "app.start")

    def test_external_base_is_probable_and_dynamic_base_is_reported(self):
        result = analyze({"app.py": "from remote import Base\nclass Child(Base): pass\n"
                          "class Dynamic(factory()): pass\n"})
        graph = result.flow.graph
        edges = [edge for edge in graph.edges if edge.type == EdgeType.INHERITS]
        self.assertEqual(len(edges), 1)
        self.assertEqual(graph.nodes[edges[0].target].kind, NodeKind.EXTERNAL_SYMBOL)
        self.assertEqual(edges[0].confidence, Confidence.PROBABLE)
        self.assertIn("DYNAMIC_BASE_UNRESOLVED", {issue.code for issue in result.issues})
        self.assertFalse(any(edge.type == EdgeType.EXPORTS for edge in graph.edges))

    def test_large_import_and_base_candidate_sets_report_truncation(self):
        bases = "\n".join("class Base: pass" for _ in range(17))
        result = analyze({"bases.py": bases,
                          "app.py": "from bases import Base\nclass Child(Base): pass\n"})
        graph = result.flow.graph
        for kind in (EdgeType.IMPORTS, EdgeType.INHERITS):
            edges = [edge for edge in graph.edges if edge.type == kind
                     and edge.source_anchor.file_path == "app.py"]
            self.assertEqual(len(edges), 16)
            self.assertTrue(all(edge.metadata.get("truncated") and edge.metadata.get("unknown")
                                and edge.confidence == Confidence.PROBABLE for edge in edges))
        self.assertIn("RELATION_CANDIDATE_BUDGET", {issue.code for issue in result.issues})

    def test_cyclic_reexports_do_not_create_fake_targets(self):
        result = analyze({"a.py": "from b import value\n", "b.py": "from a import value\n",
                          "unrelated.py": "def value(): pass\n"})
        self.assertIn("IMPORT_CYCLE", {issue.code for issue in result.issues})
        self.assertFalse(any(edge.type == EdgeType.IMPORTS for edge in result.flow.graph.edges))

    def test_long_reexport_chain_reports_budget_without_fake_external_target(self):
        files = {f"m{i}.py": f"from m{i + 1} import value\n" for i in range(18)}
        files["m18.py"] = "def value(): pass\n"
        result = analyze(files)
        self.assertIn("IMPORT_BUDGET", {issue.code for issue in result.issues})
        graph = result.flow.graph
        self.assertFalse(any(edge.type == EdgeType.IMPORTS
                             and edge.source_anchor.file_path == "m0.py" for edge in graph.edges))

    def test_missing_local_import_does_not_match_unrelated_global_name(self):
        result = analyze({"pkg/__init__.py": "", "other.py": "def missing(): pass\n",
                          "app.py": "from pkg import missing\n"})
        self.assertFalse(any(edge.type == EdgeType.IMPORTS for edge in result.flow.graph.edges))

    def test_dotted_import_receiver_shadowing_does_not_leak_static_member_target(self):
        result = analyze({"pkg/__init__.py": "", "pkg/sub.py": "def run(): pass\n",
                          "app.py": "import pkg.sub\npkg = unknown\npkg.sub.run()\n"})
        graph = result.flow.graph
        edge = next(edge for edge in graph.edges if edge.type == EdgeType.CALLS)
        self.assertEqual(edge.confidence, Confidence.UNRESOLVED)
        self.assertFalse(edge.possible_targets)

    def test_partial_reexport_cycle_keeps_known_target_but_marks_unknown(self):
        result = analyze({"a.py": "from b import value\ndef value(): pass\n",
                          "b.py": "from a import value\n",
                          "app.py": "from b import value\n"})
        edges = [edge for edge in result.flow.graph.edges if edge.type == EdgeType.IMPORTS
                 and edge.source_anchor.file_path == "app.py"]
        self.assertTrue(edges)
        self.assertTrue(all(edge.metadata.get("unknown") for edge in edges))
        self.assertTrue(all(edge.confidence == Confidence.PROBABLE for edge in edges))

    def test_repeated_import_retains_each_source_occurrence(self):
        result = analyze({"helper.py": "", "app.py": "import helper\nimport helper\n"})
        edges = [edge for edge in result.flow.graph.edges if edge.type == EdgeType.IMPORTS]
        self.assertEqual({edge.source_anchor.start_line for edge in edges}, {1, 2})
        self.assertTrue(all(edge.confidence == Confidence.EXACT for edge in edges))

    def test_global_import_uses_module_binding_for_call_position_value(self):
        result = analyze({"helper.py": "def work(): pass\n",
                          "app.py": "def start():\n    global fn\n"
                          "    from helper import work as fn\n    fn()\n"})
        graph = result.flow.graph
        edge = next(edge for edge in graph.edges if edge.type == EdgeType.CALLS)
        targets = {graph.nodes[target].qualified_name for target in (edge.target, *edge.possible_targets)}
        self.assertIn("helper.work", targets)
        self.assertFalse(edge.metadata.get("may_unbound"))
        binding = next(entry for entry in result.symbol_index.entries.values()
                       if entry.name == "fn" and "imported_from" in entry.metadata)
        self.assertEqual(result.symbol_index.entries[binding.parent_symbol].qualified_name, "app")

    def test_module_member_reexport_cycle_keeps_unknown_call_metadata(self):
        result = analyze({"a.py": "from b import fn\ndef fn(): pass\n",
                          "b.py": "from a import fn\n",
                          "app.py": "import b\nb.fn()\n"})
        graph = result.flow.graph
        edge = next(edge for edge in graph.edges if edge.type == EdgeType.CALLS)
        self.assertTrue(edge.possible_targets)
        self.assertTrue(edge.metadata.get("unknown"))
        self.assertEqual(edge.confidence, Confidence.PROBABLE)

    def test_partial_import_cycle_base_retains_known_method_and_unknown(self):
        result = analyze({
            "a.py": "from b import Base\nclass Base:\n    def run(self): pass\n",
            "b.py": "from a import Base\n",
            "app.py": "from b import Base\nclass Child(Base): pass\nChild().run()\n",
        })
        graph = result.flow.graph
        child = next(node for node in graph.nodes.values() if node.qualified_name == "app.Child")
        inheritance = next(edge for edge in graph.edges
                           if edge.type == EdgeType.INHERITS and edge.source == child.id)
        call = next(edge for edge in graph.edges if edge.type == EdgeType.CALLS
                    and edge.source_anchor.file_path == "app.py"
                    and graph.nodes[edge.target].qualified_name == "a.Base.run")
        self.assertTrue(inheritance.metadata.get("unknown"))
        self.assertTrue(child.metadata.get("inheritance_partial"))
        self.assertEqual(inheritance.confidence, Confidence.PROBABLE)
        self.assertEqual(graph.nodes[inheritance.target].qualified_name, "a.Base")
        self.assertTrue(call.metadata.get("unknown"))
        self.assertIn(call.target, call.possible_targets)
        self.assertEqual(call.confidence, Confidence.PROBABLE)
        self.assertTrue(any(issue.code == "IMPORT_CYCLE" and issue.source_anchor
                            and issue.source_anchor.file_path == "app.py"
                            and issue.source_anchor.start_line == 2 for issue in result.issues))

    def test_complete_probable_bases_do_not_gain_unknown(self):
        result = analyze({
            "bases.py": "class Base:\n    def run(self): pass\n"
                        "class Base:\n    def run(self): pass\n",
            "app.py": "from bases import Base\nclass Child(Base): pass\nChild().run()\n",
        })
        graph = result.flow.graph
        child = next(node for node in graph.nodes.values() if node.qualified_name == "app.Child")
        inheritance = [edge for edge in graph.edges
                       if edge.type == EdgeType.INHERITS and edge.source == child.id]
        self.assertEqual(len(inheritance), 2)
        self.assertTrue(all(edge.confidence == Confidence.PROBABLE for edge in inheritance))
        self.assertFalse(any(edge.metadata.get("unknown") for edge in inheritance))
        self.assertFalse(child.metadata.get("inheritance_partial"))
        call = next(edge for edge in graph.edges if edge.type == EdgeType.CALLS
                    and graph.nodes[edge.target].qualified_name == "bases.Base.run")
        self.assertEqual(len(call.possible_targets), 2)
        self.assertFalse(call.metadata.get("unknown"))

    def test_qualified_partial_cycle_base_retains_known_method_and_unknown(self):
        result = analyze({
            "a.py": "from b import Base\nclass Base:\n    def run(self): pass\n",
            "b.py": "from a import Base\n",
            "app.py": "import b\nclass Child(b.Base): pass\nChild().run()\n",
        })
        graph = result.flow.graph
        child = next(node for node in graph.nodes.values() if node.qualified_name == "app.Child")
        inheritance = next(edge for edge in graph.edges
                           if edge.type == EdgeType.INHERITS and edge.source == child.id)
        self.assertEqual(graph.nodes[inheritance.target].qualified_name, "a.Base")
        self.assertTrue(inheritance.metadata.get("unknown"))
        self.assertTrue(child.metadata.get("inheritance_partial"))
        call = next(edge for edge in graph.edges if edge.type == EdgeType.CALLS
                    and graph.nodes[edge.target].qualified_name == "a.Base.run")
        self.assertTrue(call.metadata.get("unknown"))
        self.assertEqual(call.confidence, Confidence.PROBABLE)
        self.assertTrue(any(issue.code == "IMPORT_CYCLE" and issue.source_anchor
                            and issue.source_anchor.file_path == "app.py"
                            and issue.source_anchor.start_line == 2 for issue in result.issues))

    def test_qualified_complete_probable_bases_do_not_gain_unknown(self):
        result = analyze({
            "bases.py": "class Base:\n    def run(self): pass\n"
                        "class Base:\n    def run(self): pass\n",
            "app.py": "import bases\nclass Child(bases.Base): pass\nChild().run()\n",
        })
        graph = result.flow.graph
        inheritance = [edge for edge in graph.edges if edge.type == EdgeType.INHERITS]
        self.assertEqual(len(inheritance), 2)
        self.assertTrue(all(edge.confidence == Confidence.PROBABLE
                            and not edge.metadata.get("unknown") for edge in inheritance))
        call = next(edge for edge in graph.edges if edge.type == EdgeType.CALLS
                    and graph.nodes[edge.target].qualified_name == "bases.Base.run")
        self.assertEqual(len(call.possible_targets), 2)
        self.assertFalse(call.metadata.get("unknown"))

    def test_nested_global_qualified_cycle_base_keeps_partial(self):
        result = analyze({
            "a.py": "from b import Base\nclass Base:\n    def run(self): pass\n",
            "b.py": "from a import Base\n",
            "app.py": "import b\ndef build():\n    global b\n"
                      "    class Child(b.Base): pass\n    Child().run()\n",
        })
        graph = result.flow.graph
        inheritance = next(edge for edge in graph.edges if edge.type == EdgeType.INHERITS)
        self.assertTrue(inheritance.metadata.get("unknown"))
        self.assertEqual(graph.nodes[inheritance.target].qualified_name, "a.Base")
        call = next(edge for edge in graph.edges if edge.type == EdgeType.CALLS
                    and graph.nodes[edge.target].qualified_name == "a.Base.run")
        self.assertTrue(call.metadata.get("unknown"))

    def test_nested_global_cycle_base_uses_lexical_binding_owner(self):
        result = analyze({
            "a.py": "from b import Base\nclass Base:\n    def run(self): pass\n",
            "b.py": "from a import Base\n",
            "app.py": "from b import Base\ndef build():\n    global Base\n"
                      "    class Child(Base): pass\n    Child().run()\n",
        })
        graph = result.flow.graph
        inheritance = next(edge for edge in graph.edges if edge.type == EdgeType.INHERITS)
        self.assertTrue(inheritance.metadata.get("unknown"))
        call = next(edge for edge in graph.edges if edge.type == EdgeType.CALLS
                    and graph.nodes[edge.target].qualified_name == "a.Base.run")
        self.assertTrue(call.metadata.get("unknown"))

    def test_local_base_shadow_does_not_inherit_unrelated_import_cycle(self):
        result = analyze({
            "a.py": "from b import Base\nclass Base:\n    def run(self): pass\n",
            "b.py": "from a import Base\n",
            "app.py": "from b import Base\ndef build():\n    class Base:\n"
                      "        def run(self): pass\n    class Child(Base): pass\n"
                      "    Child().run()\n",
        })
        graph = result.flow.graph
        inheritance = next(edge for edge in graph.edges if edge.type == EdgeType.INHERITS)
        self.assertFalse(inheritance.metadata.get("unknown"))
        self.assertEqual(inheritance.confidence, Confidence.EXACT)
        call = next(edge for edge in graph.edges if edge.type == EdgeType.CALLS
                    and graph.nodes[edge.target].qualified_name == "app.build.Base.run")
        self.assertFalse(call.metadata.get("unknown"))


if __name__ == "__main__":
    unittest.main()
