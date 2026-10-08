"""Call-position candidates must not replace lexical binding facts."""

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent
from unittest.mock import patch

from codeviz.analysis.project import analyze_project
from codeviz.analysis.value_candidates import ValueCandidates
from codeviz.core import CodeGraph, Confidence, EdgeType, NodeKind
from codeviz.ui.controller import AnalysisStatus, AppController


def analyze(source):
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "app.py").write_text(dedent(source).lstrip("\n"), encoding="utf-8")
        return analyze_project(root)


def call_at(result, line, name):
    graph = result.flow.graph
    site = next(node for node in graph.nodes.values()
                if node.kind == NodeKind.CALL_SITE and node.name == name
                and node.source_anchors[0].start_line == line)
    return next(edge for edge in graph.edges
                if edge.source == site.id and edge.type == EdgeType.CALLS)


def candidate_names(result, edge):
    return {result.flow.graph.nodes[target].qualified_name for target in edge.possible_targets}


def nested_value_loops(depth):
    source = "def f(): pass\ndef g(): pass\ndef start(flag):\n"
    for level in range(depth):
        source += "    " * (level + 1) + "x=f\n"
        source += "    " * (level + 1) + "while flag:\n"
    return source + "    " * (depth + 1) + "x=g\n    x()\n"


class ValueCandidateTests(unittest.TestCase):
    def test_nested_loops_share_actual_statement_expression_budget(self):
        original = ValueCandidates._expr
        for depth in (8, 10, 12):
            with self.subTest(depth=depth):
                visits = 0

                def counted(visitor, *args, **kwargs):
                    nonlocal visits
                    visits += 1
                    return original(visitor, *args, **kwargs)

                with patch("codeviz.analysis.value_candidates.MAX_VALUE_FILE_WORK", 128), \
                        patch.object(ValueCandidates, "_expr", counted):
                    result = analyze(nested_value_loops(depth))
                self.assertLessEqual(visits, 129, "repeated expr entry visits must stop, not just each loop")
                edge = call_at(result, 2 * depth + 5, "x")
                self.assertIn("app.f", candidate_names(result, edge))
                self.assertTrue(edge.metadata.get("unknown"))
                self.assertTrue(edge.metadata.get("truncated"))
                self.assertEqual(edge.confidence, Confidence.PROBABLE)
                self.assertEqual(AppController._result_status(result), AnalysisStatus.PARTIAL)
                self.assertTrue(any(issue.code == "VALUE_FILE_WORK_BUDGET_PARTIAL" and issue.source_anchor
                                    and issue.source_anchor.file_path == "app.py" for issue in result.issues))

    def test_work_cut_does_not_allow_later_scope_exact_fallback_and_survives_cache(self):
        source = nested_value_loops(6) + "def later():\n    f()\n"
        with patch("codeviz.analysis.value_candidates.MAX_VALUE_FILE_WORK", 64):
            result = analyze(source)
        cached = replace(result, flow=replace(result.flow, graph=CodeGraph.from_dict(
                         result.flow.graph.to_dict())),
                         files=tuple(replace(file, tree=None) for file in result.files))
        for analysis in (result, cached):
            edge = call_at(analysis, 19, "f")
            self.assertNotEqual(edge.confidence, Confidence.EXACT)
            self.assertTrue(edge.metadata.get("unknown"))
            self.assertTrue(edge.metadata.get("truncated"))
            self.assertEqual(edge.source_anchor.start_line, 19)
            self.assertEqual(AppController._result_status(analysis), AnalysisStatus.PARTIAL)

    def test_value_work_budget_is_file_local_and_simple_alias_stays_complete(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "app.py").write_text(nested_value_loops(6), encoding="utf-8")
            (root / "next.py").write_text("def helper(): pass\nfn=helper\nfn()\n", encoding="utf-8")
            with patch("codeviz.analysis.value_candidates.MAX_VALUE_FILE_WORK", 64):
                result = analyze_project(root)
        graph = result.flow.graph
        edge = next(edge for edge in graph.edges if edge.type == EdgeType.CALLS
                    and edge.source_anchor.file_path == "next.py")
        self.assertEqual(candidate_names(result, edge), {"next.helper"})
        self.assertFalse(edge.metadata.get("unknown"))
        self.assertFalse(edge.metadata.get("truncated"))
        self.assertFalse(any(issue.code == "VALUE_FILE_WORK_BUDGET_PARTIAL"
                             and issue.file_path == "next.py" for issue in result.issues))

    def test_work_cut_retains_existing_call_position_not_later_rebinding(self):
        source = "def first(): pass\ndef second(): pass\nfn=first\nfn()\nfn=second\n"
        source += nested_value_loops(6)
        with patch("codeviz.analysis.value_candidates.MAX_VALUE_FILE_WORK", 64):
            result = analyze(source)
        edge = call_at(result, 4, "fn")
        self.assertEqual(candidate_names(result, edge), {"app.first"})
        self.assertTrue(edge.metadata.get("unknown"))
        self.assertTrue(edge.metadata.get("truncated"))

    def test_work_cut_on_operator_keeps_current_expression_anchor(self):
        with patch("codeviz.analysis.value_candidates.MAX_VALUE_FILE_WORK", 4):
            result = analyze("def f(raw):\n    item=raw+raw\n    return item\n")
        issue = next(issue for issue in result.issues if issue.code == "VALUE_FILE_WORK_BUDGET_PARTIAL")
        self.assertEqual(issue.source_anchor.start_line, 2)
        module = next(node for node in result.flow.graph.nodes.values() if node.kind == NodeKind.MODULE)
        self.assertEqual(module.metadata.get("value_file_work_visits"), 4)

    def test_shared_ancestor_dag_has_bounded_lookup_work(self):
        source = "class Base:\n    def run(self): pass\n"
        parents = ["Base"]
        for layer in range(9):
            names = [f"C{layer}_{position}" for position in range(4)]
            source += "".join(f"class {name}({', '.join(parents)}): pass\n" for name in names)
            parents = names
        source += f"{parents[0]}().run()\n"
        visits = 0
        original = ValueCandidates._methods

        def counted(visitor, *args, **kwargs):
            nonlocal visits
            visits += 1
            return original(visitor, *args, **kwargs)

        with patch.object(ValueCandidates, "_methods", counted):
            result = analyze(source)
        self.assertLessEqual(visits, 256, "shared ancestors must not expand every DAG path")
        edge = call_at(result, 39, "run")
        self.assertEqual(candidate_names(result, edge), {"app.Base.run"})
        self.assertFalse(edge.metadata.get("unknown"))

    def test_method_work_budget_keeps_known_target_and_later_scope(self):
        source = "class First:\n    def run(self): pass\nclass Second:\n    def run(self): pass\n"
        source += "class Left(First): pass\nclass Right(Second): pass\n"
        source += "class Child(Left, Right): pass\nChild().run()\n"
        source += "def healthy(): pass\ndef later():\n    fn=healthy\n    fn()\n"
        with patch("codeviz.analysis.value_candidates.MAX_METHOD_WORK", 2, create=True):
            result = analyze(source)
        edge = call_at(result, 8, "run")
        self.assertIn("app.First.run", candidate_names(result, edge))
        self.assertTrue(edge.metadata.get("unknown"))
        self.assertTrue(edge.metadata.get("truncated"))
        self.assertTrue(any(issue.code == "VALUE_INHERIT_WORK_BUDGET_PARTIAL"
                            and issue.source_anchor.start_line == 8 for issue in result.issues))
        self.assertEqual(candidate_names(result, call_at(result, 12, "fn")), {"app.healthy"})

    def test_method_work_is_shared_across_calls_and_direct_facts_survive(self):
        source = "class Base:\n    def run(self): pass\n"
        source += "".join(f"class C{i}(Base): pass\n" for i in range(3))
        source += "C0().run()\nC1().run()\nC2().run()\nBase().run()\n"
        with patch("codeviz.analysis.value_candidates.MAX_METHOD_WORK", 2, create=True):
            result = analyze(source)
        self.assertFalse(call_at(result, 6, "run").metadata.get("unknown"))
        self.assertFalse(call_at(result, 7, "run").metadata.get("unknown"))
        self.assertTrue(call_at(result, 8, "run").metadata.get("unknown"))
        self.assertEqual(candidate_names(result, call_at(result, 9, "run")), {"app.Base.run"})

    def test_partial_method_summary_keeps_known_ids_at_later_call_anchor(self):
        source = "class Base:\n    def run(self): pass\nclass Left(Base): pass\n"
        source += "class Right(Base): pass\nclass Child(Left, Right): pass\n"
        source += "Child().run()\ndef later():\n    Child().run()\n"
        with patch("codeviz.analysis.value_candidates.MAX_METHOD_WORK", 2, create=True):
            result = analyze(source)
        for line in (6, 8):
            edge = call_at(result, line, "run")
            self.assertIn("app.Base.run", candidate_names(result, edge))
            self.assertTrue(edge.metadata.get("unknown"))
            self.assertTrue(edge.metadata.get("truncated"))
            self.assertTrue(any(issue.code == "VALUE_INHERIT_WORK_BUDGET_PARTIAL"
                                and issue.source_anchor.start_line == line for issue in result.issues))

    def test_eager_comprehension_call_retains_later_iteration_rebinding(self):
        result = analyze("def old(): pass\ndef new(): pass\ndef start(items):\n"
                         "    fn=old\n    result=[(fn(), (fn:=new)) for x in items]\n    fn()\n")
        inner = call_at(result, 5, "fn")
        self.assertTrue("app.new" in candidate_names(result, inner) or inner.metadata.get("unknown"))
        self.assertIn("app.old", candidate_names(result, inner))
        self.assertNotEqual(inner.confidence, Confidence.EXACT)
        self.assertEqual(candidate_names(result, call_at(result, 6, "fn")), {"app.old", "app.new"})
        self.assertTrue(any(issue.code == "VALUE_COMPREHENSION_PARTIAL"
                            and issue.source_anchor.start_line == 5 for issue in result.issues))
        self.assertEqual(AppController._result_status(result), AnalysisStatus.PARTIAL)

    def test_comprehension_rebinding_does_not_degrade_creation_iterator_call(self):
        result = analyze("def old(): pass\ndef new(): pass\ndef items(): return []\n"
                         "def start():\n    fn=old\n"
                         "    result=[(fn(), (fn:=new)) for x in items()]\n")
        iterator = call_at(result, 6, "items")
        self.assertFalse(iterator.metadata.get("unknown"))
        self.assertTrue(call_at(result, 6, "fn").metadata.get("unknown"))

    def test_nonlocal_lookup_skips_intermediate_global_function_and_class(self):
        for owner in ("def middle():", "class Middle:"):
            with self.subTest(owner=owner):
                result = analyze("def old(): pass\ndef module_fn(): pass\nfn=module_fn\n"
                                 "def outer():\n    fn=old\n    " + owner + "\n"
                                 "        global fn\n        def inner():\n"
                                 "            nonlocal fn\n            fn()\n")
                ref = next(ref for ref in result.symbol_index.references
                           if ref.name == "fn" and ref.source_anchor.start_line == 10)
                self.assertEqual(result.symbol_index.entries[ref.resolved_definition_id].qualified_name,
                                 "app.outer.fn")
                self.assertEqual(candidate_names(result, call_at(result, 10, "fn")), {"app.old"})

    def test_sequential_assignment_kills_previous_callable(self):
        result = analyze("""
            def first(): pass
            def second(): pass
            def start():
                fn = first
                fn = second
                fn()
        """)
        edge = call_at(result, 6, "fn")
        self.assertEqual(candidate_names(result, edge), {"app.second"})
        self.assertEqual(edge.confidence, Confidence.PROBABLE)
        reference = next(ref for ref in result.symbol_index.references
                         if ref.name == "fn" and ref.source_anchor.start_line == 6)
        self.assertEqual(result.symbol_index.entries[reference.resolved_definition_id].name, "fn")

    def test_direct_function_name_rebound_to_unknown_is_not_exact(self):
        result = analyze("def helper(): pass\nhelper = unknown\nhelper()\n")
        edge = call_at(result, 3, "helper")
        self.assertEqual(edge.confidence, Confidence.UNRESOLVED)
        self.assertFalse(edge.possible_targets)

    def test_unknown_branch_retains_known_candidate_and_unknown(self):
        result = analyze("""
            def helper(): pass
            def start(flag, dynamic):
                fn = helper
                if flag:
                    fn = dynamic
                fn()
        """)
        edge = call_at(result, 6, "fn")
        self.assertEqual(candidate_names(result, edge), {"app.helper"})
        self.assertEqual(edge.confidence, Confidence.PROBABLE)
        self.assertTrue(edge.metadata.get("unknown"))

    def test_unbound_branch_is_reported_on_candidate_call(self):
        result = analyze("""
            def helper(): pass
            def start(flag):
                if flag:
                    fn = helper
                fn()
        """)
        edge = call_at(result, 5, "fn")
        self.assertEqual(candidate_names(result, edge), {"app.helper"})
        self.assertTrue(edge.metadata.get("may_unbound"))

    def test_shadowed_super_has_no_inherited_candidate(self):
        result = analyze("""
            class Base:
                def run(self): pass
            class Child(Base):
                def run(self, super):
                    return super().run()
        """)
        edge = call_at(result, 5, "run")
        self.assertEqual(edge.confidence, Confidence.UNRESOLVED)
        self.assertFalse(edge.possible_targets)

    def test_direct_constructor_expression_has_method_candidate(self):
        result = analyze("class Job:\n    def run(self): pass\nJob().run()\n")
        edge = call_at(result, 3, "run")
        self.assertEqual(candidate_names(result, edge), {"app.Job.run"})

    def test_loop_iteration_budget_reports_unknown(self):
        declarations = "def helper(): pass\n"
        initial = "\n".join(f"    f{i} = unknown" for i in range(10))
        shifts = "\n".join(f"        f{i} = f{i + 1}" for i in range(9))
        result = analyze(declarations + "def start(flag):\n" + initial
                         + "\n    while flag:\n" + shifts
                         + "\n        f9 = helper\n    f0()\n")
        self.assertIn("VALUE_LOOP_BUDGET", {issue.code for issue in result.issues})
        edge = call_at(result, 24, "f0")
        self.assertNotEqual(edge.confidence, Confidence.EXACT)
        self.assertTrue(edge.metadata.get("unknown"))

    def test_candidate_budget_truncates_without_exact_dispatch(self):
        definitions = "\n".join(f"def f{i}(): pass" for i in range(17))
        branches = "\n".join(f"    if flag:\n        fn = f{i}" for i in range(1, 17))
        result = analyze(definitions + "\ndef start(flag):\n    fn = f0\n"
                         + branches + "\n    fn()\n")
        edge = call_at(result, 52, "fn")
        self.assertLessEqual(len(edge.possible_targets), 16)
        self.assertTrue(edge.metadata.get("truncated"))
        self.assertTrue(edge.metadata.get("unknown"))
        self.assertIn("VALUE_CANDIDATE_BUDGET", {issue.code for issue in result.issues})

    def test_deferred_lambda_assignment_does_not_mutate_outer_call_value(self):
        result = analyze("""
            def first(): pass
            def second(): pass
            def start():
                fn = first
                thunk = lambda: (fn := second)
                fn()
        """)
        self.assertEqual(candidate_names(result, call_at(result, 6, "fn")), {"app.first"})

    def test_inheritance_depth_budget_keeps_unknown(self):
        definitions = "class C0:\n    def run(self): pass\n"
        definitions += "\n".join(f"class C{i}(C{i - 1}): pass" for i in range(1, 18))
        result = analyze(definitions + "\nC17().run()\n")
        edge = call_at(result, 20, "run")
        self.assertTrue(edge.metadata.get("unknown"))
        self.assertTrue(edge.metadata.get("truncated"))
        self.assertIn("VALUE_INHERIT_BUDGET", {issue.code for issue in result.issues})

    def test_multiple_bases_are_union_candidates_not_mro_selection(self):
        result = analyze("""
            class First:
                def run(self): pass
            class Second:
                def run(self): pass
            class Child(First, Second): pass
            Child().run()
        """)
        edge = call_at(result, 6, "run")
        self.assertEqual(candidate_names(result, edge), {"app.First.run", "app.Second.run"})
        self.assertEqual(edge.confidence, Confidence.PROBABLE)

    def test_unknown_super_base_keeps_known_method_and_unknown(self):
        result = analyze("""
            from remote import Unknown
            class Known:
                def run(self): pass
            class Child(Known, Unknown):
                def run(self):
                    return super().run()
        """)
        edge = call_at(result, 6, "run")
        self.assertEqual(candidate_names(result, edge), {"app.Known.run"})
        self.assertTrue(edge.metadata.get("unknown"))

    def test_function_rebinding_selects_new_value_without_changing_lexical_index(self):
        result = analyze("def first(): pass\ndef second(): pass\nfirst = second\nfirst()\n")
        edge = call_at(result, 4, "first")
        self.assertEqual(candidate_names(result, edge), {"app.second"})
        self.assertEqual(edge.confidence, Confidence.PROBABLE)

    def test_deferred_generator_assignment_does_not_mutate_outer_value(self):
        result = analyze("""
            def first(): pass
            def second(): pass
            def start(items):
                fn = first
                gen = ((fn := second) for item in items)
                fn()
        """)
        self.assertEqual(candidate_names(result, call_at(result, 6, "fn")), {"app.first"})

    def test_method_can_reference_module_function_defined_after_class(self):
        result = analyze("""
            class Job:
                def run(self):
                    fn = helper
                    return fn()
            def helper(): pass
        """)
        self.assertEqual(candidate_names(result, call_at(result, 4, "fn")), {"app.helper"})

    def test_instance_member_rebinding_cannot_retain_proven_method_dispatch(self):
        result = analyze("""
            class Job:
                def run(self): pass
            def start(dynamic):
                job = Job()
                job.run = dynamic
                job.run()
        """)
        edge = call_at(result, 6, "run")
        self.assertTrue(edge.metadata.get("unknown"))
        self.assertNotEqual(edge.confidence, Confidence.EXACT)

    def test_static_method_zero_argument_super_has_no_instance_evidence(self):
        result = analyze("""
            class Base:
                def run(self): pass
            class Child(Base):
                @staticmethod
                def start():
                    return super().run()
        """)
        edge = call_at(result, 6, "run")
        self.assertFalse(edge.possible_targets)
        self.assertEqual(edge.confidence, Confidence.UNRESOLVED)

    def test_invalid_global_parameter_does_not_validate_module_function_call(self):
        result = analyze("""
            def helper(): pass
            def start(helper):
                global helper
                helper()
        """)
        edge = call_at(result, 4, "helper")
        self.assertEqual(edge.confidence, Confidence.UNRESOLVED)
        self.assertTrue(edge.metadata.get("unknown"))
        self.assertFalse(edge.possible_targets)

    def test_named_definition_kills_previous_assigned_callable(self):
        result = analyze("""
            def old(): pass
            def start():
                fn = old
                def fn(): pass
                fn()
        """)
        edge = call_at(result, 5, "fn")
        self.assertEqual(candidate_names(result, edge), {"app.start.fn"})
        self.assertNotEqual(edge.confidence, Confidence.EXACT)

    def test_eager_comprehension_walrus_keeps_outer_possible_assignment(self):
        result = analyze("""
            def old(): pass
            def new(): pass
            def start(items):
                fn = old
                result = [(fn := new) for item in items]
                fn()
        """)
        edge = call_at(result, 6, "fn")
        self.assertEqual(candidate_names(result, edge), {"app.old", "app.new"})
        self.assertEqual(edge.confidence, Confidence.PROBABLE)

    def test_short_circuit_walrus_preserves_skipped_and_taken_values(self):
        for operation in ("and", "or"):
            with self.subTest(operation=operation):
                result = analyze("def old(): pass\ndef new(): pass\ndef start(flag):\n"
                                 "    fn = old\n    flag " + operation
                                 + " (fn := new)\n    fn()\n")
                edge = call_at(result, 6, "fn")
                self.assertEqual(candidate_names(result, edge), {"app.old", "app.new"})
                self.assertEqual(edge.confidence, Confidence.PROBABLE)

    def test_mapping_rest_capture_kills_prior_callable_inside_case(self):
        result = analyze("""
            def old(): pass
            def start(subject):
                fn = old
                match subject:
                    case {**fn}:
                        fn()
        """)
        edge = call_at(result, 6, "fn")
        self.assertFalse(edge.possible_targets)
        self.assertTrue(edge.metadata.get("unknown"))
        self.assertEqual(edge.confidence, Confidence.UNRESOLVED)

    def test_class_nonmethod_member_blocks_inherited_method_candidate(self):
        result = analyze("""
            class Base:
                def run(self): pass
            class Child(Base):
                run = None
            Child().run()
        """)
        edge = call_at(result, 5, "run")
        self.assertFalse(edge.possible_targets)
        self.assertTrue(edge.metadata.get("unknown"))
        self.assertEqual(edge.confidence, Confidence.UNRESOLVED)

    def test_delete_callable_marks_unbound_including_destructured_targets(self):
        for target in ("fn", "(fn,)"):
            with self.subTest(target=target):
                result = analyze("def old(): pass\ndef start():\n    fn = old\n    del "
                                 + target + "\n    fn()\n")
                edge = call_at(result, 5, "fn")
                self.assertFalse(edge.possible_targets)
                self.assertTrue(edge.metadata.get("may_unbound"))
                self.assertEqual(edge.confidence, Confidence.UNRESOLVED)


if __name__ == "__main__":
    unittest.main()
