"""Lexical ownership regressions; fixture source is parsed, never executed."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent

from codeviz.analysis.project import analyze_project
from codeviz.core import Confidence, EdgeType, NodeKind, SourceAnchor


class GeneralPythonBindingTests(unittest.TestCase):
    def analyze(self, source):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scope.py").write_text(dedent(source).lstrip("\n"), encoding="utf-8")
            result = analyze_project(root)
        self.assertTrue(all(file.was_parsed for file in result.files))
        self.assertFalse(any(issue.code == "INDEX_ERROR" for issue in result.issues))
        return result

    def definition(self, result, name, line):
        matches = [item for item in result.symbol_index.find_definitions(name)
                   if item.source_anchor.start_line == line]
        self.assertEqual(len(matches), 1, f"expected binding {name} at line {line}")
        self.assertIsInstance(matches[0].source_anchor, SourceAnchor)
        return matches[0]

    def reference(self, result, name, line, column=None):
        matches = [item for item in result.symbol_index.find_references(name)
                   if item.qualifier is None and item.source_anchor.start_line == line
                   and (column is None or item.source_anchor.start_column == column)]
        self.assertEqual(len(matches), 1, f"expected reference {name} at {line}:{column}")
        return matches[0]

    def assert_binding(self, reference, definition):
        # Exact identifies lexical ownership, not runtime assignment or dispatch.
        self.assertEqual(reference.resolved_definition_id, definition.id)
        self.assertEqual(reference.confidence, Confidence.EXACT)

    def assert_lexical_owner(self, result, definition):
        lexical_kind = getattr(NodeKind, "LEXICAL_SCOPE", None)
        self.assertIsNotNone(lexical_kind, "lambda/comprehension require NodeKind.LEXICAL_SCOPE")
        self.assertIn(definition.parent_symbol, result.symbol_index.entries)
        owner = result.symbol_index.entries[definition.parent_symbol]
        self.assertEqual(owner.kind, lexical_kind)
        self.assertFalse(owner.definition, "lexical owner is not a callable definition")
        self.assertIsInstance(owner.source_anchor, SourceAnchor)
        return owner

    def test_lambda_parameter_shadows_outer_binding_with_lexical_owner(self):
        result = self.analyze('''
            value = 10
            callback = lambda value: value
            after = value
        ''')
        parameter = self.definition(result, "value", 2)
        owner = self.assert_lexical_owner(result, parameter)
        body = self.reference(result, "value", 2)
        self.assert_binding(body, parameter)
        self.assertEqual(body.scope_symbol_id, owner.id)
        self.assert_binding(self.reference(result, "value", 3), self.definition(result, "value", 1))

    def test_lambda_defaults_are_evaluated_in_outer_scope(self):
        result = self.analyze('''
            value = 10
            callback = lambda value=value: value
        ''')
        parameter = self.definition(result, "value", 2)
        owner = self.assert_lexical_owner(result, parameter)
        references = sorted(result.symbol_index.find_references("value"),
                            key=lambda item: item.source_anchor.start_column)
        self.assertEqual(len(references), 2)
        self.assert_binding(references[0], self.definition(result, "value", 1))
        self.assert_binding(references[1], parameter)
        self.assertNotEqual(references[0].scope_symbol_id, owner.id)
        self.assertEqual(references[1].scope_symbol_id, owner.id)

    def test_listcomp_target_is_separate_and_first_iterable_is_outer(self):
        result = self.analyze('''
            item = [1, 2]
            values = [item for item in item]
            after = item
        ''')
        target = self.definition(result, "item", 2)
        owner = self.assert_lexical_owner(result, target)
        outer = self.definition(result, "item", 1)
        references = sorted((item for item in result.symbol_index.find_references("item")
                             if item.source_anchor.start_line == 2),
                            key=lambda item: item.source_anchor.start_column)
        self.assertEqual(len(references), 2, "store target is a binding, not a read")
        self.assert_binding(references[0], target)
        self.assertEqual(references[0].scope_symbol_id, owner.id)
        self.assert_binding(references[1], outer)
        self.assertEqual(references[1].scope_symbol_id, outer.parent_symbol)
        self.assert_binding(self.reference(result, "item", 3), outer)

    def test_nested_comprehensions_have_distinct_lexical_owners(self):
        result = self.analyze('''
            rows = [[1, 2]]
            values = [[cell for cell in row] for row in rows]
        ''')
        cell = self.definition(result, "cell", 2)
        row = self.definition(result, "row", 2)
        inner = self.assert_lexical_owner(result, cell)
        outer = self.assert_lexical_owner(result, row)
        self.assertNotEqual(inner.id, outer.id)
        self.assertEqual(inner.parent_symbol, outer.id)
        self.assert_binding(self.reference(result, "cell", 2), cell)
        row_reference = self.reference(result, "row", 2)
        self.assert_binding(row_reference, row)
        self.assertEqual(row_reference.scope_symbol_id, outer.id)
        rows_reference = self.reference(result, "rows", 2)
        rows = self.definition(result, "rows", 1)
        self.assert_binding(rows_reference, rows)
        self.assertEqual(rows_reference.scope_symbol_id, rows.parent_symbol)

    def test_match_capture_guard_body_and_after_use_enclosing_function_scope(self):
        result = self.analyze('''
            def choose(subject):
                match subject:
                    case {"name": captured} if captured:
                        selected = captured
                return captured
        ''')
        captured = self.definition(result, "captured", 3)
        function = self.definition(result, "choose", 1)
        self.assertEqual(captured.parent_symbol, function.id)
        for line in (3, 4, 5):
            with self.subTest(line=line):
                reference = self.reference(result, "captured", line)
                self.assert_binding(reference, captured)
                self.assertEqual(reference.scope_symbol_id, function.id)

    def test_class_comprehension_body_skips_class_but_first_iterable_uses_class(self):
        result = self.analyze('''
            value = 100
            class Box:
                value = 200
                items = [1, 2]
                values = [value for item in items]
        ''')
        self.assert_binding(self.reference(result, "value", 5), self.definition(result, "value", 1))
        items = self.definition(result, "items", 4)
        iterable = self.reference(result, "items", 5)
        self.assert_binding(iterable, items)
        self.assertEqual(iterable.scope_symbol_id, items.parent_symbol)
        target = self.definition(result, "item", 5)
        owner = self.assert_lexical_owner(result, target)
        self.assertEqual(self.reference(result, "value", 5).scope_symbol_id, owner.id)

    def test_class_lambda_body_skips_class_namespace_but_default_uses_it(self):
        result = self.analyze('''
            value = 100
            class Box:
                value = 200
                callback = lambda default=value: value
        ''')
        references = sorted((item for item in result.symbol_index.find_references("value")
                             if item.source_anchor.start_line == 4),
                            key=lambda item: item.source_anchor.start_column)
        self.assertEqual(len(references), 2)
        self.assert_binding(references[0], self.definition(result, "value", 3))
        self.assert_binding(references[1], self.definition(result, "value", 1))
        parameter = self.definition(result, "default", 4)
        owner = self.assert_lexical_owner(result, parameter)
        self.assertEqual(references[1].scope_symbol_id, owner.id)

    def test_class_only_names_are_unresolved_in_lambda_and_comprehension_bodies(self):
        result = self.analyze('''
            class Box:
                secret = 200
                items = [1, 2]
                values = [secret for item in items]
                callback = lambda: secret
        ''')
        for line in (4, 5):
            with self.subTest(line=line):
                reference = self.reference(result, "secret", line)
                self.assertIsNone(reference.resolved_definition_id)
                self.assertEqual(reference.confidence, Confidence.UNRESOLVED)

    def test_lambda_body_calls_are_not_owned_by_outer_function(self):
        result = self.analyze('''
            def helper():
                return 1
            def outer():
                callback = lambda value: helper()
                return callback
        ''')
        outer = self.definition(result, "outer", 3)
        graph = result.flow.graph
        calls = [edge for edge in graph.edges if edge.type == EdgeType.CALLS
                 and edge.source_anchor is not None and edge.source_anchor.start_line == 4]
        self.assertTrue(calls, "lambda body call should remain represented as static syntax")
        for edge in calls:
            site = graph.nodes[edge.source]
            self.assertNotEqual(site.metadata.get("scope_symbol_id"), outer.id,
                                "deferred lambda body call must not belong to outer execution")
            parameter = self.definition(result, "value", 4)
            owner = self.assert_lexical_owner(result, parameter)
            self.assertEqual(site.metadata.get("scope_symbol_id"), owner.id)


if __name__ == "__main__":
    unittest.main()
