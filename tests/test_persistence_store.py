"""Local cache content, invalidation and corruption boundaries."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.analysis.project import analyze_project
from codeviz.core import ViewKind
from codeviz.persistence.fingerprint import fingerprint_project, outdated_reasons
from codeviz.persistence.store import CacheError, CacheStore
from codeviz.views import DetailIslandState, ViewportState, ViewState


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
            store.save_view_state(project, ViewState(
                selected_id="run.train", positions={"run.train": (10.0, 20.0)}))
            store.save_annotations(project, {"run.train": "reviewed"})
            cache_files = tuple(store.analysis_path(project).parent.glob("*.json"))
            self.assertEqual(len(cache_files), 3)
            self.assertTrue(all("PRIVATE_SOURCE_MARKER" not in path.read_text(
                encoding="utf-8") for path in cache_files))
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
                              graph_roots={"Architecture": "model-a"},
                              positions_by_view={
                                  "Architecture": {"shared": (10.0, 20.0)},
                                  "Execution": {"shared": (30.0, 40.0)},
                              },
                              viewport_by_view={
                                  "Architecture": ViewportState(1.4, (25.0, -10.0)),
                              },
                              detail_islands=(DetailIslandState(
                                  "Architecture:model-a", ViewKind.ARCHITECTURE,
                                  "model-a", (620.0, 180.0)),))
            store.save_view_state(project, state)
            self.assertEqual(store.load_view_state(project), state)
            self.assertFalse(store.analysis_path(project).exists())

    def test_legacy_view_state_defaults_new_canvas_fields(self):
        legacy = {"expanded_ids": [], "positions": {}, "zoom": 1.2,
                  "pan": [4.0, -2.0]}
        restored = ViewState.from_dict(legacy)
        self.assertEqual(restored.viewport_by_view, {})
        self.assertEqual(restored.detail_islands, ())
        self.assertEqual(restored.positions_by_view, {})
        self.assertEqual(restored.zoom, 1.2)
        self.assertEqual(restored.pan, (4.0, -2.0))

    def test_legacy_flat_positions_are_tolerated_by_every_view(self):
        restored = ViewState.from_dict({
            "expanded_ids": [], "positions": {"shared": [12.0, 18.0]}})

        self.assertEqual(restored.positions_for(ViewKind.SYMBOL)["shared"],
                         (12.0, 18.0))
        self.assertEqual(restored.positions_for(ViewKind.DATA_FLOW)["shared"],
                         (12.0, 18.0))

    def test_viewport_serialization_does_not_depend_on_insertion_order(self):
        architecture = ViewportState(1.4, (25.0, -10.0))
        execution = ViewportState(0.8, (-8.0, 12.0))
        first = ViewState(viewport_by_view={
            ViewKind.ARCHITECTURE.value: architecture,
            ViewKind.EXECUTION.value: execution,
        })
        second = ViewState(viewport_by_view={
            ViewKind.EXECUTION.value: execution,
            ViewKind.ARCHITECTURE.value: architecture,
        })

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(
            json.dumps(first.to_dict(), ensure_ascii=False, separators=(",", ":")),
            json.dumps(second.to_dict(), ensure_ascii=False, separators=(",", ":")),
        )

    def test_canvas_state_rejects_invalid_numeric_values(self):
        for zoom in (0.24, 2.51, float("nan"), float("inf"), True):
            with self.subTest(zoom=zoom), self.assertRaises(ValueError):
                ViewportState(zoom=zoom)  # type: ignore[arg-type]
        for pan in ((float("nan"), 0.0), (True, 0.0), (0.0, float("inf"))):
            with self.subTest(pan=pan), self.assertRaises(ValueError):
                ViewportState(pan=pan)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            DetailIslandState("Architecture:model", ViewKind.ARCHITECTURE,
                              "model", (float("nan"), 0.0))

    def test_canvas_state_loader_strictly_rejects_malformed_records(self):
        base: dict[str, object] = {"expanded_ids": [], "positions": {}}
        invalid_payloads = (
            {**base, "viewport_by_view": {"Unknown": {"zoom": 1.0,
                                                         "pan": [0.0, 0.0]}}},
            {**base, "viewport_by_view": {"Architecture": {
                "zoom": 1.0, "pan": [0.0, 0.0], "extra": True}}},
            {**base, "detail_islands": [{
                "id": "x", "view_kind": "Unknown", "source_node_id": "x",
                "position": None}]},
            {**base, "detail_islands": [{
                "id": "x", "view_kind": "Architecture", "source_node_id": "x",
                "position": None}] * 2},
            {**base, "detail_islands": [{
                "id": f"Architecture:{index}", "view_kind": "Architecture",
                "source_node_id": str(index), "position": None}
                for index in range(4)]},
            {**base, "detail_islands": [{
                "id": "x" * 4001, "view_kind": "Architecture",
                "source_node_id": "x", "position": None}]},
            {**base, "detail_islands": [{
                "id": "x", "view_kind": "Architecture", "source_node_id": "x",
                "position": [0.0, float("inf")]}]},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                ViewState.from_dict(payload)
