"""Controller cache lifecycle keeps old facts visible without claiming freshness."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.core import ViewKind
from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AnalysisStatus, AppController


class CachedControllerTests(unittest.TestCase):
    def test_reopen_uses_matching_cache_then_marks_changed_source_outdated(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            source = project / "run.py"
            source.write_text("def train(): return 1\n", encoding="utf-8")
            first = AppController(CacheStore(Path(data_dir)))
            first.open_project(project)
            first.analyze()
            cached = AppController(CacheStore(Path(data_dir)))
            cached.open_project(project)
            self.assertEqual(cached.status, AnalysisStatus.READY)
            self.assertIsNotNone(cached.analysis)
            self.assertEqual(cached.view(ViewKind.ARCHITECTURE),
                             first.view(ViewKind.ARCHITECTURE))
            source.write_text("def train(): return 2\n", encoding="utf-8")
            stale = AppController(CacheStore(Path(data_dir)))
            stale.open_project(project)
            self.assertEqual(stale.status, AnalysisStatus.OUTDATED)
            self.assertIn("source changed", stale.outdated_reasons)
            self.assertIsNotNone(stale.analysis)
            stale.view(ViewKind.ARCHITECTURE)
            stale.analyze()
            self.assertEqual(stale.status, AnalysisStatus.READY)
            self.assertEqual(stale.outdated_reasons, ())

    def test_failed_reanalysis_keeps_old_graph_and_cache(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "run.py").write_text("value = 1\n", encoding="utf-8")
            store = CacheStore(Path(data_dir))
            controller = AppController(store)
            controller.open_project(project)
            controller.analyze()
            old_analysis = controller.analysis
            old_cache = store.analysis_path(project).read_bytes()
            with patch("codeviz.ui.controller.analyze_project", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    controller.analyze()
            self.assertEqual(controller.status, AnalysisStatus.FAILED)
            self.assertIs(controller.analysis, old_analysis)
            self.assertEqual(store.analysis_path(project).read_bytes(), old_cache)
            controller.view(ViewKind.ARCHITECTURE)

    def test_corrupt_cache_requires_analysis_without_erasing_user_state(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "run.py").write_text("value = 1\n", encoding="utf-8")
            store = CacheStore(Path(data_dir))
            first = AppController(store)
            first.open_project(project)
            first.analyze()
            first.move_node("node-a", (12.0, 18.0))
            store.save_annotations(project, {"node-a": "Important"})
            store.analysis_path(project).write_text("{bad", encoding="utf-8")
            reopened = AppController(store)
            reopened.open_project(project)
            self.assertEqual(reopened.status, AnalysisStatus.NOT_ANALYZED)
            self.assertIsNone(reopened.analysis)
            self.assertIsNotNone(reopened.cache_issue)
            self.assertEqual(reopened.view_state.positions["node-a"], (12.0, 18.0))
            self.assertEqual(store.load_annotations(project), {"node-a": "Important"})

    def test_ignore_configuration_invalidates_cache_and_annotations_survive_reanalysis(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "run.py").write_text("value = 1\n", encoding="utf-8")
            store = CacheStore(Path(data_dir))
            first = AppController(store)
            first.open_project(project)
            first.analyze()
            store.save_annotations(project, {"symbol": "Retain after rebuilding"})
            changed = AppController(store, ignore_paths=("run.py",))
            changed.open_project(project)
            self.assertEqual(changed.status, AnalysisStatus.OUTDATED)
            self.assertIn("analysis config changed", changed.outdated_reasons)
            changed.analyze()
            self.assertEqual(changed.status, AnalysisStatus.READY)
            self.assertEqual(store.load_annotations(project),
                             {"symbol": "Retain after rebuilding"})
            self.assertEqual(store.recent_projects(), (project.resolve(),))
