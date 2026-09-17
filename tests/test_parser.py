import ast
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from codeviz.analysis.python import parse_python_file


class PythonParserTests(unittest.TestCase):
    def test_parses_ast_without_executing_project_code(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "executed.txt"
            source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\nclass Model: pass\n"
            (root / "model.py").write_text(source, encoding="utf-8")

            parsed = parse_python_file(root, "model.py")

            self.assertIsInstance(parsed.tree, ast.Module)
            self.assertEqual(parsed.module_name, "model")
            self.assertEqual(parsed.issues, ())
            self.assertFalse(marker.exists())

    def test_reports_syntax_error_with_source_line(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bad.py").write_text("x = 1\ndef broken(:\n", encoding="utf-8")

            parsed = parse_python_file(root, "bad.py")

            self.assertIsNone(parsed.tree)
            self.assertEqual(parsed.issues[0].code, "SYNTAX_ERROR")
            self.assertEqual(parsed.issues[0].source_anchor.start_line, 2)

    def test_rejects_source_path_outside_project(self):
        with TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                parse_python_file(Path(temporary), "../outside.py")

    def test_limits_large_source_files(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "large.py").write_text("x = 'very long'\n", encoding="utf-8")

            parsed = parse_python_file(root, "large.py", max_file_bytes=5)

            self.assertIsNone(parsed.tree)
            self.assertEqual(parsed.issues[0].code, "FILE_TOO_LARGE")

    def test_honors_python_encoding_cookie(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "legacy.py").write_bytes(b"# coding: latin-1\nname = '\xe9'\n")

            parsed = parse_python_file(root, "legacy.py")

            self.assertIsInstance(parsed.tree, ast.Module)
            self.assertEqual(parsed.issues, ())

    def test_parser_depth_error_becomes_file_issue(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "deep.py").write_text("x = 1\n", encoding="utf-8")

            with patch("codeviz.analysis.python.ast.parse", side_effect=RecursionError("too deep")):
                parsed = parse_python_file(root, "deep.py")

        self.assertIsNone(parsed.tree)
        self.assertEqual(parsed.issues[0].code, "PARSE_DEPTH_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
