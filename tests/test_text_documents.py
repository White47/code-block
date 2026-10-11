"""Generic text contract, using exclusively owned temporary projects."""

import codecs
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.web_bridge import DesktopBridge


class TextDocumentTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="codeviz-text-tests-")
        self.addCleanup(temporary.cleanup)
        self.scope = Path(temporary.name)
        self.root = self.scope / "project"
        self.root.mkdir()
        self.store = CacheStore(self.scope / "cache")
        self.controller = AppController(self.store)
        self.bridge = DesktopBridge(
            self.controller, self.store, PreferencesStore(self.scope / "preferences"))
        self.success(self.bridge.open_project(str(self.root)))

    def success(self, response):
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["schemaVersion"], 1)
        json.dumps(response)
        return response["data"]

    def api(self, name):
        method = getattr(self.bridge, name, None)
        self.assertTrue(callable(method), f"Approved text API missing: {name}")
        return method

    def open(self, name):
        return self.api("open_source_document")(
            str(self.root.resolve()), {"filePath": name, "startLine": 1, "endLine": 1,
                                      "startColumn": None, "endColumn": None})

    def save(self, document, text):
        request = {key: document[key] for key in ("projectPath", "filePath", "revision")}
        request["text"] = text
        return self.api("save_source_document")(request)

    def round_trip(self, name, original, text, edited, expected, newline="LF"):
        path = self.root / name
        path.write_bytes(original)
        document = self.success(self.open(name))
        self.assertEqual(document["text"], text)
        self.assertIsNone(document["readOnlyReason"])
        self.assertEqual(document["newline"], newline)
        self.assertEqual(path.read_bytes(), original)
        unchanged = self.success(self.save(document, text))
        self.assertEqual(path.read_bytes(), original)
        self.success(self.save(unchanged, edited))
        self.assertEqual(path.read_bytes(), expected)

    def rejected_text(self, name, original):
        path = self.root / name
        path.write_bytes(original)
        response = self.open(name)
        self.assertEqual(response["schemaVersion"], 1)
        if response["ok"]:
            document = self.success(response)
            self.assertTrue(document["readOnlyReason"], response)
            saved = self.save(document, "replacement\n")
            self.assertFalse(saved["ok"], saved)
            self.assertEqual(saved["error"]["code"], "SOURCE_READ_ONLY")
        else:
            self.assertIn(response["error"]["code"], ("SOURCE_READ_ONLY", "INVALID_REQUEST"))
        self.assertEqual(path.read_bytes(), original)

    def test_common_text_extensions_support_strict_utf8(self):
        for extension in ("txt", "md", "json", "yaml", "toml"):
            with self.subTest(extension=extension):
                self.round_trip(f"document.{extension}", "name = '中文'\n".encode(),
                                "name = '中文'\n", "name = 'été'\n",
                                "name = 'été'\n".encode())

    def assert_case_alias_invalidated(self, operation):
        path = self.root / "app.py"
        path.write_bytes(b"value = 1\n")
        (self.root / "other.py").write_bytes(b"value = 1\n")
        affected = self.success(self.open("APP.PY"))
        unaffected = self.success(self.open("other.py"))
        project = str(self.root.resolve())
        listing = self.success(self.api("list_project_directory")(
            {"projectPath": project, "directory": ""}))
        entry = next(item for item in listing["entries"] if item["path"] == "app.py")
        request = {"projectPath": project, "projectEpoch": listing["projectEpoch"],
                   "path": "app.py", "revision": entry["revision"], "operation": operation}
        if operation == "rename":
            request["newName"] = "renamed.py"
        prepared = self.success(self.api("prepare_project_entry_operation")(request))

        def recycle(source):
            self.assertEqual(Path(source), path)
            os.rename(source, self.scope / "recycled.py")

        with patch("codeviz.ui.recycle_bin.recycle_path", side_effect=recycle):
            self.success(self.api("apply_project_entry_operation")(
                {"projectPath": project, "projectEpoch": listing["projectEpoch"],
                 "operationToken": prepared["operationToken"]}))
        self.assertFalse(path.exists())
        destination = self.root / "renamed.py" if operation == "rename" else self.scope / "recycled.py"
        self.assertEqual(destination.read_bytes(), b"value = 1\n")
        with self.subTest(contract="case alias baseline invalidation"):
            self.assertNotIn(affected["revision"], self.controller._source_editing._documents)
        response = self.save(affected, "value = 2\n")
        self.assertEqual(response["schemaVersion"], 1)
        self.assertFalse(response["ok"], response)
        self.assertEqual(response["error"]["code"], "SOURCE_CONFLICT")
        self.assertEqual(destination.read_bytes(), b"value = 1\n")
        self.success(self.save(unaffected, "value = 2\n"))

    @unittest.skipUnless(sys.platform == "win32", "Windows case-insensitive path alias")
    def test_rename_invalidates_windows_case_alias_revision(self):
        self.assert_case_alias_invalidated("rename")

    @unittest.skipUnless(sys.platform == "win32", "Windows case-insensitive path alias")
    def test_delete_invalidates_windows_case_alias_revision(self):
        self.assert_case_alias_invalidated("delete")

    @unittest.skipUnless(sys.platform == "linux", "Linux case-sensitive source paths")
    def test_rename_preserves_linux_distinct_uppercase_source_revision(self):
        (self.root / "app.py").write_bytes(b"value = 1\n")
        uppercase = self.root / "APP.PY"
        uppercase.write_bytes(b"value = 3\n")
        unaffected = self.success(self.open("APP.PY"))
        project = str(self.root.resolve())
        listing = self.success(self.api("list_project_directory")(
            {"projectPath": project, "directory": ""}))
        entry = next(item for item in listing["entries"] if item["path"] == "app.py")
        prepared = self.success(self.api("prepare_project_entry_operation")(
            {"projectPath": project, "projectEpoch": listing["projectEpoch"],
             "path": "app.py", "revision": entry["revision"],
             "operation": "rename", "newName": "renamed.py"}))
        self.success(self.api("apply_project_entry_operation")(
            {"projectPath": project, "projectEpoch": listing["projectEpoch"],
             "operationToken": prepared["operationToken"]}))
        self.success(self.save(unaffected, "value = 4\n"))
        self.assertEqual(uppercase.read_bytes(), b"value = 4\n")
        self.assertEqual((self.root / "renamed.py").read_bytes(), b"value = 1\n")

    def test_utf8_bom_and_crlf_preserve_bytes(self):
        self.round_trip("readme.md", codecs.BOM_UTF8 + "中文\r\n".encode(),
                        "中文\r\n", "été\n", codecs.BOM_UTF8 + "été\r\n".encode(), "CRLF")

    def test_utf16_little_endian_bom_preserves_bytes(self):
        self.round_trip("settings.yaml", codecs.BOM_UTF16_LE + "中文\r\n".encode("utf-16-le"),
                        "中文\r\n", "été\n",
                        codecs.BOM_UTF16_LE + "été\r\n".encode("utf-16-le"), "CRLF")

    def test_utf16_big_endian_bom_preserves_bytes(self):
        self.round_trip("settings.toml", codecs.BOM_UTF16_BE + "中文\n".encode("utf-16-be"),
                        "中文\n", "été\n", codecs.BOM_UTF16_BE + "été\n".encode("utf-16-be"))

    def test_no_final_newline_is_not_added(self):
        self.round_trip("notes.txt", b"one", "one", "two", b"two")

    def test_empty_text_is_utf8_with_lf_default(self):
        self.round_trip("empty.txt", b"", "", "new\n", b"new\n")

    def test_generic_coding_cookie_does_not_enable_latin1(self):
        self.rejected_text("notes.txt", b"# coding: latin-1\ncaf\xe9\n")

    def test_invalid_utf8_is_rejected(self):
        self.rejected_text("notes.md", b"bad\xff\n")

    def test_nul_and_binary_are_rejected(self):
        for original in (b"text\x00data\n", b"\x89PNG\r\n\x1a\n\x00", b"text\x01\x02\n"):
            with self.subTest(original=original):
                self.rejected_text("binary.txt", original)

    def test_utf16_without_bom_is_rejected(self):
        self.rejected_text("notes.txt", "plain\n".encode("utf-16-le"))

    def test_utf32_bom_is_rejected_before_utf16_detection(self):
        for bom, encoding in ((codecs.BOM_UTF32_LE, "utf-32-le"),
                              (codecs.BOM_UTF32_BE, "utf-32-be")):
            with self.subTest(encoding=encoding):
                self.rejected_text("notes.txt", bom + "plain\n".encode(encoding))

    def test_utf16_decoded_nul_is_rejected(self):
        self.rejected_text("notes.txt", codecs.BOM_UTF16_LE + "a\x00b".encode("utf-16-le"))

    def test_mixed_and_cr_only_newlines_are_readonly(self):
        for original in (b"one\r\ntwo\n", b"one\rtwo\r"):
            with self.subTest(original=original):
                self.rejected_text("notes.txt", original)

    def test_unknown_extension_is_not_editable(self):
        self.rejected_text("notes.unknown", b"plain\n")

    def test_oversized_text_is_rejected_without_modification(self):
        self.rejected_text("notes.txt", b"x" * 5_000_001)

    def test_python_coding_cookie_still_preserves_latin1(self):
        self.round_trip("app.py", b"# coding: latin-1\nname = 'caf\xe9'\n",
                        "# coding: latin-1\nname = 'café'\n",
                        "# coding: latin-1\nname = 'été'\n",
                        b"# coding: latin-1\nname = '\xe9t\xe9'\n")

    def test_python_nul_document_cannot_open_as_editable(self):
        self.rejected_text("app.py", b"value=1\x00\n")

    def test_python_nul_draft_save_is_rejected_and_preserves_bytes(self):
        path = self.root / "valid.py"
        original = b"value = 1\n"
        path.write_bytes(original)
        document = self.success(self.open("valid.py"))
        self.assertIsNone(document["readOnlyReason"])
        response = self.save(document, "value = 2\x00\n")
        self.assertEqual(response["schemaVersion"], 1)
        json.dumps(response)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(response["ok"], response)
        self.assertIn(response["error"]["code"], ("SOURCE_READ_ONLY", "INVALID_REQUEST"))

    def test_protected_python_opens_readonly_and_cannot_save(self):
        for protected in (".git", ".venv", "venv", "node_modules"):
            with self.subTest(protected=protected):
                directory = self.root / protected
                directory.mkdir()
                path = directory / "protected.py"
                original = b"value = 1\n"
                path.write_bytes(original)
                document = self.success(self.open(f"{protected}/protected.py"))
                self.assertTrue(document["readOnlyReason"])
                response = self.save(document, "value = 2\n")
                self.assertFalse(response["ok"], response)
                self.assertEqual(response["error"]["code"], "SOURCE_READ_ONLY")
                self.assertEqual(path.read_bytes(), original)
