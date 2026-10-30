"""B4 pure decoding, AST-byte coordinates, bounded prefixes and secret rules."""

import ast
import hashlib
import importlib
import importlib.util
import unittest
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, fields
from unittest.mock import patch

from codeviz.assistant.snapshot import AnchorRecord


class SourceTextTests(unittest.TestCase):
    def setUp(self):
        for name, exports in (
            ("source_text", ("MonacoRange", "ExcerptLimits", "TextSelection",
                             "decode_python_source", "select_source_text",
                             "contains_suspected_secret")),
            ("source_policy", ("SourceExcerptError",)),
        ):
            qualified = "codeviz.assistant." + name
            self.assertIsNotNone(importlib.util.find_spec(qualified),
                                 f"Missing B4 API: {qualified}")
            module = importlib.import_module(qualified)
            for export in exports:
                self.assertTrue(hasattr(module, export), f"Missing B4 API: {export}")
            setattr(self, name, module)
        self.api = self.source_text

    def rejected(self, action, code="invalid_input"):
        messages = {
            "invalid_input": "Invalid source excerpt input.",
            "decode_failed": "Source encoding could not be decoded.",
            "invalid_range": "Source range is invalid.",
        }
        with self.assertRaises(self.source_policy.SourceExcerptError) as caught:
            action()
        error = caught.exception
        self.assertEqual(error.code, code)
        self.assertEqual((error.message, str(error)), (messages[code], messages[code]))
        self.assertIsNone(error.__cause__)
        if error.__context__ is not None:
            self.assertTrue(error.__suppress_context__)

    def selection(self, source, anchor, text, returned, monaco, truncated=False, **limits):
        result = self.api.select_source_text(
            source, anchor, limits=self.api.ExcerptLimits(**limits))
        self.assertEqual(result.text, text)
        self.assertEqual(result.returned_anchor, AnchorRecord("app.py", *returned))
        self.assertEqual(result.monaco_range, self.api.MonacoRange(*monaco))
        self.assertIs(result.truncated, truncated)
        return result

    def test_decode_pep263_latin1_normalizes_lf_without_changing_characters(self):
        raw = b"# coding: latin-1\r\nname = '\xe9'\r\n"
        expected = "# coding: latin-1\nname = 'é'\n"
        self.assertEqual(self.api.decode_python_source(raw), expected)
        self.assertNotEqual(hashlib.sha256(raw).hexdigest(),
                            hashlib.sha256(expected.encode("utf-8")).hexdigest())

    def test_decode_bom_cookie_and_second_line_encoding(self):
        cases = ((b"\xef\xbb\xbfx = 1\n", "x = 1\n"),
                 (b"\xef\xbb\xbf# coding: utf-8\n", "# coding: utf-8\n"),
                 (b"#!/usr/bin/python\n# coding: latin-1\nx='\xe9'\n",
                  "#!/usr/bin/python\n# coding: latin-1\nx='é'\n"),
                 ("x='中😀'\n".encode(), "x='中😀'\n"))
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(self.api.decode_python_source(raw), expected)

    def test_decode_strict_failure_is_fixed_and_does_not_replace_bytes(self):
        for raw in (b"x='\xff'", b"# coding: unknown-synthetic-codec\nx=1",
                    b"\xef\xbb\xbf# coding: latin-1\nx=1\n",
                    b"# coding: ascii\nx='\xe9'", b"x='\xed\xa0\x80'"):
            with self.subTest(raw=raw):
                self.rejected(lambda raw=raw: self.api.decode_python_source(raw), "decode_failed")

    def test_decode_accepts_exact_bytes_only(self):
        class BytesSubclass(bytes):
            pass

        for raw in (None, "x=1", bytearray(b"x=1"), memoryview(b"x=1"), BytesSubclass(b"x=1")):
            with self.subTest(raw=repr(raw)):
                self.rejected(lambda raw=raw: self.api.decode_python_source(raw))

    def test_decode_empty_cr_only_and_mixed_newlines(self):
        for raw, expected in ((b"", ""), (b"a\rb\r", "a\nb\n"),
                              (b"a\r\nb\rc\nd", "a\nb\nc\nd")):
            with self.subTest(raw=raw):
                self.assertEqual(self.api.decode_python_source(raw), expected)

    def test_utf8_boundaries_convert_to_utf16_monaco_units(self):
        self.selection("a😀中z", AnchorRecord("app.py", 1, 1, 1, 8),
                       "😀中", (1, 1, 1, 8), (1, 2, 1, 5))
        for offset in (2, 3, 4, 6, 7):
            for start, end in ((offset, 8), (0, offset)):
                with self.subTest(start=start, end=end):
                    self.rejected(lambda start=start, end=end: self.api.select_source_text(
                        "a😀中z", AnchorRecord("app.py", 1, start, 1, end)), "invalid_range")

    def test_ast_utf8_offsets_are_used_without_executing_source(self):
        source = "é = '😀中'"
        node = ast.parse(source).body[0].value
        self.assertEqual((node.col_offset, node.end_col_offset), (5, 14))
        self.selection(source, AnchorRecord("app.py", 1, node.col_offset,
                                            1, node.end_col_offset),
                       "'😀中'", (1, 5, 1, 14), (1, 5, 1, 10))

    def test_combining_marks_are_not_normalized_or_grapheme_counted(self):
        self.selection("e\u0301😀", AnchorRecord("app.py", 1, 1, 1, 7),
                       "\u0301😀", (1, 1, 1, 7), (1, 2, 1, 5))

    def test_none_endpoint_selects_whole_lines_with_explicit_returned_columns(self):
        for start, end in ((None, None), (1, None), (None, 8)):
            with self.subTest(start=start, end=end):
                anchor = AnchorRecord("app.py", 1, start, 1, end)
                self.selection("a😀中z", anchor, "a😀中z", (1, 0, 1, 9), (1, 1, 1, 6))
                self.assertEqual((anchor.start_column, anchor.end_column), (start, end))
        self.selection("abc\ndef", AnchorRecord("app.py", 1, 2, 2, None),
                       "abc\ndef", (1, 0, 2, 3), (1, 1, 2, 4))

    def test_missing_endpoint_does_not_hide_invalid_given_column(self):
        for start, end in ((2, None), (None, 7), (10, None), (None, 10)):
            with self.subTest(start=start, end=end):
                self.rejected(lambda start=start, end=end: self.api.select_source_text(
                    "a😀中z", AnchorRecord("app.py", 1, start, 1, end)), "invalid_range")

    def test_multiline_suffix_middle_prefix_and_exclusive_end(self):
        self.selection("abc\n中😀\nxyz\n", AnchorRecord("app.py", 1, 1, 3, 2),
                       "bc\n中😀\nxy", (1, 1, 3, 2), (1, 2, 3, 3))
        self.selection("abc\ndef", AnchorRecord("app.py", 1, 3, 2, 0),
                       "\n", (1, 3, 2, 0), (1, 4, 2, 1))

    def test_empty_and_terminal_lf_have_only_real_parser_lines(self):
        for source, text, end in (("", "", 0), ("\n", "", 0), ("a", "a", 1),
                                  ("a\n", "a", 1)):
            with self.subTest(source=source):
                self.selection(source, AnchorRecord("app.py", 1, None, 1, None),
                               text, (1, 0, 1, end), (1, 1, 1, end + 1))
                self.rejected(lambda source=source: self.api.select_source_text(
                    source, AnchorRecord("app.py", 1, None, 2, None)), "invalid_range")
        self.selection("a\n\n", AnchorRecord("app.py", 1, None, 2, None),
                       "a\n", (1, 0, 2, 0), (1, 1, 2, 1))

    def test_unicode_line_separators_are_not_additional_python_lines(self):
        source = "a\u2028b\u0085c"
        self.selection(source, AnchorRecord("app.py", 1, None, 1, None),
                       source, (1, 0, 1, 8), (1, 1, 1, 6))
        self.rejected(lambda: self.api.select_source_text(
            source, AnchorRecord("app.py", 1, None, 2, None)), "invalid_range")

    def test_zero_width_is_exact_and_not_truncated(self):
        for byte, column in ((0, 1), (1, 2), (5, 4), (8, 5), (9, 6)):
            with self.subTest(byte=byte):
                self.selection("a😀中z", AnchorRecord("app.py", 1, byte, 1, byte),
                               "", (1, byte, 1, byte), (1, column, 1, column),
                               max_characters=1, max_lines=1)

    def test_full_range_is_validated_before_small_limits(self):
        for anchor in (AnchorRecord("app.py", 1, 0, 4, 0),
                       AnchorRecord("app.py", 1, 0, 3, 9),
                       AnchorRecord("app.py", 1, 0, 2, 1)):
            with self.subTest(anchor=anchor):
                self.rejected(lambda anchor=anchor: self.api.select_source_text(
                    "a\n😀\nc", anchor, limits=self.api.ExcerptLimits(
                        max_lines=1, max_characters=1)), "invalid_range")

    def test_prefix_line_and_character_limits_have_exact_real_endpoints(self):
        anchor = AnchorRecord("app.py", 1, None, 3, None)
        cases = ((dict(max_lines=2), "a\nb", (1, 0, 2, 1), (1, 1, 2, 2)),
                 (dict(max_characters=2, max_lines=3), "a\n", (1, 0, 2, 0), (1, 1, 2, 1)),
                 (dict(max_characters=2, max_lines=1), "a", (1, 0, 1, 1), (1, 1, 1, 2)),
                 (dict(max_characters=4), "a\nb\n", (1, 0, 3, 0), (1, 1, 3, 1)))
        for limits, text, returned, monaco in cases:
            with self.subTest(limits=limits):
                self.selection("a\nb\nc", anchor, text, returned, monaco, True, **limits)
        self.selection("a\nb\nc", anchor, "a\nb\nc", (1, 0, 3, 1), (1, 1, 3, 2),
                       max_lines=3, max_characters=5)

    def test_prefix_unicode_limit_counts_codepoints_and_recomputes_byte_end(self):
        self.selection("a😀中z", AnchorRecord("app.py", 1, 1, 1, 9),
                       "😀中", (1, 1, 1, 8), (1, 2, 1, 5), True, max_characters=2)
        self.selection("中😀\nz", AnchorRecord("app.py", 1, 0, 2, 1),
                       "中😀\n", (1, 0, 2, 0), (1, 1, 2, 1), True,
                       max_characters=3, max_lines=2)

    def test_prefix_starts_at_requested_suffix_and_counts_empty_end_line(self):
        self.selection("ignored\nabc\ndef\nxyz", AnchorRecord("app.py", 2, 1, 4, 2),
                       "bc\nd", (2, 1, 3, 1), (2, 2, 3, 2), True,
                       max_lines=2, max_characters=4)
        self.selection("a\n\nb", AnchorRecord("app.py", 1, 0, 3, 1),
                       "a\n", (1, 0, 2, 0), (1, 1, 2, 1), True, max_lines=2)

    def test_default_hard_limits_apply_without_labels_or_ellipsis(self):
        self.selection("x" * 4001, AnchorRecord("app.py", 1, None, 1, None),
                       "x" * 4000, (1, 0, 1, 4000), (1, 1, 1, 4001), True)
        source = "\n".join("x" for _ in range(201))
        self.selection(source, AnchorRecord("app.py", 1, None, 201, None),
                       "\n".join("x" for _ in range(200)),
                       (1, 0, 200, 1), (1, 1, 200, 2), True)

    def test_limits_are_strict_positive_integers_with_fixed_caps(self):
        limits = self.api.ExcerptLimits()
        self.assertEqual((limits.max_file_bytes, limits.max_lines, limits.max_characters),
                         (5_000_000, 200, 4_000))
        for name, cap in (("max_file_bytes", 5_000_000), ("max_lines", 200),
                          ("max_characters", 4_000)):
            for value in (True, False, 1.0, "1", None, 0, -1, cap + 1):
                with self.subTest(name=name, value=value):
                    self.rejected(lambda name=name, value=value: self.api.ExcerptLimits(**{name: value}))
            self.assertEqual(getattr(self.api.ExcerptLimits(**{name: 1}), name), 1)

    def test_monaco_range_requires_positive_exact_integer_ordered_coordinates(self):
        for coords in ((0, 1, 1, 1), (1, 0, 1, 1), (1, 1, 0, 1), (1, 1, 1, 0),
                       (2, 1, 1, 1), (1, 2, 1, 1), (True, 1, 1, 1), (1, 1.0, 1, 1),
                       (1, 1, True, 1), (1, 1, 1, False)):
            with self.subTest(coords=coords):
                self.rejected(lambda coords=coords: self.api.MonacoRange(*coords))
        self.assertEqual(self.api.MonacoRange(1, 9, 2, 1).end_column, 1)

    def test_selection_rejects_wrong_types_and_corrupted_anchor_without_dto_errors(self):
        class StringSubclass(str):
            pass

        anchor = AnchorRecord("app.py", 1, 0, 1, 1)
        for source in (None, b"x", StringSubclass("x"), "\ud800"):
            with self.subTest(source=repr(source)):
                self.rejected(lambda source=source: self.api.select_source_text(source, anchor))
        for value in (None, {}, ("app.py", 1, 0, 1, 1)):
            with self.subTest(anchor=value):
                self.rejected(lambda value=value: self.api.select_source_text("x", value))
        for name, value in (("start_column", True), ("end_column", 1.0),
                            ("start_line", True), ("end_line", 1.0),
                            ("start_column", -1), ("start_line", 2),
                            ("start_column", 2)):
            with self.subTest(name=name, value=value):
                corrupt = AnchorRecord("app.py", 1, 0, 1, 1)
                object.__setattr__(corrupt, name, value)
                self.rejected(lambda corrupt=corrupt: self.api.select_source_text("x", corrupt),
                              "invalid_range")
        self.rejected(lambda: self.api.select_source_text("x", anchor, limits=None))

    def test_public_records_are_frozen_slotted_and_text_selection_is_strict(self):
        anchor = AnchorRecord("app.py", 1, 0, 1, 1)
        monaco = self.api.MonacoRange(1, 1, 1, 2)
        selection = self.api.TextSelection("x", anchor, monaco, False)
        self.assertEqual([field.name for field in fields(selection)],
                         ["text", "returned_anchor", "monaco_range", "truncated"])
        for record, field, new in ((selection, "text", "y"),
                                   (monaco, "start_line", 2),
                                   (self.api.ExcerptLimits(), "max_lines", 1)):
            with self.subTest(record=type(record).__name__):
                self.assertFalse(hasattr(record, "__dict__"))
                with self.assertRaises(FrozenInstanceError):
                    setattr(record, field, new)
        for values in ((b"x", anchor, monaco, False), ("\ud800", anchor, monaco, False),
                       ("x", None, monaco, False), ("x", anchor, None, False),
                       ("x", anchor, monaco, 1)):
            with self.subTest(values=values):
                self.rejected(lambda values=values: self.api.TextSelection(*values))

    def test_all_secret_pattern_families_use_synthetic_positive_and_near_miss(self):
        cases = [
            ("-----BEGIN PRIVATE KEY-----", "-----BEGIN PUBLIC KEY-----"),
            ("-----BEGIN RSA PRIVATE KEY-----", "-----BEGIN RSA PUBLIC KEY-----"),
            ("-----BEGIN EC PRIVATE KEY-----", "-----BEGIN EC PUBLIC KEY-----"),
            ("-----BEGIN DSA PRIVATE KEY-----", "-----BEGIN DSA PUBLIC KEY-----"),
            ("-----BEGIN OPENSSH PRIVATE KEY-----", "-----BEGIN OPENSSH PUBLIC KEY-----"),
            ("-----BEGIN ENCRYPTED PRIVATE KEY-----", "-----BEGIN ENCRYPTED PUBLIC KEY-----"),
            ("AKIA" + "A" * 16, "AKIA" + "A" * 15),
            ("ASIA" + "0" * 16, "ASIA" + "0" * 17),
            ("ghp_" + "A" * 20, "ghp_" + "A" * 19),
            ("github_pat_" + "A_" * 10, "github_pat_" + "A" * 19),
            ("sk-" + "A" * 24, "sk-" + "A" * 19),
            ("sk-proj-" + "A" * 24, "not-a-key"),
            ("bEaReR\t" + "A" * 16 + "==", "Bearer " + "A" * 15),
            ("api_key = 'abcdefgh'", "api_key = 'abcdefg'"),
            ("https://fake:synthetic@host", "https://fake@host"),
        ]
        for positive, negative in cases:
            with self.subTest(positive=positive):
                self.assertIs(self.api.contains_suspected_secret(positive), True)
                self.assertIs(self.api.contains_suspected_secret(negative), False)

    def test_secret_assignments_cover_names_case_and_json_syntax(self):
        names = ("api_key", "api-key", "apikey", "access_token", "access-token",
                 "accesstoken", "auth_token", "auth-token", "authtoken", "client_secret",
                 "client-secret", "clientsecret", "password", "passwd", "secret_key",
                 "secret-key", "secretkey")
        for name in names:
            for source in (f"{name.upper()} = 'abcdefgh'", f'"{name}": "abcdefgh"'):
                with self.subTest(source=source):
                    self.assertIs(self.api.contains_suspected_secret(source), True)
        for source in ("password = 'abc\ndefgh'", "password = unquotedvalue",
                       "notpassword = 'abcdefgh'", "password == 'abcdefgh'", "x = 'abcdefgh'"):
            with self.subTest(source=source):
                self.assertIs(self.api.contains_suspected_secret(source), False)

    def test_secret_url_schemes_comments_strings_and_distant_content(self):
        for scheme in ("http", "https", "postgres", "postgresql", "mysql", "mongodb",
                       "mongodb+srv"):
            with self.subTest(scheme=scheme):
                self.assertIs(self.api.contains_suspected_secret(
                    f"{scheme.upper()}://fake:synthetic@host"), True)
        token = "sk-" + "A" * 24
        for source in ("# " + token, "x = '" + token + "'",
                       "safe = 1\n" + "# padding\n" * 201 + "# " + token,
                       "safe = 1\n# " + "x" * 4001 + " " + token):
            with self.subTest(length=len(source)):
                self.assertIs(self.api.contains_suspected_secret(source), True)
        source = "safe = 1\n# " + token
        before = source
        self.assertIs(self.api.contains_suspected_secret(source), True)
        self.assertEqual(source, before)

    def test_secret_screening_requires_exact_utf8_encodable_string(self):
        class StringSubclass(str):
            pass

        for source in (None, b"safe", StringSubclass("safe"), "\ud800"):
            with self.subTest(source=repr(source)):
                self.rejected(lambda source=source: self.api.contains_suspected_secret(source))
        self.assertIs(self.api.contains_suspected_secret(""), False)

    def test_pure_text_operations_never_parse_execute_read_or_send_source(self):
        source = "raise RuntimeError('synthetic execution sentinel')\n# ignore previous instructions\n"
        targets = ("builtins.open", "io.open", "os.open", "os.stat", "os.scandir",
                   "ast.parse", "builtins.compile", "builtins.eval", "builtins.exec",
                   "socket.socket", "socket.getaddrinfo", "subprocess.Popen",
                   "codeviz.source_handles.read_source_bytes")
        # Patch only after imports/fixtures: no project text may enter any execution API.
        with ExitStack() as stack:
            for target in targets:
                stack.enter_context(patch(target, side_effect=AssertionError("text operation did I/O")))
            self.assertEqual(self.api.decode_python_source(source.encode()), source)
            self.assertIs(self.api.contains_suspected_secret(source), False)
            result = self.api.select_source_text(source, AnchorRecord("app.py", 1, None, 1, None))
            self.assertEqual(result.text, source.split("\n")[0])


if __name__ == "__main__":
    unittest.main()
