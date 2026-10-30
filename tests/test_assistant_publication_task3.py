"""C3 Task 3: project, policy, and displayed-session identity contracts."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest.mock import patch

from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AnalysisStatus, AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.web_bridge import DesktopBridge


class PublicationTask3Tests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory(prefix="codeviz-publication-task3-")
        self.addCleanup(temporary.cleanup)
        self.scope = Path(temporary.name)
        self.root = self.scope / "project"
        self.root.mkdir()
        (self.root / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")
        (self.root / "skip.py").write_text("skip = True\n", encoding="utf-8")
        self.store = CacheStore(self.scope / "cache")
        self.controller = AppController(self.store)
        self.bridge = DesktopBridge(
            self.controller, self.store, PreferencesStore(self.scope / "preferences"))

    def success(self, response):
        self.assertTrue(response["ok"], response)
        json.dumps(response)
        return response["data"]

    def context(self):
        return self.controller.capture_assistant_context()

    def session(self):
        return self.success(self.bridge.get_session())

    def assert_pair(self, session, context):
        self.assertIn("projectEpoch", session)
        self.assertIn("analysisGeneration", session)
        self.assertEqual(
            (session["projectEpoch"], session["analysisGeneration"]),
            (context.project_epoch, context.analysis_generation))

    def listing(self):
        return self.success(self.bridge.list_project_directory(
            {"projectPath": str(self.root), "directory": ""}))

    def document(self):
        return self.success(self.bridge.open_source_document(str(self.root), {
            "filePath": "main.py", "startLine": 1, "endLine": 1,
            "startColumn": None, "endColumn": None,
        }))

    def prepare(self, listing):
        entry = next(item for item in listing["entries"] if item["path"] == "main.py")
        return self.success(self.bridge.prepare_project_entry_operation({
            "projectPath": str(self.root), "projectEpoch": listing["projectEpoch"],
            "path": "main.py", "revision": entry["revision"],
            "operation": "rename", "newName": "renamed.py",
        }))

    def test_rejected_open_preserves_identity_and_same_root_reopen_rotates_all_handles(self):
        self.success(self.bridge.open_project(str(self.root)))
        original = self.context()
        for rejected in (self.scope / "missing", self.root / "main.py"):
            with self.subTest(path=str(rejected)):
                self.assertFalse(self.bridge.open_project(str(rejected))["ok"])
                self.assertIs(self.context(), original)

        self.success(self.bridge.analyze())
        before = self.context()
        old_listing = self.listing()
        old_document = self.document()
        old_token = self.prepare(old_listing)

        def switch_during_analysis():
            active = self.context()
            with self.assertRaises(RuntimeError):
                self.controller.open_project(self.scope)
            self.assertIs(self.context(), active)

        self.controller.analyze(before_analysis=switch_during_analysis)
        before = self.context()
        self.success(self.bridge.open_project(str(self.root)))
        reopened = self.context()
        self.assertNotEqual(reopened.project_epoch, before.project_epoch)
        self.assertEqual(reopened.analysis_generation, before.analysis_generation + 1)
        self.assert_pair(self.session(), reopened)
        self.assertEqual(self.listing()["projectEpoch"], reopened.project_epoch)
        self.assertTrue(self.document()["revision"].startswith(reopened.project_epoch + "."))
        self.assertNotEqual(old_listing["projectEpoch"], reopened.project_epoch)
        self.assertNotEqual(old_document["revision"], self.document()["revision"])
        self.assertFalse(self.bridge.save_source_document({
            "projectPath": str(self.root), "filePath": "main.py",
            "revision": old_document["revision"], "text": old_document["text"],
        })["ok"])
        self.assertFalse(self.bridge.apply_project_entry_operation({
            "projectPath": str(self.root), "projectEpoch": old_listing["projectEpoch"],
            "operationToken": old_token["operationToken"],
        })["ok"])

    def test_live_and_cached_acceptance_share_controller_explorer_and_session_identity(self):
        self.assert_pair(self.session(), self.context())
        self.success(self.bridge.open_project(str(self.root)))
        opened = self.context()
        self.assertEqual(opened.analysis_generation, 1)
        self.assert_pair(self.session(), opened)
        self.assertEqual(self.listing()["projectEpoch"], opened.project_epoch)
        self.assertTrue(self.document()["revision"].startswith(opened.project_epoch + "."))

        self.success(self.bridge.analyze())
        live = self.context()
        self.assertTrue(live.eligible)
        self.assertEqual(live.analysis_generation, 2)
        self.assertEqual(live.project_epoch, opened.project_epoch)
        self.assertEqual(live.snapshot.binding.project_epoch, live.project_epoch)
        self.assertEqual(live.snapshot.binding.analysis_generation, 2)
        self.assert_pair(self.session(), live)
        self.assertIsNotNone(self.store.load_analysis(self.root.resolve()))

        self.success(self.bridge.open_project(str(self.root)))
        cached = self.context()
        self.assertTrue(cached.eligible)
        self.assertEqual(cached.analysis_generation, 3)
        self.assertNotEqual(cached.project_epoch, live.project_epoch)
        self.assertEqual(cached.snapshot.binding.project_epoch, cached.project_epoch)
        self.assertEqual(cached.snapshot.binding.analysis_generation, 3)
        self.assert_pair(self.session(), cached)
        self.assertEqual(self.listing()["projectEpoch"], cached.project_epoch)
        self.assertTrue(self.document()["revision"].startswith(cached.project_epoch + "."))
        revision = cached.publication_revision
        self.success(self.bridge.get_overview())
        self.assertEqual(self.session()["analysisGeneration"], 3)
        self.assertEqual(self.context().publication_revision, revision)

    def test_changed_ignore_invalidates_before_persistence_and_failure_stays_ineligible(self):
        self.success(self.bridge.open_project(str(self.root)))
        self.success(self.bridge.analyze())
        before = self.context()
        original_mkdir = Path.mkdir
        at_mkdir = []

        def observe_mkdir(path, *args, **kwargs):
            if path == self.store.data_dir:
                at_mkdir.append(self.context())
            return original_mkdir(path, *args, **kwargs)

        with patch.object(Path, "mkdir", observe_mkdir):
            self.success(self.bridge.set_analysis_settings({"ignorePaths": ["skip.py"]}))
        self.assertGreaterEqual(len(at_mkdir), 1)
        pending = at_mkdir[0]
        self.assertFalse(pending.eligible)
        self.assertIsNone(pending.snapshot)
        self.assertEqual(pending.project_epoch, before.project_epoch)
        self.assertEqual(pending.analysis_generation, before.analysis_generation + 1)
        changed = self.context()
        self.assertEqual(changed.analysis_generation, before.analysis_generation + 1)
        self.assertEqual(changed.project_epoch, before.project_epoch)
        self.assertEqual(changed.ignore_paths, ("skip.py",))
        self.assert_pair(self.session(), changed)

        self.success(self.bridge.set_analysis_settings({"ignorePaths": ["skip.py"]}))
        self.assertIs(self.context(), changed)
        self.assertFalse(self.bridge.set_analysis_settings(
            {"ignorePaths": ["../outside"]})["ok"])
        self.assertIs(self.context(), changed)
        self.success(self.bridge.analyze())
        ready = self.context()
        self.assertTrue(ready.eligible)
        analysis = self.controller.analysis
        at_replace = []

        def fail_replace(*_args):
            at_replace.append(self.context())
            raise OSError("settings write failed")

        with patch("codeviz.ui.web_bridge.os.replace", side_effect=fail_replace), \
                self.assertLogs("codeviz.ui.web_bridge", level="ERROR"):
            response = self.bridge.set_analysis_settings({"ignorePaths": ["main.py"]})
        self.assertFalse(response["ok"])
        self.assertEqual(len(at_replace), 1)
        self.assertFalse(at_replace[0].eligible)
        self.assertEqual(at_replace[0].analysis_generation, ready.analysis_generation + 1)
        failed = self.context()
        self.assertFalse(failed.eligible)
        self.assertIsNone(failed.snapshot)
        self.assertEqual(failed.project_epoch, ready.project_epoch)
        self.assertEqual(failed.analysis_generation, ready.analysis_generation + 1)
        self.assertEqual(failed.ignore_paths, ready.ignore_paths)
        self.assertEqual(self.controller.ignore_paths, ready.ignore_paths)
        self.assertIs(self.controller.analysis, analysis)
        self.assertEqual(self.success(self.bridge.get_analysis_settings())["ignorePaths"],
                         ["skip.py"])

    def test_busy_session_uses_one_stored_pair_during_open_and_analyze(self):
        self.success(self.bridge.open_project(str(self.root)))
        self.success(self.bridge.analyze())
        old = self.context()
        original_load = self.store.load_analysis
        entered, release = Event(), Event()
        responses = []

        def paused_load(root):
            entered.set()
            if not release.wait(5):
                raise AssertionError("open barrier was not released")
            return original_load(root)

        with patch.object(self.store, "load_analysis", paused_load):
            worker = Thread(target=lambda: responses.append(
                self.bridge.open_project(str(self.root))), daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                pending = self.context()
                self.assertFalse(pending.eligible)
                self.assertEqual(pending.analysis_generation, old.analysis_generation + 1)
                self.assertNotEqual(pending.project_epoch, old.project_epoch)
                with patch.object(self.controller, "capture_assistant_context",
                                  side_effect=AssertionError("busy fallback captured publication")):
                    display = self.busy_session()
                self.assertEqual(display["status"], old.status.value)
                self.assert_pair(display, old)
            finally:
                release.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.success(responses[0])
        reopened = self.context()
        self.assert_pair(self.session(), reopened)

        entered, release = Event(), Event()
        original_publish = self.bridge._publish_session
        responses.clear()

        def paused_publish(*args, **kwargs):
            original_publish(*args, **kwargs)
            if self.controller.status is AnalysisStatus.ANALYZING:
                entered.set()
                if not release.wait(5):
                    raise AssertionError("analysis barrier was not released")

        with patch.object(self.bridge, "_publish_session", paused_publish):
            worker = Thread(target=lambda: responses.append(self.bridge.analyze()), daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                analyzing = self.context()
                self.assertFalse(analyzing.eligible)
                self.assertEqual(analyzing.project_epoch, reopened.project_epoch)
                self.assertEqual(analyzing.analysis_generation,
                                 reopened.analysis_generation + 1)
                with patch.object(self.controller, "capture_assistant_context",
                                  side_effect=AssertionError("busy fallback captured publication")):
                    display = self.busy_session()
                self.assertEqual(display["status"], "Analyzing")
                self.assert_pair(display, analyzing)
            finally:
                release.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.success(responses[0])
        self.assert_pair(self.session(), self.context())

    def busy_session(self):
        finished = Event()
        responses = []
        worker = Thread(target=lambda: (responses.append(self.bridge.get_session()),
                                        finished.set()), daemon=True)
        worker.start()
        self.assertTrue(finished.wait(2), "get_session waited for an active operation")
        worker.join(2)
        return self.success(responses[0])


if __name__ == "__main__":
    unittest.main()
