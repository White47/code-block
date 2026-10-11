"""Approved Explorer contract; mutations target owned temporary fixtures only."""

import importlib
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


class ProjectExplorerTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="codeviz-explorer-tests-")
        self.addCleanup(temporary.cleanup)
        self.scope = Path(temporary.name)
        self.root = self.scope / "project"
        self.root.mkdir()
        (self.root / "app.py").write_bytes(b"value = 1\n")
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

    def api(self, name):
        method = getattr(self.bridge, name, None)
        self.assertTrue(callable(method), f"Approved Explorer API missing: {name}")
        return method

    def listing(self, directory="", **options):
        return self.api("list_project_directory")(
            {"projectPath": self.project, "directory": directory, **options})

    def bootstrap(self):
        return self.success(self.listing())

    def create_request(self, directory, name="new.txt", kind="file"):
        return {"projectPath": self.project, "projectEpoch": directory["projectEpoch"],
                "parent": directory["directory"],
                "parentRevision": directory["directoryRevision"], "name": name, "kind": kind}

    def create(self, directory, name="new.txt", kind="file", **overrides):
        request = self.create_request(directory, name, kind)
        request.update(overrides)
        return self.api("create_project_entry")(request)

    def entry(self, directory, path):
        return next(entry for entry in directory["entries"] if entry["path"] == path)

    def prepare(self, directory, path, operation="rename", new_name="renamed.py", **overrides):
        request = {"projectPath": self.project, "projectEpoch": directory["projectEpoch"],
                   "path": path, "revision": self.entry(directory, path)["revision"],
                   "operation": operation}
        if operation == "rename":
            request["newName"] = new_name
        request.update(overrides)
        return self.api("prepare_project_entry_operation")(request)

    def apply(self, directory, prepared, **overrides):
        return self.api("apply_project_entry_operation")(
            {"projectPath": self.project, "projectEpoch": directory["projectEpoch"],
             "operationToken": prepared["operationToken"], **overrides})

    def document(self, path="app.py"):
        return self.success(self.api("open_source_document")(
            self.project, {"filePath": path, "startLine": 1, "endLine": 1,
                           "startColumn": None, "endColumn": None}))

    def save(self, document):
        return self.api("save_source_document")(
            {**{key: document[key] for key in ("projectPath", "filePath", "revision")},
             "text": "value = 2\n"})

    def recycle_adapter(self):
        # Gate the feature first: baseline RED must never be a missing-module error.
        self.api("apply_project_entry_operation")
        try:
            module = importlib.import_module("codeviz.ui.recycle_bin")
        except ModuleNotFoundError as error:
            if error.name != "codeviz.ui.recycle_bin":
                raise
            self.fail("Approved lower-level recycle adapter module missing")
        method = getattr(module, "recycle_path", None)
        self.assertTrue(callable(method), "Approved recycle_bin.recycle_path adapter missing")
        return module

    def test_root_bootstrap_schema_and_no_analysis(self):
        with patch("codeviz.ui.controller.analyze_project") as analyze:
            data = self.bootstrap()
        analyze.assert_not_called()
        self.assertEqual(set(data), {"projectPath", "projectEpoch", "directory",
                                     "directoryRevision", "entries", "nextCursor"})
        self.assertEqual(data["projectPath"], self.project)
        self.assertEqual(data["directory"], "")
        self.assertTrue(data["projectEpoch"])
        self.assertTrue(data["directoryRevision"])
        entry = self.entry(data, "app.py")
        self.assertEqual(set(entry), {"path", "name", "kind", "revision", "readOnlyReason",
                                      "canOpen", "canCreate", "canRename", "canDelete"})
        self.assertEqual((entry["name"], entry["kind"]), ("app.py", "file"))
        self.assertTrue(entry["revision"])
        self.assertIsNone(entry["readOnlyReason"])
        self.assertTrue(entry["canOpen"])
        self.assertFalse(entry["canCreate"])
        self.assertTrue(entry["canRename"])
        self.assertEqual(entry["canDelete"], sys.platform == "win32")
        self.assertIsNone(data["nextCursor"])
        self.assertEqual(self.controller.status, AnalysisStatus.NOT_ANALYZED)

    def test_directories_first_name_sort_and_no_recursive_entries(self):
        self.api("list_project_directory")
        for name in ("z-dir", "a-dir"):
            directory = self.root / name
            directory.mkdir()
            (directory / "hidden-child.txt").write_bytes(b"child")
        (self.root / "z.txt").write_bytes(b"z")
        (self.root / "a.txt").write_bytes(b"a")
        data = self.bootstrap()
        self.assertEqual([entry["path"] for entry in data["entries"]],
                         ["a-dir", "z-dir", "a.txt", "app.py", "z.txt"])

    def test_pages_are_200_with_no_duplicates_or_omissions(self):
        self.api("list_project_directory")
        for index in range(400):
            (self.root / f"entry-{index:03}.txt").write_bytes(b"")
        first = self.bootstrap()
        second = self.success(self.listing(projectEpoch=first["projectEpoch"],
                                           cursor=first["nextCursor"]))
        third = self.success(self.listing(projectEpoch=first["projectEpoch"],
                                          cursor=second["nextCursor"]))
        self.assertEqual([len(page["entries"]) for page in (first, second, third)], [200, 200, 1])
        self.assertTrue(first["nextCursor"])
        self.assertTrue(second["nextCursor"])
        self.assertIsNone(third["nextCursor"])
        paths = [entry["path"] for page in (first, second, third) for entry in page["entries"]]
        self.assertEqual(paths, sorted(path.name for path in self.root.iterdir()))
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(first["directoryRevision"], second["directoryRevision"])

    def test_nested_listing_requires_epoch_and_returns_relative_paths(self):
        self.api("list_project_directory")
        (self.root / "folder").mkdir()
        (self.root / "folder" / "child.txt").write_bytes(b"child")
        root = self.bootstrap()
        self.rejected(self.listing("folder"))
        child = self.success(self.listing("folder", projectEpoch=root["projectEpoch"]))
        self.assertEqual(child["directory"], "folder")
        self.assertEqual(child["projectEpoch"], root["projectEpoch"])
        self.assertEqual([entry["path"] for entry in child["entries"]], ["folder/child.txt"])

    def test_pagination_requires_epoch(self):
        self.api("list_project_directory")
        for index in range(201):
            (self.root / f"{index:03}.txt").touch()
        root = self.bootstrap()
        self.rejected(self.listing(cursor=root["nextCursor"]))

    def test_cursor_rejects_changed_directory(self):
        self.api("list_project_directory")
        for index in range(201):
            (self.root / f"{index:03}.txt").touch()
        root = self.bootstrap()
        (self.root / "external.txt").touch()
        self.rejected(self.listing(projectEpoch=root["projectEpoch"], cursor=root["nextCursor"]))

    def test_wrong_epoch_rejects_listing_and_create(self):
        root = self.bootstrap()
        self.rejected(self.listing(projectEpoch="wrong-epoch"))
        self.rejected(self.create(root, projectEpoch="wrong-epoch"))
        self.assertFalse((self.root / "new.txt").exists())

    def test_wrong_epoch_rejects_prepare_and_apply(self):
        root = self.bootstrap()
        token = self.success(self.prepare(root, "app.py"))
        self.rejected(self.prepare(root, "app.py", projectEpoch="wrong-epoch"))
        self.rejected(self.apply(root, token, projectEpoch="wrong-epoch"))
        self.assertEqual((self.root / "app.py").read_bytes(), b"value = 1\n")

    def test_project_switch_rejects_all_old_explorer_requests(self):
        root = self.bootstrap()
        token = self.success(self.prepare(root, "app.py"))
        other = self.scope / "other"
        other.mkdir()
        (other / "app.py").write_bytes(b"other = 1\n")
        self.success(self.bridge.open_project(str(other)))
        self.rejected(self.listing(projectEpoch=root["projectEpoch"]))
        self.rejected(self.create(root))
        self.rejected(self.prepare(root, "app.py"))
        self.rejected(self.apply(root, token))
        self.assertEqual((self.root / "app.py").read_bytes(), b"value = 1\n")
        self.assertEqual((other / "app.py").read_bytes(), b"other = 1\n")

    def test_a_b_a_invalidates_epoch_token_and_source_revision(self):
        root = self.bootstrap()
        document = self.document()
        token = self.success(self.prepare(root, "app.py"))
        other = self.scope / "other"
        other.mkdir()
        self.success(self.bridge.open_project(str(other)))
        self.success(self.bridge.open_project(self.project))
        fresh = self.bootstrap()
        self.assertNotEqual(root["projectEpoch"], fresh["projectEpoch"])
        self.rejected(self.listing(projectEpoch=root["projectEpoch"]))
        self.rejected(self.create(root))
        self.rejected(self.apply(root, token))
        self.rejected(self.save(document), "SOURCE_PROJECT_CHANGED")
        self.assertEqual((self.root / "app.py").read_bytes(), b"value = 1\n")

    def test_create_empty_file_schema_utf8_lf_and_unanalyzed_status(self):
        root = self.bootstrap()
        result = self.success(self.create(root))
        self.assertEqual(result, {"operation": "create", "oldPath": None,
                                  "newPath": "new.txt", "affectedPaths": ["new.txt"]})
        self.assertEqual((self.root / "new.txt").read_bytes(), b"")
        document = self.document("new.txt")
        self.assertEqual(document["text"], "")
        self.assertEqual(document["newline"], "LF")
        self.assertIn(document["encoding"].lower().replace("_", "-"), ("utf-8", "utf8"))
        self.assertEqual(self.success(self.bridge.get_session())["status"], "NotAnalyzed")

    def test_create_directory(self):
        root = self.bootstrap()
        result = self.success(self.create(root, "folder", "directory"))
        self.assertEqual(result["newPath"], "folder")
        self.assertTrue((self.root / "folder").is_dir())
        self.assertEqual(list((self.root / "folder").iterdir()), [])

    def test_create_file_close_failure_invalidates_baseline_without_rollback(self):
        target = self.root / "created.py"
        target.write_bytes(b"")
        affected = self.document("created.py")
        target.unlink()
        self.success(self.bridge.analyze())
        root = self.bootstrap()
        original_open = Path.open
        handles = []

        class CloseThenFail:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                return self.handle.__enter__()

            def __exit__(self, *args):
                self.handle.__exit__(*args)
                raise OSError("private-create-close-detail")

        def open_target(path, mode="r", *args, **kwargs):
            handle = original_open(path, mode, *args, **kwargs)
            if path == target and mode == "xb":
                handles.append(handle)
                return CloseThenFail(handle)
            return handle

        with patch.object(Path, "open", new=open_target):
            response = self.create(root, target.name)
        self.assertEqual(len(handles), 1)
        self.assertTrue(handles[0].closed)
        self.assertEqual(target.read_bytes(), b"")
        self.assertNotIn("private-create-close-detail", json.dumps(response))
        with self.subTest(contract="operation failure"):
            self.rejected(response, "FILE_OPERATION_FAILED")
        with self.subTest(contract="analysis invalidation"):
            self.assertEqual(self.success(self.bridge.get_session())["status"], "Outdated")
        with self.subTest(contract="affected source invalidation"):
            self.assertNotIn(affected["revision"], self.controller._source_editing._documents)
            self.rejected(self.save(affected), "SOURCE_CONFLICT")
        self.assertEqual(target.read_bytes(), b"")

    def test_create_directory_post_mkdir_failure_invalidates_descendant_baseline(self):
        target = self.root / "created-folder"
        target.mkdir()
        (target / "child.py").write_bytes(b"value = 1\n")
        affected = self.document("created-folder/child.py")
        os.rename(target, self.scope / "moved-folder")
        self.success(self.bridge.analyze())
        root = self.bootstrap()
        original_mkdir = Path.mkdir

        def mkdir_then_fail(path, *args, **kwargs):
            result = original_mkdir(path, *args, **kwargs)
            if path == target:
                raise OSError("private-create-mkdir-detail")
            return result

        with patch.object(Path, "mkdir", new=mkdir_then_fail):
            response = self.create(root, target.name, "directory")
        self.assertTrue(target.is_dir())
        self.assertEqual(list(target.iterdir()), [])
        self.assertNotIn("private-create-mkdir-detail", json.dumps(response))
        with self.subTest(contract="operation failure"):
            self.rejected(response, "FILE_OPERATION_FAILED")
        with self.subTest(contract="analysis invalidation"):
            self.assertEqual(self.success(self.bridge.get_session())["status"], "Outdated")
        with self.subTest(contract="affected source invalidation"):
            self.assertNotIn(affected["revision"], self.controller._source_editing._documents)
            self.rejected(self.save(affected), "SOURCE_CONFLICT")
        self.assertTrue(target.is_dir())
        self.assertEqual((self.scope / "moved-folder" / "child.py").read_bytes(), b"value = 1\n")

    def test_duplicate_create_never_overwrites(self):
        self.success(self.bridge.analyze())
        document = self.document()
        baselines = dict(self.controller._source_editing._documents)
        root = self.bootstrap()
        for kind in ("file", "directory"):
            with self.subTest(kind=kind):
                self.rejected(self.create(root, "app.py", kind), "FILE_CONFLICT")
                self.assertEqual(self.success(self.bridge.get_session())["status"], "Ready")
                self.assertEqual(dict(self.controller._source_editing._documents), baselines)
        self.assertEqual((self.root / "app.py").read_bytes(), b"value = 1\n")
        self.success(self.save(document))

    def test_create_pre_open_permission_failure_preserves_ready_and_baselines(self):
        target = self.root / "new.txt"
        target.write_bytes(b"")
        self.document(target.name)
        target.unlink()
        self.success(self.bridge.analyze())
        document = self.document()
        baselines = dict(self.controller._source_editing._documents)
        root = self.bootstrap()
        original_open = Path.open

        def deny_target_open(path, mode="r", *args, **kwargs):
            if path == target and mode == "xb":
                raise PermissionError("private-pre-open-detail")
            return original_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", new=deny_target_open):
            response = self.create(root, target.name)
        self.rejected(response, "FILE_CREATE_FAILED")
        self.assertNotIn("private-pre-open-detail", json.dumps(response))
        self.assertFalse(target.exists())
        self.assertEqual(self.success(self.bridge.get_session())["status"], "Ready")
        self.assertEqual(dict(self.controller._source_editing._documents), baselines)
        self.assertEqual((self.root / "app.py").read_bytes(), b"value = 1\n")
        self.success(self.save(document))
        self.assertEqual((self.root / "app.py").read_bytes(), b"value = 2\n")

    def test_stale_parent_revision_rejects_create(self):
        root = self.bootstrap()
        (self.root / "external.txt").write_bytes(b"external")
        self.rejected(self.create(root))
        self.assertFalse((self.root / "new.txt").exists())

    def test_invalid_names_rejected_for_create_and_rename(self):
        self.success(self.bridge.analyze())
        document = self.document()
        baselines = dict(self.controller._source_editing._documents)
        root = self.bootstrap()
        names = ("", ".", "..", "../escape", "a/b", "a\\b", "/absolute", "C:\\escape",
                 "//server/share", "a:b", "app.py:stream", "CON", "con.txt", "NUL", "AUX",
                 "PRN", "COM1.txt", "LPT9", "trailing.", "trailing ", "bad\x00name")
        before = set(self.root.iterdir())
        for name in names:
            with self.subTest(name=name):
                self.rejected(self.create(root, name), "INVALID_REQUEST")
                self.rejected(self.prepare(root, "app.py", new_name=name))
                self.assertEqual(set(self.root.iterdir()), before)
                self.assertEqual(self.success(self.bridge.get_session())["status"], "Ready")
                self.assertEqual(dict(self.controller._source_editing._documents), baselines)
        self.assertFalse((self.scope / "escape").exists())
        self.success(self.save(document))

    def test_invalid_directory_paths_rejected(self):
        root = self.bootstrap()
        for directory in ("../", self.project, "/absolute", "C:\\escape", "//server/share",
                          "app.py:stream", "bad\x00name"):
            with self.subTest(directory=directory):
                self.rejected(self.listing(directory, projectEpoch=root["projectEpoch"]))

    def test_no_missing_parent_scaffolding(self):
        root = self.bootstrap()
        self.rejected(self.create(root, parent="missing/nested"))
        self.assertFalse((self.root / "missing").exists())

    def test_mutations_require_epoch(self):
        root = self.bootstrap()
        request = self.create_request(root)
        del request["projectEpoch"]
        self.rejected(self.api("create_project_entry")(request))
        request = {"projectPath": self.project, "path": "app.py", "operation": "rename",
                   "newName": "renamed.py", "revision": self.entry(root, "app.py")["revision"]}
        self.rejected(self.api("prepare_project_entry_operation")(request))
        token = self.success(self.prepare(root, "app.py"))
        self.rejected(self.api("apply_project_entry_operation")(
            {"projectPath": self.project, "operationToken": token["operationToken"]}))

    def test_protected_entries_visible_but_readonly(self):
        self.api("list_project_directory")
        for name in (".git", ".venv", "venv", "node_modules"):
            (self.root / name).mkdir()
        root = self.bootstrap()
        for name in (".git", ".venv", "venv", "node_modules"):
            with self.subTest(name=name):
                entry = self.entry(root, name)
                self.assertTrue(entry["readOnlyReason"])
                for capability in ("canOpen", "canCreate", "canRename", "canDelete"):
                    self.assertFalse(entry[capability])
                self.rejected(self.prepare(root, name))
                self.rejected(self.prepare(root, name, "delete"))
                child = self.success(self.listing(name, projectEpoch=root["projectEpoch"]))
                self.rejected(self.create(child))

    def test_protected_safe_text_can_open_for_readonly_viewing(self):
        self.api("list_project_directory")
        directory = self.root / ".venv"
        directory.mkdir()
        path = directory / "demo.py"
        original = b"value = 1\n"
        path.write_bytes(original)
        root = self.bootstrap()
        listing = self.success(self.listing(".venv", projectEpoch=root["projectEpoch"]))
        entry = self.entry(listing, ".venv/demo.py")
        self.assertTrue(entry["canOpen"], entry)
        self.assertTrue(entry["readOnlyReason"])
        for capability in ("canCreate", "canRename", "canDelete"):
            self.assertFalse(entry[capability])
        document = self.document(".venv/demo.py")
        self.assertEqual(document["text"], original.decode())
        self.assertTrue(document["readOnlyReason"])
        self.rejected(self.save(document), "SOURCE_READ_ONLY")
        self.rejected(self.prepare(listing, ".venv/demo.py"))
        self.rejected(self.prepare(listing, ".venv/demo.py", "delete"))
        self.assertEqual(path.read_bytes(), original)

    def test_disk_readonly_safe_text_can_open_for_readonly_viewing(self):
        self.api("list_project_directory")
        path = self.root / "notes.txt"
        original = b"keep these notes\n"
        path.write_bytes(original)
        mode = path.stat().st_mode
        self.addCleanup(path.chmod, mode)
        path.chmod(stat.S_IREAD)
        root = self.bootstrap()
        entry = self.entry(root, "notes.txt")
        self.assertTrue(entry["canOpen"], entry)
        self.assertTrue(entry["readOnlyReason"])
        for capability in ("canCreate", "canRename", "canDelete"):
            self.assertFalse(entry[capability])
        document = self.document("notes.txt")
        self.assertEqual(document["text"], original.decode())
        self.assertTrue(document["readOnlyReason"])
        self.rejected(self.save(document), "SOURCE_READ_ONLY")
        self.rejected(self.prepare(root, "notes.txt"))
        self.rejected(self.prepare(root, "notes.txt", "delete"))
        self.assertEqual(path.read_bytes(), original)

    def test_unsafe_text_cannot_open_even_when_protected(self):
        self.api("list_project_directory")
        (self.root / ".venv").mkdir()
        original = b"binary\x00content\n"
        for relative in ("unsafe.txt", ".venv/unsafe.txt"):
            (self.root / relative).write_bytes(original)
        root = self.bootstrap()
        protected = self.success(self.listing(".venv", projectEpoch=root["projectEpoch"]))
        for listing, relative in ((root, "unsafe.txt"), (protected, ".venv/unsafe.txt")):
            with self.subTest(path=relative):
                self.assertFalse(self.entry(listing, relative)["canOpen"])
                response = self.api("open_source_document")(
                    self.project, {"filePath": relative, "startLine": 1, "endLine": 1,
                                   "startColumn": None, "endColumn": None})
                self.rejected(response)
                self.assertEqual((self.root / relative).read_bytes(), original)

    def test_ancestors_containing_protected_entries_cannot_rename_or_delete(self):
        self.api("list_project_directory")
        for name in (".git", ".venv", "venv", "node_modules"):
            protected = self.root / f"parent-{name}" / "nested" / name
            protected.mkdir(parents=True)
        root = self.bootstrap()
        for name in (".git", ".venv", "venv", "node_modules"):
            with self.subTest(name=name):
                path = f"parent-{name}"
                entry = self.entry(root, path)
                self.assertFalse(entry["canRename"])
                self.assertFalse(entry["canDelete"])
                self.rejected(self.prepare(root, path))
                self.rejected(self.prepare(root, path, "delete"))
                self.assertTrue((self.root / path / "nested" / name).is_dir())

    def test_root_cannot_rename_or_delete(self):
        root = self.bootstrap()
        for operation in ("rename", "delete"):
            request = {"projectPath": self.project, "projectEpoch": root["projectEpoch"],
                       "path": "", "revision": root["directoryRevision"], "operation": operation}
            if operation == "rename":
                request["newName"] = "elsewhere"
            self.rejected(self.api("prepare_project_entry_operation")(request))
        self.assertTrue(self.root.is_dir())

    def test_symlink_visible_without_traversal_or_mutation(self):
        self.api("list_project_directory")
        outside = self.scope / "outside"
        outside.mkdir()
        (outside / "sentinel.txt").write_bytes(b"keep")
        link = self.root / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("OS does not permit creating symlinks")
        root = self.bootstrap()
        entry = self.entry(root, "linked")
        self.assertEqual(entry["kind"], "link")
        self.assertTrue(entry["readOnlyReason"])
        for capability in ("canOpen", "canCreate", "canRename", "canDelete"):
            self.assertFalse(entry[capability])
        self.rejected(self.listing("linked", projectEpoch=root["projectEpoch"]))
        self.rejected(self.prepare(root, "linked"))
        self.rejected(self.prepare(root, "linked", "delete"))
        self.assertEqual((outside / "sentinel.txt").read_bytes(), b"keep")

    def test_hardlinks_are_readonly(self):
        self.api("list_project_directory")
        alias = self.scope / "alias.py"
        try:
            os.link(self.root / "app.py", alias)
        except (OSError, NotImplementedError):
            self.skipTest("OS does not permit creating hardlinks")
        root = self.bootstrap()
        entry = self.entry(root, "app.py")
        self.assertTrue(entry["readOnlyReason"])
        self.assertFalse(entry["canOpen"])
        self.assertFalse(entry["canRename"])
        self.assertFalse(entry["canDelete"])
        self.rejected(self.prepare(root, "app.py"))
        self.rejected(self.prepare(root, "app.py", "delete"))
        self.assertEqual(alias.read_bytes(), b"value = 1\n")

    def test_prepare_and_apply_same_parent_rename_schema(self):
        root = self.bootstrap()
        token = self.success(self.prepare(root, "app.py"))
        self.assertEqual(set(token), {"operationToken", "operation", "path", "newPath", "affectedPaths"})
        self.assertTrue(token["operationToken"])
        self.assertEqual(token["operation"], "rename")
        self.assertEqual(token["path"], "app.py")
        self.assertEqual(token["newPath"], "renamed.py")
        self.assertIn("app.py", token["affectedPaths"])
        self.assertTrue((self.root / "app.py").exists())
        result = self.success(self.apply(root, token))
        self.assertEqual(set(result), {"operation", "oldPath", "newPath", "affectedPaths"})
        self.assertEqual((result["operation"], result["oldPath"], result["newPath"]),
                         ("rename", "app.py", "renamed.py"))
        self.assertFalse((self.root / "app.py").exists())
        self.assertEqual((self.root / "renamed.py").read_bytes(), b"value = 1\n")
        self.assertEqual(self.success(self.bridge.get_session())["status"], "NotAnalyzed")

    def test_nested_rename_stays_within_same_parent(self):
        self.api("list_project_directory")
        folder = self.root / "folder"
        folder.mkdir()
        (folder / "child.txt").write_bytes(b"keep")
        root = self.bootstrap()
        listing = self.success(self.listing("folder", projectEpoch=root["projectEpoch"]))
        token = self.success(self.prepare(listing, "folder/child.txt", new_name="renamed.txt"))
        self.assertEqual(token["newPath"], "folder/renamed.txt")
        result = self.success(self.apply(root, token))
        self.assertEqual(result["newPath"], "folder/renamed.txt")
        self.assertFalse((folder / "child.txt").exists())
        self.assertEqual((folder / "renamed.txt").read_bytes(), b"keep")
        self.assertFalse((self.root / "renamed.txt").exists())

    def test_directory_rename_reports_descendant_affected_paths(self):
        self.api("list_project_directory")
        folder = self.root / "folder"
        (folder / "nested").mkdir(parents=True)
        (folder / "nested" / "child.py").write_bytes(b"value = 1\n")
        root = self.bootstrap()
        document = self.document("folder/nested/child.py")
        token = self.success(self.prepare(root, "folder", new_name="renamed"))
        affected = {"folder", "folder/nested", "folder/nested/child.py"}
        self.assertTrue(affected.issubset(token["affectedPaths"]), token)
        result = self.success(self.apply(root, token))
        self.assertTrue(affected.issubset(result["affectedPaths"]), result)
        self.rejected(self.save(document))
        self.assertEqual((self.root / "renamed/nested/child.py").read_bytes(), b"value = 1\n")
        self.assertFalse(folder.exists())

    def test_prepare_rejects_stale_entry_content_revision(self):
        root = self.bootstrap()
        path = self.root / "app.py"
        before = path.stat()
        path.write_bytes(b"value = 9\n")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.rejected(self.prepare(root, "app.py"))
        self.assertEqual(path.read_bytes(), b"value = 9\n")

    def test_rename_rejects_existing_destination(self):
        root = self.bootstrap()
        (self.root / "renamed.py").write_bytes(b"keep")
        self.rejected(self.prepare(root, "app.py"))
        self.assertEqual((self.root / "renamed.py").read_bytes(), b"keep")

    def test_apply_rename_detects_new_destination(self):
        root = self.bootstrap()
        token = self.success(self.prepare(root, "app.py"))
        (self.root / "renamed.py").write_bytes(b"keep")
        self.rejected(self.apply(root, token))
        self.assertEqual((self.root / "app.py").read_bytes(), b"value = 1\n")
        self.assertEqual((self.root / "renamed.py").read_bytes(), b"keep")

    def test_apply_detects_source_content_change_preserving_mtime(self):
        root = self.bootstrap()
        token = self.success(self.prepare(root, "app.py"))
        path = self.root / "app.py"
        before = path.stat()
        path.write_bytes(b"value = 9\n")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.rejected(self.apply(root, token))
        self.assertEqual(path.read_bytes(), b"value = 9\n")
        self.assertFalse((self.root / "renamed.py").exists())

    def test_apply_detects_subtree_content_change_preserving_all_mtimes(self):
        self.api("list_project_directory")
        folder = self.root / "folder"
        folder.mkdir()
        child = folder / "child.txt"
        child.write_bytes(b"original")
        root = self.bootstrap()
        adapter = self.recycle_adapter()
        for operation in ("rename", "delete"):
            with self.subTest(operation=operation):
                token = self.success(self.prepare(root, "folder", operation, "renamed"))
                before, folder_before = child.stat(), folder.stat()
                child.write_bytes(b"modified")
                os.utime(child, ns=(before.st_atime_ns, before.st_mtime_ns))
                os.utime(folder, ns=(folder_before.st_atime_ns, folder_before.st_mtime_ns))
                with patch.object(adapter, "recycle_path") as recycle:
                    self.rejected(self.apply(root, token))
                recycle.assert_not_called()
                self.assertEqual(child.read_bytes(), b"modified")
                self.assertFalse((self.root / "renamed").exists())
                child.write_bytes(b"original")
                os.utime(child, ns=(before.st_atime_ns, before.st_mtime_ns))
                root = self.bootstrap()

    def test_apply_detects_identical_bytes_source_replacement(self):
        root = self.bootstrap()
        token = self.success(self.prepare(root, "app.py"))
        replacement = self.scope / "replacement.py"
        replacement.write_bytes(b"value = 1\n")
        os.replace(replacement, self.root / "app.py")
        self.rejected(self.apply(root, token))
        self.assertTrue((self.root / "app.py").exists())

    def test_apply_detects_parent_directory_replacement(self):
        self.api("list_project_directory")
        folder = self.root / "folder"
        folder.mkdir()
        (folder / "child.txt").write_bytes(b"keep")
        root = self.bootstrap()
        listing = self.success(self.listing("folder", projectEpoch=root["projectEpoch"]))
        token = self.success(self.prepare(listing, "folder/child.txt", new_name="renamed.txt"))
        os.rename(folder, self.scope / "old-folder")
        folder.mkdir()
        (folder / "child.txt").write_bytes(b"keep")
        self.rejected(self.apply(root, token))
        self.assertEqual((folder / "child.txt").read_bytes(), b"keep")
        self.assertEqual((self.scope / "old-folder/child.txt").read_bytes(), b"keep")
        self.assertFalse((folder / "renamed.txt").exists())

    def test_protected_subtree_added_after_prepare_blocks_apply(self):
        self.api("list_project_directory")
        folder = self.root / "folder"
        folder.mkdir()
        (folder / "child.txt").write_bytes(b"keep")
        root = self.bootstrap()
        token = self.success(self.prepare(root, "folder", new_name="renamed"))
        before = folder.stat()
        (folder / ".git").mkdir()
        os.utime(folder, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.rejected(self.apply(root, token))
        self.assertTrue((folder / ".git").is_dir())
        self.assertFalse((self.root / "renamed").exists())

    def test_hardlinked_descendant_blocks_directory_mutation(self):
        self.api("list_project_directory")
        folder = self.root / "folder"
        folder.mkdir()
        (folder / "child.txt").write_bytes(b"keep")
        try:
            os.link(folder / "child.txt", self.scope / "alias.txt")
        except (OSError, NotImplementedError):
            self.skipTest("OS does not permit creating hardlinks")
        root = self.bootstrap()
        self.rejected(self.prepare(root, "folder"))
        self.rejected(self.prepare(root, "folder", "delete"))
        self.assertEqual((self.scope / "alias.txt").read_bytes(), b"keep")

    def test_operation_token_is_single_use(self):
        root = self.bootstrap()
        token = self.success(self.prepare(root, "app.py"))
        self.success(self.apply(root, token))
        self.rejected(self.apply(root, token))
        self.assertEqual((self.root / "renamed.py").read_bytes(), b"value = 1\n")

    def test_delete_uses_lower_level_recycle_adapter(self):
        root = self.bootstrap()
        adapter = self.recycle_adapter()
        token = self.success(self.prepare(root, "app.py", "delete"))
        self.assertIsNone(token["newPath"])
        trash = self.scope / "fake-recycle"
        trash.mkdir()

        def recycle(path):
            path = Path(path)
            self.assertEqual(path.resolve(), (self.root / "app.py").resolve())
            os.rename(path, trash / path.name)

        with patch.object(adapter, "recycle_path", side_effect=recycle) as recycle_call:
            result = self.success(self.apply(root, token))
        recycle_call.assert_called_once()
        self.assertEqual((result["operation"], result["oldPath"], result["newPath"]),
                         ("delete", "app.py", None))
        self.assertFalse((self.root / "app.py").exists())
        self.assertEqual((trash / "app.py").read_bytes(), b"value = 1\n")
        self.assertEqual(self.success(self.bridge.get_session())["status"], "NotAnalyzed")

    def test_recycle_failure_preserves_file_and_never_falls_back(self):
        root = self.bootstrap()
        adapter = self.recycle_adapter()
        for failure in (PermissionError("private-recycle-detail"),
                        NotImplementedError("private-recycle-detail"),
                        OSError("private-recycle-detail")):
            with self.subTest(failure=type(failure).__name__):
                token = self.success(self.prepare(root, "app.py", "delete"))
                with patch.object(adapter, "recycle_path", side_effect=failure), \
                        patch("os.unlink") as unlink, patch("shutil.rmtree") as rmtree:
                    response = self.apply(root, token)
                self.rejected(response)
                unlink.assert_not_called()
                rmtree.assert_not_called()
                self.assertNotIn("private-recycle-detail", json.dumps(response))
                self.assertEqual((self.root / "app.py").read_bytes(), b"value = 1\n")
                self.assertEqual(self.controller.status, AnalysisStatus.NOT_ANALYZED)

    def test_all_mutations_use_bridge_write_call_and_block_analyzing(self):
        root = self.bootstrap()
        token = self.success(self.prepare(root, "app.py"))
        self.controller.status = AnalysisStatus.ANALYZING
        for action in (lambda: self.create(root), lambda: self.prepare(root, "app.py"),
                       lambda: self.apply(root, token)):
            with self.subTest(action=action), \
                    patch.object(self.bridge, "_write_call", wraps=self.bridge._write_call) as write:
                self.rejected(action(), "NOT_READY")
                write.assert_called_once()
        self.assertEqual((self.root / "app.py").read_bytes(), b"value = 1\n")
        self.assertFalse((self.root / "new.txt").exists())

    def test_success_marks_outdated_preserving_analysis_layout_annotations_cache(self):
        self.api("create_project_entry")
        self.success(self.bridge.analyze())
        analysis = self.controller.analysis
        self.controller.move_node("test-note", (12.0, 23.0))
        layout = self.controller.view_state
        self.store.save_annotations(self.root, {"test-note": "keep"})
        cache = self.store.analysis_path(self.root).read_bytes()
        root = self.bootstrap()
        with patch("codeviz.ui.controller.analyze_project") as analyze:
            self.success(self.create(root))
        analyze.assert_not_called()
        self.assertIs(self.controller.analysis, analysis)
        self.assertEqual(self.controller.view_state, layout)
        self.assertEqual(self.store.load_annotations(self.root), {"test-note": "keep"})
        self.assertEqual(self.store.analysis_path(self.root).read_bytes(), cache)
        self.assertEqual(self.success(self.bridge.get_session())["status"], "Outdated")

    def test_mutation_preserves_epoch_and_unaffected_source_revision(self):
        root = self.bootstrap()
        document = self.document()
        self.success(self.create(root))
        self.assertEqual(self.bootstrap()["projectEpoch"], root["projectEpoch"])
        self.success(self.save(document))

    def test_rename_invalidates_old_source_revision(self):
        root = self.bootstrap()
        document = self.document()
        token = self.success(self.prepare(root, "app.py"))
        self.success(self.apply(root, token))
        self.rejected(self.save(document))
        self.assertFalse((self.root / "app.py").exists())
        self.assertEqual((self.root / "renamed.py").read_bytes(), b"value = 1\n")

    def test_rename_marks_outdated_preserving_existing_analysis_state(self):
        self.api("prepare_project_entry_operation")
        self.success(self.bridge.analyze())
        analysis = self.controller.analysis
        self.controller.move_node("test-note", (12.0, 23.0))
        layout = self.controller.view_state
        self.store.save_annotations(self.root, {"test-note": "keep"})
        cache = self.store.analysis_path(self.root).read_bytes()
        root = self.bootstrap()
        token = self.success(self.prepare(root, "app.py"))
        with patch("codeviz.ui.controller.analyze_project") as analyze:
            self.success(self.apply(root, token))
        analyze.assert_not_called()
        self.assertIs(self.controller.analysis, analysis)
        self.assertEqual(self.controller.view_state, layout)
        self.assertEqual(self.store.load_annotations(self.root), {"test-note": "keep"})
        self.assertEqual(self.store.analysis_path(self.root).read_bytes(), cache)
        self.assertEqual(self.success(self.bridge.get_session())["status"], "Outdated")

    def test_delete_marks_outdated_and_invalidates_only_affected_source(self):
        self.api("apply_project_entry_operation")
        adapter = self.recycle_adapter()
        (self.root / "other.py").write_bytes(b"value = 1\n")
        self.success(self.bridge.analyze())
        analysis = self.controller.analysis
        self.controller.move_node("test-note", (12.0, 23.0))
        layout = self.controller.view_state
        self.store.save_annotations(self.root, {"test-note": "keep"})
        cache = self.store.analysis_path(self.root).read_bytes()
        root = self.bootstrap()
        affected = self.document()
        unaffected = self.document("other.py")
        token = self.success(self.prepare(root, "app.py", "delete"))

        def recycle(path):
            os.rename(path, self.scope / "recycled.py")

        with patch.object(adapter, "recycle_path", side_effect=recycle), \
                patch("codeviz.ui.controller.analyze_project") as analyze:
            self.success(self.apply(root, token))
        analyze.assert_not_called()
        self.assertIs(self.controller.analysis, analysis)
        self.assertEqual(self.controller.view_state, layout)
        self.assertEqual(self.store.load_annotations(self.root), {"test-note": "keep"})
        self.assertEqual(self.store.analysis_path(self.root).read_bytes(), cache)
        self.assertEqual(self.success(self.bridge.get_session())["status"], "Outdated")
        self.assertEqual(self.bootstrap()["projectEpoch"], root["projectEpoch"])
        self.rejected(self.save(affected))
        self.success(self.save(unaffected))
        self.assertFalse((self.root / "app.py").exists())
        self.assertEqual((self.scope / "recycled.py").read_bytes(), b"value = 1\n")

    def test_subtree_entry_limit_rejects_without_truncating(self):
        self.api("prepare_project_entry_operation")
        folder = self.root / "folder"
        folder.mkdir()
        for index in range(5001):
            (folder / f"{index:04}.txt").touch()
        root = self.bootstrap()
        for operation in ("rename", "delete"):
            self.rejected(self.prepare(root, "folder", operation, "renamed"))
        self.assertEqual(len(list(folder.iterdir())), 5001)

    def test_subtree_depth_limit_rejects_without_mutation(self):
        self.api("prepare_project_entry_operation")
        folder = self.root / "folder"
        folder.mkdir()
        deepest = folder
        for _ in range(65):
            deepest = deepest / "d"
            deepest.mkdir()
        (deepest / "sentinel.txt").write_bytes(b"keep")
        root = self.bootstrap()
        for operation in ("rename", "delete"):
            self.rejected(self.prepare(root, "folder", operation, "renamed"))
        self.assertEqual((deepest / "sentinel.txt").read_bytes(), b"keep")

    def test_subtree_fingerprint_byte_limit_rejects_without_mutation(self):
        self.api("prepare_project_entry_operation")
        folder = self.root / "folder"
        folder.mkdir()
        path = folder / "large.bin"
        size = 64 * 1024 * 1024 + 1
        with path.open("wb") as output:
            output.truncate(size)
        root = self.bootstrap()
        for operation in ("rename", "delete"):
            self.rejected(self.prepare(root, "folder", operation, "renamed"))
        self.assertEqual(path.stat().st_size, size)
