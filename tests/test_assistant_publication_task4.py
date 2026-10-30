"""C3 Task 4 RED contracts at real source and Explorer mutation boundaries."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AnalysisStatus, AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.source_editing import SourceConflictError
from codeviz.ui.web_bridge import DesktopBridge


class PublicationTask4Tests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="codeviz-publication-task4-")
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
        self.success(self.bridge.analyze())

    def success(self, response):
        self.assertTrue(response["ok"], response)
        json.dumps(response)
        return response["data"]

    def rejected(self, response, code):
        self.assertFalse(response["ok"], response)
        self.assertEqual(response["error"]["code"], code)
        json.dumps(response)

    def context(self):
        return self.controller.capture_assistant_context()

    def document(self):
        return self.success(self.bridge.open_source_document(str(self.root), {
            "filePath": "app.py", "startLine": 1, "startColumn": None,
            "endLine": 1, "endColumn": None,
        }))

    def save(self, document, text="value = 2\n"):
        return self.bridge.save_source_document({
            "projectPath": document["projectPath"], "filePath": document["filePath"],
            "revision": document["revision"], "text": text,
        })

    def listing(self):
        return self.success(self.bridge.list_project_directory({
            "projectPath": str(self.root), "directory": "",
        }))

    def create(self, listing, *, name="new.py", kind="file", revision=None):
        return self.bridge.create_project_entry({
            "projectPath": str(self.root), "projectEpoch": listing["projectEpoch"],
            "parent": "", "parentRevision": revision or listing["directoryRevision"],
            "name": name, "kind": kind,
        })

    def prepare_rename(self, listing):
        entry = next(item for item in listing["entries"] if item["path"] == "app.py")
        return self.bridge.prepare_project_entry_operation({
            "projectPath": str(self.root), "projectEpoch": listing["projectEpoch"],
            "path": "app.py", "revision": entry["revision"],
            "operation": "rename", "newName": "renamed.py",
        })

    def apply(self, listing, token):
        return self.bridge.apply_project_entry_operation({
            "projectPath": str(self.root), "projectEpoch": listing["projectEpoch"],
            "operationToken": token,
        })

    def assert_invalidated_once(self, before, after):
        self.assertTrue(before.eligible)
        self.assertFalse(after.eligible)
        self.assertIsNone(after.snapshot)
        self.assertEqual(dict(after.context_map), {})
        self.assertEqual(after.project_epoch, before.project_epoch)
        self.assertEqual(after.analysis_generation, before.analysis_generation + 1)
        self.assertEqual(after.status, AnalysisStatus.OUTDATED)

    def test_source_rejections_and_native_handle_failure_preserve_identity(self):
        document = self.document()
        before = self.context()
        self.rejected(self.save({**document, "revision": "wrong"}), "SOURCE_PROJECT_CHANGED")
        self.assertIs(self.context(), before)
        with patch("codeviz.ui.source_editing.encode_text", side_effect=UnicodeError):
            self.rejected(self.save(document), "SOURCE_READ_ONLY")
        self.assertIs(self.context(), before)
        # A failed native handle identity check is still pre-hook.
        from codeviz.ui import source_editing

        opened_file_path = source_editing.opened_file_path

        def wrong_write_handle(stream):
            if stream.mode in {"rb+", "r+b"}:
                return self.scope / "not-the-source.py"
            return opened_file_path(stream)

        with patch("codeviz.ui.source_editing.opened_file_path",
                   side_effect=wrong_write_handle):
            self.rejected(self.save(document), "SOURCE_CONFLICT")
        self.assertIs(self.context(), before)
        self.assertEqual(self.path.read_bytes(), b"value = 1\n")

    def test_source_hook_is_after_native_handle_check_and_before_mkstemp(self):
        document = self.document()
        before = self.context()
        seen = []
        native_open = Path.open

        def observe_open(path, mode="r", *args, **kwargs):
            if path == self.path and mode == "r+b":
                seen.append(("handle", self.context()))
            return native_open(path, mode, *args, **kwargs)

        def fail_mkstemp(*args, **kwargs):
            seen.append(("mkstemp", self.context()))
            raise PermissionError("native create refused")

        with patch.object(Path, "open", observe_open), \
                patch("codeviz.ui.source_editing.tempfile.mkstemp", side_effect=fail_mkstemp):
            self.rejected(self.save(document), "SOURCE_SAVE_FAILED")
        self.assertEqual([name for name, _ in seen], ["handle", "mkstemp"])
        self.assertIs(seen[0][1], before)
        self.assert_invalidated_once(before, seen[1][1])
        self.assert_invalidated_once(before, self.context())
        self.assertEqual(self.path.read_bytes(), b"value = 1\n")

    def test_source_second_verify_conflict_stays_ineligible_and_cleans_temp(self):
        document = self.document()
        before = self.context()
        files = set(self.root.iterdir())
        verify = self.controller._source_editing._verify
        seen = []

        def conflict_on_second(*args):
            seen.append(self.context())
            if len(seen) == 2:
                raise SourceConflictError()
            return verify(*args)

        with patch.object(self.controller._source_editing, "_verify",
                          side_effect=conflict_on_second):
            self.rejected(self.save(document), "SOURCE_CONFLICT")
        self.assertEqual(len(seen), 2)
        self.assertIs(seen[0], before)
        self.assert_invalidated_once(before, seen[1])
        self.assert_invalidated_once(before, self.context())
        self.assertEqual(set(self.root.iterdir()), files)
        self.assertEqual(self.path.read_bytes(), b"value = 1\n")

    def test_source_replace_failure_stays_ineligible_without_replacing_original(self):
        document = self.document()
        before = self.context()
        files = set(self.root.iterdir())
        seen = []

        def fail_replace(*args):
            seen.append(self.context())
            raise OSError("native replace refused")

        with patch("codeviz.ui.source_editing.os.replace", side_effect=fail_replace):
            self.rejected(self.save(document), "SOURCE_SAVE_FAILED")
        self.assertEqual(len(seen), 1)
        self.assert_invalidated_once(before, seen[0])
        self.assert_invalidated_once(before, self.context())
        self.assertEqual(set(self.root.iterdir()), files)
        self.assertEqual(self.path.read_bytes(), b"value = 1\n")

    def test_explorer_preflight_rejections_and_prepare_leave_identity_untouched(self):
        listing = self.listing()
        before = self.context()
        self.assertFalse(self.create(listing, name="../escape.py")["ok"])
        self.assertIs(self.context(), before)
        self.rejected(self.create(listing, revision="invalid"), "FILE_CONFLICT")
        self.assertIs(self.context(), before)
        self.assertFalse(self.create(listing, name=".git", kind="directory")["ok"])
        self.assertIs(self.context(), before)
        self.assertFalse(self.bridge.prepare_project_entry_operation({
            "projectPath": str(self.root), "projectEpoch": listing["projectEpoch"],
            "path": "app.py", "revision": "invalid", "operation": "rename",
            "newName": "renamed.py",
        })["ok"])
        self.assertIs(self.context(), before)
        prepared = self.success(self.prepare_rename(listing))
        self.assertIs(self.context(), before)
        self.rejected(self.apply(listing, "stale-token"), "FILE_CONFLICT")
        self.assertIs(self.context(), before)
        # A final destination check fails before the native rename and consumes the token.
        (self.root / "renamed.py").write_bytes(b"existing\n")
        self.rejected(self.apply(listing, prepared["operationToken"]), "FILE_CONFLICT")
        self.assertIs(self.context(), before)
        self.rejected(self.apply(listing, prepared["operationToken"]), "FILE_CONFLICT")
        self.assertIs(self.context(), before)

    def test_explorer_create_native_failure_invalidates_even_without_disk_mutation(self):
        listing = self.listing()
        before = self.context()
        seen = []
        native_open = Path.open

        def fail_native_create(path, mode="r", *args, **kwargs):
            if path == self.root / "new.py" and mode == "xb":
                seen.append(self.context())
                raise PermissionError("native create refused")
            return native_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", fail_native_create):
            self.rejected(self.create(listing), "FILE_CREATE_FAILED")
        self.assertEqual(len(seen), 1)
        self.assert_invalidated_once(before, seen[0])
        self.assert_invalidated_once(before, self.context())
        self.assertFalse((self.root / "new.py").exists())

    def test_explorer_apply_native_failure_invalidates_once_and_consumes_token(self):
        listing = self.listing()
        document = self.document()
        prepared = self.success(self.prepare_rename(listing))
        before = self.context()
        seen = []

        def fail_rename(*args):
            seen.append(self.context())
            raise OSError("native rename refused")

        with patch("codeviz.ui.project_explorer._rename_no_replace", side_effect=fail_rename):
            self.rejected(self.apply(listing, prepared["operationToken"]),
                          "FILE_OPERATION_FAILED")
        self.assertEqual(len(seen), 1)
        self.assert_invalidated_once(before, seen[0])
        self.assert_invalidated_once(before, self.context())
        self.assertEqual(self.path.read_bytes(), b"value = 1\n")
        self.rejected(self.apply(listing, prepared["operationToken"]), "FILE_CONFLICT")
        self.rejected(self.save(document), "SOURCE_CONFLICT")

    def test_bridge_success_mutations_increment_once_and_reads_do_not_invalidate(self):
        initial = self.context()
        listing = self.listing()
        document = self.document()
        prepared = self.success(self.prepare_rename(listing))
        self.success(self.bridge.get_session())
        self.assertIs(self.context(), initial)

        self.success(self.save(document))
        saved = self.context()
        self.assert_invalidated_once(initial, saved)
        self.success(self.bridge.analyze())
        ready = self.context()
        listing = self.listing()
        self.success(self.create(listing))
        created = self.context()
        self.assert_invalidated_once(ready, created)
        self.success(self.bridge.analyze())
        ready = self.context()
        listing = self.listing()
        prepared = self.success(self.prepare_rename(listing))
        self.assertIs(self.context(), ready)
        self.success(self.apply(listing, prepared["operationToken"]))
        self.assert_invalidated_once(ready, self.context())

    def test_bridge_analysis_blocks_source_and_explorer_before_any_mutation_hook(self):
        document = self.document()
        listing = self.listing()
        prepared = self.success(self.prepare_rename(listing))

        def blocked_operations():
            analyzing = self.context()
            with patch.object(self.controller, "_assistant_invalidate",
                              wraps=self.controller._assistant_invalidate) as invalidate:
                self.assertFalse(self.save(document)["ok"])
                self.assertFalse(self.create(listing)["ok"])
                self.assertFalse(self.apply(listing, prepared["operationToken"])["ok"])
                invalidate.assert_not_called()
            self.assertIs(self.context(), analyzing)
            self.assertFalse((self.root / "new.py").exists())
            self.assertTrue(self.path.exists())

        self.controller.analyze(before_analysis=blocked_operations)


if __name__ == "__main__":
    unittest.main()
