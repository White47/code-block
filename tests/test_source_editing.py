"""Approved editing contract; all writable source lives in owned temp fixtures."""

import json
import os
import stat
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AnalysisStatus, AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.web_bridge import DesktopBridge


class SourceEditingTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="codeviz-edit-tests-")
        self.addCleanup(temporary.cleanup)
        self.scope = Path(temporary.name)
        self.root = self.scope / "project"
        self.root.mkdir()
        self.path = self.root / "app.py"
        self.path.write_bytes(b"value = 1\n")
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

    def error(self, response, code):
        self.assertFalse(response["ok"], response)
        self.assertEqual(response["error"]["code"], code)
        return response["error"]

    def api(self, name):
        method = getattr(self.bridge, name, None)
        self.assertTrue(callable(method), f"Approved editing API missing: {name}")
        return method

    def open(self, file_path="app.py", project_path=None):
        anchor = {"filePath": file_path, "startLine": 1, "endLine": 1,
                  "startColumn": None, "endColumn": None}
        return self.api("open_source_document")(
            str(self.root.resolve()) if project_path is None else project_path, anchor)

    def save(self, document, text="value = 2\n", **overrides):
        request = {key: document[key] for key in ("projectPath", "filePath", "revision")}
        request["text"] = text
        request.update(overrides)
        return self.api("save_source_document")(request)

    def test_open_returns_exact_editable_schema_without_changing_bytes(self):
        before = self.path.read_bytes()
        document = self.success(self.open())
        self.assertEqual(set(document), {"projectPath", "filePath", "text", "revision",
                                         "encoding", "newline", "readOnlyReason"})
        self.assertEqual(document["projectPath"], str(self.root.resolve()))
        self.assertEqual(document["filePath"], "app.py")
        self.assertEqual(document["text"], "value = 1\n")
        self.assertTrue(document["revision"])
        self.assertEqual(document["newline"], "LF")
        self.assertIsNone(document["readOnlyReason"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_save_refreshes_revision_and_keeps_unanalyzed_status(self):
        document = self.success(self.open())
        saved = self.success(self.save(document))
        self.assertEqual(self.path.read_bytes(), b"value = 2\n")
        self.assertEqual(saved["text"], "value = 2\n")
        self.assertNotEqual(saved["revision"], document["revision"])
        self.assertEqual(self.controller.status, AnalysisStatus.NOT_ANALYZED)
        self.assertEqual(self.success(self.bridge.get_session())["status"], "NotAnalyzed")

    def test_save_marks_analysis_outdated_preserving_facts_layout_annotations_and_cache(self):
        self.success(self.bridge.analyze())
        analysis = self.controller.analysis
        self.controller.move_node("test-note", (12.0, 23.0))
        layout = self.controller.view_state
        self.store.save_annotations(self.root, {"test-note": "keep"})
        cache = self.store.analysis_path(self.root).read_bytes()
        document = self.success(self.open())
        with patch("codeviz.ui.controller.analyze_project") as analyze:
            self.success(self.save(document))
        analyze.assert_not_called()
        self.assertIs(self.controller.analysis, analysis)
        self.assertEqual(self.controller.view_state, layout)
        self.assertEqual(self.store.load_annotations(self.root), {"test-note": "keep"})
        self.assertEqual(self.store.analysis_path(self.root).read_bytes(), cache)
        self.assertEqual(self.success(self.bridge.get_session())["status"], "Outdated")

    def test_external_same_length_same_mtime_edit_conflicts(self):
        document = self.success(self.open())
        before = self.path.stat()
        self.path.write_bytes(b"value = 9\n")
        os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.error(self.save(document), "SOURCE_CONFLICT")
        self.assertEqual(self.path.read_bytes(), b"value = 9\n")

    def test_external_identical_bytes_file_replacement_conflicts(self):
        document = self.success(self.open())
        replacement = self.root / "replacement.py"
        replacement.write_bytes(self.path.read_bytes())
        os.replace(replacement, self.path)
        self.error(self.save(document), "SOURCE_CONFLICT")
        self.assertEqual(self.path.read_bytes(), b"value = 1\n")

    def test_deleted_source_conflicts_without_recreation(self):
        document = self.success(self.open())
        self.path.unlink()
        self.error(self.save(document), "SOURCE_CONFLICT")
        self.assertFalse(self.path.exists())

    def test_old_revision_cannot_overwrite_a_successful_save(self):
        document = self.success(self.open())
        self.success(self.save(document))
        self.error(self.save(document, "value = 3\n"), "SOURCE_CONFLICT")
        self.assertEqual(self.path.read_bytes(), b"value = 2\n")

    def test_project_switch_rejects_old_document_without_touching_either_project(self):
        document = self.success(self.open())
        other = self.scope / "other"
        other.mkdir()
        (other / "app.py").write_bytes(b"other = 1\n")
        self.success(self.bridge.open_project(str(other)))
        self.error(self.save(document), "SOURCE_PROJECT_CHANGED")
        self.assertEqual(self.path.read_bytes(), b"value = 1\n")
        self.assertEqual((other / "app.py").read_bytes(), b"other = 1\n")

    def test_a_b_a_opening_epoch_invalidates_previous_revision(self):
        document = self.success(self.open())
        other = self.scope / "other"
        other.mkdir()
        self.success(self.bridge.open_project(str(other)))
        self.success(self.bridge.open_project(str(self.root)))
        self.error(self.save(document), "SOURCE_PROJECT_CHANGED")
        self.assertEqual(self.path.read_bytes(), b"value = 1\n")

    def test_same_path_reopen_invalidates_old_revision_and_fresh_open_can_save(self):
        old = self.success(self.open())
        self.success(self.bridge.open_project(str(self.root)))
        self.error(self.save(old), "SOURCE_PROJECT_CHANGED")
        self.assertEqual(self.path.read_bytes(), b"value = 1\n")
        fresh = self.success(self.open())
        self.assertNotEqual(old["revision"], fresh["revision"])
        self.success(self.save(fresh))
        self.assertEqual(self.path.read_bytes(), b"value = 2\n")

    def test_utf8_bom_and_crlf_round_trip(self):
        self.path.write_bytes(b"\xef\xbb\xbfvalue = 1\r\n")
        document = self.success(self.open())
        self.assertEqual(document["newline"], "CRLF")
        self.assertIsNone(document["readOnlyReason"])
        self.success(self.save(document, "value = 2\n"))
        self.assertEqual(self.path.read_bytes(), b"\xef\xbb\xbfvalue = 2\r\n")

    def test_python_encoding_cookie_round_trip(self):
        self.path.write_bytes(b"# coding: latin-1\nname = 'caf\xe9'\n")
        document = self.success(self.open())
        self.assertIn("café", document["text"])
        self.assertIsNone(document["readOnlyReason"])
        self.success(self.save(document, "# coding: latin-1\nname = 'été'\n"))
        self.assertEqual(self.path.read_bytes(), b"# coding: latin-1\nname = '\xe9t\xe9'\n")

    def test_unencodable_draft_does_not_change_original(self):
        before = b"# coding: ascii\nvalue = 1\n"
        self.path.write_bytes(before)
        document = self.success(self.open())
        response = self.save(document, "# coding: ascii\nname = '中文'\n")
        self.assertFalse(response["ok"], response)
        self.assertEqual(self.path.read_bytes(), before)

    def test_mixed_eol_is_read_only(self):
        before = b"first = 1\r\nsecond = 2\n"
        self.path.write_bytes(before)
        document = self.success(self.open())
        self.assertEqual(document["newline"], "Mixed")
        self.assertTrue(document["readOnlyReason"])
        self.error(self.save(document), "SOURCE_READ_ONLY")
        self.assertEqual(self.path.read_bytes(), before)

    def test_no_newline_defaults_lf_without_appending_newline(self):
        self.path.write_bytes(b"value = 1")
        document = self.success(self.open())
        self.assertEqual(document["newline"], "LF")
        self.success(self.save(document, "value = 2"))
        self.assertEqual(self.path.read_bytes(), b"value = 2")

    def test_readonly_permission_cannot_be_bypassed_by_replace(self):
        document = self.success(self.open())
        mode = self.path.stat().st_mode
        self.addCleanup(self.path.chmod, mode)
        self.path.chmod(stat.S_IREAD)
        self.error(self.save(document), "SOURCE_READ_ONLY")
        self.assertEqual(self.path.read_bytes(), b"value = 1\n")

    def test_success_preserves_permission_bits(self):
        before = stat.S_IMODE(self.path.stat().st_mode)
        document = self.success(self.open())
        self.success(self.save(document))
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), before)

    @unittest.skipUnless(sys.platform == "win32", "Windows security descriptor test")
    def test_success_preserves_windows_owner_group_and_dacl(self):
        import ctypes
        from ctypes import wintypes

        get_security = ctypes.WinDLL("advapi32", use_last_error=True).GetFileSecurityW
        get_security.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID,
                                 wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
        get_security.restype = wintypes.BOOL

        def descriptor():
            size = wintypes.DWORD()
            get_security(str(self.path), 7, None, 0, ctypes.byref(size))
            self.assertGreater(size.value, 0)
            buffer = ctypes.create_string_buffer(size.value)
            self.assertTrue(get_security(
                str(self.path), 7, buffer, size.value, ctypes.byref(size)))
            return buffer.raw[:size.value]

        document = self.success(self.open())
        before = descriptor()
        self.success(self.save(document))
        self.assertEqual(descriptor(), before)

    def test_replace_failure_preserves_bytes_state_and_cleans_only_owned_temp(self):
        document = self.success(self.open())
        sentinel = self.root / ".unrelated.tmp"
        sentinel.write_bytes(b"keep")
        files = set(self.root.iterdir())
        with patch("os.replace", side_effect=PermissionError("private-system-detail")):
            response = self.save(document)
        self.assertFalse(response["ok"], response)
        self.assertNotIn("private-system-detail", json.dumps(response))
        self.assertEqual(self.path.read_bytes(), b"value = 1\n")
        self.assertEqual(set(self.root.iterdir()), files)
        self.assertEqual(sentinel.read_bytes(), b"keep")
        self.assertEqual(self.controller.status, AnalysisStatus.NOT_ANALYZED)
        self.success(self.save(document))

    def test_fsync_failure_does_not_replace_original(self):
        document = self.success(self.open())
        files = set(self.root.iterdir())
        with patch("os.fsync", side_effect=OSError("private-fsync-detail")):
            response = self.save(document)
        self.assertFalse(response["ok"], response)
        self.assertNotIn("private-fsync-detail", json.dumps(response))
        self.assertEqual(self.path.read_bytes(), b"value = 1\n")
        self.assertEqual(set(self.root.iterdir()), files)

    def test_external_edit_after_temp_fsync_is_detected_before_replace(self):
        document = self.success(self.open())
        original_fsync = os.fsync

        def external_edit(descriptor):
            original_fsync(descriptor)
            self.path.write_bytes(b"value = 9\n")

        with patch("os.fsync", side_effect=external_edit):
            self.error(self.save(document), "SOURCE_CONFLICT")
        self.assertEqual(self.path.read_bytes(), b"value = 9\n")
        self.assertEqual(set(self.root.iterdir()), {self.path})

    def test_cr_only_eol_is_read_only_and_preserves_original_bytes(self):
        original = b"value = 1\r"
        self.path.write_bytes(original)
        document = self.success(self.open())
        self.assertEqual(document["newline"], "CR")
        self.assertTrue(document["readOnlyReason"])
        self.error(self.save(document, "value = 2\n"), "SOURCE_READ_ONLY")
        self.assertEqual(self.path.read_bytes(), original)

    def test_invalid_source_encoding_never_becomes_editable_replacement_text(self):
        original = b"value = '\xff'\n"
        self.path.write_bytes(original)
        response = self.open()
        if response["ok"]:
            document = self.success(response)
            self.assertTrue(document["readOnlyReason"])
            self.error(self.save(document), "SOURCE_READ_ONLY")
        else:
            self.assertIn(response["error"]["code"], ("SOURCE_READ_ONLY", "INVALID_REQUEST"))
        self.assertEqual(self.path.read_bytes(), original)

    def test_oversized_disk_document_is_rejected_without_modification(self):
        self.path.write_bytes(b"#" + b"x" * 5_000_000)
        self.error(self.open(), "INVALID_REQUEST")
        self.assertEqual(self.path.stat().st_size, 5_000_001)

    def test_open_requires_exact_anchor_schema(self):
        method = self.api("open_source_document")
        anchor = {"filePath": "app.py", "startLine": 1, "endLine": 1,
                  "startColumn": None, "endColumn": None}
        for invalid in (None, [], {**anchor, "extra": 1}, {**anchor, "startLine": True}):
            with self.subTest(anchor_type=type(invalid).__name__):
                self.error(method(str(self.root.resolve()), invalid), "INVALID_REQUEST")

    def test_open_rejects_project_mismatch(self):
        other = self.scope / "other"
        other.mkdir()
        self.error(self.open(project_path=str(other)), "SOURCE_PROJECT_CHANGED")

    def test_revision_cannot_be_used_for_another_file(self):
        other = self.root / "other.py"
        other.write_bytes(self.path.read_bytes())
        document = self.success(self.open())
        self.error(self.save(document, filePath="other.py"), "SOURCE_CONFLICT")
        self.assertEqual(other.read_bytes(), b"value = 1\n")

    def test_source_is_never_executed(self):
        marker = self.scope / "executed"
        payload = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
        self.path.write_bytes(payload.encode())
        document = self.success(self.open())
        self.success(self.save(document, payload + "# saved\n"))
        self.assertFalse(marker.exists())

    def test_hardlinked_source_cannot_be_saved(self):
        alias = self.scope / "alias.py"
        os.link(self.path, alias)
        response = self.open()
        if response["ok"]:
            document = self.success(response)
            self.assertTrue(document["readOnlyReason"])
            self.error(self.save(document), "SOURCE_READ_ONLY")
        else:
            self.assertIn(response["error"]["code"], ("SOURCE_READ_ONLY", "INVALID_REQUEST"))
        self.assertEqual(alias.read_bytes(), b"value = 1\n")

    def test_symlink_traversal_inside_project_is_not_editable(self):
        self.api("open_source_document")
        directory = self.root / "real"
        directory.mkdir()
        target = directory / "linked.py"
        target.write_bytes(b"value = 1\n")
        link = self.root / "link"
        try:
            link.symlink_to(directory, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation unavailable")
        response = self.open("link/linked.py")
        if response["ok"]:
            document = self.success(response)
            self.assertTrue(document["readOnlyReason"])
            self.assertFalse(self.save(document)["ok"])
        else:
            self.assertIn(response["error"]["code"], ("SOURCE_READ_ONLY", "INVALID_REQUEST"))
        self.assertEqual(target.read_bytes(), b"value = 1\n")

    def test_invalid_paths_rejected(self):
        self.api("open_source_document")
        for value in ("../app.py", str(self.path), "app.py\x00", "app.txt",
                      "//server/share/app.py", "C:\\outside\\app.py"):
            with self.subTest(path=value):
                self.error(self.open(value), "INVALID_REQUEST")

    def test_save_requires_exact_bounded_request(self):
        document = self.success(self.open())
        method = self.api("save_source_document")
        valid = {key: document[key] for key in ("projectPath", "filePath", "text", "revision")}
        requests = [None, [], {**valid, "extra": 1}, {**valid, "text": False},
                    {**valid, "revision": "x" * 100_000},
                    {**valid, "text": "中" * 1_666_667}]
        requests.extend({key: value for key, value in valid.items() if key != missing}
                        for missing in valid)
        for request in requests:
            with self.subTest(fields=type(request).__name__):
                self.error(method(request), "INVALID_REQUEST")
                self.assertEqual(self.path.read_bytes(), b"value = 1\n")

    def test_analysis_in_progress_rejects_save(self):
        document = self.success(self.open())
        self.controller.status = AnalysisStatus.ANALYZING
        self.error(self.save(document), "NOT_READY")
        self.assertEqual(self.path.read_bytes(), b"value = 1\n")
