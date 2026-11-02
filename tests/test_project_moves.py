"""Approved tree move contract; every filesystem fixture is owned and temporary."""

import errno
import json
import os
import stat
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.persistence.store import CacheStore
from codeviz.ui import project_explorer
from codeviz.ui.controller import AnalysisStatus, AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.web_bridge import DesktopBridge


class ProjectMoveTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="codeviz-move-tests-")
        self.addCleanup(temporary.cleanup)
        self.scope = Path(temporary.name)
        self.root = self.scope / "project"
        self.root.mkdir()
        (self.root / "app.py").write_bytes(b"value = 1\n")
        (self.root / "destination").mkdir()
        self.project = str(self.root.resolve())
        self.store = CacheStore(self.scope / "cache")
        self.controller = AppController(self.store)
        self.bridge = DesktopBridge(
            self.controller, self.store, PreferencesStore(self.scope / "preferences"))
        self.success(self.bridge.open_project(self.project))

    def success(self, response):
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["schemaVersion"], 1)
        json.dumps(response)
        return response["data"]

    def rejected(self, response, code=None):
        self.assertFalse(response["ok"], response)
        self.assertEqual(response["schemaVersion"], 1)
        self.assertTrue(response["error"]["code"])
        if code is not None:
            self.assertEqual(response["error"]["code"], code)
        json.dumps(response)

    def listing(self, directory="", **options):
        request = {"projectPath": self.project, "directory": directory, **options}
        if directory and "projectEpoch" not in request:
            request["projectEpoch"] = self.success(self.bridge.list_project_directory(
                {"projectPath": self.project, "directory": ""}))["projectEpoch"]
        return self.success(self.bridge.list_project_directory(request))

    def request(self, path="app.py", destination="destination"):
        source = self.listing(path.rpartition("/")[0])
        target = self.listing(destination)
        revision = (source["directoryRevision"] if path == "" else
                    next(entry["revision"] for entry in source["entries"]
                         if entry["path"] == path))
        return {"projectPath": self.project, "projectEpoch": source["projectEpoch"],
                "path": path, "revision": revision, "operation": "move",
                "destinationDirectory": destination,
                "destinationRevision": target["directoryRevision"]}

    def prepare(self, path="app.py", destination="destination", **overrides):
        request = self.request(path, destination)
        request.update(overrides)
        return self.bridge.prepare_project_entry_operation(request)

    def supported(self):
        # Invalid cases must not pass merely because the bridge rejects every move.
        self.success(self.prepare())

    def apply(self, token, **overrides):
        request = {"projectPath": self.project,
                   "projectEpoch": self.listing()["projectEpoch"],
                   "operationToken": token["operationToken"], **overrides}
        return self.bridge.apply_project_entry_operation(request)

    def document(self, path):
        return self.success(self.bridge.open_source_document(
            self.project, {"filePath": path, "startLine": 1, "endLine": 1,
                           "startColumn": None, "endColumn": None}))

    def save(self, document):
        return self.bridge.save_source_document(
            {**{key: document[key] for key in ("projectPath", "filePath", "revision")},
             "text": "value = 2\n"})

    def folder(self, path="folder"):
        folder = self.root / path
        (folder / "nested").mkdir(parents=True)
        (folder / "nested/child.py").write_bytes(b"value = 1\n")
        (folder / "raw.bin").write_bytes(b"\x00\xff\x10")
        return folder

    def assert_unmoved(self, source="app.py", destination="destination/app.py",
                       content=b"value = 1\n"):
        self.assertEqual((self.root / source).read_bytes(), content)
        self.assertFalse((self.root / destination).exists())

    def test_file_move_prepare_is_readonly_and_apply_has_approved_schema(self):
        request = self.request()
        document = self.document("app.py")
        token = self.success(self.bridge.prepare_project_entry_operation(request))
        self.assertEqual(set(token), {"operationToken", "operation", "path", "newPath",
                                     "affectedPaths"})
        self.assertTrue(token["operationToken"])
        self.assertEqual((token["operation"], token["path"], token["newPath"]),
                         ("move", "app.py", "destination/app.py"))
        self.assertIn("app.py", token["affectedPaths"])
        self.assert_unmoved()
        result = self.success(self.apply(token))
        self.assertEqual(set(result), {"operation", "oldPath", "newPath", "affectedPaths"})
        self.assertEqual((result["operation"], result["oldPath"], result["newPath"]),
                         ("move", "app.py", "destination/app.py"))
        self.assertIn("app.py", result["affectedPaths"])
        self.assertFalse((self.root / "app.py").exists())
        self.assertEqual((self.root / "destination/app.py").read_bytes(), b"value = 1\n")
        self.rejected(self.save(document), "SOURCE_CONFLICT")
        self.assertEqual(self.listing()["projectEpoch"], request["projectEpoch"])
        self.assertEqual(self.success(self.bridge.get_session())["status"], "NotAnalyzed")

    def test_file_moves_to_root_without_new_name(self):
        path = self.root / "destination/notes.txt"
        content = b"\xef\xbb\xbfnotes\r\n"
        path.write_bytes(content)
        request = self.request("destination/notes.txt", "")
        self.assertNotIn("newName", request)
        self.assertEqual(request["destinationDirectory"], "")
        token = self.success(self.bridge.prepare_project_entry_operation(request))
        result = self.success(self.apply(token))
        self.assertEqual(result["newPath"], "notes.txt")
        self.assertEqual((self.root / "notes.txt").read_bytes(), content)
        self.assertFalse(path.exists())

    def test_folder_moves_into_directory_with_descendant_invalidation(self):
        folder = self.folder()
        document = self.document("folder/nested/child.py")
        token = self.success(self.prepare("folder"))
        affected = {"folder", "folder/nested", "folder/nested/child.py", "folder/raw.bin"}
        self.assertTrue(affected.issubset(token["affectedPaths"]), token)
        result = self.success(self.apply(token))
        self.assertTrue(affected.issubset(result["affectedPaths"]), result)
        self.assertEqual((result["oldPath"], result["newPath"]),
                         ("folder", "destination/folder"))
        self.assertFalse(folder.exists())
        self.assertEqual((self.root / "destination/folder/nested/child.py").read_bytes(),
                         b"value = 1\n")
        self.assertEqual((self.root / "destination/folder/raw.bin").read_bytes(), b"\x00\xff\x10")
        self.rejected(self.save(document), "SOURCE_CONFLICT")

    def test_folder_moves_to_root_with_bytes_and_old_revision_invalidated(self):
        folder = self.folder("destination/folder")
        document = self.document("destination/folder/nested/child.py")
        token = self.success(self.prepare("destination/folder", ""))
        result = self.success(self.apply(token))
        self.assertEqual(result["newPath"], "folder")
        self.assertFalse(folder.exists())
        self.assertEqual((self.root / "folder/raw.bin").read_bytes(), b"\x00\xff\x10")
        self.assertEqual((self.root / "folder/nested/child.py").read_bytes(), b"value = 1\n")
        self.rejected(self.save(document), "SOURCE_CONFLICT")

    def test_prepare_rejects_missing_extra_or_wrong_typed_move_fields(self):
        self.supported()
        request = self.request()
        for field in ("projectEpoch", "revision", "destinationDirectory", "destinationRevision"):
            with self.subTest(missing=field):
                invalid = dict(request)
                del invalid[field]
                self.rejected(self.bridge.prepare_project_entry_operation(invalid))
        for override in ({"newName": "other.py"}, {"destinationDirectory": None},
                         {"destinationDirectory": []}, {"destinationRevision": 1},
                         {"destinationRevision": ""}, {"unexpected": True}):
            with self.subTest(override=override):
                self.rejected(self.bridge.prepare_project_entry_operation({**request, **override}))
        self.assert_unmoved()

    def test_destination_revision_is_bound_to_exact_directory(self):
        self.supported()
        (self.root / "other").mkdir()
        wrong_revision = self.listing("other")["directoryRevision"]
        self.rejected(self.prepare(destinationRevision=wrong_revision))
        self.rejected(self.prepare(destinationRevision=self.request()["revision"]))
        self.assert_unmoved()

    def test_root_self_descendant_and_same_path_rejected(self):
        self.supported()
        self.folder()
        for source, target in (("", "destination"), ("folder", "folder"),
                               ("folder", "folder/nested"), ("app.py", "")):
            with self.subTest(source=source, target=target):
                self.rejected(self.prepare(source, target))
        self.assert_unmoved()
        self.assertEqual((self.root / "folder/nested/child.py").read_bytes(), b"value = 1\n")

    def test_file_missing_and_outside_targets_rejected_without_scaffolding(self):
        self.supported()
        outside = self.scope / "outside"
        outside.mkdir()
        (outside / "sentinel.txt").write_bytes(b"keep")
        request = self.request()
        for target in ("app.py", "missing/nested", "../outside", str(outside),
                       "C:\\escape", "//server/share", "destination:stream", "bad\x00name"):
            with self.subTest(target=target):
                self.rejected(self.bridge.prepare_project_entry_operation(
                    {**request, "destinationDirectory": target}))
        self.assert_unmoved()
        self.assertFalse((self.root / "missing").exists())
        self.assertEqual((outside / "sentinel.txt").read_bytes(), b"keep")

    def test_outside_source_rejected_without_modifying_external_file(self):
        self.supported()
        external = self.scope / "external.py"
        external.write_bytes(b"outside = 1\n")
        request = self.request()
        for source in ("../external.py", str(external), "C:\\external.py",
                       "//server/share/external.py", "app.py:stream"):
            with self.subTest(source=source):
                self.rejected(self.bridge.prepare_project_entry_operation({**request, "path": source}))
        self.assertEqual(external.read_bytes(), b"outside = 1\n")
        self.assert_unmoved()

    def test_existing_and_case_conflicting_landing_never_overwritten(self):
        self.supported()
        for name in ("app.py", "APP.PY"):
            with self.subTest(name=name):
                target = self.root / "destination" / name
                target.write_bytes(b"keep destination")
                self.rejected(self.prepare())
                self.assertEqual(target.read_bytes(), b"keep destination")
                self.assertEqual((self.root / "app.py").read_bytes(), b"value = 1\n")
                target.unlink()

    def test_directory_collision_never_merges_subtrees(self):
        self.supported()
        self.folder()
        target = self.root / "destination/folder"
        target.mkdir()
        (target / "sentinel.txt").write_bytes(b"keep destination")
        self.rejected(self.prepare("folder"))
        self.assertEqual((target / "sentinel.txt").read_bytes(), b"keep destination")
        self.assertFalse((target / "nested").exists())
        self.assertEqual((self.root / "folder/nested/child.py").read_bytes(), b"value = 1\n")

    def test_protected_source_or_landing_rejected(self):
        self.supported()
        for name in (".git", ".venv", "venv", "node_modules"):
            with self.subTest(name=name):
                protected = self.root / name
                protected.mkdir()
                (protected / "child.py").write_bytes(b"value = 1\n")
                self.rejected(self.prepare(name))
                self.rejected(self.prepare(f"{name}/child.py"))
                self.rejected(self.prepare("app.py", name))
                self.assertEqual((protected / "child.py").read_bytes(), b"value = 1\n")
                self.assertFalse((protected / "app.py").exists())
        self.assert_unmoved()

    def test_source_containing_protected_descendant_rejected(self):
        self.supported()
        self.folder()
        (self.root / "folder/nested/.git").mkdir()
        self.rejected(self.prepare("folder"))
        self.assertTrue((self.root / "folder/nested/.git").is_dir())
        self.assertFalse((self.root / "destination/folder").exists())

    def test_safe_landing_in_target_with_git_descendant_allows_create_and_move(self):
        (self.root / "destination/nested/.git").mkdir(parents=True)
        target = self.listing("destination")
        self.success(self.bridge.create_project_entry(
            {"projectPath": self.project, "projectEpoch": target["projectEpoch"],
             "parent": "destination", "parentRevision": target["directoryRevision"],
             "name": "created.txt", "kind": "file"}))
        token = self.success(self.prepare())
        self.success(self.apply(token))
        self.assertEqual((self.root / "destination/app.py").read_bytes(), b"value = 1\n")
        self.assertEqual((self.root / "destination/created.txt").read_bytes(), b"")
        self.assertTrue((self.root / "destination/nested/.git").is_dir())

    def test_disk_readonly_source_and_target_rejected(self):
        self.supported()
        for path in (self.root / "app.py", self.root / "destination"):
            with self.subTest(path=path.name):
                mode = path.stat().st_mode
                try:
                    path.chmod(stat.S_IREAD)
                    self.rejected(self.prepare())
                finally:
                    path.chmod(mode)
                self.assert_unmoved()

    def test_hardlinked_source_and_descendant_rejected(self):
        self.supported()
        folder = self.folder()
        for source in (self.root / "app.py", folder / "nested/child.py"):
            with self.subTest(source=source):
                alias = self.scope / "alias.py"
                try:
                    os.link(source, alias)
                except (OSError, NotImplementedError):
                    self.skipTest("OS does not permit creating hardlinks")
                try:
                    self.rejected(self.prepare("app.py" if source.name == "app.py" else "folder"))
                    self.assertEqual(alias.read_bytes(), b"value = 1\n")
                finally:
                    alias.unlink()
        self.assert_unmoved()
        self.assertFalse((self.root / "destination/folder").exists())

    def test_reparse_source_target_and_descendant_rejected_without_traversal(self):
        self.supported()
        outside = self.scope / "outside"
        outside.mkdir()
        (outside / "sentinel.txt").write_bytes(b"keep")
        link = self.root / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("OS does not permit creating symlinks")
        self.rejected(self.prepare("linked"))
        self.rejected(self.prepare(destinationDirectory="linked"))
        folder = self.folder()
        (folder / "nested/linked").symlink_to(outside, target_is_directory=True)
        self.rejected(self.prepare("folder"))
        self.assertEqual((outside / "sentinel.txt").read_bytes(), b"keep")
        self.assertFalse((outside / "app.py").exists())
        self.assert_unmoved()

    def test_stale_source_revision_rejected_even_with_preserved_mtime(self):
        self.supported()
        request = self.request()
        path = self.root / "app.py"
        before = path.stat()
        path.write_bytes(b"value = 9\n")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.rejected(self.bridge.prepare_project_entry_operation(request))
        self.assert_unmoved(content=b"value = 9\n")

    def test_stale_destination_revision_rejected_before_prepare(self):
        self.supported()
        request = self.request()
        (self.root / "destination/external.txt").write_bytes(b"keep")
        self.rejected(self.bridge.prepare_project_entry_operation(request))
        self.assert_unmoved()

    def test_source_and_destination_stale_after_prepare_rejected(self):
        for parent in ("", "destination"):
            with self.subTest(parent=parent):
                token = self.success(self.prepare())
                external = self.root / parent / "external.txt"
                external.write_bytes(b"keep")
                self.rejected(self.apply(token))
                self.assert_unmoved()
                self.assertEqual(external.read_bytes(), b"keep")
                external.unlink()

    def test_apply_detects_new_collision_including_case_conflict(self):
        for name in ("app.py", "APP.PY"):
            with self.subTest(name=name):
                token = self.success(self.prepare())
                target = self.root / "destination" / name
                target.write_bytes(b"keep destination")
                self.rejected(self.apply(token))
                self.assertEqual((self.root / "app.py").read_bytes(), b"value = 1\n")
                self.assertEqual(target.read_bytes(), b"keep destination")
                target.unlink()

    def test_apply_detects_source_content_change_preserving_mtime(self):
        token = self.success(self.prepare())
        source = self.root / "app.py"
        before = source.stat()
        source.write_bytes(b"value = 9\n")
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.rejected(self.apply(token))
        self.assert_unmoved(content=b"value = 9\n")

    def test_apply_detects_descendant_content_change_preserving_all_mtimes(self):
        folder = self.folder()
        token = self.success(self.prepare("folder"))
        child = folder / "nested/child.py"
        stamps = [(path, path.stat()) for path in (child, child.parent, folder)]
        child.write_bytes(b"value = 9\n")
        for path, before in stamps:
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.rejected(self.apply(token))
        self.assertEqual(child.read_bytes(), b"value = 9\n")
        self.assertFalse((self.root / "destination/folder").exists())

    def test_apply_detects_identical_bytes_source_replacement(self):
        token = self.success(self.prepare())
        replacement = self.scope / "replacement.py"
        replacement.write_bytes(b"value = 1\n")
        os.replace(replacement, self.root / "app.py")
        self.rejected(self.apply(token))
        self.assert_unmoved()

    def test_source_parent_replacement_rejected(self):
        self.folder()
        token = self.success(self.prepare("folder/nested/child.py"))
        os.rename(self.root / "folder", self.scope / "old-folder")
        self.folder()
        self.rejected(self.apply(token))
        self.assert_unmoved("folder/nested/child.py", "destination/child.py")
        self.assertEqual((self.scope / "old-folder/nested/child.py").read_bytes(), b"value = 1\n")

    def test_destination_directory_replacement_rejected(self):
        token = self.success(self.prepare())
        os.rename(self.root / "destination", self.scope / "old-destination")
        (self.root / "destination").mkdir()
        self.rejected(self.apply(token))
        self.assert_unmoved()
        self.assertEqual(list((self.scope / "old-destination").iterdir()), [])

    def test_destination_replaced_by_file_rejected(self):
        token = self.success(self.prepare())
        os.rename(self.root / "destination", self.scope / "old-destination")
        (self.root / "destination").write_bytes(b"keep replacement")
        self.rejected(self.apply(token))
        self.assertEqual((self.root / "app.py").read_bytes(), b"value = 1\n")
        self.assertEqual((self.root / "destination").read_bytes(), b"keep replacement")
        self.assertEqual(list((self.scope / "old-destination").iterdir()), [])

    def test_destination_replaced_by_reparse_point_rejected(self):
        outside = self.scope / "outside"
        outside.mkdir()
        (outside / "sentinel.txt").write_bytes(b"keep")
        probe = self.scope / "link-probe"
        try:
            probe.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("OS does not permit creating symlinks")
        token = self.success(self.prepare())
        os.rename(self.root / "destination", self.scope / "old-destination")
        (self.root / "destination").symlink_to(outside, target_is_directory=True)
        self.rejected(self.apply(token))
        self.assertEqual((self.root / "app.py").read_bytes(), b"value = 1\n")
        self.assertEqual((outside / "sentinel.txt").read_bytes(), b"keep")
        self.assertFalse((outside / "app.py").exists())

    def test_destination_ancestor_replacement_rejected(self):
        (self.root / "landing/inner").mkdir(parents=True)
        token = self.success(self.prepare(destination="landing/inner"))
        os.rename(self.root / "landing", self.scope / "old-landing")
        (self.root / "landing/inner").mkdir(parents=True)
        self.rejected(self.apply(token))
        self.assert_unmoved(destination="landing/inner/app.py")
        self.assertEqual(list((self.scope / "old-landing/inner").iterdir()), [])

    def test_protected_descendant_added_after_prepare_rejected(self):
        folder = self.folder()
        token = self.success(self.prepare("folder"))
        before = folder.stat()
        (folder / ".git").mkdir()
        os.utime(folder, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.rejected(self.apply(token))
        self.assertTrue((folder / ".git").is_dir())
        self.assertFalse((self.root / "destination/folder").exists())

    def test_move_token_single_use(self):
        token = self.success(self.prepare())
        self.success(self.apply(token))
        self.rejected(self.apply(token))
        self.assertEqual((self.root / "destination/app.py").read_bytes(), b"value = 1\n")
        self.assertFalse((self.root / "app.py").exists())

    def test_apply_cannot_override_token_bound_source_or_destination(self):
        token = self.success(self.prepare())
        for override in ({"path": "other.py"}, {"operation": "delete"},
                         {"destinationDirectory": ""}, {"destinationRevision": "other"},
                         {"newName": "renamed.py"}):
            with self.subTest(override=override):
                self.rejected(self.apply(token, **override), "INVALID_REQUEST")
                self.assert_unmoved()
        self.success(self.apply(token))
        self.assertEqual((self.root / "destination/app.py").read_bytes(), b"value = 1\n")

    def test_failed_conflict_consumes_token_even_after_collision_removed(self):
        token = self.success(self.prepare())
        collision = self.root / "destination/app.py"
        collision.write_bytes(b"keep")
        self.rejected(self.apply(token))
        collision.unlink()
        self.rejected(self.apply(token))
        self.assert_unmoved()

    def test_move_token_expires_at_sixty_seconds(self):
        now = time.monotonic()
        with patch.object(project_explorer.time, "monotonic", return_value=now):
            token = self.success(self.prepare())
        with patch.object(project_explorer.time, "monotonic", return_value=now + 60):
            self.rejected(self.apply(token))
        self.assert_unmoved()

    def test_move_token_still_valid_before_sixty_seconds(self):
        now = time.monotonic()
        with patch.object(project_explorer.time, "monotonic", return_value=now):
            token = self.success(self.prepare())
        with patch.object(project_explorer.time, "monotonic", return_value=now + 59):
            self.success(self.apply(token))
        self.assertEqual((self.root / "destination/app.py").read_bytes(), b"value = 1\n")

    def test_wrong_epoch_and_other_project_cannot_prepare_or_apply(self):
        request = self.request()
        token = self.success(self.bridge.prepare_project_entry_operation(request))
        other = self.scope / "other"
        other.mkdir()
        for override in ({"projectEpoch": "old-epoch"}, {"projectPath": str(other)}):
            with self.subTest(override=override):
                self.rejected(self.bridge.prepare_project_entry_operation({**request, **override}))
                self.rejected(self.apply(token, **override))
        self.assert_unmoved()

    def test_project_switch_and_reopen_invalidate_old_epoch_and_token(self):
        request = self.request()
        token = self.success(self.bridge.prepare_project_entry_operation(request))
        other = self.scope / "other"
        other.mkdir()
        self.success(self.bridge.open_project(str(other)))
        self.rejected(self.bridge.prepare_project_entry_operation(request))
        self.rejected(self.bridge.apply_project_entry_operation(
            {"projectPath": self.project, "projectEpoch": request["projectEpoch"],
             "operationToken": token["operationToken"]}))
        self.success(self.bridge.open_project(self.project))
        self.assertNotEqual(self.listing()["projectEpoch"], request["projectEpoch"])
        self.rejected(self.bridge.prepare_project_entry_operation(request))
        self.rejected(self.apply(token))
        self.assert_unmoved()

    def test_native_rename_preserves_identity_without_copy_delete(self):
        token = self.success(self.prepare())
        before = (self.root / "app.py").stat()
        with patch.object(project_explorer, "_rename_no_replace",
                          wraps=project_explorer._rename_no_replace) as rename, \
                patch("shutil.copyfile") as copyfile, patch("shutil.copy2") as copy2, \
                patch("shutil.move") as move, patch("os.unlink") as unlink, \
                patch("shutil.rmtree") as rmtree, \
                patch.object(project_explorer.recycle_bin, "recycle_path") as recycle:
            self.success(self.apply(token))
        rename.assert_called_once_with(self.root / "app.py", self.root / "destination/app.py")
        for fallback in (copyfile, copy2, move, unlink, rmtree, recycle):
            fallback.assert_not_called()
        after = (self.root / "destination/app.py").stat()
        self.assertEqual((after.st_dev, after.st_ino), (before.st_dev, before.st_ino))
        self.assertEqual((self.root / "destination/app.py").read_bytes(), b"value = 1\n")

    def test_native_failure_including_cross_volume_never_falls_back(self):
        for failure in (OSError(errno.EXDEV, "private-move-detail"),
                        PermissionError("private-move-detail"),
                        NotImplementedError("private-move-detail")):
            with self.subTest(failure=type(failure).__name__):
                token = self.success(self.prepare())
                with patch.object(project_explorer, "_rename_no_replace", side_effect=failure), \
                        patch("shutil.copyfile") as copyfile, patch("shutil.copy2") as copy2, \
                        patch("shutil.move") as move, patch("os.unlink") as unlink, \
                        patch("shutil.rmtree") as rmtree, \
                        patch.object(project_explorer.recycle_bin, "recycle_path") as recycle:
                    response = self.apply(token)
                self.rejected(response)
                self.assertNotIn("private-move-detail", json.dumps(response))
                for fallback in (copyfile, copy2, move, unlink, rmtree, recycle):
                    fallback.assert_not_called()
                self.rejected(self.apply(token))
                self.assert_unmoved()

    def test_collision_at_native_rename_boundary_never_overwrites(self):
        token = self.success(self.prepare())
        native_rename = project_explorer._rename_no_replace
        target = self.root / "destination/app.py"

        def collide_then_rename(source, destination):
            self.assertEqual(destination, target)
            target.write_bytes(b"racing destination")
            return native_rename(source, destination)

        with patch.object(project_explorer, "_rename_no_replace", side_effect=collide_then_rename):
            response = self.apply(token)
        self.rejected(response)
        self.assertEqual((self.root / "app.py").read_bytes(), b"value = 1\n")
        self.assertEqual(target.read_bytes(), b"racing destination")
        self.rejected(self.apply(token))

    def test_native_move_then_raise_invalidates_both_prefixes_and_consumes_token(self):
        native_rename = project_explorer._rename_no_replace
        source = self.folder()
        destination = self.folder("destination/folder")
        destination_document = self.document("destination/folder/nested/child.py")
        archived = self.scope / "previous-destination"
        native_rename(destination, archived)
        self.success(self.bridge.analyze())
        self.assertEqual(self.success(self.bridge.get_session())["status"], "Ready")
        analysis = self.controller.analysis
        source_document = self.document("folder/nested/child.py")
        unaffected = self.document("app.py")
        documents = self.controller._source_editing._documents
        for document in (source_document, destination_document, unaffected):
            self.assertIn(document["revision"], documents)
        token = self.success(self.prepare("folder"))

        def rename_then_raise(old_path, new_path):
            native_rename(old_path, new_path)
            raise OSError("private-move-after-rename-detail")

        with patch.object(project_explorer, "_rename_no_replace",
                          side_effect=rename_then_raise) as rename:
            response = self.apply(token)
            self.rejected(response, "FILE_OPERATION_FAILED")
            self.assertNotIn("private-move-after-rename-detail", json.dumps(response))
            self.assertFalse(source.exists())
            self.assertEqual((destination / "nested/child.py").read_bytes(), b"value = 1\n")
            self.assertEqual((destination / "raw.bin").read_bytes(), b"\x00\xff\x10")
            self.assertEqual(self.success(self.bridge.get_session())["status"], "Outdated")
            self.assertIs(self.controller.analysis, analysis)
            for document in (source_document, destination_document):
                with self.subTest(path=document["filePath"]):
                    self.assertNotIn(document["revision"], documents)
                    self.rejected(self.save(document), "SOURCE_CONFLICT")
            self.assertIn(unaffected["revision"], documents)
            self.rejected(self.apply(token), "FILE_CONFLICT")
            # Restore the paths so replay cannot pass just because the source is missing.
            native_rename(destination, source)
            self.rejected(self.apply(token), "FILE_CONFLICT")
            rename.assert_called_once_with(source, destination)

        self.assertFalse(destination.exists())
        for folder in (source, archived):
            self.assertEqual((folder / "nested/child.py").read_bytes(), b"value = 1\n")
            self.assertEqual((folder / "raw.bin").read_bytes(), b"\x00\xff\x10")
        self.assertEqual((self.root / "app.py").read_bytes(), b"value = 1\n")

    def test_move_marks_outdated_preserving_analysis_imports_and_unaffected_document(self):
        (self.root / "other.py").write_bytes(b"value = 1\n")
        importer = b"from app import value\n"
        (self.root / "importer.py").write_bytes(importer)
        self.success(self.bridge.analyze())
        analysis = self.controller.analysis
        self.controller.move_node("test-note", (12.0, 23.0))
        layout = self.controller.view_state
        self.store.save_annotations(self.root, {"test-note": "keep"})
        cache = self.store.analysis_path(self.root).read_bytes()
        affected = self.document("app.py")
        unaffected = self.document("other.py")
        token = self.success(self.prepare())
        with patch("codeviz.ui.controller.analyze_project") as analyze:
            self.success(self.apply(token))
        analyze.assert_not_called()
        self.assertIs(self.controller.analysis, analysis)
        self.assertEqual(self.controller.view_state, layout)
        self.assertEqual(self.store.load_annotations(self.root), {"test-note": "keep"})
        self.assertEqual(self.store.analysis_path(self.root).read_bytes(), cache)
        self.assertEqual(self.success(self.bridge.get_session())["status"], "Outdated")
        self.rejected(self.save(affected), "SOURCE_CONFLICT")
        self.success(self.save(unaffected))
        self.assertEqual((self.root / "importer.py").read_bytes(), importer)
        self.assertEqual((self.root / "destination/app.py").read_bytes(), b"value = 1\n")

    def test_move_never_executes_source_and_preserves_extension_facts(self):
        marker = self.scope / "executed.txt"
        source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n".encode()
        (self.root / "app.py").write_bytes(source)
        (self.root / "opaque.bin").write_bytes(b"\x00\xff")
        for name, content, can_open in (("app.py", source, True),
                                        ("opaque.bin", b"\x00\xff", False)):
            with self.subTest(name=name):
                token = self.success(self.prepare(name))
                self.success(self.apply(token))
                listing = self.listing("destination")
                entry = next(item for item in listing["entries"] if item["name"] == name)
                self.assertEqual(entry["kind"], "file")
                self.assertEqual(entry["canOpen"], can_open)
                self.assertEqual((self.root / "destination" / name).read_bytes(), content)
                self.assertFalse((self.root / name).exists())
        self.assertFalse(marker.exists())
        self.assertEqual(self.controller.status, AnalysisStatus.NOT_ANALYZED)

    def test_analyzing_blocks_move_prepare_and_apply(self):
        request = self.request()
        token = self.success(self.bridge.prepare_project_entry_operation(request))
        self.controller.status = AnalysisStatus.ANALYZING
        self.rejected(self.bridge.prepare_project_entry_operation(request), "NOT_READY")
        self.rejected(self.apply(token), "NOT_READY")
        self.assert_unmoved()

    def test_move_invalidates_both_directory_cursors_and_retains_paging(self):
        for index in range(201):
            (self.root / f"file-{index:03}.txt").touch()
            (self.root / "destination" / f"entry-{index:03}.txt").touch()
        source = self.listing()
        target = self.listing("destination")
        token = self.success(self.prepare())
        self.success(self.apply(token))
        for directory, old in (("", source), ("destination", target)):
            with self.subTest(directory=directory):
                self.rejected(self.bridge.list_project_directory(
                    {"projectPath": self.project, "projectEpoch": old["projectEpoch"],
                     "directory": directory, "cursor": old["nextCursor"]}))
                first = self.listing(directory)
                second = self.listing(directory, projectEpoch=first["projectEpoch"],
                                      cursor=first["nextCursor"])
                self.assertEqual([len(first["entries"]), len(second["entries"])], [200, 2])
                self.assertIsNone(second["nextCursor"])
                paths = [entry["path"] for page in (first, second) for entry in page["entries"]]
                self.assertEqual(len(paths), len(set(paths)))
                expected = {path.relative_to(self.root).as_posix()
                            for path in (self.root / directory).iterdir()}
                self.assertEqual(set(paths), expected)

    def test_subtree_entry_limit_rejects_move_without_truncation(self):
        self.supported()
        folder = self.root / "folder"
        folder.mkdir()
        for index in range(5001):
            (folder / f"{index:04}.txt").touch()
        self.rejected(self.prepare("folder"), "FILE_LIMIT")
        self.assertEqual(len(list(folder.iterdir())), 5001)
        self.assertFalse((self.root / "destination/folder").exists())

    def test_subtree_depth_limit_rejects_move_without_mutation(self):
        self.supported()
        deepest = self.root / "folder"
        deepest.mkdir()
        for _ in range(65):
            deepest = deepest / "d"
            deepest.mkdir()
        (deepest / "sentinel.txt").write_bytes(b"keep")
        self.rejected(self.prepare("folder"), "FILE_LIMIT")
        self.assertEqual((deepest / "sentinel.txt").read_bytes(), b"keep")
        self.assertFalse((self.root / "destination/folder").exists())

    def test_subtree_byte_limit_rejects_move_without_mutation(self):
        self.supported()
        folder = self.root / "folder"
        folder.mkdir()
        path = folder / "large.bin"
        size = 64 * 1024 * 1024 + 1
        with path.open("wb") as output:
            output.truncate(size)
        self.rejected(self.prepare("folder"), "FILE_LIMIT")
        self.assertEqual(path.stat().st_size, size)
        self.assertFalse((self.root / "destination/folder").exists())
