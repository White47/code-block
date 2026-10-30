"""Controller cache lifecycle keeps old facts visible without claiming freshness."""

import ast
import json
import unittest
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz import source_handles
from codeviz.analysis.project import analyze_project
from codeviz.core import ViewKind
from codeviz.persistence.fingerprint import fingerprint_project
from codeviz.persistence.store import CacheStore
from codeviz.ui.controller import AnalysisStatus, AppController


class CachedControllerTests(unittest.TestCase):
    def test_transient_source_change_with_equal_fingerprints_rejects_cache_and_keeps_result(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir).resolve()
            source = project / "run.py"
            original = b"def train(): return 1\n"
            transient = b"def train(): return 999\n# PRIVATE_TRANSIENT_MARKER\n"
            source.write_bytes(original)
            store = CacheStore(Path(data_dir))
            controller = AppController(store)
            controller.open_project(project)
            initial = controller.analyze()
            node_id = next(iter(initial.symbol_index.entries))
            controller.move_node(node_id, (12., 18.), ViewKind.SYMBOL)
            state = controller.view_state
            store.save_annotations(project, {node_id: "retain review"})
            path = store.analysis_path(project)
            before_bytes = {item: item.read_bytes() for item in path.parent.glob("*.json")}
            before_fingerprint = fingerprint_project(project)
            actual_results = []

            def analyze_transient(root, ignore_paths):
                source.write_bytes(transient)
                try:
                    result = analyze_project(root, ignore_paths)
                    actual_results.append(result)
                    return result
                finally:
                    source.write_bytes(original)

            with patch("codeviz.ui.controller.analyze_project", side_effect=analyze_transient), \
                    patch("codeviz.ui.controller.fingerprint_project", wraps=fingerprint_project) as fingerprints:
                result = controller.analyze()
            self.assertEqual(fingerprints.call_count, 2)
            self.assertEqual(before_fingerprint, fingerprint_project(project))
            self.assertEqual(source.read_bytes(), original)
            self.assertIs(result, actual_results[0])
            self.assertIs(controller.analysis, result)
            self.assertTrue(result.files[0].was_parsed)
            self.assertEqual(result.files[0].content_hash, sha256(transient).hexdigest())
            self.assertNotEqual(result.files[0].content_hash, before_fingerprint.files[0][1])
            self.assertIsNotNone(result.files[0].tree)
            self.assertIn(999, [node.value for node in ast.walk(result.files[0].tree)
                                if isinstance(node, ast.Constant)])
            self.assertEqual(controller.status, AnalysisStatus.PARTIAL)
            self.assertEqual(controller.cache_issue,
                             "Analysis cache: analysis cache source hash mismatch")
            self.assertEqual(controller.view_state, state)
            self.assertEqual(store.load_annotations(project), {node_id: "retain review"})
            self.assertEqual({item: item.read_bytes() for item in before_bytes}, before_bytes)
            controller.view(ViewKind.ARCHITECTURE, _remember_depth=False)
            self.assertIs(controller.analysis, result)

    def test_current_cache_source_mismatch_reports_issue_preserving_whole_user_state(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "run.py").write_bytes(b"def train(): return 1\n")
            store = CacheStore(Path(data_dir))
            first = AppController(store)
            first.open_project(project)
            result = first.analyze()
            node_id = next(iter(result.symbol_index.entries))
            first.move_node(node_id, (12., 18.), ViewKind.SYMBOL)
            first.save_viewport(ViewKind.SYMBOL, 1.2, (3., 4.))
            state = first.view_state
            store.save_annotations(project, {node_id: "retain review"})
            path = store.analysis_path(project)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["fingerprint"]["files"][0][1] = "0" * 64
            path.write_text(json.dumps(payload), encoding="utf-8")
            before = {item: item.read_bytes() for item in path.parent.glob("*.json")}
            reopened = AppController(store)
            reopened.open_project(project)
            self.assertIsNone(reopened.analysis)
            self.assertEqual(reopened.status, AnalysisStatus.NOT_ANALYZED)
            self.assertEqual(reopened.cache_issue,
                             "Analysis cache: analysis cache source hash mismatch")
            self.assertEqual(reopened.view_state, state)
            self.assertEqual(store.load_annotations(project), {node_id: "retain review"})
            self.assertEqual({item: item.read_bytes() for item in before}, before)

    def test_source_read_error_during_cached_verification_keeps_view_state(self):
        self.assertTrue(hasattr(source_handles, "SourceReadError"), "missing shared read error")
        self.assertTrue(hasattr(source_handles, "read_source_bytes"), "missing shared reader")
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir).resolve()
            (project / "run.py").write_bytes(b"value = 1\n")
            store = CacheStore(Path(data_dir))
            first = AppController(store)
            first.open_project(project)
            expected = first.analyze()
            node_id = next(iter(expected.symbol_index.entries))
            first.move_node(node_id, (12.0, 18.0), ViewKind.SYMBOL)
            expected_state = first.view_state
            old_cache = store.analysis_path(project).read_bytes()
            with self.assertRaises(source_handles.SourceReadError) as caught:
                source_handles.read_source_bytes(project, "private-missing.py", max_bytes=100)
            error = caught.exception
            self.assertIsInstance(error, ValueError)
            # Only fingerprint's read fails; real cache/controller handling remains active.
            with patch("codeviz.persistence.fingerprint.read_source_bytes", side_effect=error):
                reopened = AppController(store)
                reopened.open_project(project)
            self.assertEqual(reopened.status, AnalysisStatus.OUTDATED)
            self.assertEqual(reopened.outdated_reasons, ("source could not be verified",))
            self.assertIsNotNone(reopened.analysis)
            self.assertEqual(tuple(reopened.analysis.symbol_index.entries),
                             tuple(expected.symbol_index.entries))
            self.assertEqual(reopened.view_state.positions_for(ViewKind.SYMBOL)[node_id],
                             (12.0, 18.0))
            self.assertEqual(reopened.view_state, expected_state)
            self.assertEqual(reopened.cache_issue, f"Source fingerprint: {error.message}")
            self.assertNotIn("private-missing.py", reopened.cache_issue)
            self.assertNotIn(str(project), reopened.cache_issue)
            self.assertEqual(store.analysis_path(project).read_bytes(), old_cache)
            reopened.view(ViewKind.ARCHITECTURE, _remember_depth=False)
            self.assertEqual(reopened.view_state, expected_state)

    def test_source_read_error_during_analysis_fingerprint_returns_partial(self):
        self.assertTrue(hasattr(source_handles, "SourceReadError"), "missing shared read error")
        self.assertTrue(hasattr(source_handles, "read_source_bytes"), "missing shared reader")
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir).resolve()
            (project / "run.py").write_bytes(b"value = 1\n")
            store = CacheStore(Path(data_dir))
            controller = AppController(store)
            controller.open_project(project)
            initial = controller.analyze()
            node_id = next(iter(initial.symbol_index.entries))
            controller.move_node(node_id, (12.0, 18.0), ViewKind.SYMBOL)
            expected_state = controller.view_state
            old_cache = store.analysis_path(project).read_bytes()
            with self.assertRaises(source_handles.SourceReadError) as caught:
                source_handles.read_source_bytes(project, "private-missing.py", max_bytes=100)
            error = caught.exception
            with patch("codeviz.persistence.fingerprint.read_source_bytes", side_effect=error):
                result = controller.analyze()
            self.assertIs(controller.analysis, result)
            self.assertTrue(result.files[0].was_parsed)
            self.assertEqual(controller.status, AnalysisStatus.PARTIAL)
            self.assertEqual(controller.view_state.positions_for(ViewKind.SYMBOL)[node_id],
                             (12.0, 18.0))
            self.assertEqual(controller.view_state, expected_state)
            self.assertEqual(controller.cache_issue, f"Analysis cache: {error.message}")
            self.assertNotIn("private-missing.py", controller.cache_issue)
            self.assertNotIn(str(project), controller.cache_issue)
            self.assertEqual(store.analysis_path(project).read_bytes(), old_cache)
            controller.view(ViewKind.ARCHITECTURE, _remember_depth=False)
            self.assertEqual(controller.view_state, expected_state)

    def test_main_node_positions_remain_isolated_by_view_after_reload(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "run.py").write_text("value = 1\n", encoding="utf-8")
            store = CacheStore(Path(data_dir))
            first = AppController(store)
            first.open_project(project)

            first.move_node("shared", (10.0, 20.0), ViewKind.SYMBOL)
            first.move_node("shared", (30.0, 40.0), ViewKind.DATA_FLOW)

            reopened = AppController(store)
            reopened.open_project(project)
            self.assertEqual(reopened.view_state.positions_for(ViewKind.SYMBOL)["shared"],
                             (10.0, 20.0))
            self.assertEqual(reopened.view_state.positions_for(ViewKind.DATA_FLOW)["shared"],
                             (30.0, 40.0))

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
