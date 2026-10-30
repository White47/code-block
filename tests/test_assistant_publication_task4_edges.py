"""Supplementary C3 Task 4 contracts at approved write boundaries."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AnalysisStatus, AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.web_bridge import DesktopBridge


class PublicationTask4EdgeTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="codeviz-publication-task4-edges-")
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

    def assert_invalidated_once(self, before, after):
        self.assertTrue(before.eligible)
        self.assertFalse(after.eligible)
        self.assertIsNone(after.snapshot)
        self.assertEqual(dict(after.context_map), {})
        self.assertEqual(after.project_epoch, before.project_epoch)
        self.assertEqual(after.analysis_generation, before.analysis_generation + 1)
        self.assertEqual(after.status, AnalysisStatus.OUTDATED)

    def listing(self, directory="", *, epoch=None):
        request = {"projectPath": str(self.root), "directory": directory}
        if epoch is not None:
            request["projectEpoch"] = epoch
        return self.success(self.bridge.list_project_directory(request))

    def entry(self, listing, path):
        return next(item for item in listing["entries"] if item["path"] == path)

    def prepare(self, listing, operation, **target):
        return self.success(self.bridge.prepare_project_entry_operation({
            "projectPath": str(self.root), "projectEpoch": listing["projectEpoch"],
            "path": "app.py", "revision": self.entry(listing, "app.py")["revision"],
            "operation": operation, **target,
        }))

    def apply(self, listing, prepared):
        return self.bridge.apply_project_entry_operation({
            "projectPath": str(self.root), "projectEpoch": listing["projectEpoch"],
            "operationToken": prepared["operationToken"],
        })

    def test_explorer_mkdir_native_failure_invalidates_without_creating_directory(self):
        listing = self.listing()
        before = self.context()
        target = self.root / "new_folder"
        observed = []
        original_mkdir = Path.mkdir

        def fail_target_mkdir(path, *args, **kwargs):
            if path == target:
                observed.append(self.context())
                raise PermissionError("native mkdir refused")
            return original_mkdir(path, *args, **kwargs)

        with patch.object(Path, "mkdir", new=fail_target_mkdir):
            response = self.bridge.create_project_entry({
                "projectPath": str(self.root), "projectEpoch": listing["projectEpoch"],
                "parent": "", "parentRevision": listing["directoryRevision"],
                "name": target.name, "kind": "directory",
            })

        self.rejected(response, "FILE_CREATE_FAILED")
        self.assertEqual(len(observed), 1)
        self.assert_invalidated_once(before, observed[0])
        self.assert_invalidated_once(before, self.context())
        self.assertFalse(target.exists())
        self.assertEqual(self.path.read_bytes(), b"value = 1\n")

    def test_explorer_recycle_native_failure_invalidates_and_consumes_token(self):
        listing = self.listing()
        prepared = self.prepare(listing, "delete")
        before = self.context()
        observed = []

        def fail_recycle(path):
            self.assertEqual(path, self.path)
            observed.append(self.context())
            raise PermissionError("native recycle refused")

        with patch("codeviz.ui.project_explorer.recycle_bin.recycle_path",
                   side_effect=fail_recycle) as recycle:
            self.rejected(self.apply(listing, prepared), "FILE_OPERATION_FAILED")
        recycle.assert_called_once_with(self.path)
        self.assertEqual(len(observed), 1)
        self.assert_invalidated_once(before, observed[0])
        self.assert_invalidated_once(before, self.context())
        self.assertEqual(self.path.read_bytes(), b"value = 1\n")
        self.rejected(self.apply(listing, prepared), "FILE_CONFLICT")
        self.assert_invalidated_once(before, self.context())

    def test_explorer_move_native_failure_invalidates_and_consumes_token(self):
        destination = self.root / "destination"
        destination.mkdir()
        listing = self.listing()
        target_listing = self.listing("destination", epoch=listing["projectEpoch"])
        prepared = self.prepare(
            listing, "move", destinationDirectory="destination",
            destinationRevision=target_listing["directoryRevision"])
        before = self.context()
        observed = []

        def fail_move(source, target):
            self.assertEqual((source, target), (self.path, destination / "app.py"))
            observed.append(self.context())
            raise PermissionError("native move refused")

        with patch("codeviz.ui.project_explorer._rename_no_replace",
                   side_effect=fail_move) as move:
            self.rejected(self.apply(listing, prepared), "FILE_OPERATION_FAILED")
        move.assert_called_once_with(self.path, destination / "app.py")
        self.assertEqual(len(observed), 1)
        self.assert_invalidated_once(before, observed[0])
        self.assert_invalidated_once(before, self.context())
        self.assertEqual(self.path.read_bytes(), b"value = 1\n")
        self.assertFalse((destination / "app.py").exists())
        self.rejected(self.apply(listing, prepared), "FILE_CONFLICT")
        self.assert_invalidated_once(before, self.context())

    def test_move_final_preflight_rejection_preserves_identity(self):
        destination = self.root / "destination"
        destination.mkdir()
        listing = self.listing()
        target_listing = self.listing("destination", epoch=listing["projectEpoch"])
        prepared = self.prepare(
            listing, "move", destinationDirectory="destination",
            destinationRevision=target_listing["directoryRevision"])
        before = self.context()
        collision = destination / "app.py"
        collision.write_bytes(b"existing\n")

        with patch("codeviz.ui.project_explorer._rename_no_replace") as move:
            self.rejected(self.apply(listing, prepared), "FILE_CONFLICT")
        move.assert_not_called()
        self.assertIs(self.context(), before)
        self.assertEqual(self.path.read_bytes(), b"value = 1\n")
        self.assertEqual(collision.read_bytes(), b"existing\n")
        self.rejected(self.apply(listing, prepared), "FILE_CONFLICT")
        self.assertIs(self.context(), before)

    def test_protected_source_path_rejects_before_prewrite_hook(self):
        protected = self.root / ".venv"
        protected.mkdir()
        source = protected / "module.py"
        source.write_bytes(b"value = 1\n")
        document = self.success(self.bridge.open_source_document(str(self.root), {
            "filePath": ".venv/module.py", "startLine": 1, "startColumn": None,
            "endLine": 1, "endColumn": None,
        }))
        self.assertTrue(document["readOnlyReason"])
        before = self.context()
        with patch("codeviz.ui.source_editing.tempfile.mkstemp") as mkstemp:
            self.rejected(self.bridge.save_source_document({
                "projectPath": document["projectPath"],
                "filePath": document["filePath"],
                "revision": document["revision"], "text": "value = 2\n",
            }), "SOURCE_READ_ONLY")
        mkstemp.assert_not_called()
        self.assertIs(self.context(), before)
        self.assertEqual(source.read_bytes(), b"value = 1\n")


if __name__ == "__main__":
    unittest.main()
