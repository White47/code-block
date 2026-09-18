"""Local cache content, invalidation and corruption boundaries."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.analysis.project import analyze_project
from codeviz.core import ViewKind
from codeviz.persistence.fingerprint import fingerprint_project, outdated_reasons
from codeviz.persistence.store import CacheError, CacheStore
from codeviz.views import ViewState


class CacheStoreTests(unittest.TestCase):
    def test_save_load_and_source_change_do_not_write_into_project(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project, data = Path(project_dir), Path(data_dir)
            (project / "run.py").write_text(
                "def train(loss):\n    loss.backward()\n# PRIVATE_SOURCE_MARKER\n",
                encoding="utf-8")
            before = set(project.iterdir())
            store = CacheStore(data)
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            store.save_analysis(project, analysis, fingerprint)
            saved = store.load_analysis(project)
            self.assertIsNotNone(saved)
            self.assertEqual(saved.fingerprint, fingerprint)
            self.assertEqual(len(saved.analysis.symbol_index.entries),
                             len(analysis.symbol_index.entries))
            self.assertNotIn("PRIVATE_SOURCE_MARKER",
                             store.analysis_path(project).read_text(encoding="utf-8"))
            self.assertEqual(set(project.iterdir()), before)
            self.assertEqual(outdated_reasons(fingerprint, fingerprint_project(project)), ())
            (project / "run.py").write_text("def train(): return 2\n", encoding="utf-8")
            self.assertIn("source changed",
                          outdated_reasons(fingerprint, fingerprint_project(project)))

    def test_added_deleted_config_and_version_changes_are_detected(self):
        with TemporaryDirectory() as project_dir:
            project = Path(project_dir)
            (project / "run.py").write_text("value = 1\n", encoding="utf-8")
            original = fingerprint_project(project)
            (project / "new.py").write_text("other = 2\n", encoding="utf-8")
            self.assertIn("source changed", outdated_reasons(original, fingerprint_project(project)))
            with_new_file = fingerprint_project(project)
            (project / "new.py").unlink()
            self.assertIn("source changed", outdated_reasons(
                with_new_file, fingerprint_project(project)))
            self.assertIn("analysis config changed", outdated_reasons(
                original, fingerprint_project(project, ignore_paths=("ignored",))))
            self.assertIn("parser version changed", outdated_reasons(
                original, fingerprint_project(project, parser_version="future")))
            self.assertIn("semantic rules version changed", outdated_reasons(
                original, fingerprint_project(project, semantic_version="future")))

    def test_corrupt_cache_is_rejected_without_touching_other_files(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project, data = Path(project_dir), Path(data_dir)
            (project / "run.py").write_text("value = 1\n", encoding="utf-8")
            store = CacheStore(data)
            store.save_analysis(project, analyze_project(project), fingerprint_project(project))
            unrelated = store.analysis_path(project).parent / "annotations.json"
            unrelated.write_text('{"note":"keep me"}', encoding="utf-8")
            store.analysis_path(project).write_text("{broken", encoding="utf-8")
            with self.assertRaises(CacheError):
                store.load_analysis(project)
            self.assertEqual(unrelated.read_text(encoding="utf-8"), '{"note":"keep me"}')
            store.analysis_path(project).write_text("null", encoding="utf-8")
            with self.assertRaises(CacheError):
                store.load_analysis(project)

    def test_atomic_write_failure_preserves_previous_cache_and_cleans_temp_file(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "run.py").write_text("value = 1\n", encoding="utf-8")
            store = CacheStore(Path(data_dir))
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            store.save_analysis(project, analysis, fingerprint)
            target = store.analysis_path(project)
            previous = target.read_bytes()
            with patch("codeviz.persistence.store.os.fsync", side_effect=OSError("disk error")):
                with self.assertRaises(CacheError):
                    store.save_analysis(project, analysis, fingerprint)
            self.assertEqual(target.read_bytes(), previous)
            self.assertEqual(list(target.parent.glob(".analysis-*.tmp")), [])
            with patch("codeviz.persistence.store.os.replace", side_effect=OSError("disk error")):
                with self.assertRaises(CacheError):
                    store.save_analysis(project, analysis, fingerprint)
            self.assertEqual(target.read_bytes(), previous)
            self.assertEqual(list(target.parent.glob(".analysis-*.tmp")), [])

    def test_cache_directory_inside_project_is_rejected(self):
        with TemporaryDirectory() as project_dir:
            project = Path(project_dir)
            (project / "run.py").write_text("value = 1\n", encoding="utf-8")
            store = CacheStore(project / ".codeviz")
            with self.assertRaises(CacheError):
                store.save_analysis(project, analyze_project(project),
                                    fingerprint_project(project))
            self.assertFalse((project / ".codeviz").exists())

    def test_view_layout_round_trip_is_independent_of_analysis(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            store = CacheStore(Path(data_dir))
            state = ViewState(active_view=ViewKind.ARCHITECTURE,
                              selected_id="model-a",
                              open_tabs=("graph:Architecture", "source:0:model.py"),
                              graph_roots={"Architecture": "model-a"})
            store.save_view_state(project, state)
            self.assertEqual(store.load_view_state(project), state)
            self.assertFalse(store.analysis_path(project).exists())
