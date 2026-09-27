"""Controller cache lifecycle keeps old facts visible without claiming freshness."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.core import ViewKind
from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AnalysisStatus, AppController


class CachedControllerTests(unittest.TestCase):
    def test_malformed_canvas_state_does_not_discard_analysis_or_annotations(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "run.py").write_text(
                "def train(loss):\n    loss.backward()\n", encoding="utf-8")
            store = CacheStore(Path(data_dir))
            first = AppController(store)
            first.open_project(project)
            expected = first.analyze()
            expected_symbol_ids = tuple(expected.symbol_index.entries)
            expected_code_ids = tuple(expected.flow.graph.nodes)
            assert expected.semantic is not None
            expected_semantic_ids = tuple(
                item.id for item in expected.semantic.graph.components)
            store.save_annotations(project, {"review": "keep"})
            state_path = store.analysis_path(project).parent / "view-state.json"
            base = {"expanded_ids": [], "positions": {}}
            invalid_states = (
                {**base, "detail_islands": [{
                    "id": f"Architecture:{index}",
                    "view_kind": "Architecture", "source_node_id": str(index),
                    "position": None,
                } for index in range(4)]},
                {**base, "viewport_by_view": {
                    "Architecture": {"zoom": float("inf"), "pan": [0.0, 0.0]}}},
                {**base, "detail_islands": [{
                    "id": "x" * 10_000, "view_kind": "Architecture",
                    "source_node_id": "missing", "position": None,
                }]},
            )

            for invalid in invalid_states:
                with self.subTest(case=tuple(invalid)):
                    state_path.write_text(json.dumps({
                        "schema_version": 1,
                        "project_root": str(project.resolve()),
                        "data": invalid,
                    }), encoding="utf-8")
                    reopened = AppController(store)
                    reopened.open_project(project)

                    self.assertIn("View state:", reopened.cache_issue or "")
                    self.assertEqual(reopened.status, AnalysisStatus.READY)
                    restored = reopened.analysis
                    assert restored is not None
                    self.assertEqual(
                        tuple(restored.symbol_index.entries),
                        expected_symbol_ids)
                    self.assertEqual(tuple(restored.flow.graph.nodes), expected_code_ids)
                    assert restored.semantic is not None
                    self.assertEqual(
                        tuple(item.id for item in restored.semantic.graph.components),
                        expected_semantic_ids)
                    self.assertEqual(store.load_annotations(project),
                                     {"review": "keep"})

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
