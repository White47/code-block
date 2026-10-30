import ast
import unittest
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz import source_handles
from codeviz.analysis.python import ParsedFile, parse_python_file


class PythonParserTests(unittest.TestCase):
    def assert_hash(self, parsed, raw):
        self.assertTrue(hasattr(parsed, "content_hash"), "ParsedFile must retain the raw source hash")
        self.assertEqual(parsed.content_hash, sha256(raw).hexdigest())

    def test_raw_hash_encoding_and_newline_representatives(self):
        for raw in (b"x = 1\n", b"\xef\xbb\xbfx = 1\r\n",
                    b"# coding: latin-1\nname = '\xe9'\r", b"", b"x = 1\r\ny = 2\r"):
            with self.subTest(raw=raw), TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "app.py").write_bytes(raw)
                parsed = parse_python_file(root, "app.py")
                self.assertIsInstance(parsed.tree, ast.Module)
                self.assertEqual(parsed.issues, ())
                self.assert_hash(parsed, raw)
                normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                self.assertEqual(parsed.line_count, max(1, len(normalized.split(b"\n"))
                                                       - normalized.endswith(b"\n")))

    def test_unicode_separators_do_not_add_physical_lines(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = "value = 'a\u0085b\u2028c\u2029d'\n# comment\n".encode()
            (root / "app.py").write_bytes(raw)
            parsed = parse_python_file(root, "app.py")
            self.assertEqual(parsed.line_count, 2)
            self.assertIsInstance(parsed.tree, ast.Module)
            self.assert_hash(parsed, raw)

    def test_decode_errors_preserve_raw_hash(self):
        for raw in (b"x = '\xff'\n", b"# coding: no-such-codec\nx = 1\n",
                    b"\xef\xbb\xbf# coding: latin-1\nx = 1\n"):
            with self.subTest(raw=raw), TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "app.py").write_bytes(raw)
                parsed = parse_python_file(root, "app.py")
                self.assertIsNone(parsed.tree)
                self.assertEqual(parsed.issues[0].code, "DECODE_ERROR")
                self.assert_hash(parsed, raw)

    def test_syntax_error_preserves_hash_and_normalized_line(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = b"x = 1\rdef broken(:\r"
            (root / "app.py").write_bytes(raw)
            parsed = parse_python_file(root, "app.py")
            self.assertEqual(parsed.issues[0].code, "SYNTAX_ERROR")
            self.assertEqual(parsed.issues[0].source_anchor.start_line, 2)
            self.assertEqual(parsed.line_count, 2)
            self.assert_hash(parsed, raw)

    def test_ast_failures_preserve_verified_hash(self):
        for error, code in ((RecursionError("too deep"), "PARSE_DEPTH_EXCEEDED"),
                            (UnicodeError("AST encoding failure"), "DECODE_ERROR")):
            with self.subTest(code=code), TemporaryDirectory() as temporary:
                root = Path(temporary)
                raw = b"x = 1\n"
                (root / "app.py").write_bytes(raw)
                with patch("codeviz.analysis.python.ast.parse", side_effect=error):
                    try:
                        parsed = parse_python_file(root, "app.py")
                    except UnicodeError as escaped:
                        self.fail(f"AST UnicodeError must become DECODE_ERROR: {escaped}")
                self.assertIsNone(parsed.tree)
                self.assertEqual(parsed.issues[0].code, code)
                self.assert_hash(parsed, raw)

    def test_read_failure_and_oversize_have_no_hash(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "large.py").write_bytes(b"x = 123\n")
            for relative, limit, code in (("absent.py", 100, "READ_ERROR"),
                                          ("large.py", 2, "FILE_TOO_LARGE")):
                with self.subTest(code=code):
                    parsed = parse_python_file(root, relative, max_file_bytes=limit)
                    self.assertEqual(parsed.issues[0].code, code)
                    self.assertTrue(hasattr(parsed, "content_hash"))
                    self.assertIsNone(parsed.content_hash)
                    self.assertNotIn(str(root), parsed.issues[0].message)

    def test_old_six_position_constructor_remains_compatible(self):
        parsed = ParsedFile("app.py", "app", None, 3, (), True)
        self.assertTrue(parsed.was_parsed)
        self.assertTrue(hasattr(parsed, "content_hash"))
        self.assertIsNone(parsed.content_hash)

    def test_backslash_input_normalizes_before_shared_read(self):
        self.assertTrue(hasattr(source_handles, "read_source_bytes"), "missing shared reader")
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pkg").mkdir()
            raw = b"x = 1\n"
            (root / "pkg" / "app.py").write_bytes(raw)
            # Spy delegates to the actual safe reader, preserving its verification.
            with patch("codeviz.analysis.python.read_source_bytes",
                       wraps=source_handles.read_source_bytes) as reader:
                parsed = parse_python_file(root, "pkg\\app.py")
            reader.assert_called_once_with(root.resolve(), "pkg/app.py", max_bytes=5_000_000)
            self.assertEqual(parsed.relative_path, "pkg/app.py")
            self.assert_hash(parsed, raw)

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
