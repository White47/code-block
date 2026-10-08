"""Malformed declaration fixtures are parsed and analyzed, never executed."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from codeviz.analysis.project import analyze_project
from codeviz.core import Confidence, NodeKind


class ScopeDeclarationConflictTests(unittest.TestCase):
    def analyze(self, source, other=None):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "example.py").write_text(source, encoding="utf-8")
            if other is not None:
                (root / "valid.py").write_text(other, encoding="utf-8")
            return analyze_project(root)

    def assert_partial(self, analysis, line):
        issues = [issue for issue in analysis.issues
                  if issue.code == "SCOPE_DECLARATION_CONFLICT"]
        self.assertTrue(issues, "invalid declarations must produce a diagnostic")
        self.assertTrue(any(issue.source_anchor.file_path == "example.py"
                            and issue.source_anchor.start_line == line
                            for issue in issues))
        self.assertTrue(all("partial" in issue.message.casefold() for issue in issues))
        self.assertFalse(any(issue.code == "INDEX_ERROR" for issue in analysis.issues))

    def test_global_parameter_stays_in_function_without_exact_reference(self):
        for arguments in ("x", "x, /", "*, x", "*x", "**x"):
            with self.subTest(arguments=arguments):
                analysis = self.analyze(
                    f"def f({arguments}):\n    global x\n    return x\n")
                index = analysis.symbol_index
                function = index.find_definitions("f")[0]
                parameters = [entry for entry in index.find_definitions("x")
                              if entry.kind == NodeKind.PARAMETER]
                self.assertEqual(len(parameters), 1)
                self.assertEqual(parameters[0].parent_symbol, function.id)
                self.assertFalse(any(entry.name == "x" and entry.scope == "example"
                                     for entry in index.entries.values()))
                reference = index.find_references("x")[0]
                self.assertNotEqual(reference.confidence, Confidence.EXACT)
                self.assertIsNone(reference.resolved_definition_id)
                self.assert_partial(analysis, 2)

    def test_nonlocal_parameter_does_not_pollute_enclosing_scope(self):
        analysis = self.analyze(
            "def outer():\n"
            "    x = 1\n"
            "    def f(x):\n"
            "        nonlocal x\n"
            "        x = 2\n"
            "        return x\n"
            "    return x\n")
        index = analysis.symbol_index
        function = index.find_definitions("f")[0]
        parameter = next(entry for entry in index.find_definitions("x")
                         if entry.kind == NodeKind.PARAMETER)
        self.assertEqual(parameter.parent_symbol, function.id)
        inner = next(ref for ref in index.find_references("x")
                     if ref.source_anchor.start_line == 6)
        self.assertNotEqual(inner.confidence, Confidence.EXACT)
        outer = next(ref for ref in index.find_references("x")
                     if ref.source_anchor.start_line == 7)
        self.assertEqual(outer.confidence, Confidence.EXACT)
        self.assertEqual(index.entries[outer.resolved_definition_id].source_anchor.start_line, 2)
        self.assert_partial(analysis, 4)

    def test_global_nonlocal_conflict_never_creates_outer_binding(self):
        for first, second in (("global", "nonlocal"), ("nonlocal", "global")):
            with self.subTest(first=first):
                analysis = self.analyze(
                    "x = 1\n"
                    "def outer():\n"
                    "    x = 2\n"
                    "    def f():\n"
                    f"        {first} x\n"
                    f"        {second} x\n"
                    "        x = 3\n"
                    "        return x\n")
                index = analysis.symbol_index
                inner = index.find_references("x")[0]
                self.assertNotEqual(inner.confidence, Confidence.EXACT)
                self.assertIsNone(inner.resolved_definition_id)
                owners = {entry.id for entry in index.find_definitions("f")}
                self.assertTrue(all(entry.parent_symbol in owners
                                    for entry in index.find_definitions("x")
                                    if entry.source_anchor.start_line == 7))
                self.assert_partial(analysis, 6)

    def test_unused_parameter_conflict_still_reports_declaration(self):
        analysis = self.analyze("def f(x):\n    global x\n    return 1\n")
        self.assert_partial(analysis, 2)

    def test_invalid_scope_preserves_valid_file_and_sibling_facts(self):
        analysis = self.analyze(
            "def f(x):\n    global x\n    return x\n"
            "def sibling(y):\n    return y\n",
            "def valid(z):\n    return z\n")
        self.assertEqual(len(analysis.files), 2)
        self.assertTrue(all(file.was_parsed for file in analysis.files))
        for name in ("y", "z"):
            reference = analysis.symbol_index.find_references(name)[0]
            self.assertEqual(reference.confidence, Confidence.EXACT)
            self.assertEqual(analysis.symbol_index.entries[
                reference.resolved_definition_id].kind, NodeKind.PARAMETER)
        self.assert_partial(analysis, 2)


if __name__ == "__main__":
    unittest.main()
