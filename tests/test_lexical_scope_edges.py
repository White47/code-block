"""Additional lexical regressions using real syntax and static project analysis."""

import ast
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from codeviz.analysis.project import analyze_project
from codeviz.core import Confidence, EdgeType, NodeKind


class LexicalScopeEdgeTests(unittest.TestCase):
    def analyze(self, source):
        ast.parse(source)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "example.py").write_text(source, encoding="utf-8")
            result = analyze_project(root)
        self.assertFalse(any(issue.code in ("INDEX_ERROR", "FLOW_ERROR", "DATA_FLOW_ERROR")
                             for issue in result.issues), result.issues)
        return result

    def assert_exact_owner(self, result, name, line, owner_name):
        refs = [ref for ref in result.symbol_index.find_references(name)
                if ref.qualifier is None and ref.source_anchor.start_line == line]
        self.assertTrue(refs)
        for ref in refs:
            self.assertEqual(ref.confidence, Confidence.EXACT)
            binding = result.symbol_index.entries[ref.resolved_definition_id]
            owner = result.symbol_index.entries[binding.parent_symbol]
            self.assertEqual(owner.name, owner_name)

    def test_walrus_nested_comprehensions_bind_function_and_global_nonlocal(self):
        result = self.analyze("x = 0\ndef outer():\n    y = 0\n"
                              "    def inner():\n        global x\n        nonlocal y\n"
                              "        a = [[(x := v, y := v) for v in row] for row in [[1]]]\n"
                              "        return x, y\n    return y\n")
        self.assert_exact_owner(result, "x", 8, "example")
        self.assert_exact_owner(result, "y", 8, "outer")
        self.assert_exact_owner(result, "y", 9, "outer")

    def test_nonlocal_walrus_sees_binding_declared_after_nested_definition(self):
        result = self.analyze("def outer():\n    def inner():\n        nonlocal x\n"
                              "        a = [(x := v) for v in [1]]\n        return x\n"
                              "    x = 0\n    return x\n")
        self.assert_exact_owner(result, "x", 5, "outer")
        self.assert_exact_owner(result, "x", 7, "outer")

    def test_match_or_as_star_mapping_rest_are_deduplicated(self):
        result = self.analyze("def choose(subject):\n    match subject:\n"
                              "        case [x, *tail] | (x, *tail) if x:\n"
                              "            return x, tail\n"
                              "        case {'key': y, **rest} as whole:\n"
                              "            return y, rest, whole\n"
                              "        case _:\n            return None\n")
        for name in ("x", "tail", "y", "rest", "whole"):
            definitions = result.symbol_index.find_definitions(name)
            self.assertEqual(len(definitions), 1)
            self.assertEqual(result.symbol_index.entries[definitions[0].parent_symbol].name, "choose")
        self.assertFalse(result.symbol_index.find_definitions("_"))
        self.assert_exact_owner(result, "x", 3, "choose")

    def test_implicit_scope_ids_are_distinct_and_survive_line_shifts(self):
        source = "a = lambda x: x; b = lambda x: x\nc = [x for x in [1]]\n"
        first = self.analyze(source)
        shifted = self.analyze("\n# moved\n" + source)
        scopes = [entry for entry in first.symbol_index.entries.values()
                  if entry.kind.value == "LexicalScope"]
        self.assertEqual(len(scopes), 3)
        self.assertEqual(len({entry.id for entry in scopes}), 3)
        self.assertTrue(all(not entry.definition for entry in scopes))
        self.assertEqual({entry.id for entry in scopes},
                         {entry.id for entry in shifted.symbol_index.entries.values()
                          if entry.kind.value == "LexicalScope"})

    def test_generator_creation_calls_outer_but_body_is_deferred(self):
        result = self.analyze("def source():\n    return [1]\ndef body(x):\n    return x\n"
                              "def outer():\n    return (body(x) for x in source())\n")
        graph = result.flow.graph
        sites = [node for node in graph.nodes.values()
                 if node.kind == NodeKind.CALL_SITE and node.source_anchors[0].start_line == 6]
        self.assertEqual(len(sites), 2)
        for site in sites:
            owner = result.symbol_index.entries[site.metadata["scope_symbol_id"]]
            self.assertEqual(owner.kind.value, "Function" if site.name == "source" else "LexicalScope")
            if site.name == "body":
                parents = [edge.source for edge in graph.edges
                           if edge.type == EdgeType.CONTAINS and edge.target == site.id]
                self.assertEqual(parents, [owner.id])

    def test_shadowed_call_targets_do_not_call_outer_function(self):
        result = self.analyze("def f():\n    return 1\na = lambda f: f()\nb = [f() for f in []]\n")
        function = result.symbol_index.find_definitions("f")[0]
        calls = [edge for edge in result.flow.graph.edges if edge.type == EdgeType.CALLS
                 and edge.source_anchor.start_line in (3, 4)]
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(edge.target != function.id and edge.confidence == Confidence.UNRESOLVED
                            for edge in calls))

    def test_deferred_bodies_do_not_read_outer_data_at_creation(self):
        result = self.analyze("def outer(inputs):\n    a = lambda: inputs\n"
                              "    b = (inputs for x in [])\n    return a, b\n")
        reads = [edge for edge in result.flow.graph.edges if edge.type == EdgeType.READS
                 and edge.source_anchor.start_line in (2, 3)]
        self.assertEqual(reads, [])

    def test_eager_comprehension_data_reads_use_local_scope(self):
        result = self.analyze("def outer(inputs):\n    predictions = [inputs for inputs in []]\n"
                              "    return predictions\n")
        local = [entry for entry in result.symbol_index.find_definitions("inputs")
                 if entry.source_anchor.start_line == 2]
        self.assertEqual(len(local), 1)
        reads = [edge for edge in result.flow.graph.edges if edge.type == EdgeType.READS
                 and edge.source_anchor.start_line == 2]
        self.assertTrue(reads)
        self.assertTrue(all(result.flow.graph.nodes[edge.target].metadata["symbol_id"] == local[0].id
                            for edge in reads))

    def test_walrus_in_comprehension_iterable_is_diagnosed_without_exact(self):
        result = self.analyze("x = 0\na = [v for v in (x := [])]\nafter = x\n")
        reference = result.symbol_index.find_references("x")[0]
        self.assertIsNone(reference.resolved_definition_id)
        self.assertEqual(reference.confidence, Confidence.UNRESOLVED)
        self.assertTrue(any(issue.code == "WALRUS_BINDING_UNRESOLVED" for issue in result.issues))

    def test_invalid_walrus_without_reads_still_emits_diagnosis(self):
        result = self.analyze("class Box:\n    a = [(x := v) for v in []]\n")
        self.assertTrue(any(issue.code == "WALRUS_BINDING_UNRESOLVED" for issue in result.issues))

    def test_comprehension_walrus_target_collision_has_no_exact(self):
        result = self.analyze("a = [(x := 0) for x in []]\n")
        self.assertTrue(any(issue.code == "WALRUS_BINDING_UNRESOLVED" for issue in result.issues))

    def test_missing_nonlocal_never_falls_back_to_module(self):
        result = self.analyze("x = 0\ndef outer():\n    def inner():\n        nonlocal x\n"
                              "        a = [(x := v) for v in []]\n        return x\n")
        refs = result.symbol_index.find_references("x")
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].confidence, Confidence.UNRESOLVED)
        self.assertIsNone(refs[0].resolved_definition_id)

    def test_nonlocal_skips_intermediate_function_without_local_binding(self):
        result = self.analyze("def outer():\n    def middle():\n        def inner():\n"
                              "            nonlocal x\n            a = [(x := v) for v in []]\n"
                              "            return x\n        return lambda: x\n"
                              "    x = 0\n    return x\n")
        for line in (6, 7, 9):
            self.assert_exact_owner(result, "x", line, "outer")

    def test_lambda_and_generator_defaults_and_first_iterable_read_outer_data(self):
        result = self.analyze("def outer(inputs):\n    a = lambda inputs=inputs: inputs\n"
                              "    b = (inputs for inputs in inputs)\n    return a, b\n")
        outer = result.symbol_index.find_definitions("inputs")[0]
        reads = [edge for edge in result.flow.graph.edges if edge.type == EdgeType.READS
                 and edge.source_anchor.start_line in (2, 3)]
        self.assertEqual(len(reads), 2)
        self.assertTrue(all(result.flow.graph.nodes[edge.target].metadata["symbol_id"] == outer.id
                            for edge in reads))

    def test_dict_set_and_multi_generator_scopes_preserve_local_dependencies(self):
        result = self.analyze("xs = [1]\na = {y: x for x in xs for y in x if y}\n"
                              "b = {x for x in xs if x}\n")
        for name, line in (("x", 2), ("y", 2), ("x", 3)):
            refs = [ref for ref in result.symbol_index.find_references(name)
                    if ref.source_anchor.start_line == line]
            self.assertTrue(refs)
            for ref in refs:
                binding = result.symbol_index.entries[ref.resolved_definition_id]
                self.assertEqual(binding.parent_symbol, ref.scope_symbol_id)
                self.assertEqual(ref.confidence, Confidence.EXACT)

    def test_lambda_walrus_binding_stays_local(self):
        result = self.analyze("x = 0\na = lambda: ([(x := v) for v in []], x)\nafter = x\n")
        refs = result.symbol_index.find_references("x")
        local_ref = next(ref for ref in refs if ref.source_anchor.start_line == 2)
        local = result.symbol_index.entries[local_ref.resolved_definition_id]
        self.assertEqual(local.parent_symbol, local_ref.scope_symbol_id)
        self.assertEqual(result.symbol_index.entries[local.parent_symbol].metadata["scope_type"], "Lambda")
        self.assert_exact_owner(result, "x", 3, "example")

    def test_shadowed_self_in_implicit_scope_does_not_target_enclosing_method(self):
        result = self.analyze("class Box:\n    def f(self):\n        return 1\n"
                              "    def outer(self):\n        a = lambda self: self.f()\n"
                              "        b = [self.f() for self in []]\n        return a, b\n")
        calls = [edge for edge in result.flow.graph.edges if edge.type == EdgeType.CALLS
                 and edge.source_anchor.start_line in (5, 6)]
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(edge.confidence == Confidence.UNRESOLVED and not edge.possible_targets
                            and result.flow.graph.nodes[edge.target].kind == NodeKind.UNRESOLVED_SYMBOL
                            for edge in calls))

    def test_lambda_capturing_method_self_retains_conservative_dispatch(self):
        result = self.analyze("class Box:\n    def f(self):\n        return 1\n"
                              "    def outer(self):\n        return lambda: self.f()\n")
        method = result.symbol_index.find_definitions("f")[0]
        calls = [edge for edge in result.flow.graph.edges if edge.type == EdgeType.CALLS]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].confidence, Confidence.PROBABLE)
        self.assertEqual(calls[0].possible_targets, (method.id,))

    def test_nonlocal_does_not_treat_enclosing_function_default_as_local(self):
        result = self.analyze("def outer(arg=(x := 0)):\n    def inner():\n        nonlocal x\n"
                              "        a = [(x := v) for v in []]\n        return x\n")
        refs = result.symbol_index.find_references("x")
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].confidence, Confidence.UNRESOLVED)
        bindings = result.symbol_index.find_definitions("x")
        self.assertFalse(any(result.symbol_index.entries[entry.parent_symbol].name == "outer"
                             for entry in bindings))

    def test_nonlocal_sees_outer_walrus_in_nested_class_creation_expression(self):
        result = self.analyze("def outer():\n    def inner():\n        nonlocal x\n"
                              "        a = [(x := v) for v in []]\n        return x\n"
                              "    class Box((x := object)):\n        pass\n    return x\n")
        self.assert_exact_owner(result, "x", 5, "outer")
        self.assert_exact_owner(result, "x", 8, "outer")
        self.assertTrue(all(result.symbol_index.entries[entry.parent_symbol].name == "outer"
                            for entry in result.symbol_index.find_definitions("x")))

    def test_lambda_in_vararg_annotation_retains_static_call(self):
        result = self.analyze("def helper():\n    return 1\n"
                              "def outer(*args: (lambda: helper())):\n    pass\n")
        calls = [edge for edge in result.flow.graph.edges if edge.type == EdgeType.CALLS]
        self.assertEqual(len(calls), 1)
        site = result.flow.graph.nodes[calls[0].source]
        owner = result.symbol_index.entries[site.metadata["scope_symbol_id"]]
        self.assertEqual(owner.kind.value, "LexicalScope")

    def test_reviewer_default_walrus_does_not_create_middle_local(self):
        result = self.analyze("def outer():\n    x = 0\n"
                              "    def middle(arg=(x := 1)):\n        def inner():\n"
                              "            nonlocal x\n            x = 2\n            return x\n"
                              "        return x\n    return x\n")
        for line in (7, 8, 9):
            self.assert_exact_owner(result, "x", line, "outer")
        bindings = result.symbol_index.find_definitions("x")
        self.assertEqual(len(bindings), 1)
        self.assertEqual(result.symbol_index.entries[bindings[0].parent_symbol].name, "outer")


if __name__ == "__main__":
    unittest.main()
