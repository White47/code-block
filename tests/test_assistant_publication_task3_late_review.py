"""Late Task 3 regressions for bridge publication conflict handling."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.persistence.store import CacheStore
from codeviz.ui.assistant_context import PublicationError
from codeviz.ui.controller import AnalysisStatus, AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.web_bridge import DesktopBridge


class PublicationTask3LateReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory(prefix="codeviz-task3-late-review-")
        self.addCleanup(temporary.cleanup)
        self.scope = Path(temporary.name).resolve()
        self.root = self.scope / "project"
        self.root.mkdir()
        (self.root / "main.py").write_text("value = 1\n", encoding="utf-8")
        self.store = CacheStore(self.scope / "cache")
        self.controller = AppController(self.store)
        self.bridge = DesktopBridge(
            self.controller, self.store, PreferencesStore(self.scope / "preferences"))

    def success(self, response):
        self.assertTrue(response["ok"], response)
        json.dumps(response)
        return response["data"]

    def test_same_project_capture_conflict_preserves_active_explorer(self):
        self.success(self.bridge.open_project(str(self.root)))
        self.success(self.bridge.analyze())
        before = self.controller.capture_assistant_context()
        self.assertTrue(before.eligible)
        listing = self.success(self.bridge.list_project_directory({
            "projectPath": str(self.root), "directory": "",
            "projectEpoch": before.project_epoch,
        }))

        # Simulate the nonblocking publication-lock rejection at the very first
        # capture in AppController.open_project, before it can invalidate.
        with patch.object(self.controller, "capture_assistant_context",
                          side_effect=PublicationError("AI_ANALYSIS_BUSY")) as capture:
            rejected = self.bridge.open_project(str(self.root))

        self.assertGreaterEqual(capture.call_count, 1)
        self.assertFalse(rejected["ok"], rejected)
        json.dumps(rejected)
        self.assertIs(self.controller.capture_assistant_context(), before)
        self.assertEqual(self.controller.project_root, self.root)
        self.assertEqual(self.controller.status, AnalysisStatus.READY)
        self.assertEqual(self.controller._source_editing._root, self.root)
        self.assertEqual(self.bridge._project_explorer._root, self.root)
        self.assertEqual(self.bridge._project_explorer._epoch, before.project_epoch)
        after = self.success(self.bridge.list_project_directory({
            "projectPath": str(self.root), "directory": "",
            "projectEpoch": before.project_epoch,
        }))
        self.assertEqual(after["projectEpoch"], listing["projectEpoch"])
        self.assertIn("main.py", {entry["path"] for entry in after["entries"]})

    def test_completion_capture_conflict_does_not_leave_ready_writes_blocked(self):
        self.success(self.bridge.open_project(str(self.root)))
        captures = self.controller.capture_assistant_context
        conflicts = []

        def conflict_only_after_completion():
            if self.controller.status is AnalysisStatus.READY:
                conflicts.append(self.controller.status)
                raise PublicationError("AI_ANALYSIS_BUSY")
            return captures()

        def complete_analysis():
            self.controller.analyze(before_analysis=self.bridge._publish_session)
            return {"completed": True}

        # The controller completes a real analysis inside _call. Its Analyzing
        # display was already published; only _call's final refresh conflicts.
        with patch.object(self.controller, "capture_assistant_context",
                          side_effect=conflict_only_after_completion):
            result = self.bridge._call(
                complete_analysis, invalid_message="The analysis request is invalid.")

        self.assertEqual(self.success(result), {"completed": True})
        self.assertEqual(conflicts, [AnalysisStatus.READY])
        self.assertEqual(self.controller.status, AnalysisStatus.READY)
        self.assertTrue(self.controller.capture_assistant_context().eligible)

        # Do not call get_session first: it would repair the cached display and
        # hide the stale Analyzing guard in _write_call.
        settings = self.success(self.bridge.set_analysis_settings({"ignorePaths": []}))
        self.assertEqual(settings["ignorePaths"], [])
        displayed = self.success(self.bridge.get_session())
        self.assertEqual(displayed["status"], AnalysisStatus.READY.value)


if __name__ == "__main__":
    unittest.main()
